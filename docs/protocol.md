# CFS RS485 Protocol Specification

Protocol reverse-engineered from:
- `CrealityOfficial/Hi_Klipper`: `auto_addr_wrapper.py` (full Python source, GPL-3.0)
- `strings` analysis of `box_wrapper.cpython-39.so` and `serial_485_wrapper.cpython-39.so`
- Live RS485 traffic captures on a Creality Hi: tool changes, a 3-color print, and the
  2026-06 stock-choreography recaptures (all frames CRC-verified)
- The hardware-validated open reference implementation (`creality-klipper-unlocked/extras/box.py`),
  deployed and exercised on a real Creality Hi + CFS v1: load, unload, flush and cut-read
  verified on the wire through 2026-07-01
- Cross-referenced with `ityshchenko/klipper-cfs` and `fake-name/cfs-reverse-engineering`

Raw capture files: see [`captures/`](../captures/)

**Validation status.** Transport, CRC, and addressing are capture-validated on this module
(`src/creality_cfs.py`). The load/unload/cut/flush choreography documented below was
hardware-validated on the reference implementation, on the same wire protocol and the same
printer class (Creality Hi + CFS v1). This module's port of that choreography is
wire-faithful but has not itself been exercised on hardware yet.

---

## Physical Layer

| Parameter | Value | Source |
|-----------|-------|--------|
| Interface | RS485 half-duplex | hardware |
| Baud rate | 230400 | box.cfg + serial_485_wrapper.so + capture confirmed |
| Data format | 8N1 | hardware |
| Connector | 6-pin daisy-chain (see hardware.md) | measured |
| Termination | 300Ω pull-up/down bias resistors | hardware |

Direction control is handled automatically by the CFS hardware. RTS pin toggling
is not required from the host side.

---

## 6-Pin Connector Pinout

Confirmed with multimeter on Creality Hi (locking latch on top, reading left to right,
top row pins 1-3, bottom row pins 4-6):

| Pin | Wire | Idle Voltage | Triggered | Function |
|-----|------|-------------|-----------|----------|
| 1 | Red | ~1.75V | n/a | RS485-A |
| 2 | White | 0.01V | 3.3V | Buffer switch 1 (GPIO, not RS485) |
| 3 | Black | 3.3V | 0.01V | Buffer switch 2 (inverted pair of pin 2) |
| 4 | Yellow | 24V | n/a | 24V power |
| 5 | Green | 0V | n/a | GND |
| 6 | Blue | ~1.74V | n/a | RS485-B |

**Important:** Pins 2 and 3 are direct GPIO buffer switch signals, NOT RS485 data.
No RS485 traffic is generated when the buffer triggers; these are hardware lines
read directly by the printer as GPIO inputs.

**Daisy-chain topology:**
```
Printer (1x 6-pin OUT)
  → CFS1 port1 (IN) / CFS1 port2 (OUT)
  → CFS2 port1 (IN) / CFS2 port2 (OUT)
  → CFS3 port1 (IN) / CFS3 port2 (OUT)
  → CFS4 port1 (IN) / CFS4 port2 (OUT)
  → Filament buffer (terminator, 1x 6-pin IN)
```

Buffer switch signals are per-segment. Pin 2/3 on the Printer→CFS1 link carry
CFS1's buffer state only. Each segment has its own independent buffer signals.

---

## Frame Format

Every RS485 message follows this structure:

```
[HEAD] [ADDR] [LENGTH] [STATUS] [FUNC] [DATA...] [CRC8]
  0xF7   1B      1B       1B      1B    0-N bytes   1B
```

| Field | Size | Description |
|-------|------|-------------|
| HEAD | 1 byte | Always `0xF7` |
| ADDR | 1 byte | Destination address |
| LENGTH | 1 byte | Bytes from STATUS through CRC inclusive: `len(DATA) + 3` |
| STATUS | 1 byte | See below |
| FUNC | 1 byte | Function/command code |
| DATA | 0-N bytes | Variable payload |
| CRC8 | 1 byte | CRC-8/SMBUS over `msg[2:-1]` |

