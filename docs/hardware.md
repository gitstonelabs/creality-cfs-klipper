# Hardware Reference

Wiring, connector pinouts, and hardware compatibility for the Creality CFS.

---

## Supported Hardware Configurations

### Option A: USB-RS485 Adapter (recommended for non-Creality printers)

Any Linux host (Jetson, Raspberry Pi, x86 PC) with a CH341-based USB-RS485
adapter. Confirmed working on Jetson Orin Nano (Ubuntu 22.04, JetPack 6.2.2)
with mainline Klipper.

**Adapter requirements:**
- CH341 chip (confirmed working) or any standard USB-RS485 adapter
- Linux driver: `ch341` (built into most kernels, loads automatically on plug-in)
- Device node: `/dev/ttyUSB0` (or use `/dev/serial/by-id/` path for stability)

**Verify driver loaded:**
```bash
dmesg | grep -i ch341
ls /dev/ttyUSB*
```

**Set baud rate before use:**
```bash
stty -F /dev/ttyUSB0 230400 cs8 -cstopb -parenb raw -echo -ixon -ixoff
```

### Option B: Creality Hi Mainboard RS485 (stock)

The Creality Hi mainboard exposes RS485 on `/dev/ttyS5` at 230400 baud.
Use this if you want to run the module on the Hi itself alongside Creality's
Klipper fork, though be aware of the Creality `.so` binary dependencies.

**Known failure mode:** The mainboard RS485 transceiver (likely a MAX485 or
equivalent) can be damaged by ESD or hot-plugging the CFS connector. Symptoms:
all four RS485 wires drag to ~0.01V (should be ~1.7-1.8V idle on A/B lines).
See Troubleshooting section below.

### Option C: BTT Octopus or other mainboards

Wire USB-RS485 adapter to the host PC/SBC. Configure `serial_port` in
`[creality_cfs]` to the adapter's device path. No mainboard RS485 required.

---

## 6-Pin CFS Daisy-Chain Connector

The CFS, filament buffer, and printer all use the same 6-pin connector.
This is **not** the Yeonho SMW200-08 8-pin documented elsewhere. It is a
6-pin variant used on the Creality Hi (F018).

**Orientation:** Locking latch on top, reading left to right.
Top row = pins 1-3, bottom row = pins 4-6.

| Pin | Wire Color | Idle Voltage | Triggered | Function |
|-----|-----------|-------------|-----------|----------|
| 1 | Red | ~1.75V | n/a | RS485-A |
| 2 | White | 0.01V | 3.3V | Buffer switch 1 (GPIO) |
| 3 | Black | 3.3V | 0.01V | Buffer switch 2 (GPIO, inverted) |
| 4 | Yellow | 24V | n/a | 24V power |
| 5 | Green | 0V | n/a | GND |
| 6 | Blue | ~1.74V | n/a | RS485-B |

**Measurements confirmed with multimeter on Creality Hi with:**
- CFS box connected and powered
- Filament buffer connected at end of chain
- RFID board disconnected (it had a short, see troubleshooting)

**Resistance measurements (from printer end, CFS disconnected):**
- Pin 1 (A): 19.35 kΩ to GND (normal, bias resistor network)
- Pin 6 (B): 19.35 kΩ to GND (normal, bias resistor network)
- Pin 2: 10.55 kΩ to GND (buffer switch pull-down)
- Pin 3: 10.55 kΩ to GND (buffer switch pull-up)

---

## Daisy-Chain Topology

The CFS uses a true RS485 daisy-chain. Each CFS box has two 6-pin ports
(IN and OUT). The filament buffer sits at the end of the chain as the terminator.

```
Printer (1x 6-pin OUT)
    │
    ├── Pin 1 (A) ─────────────────────────────────────────┐
    ├── Pin 5 (GND) ─────────────────────────────────────┐ │
    └── Pin 6 (B) ──────────────────────────────────────┐│ │
                                                         ││ │
CFS Box 1 port1 (IN) ← pins 1,5,6                       ││ │
CFS Box 1 port2 (OUT) → CFS Box 2 port1 (IN) → ...      ││ │
    │                                                    ││ │
    └── Buffer switch (pins 2,3) ───► Printer GPIO ──────┘│ │
                                                          │ │
CFS Box 2 port2 (OUT) → CFS Box 3 → CFS Box 4           │ │
    │                                                    │ │
    └── Buffer switch (pins 2,3) ───► independent GPIO ──┘ │
                                                           │
Filament Buffer (terminator, 1x 6-pin IN)                 │
    └── Termination resistor ──────────────────────────────┘
```

**Key point:** Buffer switch signals on pins 2/3 are per-segment and independent.
The Printer→CFS1 link carries CFS1's buffer state. The CFS1→CFS2 link carries
CFS2's buffer state. Each requires its own GPIO input on the host if you want to
monitor all segments independently.

---

## Wiring the USB-RS485 Adapter

Connect the adapter to the CFS connector:

| CFS Connector | Wire | USB-RS485 Adapter |
|--------------|------|------------------|
| Pin 1 | Red | A+ (or A or D+) |
| Pin 5 | Green | GND |
| Pin 6 | Blue | B- (or B or D-) |

