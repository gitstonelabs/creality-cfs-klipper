# CFS RS485 Protocol Specification

Protocol reverse-engineered from:
- `CrealityOfficial/Hi_Klipper` — `auto_addr_wrapper.py` (full Python source, GPL-3.0)
- `strings` analysis of `box_wrapper.cpython-39.so` and `serial_485_wrapper.cpython-39.so`
- Live RS485 traffic captures during T0→T1→T2→T3 tool changes on Creality Hi
- Cross-referenced with `ityshchenko/klipper-cfs` and `fake-name/cfs-reverse-engineering`

Raw capture files: see [`captures/`](../captures/)

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
| 1 | Red | ~1.75V | — | RS485-A |
| 2 | White | 0.01V | 3.3V | Buffer switch 1 (GPIO, not RS485) |
| 3 | Black | 3.3V | 0.01V | Buffer switch 2 (inverted pair of pin 2) |
| 4 | Yellow | 24V | — | 24V power |
| 5 | Green | 0V | — | GND |
| 6 | Blue | ~1.74V | — | RS485-B |

**Important:** Pins 2 and 3 are direct GPIO buffer switch signals, NOT RS485 data.
No RS485 traffic is generated when the buffer triggers — these are hardware lines
read directly by the printer as GPIO inputs.

**Daisy-chain topology:**
```
Printer (1x 6-pin OUT)
  → CFS1 port1 (IN) — CFS1 port2 (OUT)
  → CFS2 port1 (IN) — CFS2 port2 (OUT)
  → CFS3 port1 (IN) — CFS3 port2 (OUT)
  → CFS4 port1 (IN) — CFS4 port2 (OUT)
  → Filament buffer (terminator, 1x 6-pin IN)
```

Buffer switch signals are per-segment — pin 2/3 on the Printer→CFS1 link carry
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
| STATUS | 1 byte | `0xFF` for operational requests (confirmed); `0x00` for addressing and all responses |
| FUNC | 1 byte | Function/command code |
| DATA | 0-N bytes | Variable payload |
| CRC8 | 1 byte | CRC-8/SMBUS over `msg[2:-1]` |

**Note on short frames:** Some responses (e.g. CMD_GET_BOX_STATE) use a 6-byte
frame with no separate DATA field. In these frames the state value occupies the
final byte position and there is no CRC. This was confirmed by computing expected
CRC values against observed bytes — they do not match, confirming the last byte
is data, not CRC.

---

## CRC Algorithm

**Type:** CRC-8/SMBUS — confirmed from `auto_addr_wrapper.py` source and validated
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

## Addressing

| Address | Type | Target |
|---------|------|--------|
| 0x01–0x04 | Unicast | Individual CFS boxes |
| 0x81–0x84 | Unicast | Closed-loop servo motors |
| 0x91–0x92 | Unicast | Belt tension motors |
| 0xFC | Broadcast | Belt tension motors only |
| 0xFD | Broadcast | Closed-loop servo motors only |
| 0xFE | Broadcast | Material boxes only |
| 0xFF | Broadcast | All devices |

Each device has a 12-byte UniID for permanent identification. Addresses are
assigned dynamically at boot via the auto-addressing sequence.

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
broadcast. This is expected behavior — the box considers itself already addressed.

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

### Operational Commands (STATUS = 0xFF for requests, confirmed from capture)

| Code | Name | Request Payload | Response | Confidence |
|------|------|----------------|----------|-----------|
| 0x02 | CMD_GET_RFID | `[slot_num]` (unconfirmed) | RFID string | 80% |
| 0x04 | CMD_SET_BOX_MODE | `[mode][param]` | ACK | 97% |
| 0x08 | CMD_GET_BOX_STATE | `[param]` | `[state]` 1 byte | **100% confirmed** |
| 0x0D | CMD_SET_PRE_LOADING | `[slot_mask][enable]` | ACK | 93% |
| 0x0F | CMD_GET_REMAIN_LEN | `[0x01]` | TBD | seen in capture |
| 0x10 | CMD_EXTRUDE_PROCESS | see below | see below | **100% confirmed** |
| 0x11 | CMD_RETRUDE_PROCESS | see below | see below | **100% confirmed** |
| 0x14 | CMD_GET_VERSION_SN | `[]` empty | 22-byte ASCII string | 97% |
| 0xF0 | CMD_VERSION_INFO | `[0x00]` | ASCII version string | **100% confirmed** |

---

## CMD_GET_BOX_STATE (0x08) — Confirmed

**Corrected in v1.1.0** — was incorrectly documented as 0x0A (which is LOADER_TO_APP).

```
REQ: f7 [addr] 04 ff 08 [param]
RSP: f7 [addr] 04 00 08 [state]

param: 0x00 = standard poll
       0x01 = poll with transition trigger

state: 0x0F = IDLE    (standby, normal polling response)
       0x00 = BUSY    (transitioning/executing)
       0x02 = ACTIVE  (active during retract sequence)
```