**STATUS byte semantics:**
- `0xFF`: host operational requests.
- `0x00`: addressing commands (both directions) and box replies.
- In box replies the STATUS byte doubles as the async event channel: `0x00` idle,
  `0x30` insert/update push, `0x16` busy/active-cal. See CMD_GET_BOX_STATE below.

Every frame, including the shortest 6-byte replies, ends in the CRC byte. An earlier
revision of this document claimed short CMD_GET_BOX_STATE replies carried no CRC; that
claim came from misreading 0x08 hardware-status replies as box-state frames and is
withdrawn. All captured frames CRC-verify.

---

## CRC Algorithm

**Type:** CRC-8/SMBUS, confirmed from `auto_addr_wrapper.py` source and validated
against 16 live capture test vectors.

- Polynomial: `0x07`
- Initial value: `0x00`
- No reflection, no final XOR
- Bit order: MSB-first
- Scope: `msg[2:-1]` (LENGTH byte through last DATA byte)

```python
def crc8_cfs(data: bytes) -> int:
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x07
            else:
                crc <<= 1
            crc &= 0xFF
    return crc
```

Test vector (confirmed from live capture):
```
Message:   F7 01 03 00 A3 DD
CRC scope: 03 00 A3
Expected:  0xDD  ✓
```

---

## Addressing and Topology

| Address | Type | Target |
|---------|------|--------|
| 0x01–0x04 | Unicast | CFS box controllers |
| 0x81–0x84 | Unicast | Closed-loop servo motors / buffer nodes |
| 0x91–0x92 | Unicast | Belt tension motors |
| 0xFC | Broadcast | Belt tension motors only |
| 0xFD | Broadcast | Closed-loop servo motors only |
| 0xFE | Broadcast | Material boxes only |
| 0xFF | Broadcast | All devices |

Each device has a 12-byte UniID for permanent identification. Addresses are
assigned dynamically at boot via the auto-addressing sequence.

**One controller per box; slots are a data-byte bitmask.** Each CFS is ONE bus device.
The four filament slots (tools T0..T3) are NOT bus addresses; they are selected inside
command payloads by a 1-hot bitmask:

| Tool | Slot | Bitmask |
|------|------|---------|
| T0 | A | 0x01 |
| T1 | B | 0x02 |
| T2 | C | 0x04 |
| T3 | D | 0x08 |

A single-box setup (the Creality Hi) puts the controller at `0x01` and every box
operation goes to `addr=0x01` with the slot in the data bytes. Multi-box daisy-chains
add CONTROLLERS at `0x02`-`0x04`; that is a separate axis from tool slots.

**Buffer nodes (0x81+).** On the CFS side, nodes at `0x81` and up answer only
CMD_GET_BUFFER_STATE (0x0C). On the reference printer the X/Y FOC servos share the same
RS485 bus at `0x81`/`0x82`, so func-0x11 frames captured at those addresses are servo
traffic, not CFS retrude commands. An earlier revision documented a "buffer-node retrude"
form based on those frames; it is wire-disproven and removed.

---

## Auto-Addressing Sequence

On startup, the host runs this 5-step sequence:

```
Step 1: Host → 0xFF  CMD_LOADER_TO_APP (0x0B)
        Wakes devices stuck in bootloader. Response includes version word.

Step 2: Host → 0xFE  CMD_GET_SLAVE_INFO (0xA1) [once per expected box]
        Unaddressed box responds: [dev_type][mode][uniid_12bytes]
        Host allocates an address 0x01-0x04 per UniID.

Step 3: Host → 0xFE  CMD_SET_SLAVE_ADDR (0xA0) [per discovered box]
        Payload: [assigned_addr][uniid_12bytes]
        Box with matching UniID claims the address and acknowledges.

Step 4: Host → unicast  CMD_ONLINE_CHECK (0xA2) to each assigned address
        Confirms address assignment is stable.

Step 5: Host → unicast  CMD_GET_ADDR_TABLE (0xA3) to each address
        Final confirmation and table sync.
```

