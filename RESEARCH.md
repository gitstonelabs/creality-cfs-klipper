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

Four sources were used:

**RS485 traffic captures.** A CH341 USB-RS485 adapter in passive tap mode was
attached to the CFS bus on a live Creality Hi during T0, T1, T2, and T3 tool
change sequences. These captures record every byte on the bus in both directions
during filament load, filament retract, and slot-to-slot transitions. The two
initial raw capture files ship in `captures/`; the later decode passes (function
code map, tool-change reconfirmation, a full 3-color print, the connect and
choreography timing work) came from further CRC-verified captures on the same
printer in the project's reverse-engineering workspace. Every function code and
payload decode in this repository traces to a capture.

**String extraction from the .so files.** Running `strings` against both `.so` files
yielded class names, error strings, and command mnemonic labels. These were used to
correlate captured byte sequences with their semantic meaning. No decompilation was
performed. Several early opcode hypotheses drawn from string correlation alone
turned out to be wrong and were later corrected against the wire (see the
superseded hypotheses section below).

**Creality's open Python.** Creality released `auto_addr_wrapper.py` as readable
source in the `CrealityOfficial/Hi_Klipper` repository. This file documents the
RS485 addressing handshake, including timing and command codes for the auto-address
sequence. Protocol constants are facts, not copyrightable expression, so citing
them here is a citation of observed behavior, not a copy.

**The open reference implementation.** The full load, unload, cut and flush
choreography was developed and hardware-validated in an open reference stack
deployed on a real Creality Hi with a CFS v1 box, exercised on the wire through
2026-07-01. The choreography in `creality_cfs.py` v1.4.0 is a port of those
hardware-validated decodes.

## What was found

### Frame format

```
[0xF7] [ADDR] [LEN] [STATUS] [FUNC] [DATA...] [CRC8]
```

`0xF7` is the fixed start byte. `ADDR` is the controller address (0x01 to 0x04,
assigned dynamically; buffer/feeder nodes sit at 0x81 and up). `LEN` is
`len(DATA) + 3`: it counts the STATUS, FUNC, DATA, and CRC bytes. It is not the
total frame length; the start byte, the address, and the LEN byte itself are not
counted. `STATUS` is 0xFF for host operational commands, and 0x00 for addressing
traffic and for all box replies. In box replies the STATUS byte doubles as the
async event channel: 0x00 idle, 0x30 insert/update push, 0x16 busy or active
calibration. `FUNC` is the function code. The CRC is CRC-8/SMBUS: polynomial
0x07, init 0x00, computed over `frame[2:-1]`, i.e. `[LEN][STATUS][FUNC][DATA]`
(the start byte, the address, and the CRC byte itself are excluded).

### Function codes

The protocol has two layers. The addressing layer (STATUS 0x00) assigns bus
addresses at power-on:

| Func | Name | Purpose |
|------|------|---------|
| 0x0B | LOADER_TO_APP | Wake boxes from the bootloader |
| 0xA0 | SET_SLAVE_ADDR | Assign an address to a specific UniID |
| 0xA1 | GET_SLAVE_INFO | Discover boxes by UniID |
| 0xA2 | ONLINE_CHECK | Verify an address assignment |
| 0xA3 | GET_ADDR_TABLE | Confirm the full address table |

The operational layer (host STATUS 0xFF) does the actual work. All codes below
are CRC-verified from live captures on the Hi RS485 wire:

| Func | Name | Notes |
|------|------|-------|
| 0x02 | READ_MATERIAL | Slot-bitmask selected; ASCII map reply (`A:unknown;B:none;...`) |
| 0x03 | READ_REMAIN | Slot-bitmask selected; positional 4-byte reply, 0xFF = not-in-mask sentinel |
| 0x04 | SET_BOX_MODE | `[0x00][slot]` enters feed mode; `[slot][0x00]` is the per-slot print mode |
| 0x05 | CUT_STATE | Read-only, after the mechanical cut: 0x00 cut OK, 0x01 transient, 0x02 nothing to cut (slot empty) |
| 0x08 | GET_HARDWARE_STATUS | `[channel]` request, 1 flag byte reply; 0x01 is the idle value |
| 0x0A | GET_BOX_STATE | EMPTY request payload; 4-byte reply `[b0][b1][b2][b3]`: b0/b1 are an opaque drifting firmware base carrying no state, b2 is a substatus, b3 is the load flag (0x02 loaded/print-locked, 0x00 feed mode) |
| 0x0C | GET_BUFFER_STATE | Buffer node (0x81+) only; 8-byte block, all-zero = empty |
| 0x0D | SET_PRE_LOADING | `[mask][phase]`: arm `[0f][00]`, disarm `[0f][01]`, connect self-check `[00][01]` then `[0f][01]` only, per-slot re-arm `[slot][02]` (blocks ~38 s); reply STATUS 0x00 ACK, 0x16 NAK |
| 0x0E | MEASURING_WHEEL | Data `[0x01]`; reply is a 4-byte big-endian IEEE-754 float, negative, magnitude grows as filament feeds |
| 0x0F | CTRL_CONNECTION_MOTOR_ACTION | 0x01 engage, 0x00 release |
| 0x10 | EXTRUDE_PROCESS | `[slot][stage_hi][stage_lo]`; the sensor-gated load ramp, see below |
| 0x11 | RETRUDE_PROCESS | START/FINISH unload pair, see below |
| 0x14 | GET_VERSION_SN | 22-byte ASCII version/serial string |
| 0xF0 | VERSION_INFO | ASCII firmware version string |

### Bus topology

