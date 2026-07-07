# Creality Filament System (CFS) Klipper Integration

![Status](https://img.shields.io/badge/status-beta-blue)
![License](https://img.shields.io/badge/license-GPL--3.0-blue)
![Python](https://img.shields.io/badge/python-3.7%2B-blue)
![Klipper](https://img.shields.io/badge/klipper-0.11.0%2B-green)
![Protocol](https://img.shields.io/badge/protocol-hardware--validated-brightgreen)

Open-source Klipper integration for the Creality Filament System (CFS) multi-material unit.
Run your CFS on any Klipper printer. No Creality hardware or firmware required.

Maintained by [@gitstonelabs](https://github.com/gitstonelabs)

---

## Status

> **v1.4.0 Beta**
>
> - ✅ Protocol fully reverse-engineered from live RS485 traffic captures (CRC-verified frames)
> - ✅ Transport, CRC, and auto-addressing capture-validated on this module
> - ✅ Full choreography layer ported in v1.4.0: sensor-gated load (0x10), START/FINISH unload (0x11), mechanical cut with 0x05 post-check, hotend flush loop, wire-correct preload semantics, temperature guards, connect timing
> - ✅ T0/T1/T2/T3 tool-change macros (slot-bitmask topology on one controller)
> - ✅ Non-blocking reactor serial transport, no pyserial dependency, never blocks the Klipper reactor
> - ✅ USB-RS485 dongle operation confirmed (CH341, works on any Linux host)
> - 🔵 Choreography validation on hardware from THIS module in progress
> - 🔵 Third-party hardware validation (non-Creality mainboard) in progress

**Validation honesty:** the transport, CRC, and addressing layers are capture-validated on this module. The choreography (load/unload/cut/flush sequencing, timings, and reply decodes) was hardware-validated on the reference implementation, same wire protocol, on a Creality Hi with a CFS v1 box. This module's port of that choreography is wire-faithful but has not yet been exercised on hardware from this module itself.

**Beta exit:** the v1.4.0 choreography exercised end-to-end from this module on hardware, plus validation on a non-Creality mainboard.

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
| K1 / K1C | ⚠️ Untested. The K1-family firmware is a CAN build that remaps function codes (0x02/0x05/0x08/0x0C); do not assume the RS485 map applies |
| K2 Plus / K2 Max | ⚠️ Untested (same CAN remap caveat as K1) |
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
box_count: 1                # number of CFS CONTROLLERS in the daisy-chain (1-4), not slots
filament_sensor: filament_sensor   # toolhead filament switch; gates loads and unloads
extrude_temp: 220           # melt temp; every filament move blocks on M109 to this first
```

For the Creality Hi mainboard RS485 port:
```ini
[creality_cfs]
serial_port: /dev/ttyS5
baud: 230400
box_count: 1
```

The cutter (`cut_switch_pin`, cut geometry) and flush tuning (`nozzle_volume`,
`flush_cycle_cap`, `nozzle_clean_macro`, ...) options are documented in
[configs/printer.cfg.example](configs/printer.cfg.example).

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
| `CFS_INIT` | Run the auto-addressing sequence; discovers and assigns addresses to all CFS controllers |
| `CFS_STATUS [BOX=N]` | Query box state via 0x0A (LOADED / FEEDING flag plus the async event channel) |
| `CFS_VERSION [BOX=N]` | Query firmware version/serial number string (0x14) |
| `CFS_FW_VERSION BOX=N` | Query 0xF0 firmware version string (e.g. `cfs0_050_G32`) |
| `CFS_SET_MODE BOX=N [TOOL=0-3] [MODE=N PARAM=N]` | Box-mode frames: per-slot print mode via TOOL, or the raw enter form via MODE/PARAM |
| `CFS_SET_PRELOAD BOX=N MASK=N ENABLE=0\|1` | Arm (ENABLE=1, wire phase 0x00) or disarm pre-loading; advanced `PHASE=0-2` form for the blocking per-slot re-arm |
| `CFS_EXTRUDE TOOL=0-3 [BOX=N] [TEMP=C]` | Full sensor-gated load: M109 melt guard, feeder engage, 0x10 push loop gated on the toolhead filament switch |
| `CFS_RETRUDE TOOL=0-3 [BOX=N] [TEMP=C]` | Full unload: 0x11 START/FINISH pair with one interleaved toolhead pull, complete when the switch clears |
| `CFS_CUT [BOX=N] [TEMP=C]` | Mechanical cut ram (requires `cut_switch_pin` and the cut geometry); 0x05 post-check |
| `CFS_FLUSH [BOX=N] [LEN=\|VOLUME=] [VELOCITY=] [TEMP=]` | Hotend purge loop: per-cycle cap, measuring-wheel clog watchdog, optional wipe macro, final retract |
| `CFS_ADDR_TABLE` | Print the address assignment table |
| `T0` / `T1` / `T2` / `T3` | Tool-change macros (cfs_macros.cfg): optional cut, unload the old slot, load the new one, optional flush |

**Topology:** one CFS is ONE controller on the RS485 bus (normally address 0x01, `BOX=1`)
with four slots selected by a data-byte bitmask (`TOOL=0..3` maps to 0x01/0x02/0x04/0x08).
`BOX=` exists for multi-box daisy-chains (a second 4-slot unit at address 2); it is a
separate axis from tool slots. Tools are slots, not bus addresses.

---

## Protocol Summary

- **Interface:** RS485 half-duplex, 230400 baud, 8N1
- **Frame:** `[0xF7][ADDR][LEN][STATUS][FUNC][DATA...][CRC8]`, LEN = len(DATA) + 3 (counts STATUS, FUNC, DATA, CRC)
- **CRC:** CRC-8/SMBUS, poly=0x07, init=0x00, scope=`msg[2:-1]`
- **STATUS byte:** 0xFF for host operational commands, 0x00 for addressing and box replies; in replies it doubles as the async event channel (0x30 insert push, 0x16 busy/active-cal)
- **Addressing:** dynamic assignment, 0x01-0x04 for controllers; tools/slots are the data-byte bitmask on one controller, not addresses
- **Measuring wheel:** 0x0E and the 0x10 push replies carry a 4-byte big-endian IEEE-754 float (negative, magnitude grows as filament feeds)
- **Connector:** 6-pin (see hardware.md for full pinout)

All commands confirmed from live RS485 traffic captures. See [docs/protocol.md](docs/protocol.md) for full details.

---

## Reverse Engineering

This protocol was reverse-engineered through:

1. **Source analysis:** `auto_addr_wrapper.py` from `CrealityOfficial/Hi_Klipper` (GPL-3.0)
2. **Binary analysis:** `strings` extraction from `box_wrapper.cpython-39.so` and `serial_485_wrapper.cpython-39.so`
3. **Live RS485 capture:** USB-RS485 sniffer on Creality Hi during T0→T1→T2→T3 tool changes
4. **Reference implementation:** an open clean-room `box.py` stack deployed and exercised on a real Creality Hi + CFS v1 (load, unload, flush, and cut-read verified on the wire); the v1.4.0 choreography is ported from it

Raw capture files are in [`captures/`](captures/) for independent verification.

### GPL Compliance

The compiled `.so` binaries distributed in `CrealityOfficial/Hi_Klipper` are covered
by GPL-3.0 but no corresponding source code has been provided. A formal source code
request has been submitted to Creality twice. If no response is received, the next
escalation is a formal complaint with the
[Software Freedom Conservancy](https://sfconservancy.org/).

---

## Roadmap

### ✅ v1.4.0 Beta (current)
- Choreography layer ported from the hardware-validated reference implementation: sensor-gated load, START/FINISH unload with the toolhead-switch completion gate, mechanical cut ram, hotend flush loop with clog watchdog, wire-correct preload semantics (arm = phase 0x00), connect timing (~9.5 s box wake)
- 0x10 push-reply decode corrected: a 4-byte big-endian IEEE-754 measuring-wheel float (the old motor-state + uint16 position model was a misparse)
- 0x0E measuring-wheel decode resolved (same BE float, negative, magnitude-monotonic)
- T0/T1/T2/T3 macros fixed to the slot-bitmask topology: tools are slots on one controller, not bus addresses
- Mainline temperature guards: blocking M109 plus a 170 C floor before any feed toward the hotend (box-motor feeds bypass Klipper's cold-extrude protection entirely)

### ✅ v1.3.0
- Non-blocking reactor serial transport; no pyserial dependency, never blocks the Klipper reactor greenlet
- Optional kernel RS485 RTS direction control (`rts_on_send`)

### ✅ v1.2.x
- Wire-evidenced corrections from CRC-verified Hi captures: GET_BOX_STATE is 0x0A (0x08 is GET_HARDWARE_STATUS, the toolhead filament-sensor read), slot bitmask replaces the hardcoded T1 slot, CUT_STATE (0x05), CTRL_CONNECTION_MOTOR_ACTION (0x0F), MEASURING_WHEEL (0x0E) added

### ✅ v1.1.x
- 0x10/0x11 first implemented from live capture; USB-RS485 on mainline Klipper confirmed; buffer switch confirmed as direct GPIO, not RS485

### 🔵 Next: hardware validation of the v1.4.0 port
- Exercise the full load/unload/cut/flush choreography on hardware from THIS module (it is currently a wire-faithful port of the reference implementation's hardware-validated behavior)
- Validate on BTT Octopus + Jetson Orin Nano (non-Creality mainboard)

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