After addressing, `CMD_ONLINE_CHECK` is sent every 1.5 seconds (10s during print).
Three consecutive failures mark a box offline.

**Note:** If a CFS box already has an address stored from a previous session, it
responds to `CMD_ONLINE_CHECK (0xA2)` but not to the `CMD_GET_SLAVE_INFO (0xA1)`
broadcast. This is expected behavior; the box considers itself already addressed.

**Wake timing (decoded 2026-06-29 on the reference stack).** After the 0xA0 address
assign, the box slave-MCU needs about 9.5 seconds before it services operational
commands. The first 0x0A GET_BOX_STATE after a quiet period legitimately returns
nothing. Short-timeout probes (50-100 ms) miss the box entirely. The module probes with
a single 12-second shot per attempt, retried a bounded number of times, then runs the
connect-init burst: enter feed mode (0x04), read version (0x14), the two-frame pre-load
self-check (0x0D, see below) with 0x08 hardware reads, and the all-slot presence read
(0x02/0x03), which itself can hold about 11 seconds while the box scans all four bays.

---

## Command Set

### Addressing Commands (STATUS = 0x00 for both request and response)

| Code | Name | Request Payload | Response Payload |
|------|------|----------------|-----------------|
| 0x0B | CMD_LOADER_TO_APP | `[0x01]` | 4-byte version word |
| 0xA1 | CMD_GET_SLAVE_INFO | `[0xFE][0xFE]` | `[dev_type][mode][uniid_12B]` |
| 0xA0 | CMD_SET_SLAVE_ADDR | `[target_addr][uniid_12B]` | `[dev_type][mode][uniid_12B]` |
| 0xA2 | CMD_ONLINE_CHECK | `[]` empty | `[dev_type][mode][uniid_12B]` |
| 0xA3 | CMD_GET_ADDR_TABLE | `[]` empty | `[dev_type][mode][uniid_12B]` |

### Operational Commands (STATUS = 0xFF for requests)

All codes below are wire-confirmed on the Creality Hi RS485 bus (CRC-verified capture
frames). The choreography around 0x10/0x11/0x0D was additionally hardware-validated on
the reference implementation.

| Code | Name | Request Payload | Response |
|------|------|----------------|----------|
| 0x02 | CMD_READ_MATERIAL | `[slot_mask]` | ASCII per-slot material map |
| 0x03 | CMD_READ_REMAIN | `[slot_mask]` | 4 positional bytes, 0xFF sentinels |
| 0x04 | CMD_SET_BOX_MODE | `[b0][b1]`, two forms | ACK |
| 0x05 | CMD_CUT_STATE | `[]` empty | 1 state byte |
| 0x08 | CMD_GET_HARDWARE_STATUS | `[channel]` | 1 flag byte |
| 0x0A | CMD_GET_BOX_STATE | `[]` empty | 4-byte state word |
| 0x0C | CMD_GET_BUFFER_STATE | `[0x0B]` | 8-byte block (buffer node 0x81+) |
| 0x0D | CMD_SET_PRE_LOADING | `[mask][phase]` | ACK (STATUS 0x00) / NAK (0x16) |
| 0x0E | CMD_MEASURING_WHEEL | `[0x01]` | 4-byte big-endian IEEE-754 float |
| 0x0F | CMD_CTRL_CONNECTION_MOTOR_ACTION | `[0x01]` engage / `[0x00]` release | ACK |
| 0x10 | CMD_EXTRUDE_PROCESS | `[slot][stage_hi][stage_lo]` | held ACK; push reply carries wheel float |
| 0x11 | CMD_RETRUDE_PROCESS | `[slot][phase]` START/FINISH pair | bare ACK, FINISH held ~9.6 s |
| 0x14 | CMD_GET_VERSION_SN | `[]` empty | 22-byte ASCII string |
| 0xF0 | CMD_VERSION_INFO | `[0x00]` | ASCII firmware string |

