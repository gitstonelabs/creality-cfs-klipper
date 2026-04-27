# CFS RS485 Protocol Specification

This document outlines the RS485 communication protocol used by the Creality Filament
System (CFS) and implemented in this Klipper integration.

Protocol reverse-engineered from:
- `CrealityOfficial/Hi_Klipper` — `auto_addr_wrapper.py` (full Python source, GPL-3.0)
- `strings` analysis of `box_wrapper.cpython-39.so` and `serial_485_wrapper.cpython-39.so`
- Cross-referenced with `ityshchenko/klipper-cfs` and `fake-name/cfs-reverse-engineering`
- RS485 traffic captures (pending for 0x10/0x11 payload validation)

---

## Physical Layer

| Parameter | Value |
|-----------|-------|
| Interface | RS485 (half-duplex) |
| Baud rate | 230400 (confirmed from box.cfg and serial_485_wrapper.so) |
| Data format | 8N1 (8 data bits, no parity, 1 stop bit) |
| Connector | Yeonho SMW200-08 (2mm pitch, 8-pin) |
| Termination | 300Ω pull-up/down bias resistors (non-standard) |

Direction control is handled automatically by the CFS hardware. RTS pin toggling
is not required from the host side.

---

## Frame Format

Every message on the bus follows this structure:

```
[HEAD] [ADDR] [LENGTH] [STATUS] [FUNC] [DATA...] [CRC8]
  0xF7   1B      1B       1B      1B    0-N bytes   1B
```

| Field | Size | Description |
|-------|------|-------------|
| HEAD | 1 byte | Always `0xF7` — start of frame |
| ADDR | 1 byte | Destination address (see Addressing section) |
| LENGTH | 1 byte | Byte count from STATUS through CRC inclusive: `len(DATA) + 3` |
| STATUS | 1 byte | `0xFF` for operational commands (UNCONFIRMED — see note below); `0x00` for addressing commands and all responses |
| FUNC | 1 byte | Function code (command identifier) |
| DATA | 0–N bytes | Variable-length payload. Maximum observed: 100 bytes |
| CRC8 | 1 byte | CRC-8/SMBUS over `msg[2:-1]` (from LENGTH through last DATA byte) |

**Minimum frame length:** 6 bytes (HEAD + ADDR + LENGTH + STATUS + FUNC + CRC, no data)

**STATUS byte note:** The `auto_addr_wrapper.py` source always uses `STATUS=0x00` for
outbound messages. The value `0xFF` for operational command requests is inferred from
response patterns and has not been confirmed via live capture. Pending capture of a
`CMD_SET_BOX_MODE` request to validate. If commands fail to respond, try `STATUS=0x00`.

---

## CRC Algorithm

**Type:** CRC-8/SMBUS (confirmed from `auto_addr_wrapper.py` source)
- Polynomial: `0x07`
- Initial value: `0x00`
- No reflection
- No final XOR
- Bit order: MSB-first

**CRC scope:** `msg[2:-1]` — from the LENGTH byte through the last DATA byte,
excluding HEAD, ADDR, and the CRC byte itself.

```python
def crc8_cfs(data: bytes) -> int:
    """CRC-8/SMBUS, poly=0x07, init=0x00, MSB-first, no XOR-out.
    Confirmed from CrealityOfficial/Hi_Klipper auto_addr_wrapper.py.
    Validated against 16 captured test vectors.
    """
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

**Test vector:**
```
Message:   F7 01 03 00 A3 DD
CRC scope: 03 00 A3         (msg[2:-1])
Expected:  0xDD             ✓
```

---

## Addressing

| Address | Type | Target |
|---------|------|--------|
| 0x01–0x04 | Unicast | Individual material boxes |
| 0xFC | Broadcast | Belt tension motors only |
| 0xFD | Broadcast | Closed-loop servo motors only |
| 0xFE | Broadcast | Material boxes only |
| 0xFF | Broadcast | All devices |

Closed-loop motor addresses: 0x81–0x84
Belt tension motor addresses: 0x91–0x92

Each device has a 12-byte UniID for permanent identification. Addresses are
assigned dynamically at boot via the auto-addressing sequence.

---

## Auto-Addressing Sequence

On startup, the host runs this 5-step sequence to discover and assign addresses:

```
Step 1: Host → 0xFF broadcast CMD_LOADER_TO_APP (0x0B)
        Wakes any devices stuck in bootloader mode.

Step 2: Host → 0xFE broadcast CMD_GET_SLAVE_INFO (0xA1) [repeated per expected box]
        Each unaddressed box responds with: [dev_type][mode][uniid_12bytes]
        Host allocates an address from 0x01–0x04 for each discovered UniID.

