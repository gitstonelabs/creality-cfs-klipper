# Changelog

All notable changes to this project will be documented in this file.

This project adheres to [Semantic Versioning](https://semver.org/).

---

## [0.2.0-alpha] - 2026-04-26

### Added
- `get_rfid()` method stub for CMD_GET_RFID (0x02) — sends empty payload,
  response format unconfirmed pending RS485 capture
- Filament rack command inventory in `commands.md` — commands registered by
  `filament_rack_wrapper.cpython-39.so` (FILAMENT_RACK, FILAMENT_RACK_FLUSH,
  FILAMENT_RACK_SET_TEMP, FILAMENT_RUNOUT_FLUSH, etc.)
- Full CFS command inventory in `commands.md` — 60+ commands extracted from
  `strings` analysis of `box_wrapper.cpython-39.so` on a Creality Hi device
- Physical filament path diagram in `commands.md` — inferred from error message
  strings in box_wrapper.so (key835-key864)
- Known error code table in `commands.md` — 13 confirmed error messages with
  Creality internal key identifiers
- Serial transport layer details in `protocol.md` — from `strings` analysis of
  `serial_485_wrapper.cpython-39.so`: frame position constants (HEAD_POS,
  ADDR_POS, LEN_POS, STATE_POS, CMD_POS, DATA_POS), class names
  (Serialhdl_485, Serial_485_Wrapper), and method inventory
- `notifications_addr` and `notifications_cmd` constants documented — confirms
  the CFS box can send unsolicited push events to the host (not just request-response)

### Changed
- Clarified STATUS byte uncertainty for operational commands — the
  `auto_addr_wrapper.py` source always uses STATUS=0x00 for outbound messages.
  STATUS=0xFF (STATUS_OPERATIONAL) was inferred and may not be correct for
  outbound operational commands. Added prominent NOTE in code and docs.
- Improved docstrings on `extrude_process()` and `retrude_process()` stubs —
  added detail from box_wrapper.so strings about multi-stage extrude sequence
  (stage7, auto_retry, buffer fill verification) and retract sequence
  (retract_filament_before_cut, box_retract_buffer, get_last_box_info)
- Updated `_discover_slaves()` comment to explain why the loop sends one
  broadcast per slot rather than a single broadcast
- Added `CMD_GET_RFID` to CMD_TIMEOUTS dict

### Fixed
- Variable name `attempt` reused in nested loops in `_run_auto_addressing()` —
  renamed outer loop variables to `_` to avoid shadowing

### Research
- Confirmed 230400 baud rate from two independent sources: `box.cfg` serial config
  and `serial_485_wrapper.cpython-39.so` strings analysis
- Confirmed protocol is identical across K1/K2/Hi CFS hardware (same function
  codes, same frame format, same baud rate)
- Confirmed `CrealityOfficial/Hi_Klipper` is now open-source (GPL-3.0) on GitHub
  at https://github.com/CrealityOfficial/Hi_Klipper — however the .so files
  (box_wrapper, serial_485_wrapper, motor_control_wrapper, etc.) are compiled
  Cython and no .pyx source files are present in the repository
- Confirmed no .pyx files exist anywhere on the Creality Hi device filesystem
- Identified related project: fake-name/cfs-reverse-engineering — hardware
  interposer approach (RS485→CAN conversion), board images, partial decodes
- Identified related project: ityshchenko/klipper-cfs — alternative community
  Klipper module implementation

---

## [0.1.0-alpha] - 2026-03-27

### Added
- Initial repository structure with src/, tests/, docs/, tools/, and configs/
- creality_cfs.py Klipper module with 9 implemented commands:
  - CMD_LOADER_TO_APP (0x0B)
  - CMD_GET_SLAVE_INFO (0xA1)
  - CMD_SET_SLAVE_ADDR (0xA0)
  - CMD_ONLINE_CHECK (0xA2)
  - CMD_GET_ADDR_TABLE (0xA3)
  - CMD_SET_BOX_MODE (0x04)
  - CMD_GET_BOX_STATE (0x0A)
  - CMD_SET_PRE_LOADING (0x0D)
  - CMD_GET_VERSION_SN (0x14)
- Stubbed commands for:
  - CMD_EXTRUDE_PROCESS (0x10)
  - CMD_RETRUDE_PROCESS (0x11)
- Full pytest test suite with >80% coverage
- RS485 traffic capture tool (tools/capture_cfs_traffic.py)
- Documentation:
  - README.md
  - CONTRIBUTING.md
  - protocol.md
  - commands.md
  - hardware.md
  - troubleshooting.md
- GitHub issue templates for bug reports, feature requests, and questions
- CI workflow for testing and coverage enforcement

### Known Limitations
- 0x10 and 0x11 payloads not yet implemented (pending RS485 capture)
- No physical hardware validation yet (alpha stage)

---

## [Unreleased]

### Planned for Beta (0.2.0-beta → 0.3.0-beta)
- Implement 0x10 (CMD_EXTRUDE_PROCESS) based on captured RS485 payload
- Implement 0x11 (CMD_RETRUDE_PROCESS) based on captured RS485 payload
- Validate STATUS byte for operational requests (0xFF vs 0x00) via capture
- Confirm CMD_GET_RFID (0x02) payload and response format via capture
- Validate all confirmed commands on physical CFS hardware
- Implement filament rack commands once function codes are captured
- Add example macros for filament loading, unloading, and tool changes
- Improve error handling and retry logic with specific RESP_* error code handling
- Expand test coverage to edge cases and hardware-in-the-loop tests
- Document GET_BOX_STATE response bytes 1-3 (semantics currently unknown)
- Document GET_BUFFER_STATE command and response

### Capture Priority Queue
When the RS485 analyzer is available, capture these operations in priority order:

1. Printer boot sequence — captures full discovery traffic (0xA1/0xA0/0xA2)
   to validate CRC implementation and frame format against live traffic
2. `CFS_VERSION BOX=1` — simple query, easiest first decode
3. `BOX_GET_RFID ADDR=1 NUM=1` — validates 0x02 payload
4. Tool change (T0→T1) — should reveal 0x10 and 0x11 payloads
5. `BOX_GET_REMAIN_LEN ADDR=1 NUM=1` — validates remaining length query
6. `BOX_GET_BUFFER_STATE` — validates buffer sensor query
7. `BOX_CUT_MATERIAL` — validates cut command
8. `BOX_SET_BOX_MODE BOX=1 MODE=1` — validates STATUS byte on operational commands