Corrections against earlier revisions of this table: box-state is `0x0A`, not `0x08`
(`0x08` is the hardware-status read; the v1.1.0 "corrected 0x0A to 0x08" note had it
backwards, and `0x0B`, not `0x0A`, is LOADER_TO_APP). Remaining-length is `0x03`, not
`0x0F` (`0x0F` is the connection-motor engage/release). The RFID/material read shares
func `0x02`; the tag-label decode from a tagged spool is still pending capture.

---

## CMD_READ_MATERIAL (0x02)

```
REQ: f7 [addr] 04 ff 02 [slot_mask] [crc]
RSP: f7 [addr] .. 00 02 [ASCII map] [crc]
```

The reply is an ASCII per-slot material map:

```
A:unknown;B:none;C:none;D:none;
```

- `none` = slot empty
- `unknown` = filament inserted, no RFID match
- any other label = RFID-identified material

The all-slot form (`slot_mask=0x0F`) can hold the reply about 11 seconds while the box
scans all four bays; size the timeout accordingly.

---

## CMD_READ_REMAIN (0x03)

```
REQ: f7 [addr] 04 ff 03 [slot_mask] [crc]
RSP: f7 [addr] 07 00 03 [A][B][C][D] [crc]
```

The reply is POSITIONAL: four bytes, one per slot A..D, regardless of the mask.

- `0xFF` = slot not selected in the mask (sentinel, no information)
- `0x00` = selected slot empty
- any other value = filament present; the value is the remaining percentage

Do not treat the `0xFF` sentinels as filament present. That misread caused a
spurious-retrude bug on the reference stack.

---

## CMD_SET_BOX_MODE (0x04): two wire forms

```
REQ: f7 [addr] 05 ff 04 [b0][b1] [crc]
RSP: ACK (STATUS 0x00)
```

| Form | Payload | Meaning |
|------|---------|---------|
| Feed/change mode | `[0x00][slot]` | Enter feed mode for the slot. Required before a load or unload; without it the 0x0F engage does not drive the rollers. The boot/connect form uses slot `0x01`. |
| Per-slot print mode | `[slot][0x00]` | Latch the slot as loaded/print-locked. Observed `01 00` / `02 00` / `04 00` keyed to the active slot. GET_BOX_STATE data[3] goes to `0x02` in lockstep with this command. |

---

## CMD_CUT_STATE (0x05): read-only

The physical cut is mechanical: the toolhead rams the cutter arm. There is no cut
command on the wire. 0x05 only reads the state the controller latches afterwards.

```
REQ: f7 [addr] 03 ff 05 [crc]        (no data)
RSP: f7 [addr] 04 00 05 [state] [crc]
```

| State | Meaning |
|-------|---------|
| 0x00 | Cut OK. Every real cut returns this. |
| 0x01 | Transient cut-state-set, seen during a real cut. |
| 0x02 | Nothing to cut: slot empty / no filament at the blade. Not a failure. |

Decoded 2026-06-22 from a stock-vs-empty capture comparison. A failing-cut
counter-example (filament present, blade jammed) is still uncaptured, so any other
value should be treated as "cut not confirmed".

---

## CMD_GET_HARDWARE_STATUS (0x08)

```
REQ: f7 [addr] 04 ff 08 [channel] [crc]
RSP: f7 [addr] 04 00 08 [flag] [crc]
```

Channel selectors used by the unload prep reads (stock order: material first):

- `0x00` = material sensor
- `0x01` = connections sensor

Flag values seen on the wire: `0x00` = clear / no filament, `0x01` = the box's
idle/global value (not a hard busy flag), `0x07` = ready flags. The validated
choreography uses 0x08 as a liveness ping and prep read, never as a gate.

This command was previously documented here as CMD_GET_BOX_STATE with a
`0x0F IDLE / 0x00 BUSY / 0x02 ACTIVE` value table. That was a conflation of two
commands: box-state is 0x0A (next section) and those state values do not apply to 0x08.

---

## CMD_GET_BOX_STATE (0x0A)

```
REQ: f7 [addr] 03 ff 0a [crc]                        (EMPTY data payload)
RSP: f7 [addr] 07 [STATUS] 0a [b0][b1][b2][b3] [crc]
```

