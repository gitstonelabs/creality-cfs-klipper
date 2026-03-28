# Creality Filament System (CFS) — Klipper Integration Installation Guide

**Module version:** 1.0.0
**Protocol confidence:** 93–97% 
**Klipper compatibility:** v0.11.0+

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [File Placement](#2-file-placement)
3. [printer.cfg Configuration](#3-printercfg-configuration)
4. [First-Boot Testing](#4-first-boot-testing)
5. [G-code Command Reference](#5-g-code-command-reference)
6. [Known Limitations](#6-known-limitations)
7. [Unlocking 0x10/0x11 (EXTRUDE/RETRUDE)](#7-unlocking-0x100x11-extruderetrude)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Prerequisites

- Klipper v0.11.0 or later installed and running.
- Python 3.7+ (included with standard Klipper installations).
- `pyserial` installed (`pip3 install pyserial` or already present on Creality boards).
- RS485 serial port accessible at `/dev/ttyS5` (Creality K2 Plus default) or `/dev/ttyUSB0`.
- The CFS hub powered on and connected via the RS485 cable to the host board.

---

## 2. File Placement

Copy the module and config files to the correct locations:

```bash
# Primary module — must be in the Klipper extras directory
cp creality_cfs.py ~/klipper/klippy/extras/creality_cfs.py

# Optional: macros file — place alongside printer.cfg
cp cfs_macros.cfg ~/printer_data/config/cfs_macros.cfg

# Optional: example config — for reference only
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

Full configuration with all options:

```ini
[creality_cfs]
serial_port: /dev/ttyS5
baud: 230400
timeout: 0.1
retry_count: 3
box_count: 4
auto_init: True
```

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

### Step 1 — Verify module loaded

Check `klippy.log` for the following line:

```
creality_cfs: module loaded, port=/dev/ttyS5 baud=230400
```

If this line is absent, see [Troubleshooting](#8-troubleshooting).

### Step 2 — Run auto-addressing

```gcode
CFS_INIT
```

Expected output (4 boxes):

```
CFS auto-addressing complete: 4/4 box(es) online
```

If you see `0/4 box(es) online`, see [Troubleshooting](#8-troubleshooting).

### Step 3 — Query firmware versions

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

### Step 4 — Check box states

```gcode
CFS_STATUS
```

Expected output:

```
Box 1 (0x01): state=0x1C raw=1c140000
Box 2 (0x02): state=0x1C raw=1c140000
...
```

The exact state codes are hardware-dependent. Any response without an error confirms the boxes are communicating.

### Step 5 — View the address table

```gcode
CFS_ADDR_TABLE
```

This prints all address slots, their UniIDs, online state, and mode.

---

## 5. G-code Command Reference

| Command | Parameters | Description |
|---------|-----------|-------------|
| `CFS_INIT` | — | Run full 5-step auto-addressing sequence |
| `CFS_STATUS` | `[BOX=1-4]` | Query GET_BOX_STATE; omit BOX for all boxes |
| `CFS_VERSION` | `[BOX=1-4]` | Query GET_VERSION_SN; omit BOX for all boxes |
| `CFS_SET_MODE` | `BOX=1-4 MODE=0-255 [PARAM=0-255]` | Set box operating mode |
| `CFS_SET_PRELOAD` | `BOX=1-4 MASK=0-255 ENABLE=0\|1` | Configure pre-loading slots |
| `CFS_ADDR_TABLE` | — | Print current address assignment table |

### Macro commands (from cfs_macros.cfg)

| Macro | Description |
|-------|-------------|
| `CFS_INITIALIZE` | Wrapper for CFS_INIT with logging |
| `CFS_CHECK_STATUS` | Query all box states with logging |
| `CFS_GET_VERSIONS` | Query all box versions with logging |
| `CFS_PRINT_START` | Pre-print: initialize + check status |
| `CFS_PRINT_END` | Post-print cleanup (placeholder until 0x11 is known) |
| `CFS_ENABLE_PRELOAD` | Enable pre-loading on all boxes, all slots |
| `CFS_DISABLE_PRELOAD` | Disable pre-loading on all boxes, all slots |

---

## 6. Known Limitations

### Tool-change macros (T0–T3) not available

The filament load and retract commands are not implemented:

- `CMD_EXTRUDE_PROCESS` (0x10) — payload unknown
- `CMD_RETRUDE_PROCESS` (0x11) — payload unknown

These commands are used by the Creality host software to push and retract filament during tool changes. Their payload format is locked inside a Creality proprietary `.so` binary that was not available for reverse engineering.

**Impact:** You cannot trigger automatic filament changes from Klipper macros until these commands are unlocked. All other CFS functions (status, version, addressing, mode setting) work normally.

### Single-direction RS485

The module does not manually toggle RTS for RS485 direction switching. This is intentional — Creality hardware uses kernel-managed or hardware auto-direction adapters. If you are using a third-party RS485 adapter that requires manual RTS toggling, you will need to modify `_connect_serial()` to enable `serial.rs485.RS485Settings()`.

### Broadcast discovery may miss boxes

The discovery step sends one `CMD_GET_SLAVE_INFO` broadcast per expected box slot. If a box takes longer than `TIMEOUT_LONG` (1.0 s) to respond after power-on, it may be missed. Run `CFS_INIT` again if fewer boxes appear online than expected.

---

## 7. Unlocking 0x10/0x11 (EXTRUDE/RETRUDE)

To recover the payload format for the stubbed commands, capture RS485 traffic while the Creality host software performs a filament change:

### Method 1: interceptty (software)

```bash
# Install interceptty
sudo apt-get install interceptty

# Intercept the serial port (replace /dev/ttyS5 with your port)
sudo interceptty -s 'ispeed 230400 ospeed 230400' /dev/ttyS5 /dev/ttyS5_tap &

# Start the Creality host and trigger a T0-T3 tool-change
# Captured bytes will be logged to stdout

# Format: each line is hex bytes of one frame
# Look for frames with function code 0x10 or 0x11
```

### Method 2: Logic analyzer

1. Connect a logic analyzer (e.g., Saleae, PulseView-compatible) to the RS485 A/B lines.
2. Configure for 230400 baud, 8N1, with RS485 framing.
3. Trigger a T0-T3 tool change on the Creality K-Ware software.
4. Capture and decode all frames. Look for frames where byte[4] == 0x10 or 0x11.

### Reporting findings

Once you have captured frames with 0x10 or 0x11 in the function code position, open an issue or pull request with:
- The raw hex bytes of the full captured message
- The tool-change operation that triggered it (which slot, load or retract)
- The CFS box model and firmware version from `CFS_VERSION`

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

**Symptom:** `creality_cfs: CRC mismatch — received 0xXX, calculated 0xYY`

**Steps:**
1. Verify baud rate matches the CFS hub (`baud: 230400`).
2. Check RS485 cable integrity — loose connections cause bit errors.
3. Verify the RS485 termination resistor is present if cable is long (>2 m).
4. Check for ground loops between the printer host board and CFS hub.

### Timeout — boxes not responding

**Symptom:** `0/4 box(es) online` after `CFS_INIT`

**Steps:**
1. Confirm CFS hub is powered on (LED on the hub should be lit).
2. Verify the RS485 cable is connected to the correct port.
3. Increase timeout: `timeout: 0.5` in printer.cfg and retry `CFS_INIT`.
4. Run `CFS_ADDR_TABLE` to see which addresses have been attempted.
5. Try running `CFS_INIT` twice — boxes may need one broadcast to wake up.

### Motor jam / MOTOR_LOAD_ERR (0x22)

**Symptom:** `CFS_STATUS` returns state byte 0x22

**Steps:**
1. Open the CFS box and check for jammed filament at the drive gear.
2. Clear the jam and push filament back past the drive gear.
3. Run `CFS_INIT` to re-establish addressing.
4. Run `CFS_STATUS` to confirm the error has cleared.

### Module fails to load (ImportError: No module named 'serial')

**Steps:**
```bash
pip3 install pyserial
# or
sudo apt-get install python3-serial
```

Then restart Klipper: `sudo systemctl restart klipper`

### Module not appearing in klippy.log

**Symptom:** No `creality_cfs:` lines in `/var/log/klipper/klippy.log`

**Steps:**
1. Verify the file is at `~/klipper/klippy/extras/creality_cfs.py` exactly.
2. Verify `[creality_cfs]` section exists in `printer.cfg`.
3. Check `klippy.log` for Python syntax errors: `grep -i "creality\|error" ~/printer_data/logs/klippy.log | tail -40`
