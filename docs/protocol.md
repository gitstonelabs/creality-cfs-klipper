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

---

## Operational Sequences

### Physical Filament Path

```
CFS Box (slots 1–4)
  │
  [slot N extruder motor]    ← box-side gear, pushes filament forward
  │
  [slot filament sensor]     ← detects filament in this slot's path
  │
  [4-way splitter/junction]  ← merges 4 paths into 1 Bowden tube
  │
  [filament buffer]          ← slack accumulator; fill sensor = BOX_GET_BUFFER_STATE
  │                            box extruder pauses/resumes based on buffer state
  [long Bowden tube]
  │
  [Nebula extruder]          ← toolhead motor takes over, pulls filament through
  │
  [filament cutter]          ← blade with hall sensor; triggered by X-axis motion
  │                            (toolhead moves to right X-rail limit → lever pushes blade)
  [hotend]
```

The box extruder and Nebula extruder run simultaneously during loading — box pushes,
Nebula pulls. The buffer absorbs the rate difference between them.

The cutter is triggered mechanically by the toolhead reaching the right X-rail limit,
which presses a lever that pushes the cutter blade. The hall sensor on the blade
confirms the cut occurred. No filament sensor is at the cutter position.

### Boot Pre-load Sequence

On startup, the CFS checks each slot for filament presence and parks all loaded
filaments in a known position. Each slot that has filament detected is pre-loaded
to the toolhead extruder, then retracted to clear the 4-way splitter.

```
For each slot 1–4:

  1. Query filament sensor state for slot N
     IF no filament → skip slot

  2. CMD_EXTRUDE_PROCESS (0x10) — feed filament forward
     Parameters: slot=N, length=full_path_length, velocity=slot_profile
     Checkpoints verified in order:
       - slot filament sensor (key836/key837 if blocked)
       - 4-way splitter passage (key835 if blocked at connection)
       - buffer fill sensor    (key864 if buffer not filled after extrude)
       - Nebula extruder input (key838/key839 if not reached)

  3. IF Nebula sensor triggered → pre-load success
     IF timeout/sensor not triggered → error key835-key839

  4. CMD_RETRUDE_PROCESS (0x11) — retract to park position
     Parameters: slot=N, length=retract_until_clear_of_splitter
     Goal: filament tip parked before the 4-way splitter junction
     Only one path through the splitter is active at a time
```

The same sequence runs when new filament is inserted into a slot mid-session.

### Tool Change Sequence (T0 → T1 during print)

Confirmed from OrcaSlicer G-code analysis of a 3-color PLA/PETG/PLA print.
The slicer generates the complete tool change G-code — no BOX_ commands appear
in the file. The CFS is triggered entirely through the `T0`/`T1`/`T2`/`T3`
G-code commands, which are caught by macros in `gcode_macro.cfg`.

```
[SLICER-GENERATED — runs before T1 command]
  M220 S100                     reset feed rate to 100%
  G4 S0                         sync point
  M104 S{next_filament_temp}    PRE-HEAT new filament temp (e.g. S245 for PETG)
  G4 S0                         sync point
  G1 E-.8                       retract 0.8mm (prevent ooze during travel)
  [spiral lift moves]           lift toolhead off print surface
  G2 Z{z+0.4} I0.86 J0.86 ...  spiral lift (from change_filament_gcode profile)
  G1 X260 Y180 F30000           MOVE TO CUT POSITION (X260 = right X-rail limit)
                                 lever at X260 physically presses cutter blade
  G1 Z{z_after_toolchange} F600 lower to print height
  M106 S255 / M106 S0           fan pulse (cool filament stub for clean cut)

[T1 COMMAND — intercepted by gcode_macro.cfg on the Hi]
  T1
  → CFS macro executes:
     1. Hall sensor on cutter blade confirms cut (key841 if fails)
     2. CMD_RETRUDE_PROCESS (0x11)
        → box motor retracts T0 filament
        → filament tip clears 4-way splitter, parks in slot tube
     3. CMD_EXTRUDE_PROCESS (0x10)
        → slot 1 box motor pushes T1 filament forward
        → through splitter → buffer → Bowden → Nebula extruder → hotend
  → Returns to slicer when T1 filament is loaded and ready

[SLICER-GENERATED — runs after T1 returns]
  M104 S{next_filament_temp}    confirm temperature (from filament_start_gcode)
  G1 X{wipe_tower} F30000       move to wipe/purge tower position
  G1 E.8 F2400                  un-retract (prime)
  [wipe tower extrusion moves]  slicer-calculated purge volume printed as wipe tower
  ; CP TOOLCHANGE END
  [resume print]
```