Wire-corrected 2026-06-20, CRC-verified across two boxes. The request carries no
parameter byte; earlier revisions documenting a `[param]` byte described a frame that
is not on the wire.

Reply data word `[b0][b1][b2][b3]`:

| Byte | Meaning |
|------|---------|
| b0, b1 | OPAQUE firmware base. Drifts per box/firmware: `0x1a20`, `0x1b26`, `0x1c24` and `0x1d21` were all observed on identical hardware. Carries NO load information. Gating on it caused a proven dry-purge bug on the reference stack. Diagnostics only. |
| b2 | Substatus. `0x00` = OK. |
| b3 | The real load flag. `0x02` = loaded/print-locked (1:1 with the SET_BOX_MODE `[slot][00]` print-mode command). `0x00` = feed/change mode. |

The frame STATUS byte is the box's async event channel:

| STATUS | Meaning |
|--------|---------|
| 0x00 | Idle / steady state. |
| 0x30 | Insert/update push. The data word becomes a 4-byte per-slot phase array; phase `0x03` in any slot byte means the insert completed. |
| 0x16 | Busy/active (with b3 = `0x04`): calibration or retract in progress. Normal transiently; a wedge only if it never settles. |

Caveat: `b3 == 0x02` means the box accepted print mode (box-side loaded/locked). It is
not a filament-reached-the-hotend confirmation; the toolhead filament switch is that
backstop.

History of this code: v1.1.0 of the module relabeled box-state from 0x0A to 0x08 on the
theory that 0x0A was LOADER_TO_APP. Both halves were wrong. On the Hi wire 0x0A is
box-state, 0x08 is GET_HARDWARE_STATUS, and 0x0B is LOADER_TO_APP. A later revision
decoded b0/b1 as `[0x1a class][0x20 LOADED / 0x1f FEEDING]`; that model is
wire-disproven (b0/b1 drift per firmware and carry no state).

---

## CMD_GET_BUFFER_STATE (0x0C)

Sent to a buffer/feeder node at `0x81` and up, not to a box controller.

```
REQ: f7 [buffer_addr] 04 ff 0c 0b [crc]
RSP: f7 [buffer_addr] .. 00 0c [8 bytes] [crc]
```

The reply is an 8-byte block. All-zero means the buffer is empty (filament parked
short). The per-byte decode of a non-empty block is not yet mapped. 0x0C is the only
function these nodes answer on the CFS protocol; see the topology note above about
servo traffic sharing the 0x81/0x82 addresses on the reference printer.

---

## CMD_SET_PRE_LOADING (0x0D)

```
REQ: f7 [addr] 05 ff 0d [mask][phase] [crc]
RSP: ACK = STATUS 0x00; NAK = STATUS 0x16
```

The payload is `[slot_mask][phase]`. Wire-observed pairs:

| Pair | When | Notes |
|------|------|-------|
| `[0x0F][0x00]` | Arm at start-print | Phase 0x00 = ARM. |
| `[0x0F][0x01]` | Disarm at end-print | Phase 0x01 = DISARM. |
| `[0x00][0x01]` then `[0x0F][0x01]` | Connect-time self-check | These TWO frames only. Stock never sends a `[0x0F][0x02]` at connect; a fabricated one is NAKed and holds the box active, so inserts never latch. The begin ACK lands in about 1 second. |
| `[slot][0x02]` | Per-slot re-arm | BLOCKS about 38 seconds while the controller settles the slot. The host must wait it out. |

The reply STATUS byte matters: `0x00` is the ACK; `0x16` is a NAK meaning the controller
did not finish. A host that hangs up on a blocking phase (timeout too short) NAKs the
box into its `0x16`/b3=`0x04` wedge.

Note for module users: the CFS_SET_PRELOAD gcode maps ENABLE=1 to wire phase `0x00`
(arm). The pre-v1.4.0 mapping was inverted (ENABLE=1 sent the wire disarm).

---

## CMD_MEASURING_WHEEL (0x0E): decode resolved

```
REQ: f7 [addr] 04 ff 0e 01 [crc]         (data = [0x01])
RSP: f7 [addr] 07 00 0e [4 bytes] [crc]
```

