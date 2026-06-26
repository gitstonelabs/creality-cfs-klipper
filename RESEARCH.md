# Research: CFS RS485 Protocol Reverse Engineering

This document describes the investigation that produced this module: what was
analyzed, how, what was found, and why it was necessary. The NOTICES.md in this
repository covers the GPL compliance situation. This file covers the research
methodology.

## Why this work exists

The Creality Hi 3D printer runs Klipper, which is licensed GPL-3.0. Klipper's
plugin system is designed to be extended in Python. Creality ships their filament
system (CFS) controller as two compiled Python extension modules, not as Python
source: `box_wrapper.cpython-39.so` (1.9 MB) and `serial_485_wrapper.cpython-39.so`
(141 KB). Both are distributed under GPL-3.0, and neither has corresponding source
available. GPL-3.0 Section 6 conditions the right to distribute object code on
making the corresponding source available. Creality has not done this for either
module.

A formal source code request was submitted to Creality twice. As of the date of
this document, neither request received a response. The next escalation is a
complaint to the Software Freedom Conservancy.

Because no source exists, the only path to a working open-source CFS integration
on Klipper was to reverse engineer the RS485 protocol the compiled modules implement.

## What was analyzed

The Creality Hi communicates with the CFS over a dedicated RS485 bus. The physical
interface runs at 230400 baud, 8N1, half-duplex. All CFS traffic passes through
`serial_485_wrapper.so`, and all filament box state and motor commands pass through
`box_wrapper.so`. To understand what either module does, you observe the RS485 bus
directly.

Three sources were used:

**RS485 traffic captures.** A USB-RS485 sniffer (CH341-based dongle) was attached
to the CFS bus on a live Creality Hi during T0, T1, T2, and T3 tool change sequences.
These captures record every byte on the bus in both directions during filament load,
filament retract, and box-to-box transitions. Raw capture files ship in `captures/`
so any finding in this repo can be independently verified against the original
traffic.

**String extraction from the .so files.** Running `strings` against both `.so` files
yielded class names, error strings, and command mnemonic labels. These were used to
correlate captured byte sequences with their semantic meaning. No decompilation was
performed.

**Creality's open Python.** Creality released `auto_addr_wrapper.py` as readable
source in the `CrealityOfficial/Hi_Klipper` repository. This file documents the
RS485 addressing handshake, including timing and command codes for the auto-address
sequence. Protocol constants are facts, not copyrightable expression, so citing
them here is a citation of observed behavior, not a copy.

## What was found

The RS485 frame format is:

```
[0xF7] [ADDR] [LEN] [STATUS] [CMD] [DATA...] [CRC8]
```

`0xF7` is the fixed start byte. `ADDR` is the box address (0x01 to 0x04, assigned
dynamically). `LEN` is the total byte count of the frame. `STATUS` is 0xFF for
operational commands and 0x00 for discovery. `CMD` is the command opcode. The CRC
is CRC-8/SMBUS: polynomial 0x07, init 0x00, scope is `msg[2:-1]` (everything after
the start byte, excluding the CRC byte itself).

Commands confirmed from live captures:

| Opcode | Name | Direction | Confirmed from |
|--------|------|-----------|----------------|
| 0x04 | CMD_GET_VERSION | host to box | tool-change capture |
| 0x08 | CMD_GET_BOX_STATE | host to box | tool-change capture |
| 0x0A | CMD_GET_BOX_SLOT | host to box | tool-change capture |
| 0x0C | CMD_SET_MODE | host to box | load sequence capture |
| 0x0E | CMD_GET_SLOT_STATE | host to box | tool-change capture |
| 0x10 | CMD_EXTRUDE_PROCESS | host to box | load sequence capture |
| 0x11 | CMD_RETRUDE_PROCESS | host to box | retract capture |
| 0xC0 | CMD_AUTO_ADDR | host to box | power-on capture |
| 0xF0 | CMD_FW_VERSION | host to box | init capture |

All commands in `creality_cfs.py` were derived from capture data, not inferred.
Each command has a corresponding raw frame in the `captures/` directory.

The protocol is confirmed identical across the Creality Hi, K1, and K2 hardware
lines based on community cross-reference.

## How the reimplementation was validated

The `creality_cfs.py` module was validated on a physical Creality Hi with a
real CFS box attached. Validation included:

- Single-slot filament load from initialization through hotend feed
- Filament retract back to parking position
- Multi-slot tool change (slot 1 to slot 2 to slot 3) with correct box motor
  sequencing and position tracking

The module runs on mainline Klipper over a USB-RS485 adapter (CH341 dongle),
with no Creality firmware, no Creality cloud, and no proprietary `.so` files
loaded. The physical CFS box is controlled entirely by the Python code in
this repository.

## Relationship to the GPL violation

Creality's own repository (`CrealityOfficial/K1_Series_Klipper`) contains the
source for ProTouch v1 (2,274 lines) and ProTouch v2 (2,202 lines), both GPL-3.0.
This proves they are capable of releasing source for Klipper extension modules.
For the CFS binaries (`box_wrapper.so` and `serial_485_wrapper.so`) they chose
not to. The reverse engineering in this repository is the direct result of that
choice. A user who receives GPL-licensed software in binary form without source
is entitled to the source. This module is what that source should have been.
