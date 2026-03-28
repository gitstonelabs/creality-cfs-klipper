# CFS Hardware Compatibility and Wiring Guide

This document provides an overview of the Creality Filament System (CFS) hardware, including supported models, RS485 wiring, and connector pinouts.

---

## Supported Hardware

| Model             | MCU              | Firmware Version | Notes                          |
|-------------------|------------------|------------------|--------------------------------|
| CFS Original      | GD32F303VET6     | MF003 V0.050     | Found in Creality Hi (F018)    |
| CFS V2            | GD32F303VET6     | MF003 V0.050+    | Used in K2 Plus / K2 Max       |
| CFS Combo         | Unknown          | Unknown          | Multi-box configuration        |

All known CFS hardware uses the same RS485 protocol and connector layout.

---

## RS485 Electrical Interface

- Interface: RS485 (half-duplex)
- Baud rate: 230400
- Data format: 8N1 (8 data bits, no parity, 1 stop bit)
- Termination: 300Ω pull-up/down bias resistors (non-standard)
- Connector: Yeonho SMW200-08 (2mm pitch, 8-pin)

---

## Connector Pinout (Yeonho SMW200-08)

| Pin | Function     | Description                          |
|-----|--------------|--------------------------------------|
| 1   | 24V          | Power input                          |
| 2   | 24V          | Power input                          |
| 3   | GND          | Ground                               |
| 4   | GND          | Ground                               |
| 5   | RS485-B      | RS485 differential line B            |
| 6   | RS485-A      | RS485 differential line A            |
| 7   | BUF_SW_1     | Buffer switch 1 (open collector)     |
| 8   | BUF_SW_2     | Buffer switch 2 (open collector)     |

Note: RS485-A and RS485-B polarity must match the mainboard’s RS485 interface.

---

## Connecting to the Printer

On Creality K2 series printers, the RS485 port is typically available as /dev/ttyS5.

To connect:

1. Match RS485-A and RS485-B lines between the printer and CFS.
2. Ensure a common ground between the printer and the CFS.
3. Power the CFS with 24V on pins 1 and 2.
4. Use a passive Y-splitter or T-tap if you need to capture traffic for debugging.

---

## Hardware Identification via Software

You can identify the CFS hardware version using the `CFS_VERSION` command:

```gcode
CFS_VERSION ADDR=1
````

Example response:

    11010000843215B625AHSC

This 22-character ASCII string may include hardware code, firmware version, and serial number.

***

## Notes

*   All known CFS hardware uses the same RS485 protocol.
*   No protocol version branching (e.g., V1/V2) has been observed.
*   The CFS uses a half-duplex RS485 bus; only one device should transmit at a time.
*   The CFS does not require RTS pin toggling; direction control is handled internally.

***

For troubleshooting hardware issues, see docs/troubleshooting.md.