The 4-byte word is a BIG-ENDIAN IEEE-754 float: the cumulative measuring-wheel
position. The value is NEGATIVE and its magnitude grows as filament feeds, e.g.
-462 to -761 to -1077 across a load; raw `c4 99 c5 bf` decodes to -1230.18.

Earlier revisions listed this decode as unresolved because captures showed a leading
`0xC4` or `0xC5` "tag byte". That byte is simply the float's exponent byte. A side
effect of the negative range: the sign bit keeps the raw big-endian word monotonically
increasing too, so raw-word comparison works for advance/no-advance checks that need
no units.

---

## CMD_CTRL_CONNECTION_MOTOR_ACTION (0x0F)

```
REQ: f7 [addr] 04 ff 0f [01|00] [crc]    (0x01 = engage, 0x00 = release)
RSP: ACK
```

Engage/release the connection (feeder) motor. These calls bracket a load: engage
before the 0x10 feed, release after print mode is set. The Hi wire uses 0x0F for this;
the K1-family CAN binary uses 0x07 for the same operation. Do not cross-use.

---

## CMD_EXTRUDE_PROCESS (0x10): sensor-gated load

Every 0x10 frame carries THREE data bytes: `[slot_bitmask][stage_hi][stage_lo]`
(LEN `0x06`).

```
REQ: f7 [addr] 06 ff 10 [slot][stage_hi][stage_lo] [crc]
RSP: f7 [addr] .. 00 10 [payload] [crc]
```

### Stage set (hardware-validated 2026-06-30)

| Stage bytes | Name | Behavior |
|-------------|------|----------|
| `00 00` | INIT | Arm the feed cycle. Reply held ~4.5 s. |
| `04 00` | ENGAGE | Engage stage. Reply held ~4.5 s. |
| `05 00` | PUSH | Feed push + measure. Reply held ~2 s and carries the wheel float. Repeated. |
| `06 00` | SETTLE | Issued only after the toolhead filament switch trips. |
| `07 03` | FINALIZE | Commit the load. Reply held ~4.5 s. |

The box HOLDS each stage's reply until that stage's mechanical step completes. The
blocking per-stage reply IS the ready mechanism; there is no host status poll. Size
per-stage timeouts to cover the longest hold (the module uses 15 s).

### Push reply: a wheel float, not a position word

The `05 00` push reply payload is the same 4-byte big-endian IEEE-754 float as the 0x0E
wheel read: negative, magnitude grows about 300 counts per real push, and stays near
zero when the box fast-acks a self-limited no-op push.

The pre-v1.4.0 decode of this reply, `[motor_state 0xC3/0xC4][uint16 position in
0.01mm]`, was a MISPARSE: the "state" byte was the float's exponent byte and the
"position" was the float's mantissa. The position-profile table and the settle-based
exit condition built on it are withdrawn. The ~400 mm figure survives only as the
physical path length from CFS motor to toolhead sensor on the reference printer.

### The load is sensor-gated

The validated load model loops the `05 00` push and issues `06 00`/`07 03` ONLY after
the toolhead filament switch trips. The loop exit is the switch, never a push count or
a position value. The box self-limits to about 3 real pushes per `00 00` arm, then
fast-acks no-op pushes (wheel advance near zero); the host detects the dead pushes via
the wheel delta and re-arms the whole cycle with a fresh `00 00` init until the switch
latches. The module bounds the whole load with a 90 s wall budget.

### Full load choreography (CFS_EXTRUDE, hardware-validated on the reference stack)

```
M109 (blocking heat, melt guard)     host-side, see temperature note below
0x04 [00][slot]                      enter feed mode
0x0F 01                              engage connection motor
0x08 [00]                            one-shot liveness ping (never a gate)
0x10 sensor-gated cycles             init/engage/push... until the switch latches,
                                     re-armed on self-limit, 90 s wall budget
0x05                                 post-load cut-state check (diagnostic)
0x04 [slot][00]                      per-slot print mode
0x0F 00                              release connection motor
```

