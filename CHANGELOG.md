# Changelog

All notable changes to this project will be documented in this file.

This project adheres to [Semantic Versioning](https://semver.org/).

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

### Planned for Beta (0.2.0-beta)
- Implement 0x10 and 0x11 based on captured payloads
- Validate all commands on physical CFS hardware
- Add support for GET_RFID (0x02)
- Improve error handling and retry logic
- Expand test coverage to edge cases and hardware-in-the-loop tests
- Add example macros for filament loading/unloading
- Add firmware flashing guide (if feasible)
