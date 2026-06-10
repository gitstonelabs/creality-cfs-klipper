# NOTICES

This file records the license provenance of the CFS Klipper module in this repository, names the Creality binaries it replaces, and states the method used to write it. It exists because the upstream license terms were not honored, and a downstream user is entitled to know exactly what they are running and where it came from.

## The GPL-3.0 facts

Creality's control software for the Creality Filament System on the Hi is conveyed as compiled CPython extension modules: `box_wrapper.cpython-39.so` (the `MultiColorMeterialBoxWrapper` class), `serial_485_wrapper.cpython-39.so`, and `filament_rack_wrapper.cpython-39.so`, distributed in `CrealityOfficial/Hi_Klipper`. Those modules are licensed GPL-3.0. GPL-3.0 Section 6 conditions the right to convey object code on conveying the Corresponding Source. For these modules the corresponding source has not been provided.

The source was requested from Creality twice. As of 2026-06-06 it has not been published. If the request stays unanswered, the next escalation is a complaint to the Software Freedom Conservancy.

## What this repository is

`src/creality_cfs.py` is a clean-room reimplementation of the CFS RS485 protocol. It was written from observed behavior, not from Creality's source, because Creality's source for the compiled CFS modules does not exist in public. None of the code here was decompiled from a `.so` and pasted back into Python. The method was:

1. Live RS485 capture. A USB-RS485 sniffer on a real Creality Hi during T0 to T3 tool changes recorded the actual bus traffic. The raw captures ship in `captures/` for independent verification.
2. Protocol decode. The `0xF7` frame format, the CRC-8/SMBUS check (poly 0x07, init 0x00), the command set, and the dynamic addressing were derived from those captures and confirmed byte for byte.
3. On-device open Python. `auto_addr_wrapper.py`, which Creality shipped as readable source in `CrealityOfficial/Hi_Klipper`, supplied the addressing handshake and timing. Running `strings` on the closed `.so` modules confirmed command names. Protocol values and timing constants are facts, not copyrightable expression.

The result was validated on physical Creality Hi hardware with a real CFS box, and confirmed working on mainline Klipper over a USB-RS485 adapter, independent of Creality hardware and firmware. The protocol is confirmed identical across the Hi, K1, and K2.

## Provenance note

The addressing logic cites Creality's open `auto_addr_wrapper.py` for its handshake timing and command codes. That file is open source on the device, and protocol values are not copyrightable expression, so this is a citation of fact, not a copy. The module is otherwise original code.

## Relationship to the other repositories

This is the upstream of record for the `creality_cfs.py` that ships, feature-parallel, in `creality-klipper-unlocked` and `creality-custom-OS`. Changes land here first; the bundled copies track this one.

## What a user is entitled to

You can run the Creality CFS on any Klipper printer, on a stock Hi running Creality's Klipper or on mainline Klipper over a USB-RS485 adapter, with source you can read and modify. That source is the thing GPL-3.0 was supposed to guarantee and that Creality did not deliver for the compiled CFS modules.

## License

Every file in this repository is GPL-3.0-or-later, matching the license Creality placed on the binaries and matching the Klipper project. See `LICENSE`.