The hotend feed is 100% box-motor. No toolhead `G1 E` move is part of the load; the
hotend purge is a separate operation (CFS_FLUSH in the module) sequenced after the load
completes, exactly as the validated stack does it.

---

## CMD_RETRUDE_PROCESS (0x11): START/FINISH unload pair

The unload is a two-command pair, BOTH frames carrying the slot bitmask in data[0]:

```
REQ start:  f7 [addr] 05 ff 11 [slot] 00 [crc]
REQ finish: f7 [addr] 05 ff 11 [slot] 01 [crc]
RSP (both): f7 [addr] 03 00 11 [crc]           (bare STATUS-0x00 ACK)
```

Timing on a real pull:
- The START reply is fast (~0.25 s) when the slot is empty but held 12-14 s on a real
  pull.
- The FINISH ACK is HELD about 9.6 seconds while the box reels the filament fully in.
  A 0.5 s timeout always times the finish out; earlier module versions could therefore
  never confirm an unload.

Completion is gated on the toolhead filament switch CLEARING, not on any reply status.
A previously documented status-poll model (`0x14` in-progress / `0x16` NAK on the 0x11
reply) is wire-disproven; those bytes never appear on the wire. The reply statuses are
diagnostic only.

Exactly ONE toolhead pull, `G1 E-15 F360` (relative), is interleaved between the START
and FINISH frames. The stock `.so` derives the -15/360 pair internally regardless of
config.

Earlier revisions documented 0x11 as a fixed one-shot `[0x02][0x01]` fire-and-confirm.
That payload was the T1 slot bitmask plus the FINISH phase read out of a T1-only
capture; the slot byte varies with the tool and the START frame precedes it.

### Full unload choreography (CFS_RETRUDE, hardware-validated on the reference stack)

```
M109 (blocking heat, melt guard)
0x04 [00][slot]        enter feed mode
0x08 [00]              material sensor prep read
0x11 [slot][00]        START
G1 E-15 F360           the single interleaved toolhead pull
0x08 [01]              connections sensor prep read
0x11 [slot][01]        FINISH (ACK held ~9.6 s; 13 s timeout)
wait                   toolhead filament switch clears = unload complete
```

The module bounds the whole unload with a 60 s wall budget.

---

## Version Commands (0x14, 0xF0)

**CMD_GET_VERSION_SN (0x14):** empty request, 22-byte ASCII version/serial string.

**CMD_VERSION_INFO (0xF0):**

```
REQ: f7 [addr] 04 ff f0 00 [crc]
RSP: f7 [addr] 1c 00 f0 [25 bytes ASCII] [crc]

CFS box:          'cfs0_050_G32-cfs0_000_113'
Motor controller: 'mot2_023_C30-mot2_002_071'
```

LEN `0x1C` (28) counts STATUS + FUNC + DATA + CRC, so the ASCII payload is 25 bytes.
The motor-controller string comes from the servo boards sharing the bus on the
reference printer, not from a CFS node.

---

## Temperature Guard (required on mainline Klipper)

Two protocol facts force a host-side temperature policy:

1. The box-motor feed (0x10) rams filament toward the hotend without any Klipper
   extruder move, so Klipper's cold-extrude protection never sees it.
2. Mainline Klipper KEEPS the `min_extrude_temp` raise that the Creality fork deletes,
   so any hotend `G1 E` move this choreography issues (the unload pull, the flush
   purge) hard-errors on a cold hotend.

The module therefore enforces its own 170°C floor and runs a blocking `M109` before any
filament motion toward or out of the hotend: the load feed, the unload pull, the flush
purge, and the cut.

---

## Bidirectional Communication (stock stack, unverified on the wire)

`strings` analysis of the stock `box_wrapper.so` shows the CFS stack delivering G-code
back to the printer host via `notifications_addr` / `notifications_cmd` in
`serial_485_wrapper.so`:

| Command | Purpose |
|---------|---------|
| `M104 S<temp>` | Change hotend temperature |
| `M204 S<accel>` | Adjust acceleration |
| `SET_TMC_CURRENT STEPPER=stepper_x CURRENT=<A>` | Adjust X stepper current |
| `G0 E<mm> F74.87` | Extrude filament (~1.25 mm/s) |
| `G0 E-<mm> F74.87` | Retract filament |
| `G4 P<ms>` | Dwell |