One controller per CFS box, at bus address 0x01. Tools and slots are NOT bus
addresses: they are a one-hot bitmask in the data bytes, 0x01/0x02/0x04/0x08 for
T0 through T3. Multi-box daisy-chains are additional controllers at 0x02 to 0x04,
a separate axis from tool slots. Buffer/feeder nodes at 0x81 and up answer only
the 0x0C buffer-state read; function-0x11 frames observed at 0x81/0x82 on the
reference printer are X/Y FOC servo traffic sharing the bus, not CFS retrude.

### Choreography findings

The load (0x10) is sensor-gated, not position-settled. The stage sequence is
`00 00` (init/arm), `04 00` (engage), `05 00` (push and measure), `06 00`
(settle), `07 03` (finalize). The push repeats, and the 06/07 stages fire only
after the toolhead filament switch trips; the whole cycle is re-armed with a
fresh init until the switch latches. The box HOLDS each stage reply until the
mechanical step completes (init and finalize around 4.5 s, push around 2 s); the
blocking reply is the ready mechanism, there is no host poll. The push reply
payload is the 4-byte big-endian IEEE-754 measuring-wheel float.

The unload (0x11) is a START/FINISH command pair, both frames carrying the slot
bitmask: `[slot][00]` then `[slot][01]`. Both ACK with the bare status-0x00
frame, and the FINISH ACK is held about 9.6 s while the box reels the filament
in. Completion is gated on the toolhead filament switch clearing, not on any
reply status. One toolhead `G1 E-15 F360` pull is interleaved between the two
frames.

Connect timing matters: after the 0xA0 address assign the box slave MCU needs
about 9.5 s to wake, and the first 0x0A after a quiet period legitimately gets
no reply. The module probes with a 12 s single shot and bounded retries before
running the connect-init burst.

One finding is critical for mainline Klipper specifically: mainline keeps the
`min_extrude_temp` protection that the Creality fork deletes, so every hotend
`G1 E` move the module issues is preceded by a blocking M109. The box-motor feed
bypasses Klipper's cold-extrude protection entirely (it is not an extruder
move), so the module enforces its own 170 C floor before feeding filament
toward the hotend.

### K1/K1C/K2 compatibility

Not confirmed. The K1-family firmware is a CAN build that remaps at least
0x02, 0x05, 0x08, and 0x0C to different numbers. Nothing in this repository has
been tested against K1-family hardware; treat any cross-family use as untested
and do not assume the RS485 codes documented here apply.

## Superseded early hypotheses

This section is historical. These decodes appeared in earlier revisions of this
document and the module, and were each disproven by later CRC-verified captures.
They are kept so old notes and forks can be reconciled.

- **Early opcode table from string correlation.** An early table assigned
  0x04 = GET_VERSION, 0x08 = GET_BOX_STATE, 0x0A = GET_BOX_SLOT,
  0x0C = SET_MODE, 0x0E = GET_SLOT_STATE, and 0xC0 = AUTO_ADDR. None of these
  match the wire. The actual assignments are in the table above: 0x04 is
  SET_BOX_MODE, 0x08 is GET_HARDWARE_STATUS, 0x0A is GET_BOX_STATE, 0x0C is
  GET_BUFFER_STATE, 0x0E is MEASURING_WHEEL, and there is no 0xC0; addressing
  is the 0x0B/0xA0-0xA3 layer.
- **LEN as total frame length.** LEN counts STATUS, FUNC, DATA, and CRC
  (`len(DATA) + 3`), not the whole frame.
- **The 0x10 reply as `[motor state 0xC3/0xC4][uint16 position]`.** A misparse.
  The payload is a 4-byte big-endian IEEE-754 float; the "state" byte was the
  float's exponent byte.
- **The 0x11 status-poll completion model.** Wire-disproven. Both 0x11 frames
  ACK with the bare status-0x00 frame and completion is gated on the toolhead
  filament switch, not a reply status.
- **The 0x0A request param byte and b0/b1 state decode.** The request is sent
  empty, and b0/b1 are an opaque per-firmware base (multiple values observed on
  identical hardware); the load flag is data[3].
- **The 0x0E "unknown decode".** Resolved: big-endian IEEE-754 float, negative,
  magnitude-monotonic as filament feeds.

## How the reimplementation was validated

Validation status is layered, and the layers differ. Stating them precisely:

**Transport, framing, CRC, and addressing: capture-validated on this module.**
The frame builder and parser in `creality_cfs.py` are verified against the raw
captures (the CRC algorithm against 16 captured test vectors, the addressing
sequence against the power-on capture in `captures/`).

**Choreography: hardware-validated on the reference implementation.** The load,
unload, cut-read, and flush sequences, including the stage bytes, the held-reply
timings, and the sensor gating, were exercised end to end on a real Creality Hi
with a CFS v1 box by the open reference stack this module ports from, through
2026-07-01. Same wire protocol, same printer family, same box hardware.

**This module's port of the choreography: wire-faithful, not yet exercised on
hardware.** The v1.4.0 port reproduces the validated frame sequences and timing
budgets exactly, but this specific module has not yet driven a physical CFS box
itself. It is neither an untested guess nor fully validated here; it is a
faithful port of a hardware-validated decode awaiting its own bench run.

The design target is mainline Klipper with a dedicated serial port (for example
a CH341 USB-RS485 adapter), no Creality firmware, no Creality cloud, and no
proprietary `.so` files loaded.

## Relationship to the GPL violation

Creality's own repository (`CrealityOfficial/K1_Series_Klipper`) contains the
source for ProTouch v1 (2,274 lines) and ProTouch v2 (2,202 lines), both GPL-3.0.
This proves they are capable of releasing source for Klipper extension modules.
For the CFS binaries (`box_wrapper.so` and `serial_485_wrapper.so`) they chose
not to. The reverse engineering in this repository is the direct result of that
choice. A user who receives GPL-licensed software in binary form without source
is entitled to the source. This module is what that source should have been.