Step 3: Host → 0xFE broadcast CMD_SET_SLAVE_ADDR (0xA0) [per discovered box]
        Payload: [assigned_addr][uniid_12bytes]
        Box with matching UniID claims the address and acknowledges.

Step 4: Host → unicast CMD_ONLINE_CHECK (0xA2) to each assigned address
        Box responds with: [dev_type][mode][uniid]
        Confirms the address assignment is stable.

Step 5: Host → unicast CMD_GET_ADDR_TABLE (0xA3) to each address
        Final confirmation and table synchronization.
```

After addressing, the host sends `CMD_ONLINE_CHECK` to all addressed boxes
every 1.5 seconds (10 seconds during printing). Three consecutive failures
mark a box as offline.

---

## Command Set

### Addressing Commands (STATUS = 0x00 confirmed for both request and response)

| Code | Name | Request Payload | Response Payload | Confidence |
|------|------|----------------|-----------------|-----------|
| 0x0B | CMD_LOADER_TO_APP | `[0x01]` | None | 97% |
| 0xA1 | CMD_GET_SLAVE_INFO | `[broadcast_addr][broadcast_addr]` | `[dev_type][mode][uniid_12B]` | 97% |
| 0xA0 | CMD_SET_SLAVE_ADDR | `[target_addr][uniid_12B]` | `[dev_type][mode][uniid_12B]` | 97% |
| 0xA2 | CMD_ONLINE_CHECK | `[]` (empty) | `[dev_type][mode][uniid_12B]` | 95% |
| 0xA3 | CMD_GET_ADDR_TABLE | `[]` (empty) | `[dev_type][mode][uniid_12B]` | 95% |

### Operational Commands (STATUS byte for request UNCONFIRMED — see note above)

| Code | Name | Request Payload | Response Payload | Confidence |
|------|------|----------------|-----------------|-----------|
| 0x02 | CMD_GET_RFID | Unknown (possibly `[slot_num]`) | RFID string (format TBD) | 80% |
| 0x04 | CMD_SET_BOX_MODE | `[mode][param]` | ACK: `F7 01 03 00 04 A1` | 97% |
| 0x0A | CMD_GET_BOX_STATE | `[]` (empty) | `[state][?][?][?]` (4 bytes) | 97% |
| 0x0D | CMD_SET_PRE_LOADING | `[slot_mask][enable]` | ACK | 93% |
| 0x10 | CMD_EXTRUDE_PROCESS | **UNKNOWN** — see analysis below | TBD | 90% |
| 0x11 | CMD_RETRUDE_PROCESS | **UNKNOWN** — see analysis below | TBD | 90% |
| 0x14 | CMD_GET_VERSION_SN | `[]` (empty) | 22-byte ASCII string | 97% |

---

## Payload Analysis: 0x10 and 0x11

The exact byte layout of CMD_EXTRUDE_PROCESS and CMD_RETRUDE_PROCESS is locked
in `box_wrapper.cpython-39.so`. However, `strings` analysis reveals the parameters.

### CMD_EXTRUDE_PROCESS (0x10)

From `box_wrapper.so` format strings:
```
G0 E%f F74.87        → length parameter is a float (mm)
G4 P%d               → dwell time (ms) used in sequence
Tn_extrude_percent[%s] and Tn_extrude_velocity[%s] mismatch
extrude = %s, velocity: %s, temp: %s, percent: %s, tnn: %s
```

**Inferred payload fields:**
- Slot/channel identifier (TNN string like "T1A" or encoded slot number 1B)
- Extrude length in mm (float or fixed-point integer)
- Velocity / feedrate
- Temperature (passed to hotend)
- Percent (extrusion multiplier)

The feedrate `F74.87` (~1.25 mm/s) appears hardcoded for box-side extrusion.
The printer extruder uses a separate speed from the `Tn_extrude_velocity` parameter.

### CMD_RETRUDE_PROCESS (0x11)

From `box_wrapper.so` format strings:
```
G0 E-%f F74.87       → retract length (negative, same feedrate as extrude)
retrude error, failed to exit connections   (key849)
retrude error, multiple connections triggered, addr: %d   (key850)
```

The source comment says: *"In order to save filament, retract 30mm before cutting"*
suggesting a default retract length of 30mm when no explicit length is given.

**Inferred payload fields:**
- Slot/channel identifier (same as extrude)
- Retract length in mm
- Velocity

### Capture instructions

To determine the exact byte layout, capture RS485 traffic on the CFS line
while triggering a T0→T1 tool change:

```bash
python3 tools/capture_cfs_traffic.py \
    --port /dev/ttyUSB0 \
    --baud 230400 \
    --filter-func 0x10 0x11 \
    --segment tool-change
