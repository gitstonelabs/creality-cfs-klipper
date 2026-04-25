# Creality Filament System (CFS) - Klipper Integration


Open-source Klipper integration for the Creality Filament System (CFS) multi-material unit.

Maintained by https://github.com/gitstonelabs

---

## ⚠️ Alpha Status Notice

> This project is currently in ALPHA.
>
> - ✅ Protocol reverse-engineered and documented  
> - ✅ Core commands implemented and tested in simulation  
> - ❌ Not yet tested on physical hardware  
> - ❌ Filament change commands (0x10/0x11) require traffic capture  
>
> Alpha → Beta: When all commands are validated on physical Creality hardware  
> Beta → v1.0: When validated on third-party (non-Creality) printers

---

## Overview

This Klipper module enables communication with the Creality Filament System (CFS) over RS485. It supports auto-addressing, status polling, and version detection for up to four CFS boxes.

---

## Installation

1. Copy the module to your Klipper extras directory:

```bash
cd ~/klipper/klippy/extras/
wget https://raw.githubusercontent.com/gitstonelabs/creality-cfs-klipper/main/src/creality_cfs.py
````

2.  Add the following to your printer.cfg:

```ini
[creality_cfs]
serial_port: /dev/ttyS5
timeout: 0.1
retry_count: 3
```

3.  Restart Klipper:

```bash
sudo systemctl restart klipper
```

***

## Testing

Install development dependencies:

```bash
pip install -r requirements-dev.txt
```

Run the test suite:

```bash
pytest tests/ -v
```

Run with coverage:

```bash
pytest --cov=src/creality_cfs --cov-report=html tests/
```

Open the coverage report:

```bash
open htmlcov/index.html
```

***

## Reverse Engineering Summary

The CFS RS485 protocol was reverse-engineered through analysis of Creality firmware, community tools, and hardware inspection. The protocol uses a fixed message format and CRC-8/SMBUS for validation.

See docs/protocol.md for full details.

***

## Roadmap

### ✅ Alpha (Current: v0.1.0-alpha)

*   Protocol reverse-engineered and documented
*   9 of 11 commands implemented
*   Test suite with >80% coverage
*   CI/CD pipeline in place
*   Pending: Physical hardware validation
*   Pending: RS485 traffic capture for 0x10/0x11

### 🔵 Beta (Target: Q2 2026)

*   All commands validated on physical Creality hardware
*   0x10/0x11 payloads captured and implemented
*   Successful automated filament changes
*   Field validation of response data structures

### 🟢 v1.0 Release (Target: Q2-Q3 2026)

*   Validated on third-party (non-Creality) hardware
*   Community testing and feedback incorporated
*   Production-ready stability and documentation

***

## To-Do List (Hardware Testing Required)

*   [ ] Capture 0x10 (EXTRUDE\_PROCESS) payload during tool change
*   [ ] Capture 0x11 (RETRUDE\_PROCESS) payload during tool change
*   [ ] Test auto-addressing with 4 physical CFS boxes
*   [ ] Validate GET\_BOX\_STATE field semantics
*   [ ] Validate GET\_VERSION\_SN parsing across multiple hardware units
*   [ ] Test SET\_PRE\_LOADING slot mask behavior
*   [ ] Test GET\_RFID (0x02) with RFID-tagged spools
*   [ ] Validate timing constants under load
*   [ ] Test retry logic with real serial timeouts
*   [ ] Test on Creality Hi, K1, and third-party printers

***

## License

This project is licensed under the GNU General Public License v3.0.  
See the LICENSE file for details.

***

## Contributing

We welcome contributions!  
See CONTRIBUTING.md for guidelines.

***

## Support

*   🐛 Bug Reports: <https://github.com/gitstonelabs/creality-cfs-klipper/issues>
*   💬 Questions: <https://github.com/gitstonelabs/creality-cfs-klipper/discussions>
*   📖 Documentation: See the docs/ directory

# Creality Filament System (CFS) - Klipper Integration


Open-source Klipper integration for the Creality Filament System (CFS) multi-material unit.

Maintained by https://github.com/gitstonelabs

---

## ⚠️ Alpha Status Notice

> This project is currently in ALPHA.
>
> - ✅ Protocol reverse-engineered and documented  
> - ✅ Core commands implemented and tested in simulation  
> - ❌ Not yet tested on physical hardware  
> - ❌ Filament change commands (0x10/0x11) require traffic capture  
>
> Alpha → Beta: When all commands are validated on physical Creality hardware  
> Beta → v1.0: When validated on third-party (non-Creality) printers

---

## Overview

This Klipper module enables communication with the Creality Filament System (CFS) over RS485. It supports auto-addressing, status polling, and version detection for up to four CFS boxes.

---

## Installation

1. Copy the module to your Klipper extras directory:

```bash
cd ~/klipper/klippy/extras/
wget https://raw.githubusercontent.com/gitstonelabs/creality-cfs-klipper/main/src/creality_cfs.py
````