These are internal stock-stack notification strings, not captured RS485 frames. The
validated choreography needed none of them: the hotend moves it uses (M109, the single
`G1 E-15 F360` pull, the flush purges) are issued host-side, and no box-to-host G-code
was observed in the wire captures. This module does not implement the callback path.

---

## Buffer State

Buffer state is **not communicated over RS485**. The buffer switch signals on
pins 2 and 3 of the 6-pin connector are direct GPIO lines:

- Pin 2 (white): `0.01V idle / 3.3V triggered` = buffer triggered (active high)
- Pin 3 (black): `3.3V idle / 0.01V triggered` = inverted pair (active low)

Only one pin needs to be wired to a GPIO input on the host. Use Klipper's native
`[filament_switch_sensor]` to read buffer state:

```ini
[filament_switch_sensor cfs_buffer]
switch_pin: ^YOUR_GPIO_PIN
pause_on_runout: false
runout_gcode:
    RESPOND MSG="CFS buffer triggered"
```

(The RS485 CMD_GET_BUFFER_STATE (0x0C) above reads the separate buffer/feeder NODE at
0x81+, which is a different device from these per-segment GPIO switch lines.)

---

## Transport Layer

From `strings` analysis of `serial_485_wrapper.cpython-39.so`:

Frame position constants:
```
HEAD_POS  = 0   (0xF7)
ADDR_POS  = 1
LEN_POS   = 2
STATE_POS = 3
CMD_POS   = 4
DATA_POS  = 5
```

Class hierarchy:
- `Serialhdl_485`: low-level UART: `connect_uart`, `raw_send`, `get_response`
- `Serial_485_Wrapper`: high-level queue: `cmd_send_data_with_response`,
  `send_queue_process`, `handle_callback`, `register_response`

---

## Known Error Codes

From `strings` analysis of `box_wrapper.cpython-39.so`:

| Key | Error |
|-----|-------|
| key831 | serial_485 communication timeout |
| key834 | params error, send data |
| key835 | extrude error: blocked at connections |
| key836 | extrude error: blockage between connections and filament sensor |
| key837 | extrude error: blockage between filament sensor and extrusion gear |
| key838 | extrude error: through connections but not extruding |
| key839 | filament error: no filament detected at box extrude position |
| key840 | box switch state error |
| key841 | cut error: cut sensor not detected, not rebounded |
| key843 | RFID error: get rfid failed |
| key846 | empty printing: box speed < extruder speed |
| key848 | material error: may be broken at connections |
| key849 | retrude error: failed to exit connections |
| key850 | retrude error: multiple connections triggered |
| key852 | check extruder filament sensor and box sensor state |
| key853 | humidity sensor error |
| key854 | filament present when cutting detected |
| key855 | cut position error |
| key856 | no cutter |
| key857 | motor load error |
| key858 | errprom (EEPROM) error |
| key859 | measuring wheel error |
| key861 | left RFID card error |
| key862 | right RFID card error |
| key864 | extrude error: buffer full limit not triggered |

---

## Cross-Model Applicability

- **Creality Hi (F018), RS485:** primary reference. Transport, addressing, and every
  function code above are capture-validated on this wire.
- **K1 / K1C / K2 Plus / K2 Max:** NOT confirmed. The K1-family firmware is a CAN
  build that REMAPS function codes: 0x02, 0x05, 0x08 and 0x0C carry different meanings
  there, and the connection-motor action is 0x07 instead of the Hi's 0x0F. An earlier
  revision of this document claimed the protocol was "confirmed identical" on K1/K2;
  that claim is withdrawn. Treat every non-Hi model as untested and never cross-use
  the CAN binary's numbering on the RS485 wire.

---

For implementation: `src/creality_cfs.py`
For command reference: `docs/commands.md`
For hardware details: `docs/hardware.md`
For capture files: `captures/`
