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

The CFS uses a true RS485 daisy-chain. Each CFS box has two 6-pin ports (IN and
OUT). RS485 data (pins 1/5/6) runs the length of the chain. A single filament
buffer sits at the very end of the chain.

```
Host (USB-RS485 dongle = pins 1/5/6 only, or a mainboard 6-pin port)
   |  RS485  A / GND / B
   v
CFS Box 1  IN -> OUT  -->  CFS Box 2  IN -> OUT  -->  ...  -->  Filament Buffer (end of chain)
```

**Buffer wiring: one buffer, one GPIO.** Creality ships a single filament buffer
at the end of the chain. Its state is a plain mechanical switch on pin 2,
referenced to pin 5 (GND). It is NOT carried over RS485, so a 3-wire USB-RS485
dongle (A/B/GND) does not see it. Read it by tapping pin 2 and pin 5 off a CFS
6-pin connector and wiring them to ONE host GPIO input. For a single box, pin 2
at the host-side (CFS Box 1 IN) connector carries the buffer state, which the
"from printer end" resistance reading above confirms. You do not need a buffer
per box, and a single buffer needs only one GPIO.

**Unconfirmed, needs a multi-box probe.** An earlier version of this page said the
pin-2 signal is "per segment," meaning each cable carries a different box's buffer
and you need one GPIO per box. That was inferred from a SINGLE-box Hi capture and
is not confirmed on a real multi-box chain. If you run more than one box, power the
chain and probe pin 2 against pin 5 on each cable while triggering each box's buffer
by hand to see which segment responds. If it differs from the single-buffer-at-the-
end model above, please open an issue; that is data we do not have yet.

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

## Passive bus tap for capture (logic analyzer, scope, or sniffer)

To watch the wire (protocol capture, timing analysis, or a "did this device
answer" check) tap the RS485 trio off any 6-pin CFS connector. The reliable
method is a T-splice on each of the three data wires so the tap sits in parallel
without breaking the harness. The tap is passive: it only listens, so it does not
disturb the bus and can coexist with a USB-RS485 sniffer on the same points.

| Tap this | Pin | Wire | Note |
|----------|-----|------|------|
| RS485-A | 1 | Red | idles ~1.75V, swings within logic range |
| GND | 5 | Green | mandatory common reference |
| RS485-B | 6 | Blue | idles ~1.74V |
| do NOT tap | 4 | Yellow | **24V power. Never probe this.** |

**Single shared bus, so the tap point does not matter.** The Creality Hi
mainboard has exactly ONE RS485 transceiver, so every device rides the same A/B
pair on `/dev/ttyS5`: the CFS boxes (`0x01`-`0x04`), the X and Y closed-loop
servos (`0x81`/`0x82`), the belt motors (`0x91`/`0x92`), the RFID reader, and the
`auto_addr` broadcasts. A tap on the CFS 6-pin harness sees the servo and
cutter-cal traffic exactly as well as a tap at the motor connector, so you do not
need to open the printer to reach the servos.

**Voltage warning, especially for a logic analyzer.** Pin 4 is 24V. A cheap USB
logic analyzer (FX2 / fx2lafw clone) has an absolute-maximum input around 5.25V,
so touching 24V destroys it instantly. Tap only pins 1, 5, and 6.

Logic analyzer channel map (matches the fx2lafw / PulseView capture procedure):
- D0 to pin 1 (A, red)
- D1 to pin 6 (B, blue)
- GND to pin 5 (green)
- Optional: D2 to pin 2 (white, buffer switch) to correlate the filament-buffer
  trigger with bus traffic. It is a 3.3V GPIO line, not RS485, and is safe on a
  spare channel.

Because A and B are a differential pair, a single leg referenced to GND is
marginal for measuring the idle-to-active turnaround, capture both A and B and
keep whichever decodes cleanly. A passive USB-RS485 sniffer (FTDI or CH341) uses
the same three points (A/B/GND) and can share the T-splices with the analyzer.

---

## Buffer Switch GPIO Wiring

The single filament buffer at the end of the chain reports its state on a separate
GPIO line, independent of RS485. One GPIO covers it. Tap pin 2 and pin 5 off a CFS
6-pin connector (a 3-wire USB-RS485 dongle does not break these out):

| CFS Connector | Wire | Connect to |
|--------------|------|-----------|
| Pin 2 | White | GPIO signal input pin |
| Pin 5 | Green | GPIO GND |

Pin 3 (black) is the inverted pair of pin 2; only one is needed.
Pin 2 goes HIGH (3.3V) when the buffer is triggered (filament present/tension).
The line is 3.3V logic, so use a 3.3V input. Pi and Jetson GPIO and most toolhead
MCU pins are 3.3V and fine; do not feed 5V into it.

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

If the sensor reads inverted, flip the pin polarity with `!` (pin 2 idles near 0V
and goes to 3.3V when triggered). Set it to match what you measure.

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
switch. For this module, configure it as `cut_switch_pin` in `[creality_cfs]`
together with the cut geometry (`pre_cut_pos_x/y`, `cut_pos_x`, `cut_pos_x_max`,
`cut_velocity`); `CFS_CUT` refuses to run without the pin configured:

```ini
[creality_cfs]
cut_switch_pin: ^EBB:PA5    # EBB42 Gen2 PROBE port PA5
```

(On the stock Creality firmware the same switch lives in the `[box]` section and
is calibrated with `CALIBRATE_CUT_POS`; that command does not exist in this module.
Find your `cut_pos_x` by jogging X until the cutter lever bottoms out and the
switch triggers, then set the values in `[creality_cfs]`.)

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