**Do not connect:**
- Pin 2 (white): buffer switch, connect to GPIO if needed
- Pin 3 (black): buffer switch inverted, usually not needed
- Pin 4 (yellow): 24V power, do NOT connect to adapter

**Power:** The CFS still needs 24V from the printer PSU on pin 4, even when
using USB-RS485 for data. The adapter carries data only.

**If no data received:** Swap A and B connections. RS485 polarity conventions
vary between manufacturers and the labeling can be misleading.

---

## Buffer Switch GPIO Wiring

The filament buffer signals require a separate GPIO connection, independent of RS485:

| CFS Connector | Wire | Connect to |
|--------------|------|-----------|
| Pin 2 | White | GPIO signal input pin |
| Pin 5 | Green | GPIO GND |

Pin 3 (black) is the inverted pair of pin 2; only one is needed.
Pin 2 goes HIGH (3.3V) when the buffer is triggered (filament present/tension).

**Klipper config:**
```ini
[filament_switch_sensor cfs_buffer]
switch_pin: ^YOUR_GPIO_PIN    # ^ enables pull-up
pause_on_runout: false        # buffer trigger should not pause print
runout_gcode:
    RESPOND MSG="CFS buffer triggered"
insert_gcode:
    RESPOND MSG="CFS buffer released"
```

Suitable GPIO pins:
- BTT EBB42 Gen2: `EBB:PA2` (ENDSTOP port) or `EBB:PA4` (PROBE SERVOS pin)
- BTT Octopus: any free endstop or probe pin
- Jetson Orin Nano: any available GPIO (via gpiod or Klipper host MCU)

---

## Cutter Sensor

The filament cutter on the Creality Hi is triggered mechanically when the toolhead
reaches the right X-rail limit (X=260 on a 260x260 bed). A lever at that position
depresses the cutter blade.

**Stock sensor:** 3-wire hall effect sensor, 3.3V powered, wired to nozzle_mcu:PB1
on the Creality Hi toolhead board.

**Alternative (custom toolhead):** Standard 2-wire mechanical microswitch (same
type as X endstop). Wire signal to GPIO, GND to GND. No VCC needed for mechanical
switch. In Klipper config use the switch as the `switch_pin` in `[box]`:

```ini
[box]
switch_pin: ^EBB:PA5    # EBB42 Gen2 PROBE port PA5
```

**Calibrating cutter position:**
```
CALIBRATE_CUT_POS
```

This runs `MOTOR_CHECK_CUT_POS` which moves X to the cut position and reads
the cutter sensor. The calibrated `cut_pos_x` value is saved to SAVE_CONFIG.

---

## RS485 Transceiver Failure

**Symptoms:**
- All four RS485 wires (A, B, and both buffer switches) drag to ~0.01V
- Should be: A/B ~1.7-1.8V idle, buffer switches at defined idle levels
- CFS communication times out completely

**Cause:**
ESD or hot-plugging the CFS connector while powered can damage the RS485
transceiver on the mainboard. The transceiver then pulls the bus low.

**Diagnosis:**
1. Disconnect all devices from the 6-pin connector
2. Measure resistance between pin 1 (A) and GND; should be ~19kΩ (bias network)
3. If resistance is <1Ω, the transceiver is shorted
4. Reconnect devices one at a time; the device that restores the short is also damaged

**Devices known to be at risk from a single ESD event:**
- Mainboard RS485 transceiver
- Y-axis closed-loop motor controller board
- RFID reader board

**Fix:**
Replace the mainboard (or just the transceiver chip if you can rework SMD).
Use a USB-RS485 adapter to bypass the mainboard RS485 entirely.

**Prevention:**
Always power off the printer before connecting or disconnecting CFS cables.

---

## CFS Box UniID After Board Replacement

Each CFS box stores a unique 12-byte UniID. When you replace a CFS board,
the new board has a different UniID. The printer's stored address table in
SAVE_CONFIG will not match and auto-addressing will loop indefinitely.

**Fix:** Clear the stored UniID table in `printer.cfg` SAVE_CONFIG block:

```ini
#*# [auto_addr]
#*# mb_addr_table_uniids =
#*#       0x00
#*#       0x00
#*#       0x00
#*#       0x00
```

Replace all non-zero rows with `0x00`. Restart Klipper. The auto-addressing
sequence will run a fresh discovery and save the new UniID automatically.

---

## Verified Hardware Combinations

| Host | RS485 Interface | CFS | Status |
|------|----------------|-----|--------|
| Creality Hi (GD32F303) | Mainboard /dev/ttyS5 | CFS v1 (1 box) | ✅ Confirmed |
| Jetson Orin Nano (JetPack 6.2.2) | CH341 USB-RS485 dongle | CFS v1 (1 box) | ✅ Confirmed |
| BTT Octopus + Jetson Orin Nano | CH341 USB-RS485 dongle | CFS v1 | 🔵 In progress |

---

## Related Hardware Projects

- [fake-name/cfs-reverse-engineering](https://github.com/fake-name/cfs-reverse-engineering)
  custom RS485→CAN interposer board for CFS integration, high-res board images,
  partial protocol decodes. Good reference for anyone building custom CFS interface hardware.