The response last byte is the state value — no CRC on short frames (confirmed
by computing expected CRC: does not match observed value).

---

## CMD_EXTRUDE_PROCESS (0x10) — Confirmed from capture

Three sub-commands sent in sequence per tool change:

### Sub-command 0x02/0x00 — Init/start

```
REQ: f7 01 06 ff 10 02 00 00 [crc]
RSP: f7 01 04 00 10 00 [crc]    ← 1-byte response: 0x00 = success
```

Sent once to start the extrusion motor.

### Sub-command 0x02/0x04 — Status poll

```
REQ: f7 01 06 ff 10 02 04 00 [crc]
RSP: f7 01 03 00 10 [crc]       ← ACK only, no payload
```

Sent periodically during extrusion to check status.

### Sub-command 0x02/0x05 — Streaming position feedback

```
REQ: f7 01 06 ff 10 02 05 00 [crc]
RSP: f7 01 07 00 10 [state] [pos_hi] [pos_lo] [crc]
```

Response payload (3 bytes):
- `state` (1 byte): `0xC3` = accelerating, `0xC4` = at speed
- `pos_hi`, `pos_lo` (2 bytes): uint16 big-endian, filament position in 0.01mm units

Position profile observed across multiple tool changes:
```
state=0xC3  pos≈588mm  (wrap-around during acceleration, not valid)
state=0xC4  pos≈149mm  (filament moving, just started)
state=0xC4  pos≈338mm  (filament mid-path through buffer)
state=0xC4  pos≈400mm  (filament arrived at toolhead sensor — stable)
```

Filament path length confirmed: **~398-400mm** from CFS motor to toolhead sensor.

### Full sequence per tool change

```
GET_BOX_STATE (0x08)   pre-check
GET_REMAIN_LEN (0x0F)  check remaining filament
SET_BOX_MODE (0x04)    prepare
EXTRUDE 0x02/0x00      start motor
EXTRUDE 0x02/0x04      status poll
EXTRUDE 0x02/0x05      stream × N  (until position stabilizes ~400mm)
EXTRUDE 0x02/0x04      status poll
EXTRUDE 0x02/0x05      stream × N  (confirmation)
SET_BOX_MODE (0x04)    transition
RETRUDE 0x02/0x01      retract (one-shot)
EXTRUDE 0x02/0x00      start next load cycle (purge)
  ... repeats
```

---

## CMD_RETRUDE_PROCESS (0x11) — Confirmed from capture

One-shot command, no streaming feedback:

```
REQ: f7 [addr] 05 ff 11 02 01 [crc]
RSP: f7 [addr] 03 00 11 [crc]      ← ACK only

Payload bytes: 0x02 = sub-command, 0x01 = mode/slot flag
```

Always the same payload in all observed captures. Retraction is fire-and-confirm.

---

## CMD_VERSION_INFO (0xF0) — Confirmed from capture

```
REQ: f7 [addr] 04 ff f0 00 [crc]
RSP: f7 [addr] 1c 00 f0 [28 bytes ASCII] [crc]

CFS box:          'cfs0_050_G32-cfs0_000_113'
Motor controller: 'mot2_023_C30-mot2_002_071'
```

---

## Bidirectional Communication

During material change sequences, the CFS sends commands back to the printer host
(confirmed from `box_wrapper.so` strings analysis):

| Command | Purpose |
|---------|---------|
| `M104 S<temp>` | Change hotend temperature |
| `M204 S<accel>` | Adjust acceleration |
| `SET_TMC_CURRENT STEPPER=stepper_x CURRENT=<A>` | Adjust X stepper current |
| `G0 E<mm> F74.87` | Extrude filament (~1.25 mm/s) |
| `G0 E-<mm> F74.87` | Retract filament |
| `G4 P<ms>` | Dwell |

Delivered via `notifications_addr` / `notifications_cmd` in `serial_485_wrapper.so`.
The Klipper module must register response handlers to process these callbacks.

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
- `Serialhdl_485` — low-level UART: `connect_uart`, `raw_send`, `get_response`
- `Serial_485_Wrapper` — high-level queue: `cmd_send_data_with_response`,
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

## Protocol Consistency

Identical across all known CFS hardware:
- Creality Hi (F018) — primary reference, fully validated
- K1 / K1C — protocol confirmed identical
- K2 Plus / K2 Max — protocol confirmed identical

No version branching observed. Function codes, frame format, baud rate, and
CRC algorithm are identical across all models.

---

For implementation: `src/creality_cfs.py`
For command reference: `docs/commands.md`
For hardware details: `docs/hardware.md`
For capture files: `captures/`