2.  Add the following to your printer.cfg:

```ini
[creality_cfs]
serial_port: /dev/ttyS5
timeout: 0.1
retry_count: 3
```

3.  Restart Klipper:

```bash
sudo systemctl restart klipper
```

***

## Testing

Install development dependencies:

```bash
pip install -r requirements-dev.txt
```

Run the test suite:

```bash
pytest tests/ -v
```

Run with coverage:

```bash
pytest --cov=src/creality_cfs --cov-report=html tests/
```

Open the coverage report:

```bash
open htmlcov/index.html
```

***

## Reverse Engineering Summary

The CFS RS485 protocol was reverse-engineered through analysis of Creality firmware, community tools, and hardware inspection. The protocol uses a fixed message format and CRC-8/SMBUS for validation.

See docs/protocol.md for full details.

***

## Roadmap

### ✅ Alpha (Current: v0.1.0-alpha)

*   Protocol reverse-engineered and documented
*   9 of 11 commands implemented
*   Test suite with >80% coverage
*   CI/CD pipeline in place
*   Pending: Physical hardware validation
*   Pending: RS485 traffic capture for 0x10/0x11

### 🔵 Beta (Target: Q2 2026)

*   All commands validated on physical Creality hardware
*   0x10/0x11 payloads captured and implemented
*   Successful automated filament changes
*   Field validation of response data structures

### 🟢 v1.0 Release (Target: Q2-Q3 2026)

*   Validated on third-party (non-Creality) hardware
*   Community testing and feedback incorporated
*   Production-ready stability and documentation

***

## To-Do List (Hardware Testing Required)

*   [ ] Capture 0x10 (EXTRUDE\_PROCESS) payload during tool change
*   [ ] Capture 0x11 (RETRUDE\_PROCESS) payload during tool change
*   [ ] Test auto-addressing with 4 physical CFS boxes
*   [ ] Validate GET\_BOX\_STATE field semantics
*   [ ] Validate GET\_VERSION\_SN parsing across multiple hardware units
*   [ ] Test SET\_PRE\_LOADING slot mask behavior
*   [ ] Test GET\_RFID (0x02) with RFID-tagged spools
*   [ ] Validate timing constants under load
*   [ ] Test retry logic with real serial timeouts
*   [ ] Test on Creality Hi, K1, and third-party printers

***

## License

This project is licensed under the GNU General Public License v3.0.  
See the LICENSE file for details.

***

## Contributing

We welcome contributions!  
See CONTRIBUTING.md for guidelines.

***

## Support

*   🐛 Bug Reports: <https://github.com/gitstonelabs/creality-cfs-klipper/issues>
*   💬 Questions: <https://github.com/gitstonelabs/creality-cfs-klipper/discussions>
*   📖 Documentation: See the docs/ directory

https://img.shields.io/badge/status-alpha-yellow
https://img.shields.io/badge/license-GPL--3.0-blue
https://img.shields.io/badge/python-3.7%2B-blue
https://img.shields.io/badge/klipper-0.11.0%2B-green