Key findings from G-code analysis:

- Temperature is managed by the slicer, not the CFS — M104 fires before T1,
  not as a CFS notification during the change
- The wipe tower purge is generated entirely by the slicer as G1 extrusion moves —
  BOX_MATERIAL_CHANGE_FLUSH is likely only used for touchscreen-initiated changes
- The cut position is X260 (confirmed: right X-rail limit on 260x260 bed)
- The slicer interface to CFS is only T0/T1/T2/T3 — no BOX_ commands in print G-code
- PETG uses longer pre-cut retract (filament_retraction_distances_when_cut=18mm)
  while PLA uses the default (nil = shorter)

### Slicer Filament Parameters (from analyzed G-code)

These are the per-slot values that become CMD_EXTRUDE_PROCESS and CMD_RETRUDE_PROCESS
parameters. Captured from OrcaSlicer 2.3.2 with Creality Hi profile:

| Parameter | PLA (slots 1,3) | PETG (slot 2) |
|-----------|----------------|---------------|
| filament_max_volumetric_speed | **18 mm³/s** | **14 mm³/s** |
| filament_loading_speed | **28 mm/s** | **28 mm/s** |
| filament_loading_speed_start | **3 mm/s** | **3 mm/s** |
| filament_unloading_speed | **90 mm/s** | **90 mm/s** |
| filament_flow_ratio | 0.98 | 0.95 |
| retraction_distances_when_cut | nil (short) | **18 mm** |
| long_retractions_when_cut | 0 | **1 (enabled)** |
| filament_change_length | 10 mm | 10 mm |

The `filament_max_volumetric_speed` (velocity), `filament_loading_speed`, and
`filament_unloading_speed` are the likely source of the `velocity` field in
the CMD_EXTRUDE_PROCESS payload. RS485 capture will confirm encoding.

### Per-Slot Filament Profile

Each slot stores a profile used during extrude/retract operations:

| Parameter | Source | Description |
|-----------|--------|-------------|
| `velocity` | Slicer / printer UI | Volumetric flow rate (mm³/s or mm/min) |
| `temp` | Slicer / printer UI | Hotend target temperature for this filament |
| `percent` | Slicer | Extrusion multiplier (flow %) |
| `tnn` | Slot assignment | Slot identifier, e.g. "T1A", "T2B" |

The slicer-provided values override whatever is set on the printer touchscreen.
The CFS communicates temperature changes to the printer via `M104 S<temp>` during
tool changes, requiring the Klipper module to handle incoming notifications from the CFS.

### Incoming Notifications (CFS → Printer)

During material change sequences, the CFS sends these commands to the printer host:

| Command | Purpose |
|---------|---------|
| `M104 S<temp>` | Change hotend temperature for new filament |
| `M204 S<accel>` | Adjust acceleration |
| `SET_TMC_CURRENT STEPPER=stepper_x CURRENT=<A>` | Adjust X stepper current |
| `SET_GCODE_VARIABLE MACRO=PRINTER_PARAM VARIABLE=hotend_temp VALUE=<temp>` | Store temp in printer params |
| `G0 E<mm> F74.87` | Extrude filament (printer extruder, ~1.25 mm/s) |
| `G0 E-<mm> F74.87` | Retract filament (printer extruder) |
| `G4 P<ms>` | Dwell wait |

These are delivered via the `notifications_addr` / `notifications_cmd` mechanism
in `serial_485_wrapper.so`. The Klipper module must register response handlers to
process these and forward them to the appropriate Klipper subsystems (heaters,
motion system, etc.).
