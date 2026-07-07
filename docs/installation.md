# Creality Filament System (CFS) Klipper Integration Installation Guide

**Module version:** 1.4.0 (beta)
**Protocol confidence:** all commands confirmed against live RS485 capture; the v1.4.0 choreography is a wire-faithful port of a reference implementation hardware-validated on a Creality Hi + CFS v1 (not yet exercised on hardware from this module itself)
**Klipper compatibility:** v0.11.0+

> For a visually-illustrated quickstart with wiring diagrams, see [`INSTALL.md`](../INSTALL.md) at the repo root.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [File Placement](#2-file-placement)
3. [printer.cfg Configuration](#3-printercfg-configuration)
4. [First-Boot Testing](#4-first-boot-testing)
5. [G-code Command Reference](#5-g-code-command-reference)
6. [Known Limitations](#6-known-limitations)
7. [Capturing new commands](#7-capturing-new-commands-for-future-protocol-work)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Prerequisites

- Klipper v0.11.0 or later installed and running.
- Python 3.7+ (included with standard Klipper installations).
- No pyserial needed: since v1.3.0 the module uses its own non-blocking reactor serial transport.
- RS485 serial port accessible at `/dev/ttyS5` (Creality Hi mainboard default) or a USB-RS485 adapter at `/dev/ttyUSB0`.
- The CFS hub powered on and connected via the RS485 cable to the host board.
- A toolhead `[filament_switch_sensor]`, strongly recommended: it gates the load and unload choreography.

---

## 2. File Placement

Copy the module and config files to the correct locations:

```bash
# Primary module: must be in the Klipper extras directory
cp creality_cfs.py ~/klipper/klippy/extras/creality_cfs.py

# Optional: macros file, place alongside printer.cfg
cp cfs_macros.cfg ~/printer_data/config/cfs_macros.cfg

# Optional: example config, for reference only
cp printer.cfg.example ~/printer_data/config/printer.cfg.example
```

Verify the module is in the right place:

```bash
ls -la ~/klipper/klippy/extras/creality_cfs.py
```

---

## 3. printer.cfg Configuration

Add the following section to your `printer.cfg`. Minimum required configuration:

```ini
[creality_cfs]
serial_port: /dev/ttyS5
```

Core configuration for the normal single-CFS setup:

```ini
[creality_cfs]
serial_port: /dev/ttyS5
baud: 230400
timeout: 0.1
retry_count: 3
box_count: 1                        # CONTROLLERS in the daisy chain, not slots;
                                    # one CFS (four slots) = 1
auto_init: True
filament_sensor: filament_sensor    # toolhead switch that gates loads/unloads
extrude_temp: 220                   # M109 melt guard before any filament move
```

The optional cutter, flush, and load-tuning options are documented inline in
`configs/printer.cfg.example`.

To include the macros file, add this line anywhere in `printer.cfg`:

```ini
[include cfs_macros.cfg]
```

After editing `printer.cfg`, restart Klipper:

```bash
sudo systemctl restart klipper
```

---

## 4. First-Boot Testing

Run these commands in sequence from the Klipper console (Mainsail, Fluidd, or Moonraker terminal) to verify the integration:

### Step 1: Verify module loaded

Check `klippy.log` for the following line:

```
creality_cfs: module loaded, port=/dev/ttyS5 baud=230400
```

If this line is absent, see [Troubleshooting](#8-troubleshooting).

### Step 2: Run auto-addressing

```gcode
CFS_INIT
```

Expected output (4 boxes):

```
CFS auto-addressing complete: 4/4 box(es) online
```

If you see `0/4 box(es) online`, see [Troubleshooting](#8-troubleshooting).

### Step 3: Query firmware versions

```gcode
CFS_VERSION
```

Expected output (example):

```
Box 1 (0x01): 1101000084321 5B625AHSC
Box 2 (0x02): 1101000084321 5B625AHSC
Box 3 (0x03): 1101000084321 5B625AHSC
Box 4 (0x04): 1101000084321 5B625AHSC
```

### Step 4: Check box states

```gcode
CFS_STATUS
```

Expected output:

```
Box 1 (0x01): FEEDING raw=1c240000
...
```

The state is decoded from data byte 3 of the 0x0A reply (0x02 = LOADED/print-locked, 0x00 = FEEDING/change mode). The first two raw bytes are an opaque per-firmware base and vary between boxes; any response without an error confirms the boxes are communicating.

### Step 5: View the address table

```gcode
CFS_ADDR_TABLE
```

This prints all address slots, their UniIDs, online state, and mode.

---

## 5. G-code Command Reference

| Command | Parameters | Description |
|---------|-----------|-------------|
| `CFS_INIT` | none | Run full 5-step auto-addressing sequence |
| `CFS_STATUS` | `[BOX=1-4]` | Query GET_BOX_STATE (0x0A); omit BOX for all boxes |
| `CFS_VERSION` | `[BOX=1-4]` | Query GET_VERSION_SN; omit BOX for all boxes |
| `CFS_FW_VERSION` | `BOX=1-4` | Query 0xF0 firmware version string |
| `CFS_SET_MODE` | `BOX=1-4 [TOOL=0-3] [MODE=0-255 PARAM=0-255]` | Per-slot print mode via TOOL, or the raw enter form via MODE/PARAM |
| `CFS_SET_PRELOAD` | `BOX=1-4 MASK=0-255 ENABLE=0\|1` | ENABLE=1 arms (wire phase 0x00), ENABLE=0 disarms; advanced `PHASE=0-2` for the blocking per-slot re-arm |
| `CFS_EXTRUDE` | `TOOL=0-3 [BOX=1-4] [TEMP=C]` | Full sensor-gated load to the toolhead (M109 melt guard, 0x10 push loop gated on the toolhead filament switch) |
| `CFS_RETRUDE` | `TOOL=0-3 [BOX=1-4] [TEMP=C]` | Full unload (0x11 START/FINISH pair, one interleaved toolhead pull, complete when the switch clears) |
| `CFS_CUT` | `[BOX=1-4] [TEMP=C]` | Mechanical cut ram; requires `cut_switch_pin` and the cut geometry in `[creality_cfs]` |
| `CFS_FLUSH` | `[BOX=1-4] [LEN=\|VOLUME=] [VELOCITY=] [TEMP=]` | Hotend purge loop with per-cycle cap and clog watchdog |
| `CFS_ADDR_TABLE` | none | Print current address assignment table |

`TOOL=` selects the slot bitmask (T0..T3 = 0x01/0x02/0x04/0x08) on ONE controller; `BOX=`
selects the controller bus address and only matters for multi-box daisy-chains (default 1).

### Macro commands (from cfs_macros.cfg)

| Macro | Description |
|-------|-------------|
| `CFS_INITIALIZE` | Wrapper for CFS_INIT with logging |
| `CFS_CHECK_STATUS` | Query all box states with logging |
| `CFS_GET_VERSIONS` | Query all box versions with logging |
| `CFS_PRINT_START` | Pre-print: check status + arm pre-loading |
| `CFS_PRINT_END` | Post-print: unload the active tool and disarm pre-loading |
| `CFS_ENABLE_PRELOAD` | Arm pre-loading on all slots of the controller |
| `CFS_DISABLE_PRELOAD` | Disarm pre-loading on all slots of the controller |
| `T0` / `T1` / `T2` / `T3` | Tool-change to slot 0-3 on the controller at BOX=1 (cut if enabled, unload the previous slot, load the new one). Tools are slots, not bus addresses |

---

## 6. Known Limitations

### Choreography not yet hardware-exercised from this module

The v1.4.0 load/unload/cut/flush choreography is a wire-faithful port of a reference
implementation that WAS hardware-validated (Creality Hi + CFS v1, same wire protocol).
This module's port has not itself been exercised on hardware yet. The load is
sensor-gated: without a toolhead `[filament_switch_sensor]` configured via
`filament_sensor`, a load runs a single ungated cycle and cannot confirm arrival.

### Half-duplex RS485 direction switching

The module does not toggle RTS by default. Creality's RS485 hardware (and CH341 USB-RS485
dongles) handle direction switching automatically. For an adapter or UART that needs the
kernel RS485 RTS-as-DE mode, set `rts_on_send: 1` (or `0` for RTS-low-on-send) in
`[creality_cfs]`; the default `-1` leaves the UART alone.

### Broadcast discovery may miss boxes

The discovery step sends one `CMD_GET_SLAVE_INFO` broadcast per expected box slot. If a box takes longer than `TIMEOUT_LONG` (1.0 s) to respond after power-on, it may be missed. Run `CFS_INIT` again if fewer boxes appear online than expected.

---

## 7. Capturing new commands (for future protocol work)

`0x10 EXTRUDE_PROCESS` and `0x11 RETRUDE_PROCESS` have been implemented since v1.1.0 and were rebuilt to the hardware-validated choreography (sensor-gated load, START/FINISH unload) in v1.4.0. If you discover additional undocumented function codes during operation, capture them with the tools below and open an issue.

### Method 1: interceptty (software)

```bash
sudo apt-get install interceptty

# Intercept the serial port (replace /dev/ttyS5 with your port)
sudo interceptty -s 'ispeed 230400 ospeed 230400' /dev/ttyS5 /dev/ttyS5_tap &

# Trigger the Creality host action you want to capture.
# Captured bytes are logged to stdout as hex.
```

### Method 2: Logic analyzer

1. Connect a logic analyzer (Saleae, PulseView-compatible) to the RS485 A/B lines.
2. Configure for 230400 baud, 8N1, with RS485 framing.
3. Trigger the operation on the Creality K-Ware software.
4. Decode all frames and inspect the function-code byte (byte 4).

### Method 3: bundled sniffer script

```bash
python3 tools/capture_cfs_traffic.py --port /dev/ttyS5 --out capture.bin
```

### Reporting findings

Attach the raw hex bytes, what operation triggered them, and the CFS firmware version from `CFS_FW_VERSION`.

---

## 8. Troubleshooting

### Serial port not found

**Symptom:** `creality_cfs: failed to open serial port /dev/ttyS5`

**Steps:**
1. Verify the port exists: `ls -la /dev/ttyS5`
2. Check permissions: `sudo usermod -aG dialout $USER` then log out and back in.
3. Try alternative ports: `/dev/ttyUSB0`, `/dev/ttyAMA0`, `/dev/ttyS3`.
4. Update `serial_port` in `printer.cfg`.

### CRC errors in klippy.log

**Symptom:** `creality_cfs: CRC mismatch: received 0xXX, calculated 0xYY`

**Steps:**
1. Verify baud rate matches the CFS hub (`baud: 230400`).
2. Check RS485 cable integrity; loose connections cause bit errors.
3. Verify the RS485 termination resistor is present if cable is long (>2 m).
4. Check for ground loops between the printer host board and CFS hub.

### Timeout: boxes not responding

**Symptom:** `0/4 box(es) online` after `CFS_INIT`

**Steps:**
1. Confirm CFS hub is powered on (LED on the hub should be lit).
2. Verify the RS485 cable is connected to the correct port.
3. Increase timeout: `timeout: 0.5` in printer.cfg and retry `CFS_INIT`.
4. Run `CFS_ADDR_TABLE` to see which addresses have been attempted.
5. Try running `CFS_INIT` twice; boxes may need one broadcast to wake up.

### Motor jam / MOTOR_LOAD_ERR (0x22)

**Symptom:** a command reply carries response state 0x22 (MOTOR_LOAD_ERR) in klippy.log, or loads stall with no wheel advance

**Steps:**
1. Open the CFS box and check for jammed filament at the drive gear.
2. Clear the jam and push filament back past the drive gear.
3. Run `CFS_INIT` to re-establish addressing.
4. Run `CFS_STATUS` to confirm the error has cleared.

### Module fails to load (ImportError: No module named 'serial')

Since v1.3.0 the module does not import pyserial at all (it uses a non-blocking
reactor-fd transport). If you see this error you are running a pre-1.3.0 copy of
`creality_cfs.py`; replace it with the current version from this repo and restart
Klipper: `sudo systemctl restart klipper`

### Module not appearing in klippy.log

**Symptom:** No `creality_cfs:` lines in `/var/log/klipper/klippy.log`

**Steps:**
1. Verify the file is at `~/klipper/klippy/extras/creality_cfs.py` exactly.
2. Verify `[creality_cfs]` section exists in `printer.cfg`.
3. Check `klippy.log` for Python syntax errors: `grep -i "creality\|error" ~/printer_data/logs/klippy.log | tail -40`
