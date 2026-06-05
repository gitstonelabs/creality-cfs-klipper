# Changelog

All notable changes to this project will be documented in this file.

This project adheres to [Semantic Versioning](https://semver.org/).

---

## [1.1.1] - 2026-06-03

### Added
- `CMD_*_TODO` placeholder constants for the 10 stock `BoxAction.communication_*` methods whose
  function codes still need a live CFS capture to confirm. The names are taken from the
  `box_wrapper.cpython-39.so` symbol table; each is left `None`, except an inferred
  `CMD_CREATE_CONNECT_TODO = 0x01`, so a capture can fill in the byte.

### Documented
- Cross-referenced `creality_cfs.py` against the stock `box_wrapper.cpython-39.so` and the rest of
  the on-board CFS stack. 3 of 5 CFS modules already ship as open Python on the Hi: `auto_addr`,
  `external_material` (RFID reader at addr 0x11, cmd 0x02), and `steer` (the CFS camera module at
  addr 0x41, GET_STATE 0x0A heartbeat; the long-unknown 0x41 device on the bus is the steer/camera,
  not a box).
- The 7 box commands implemented here are confirmed identical to the stock methods; the other 10
  are inventoried as TODO, pending a capture.
- Noted the Hi-side `serial_485`-wired sibling of this driver, the box module, which
  uses the shared transport because `/dev/ttyS5` on the Hi is owned by `serial_485`.

### Maintenance
- Editorial pass over the documentation, the module docstring and comments, the configs, and the
  test descriptions: consistent punctuation and tightened wording, with no change to any code,
  protocol value, hex code, CRC value, pin assignment, or test vector.
- Moved the visual style reference from `BRAND.md` to `docs/STYLE.md`, trimmed to the color palette,
  the Mermaid theme, and the typography note. The logo and mascot-rebrand sections were dropped.

---

## [1.1.0] - 2026-05-20

### Major: Physical Hardware Validation Complete

This release marks the transition from alpha to beta. All core protocol commands
have been confirmed on physical Creality Hi hardware with a real CFS box. The two
previously stubbed commands (0x10/0x11) are now fully implemented from live RS485
traffic captures. The module is confirmed working on mainline Klipper over a
USB-RS485 adapter, completely independent of Creality hardware and firmware.

### Added
- `CMD_EXTRUDE_PROCESS (0x10)` fully implemented from live RS485 capture
  - Three sub-commands confirmed from capture:
    - `0x02/0x00`: init/start extrusion motor
    - `0x02/0x04`: status poll (ACK-only response)
    - `0x02/0x05`: streaming position feedback (repeating)
  - Response format confirmed: 1-byte motor state + 2-byte uint16 position
    - Position units: 0.01mm (divide by 100 for mm)
    - Motor state `0xC3` = accelerating (wrap-around phase, position not valid)
    - Motor state `0xC4` = at speed (position valid)
  - Filament path length confirmed: ~398-400mm from CFS motor to toolhead sensor
  - Position profile per tool change: ~149mm → ~338mm → ~400mm (stable)
- `CMD_RETRUDE_PROCESS (0x11)` fully implemented from live RS485 capture
  - Payload confirmed: `0x02 0x01` (sub-command + mode flag)
  - One-shot command with ACK-only response, no streaming feedback
- `CMD_VERSION_INFO (0xF0)` new command decoded from capture
  - Returns ASCII firmware version string
  - CFS box example: `cfs0_050_G32-cfs0_000_113`
  - Motor controller example: `mot2_023_C30-mot2_002_071`
- Three new G-code commands:
  - `CFS_EXTRUDE BOX=N`: run full extrude sequence, reports final position
  - `CFS_RETRUDE BOX=N`: run retract sequence
  - `CFS_FW_VERSION BOX=N`: query 0xF0 firmware version string
- Box state constants confirmed from capture:
  - `BOX_STATE_IDLE = 0x0F`: standby/normal polling state
  - `BOX_STATE_BUSY = 0x00`: transitioning/executing command
  - `BOX_STATE_ACTIVE = 0x02`: active during retract sequence
- `FILAMENT_PATH_LENGTH_MM = 400.0` documented as confirmed physical constant
- Raw capture files added to `captures/`:
  - `cfs_toolchange_capture_20260520_013844.bin`: T0→T1→T2→T3 tool change sequence
  - `buffer_test_20260520_022430.bin`: buffer switch trigger + retract sequence

### Fixed
- **Critical: `CMD_GET_BOX_STATE` function code corrected from `0x0A` to `0x08`**
  - `0x0A` is `CMD_LOADER_TO_APP`. Every state poll was accidentally triggering
    a device reboot cycle on the CFS box. This bug existed since v0.1.0-alpha.
  - Correct code `0x08` confirmed from live capture
  - Frame format confirmed: 6-byte frame, state byte in last position
  - Short frames (length=4, no data) do not carry a separate CRC byte;
    the state value occupies the final byte position
- **`STATUS=0xFF` for operational commands confirmed from capture**
  - The uncertainty noted in v0.2.0-alpha is now fully resolved
  - All operational command requests use `STATUS=0xFF`
  - All responses (operational and addressing) use `STATUS=0x00`
  - `get_box_state()` updated to pass correct `param` byte and parse 1-byte state

### Confirmed from live capture
- Buffer switch signals (pins 2/3 on 6-pin connector) are direct GPIO lines,
  NOT RS485 commands. No RS485 traffic is generated by buffer state changes.
  Buffer state monitoring uses Klipper native `[filament_switch_sensor]` on a GPIO pin.
- 6-pin CFS daisy-chain connector pinout (confirmed with multimeter on Creality Hi):
  - Pin 1 (red):    RS485-A, ~1.75V idle
  - Pin 2 (white):  Buffer switch 1, 0.01V idle / 3.3V triggered
  - Pin 3 (black):  Buffer switch 2, 3.3V idle / 0.01V triggered (inverted pair)
  - Pin 4 (yellow): 24V power
  - Pin 5 (green):  GND
  - Pin 6 (blue):   RS485-B, ~1.74V idle
- Daisy-chain topology confirmed:
  `Printer → CFS1 → CFS2 → CFS3 → CFS4 → Filament buffer (terminator)`
  Buffer switch signals are per-segment, not bus-wide
- CFS firmware version: `cfs0_050_G32-cfs0_000_113` (hardware under test)
- Motor controller firmware version: `mot2_023_C30-mot2_002_071`
- USB-RS485 adapter (CH341 chip) confirmed working as drop-in replacement
  for mainboard RS485 port on mainline Klipper (Jetson Orin Nano, Ubuntu 22.04)

### Hardware findings documented
- RS485 transceiver failure mode: ESD or hot-plugging the CFS connector can
  damage the mainboard RS485 transceiver AND connected peripheral boards
  (Y-axis closed-loop motor controller, RFID reader board). Always power off
  before connecting or disconnecting CFS cables.
- After replacing a CFS board, clear the `[auto_addr] mb_addr_table_uniids`
  entry in SAVE_CONFIG to force fresh UniID discovery. The new board has a
  different UniID and will not be recognized until the table is cleared.
- If RS485 lines drag to ~0.01V on all four wires: check every device on the
  bus for shorts before replacing the mainboard. Damaged peripherals (Y-motor
  board, RFID board) will kill the new mainboard's transceiver too.

### GPL compliance update
- Formal source code request submitted to Creality twice for compiled `.so`
  binaries distributed under GPL-3.0 in `CrealityOfficial/Hi_Klipper`
- Creality initially claimed the Hi is "not open source". Responded with
  citations of their own open-source commitments and the specific GPL-3.0
  license on their repository
- Awaiting second response. Escalation to Software Freedom Conservancy
  planned if source code is not provided

---

## [0.2.0-alpha] - 2026-04-26

### Added
- `get_rfid()` method stub for CMD_GET_RFID (0x02)
- Filament rack command inventory in `commands.md`
- Full CFS command inventory in `commands.md`: 60+ commands from strings analysis
- Physical filament path diagram in `commands.md`
- Known error code table in `commands.md`
- Serial transport layer details in `protocol.md`
- `notifications_addr` and `notifications_cmd` constants documented

### Changed
- Clarified STATUS byte uncertainty for operational commands
- Improved docstrings on `extrude_process()` and `retrude_process()` stubs
- Updated `_discover_slaves()` comment
- Added `CMD_GET_RFID` to CMD_TIMEOUTS dict

### Fixed
- Variable name `attempt` reused in nested loops, renamed outer loop variables to `_`

### Research
- Confirmed 230400 baud rate from two independent sources
- Confirmed protocol is identical across K1/K2/Hi CFS hardware
- Confirmed `CrealityOfficial/Hi_Klipper` is GPL-3.0 on GitHub but .pyx source absent
- Identified related projects: fake-name/cfs-reverse-engineering, ityshchenko/klipper-cfs

---

## [0.1.0-alpha] - 2026-03-27

### Added
- Initial repository structure with src/, tests/, docs/, tools/, and configs/
- `creality_cfs.py` Klipper module with 9 implemented commands:
  - CMD_LOADER_TO_APP (0x0B)
  - CMD_GET_SLAVE_INFO (0xA1)
  - CMD_SET_SLAVE_ADDR (0xA0)
  - CMD_ONLINE_CHECK (0xA2)
  - CMD_GET_ADDR_TABLE (0xA3)
  - CMD_SET_BOX_MODE (0x04)
  - CMD_GET_BOX_STATE (0x0A) ← incorrect code, fixed in v1.1.0
  - CMD_SET_PRE_LOADING (0x0D)
  - CMD_GET_VERSION_SN (0x14)
- Stubbed commands: CMD_EXTRUDE_PROCESS (0x10), CMD_RETRUDE_PROCESS (0x11)
- Full pytest test suite with >80% coverage
- RS485 traffic capture tool (tools/capture_cfs_traffic.py)
- Documentation: README, CONTRIBUTING, protocol.md, commands.md,
  hardware.md, troubleshooting.md
- GitHub issue templates and CI workflow

### Known Limitations
- 0x10 and 0x11 payloads not yet implemented (resolved in v1.1.0)
- No physical hardware validation yet (resolved in v1.1.0)

---

## [Unreleased]

### Planned for v1.2.0
- T0/T1/T2/T3 tool change macro set replacing box_wrapper.so
- Full automated multi-material tool change on mainline Klipper
- Validated on BTT Octopus + Jetson Orin Nano host
- Buffer GPIO integration example config
- GET_RFID (0x02) response format from capture
- GET_REMAIN_LEN (0x0F) implementation from capture
- Filament rack command function codes from capture