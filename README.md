# Creality Filament System (CFS) Klipper Integration

![Status](https://img.shields.io/badge/status-beta-blue)
![License](https://img.shields.io/badge/license-GPL--3.0-blue)
![Python](https://img.shields.io/badge/python-3.7%2B-blue)
![Klipper](https://img.shields.io/badge/klipper-0.11.0%2B-green)
![Hardware Validated](https://img.shields.io/badge/hardware-validated-brightgreen)

Open-source Klipper integration for the Creality Filament System (CFS) multi-material unit.
Run your CFS on any Klipper printer. No Creality hardware or firmware required.

Maintained by [@gitstonelabs](https://github.com/gitstonelabs)

---

## Status

> **v1.1.1 Beta**
>
> - ✅ Protocol fully reverse-engineered from live RS485 traffic captures
> - ✅ All core commands implemented and validated on physical Creality Hi hardware
> - ✅ Filament change commands (0x10/0x11) captured and implemented
> - ✅ USB-RS485 dongle operation confirmed (CH341, works on any Linux host)
> - ✅ CFS communicates on mainline Klipper over USB-RS485 adapter
> - 🔵 Tool change macros (T0/T1/T2/T3) in progress
> - 🔵 Third-party hardware validation (non-Creality mainboard) in progress

**Beta → v1.0:** When tool change macros are complete and validated on non-Creality hardware.

---

## What This Is

The Creality Hi ships with a multi-material filament system (CFS) that communicates
over RS485. Creality's control software is a compiled binary (`.so` file) that cannot
be modified, does not run on non-Creality hardware, and phones home to Creality's cloud
servers over MQTT.

This project reverse-engineers the CFS RS485 protocol and implements it as a standard
Klipper extra module. With this module you can:

- Run the Creality CFS on **any Klipper printer** (BTT Octopus, SKR, etc.)
- Use the CFS from a **Jetson, Raspberry Pi, or any Linux host** over a cheap USB-RS485 adapter
- Perform **automated multi-material tool changes** without any Creality software
- **Escape Creality's ecosystem** entirely while keeping your CFS hardware

---

## Hardware Compatibility

| Hardware | Status |
|----------|--------|
| Creality Hi (F018) | ✅ Validated (primary reference hardware) |
| K1 / K1C | ✅ Protocol confirmed identical |
| K2 Plus / K2 Max | ✅ Protocol confirmed identical |
| Any printer + USB-RS485 adapter | ✅ Confirmed working (CH341 dongle) |
| BTT Octopus + Jetson Orin Nano | 🔵 In progress |

---

## Installation

### 1. Install the Klipper module

```bash
cp src/creality_cfs.py ~/klipper/klippy/extras/
```

Or install directly from GitHub:

```bash
cd ~/klipper/klippy/extras/
wget https://raw.githubusercontent.com/gitstonelabs/creality-cfs-klipper/main/src/creality_cfs.py
```

### 2. Add to your `printer.cfg`

```ini
[creality_cfs]
serial_port: /dev/ttyUSB0   # USB-RS485 adapter (use by-id path if available)
baud: 230400
box_count: 1                # number of CFS boxes in your daisy-chain (1-4)
```

For the Creality Hi mainboard RS485 port:
```ini
[creality_cfs]
serial_port: /dev/ttyS5
baud: 230400
box_count: 1
```

### 3. Wire the USB-RS485 adapter

The CFS uses a 6-pin connector. Wire the adapter to:

| CFS Pin | Wire Color | Signal | Connect to |
|---------|-----------|--------|-----------|
| Pin 1 | Red | RS485-A | Dongle A+ |
| Pin 5 | Green | GND | Dongle GND |
| Pin 6 | Blue | RS485-B | Dongle B- |

Power (24V on pin 4, GND on pin 5) must come from your printer PSU. The dongle
carries data only.

For buffer state monitoring, wire a GPIO input:

| CFS Pin | Wire Color | Signal | Connect to |
|---------|-----------|--------|-----------|
| Pin 2 | White | Buffer switch | GPIO input pin |
| Pin 5 | Green | GND | GPIO GND |

```ini
# In printer.cfg: buffer state monitoring (optional)
[filament_switch_sensor cfs_buffer]
switch_pin: ^YOUR_GPIO_PIN   # e.g. ^EBB:PA2
pause_on_runout: false
```

### 4. Restart Klipper and initialize

```bash
sudo systemctl restart klipper
```

Then from the Klipper console:

```
CFS_INIT
CFS_STATUS BOX=1
CFS_VERSION BOX=1
```

---

## G-code Commands

| Command | Description |
|---------|-------------|
| `CFS_INIT` | Run auto-addressing sequence; discovers and assigns addresses to all CFS boxes |
| `CFS_STATUS [BOX=N]` | Query box operating state (IDLE / BUSY / ACTIVE) |
| `CFS_VERSION [BOX=N]` | Query firmware version/serial number string |
| `CFS_FW_VERSION BOX=N` | Query 0xF0 firmware version string (e.g. `cfs0_050_G32`) |
| `CFS_SET_MODE BOX=N MODE=N` | Set box mode (0=standby, 1=load) |
| `CFS_SET_PRELOAD BOX=N MASK=N ENABLE=N` | Configure slot pre-loading |
| `CFS_EXTRUDE BOX=N` | Run extrude sequence; feeds filament to toolhead, reports position |
| `CFS_RETRUDE BOX=N` | Retract filament back into CFS box |
| `CFS_ADDR_TABLE` | Print address assignment table |

---

## Protocol Summary

- **Interface:** RS485 half-duplex, 230400 baud, 8N1
- **Frame:** `[0xF7][ADDR][LEN][STATUS][CMD][DATA...][CRC8]`
- **CRC:** CRC-8/SMBUS, poly=0x07, init=0x00, scope=`msg[2:-1]`
- **Addressing:** Dynamic assignment, 0x01-0x04 for boxes
- **Connector:** 6-pin (see hardware.md for full pinout)

All commands confirmed from live RS485 traffic captures. See [docs/protocol.md](docs/protocol.md) for full details.

---

## Reverse Engineering

This protocol was reverse-engineered through:

1. **Source analysis:** `auto_addr_wrapper.py` from `CrealityOfficial/Hi_Klipper` (GPL-3.0)
2. **Binary analysis:** `strings` extraction from `box_wrapper.cpython-39.so` and `serial_485_wrapper.cpython-39.so`
3. **Live RS485 capture:** USB-RS485 sniffer on Creality Hi during T0→T1→T2→T3 tool changes

Raw capture files are in [`captures/`](captures/) for independent verification.

### GPL Compliance

The compiled `.so` binaries distributed in `CrealityOfficial/Hi_Klipper` are covered
by GPL-3.0 but no corresponding source code has been provided. A formal source code
request has been submitted to Creality twice. If no response is received, the next
escalation is a formal complaint with the
[Software Freedom Conservancy](https://sfconservancy.org/).

---

## Roadmap

### ✅ v1.1.1 Beta (current)
- Stock box_wrapper.so cross-reference: 7 commands confirmed identical, 10 inventoried as TODO pending a capture
- Documented the steer/camera (addr 0x41) and external_material/RFID (addr 0x11) CFS modules

### ✅ v1.1.0 Beta
- All protocol commands confirmed from live hardware capture
- CMD_EXTRUDE_PROCESS (0x10) and CMD_RETRUDE_PROCESS (0x11) implemented
- CMD_GET_BOX_STATE (0x08) corrected (was wrong function code 0x0A)
- STATUS=0xFF for operational commands confirmed from capture
- USB-RS485 operation confirmed on mainline Klipper
- Buffer switch confirmed as direct GPIO, not RS485

### 🔵 v1.2.0 Tool Change Macros
- T0/T1/T2/T3 macro set replacing box_wrapper.so functionality
- Full automated multi-material tool change on mainline Klipper
- Validated on BTT Octopus + Jetson Orin Nano

### 🟢 v2.0.0 Production Release
- Validated on multiple third-party hardware combinations
- Community testing incorporated
- OrcaSlicer / SuperSlicer profile for direct CFS support
- Documentation for common printer integrations

---

## Related Projects

- [fake-name/cfs-reverse-engineering](https://github.com/fake-name/cfs-reverse-engineering): hardware interposer approach, board images, partial protocol decodes
- [ityshchenko/klipper-cfs](https://github.com/ityshchenko/klipper-cfs): alternative community Klipper CFS module

---

## License

GNU General Public License v3.0. See [LICENSE](LICENSE). The clean-room method and the GPL-3.0 provenance are documented in [NOTICES.md](NOTICES.md).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests welcome.

- 🐛 [Bug Reports](https://github.com/gitstonelabs/creality-cfs-klipper/issues)
- 💬 [Discussions](https://github.com/gitstonelabs/creality-cfs-klipper/discussions)
- 📖 [Documentation](docs/)