```

The first 0x10 frame you capture will reveal:
1. The STATUS byte (0xFF or 0x00 — also answers the open question above)
2. The LENGTH field (tells you total payload size)
3. The DATA bytes (the actual parameters)

---

## Bidirectional Communication

The CFS is not purely a slave. During material change sequences, the CFS sends
commands **back to the printer** (confirmed from `box_wrapper.so` format strings):

```
M104 S%d                                    → set hotend temperature
M204 S%d / M204 S%s                         → set acceleration
SET_TMC_CURRENT STEPPER=stepper_x CURRENT=%f → adjust stepper current
SET_GCODE_VARIABLE MACRO=PRINTER_PARAM...   → update printer parameters
G0 E%f F74.87                               → extrude filament (printer side)
G0 E-%f F74.87                              → retract filament (printer side)
G4 P%d                                      → dwell
```

This is implemented via the `notifications_addr` and `notifications_cmd` mechanism
in `serial_485_wrapper.so`. The host Klipper instance must handle these unsolicited
callbacks from the CFS during tool-change sequences.

---

## Transport Layer

From `strings` analysis of `serial_485_wrapper.cpython-39.so`:

**Frame position constants (internal):**
```
HEAD_POS  = 0   (0xF7)
ADDR_POS  = 1
LEN_POS   = 2
STATE_POS = 3
CMD_POS   = 4
DATA_POS  = 5
```

**Class hierarchy:**
- `Serialhdl_485` — low-level UART handler: `connect_uart`, `raw_send`,
  `raw_send_wait_ack`, `get_response`, `register_response`, `_bg_thread`
- `Serial_485_Wrapper` — high-level queue manager: `cmd_send_data_with_response`,
  `cmd_485_send_data`, `send_queue_process`, `handle_callback`, `register_response`

The `cmd_send_data_with_response(data, timeout, retry_en)` interface is the main
send path. The `retry_en` bool controls whether the transport layer retries
automatically (the addressing layer disables this and retries at the application layer).

---

## Known Error Codes

From `strings` analysis of `box_wrapper.cpython-39.so`:

| Key | Error | Parameters |
|-----|-------|-----------|
| key831 | serial_485 communication timeout | addr or cmd |
| key834 | params error, send data | payload hex |
| key835 | extrude error: blocked at connections | addr, tnn |
| key836 | extrude error: blockage between connections and filament sensor | addr, tnn |
| key837 | extrude error: blockage between filament sensor and extrusion gear | addr, tnn |
| key838 | extrude error: through connections but not extruding | addr, tnn |
| key839 | filament error: no filament detected at box extrude position | addr, tnn |
| key840 | box switch state error | addr, cmd |
| key841 | cut error: cut sensor not detected, not rebounded | — |
| key843 | RFID error: get rfid failed | addr, rfid_string |
| key846 | empty printing: box speed < extruder speed | — |
| key848 | material error: may be broken at connections | addr, tnn |
| key849 | retrude error: failed to exit connections | addr, tnn |
| key850 | retrude error: multiple connections triggered | addr |
| key852 | check extruder filament sensor and box sensor state | — |
| key853 | humidity sensor error | addr |
| key854 | filament present when cutting detected | — |
| key855 | cut position error | cut_pos_x |
| key856 | no cutter | — |
| key857 | motor load error | — |
| key858 | errprom (EEPROM) error | addr |
| key859 | measuring wheel error | addr |
| key861 | left RFID card error | addr |
| key862 | right RFID card error | addr |
| key864 | extrude error: buffer full limit not triggered | — |

---

## Physical Filament Path

```
CFS Box (slots 1–4)
  │
  ├─[filament reel / spool]
  │
  ▼
[connections / coupling joint]  ← key835: blockage
  │
  ▼
[box-side filament sensor]      ← key836: blockage between joint and sensor
  │
  ▼
[box extrusion gear]            ← key837: blockage at gear
  │                             ← key838: gear turned but filament didn't advance
  ▼
[in-line buffer]                ← key864: buffer not filled after extrude
  │
  ▼
[Bowden tube → printer]
  │
  ▼
[filament cutter]               ← key841: cut sensor not triggered
  │                             ← key854: filament present when shouldn't be
  ▼
[extruder / Nebula]
  │
  ▼
[hotend]
```

---

## Protocol Consistency

The protocol is consistent across all known CFS hardware:
- Creality Hi (F018) — primary reference
- K1 / K1C — same protocol confirmed
- K2 Plus / K2 Max — same protocol confirmed

No version branching (V1/V2) has been observed. The function codes, frame format,
baud rate, and CRC algorithm are identical across models.

---

For implementation details, see `src/creality_cfs.py`.
For command reference, see `docs/commands.md`.
For capture setup, see `tools/capture_cfs_traffic.py`.
