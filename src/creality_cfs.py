"""
creality_cfs.py: Klipper Extra Module for Creality Filament System (CFS)

Protocol version: CFS RS485 v1 (single version)
Klipper compatibility: v0.11.0+
License: GPL-3.0 (matching Klipper project)
Author: gitstonelabs

Protocol reverse-engineered from live RS485 capture on Creality Hi.
CRC algorithm validated against 16 test vectors.
Command IDs, payload structures, and response formats confirmed from capture.

Changelog:
  v1.0.0 (2026-03-27): Initial production release. 9 confirmed commands implemented,
                         0x10/0x11 stubbed. Full auto-addressing sequence (5-step).
  v1.1.0 (2026-05-20): CMD_EXTRUDE_PROCESS (0x10) and CMD_RETRUDE_PROCESS (0x11)
                         fully implemented from live RS485 capture during T0->T1->T2->T3
                         tool-change on Creality Hi with CFS v1 box.
                         Capture: cfs_toolchange_capture_20260520_013844.bin
                         0x10 sub-commands: 0x02/0x00 init, 0x02/0x04 poll,
                         0x02/0x05 streaming position feedback.
                         0x10 response: 1-byte motor state + 2-byte uint16 position
                         (units: 0.01mm). Motor state 0xc3=accel, 0xc4=at speed.
                         0x11: sub-command 0x02/0x01, ACK-only response.
                         0xf0 VERSION_INFO decoded: ASCII firmware version string.
                         Filament path length confirmed: ~398-400mm to toolhead.
  v1.1.1 (2026-06-03): Cross-referenced against the STOCK box_wrapper.cpython-39.so + the rest of
                         the on-board CFS stack (the project's CFS protocol notes):
                         * 3 of 5 CFS modules ship as open Python on the Hi already: auto_addr,
                           external_material (RFID reader @ addr 0x11, cmd 0x02), and steer (the CFS
                           CAMERA module @ addr 0x41, GET_STATE 0x0A heartbeat; the long-unknown
                           "0x41" device on the bus is the steer/camera, not a box).
                         * Stock box exposes ~17 BoxAction.communication_* RS485 methods. The 7
                           implemented here are confirmed identical; the other 10 are listed below
                           as CMD_*_TODO (codes are not readable in .so strings; a CFS load capture
                           via tools/capture_cfs_traffic.py is needed to fill them).
                         * A Hi-side serial_485-wired sibling of this driver lives at
                           the box module (uses the shared transport
                           instead of pyserial, since on the Hi /dev/ttyS5 is owned by serial_485).
  v1.2.0 (2026-06-19): Wire-evidenced protocol corrections from the live Hi RS-485 tool-change
                         captures (reverse-engineering/captures/cfs-re/cfs_func_code_map_2026-06-09.md
                         and cfs_toolchange_reconfirm_2026-06-19.md, both CRC-verified):
                         * CMD_GET_BOX_STATE corrected 0x08 -> 0x0A. The 0x08 code is a SEPARATE
                           command, GET_HARDWARE_STATUS (the toolhead filament-sensor read). The
                           v1.1.0 changelog's "0x0A->0x08" fix was itself wrong: on the Hi wire 0x0A
                           IS box-state and 0x0B (not 0x0A) is LOADER_TO_APP (already correct here).
                         * get_box_state() now decodes the 0x0A 4-byte state word: data[0]=0x1a class
                           byte, data[1]=lo byte (0x20 LOADED, 0x1f FEEDING). The old 0x0f/0x00/0x02
                           single-flag model never matched the 0x0A payload.
                         * extrude_process()/retrude_process() were slot-locked to T1 (hardcoded
                           0x02). They now take a 1-hot slot bitmask (T0=0x01, T1=0x02, T2=0x04,
                           T3=0x08). retrude payload is [slot, phase], phase 0x00 start then 0x01
                           running (was wrongly [sub, slot] and skipped phase 0x00). On the buffer
                           node addr 0x81 the retrude payload is a single channel byte.
                         * Added CUT_STATE (0x05, reads cut-state after the mechanical cut),
                           GET_HARDWARE_STATUS (0x08), CTRL_CONNECTION_MOTOR_ACTION (0x0F engage/
                           release, Hi uses 0x0F not the CAN binary's 0x07), and MEASURING_WHEEL
                           (0x0E; raw 4-byte word returned, numeric decode is an OPEN TODO: the
                           0x0E RX leads with 0xc5 OR 0xc4 across captures, so [tag][3-byte BE] vs
                           float32-LE is unresolved; do NOT assume a scale).
                         * CMD_CREATE_CONNECT_TODO (guessed 0x01) aliased to CMD_GET_ADDR_TABLE
                           (0xA3): the connect / get-addr-table func is 0xA3 on the wire.
                         * extrude_process() STREAM loop is now settle-based (EXTRUDE_SETTLE_THRESHOLD)
                           with a path-length timeout, replacing the fixed EXTRUDE_POLL_MAX=8 count
                           that could finalize before the filament reached the toolhead.
  v1.2.1 (2026-06-19): Second-pass wire corrections from the live 3-color print capture
                         (hi_rs485_3color_print_2026-06-19.json, CRC-clean) and the
                         cfs_toolchange_reconfirm_2026-06-19.md cross-check:
                         * SET_BOX_MODE (0x04) per-channel form wired to the slot. The 0x04 payload
                           has two wire forms: the ENTER form [mode, param] = [00 01] that brackets a
                           tool change, and a PER-CHANNEL (print-mode) form [slot_bitmask, 0x00]
                           observed 01 00 / 02 00 / 04 00 keyed to the active slot. cmd_CFS_SET_MODE
                           now takes an optional TOOL=<0-3> (maps to SLOT_BITMASKS) for the
                           per-channel form via set_box_mode_channel(); the ENTER form stays available
                           via MODE/PARAM.
                         * extrude_process() load ramp completed. The STREAM loop only issued
                           0000/0400/0500 and never the wire's 0600 (SETTLE) and 0703 (FINALIZE,
                           data byte 0x03) stages, so a real load never finished the way stock does.
                           SETTLE then FINALIZE are now issued after the STREAM loop converges or
                           times out, per the 06-09 ramp 0000/0400/0500/0600/0703. Added
                           EXTRUDE_SUB_SETTLE=0x06, EXTRUDE_SUB_FINALIZE=0x07, EXTRUDE_FINALIZE_DATA=0x03
                           and settle_ok/finalize_ok/complete keys in the result dict.
  v1.3.0 (2026-06-19): B1 mainline-acceptance blocker: serial transport rewritten from
                         blocking pyserial to a reactor-friendly, non-blocking model so it
                         NEVER blocks the Klipper reactor greenlet. The fd is opened
                         non-blocking (os.open O_NONBLOCK + raw 8N1 termios) and registered
                         with the reactor (reactor.register_fd); a read callback buffers and
                         frames incoming bytes (reusing the EXISTING framing + crc8_cfs verify)
                         and completes the pending request's reactor.completion. _send_command
                         writes the request, arms a reactor timer for the timeout, and parks the
                         caller in completion.wait() so the reactor keeps servicing the MCU
                         keepalive and other events during the wait -- this looks synchronous to
                         callers but releases the greenlet. The OS-blocking serial.read and the
                         reset_input_buffer that ran on the reactor path were removed; partial
                         reads are handled in the fd callback. A reactor.mutex() serializes the
                         half-duplex bus. The deferred auto-init (register_callback off
                         klippy:ready) is preserved; the fd is unregistered cleanly and any
                         pending waiter is aborted on klippy:disconnect/shutdown. The public
                         API of every caller (get_box_state, extrude_process incl. the
                         STREAM/SETTLE/FINALIZE sequence, retrude_process, cut_state,
                         get_hardware_status, measuring_wheel, ctrl_connection_motor_action,
                         set_box_mode, set_box_mode_channel) is UNCHANGED.
                         TARGET: a portable mainline-Klipper CFS extra on a non-Hi host with its
                         OWN dedicated serial port. It does NOT share serial_485; it keeps owning
                         its own port. serial_port is effectively REQUIRED off-Hi because the
                         CFS_DEFAULT_PORT /dev/ttyS5 is Hi-specific (on the Hi that node is owned
                         by serial_485 anyway). RS485 direction is left to an auto-direction
                         adapter by default; opt in to kernel RS485 RTS via rts_on_send.
  v1.4.0 (2026-07-05): CHOREOGRAPHY REBUILD from the hardware-validated reference stack
                         (the open box.py deployed and exercised on a real Creality Hi + CFS
                         through 2026-07-01: load, unload, flush and cut-read all verified on
                         the wire). Every post-2026-06-19 protocol decode is now ported:
                         * LOAD (0x10): the fixed 5-stage settle-based ramp is replaced by the
                           SENSOR-GATED push loop -- the 0x05 push repeats and the 0x06/0x07
                           finalize fires only after the TOOLHEAD filament switch trips, with
                           whole-cycle re-arms (fresh 0x00 init) until the switch latches, a
                           per-push wheel-advance watchdog for the box's ~3-push per-arm
                           self-limit, 15 s blocking per-stage replies (the real ready
                           mechanism; the box HOLDS each reply), and a 90 s wall budget.
                         * 0x10 push reply decode CORRECTED: the payload is a 4-byte BE
                           IEEE-754 wheel float (negative, magnitude-monotonic). The old
                           [state 0xC3/0xC4][uint16 0.01mm] model was a misparse (the 'state'
                           byte was the float's exponent byte).
                         * UNLOAD (0x11): rebuilt to the START/FINISH pair (both frames carry
                           the slot bitmask) with ONE interleaved toolhead G1 E-15 F360 pull,
                           0x08 00/01 sensor prep reads, a finish timeout covering the ~9.6 s
                           held FINISH ACK, and completion gated on the toolhead filament
                           switch clearing (the 0x11 reply status is wire-disproven as a gate
                           and is now diagnostic-only). 60 s wall budget.
                         * GET_BOX_STATE (0x0A): the request is sent EMPTY (no param byte) and
                           the loaded flag is data[3]==0x02. b0/b1 are an opaque per-firmware
                           base (0x1a20/0x1b26/0x1c24/0x1d21 all observed) and are no longer
                           decoded as state. The frame STATUS byte is surfaced as the async
                           event channel (0x30 insert push, 0x16 busy/cal).
                         * SET_PRE_LOADING (0x0D): payload generalized to [mask][phase] and the
                           INVERTED gcode mapping fixed -- arm is phase 0x00, disarm 0x01 (the
                           old ENABLE=1 sent the wire DISARM). The reply STATUS byte is now
                           checked (0x00 ACK; 0x16 NAK) and blocking phases get real timeouts
                           so the host can never hang up mid-phase and NAK-wedge the box.
                         * CUT: CFS_CUT mechanical cut ram added (switch-guard, zero-travel
                           refusal, M109 preheat, 0x05 post-check with the 0x02 nothing-to-cut
                           decode).
                         * FLUSH: CFS_FLUSH hotend purge loop added (total = nozzle_volume/2.4
                           + (5/12)*flush_volume*multiplier, 80 mm per-cycle cap, measuring-
                           wheel under-feed/clog watchdog, optional per-cycle wipe macro,
                           final 1.5 mm retract).
                         * TEMP GUARDS (critical for mainline): every hotend G1 E move is
                           preceded by a blocking M109 and a MIN_EXTRUDE_TEMP floor check
                           (mainline KEEPS the min_extrude_temp raise the Creality fork
                           deletes), and the box-motor feed -- which bypasses Klipper's
                           protection entirely -- enforces the same floor before feeding
                           toward the hotend.
                         * Slot presence reads added (0x02 READ_MATERIAL / 0x03 READ_REMAIN,
                           slot-bitmask selected) and 0x0C GET_BUFFER_STATE on the buffer node.
                         * Connect timing: after addressing, boxes get a wake-sized 12 s
                           single-shot 0x0A probe with bounded retries (the box slave-MCU
                           needs ~9.5 s after the 0xA0 assign and the first 0x0A after quiet
                           legitimately returns None), then the stock connect-init burst
                           (feed-mode, version, the two-frame pre-load self-check -- stock
                           sends NO [0f][02] -- and the all-slot presence read).
                         * get_status() added so printer["creality_cfs"] resolves in macros.
                         * T0..T3 macros fixed: tools select the SLOT BITMASK on the single
                           controller at addr 0x01 (TOOL=0..3), NOT bus addresses 1..4.
                           Multi-box daisy-chains are a separate axis from tool slots.

Known limitations:
  - Half-duplex RS485 direction switching is left to a hardware auto-direction adapter
    by default. Opt in to the kernel RS485 RTS-as-DE mode with rts_on_send=1 (or 0 for
    RTS-low-on-send); rts_on_send=-1 (default) leaves the UART alone.
  - Serial I/O is fully non-blocking and reactor-driven (v1.3.0). A registered fd callback
    parses incoming frames; a reactor.completion delivers the matched response to the waiting
    caller, which parks in completion.wait() bounded by a reactor timer. No call blocks the
    reactor greenlet.
  - 0x05 CUT_STATE: 0x00 (cut OK) and 0x02 (nothing to cut / empty slot) are wire-decoded; a
    failing-cut counter-example (filament present, blade jammed) is still uncaptured, so any
    other value is treated as 'cut not confirmed'.
  - The choreography constants (stage timings, self-limit thresholds, flush split) were
    hardware-validated on a Creality Hi + CFS v1 box. Other hosts/boxes are expected to match
    (the box firmware paces itself via the blocking replies) but have not been bench-verified.

Resolved in v1.4.0:
  - 0x0E MEASURING_WHEEL numeric decode: a 4-byte BIG-ENDIAN IEEE-754 float (negative,
    magnitude grows as filament feeds). measuring_wheel_mm() returns the decoded mm value;
    measuring_wheel() still returns the raw word for monotonic-advance checks.
Resolved in v1.2.0:
  - 0x10 STREAM poll is no longer a fixed count (superseded again in v1.4.0 by the
    sensor-gated push loop).
"""

import logging
import os
import struct

# ---------------------------------------------------------------------------
# POSIX-only serial imports. fcntl/termios do not exist off-POSIX (e.g. the
# Windows dev/CI box that runs the protocol unit tests). The live reactor-fd
# transport is POSIX-only by design; guard the imports so the module still
# IMPORTS and the class still CONSTRUCTS off-POSIX (the CRC/framing/command
# logic is fully testable without a real fd). Opening the live serial port
# off-POSIX raises a clear error in _connect_serial(), not at import time.
# ---------------------------------------------------------------------------
try:
    import fcntl
    import termios
    _HAS_POSIX_SERIAL = True
except ImportError:                 # pragma: no cover - exercised only off-POSIX
    fcntl = None
    termios = None
    _HAS_POSIX_SERIAL = False

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------
PACK_HEAD: int = 0xF7           # Fixed message header byte
BROADCAST_ADDR_MB: int = 0xFE   # Broadcast address for material boxes (料盒)
BROADCAST_ADDR_ALL: int = 0xFF  # Broadcast address for all devices

# STATUS byte values
STATUS_ADDRESSING: int = 0x00   # Used for auto-addressing commands and responses
STATUS_OPERATIONAL: int = 0xFF  # Used for host operational commands

# Address range for individual boxes
ADDR_BOX_MIN: int = 0x01
ADDR_BOX_MAX: int = 0x04

# Maximum data payload bytes (LENGTH field covers STATUS+FUNC+DATA+CRC, data max = 251)
MAX_DATA_LEN: int = 100         # Practical limit observed in reference code
MAX_UNIID_LEN: int = 12         # UniID byte length for CFS boxes

# Minimum valid response length: HEAD(1)+ADDR(1)+LEN(1)+STATUS(1)+FUNC(1)+CRC(1) = 6
MIN_MSG_LEN: int = 6

# Serial defaults
# NOTE (v1.3.0): CFS_DEFAULT_PORT is Hi-specific. On the Creality Hi /dev/ttyS5 is the
# mainboard RS-485 node and is owned by the serial_485 transport, so this default only makes
# sense on the Hi. OFF-Hi (the portable mainline target) serial_port is effectively REQUIRED:
# set it to your dedicated CFS port, e.g. a USB-RS485 adapter like /dev/ttyUSB0 or /dev/ttyACM0.
CFS_DEFAULT_PORT: str = "/dev/ttyS5"   # default RS485 port (Hi-specific; set serial_port off-Hi)
CFS_BAUD_RATE: int = 230400            # validated baud rate
CFS_SERIAL_BYTESIZE: int = 8
CFS_SERIAL_PARITY: str = "N"
CFS_SERIAL_STOPBITS: int = 1
CFS_READ_CHUNK: int = 256              # max bytes drained per fd-readable callback

# Linux RS-485 ioctl (drivers/tty): optionally put the UART in hardware RS-485 mode so RTS acts
# as the transceiver direction (DE) line. Left OFF by default (rts_on_send=-1) for portability:
# most USB-RS485 adapters are auto-direction and need no RTS toggling. Mirrors serial_485_wrapper.
TIOCSRS485: int = 0x542F
SER_RS485_ENABLED: int = (1 << 0)
SER_RS485_RTS_ON_SEND: int = (1 << 1)
SER_RS485_RTS_AFTER_SEND: int = (1 << 2)

# Timing constants from Hi_Klipper/klippy/extras/auto_addr_wrapper.py
TIMEOUT_LONG: float = 1.0    # CMD_GET_SLAVE_INFO discovery broadcast (may block ~1 s)
TIMEOUT_SHORT: float = 0.05  # CMD_SET_SLAVE_ADDR, CMD_GET_ADDR_TABLE, CMD_LOADER_TO_APP
TIMEOUT_MEDIUM: float = 0.1  # CMD_ONLINE_CHECK and operational commands

# Default retry count for operational commands
DEFAULT_RETRY_COUNT: int = 3

# Maximum addressing passes
MAX_GET_TIMES: int = 2
MAX_SET_TIMES: int = 2
MAX_LOST_CNT: int = 3

# ---------------------------------------------------------------------------
# Command function codes
# ---------------------------------------------------------------------------
# Auto-addressing commands (STATUS = 0x00)
CMD_LOADER_TO_APP: int = 0x0B   # Wake boxes from loader; confidence 97%
CMD_GET_SLAVE_INFO: int = 0xA1  # Discover boxes by UniID; confidence 97%
CMD_SET_SLAVE_ADDR: int = 0xA0  # Assign address to a specific UniID; confidence 97%
CMD_ONLINE_CHECK: int = 0xA2    # Verify address assignment; confidence 95%
CMD_GET_ADDR_TABLE: int = 0xA3  # Confirm full address table; confidence 95%

# Operational commands (STATUS = 0xFF)
CMD_SET_BOX_MODE: int = 0x04    # Set box operating mode; confidence 97%
CMD_GET_BOX_STATE: int = 0x0A   # Get box state word; WIRE-CONFIRMED 2026-06-09/06-19.
                                # WAS 0x08 (wrong; 0x08 is GET_HARDWARE_STATUS, see below).
CMD_GET_HARDWARE_STATUS: int = 0x08  # Toolhead filament-sensor / hardware status flags;
                                     # WIRE-CONFIRMED 2026-06-09. (Was CMD_GET_HARDWARE_STATUS_TODO.)
CMD_CUT_STATE: int = 0x05       # Read cut-state AFTER the mechanical cut; WIRE-CONFIRMED 2026-06-09.
CMD_MEASURING_WHEEL: int = 0x0E # Feed encoder/measuring-wheel word; WIRE-CONFIRMED 2026-06-09.
CMD_CTRL_CONNECTION_MOTOR_ACTION: int = 0x0F  # Engage(0x01)/release(0x00) feeder motor;
                                              # WIRE-CONFIRMED 2026-06-09. Hi uses 0x0F, NOT the
                                              # CAN binary's 0x07.
CMD_SET_PRE_LOADING: int = 0x0D # Set pre-loading slot mask; confidence 93%
CMD_GET_VERSION_SN: int = 0x14  # Get 22-byte version/SN string; confidence 97%

# Confirmed commands (v1.1.0) - payloads decoded from live RS485 capture
CMD_EXTRUDE_PROCESS: int = 0x10  # sensor-gated load ramp -- see extrude_load_ramp_gated()
CMD_RETRUDE_PROCESS: int = 0x11  # START/FINISH unload pair -- see unload_process()
CMD_VERSION_INFO: int = 0xF0     # CONFIRMED v1.1.0 - ASCII firmware version string

# Data/sensor commands, WIRE-CONFIRMED on the Hi RS-485 wire (2026-06-09, CRC-verified frames).
# *** TRANSPORT WARNING: the K1-family CAN build uses DIFFERENT numbers for these (it remaps
# 0x02/0x05/0x08/0x0c) -- do not cross-use the CAN binary's numbering on the RS-485 wire. ***
CMD_GET_FILAMENT_SENSOR_STATE: int = 0x02  # slot-material ASCII map ('A:unknown;B:none;...'),
                                           # slot-bitmask selected ('none'=empty, 'unknown'=
                                           # inserted-no-tag, a label=RFID-identified)
CMD_GET_REMAIN_LEN: int = 0x03             # per-slot remain byte(s), slot-bitmask selected;
                                           # positional 4-byte reply, 0xFF = not-in-mask sentinel
CMD_GET_BUFFER_STATE: int = 0x0C           # buffer node (0x81+) 8-byte block; all-zero = empty
# The RFID/material read shares func 0x02 on this wire; the tag-LABEL byte decode out of the
# reply is still pending a tagged-spool capture.
CMD_GET_RFID: int = CMD_GET_FILAMENT_SENSOR_STATE
# Still genuinely unknown -> None so no bogus frame is ever sent.
# CMD_CREATE_CONNECT was a guessed 0x01; the connect / get-addr-table func is 0xA3 on the wire.
CMD_CREATE_CONNECT_TODO: int = CMD_GET_ADDR_TABLE  # = 0xA3 (addressing layer), NOT an app connect
CMD_COMMUNICATION_TEST_TODO = None
CMD_EXTRUDE2_PROCESS_TODO = None
CMD_TIGHTEN_UP_ENABLE_TODO = None

# ---------------------------------------------------------------------------
# 0x10 EXTRUDE_PROCESS stage bytes (hardware-validated 2026-06-30 on a Creality Hi + CFS;
# behavioral source: the deployed open box.py reference stack, extrude_load_ramp_gated)
# ---------------------------------------------------------------------------
# Every 0x10 frame carries THREE data bytes: [slot_bitmask][stage_hi][stage_lo] (LEN 0x06).
EXTRUDE_SUB_INIT: int = 0x00      # [slot] 00 00 -- init / ARM the feed cycle
EXTRUDE_SUB_ENGAGE: int = 0x04    # [slot] 04 00 -- engage stage (reply held ~4.5 s)
EXTRUDE_SUB_PUSH: int = 0x05      # [slot] 05 00 -- feed push + measure (reply carries the wheel)
EXTRUDE_SUB_SETTLE: int = 0x06    # [slot] 06 00 -- settle; ONLY after the toolhead switch trips
EXTRUDE_SUB_FINALIZE: int = 0x07  # [slot] 07 03 -- finalize/commit the load
EXTRUDE_FINALIZE_DATA: int = 0x03
# Back-compat aliases (pre-v1.4.0 names for the same stage bytes; 0x04 is an engage stage the
# box blocks on, not a status poll, and 0x05 is the repeated feed push, not a position stream).
EXTRUDE_SUB_POLL: int = EXTRUDE_SUB_ENGAGE
EXTRUDE_SUB_STREAM: int = EXTRUDE_SUB_PUSH

# *** 0x10 push-reply decode (v1.4.0 CORRECTION): the 0x05 push reply payload is a 4-byte
# BIG-ENDIAN IEEE-754 FLOAT -- the cumulative measuring-wheel position (negative; magnitude
# grows as filament feeds, ~300 counts per real push, ~0 when the box fast-acks a self-limited
# no-op push). The pre-v1.4.0 [motor_state 0xC3/0xC4][uint16 0.01mm] model was a MISPARSE:
# the 'state' byte was the float's exponent byte. Decode with struct.unpack('>f', data[:4]).
#
# The load is SENSOR-GATED, not position-settled: the validated behavior LOOPS the 0x05 push
# and gates the 0x06/0x07 finalize on the TOOLHEAD filament switch, RE-ARMING the whole cycle
# (fresh [slot] 00 00) until the switch latches. The box self-limits to ~3 real pushes per
# arm, then fast-acks no-op pushes; the per-push wheel-advance watchdog below detects that so
# the loop re-arms immediately instead of grinding dead pushes.
EXTRUDE_STAGE_TIMEOUT_S: float = 15.0   # the box HOLDS each stage reply until the mechanical
                                        # step completes (init/finalize ~4.5 s, push ~2 s); the
                                        # blocking per-stage reply IS the ready mechanism --
                                        # there is no host poll. 15 s covers the longest hold.
LOAD_TOPUP_MAX_BURSTS: int = 5          # per-arm push cap (box self-limits ~3 real pushes)
LOAD_TOPUP_WALL_BUDGET_S: float = 90.0  # wall-clock ceiling for the whole sensor-gated load
LOAD_PUSH_MIN_ADVANCE: float = 50.0     # wheel delta below this = the box fed nothing this push
LOAD_PUSH_STALL_LIMIT: int = 2          # consecutive no-feed pushes before re-arming the cycle

# Filament path length reference (confirmed from capture, units: mm)
# ~398-400mm = physical path from CFS motor to toolhead sensor on the reference printer.
FILAMENT_PATH_LENGTH_MM: float = 400.0

# ---------------------------------------------------------------------------
# Response state codes from klipper-cfs/extras/creality_cfs.py community impl
# ---------------------------------------------------------------------------
RESP_OK: int = 0x00
RESP_PARAMS_ERR: int = 0x01
RESP_CRC_ERR: int = 0x02
RESP_STATE_ERR: int = 0x03
RESP_LENGTH_ERR: int = 0x04
RESP_EXTRUDE_ERR1: int = 0x05
RESP_MOTOR_LOAD_ERR: int = 0x22
RESP_FILAMENT_ERR: int = 0x50
RESP_SPEED_ERR: int = 0x51
RESP_ENWIND_ERR: int = 0x52

# Device type constants
DEV_TYPE_MB: int = 0x01   # Material box (料盒)

# Box mode constants (SET_BOX_MODE payload byte 0)
BOX_MODE_STANDBY: int = 0x00
BOX_MODE_LOAD: int = 0x01

# ---------------------------------------------------------------------------
# Slot / tool 1-hot bitmask (WIRE-CONFIRMED 2026-06-09/06-19)
# ---------------------------------------------------------------------------
# The Hi has ONE 4-slot CFS controller at bus addr 0x01. ALL box ops go to addr=0x01 with the
# SLOT selected by this data-byte bitmask, NOT by a per-channel bus address.
SLOT_T0: int = 0x01   # tool/slot A
SLOT_T1: int = 0x02   # tool/slot B
SLOT_T2: int = 0x04   # tool/slot C
SLOT_T3: int = 0x08   # tool/slot D
SLOT_BITMASKS: tuple = (SLOT_T0, SLOT_T1, SLOT_T2, SLOT_T3)

# Separate buffer/feeder node base address. ONLY the 0x0C GET_BUFFER_STATE read goes here.
# (v1.4.0 correction: the func-0x11 traffic seen on 0x81/0x82 is X/Y FOC-servo traffic sharing
# the RS-485 wire on the reference printer -- NOT a CFS retrude. The pre-v1.4.0 buffer-node
# retrude form was wire-disproven and removed.)
ADDR_BUFFER_NODE: int = 0x81

# ---------------------------------------------------------------------------
# 0x0A GET_BOX_STATE decode (wire-corrected 2026-06-20, CRC-verified across two boxes;
# the pre-v1.4.0 [hi=0x1a][lo 0x20/0x1f] model is WIRE-DISPROVEN)
# ---------------------------------------------------------------------------
# The request is sent with an EMPTY data payload (the old param byte is not on the wire).
# RX data = 4 bytes [b0][b1][b2][b3]:
#   b0/b1: an OPAQUE firmware base that drifts per box/firmware (0x1a20, 0x1b26, 0x1c24 and
#          0x1d21 all observed on identical hardware) -- it carries NO load information.
#          Gating on it caused a proven dry-purge bug on the reference stack. NEVER gate on it.
#   b2:    substatus (0x00 = OK).
#   b3:    the REAL load/print-mode flag: 0x02 = loaded/print-locked (1:1 with the SET_BOX_MODE
#          [slot][00] print-mode command), 0x00 = feed/change mode.
# The frame STATUS byte (frame[3]) is the box's async-event channel (surfaced as 'event'):
#   0x00 = idle/steady; 0x30 = UPDATE_STATE insert push (data = 4-byte per-slot phase array,
#   phase 0x03 in ANY slot byte = insert complete); 0x16 with b3==0x04 = busy/active-cal
#   (normal transiently -- e.g. during a retract -- a wedge only if it never settles).
BOX_STATE_LOADED_B3: int = 0x02    # data[3]: loaded / print-locked
BOX_STATE_FEEDING_B3: int = 0x00   # data[3]: feed/change mode
BOX_EVENT_IDLE: int = 0x00         # frame STATUS byte: steady state
BOX_EVENT_INSERT: int = 0x30       # frame STATUS byte: async insert/update push
BOX_EVENT_BUSY: int = 0x16         # frame STATUS byte: busy/active (cal or retract in progress)
BOX_INSERT_PHASE_COMPLETE: int = 0x03
BOX_BUSY_SUBCODE: int = 0x04       # data[3] value during a 0x16 busy/active state

# ---------------------------------------------------------------------------
# 0x08 GET_HARDWARE_STATUS flags (WIRE-CONFIRMED 2026-06-09)
# TX data = [channel]; RX = 1 status flag byte.
# ---------------------------------------------------------------------------
HW_STATUS_CLEAR: int = 0x00    # sensor clear / no filament
HW_STATUS_BUSY: int = 0x01     # the box's idle/global value (NOT a hard busy flag)
HW_STATUS_READY: int = 0x07    # ready flags
# 0x08 channel selectors used by the unload prep reads (stock order: material first).
HW_SENSOR_MATERIAL: int = 0x00
HW_SENSOR_CONNECTIONS: int = 0x01

# 0x05 CUT_STATE RX byte (decoded 2026-06-22 from stock-vs-empty capture comparison):
CUT_STATE_DONE: int = 0x00     # cut OK -- every real cut returns this
CUT_STATE_SET: int = 0x01      # transient cut-state-set seen during a real cut
CUT_STATE_NOTHING: int = 0x02  # NOTHING CUT -- slot empty / no filament at the blade (not a failure)

# 0x0F CTRL_CONNECTION_MOTOR_ACTION TX byte (WIRE-CONFIRMED 2026-06-09).
MOTOR_ACTION_RELEASE: int = 0x00
MOTOR_ACTION_ENGAGE: int = 0x01

# ---------------------------------------------------------------------------
# 0x11 RETRUDE_PROCESS (unload) -- rebuilt 2026-06-25 from the stock retract decode and
# hardware-validated on the reference stack
# ---------------------------------------------------------------------------
# The unload is a START/FINISH COMMAND PAIR, BOTH frames carrying the slot bitmask in data[0]:
# [slot][0x00] (START) then [slot][0x01] (FINISH). Both ACK with the same bare status-0x00
# frame; the FINISH ACK is HELD ~9.6 s while the box reels the filament fully in, so the
# finish timeout must cover it -- the pre-v1.4.0 0.5 s timeout ALWAYS timed the finish out on
# real hardware, so an unload could never be confirmed. Completion is gated on the TOOLHEAD
# filament switch clearing, NOT on the 0x11 reply status (the 0x14 in-progress / 0x16 NAK
# status-poll model is wire-disproven; those bytes never appear on the wire). ONE toolhead
# G1 E-15 F360 pull is interleaved between the two frames (the reference .so derives -15/360
# internally regardless of config).
RETRUDE_PHASE_START: int = 0x00
RETRUDE_PHASE_FINISH: int = 0x01
RETRUDE_PHASE_RUNNING: int = RETRUDE_PHASE_FINISH   # back-compat alias (pre-v1.4.0 name)
RETRUDE_START_TIMEOUT_S: float = 22.0   # start frame reply (a real pull replies in ~12-14 s)
RETRUDE_FINISH_TIMEOUT_S: float = 13.0  # finish ACK held ~9.6 s; 13 s gives headroom
RETRUDE_PREP_TIMEOUT_S: float = 2.0     # the 0x08 sensor prep reads (wire to=2)
RETRUDE_SENSOR_WAIT_S: float = 13.0     # post-finish toolhead-switch clear wait
RETRUDE_SENSOR_POLL_DT_S: float = 0.25
RETRUDE_WALL_BUDGET_S: float = 60.0     # hard wall-clock budget for the whole unload
RETRUDE_TOOLHEAD_PULL_MM: float = 15.0
RETRUDE_TOOLHEAD_PULL_VEL: float = 360.0

# ---------------------------------------------------------------------------
# 0x0D SET_PRE_LOADING payload = [mask][phase] (generalized per the 2026-06-20 decode)
# ---------------------------------------------------------------------------
#   arm at start-print:      [0x0f][0x00]        disarm at end-print: [0x0f][0x01]
#   connect-time self-check: [0x00][0x01] (begin) then [0x0f][0x01] (phase 1) -- the stock
#     connect pre-load is these TWO frames ONLY; a fabricated [0x0f][0x02] phase is NAKed and
#     holds the box active so inserts never latch (stock never sends it at connect).
#   per-slot re-arm:         [slot][0x02] -- BLOCKS ~38 s while the controller settles the slot.
# *** v1.4.0 INVERSION FIX: the old gcode mapping ENABLE=1 -> [mask][0x01] sent the wire
# DISARM. Arm is phase 0x00; disarm is phase 0x01. ***
# The reply STATUS byte must be 0x00 (ACK). 0x16 = NAK / controller did not finish; a host that
# hangs up on a blocking phase (too-short timeout) NAKs the box into its 0x16/d3=04 wedge.
PRELOAD_PHASE_ARM: int = 0x00
PRELOAD_PHASE_DISARM: int = 0x01
PRELOAD_PHASE_SLOT_REARM: int = 0x02
PRELOAD_MASK_ALL: int = 0x0F
PRELOAD_BEGIN_TIMEOUT_S: float = 2.0     # [00][01] begin ACK lands ~0.98 s
PRELOAD_PHASE1_TIMEOUT_S: float = 5.0    # [0f][01] ACKs ~0.07 s; 5 s ceiling
PRELOAD_BLOCKING_TIMEOUT_S: float = 90.0 # any genuinely blocking phase (slot re-arm ~38 s)

# ---------------------------------------------------------------------------
# Temperature guards (v1.4.0 -- CRITICAL on mainline Klipper)
# ---------------------------------------------------------------------------
# Mainline Klipper KEEPS the min_extrude_temp raise that the Creality fork deletes, so any
# hotend G1 E move issued by this module hard-errors on mainline unless the hotend is heated
# first (blocking M109). Conversely, mainline's cold-extrude protection does NOT cover the
# BOX-MOTOR feed at all (it is not an extruder move), so the module enforces its own explicit
# floor before feeding filament toward a possibly-cold hotend.
MIN_EXTRUDE_TEMP: float = 170.0
DEFAULT_EXTRUDE_TEMP: float = 220.0

# ---------------------------------------------------------------------------
# Change-flush constants (hotend purge loop; stock-capture decode 2026-06-30)
# ---------------------------------------------------------------------------
# total purge = nozzle_volume/2.4 + (5/12) * flush_volume * flush_multiplier, split into
# per-cycle purges capped at the per-cycle cap: cycle 1 = cap, remainder split equally.
# Wire-verified breakdowns: 158.75 -> [80, 78.75]; 343.33 -> [80, 65.83 x4]; 101.25 -> [80, 21.25].
FLUSH_TOTAL_BASE: float = 76.25       # nozzle_volume/2.4 at the 183 mm^3 default
FLUSH_VOL_COEFF: float = 5.0 / 12.0   # exactly 1/2.4 (binary-confirmed volume->length divisor)
NOZZLE_VOLUME_DEFAULT: float = 183.0
FLUSH_MULTIPLIER_DEFAULT: float = 1.0
FLUSH_CYCLE_CAP_DEFAULT: float = 80.0
FLUSH_TOTAL_DEFAULT: float = 140.0    # no-LEN=/no-volume fallback (stock falls back to its
                                      # configured feed length, 140 on the reference printer)
FLUSH_TOTAL_MAX: float = 600.0        # hard ceiling on the total purge (bounds a runaway LEN=)
FLUSH_CYCLES_MAX: int = 10            # hard ceiling on purge+wipe cycles
FLUSH_CAP_MAX: float = 160.0          # hard ceiling on the per-cycle cap
FLUSH_WHEEL_MIN_FRAC: float = 0.30    # per-cycle under-feed/clog watchdog: the wheel must
                                      # advance at least this fraction of the purged length
FLUSH_POST_RETRACT_LEN_MM: float = 1.5
FLUSH_POST_RETRACT_VEL: float = 600.0
FLUSH_VELOCITY_DEFAULT: float = 360.0

# ---------------------------------------------------------------------------
# Connect timing (decoded 2026-06-29 on the reference stack)
# ---------------------------------------------------------------------------
# After the 0xA0 address assign the box slave-MCU needs ~9.5 s to wake, and the FIRST 0x0A
# after a quiet period legitimately returns None. Short-timeout probes miss the box entirely
# (the pre-v1.4.0 0.05-0.1 s init shots were exactly that failure). The connect probe is a
# wake-sized single shot (NO retry inside one attempt -- a retried 12 s shot would hog the
# bus) re-armed a bounded number of times.
BOX_WAKE_PROBE_TIMEOUT_S: float = 12.0
BOX_PROBE_RETRY_MAX: int = 8
BOX_PROBE_RETRY_DELAY_S: float = 1.0

# ---------------------------------------------------------------------------
# Per-command timeouts
# ---------------------------------------------------------------------------
CMD_TIMEOUTS: dict = {
    CMD_GET_SLAVE_INFO: TIMEOUT_LONG,
    CMD_SET_SLAVE_ADDR: TIMEOUT_SHORT,
    CMD_GET_ADDR_TABLE: TIMEOUT_SHORT,
    CMD_ONLINE_CHECK:   TIMEOUT_MEDIUM,
    CMD_LOADER_TO_APP:  TIMEOUT_SHORT,
    CMD_SET_BOX_MODE:   TIMEOUT_MEDIUM,
    CMD_GET_BOX_STATE:  TIMEOUT_MEDIUM,
    CMD_GET_HARDWARE_STATUS: TIMEOUT_MEDIUM,
    CMD_CUT_STATE:      TIMEOUT_MEDIUM,
    CMD_MEASURING_WHEEL: TIMEOUT_MEDIUM,
    CMD_CTRL_CONNECTION_MOTOR_ACTION: TIMEOUT_MEDIUM,
    CMD_SET_PRE_LOADING: TIMEOUT_MEDIUM,
    CMD_GET_VERSION_SN: TIMEOUT_MEDIUM,
    CMD_GET_FILAMENT_SENSOR_STATE: TIMEOUT_MEDIUM,
    CMD_GET_REMAIN_LEN: TIMEOUT_MEDIUM,
    CMD_GET_BUFFER_STATE: TIMEOUT_MEDIUM,
    # The box HOLDS 0x10/0x11 replies until the mechanical step completes -- these are the
    # v1.4.0 blocking-reply timeouts (the old 0.5 s EXTRUDE_TIMEOUT could never see them).
    CMD_EXTRUDE_PROCESS: EXTRUDE_STAGE_TIMEOUT_S,
    CMD_RETRUDE_PROCESS: RETRUDE_START_TIMEOUT_S,
    CMD_VERSION_INFO: TIMEOUT_MEDIUM,
}

# ---------------------------------------------------------------------------
# CRC-8/SMBUS, 16/16 test vectors validated, poly=0x07, init=0x00
# Scope: msg[2:-1] (covers LENGTH, STATUS, FUNCTION_CODE, DATA; excludes HEAD, ADDR, CRC)
# ---------------------------------------------------------------------------

def crc8_cfs(data: bytes) -> int:
    """Calculate CRC-8/SMBUS checksum for the given data.

    Algorithm validated against 16 captured packet test vectors.
    Polynomial: 0x07, Initial value: 0x00, no reflection, no final XOR.
    CRC scope is msg[2:-1], i.e., from the LENGTH byte through the last DATA byte.

    Args:
        data: Bytes to checksum.

    Returns:
        int: Single-byte CRC value in range [0x00, 0xFF].

    Example:
        # Test vector from klipper-cfs/tests/test_structures.py:
        # msg = b'\\xf7\\x01\\x03\\x00\\xa3\\xdd'
        # CRC scope = msg[2:-1] = b'\\x03\\x00\\xa3'
        # Expected CRC = 0xDD
        # assert crc8_cfs(b'\\x03\\x00\\xa3') == 0xDD  # passes
    """
    crc: int = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x07
            else:
                crc <<= 1
            crc &= 0xFF
    return crc


# ---------------------------------------------------------------------------
# Message construction and parsing
# ---------------------------------------------------------------------------

def build_message(addr: int, status: int, func: int, data: bytes = b"") -> bytes:
    """Construct a complete CFS RS485 message frame.

    Message format:
        [HEAD:0xF7][ADDR][LENGTH][STATUS][FUNC][DATA 0-N bytes][CRC8]
    where LENGTH = len(STATUS) + len(FUNC) + len(DATA) + len(CRC) = len(data) + 3

    CRC scope is msg[2:-1] = [LENGTH][STATUS][FUNC][DATA...].

    Args:
        addr: Destination address (0x01-0x04 for individual boxes, 0xFE/0xFF broadcast).
        status: STATUS byte (STATUS_ADDRESSING=0x00 or STATUS_OPERATIONAL=0xFF).
        func: Function code (one of the CMD_* constants).
        data: Optional payload bytes. Default is empty.

    Returns:
        bytes: Complete message frame ready for transmission.

    Raises:
        ValueError: If data length exceeds MAX_DATA_LEN.
    """
    if len(data) > MAX_DATA_LEN:
        raise ValueError(
            f"Data payload length {len(data)} exceeds maximum {MAX_DATA_LEN}"
        )
    length: int = len(data) + 3  # STATUS(1) + FUNC(1) + DATA(N) + CRC(1)
    # Build the CRC scope: everything from LENGTH through end of DATA
    crc_scope: bytes = bytes([length, status, func]) + data
    crc: int = crc8_cfs(crc_scope)
    return bytes([PACK_HEAD, addr, length, status, func]) + data + bytes([crc])


def parse_message(raw: bytes) -> dict:
    """Parse and validate a raw CFS RS485 response frame.

    Validates:
      - Minimum length (MIN_MSG_LEN = 6 bytes)
      - Header byte (0xF7)
      - CRC-8 over msg[2:-1]
      - LENGTH field consistency

    Args:
        raw: Raw bytes received from the serial port.

    Returns:
        dict with keys:
            addr (int): Source device address.
            length (int): LENGTH field value from message.
            status (int): STATUS byte (0x00 for response, etc.).
            func (int): Function code echoed from command.
            data (bytes): Payload data bytes (may be empty).
            crc (int): CRC byte as received.
            crc_valid (bool): True if CRC check passed.

    Returns:
        None if the message cannot be parsed at all (too short, wrong header).
    """
    if len(raw) < MIN_MSG_LEN:
        logger.debug("parse_message: too short (%d bytes), need %d", len(raw), MIN_MSG_LEN)
        return None

    if raw[0] != PACK_HEAD:
        logger.debug("parse_message: bad header 0x%02X (expected 0x%02X)", raw[0], PACK_HEAD)
        return None

    addr: int = raw[1]
    length: int = raw[2]
    status: int = raw[3]
    func: int = raw[4]

    # Data bytes sit between func and CRC
    # Total message length = 1(HEAD) + 1(ADDR) + 1(LEN) + length_field bytes
    # length_field = STATUS + FUNC + DATA + CRC = len(data) + 3
    expected_total: int = 3 + length  # HEAD + ADDR + LEN + (STATUS+FUNC+DATA+CRC)
    if len(raw) < expected_total:
        logger.debug(
            "parse_message: truncated, got %d bytes, expected %d",
            len(raw), expected_total,
        )
        return None

    data: bytes = raw[5 : expected_total - 1]
    crc_received: int = raw[expected_total - 1]

    crc_scope: bytes = raw[2 : expected_total - 1]  # msg[2:-1] for this message
    crc_calculated: int = crc8_cfs(crc_scope)
    crc_valid: bool = crc_received == crc_calculated

    if not crc_valid:
        logger.warning(
            "parse_message: CRC mismatch, received 0x%02X, calculated 0x%02X for func=0x%02X",
            crc_received, crc_calculated, func,
        )

    return {
        "addr": addr,
        "length": length,
        "status": status,
        "func": func,
        "data": data,
        "crc": crc_received,
        "crc_valid": crc_valid,
    }


# ---------------------------------------------------------------------------
# Address manager: tracks per-box state through the auto-addressing sequence
# ---------------------------------------------------------------------------

class BoxAddressEntry:
    """Tracks the addressing state for a single CFS box slot.

    Attributes:
        addr: Assigned RS485 address (0x01-0x04).
        uniid: 12-byte unique ID of the device mapped to this slot.
        mapped: True if a device UniID has been assigned to this slot.
        online: ONLINE_STATE_* constant for this slot.
        acked: True if the most recent SET_SLAVE_ADDR was acknowledged.
        lost_cnt: Consecutive online-check failures since last successful ack.
        mode: MODE_APP or MODE_LOADER.
    """

    ONLINE_OFFLINE: int = 0
    ONLINE_ONLINE: int = 1
    ONLINE_INIT: int = 2
    ONLINE_WAIT_ACK: int = 3

    MODE_APP: int = 0
    MODE_LOADER: int = 1

    def __init__(self, addr: int) -> None:
        self.addr: int = addr
        self.uniid: list = [0x00]
        self.mapped: bool = False
        self.online: int = self.ONLINE_INIT
        self.acked: bool = False
        self.lost_cnt: int = 0
        self.mode: int = self.MODE_APP

    def reset(self) -> None:
        """Reset slot to unassigned state."""
        self.uniid = [0x00]
        self.mapped = False
        self.online = self.ONLINE_INIT
        self.acked = False
        self.lost_cnt = 0
        self.mode = self.MODE_APP

    def __repr__(self) -> str:
        uniid_hex = " ".join(f"0x{b:02X}" for b in self.uniid)
        return (
            f"<BoxEntry addr=0x{self.addr:02X} online={self.online} "
            f"acked={self.acked} mode={self.mode} uniid=[{uniid_hex}]>"
        )


# ---------------------------------------------------------------------------
# Main Klipper extra class
# ---------------------------------------------------------------------------

class CrealityCFS:
    """Klipper extra module for Creality Filament System (CFS) RS485 communication.

    Provides:
      - Full auto-addressing sequence (5-step, from auto_addr_wrapper.py pattern)
      - All 9 confirmed operational and addressing commands
      - G-code commands: CFS_INIT, CFS_STATUS, CFS_VERSION
      - Configurable serial port, baud rate, timeouts, and retry count
      - Detailed logging at appropriate levels
    """

    def __init__(self, config) -> None:
        """Initialize CrealityCFS module from Klipper config.

        Args:
            config: Klipper config object for this section.
        """
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.name: str = config.get_name()

        # --- Configuration parameters (all defensive with defaults) ---
        # serial_port is effectively REQUIRED off-Hi: CFS_DEFAULT_PORT (/dev/ttyS5) is the
        # Hi mainboard RS-485 node (owned by serial_485 on the Hi). On a portable mainline
        # host set this to the dedicated CFS port (e.g. a USB-RS485 adapter /dev/ttyUSB0).
        self.serial_port: str = config.get("serial_port", CFS_DEFAULT_PORT)
        self.baud: int = config.getint("baud", CFS_BAUD_RATE, minval=9600, maxval=921600)
        self.timeout: float = config.getfloat("timeout", TIMEOUT_MEDIUM, minval=0.01, maxval=10.0)
        self.retry_count: int = config.getint("retry_count", DEFAULT_RETRY_COUNT, minval=0, maxval=10)
        self.box_count: int = config.getint("box_count", 4, minval=1, maxval=4)
        self.auto_init: bool = config.getboolean("auto_init", True)
        # rts_on_send: -1 (default) leaves the UART alone (auto-direction transceiver, portable);
        # 1 = kernel RS-485 mode with RTS high on send (DE); 0 = RTS low on send.
        rts: int = config.getint("rts_on_send", -1, minval=-1, maxval=1)
        self.rts_on_send = None if rts < 0 else bool(rts)

        # --- Choreography configuration (v1.4.0; all optional, printer-agnostic) ---
        # filament_sensor: the name of the TOOLHEAD [filament_switch_sensor <name>] section.
        # This switch is the load gate (the 0x06/0x07 finalize fires only after it trips) and
        # the unload completion gate (done when it clears). Without one, loads degrade to a
        # single ungated ramp cycle and unloads fall back to box-state corroboration.
        self.filament_sensor_name: str = config.get("filament_sensor", "filament_sensor")
        # extrude_temp: the default change/melt temperature. Every hotend E move and every
        # box-motor feed toward the hotend is gated on a blocking M109 to at least this
        # (>= MIN_EXTRUDE_TEMP) -- see the temperature-guard constants above.
        self.extrude_temp: float = config.getfloat(
            "extrude_temp", DEFAULT_EXTRUDE_TEMP, above=0.)
        self.load_max_bursts: int = config.getint(
            "load_max_bursts", LOAD_TOPUP_MAX_BURSTS, minval=1, maxval=20)
        self.load_wall_budget: float = config.getfloat(
            "load_wall_budget", LOAD_TOPUP_WALL_BUDGET_S, above=0.)
        # Cut geometry (all optional). CFS_CUT refuses to run without cut_switch_pin (the
        # cutter microswitch/hall the mechanical ram relies on) and a real, non-zero travel.
        self.cut_switch_pin = config.get("cut_switch_pin", None)
        self.pre_cut_pos_x = config.getfloat("pre_cut_pos_x", None)
        self.pre_cut_pos_y = config.getfloat("pre_cut_pos_y", None)
        self.cut_pos_x = config.getfloat("cut_pos_x", None)
        self.cut_pos_y = config.getfloat("cut_pos_y", None)
        self.cut_velocity: float = config.getfloat("cut_velocity", 3000.0, above=0.)
        self.cut_pos_x_max = config.getfloat("cut_pos_x_max", None)
        # Flush parameters (see the FLUSH_* constants for the wire-verified model).
        self.nozzle_volume: float = config.getfloat(
            "nozzle_volume", NOZZLE_VOLUME_DEFAULT, above=0.)
        self.flush_multiplier: float = config.getfloat(
            "flush_multiplier", FLUSH_MULTIPLIER_DEFAULT, above=0.)
        self.flush_cycle_cap: float = config.getfloat(
            "flush_cycle_cap", FLUSH_CYCLE_CAP_DEFAULT, above=0.)
        self.flush_default_len: float = config.getfloat(
            "flush_default_len", FLUSH_TOTAL_DEFAULT, above=0.)
        self.flush_velocity: float = config.getfloat(
            "flush_velocity", FLUSH_VELOCITY_DEFAULT, above=0.)
        # nozzle_clean_macro: an optional [gcode_macro] name run once per flush cycle (the
        # per-cycle nozzle wipe). Printer-specific wipe geometry belongs in that macro.
        self.nozzle_clean_macro = config.get("nozzle_clean_macro", None)
        # The requested baud is mapped to a termios B-constant lazily in the connect path
        # (_resolve_baud_const, called from _config_tty). It is NOT resolved here because
        # termios does not exist off-POSIX and __init__ must construct on any host (the
        # protocol/command logic is tested off-POSIX). An unsupported baud surfaces when the
        # port is actually opened, not at construction.
        self._baud_const = None

        # --- Internal state (reactor-driven, non-blocking transport; v1.3.0) ---
        self._fd: int = None                 # raw non-blocking tty file descriptor
        self._fd_handle = None               # ReactorFileHandler from reactor.register_fd
        self._rx_buf: bytearray = bytearray()  # incoming-byte accumulator (framed in the fd cb)
        self._pending = None                 # reactor.completion awaiting a response frame
        self._pending_match = None           # (addr, func) the in-flight waiter expects, or None
        # Half-duplex mutual exclusion: one transaction owns the bus at a time. reactor.mutex()
        # is greenlet-aware (FIFO), so a second caller parks and is woken in order rather than
        # racing past a bool and clobbering self._pending.
        self._bus_lock = self.reactor.mutex()
        self._shutdown: bool = False         # set on klippy:shutdown/disconnect (quiesce)
        self.is_connected: bool = False

        # Address table for up to 4 boxes (addr 0x01-0x04)
        self._box_table: list = [BoxAddressEntry(i + 1) for i in range(self.box_count)]

        # --- Choreography state (v1.4.0) ---
        self._active_tool = None        # 0-based tool index of the currently loaded slot, or None
        self._connected: set = set()    # addrs whose connect-init burst completed
        self._probe_attempts: int = 0   # bounded wake-probe retry counter
        self._preload_done: dict = {}   # addr -> True once the connect pre-load completed
        self._preload_inflight: dict = {}  # addr -> True while a pre-load sequence is running
        self._slots: dict = {}          # tool idx -> {"present","material","remain"} cache

        # --- Register Klipper lifecycle handlers ---
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._handle_shutdown)

        # --- Register G-code commands ---
        self.gcode.register_command(
            "CFS_INIT",
            self.cmd_CFS_INIT,
            desc=self.cmd_CFS_INIT_help,
        )
        self.gcode.register_command(
            "CFS_STATUS",
            self.cmd_CFS_STATUS,
            desc=self.cmd_CFS_STATUS_help,
        )
        self.gcode.register_command(
            "CFS_VERSION",
            self.cmd_CFS_VERSION,
            desc=self.cmd_CFS_VERSION_help,
        )
        self.gcode.register_command(
            "CFS_SET_MODE",
            self.cmd_CFS_SET_MODE,
            desc=self.cmd_CFS_SET_MODE_help,
        )
        self.gcode.register_command(
            "CFS_SET_PRELOAD",
            self.cmd_CFS_SET_PRELOAD,
            desc=self.cmd_CFS_SET_PRELOAD_help,
        )
        self.gcode.register_command(
            "CFS_ADDR_TABLE",
            self.cmd_CFS_ADDR_TABLE,
            desc=self.cmd_CFS_ADDR_TABLE_help,
        )
        self.gcode.register_command(
            "CFS_EXTRUDE",
            self.cmd_CFS_EXTRUDE,
            desc=self.cmd_CFS_EXTRUDE_help,
        )
        self.gcode.register_command(
            "CFS_RETRUDE",
            self.cmd_CFS_RETRUDE,
            desc=self.cmd_CFS_RETRUDE_help,
        )
        self.gcode.register_command(
            "CFS_FW_VERSION",
            self.cmd_CFS_FW_VERSION,
            desc=self.cmd_CFS_FW_VERSION_help,
        )
        self.gcode.register_command(
            "CFS_CUT",
            self.cmd_CFS_CUT,
            desc=self.cmd_CFS_CUT_help,
        )
        self.gcode.register_command(
            "CFS_FLUSH",
            self.cmd_CFS_FLUSH,
            desc=self.cmd_CFS_FLUSH_help,
        )

        logger.info("creality_cfs: module loaded, port=%s baud=%d", self.serial_port, self.baud)

    # -----------------------------------------------------------------------
    # Klipper lifecycle handlers
    # -----------------------------------------------------------------------

    def _handle_ready(self) -> None:
        """Called when Klipper transitions to ready state.

        Opens the serial port and optionally runs auto-addressing.
        Logs failure but does not raise. A missing CFS should not prevent
        the printer from otherwise operating.
        """
        try:
            self._connect_serial()
        except Exception as exc:
            logger.error("creality_cfs: failed to open serial port %s: %s", self.serial_port, exc)
            return

        if self.auto_init:
            self.reactor.register_callback(self._auto_init_callback)

    def _auto_init_callback(self, eventtime: float) -> None:
        """Reactor callback to run auto-addressing on klippy:ready.

        This runs in a reactor callback so it does not block the main thread
        during the klippy:ready event dispatch phase. After addressing, the
        wake-sized connect probe is armed (v1.4.0): a freshly-assigned box needs
        ~9.5 s of slave-MCU wake after the 0xA0 assign and its first 0x0A after a
        quiet period legitimately returns None, so short-timeout init reads at
        ready (the pre-v1.4.0 behavior) missed the box entirely.
        """
        try:
            self._run_auto_addressing()
        except Exception as exc:
            logger.error("creality_cfs: auto-init failed: %s", exc)
            return
        self._probe_attempts = 0
        self.reactor.register_callback(self._connect_probe)

    def _connect_probe(self, eventtime: float) -> None:
        """Bounded post-addressing connect probe (self-re-arming reactor callback).

        For each addressed box not yet connect-inited: one wake-sized 12 s single-shot
        GET_BOX_STATE (no retry inside the attempt -- a retried 12 s shot would hog the
        bus), then the stock connect-init burst on an answer. Boxes still silent re-arm
        the probe up to BOX_PROBE_RETRY_MAX times, so one contended None does not skip
        the init forever. Runs in a reactor callback (post-ready), where the parked
        completion.wait is legal and yields the greenlet.
        """
        self._probe_attempts += 1
        try:
            for entry in self._box_table:
                if entry.addr in self._connected:
                    continue
                if entry.online != BoxAddressEntry.ONLINE_ONLINE:
                    continue
                st = self.get_box_state(entry.addr, timeout=BOX_WAKE_PROBE_TIMEOUT_S,
                                        retries=1)
                if st is not None:
                    self._connect_init(entry.addr)
                    self._connected.add(entry.addr)
        except Exception:
            logger.exception("creality_cfs: connect probe attempt %d failed (non-fatal)",
                             self._probe_attempts)
        remaining = [e.addr for e in self._box_table
                     if e.online == BoxAddressEntry.ONLINE_ONLINE
                     and e.addr not in self._connected]
        if remaining and self._probe_attempts < BOX_PROBE_RETRY_MAX:
            self.reactor.register_callback(
                self._connect_probe,
                self.reactor.monotonic() + BOX_PROBE_RETRY_DELAY_S)
            return
        if remaining:
            logger.info("creality_cfs: gave up connect probe after %d attempts; "
                        "still silent: %s", self._probe_attempts,
                        ["0x%02x" % a for a in remaining])

    def _connect_init(self, addr: int) -> None:
        """The stock connect-init burst for one box (wire order from the reference decode):
        0x04 [00][01] enter feed mode -> 0x14 version/SN -> the two-frame pre-load self-check
        (0x0D [00][01] begin, 0x08, 0x0D [0f][01] phase 1, 0x08 -- stock sends NO [0f][02]) ->
        the all-slot presence read (0x02 [0x0f] + 0x03 [0x0f], long timeouts: the box scans
        all four bays for ~11 s). Tolerant of a silent box at every step (never raises)."""
        try:
            self.set_box_mode(addr, 0x00, 0x01)                 # 0x04 0001 enter feed mode
            try:
                sn = self.get_version_sn(addr)
            except Exception:
                sn = None
            self._run_preload_sequence(addr)
            mat = self.read_material(addr, PRELOAD_MASK_ALL, timeout=15.0)
            rem = self.read_remain(addr, PRELOAD_MASK_ALL, timeout=15.0)
            self._ingest_slot_reads(mat, rem)
            logger.info("creality_cfs: box 0x%02X connect-init done sn=%s slots=%s",
                        addr, sn, self._slots)
        except Exception:
            logger.exception("creality_cfs: connect-init for 0x%02X failed (non-fatal)", addr)

    def _run_preload_sequence(self, addr: int):
        """Run the stock startup pre-load self-check ONCE per box (single-owner guarded).

        Wire (stock-fidelity): TWO 0x0D frames only --
          0x0D [00][01]  begin/enable      (ACK ~0.98 s)
          0x08 [00]      hardware status
          0x0D [0f][01]  phase 1           (ACK ~0.07 s)
          0x08 [00]      hardware status
        Stock NEVER sends a [0f][02] phase at connect: the box NAKs it (status 0x16) and is
        held in its active state so inserts never latch. After [0f][01] the box settles to
        idle on its own. Each 0x0D ACK's STATUS byte is checked by set_pre_loading(). Returns
        the first 0x08 flag byte (or None) for logging."""
        if self._preload_done.get(addr) or self._preload_inflight.get(addr):
            return None
        self._preload_inflight[addr] = True
        try:
            # NOTE: the connect-time phase byte is 0x01 in BOTH frames ([00 01] begin,
            # [0f 01] phase 1) -- numerically the same byte as the end-print disarm, but a
            # different wire pair (the mask selects the meaning). Passed literally here.
            if not self.set_pre_loading(addr, 0x00, 0x01,
                                        timeout=PRELOAD_BEGIN_TIMEOUT_S, retries=1):
                logger.warning("creality_cfs: pre-load begin [00 01] not ACKed on 0x%02X", addr)
            hw = self.get_hardware_status(addr, 0x00)
            if not self.set_pre_loading(addr, PRELOAD_MASK_ALL, 0x01,
                                        timeout=PRELOAD_PHASE1_TIMEOUT_S, retries=1):
                logger.warning("creality_cfs: pre-load phase 1 [0f 01] not ACKed on 0x%02X", addr)
            self.get_hardware_status(addr, 0x00)
            self._preload_done[addr] = True
            return hw
        finally:
            self._preload_inflight[addr] = False

    def _handle_shutdown(self) -> None:
        """Called on klippy:shutdown or klippy:disconnect.

        Quiesces the bus (aborts any in-flight waiter so a parked greenlet wakes instead of
        hanging on a tearing-down reactor) and closes the serial port safely. Klipper invokes
        shutdown handlers while already shutting down, so this must NEVER raise.
        """
        try:
            self._quiesce()
        except Exception as exc:
            logger.warning("creality_cfs: error during shutdown quiesce: %s", exc)
        try:
            self._disconnect_serial()
        except Exception as exc:
            logger.warning("creality_cfs: error during shutdown close: %s", exc)

    def _quiesce(self) -> None:
        """Stop the bus cleanly: refuse new traffic and wake any parked waiter.

        Sets the shutdown flag so _send_command will not start a new transaction (or park in
        completion.wait) against a tearing-down reactor, and completes any in-flight pending
        completion with None so a greenlet blocked in completion.wait() returns rather than
        hanging. Must not raise.
        """
        self._shutdown = True
        comp, self._pending = self._pending, None
        self._pending_match = None
        if comp is not None and not comp.test():
            try:
                comp.complete(None)
            except Exception:
                logger.exception("creality_cfs: error aborting pending on quiesce")

    # -----------------------------------------------------------------------
    # Serial connection management
    # -----------------------------------------------------------------------

    def _connect_serial(self) -> None:
        """Open the dedicated RS-485 port non-blocking and register it with the reactor.

        Opens the tty with O_NONBLOCK, configures it raw 8N1 at the requested baud via termios
        (so reads never block), optionally enables kernel RS-485 RTS-as-DE, and registers the fd
        with the reactor. The read callback (_handle_readable) drains and frames bytes; no read
        ever blocks the reactor greenlet.

        Raises:
            OSError: If the port cannot be opened or configured.
            RuntimeError: If opened off-POSIX (no fcntl/termios available).
        """
        if not _HAS_POSIX_SERIAL:
            raise RuntimeError(
                "creality_cfs: the live RS-485 transport requires a POSIX host "
                "(fcntl/termios); cannot open %s on this platform" % self.serial_port
            )
        # O_NOCTTY/O_NONBLOCK are POSIX-only os attributes; resolve them defensively so this
        # line does not raise AttributeError off-POSIX (they are always present on the live
        # POSIX host, where this path actually runs).
        open_flags = (os.O_RDWR
                      | getattr(os, "O_NOCTTY", 0)
                      | getattr(os, "O_NONBLOCK", 0))
        fd = os.open(self.serial_port, open_flags)
        try:
            self._config_tty(fd)
            self._config_rs485(fd)
        except Exception:
            os.close(fd)
            raise
        self._fd = fd
        self._rx_buf = bytearray()
        self._fd_handle = self.reactor.register_fd(fd, self._handle_readable)
        self.is_connected = True
        logger.info("creality_cfs: opened %s at %d baud (non-blocking, reactor fd)",
                    self.serial_port, self.baud)

    def _resolve_baud_const(self):
        """Map self.baud to a termios B-constant. POSIX-only; called from the connect path.

        Resolved lazily (not in __init__) so the module constructs off-POSIX. Raises a clear
        RuntimeError if the baud has no termios B-constant on this host.
        """
        baud_const = getattr(termios, "B%d" % self.baud, None)
        if baud_const is None:
            raise RuntimeError(
                "creality_cfs: unsupported baud %d (no termios B%d)" % (self.baud, self.baud)
            )
        self._baud_const = baud_const
        return baud_const

    def _config_tty(self, fd: int) -> None:
        """Put the tty in raw 8N1 mode at the configured baud (VMIN=0/VTIME=0, non-blocking)."""
        self._resolve_baud_const()
        a = termios.tcgetattr(fd)   # [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
        a[0] = termios.IGNPAR                                   # iflag: raw, ignore parity
        a[1] = 0                                                # oflag: raw
        a[2] = (a[2] & ~termios.CSIZE) | termios.CS8 | termios.CREAD | termios.CLOCAL
        a[2] &= ~(termios.PARENB | termios.CSTOPB | getattr(termios, "CRTSCTS", 0))
        a[3] = 0                                                # lflag: raw (no echo/canon/sig)
        a[4] = self._baud_const                                 # ispeed
        a[5] = self._baud_const                                 # ospeed
        a[6][termios.VMIN] = 0
        a[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, a)
        termios.tcflush(fd, termios.TCIOFLUSH)

    def _config_rs485(self, fd: int) -> None:
        """Optionally enable kernel RS-485 mode (RTS = DE). Skipped when rts_on_send is None."""
        if self.rts_on_send is None:
            return
        flags = SER_RS485_ENABLED
        flags |= SER_RS485_RTS_ON_SEND if self.rts_on_send else SER_RS485_RTS_AFTER_SEND
        # struct serial_rs485 { u32 flags; u32 delay_before; u32 delay_after; u32 pad[5]; }
        rs485 = struct.pack("8I", flags, 0, 0, 0, 0, 0, 0, 0)
        try:
            fcntl.ioctl(fd, TIOCSRS485, rs485)
        except (OSError, IOError) as exc:
            logger.info("creality_cfs: TIOCSRS485 unsupported (%s); assuming auto-direction xcvr",
                        exc)

    def _disconnect_serial(self) -> None:
        """Unregister the fd from the reactor and close the port if open."""
        if self._fd_handle is not None:
            try:
                self.reactor.unregister_fd(self._fd_handle)
            except Exception:
                logger.exception("creality_cfs: error unregistering fd")
            self._fd_handle = None
        if self._fd is not None:
            try:
                os.close(self._fd)
                logger.info("creality_cfs: serial port closed")
            except OSError:
                pass
            self._fd = None
        self.is_connected = False

    # -----------------------------------------------------------------------
    # Low-level send/receive
    # -----------------------------------------------------------------------

    def _send_command(
        self,
        addr: int,
        status: int,
        func: int,
        data: bytes = b"",
        timeout: float = None,
        retries: int = None,
    ) -> dict:
        """Build, send, and await a CFS command with retry logic (non-blocking transport).

        v1.3.0: this NO LONGER blocks the reactor greenlet. It writes the request bytes,
        registers a reactor.completion as the pending response matcher, arms a reactor timer
        for the timeout, and parks the caller in completion.wait(). The reactor keeps servicing
        the MCU keepalive and every other event while this caller waits; the registered fd
        callback (_handle_readable) frames the reply and completes the completion. This looks
        synchronous to callers and returns exactly what the old blocking path returned: the
        parse_message() dict, or None on timeout/no-response. Public signature unchanged.

        Args:
            addr: Destination address byte.
            status: STATUS byte (STATUS_ADDRESSING or STATUS_OPERATIONAL).
            func: Function code (CMD_* constant).
            data: Payload bytes (default empty).
            timeout: Override response timeout in seconds. Defaults to the per-command value
                     from CMD_TIMEOUTS, then self.timeout.
            retries: Override retry count. Defaults to self.retry_count.

        Returns:
            dict: Parsed response from parse_message(), or None if no response was received
                  after all retries (for addressing commands that may legitimately have no
                  responders), if the bus is quiescing, or on a write error.
        """
        if not self.is_connected or self._fd is None:
            raise RuntimeError("creality_cfs: serial port not connected")
        if self._shutdown:
            # Do not start new traffic (or park in completion.wait) against a tearing-down bus.
            return None

        if timeout is None:
            timeout = CMD_TIMEOUTS.get(func, self.timeout)
        if retries is None:
            retries = self.retry_count

        msg: bytes = build_message(addr, status, func, data)
        # The slave echoes ADDR in frame[1] and FUNC in frame[4]; only a frame matching this
        # (addr, func) may satisfy this waiter (half-duplex multi-drop correctness).
        match = (addr, func)
        logger.debug(
            "creality_cfs: TX addr=0x%02X func=0x%02X data=%s",
            addr, func, data.hex() if data else "(none)",
        )

        # Serialize the half-duplex bus: one transaction at a time. The lock is greenlet-aware,
        # so a second caller yields here instead of blocking the reactor.
        with self._bus_lock:
            if self._shutdown:
                return None
            for attempt in range(max(retries, 1)):
                # One raw request/response exchange through the transport seam. _txn is the
                # only piece that touches the fd/reactor; tests replace it to drive the
                # protocol logic without a live port.
                raw = self._txn(msg, timeout, match)

                if self._shutdown:
                    return None
                if raw is self._TXN_WRITE_ERROR:
                    # Hardware-level write failure: do not keep retrying a dead bus.
                    break
                if raw is None or len(raw) == 0:
                    logger.debug(
                        "creality_cfs: no response on attempt %d/%d for func=0x%02X",
                        attempt + 1, retries, func,
                    )
                    continue

                logger.debug("creality_cfs: RX raw=%s", raw.hex())
                parsed = parse_message(raw)
                if parsed is None:
                    logger.debug("creality_cfs: unparseable response on attempt %d", attempt + 1)
                    continue
                if not parsed["crc_valid"]:
                    logger.warning(
                        "creality_cfs: CRC error on attempt %d/%d for func=0x%02X",
                        attempt + 1, retries, func,
                    )
                    continue

                return parsed

        # Addressing broadcast commands legitimately get no response if no devices are
        # present; return None instead of raising (unchanged contract).
        return None

    # Sentinel returned by _txn when the write itself failed (vs. a plain no-response None),
    # so _send_command can break the retry loop on a dead bus instead of retrying.
    _TXN_WRITE_ERROR = object()

    def _txn(self, request_bytes: bytes, timeout: float, match=None):
        """Perform ONE raw request/response exchange over the bus (the transport seam).

        This is the only method that touches the fd and the reactor. _send_command wraps it
        with build_message, the retry loop, parse_message, and CRC validation, so the protocol
        logic is fully exercisable by replacing _txn alone (the test harness does exactly this).

        POSIX reactor-fd implementation (UNCHANGED from the v1.3.0 non-blocking transport):
        register a fresh reactor.completion as the pending (addr, func) matcher BEFORE writing
        so a fast reply cannot race ahead, os.write the request, then park the caller in
        completion.wait() bounded by a reactor timer. The registered fd callback frames the
        reply and completes the completion; this yields the greenlet instead of blocking it.

        Args:
            request_bytes: The complete framed request to write.
            timeout: Response timeout in seconds (reactor.monotonic deadline).
            match: Optional (addr, func) tuple the reply must echo; None matches anything.

        Returns:
            bytes: The raw response frame (HEAD..CRC).
            None: On timeout / no response.
            self._TXN_WRITE_ERROR: If the write itself failed (caller breaks the retry loop).
        """
        # Fresh completion per exchange; register it as the pending matcher BEFORE the write
        # so a fast reply cannot race ahead of us.
        comp = self.reactor.completion()
        self._pending = comp
        self._pending_match = match
        try:
            os.write(self._fd, request_bytes)
        except OSError as exc:
            logger.error("creality_cfs: write error: %s", exc)
            self._pending = None
            self._pending_match = None
            return self._TXN_WRITE_ERROR

        # Park the caller (yields the greenlet) until the fd callback completes us with a
        # frame, or the reactor timer wakes us with None at the deadline.
        raw = comp.wait(self.reactor.monotonic() + timeout, None)
        self._pending = None
        self._pending_match = None
        return raw

    # -----------------------------------------------------------------------
    # Reactor fd read path: drain, frame, and dispatch incoming bytes
    # -----------------------------------------------------------------------

    def _handle_readable(self, eventtime: float) -> None:
        """Reactor fd callback: drain available bytes (non-blocking) and frame them.

        Never blocks: a single non-blocking os.read drains what the kernel has buffered, the
        bytes are accumulated, and complete frames are extracted and dispatched. Partial reads
        are carried across callbacks in self._rx_buf.
        """
        if self._fd is None:
            return
        try:
            data = os.read(self._fd, CFS_READ_CHUNK)
        except (OSError, BlockingIOError):
            return
        if not data:
            return
        self._rx_buf += data
        self._parse_rx(eventtime)

    def _parse_rx(self, eventtime: float) -> None:
        """Extract complete framed responses from the rx buffer and dispatch each.

        Reuses the EXISTING frame geometry: [HEAD][ADDR][LEN][STATUS][FUNC][DATA..][CRC] where
        the on-wire LEN byte (buf[2]) counts STATUS+FUNC+DATA+CRC, so the full frame is
        3 + buf[2] bytes. CRC verification is deferred to parse_message() in _send_command,
        exactly as the blocking path did.
        """
        buf = self._rx_buf
        while True:
            i = buf.find(PACK_HEAD)
            if i < 0:
                del buf[:]                      # no header in buffer: drop noise
                return
            if i:
                del buf[:i]                     # drop noise before the header
            if len(buf) < 3:
                return                          # need HEAD + ADDR + LEN
            length_field = buf[2]
            if length_field < 3 or length_field > (MAX_DATA_LEN + 3):
                # Implausible LEN: this 0xF7 is not a real frame start; skip it and resync.
                logger.debug("creality_cfs: implausible LENGTH field %d, resyncing", length_field)
                del buf[:1]
                continue
            frame_len = 3 + length_field        # HEAD + ADDR + LEN + (STATUS..CRC)
            if len(buf) < frame_len:
                return                          # wait for the remainder of this frame
            frame = bytes(buf[:frame_len])
            del buf[:frame_len]
            self._dispatch_rx(frame, eventtime)

    def _dispatch_rx(self, frame: bytes, eventtime: float) -> None:
        """Deliver a complete raw frame to the in-flight waiter if (addr, func) matches.

        Hands the raw bytes (HEAD..CRC) to the pending completion; _send_command runs them
        through parse_message() for CRC/length validation, so the contract is identical to the
        old _read_response return value. A frame whose (addr, func) does not match the waiter is
        dropped (correct on a multi-drop bus where another device's reply must not unblock us).
        """
        addr = frame[1] if len(frame) >= 2 else None     # device address echoed by the slave
        func = frame[4] if len(frame) >= 5 else None      # command/function code echo
        if self._pending is not None and not self._pending.test():
            want_addr, want_func = self._pending_match or (None, None)
            addr_ok = want_addr is None or want_addr == addr
            func_ok = want_func is None or want_func == func
            if addr_ok and func_ok:
                comp, self._pending = self._pending, None
                self._pending_match = None
                comp.complete(frame)
                return
        logger.debug("creality_cfs: unmatched/late RX dropped frame=%s", frame.hex())

    # -----------------------------------------------------------------------
    # Auto-addressing sequence (5-step, from auto_addr_wrapper.py pattern)
    # -----------------------------------------------------------------------

    def _run_auto_addressing(self) -> int:
        """Execute the full 5-step CFS auto-addressing sequence.

        Step 1: Broadcast CMD_LOADER_TO_APP (0x0B) to wake all boxes.
        Step 2: Broadcast CMD_GET_SLAVE_INFO (0xA1) to discover all UniIDs.
                NOTE: Uses TIMEOUT_LONG (1.0 s). This step is intentionally slow.
        Step 3: For each discovered box, send CMD_SET_SLAVE_ADDR (0xA0).
        Step 4: Send CMD_ONLINE_CHECK (0xA2) per box to verify assignment.
        Step 5: Send CMD_GET_ADDR_TABLE (0xA3) to confirm full address table.

        Returns:
            int: Number of boxes that came online successfully.
        """
        logger.info("creality_cfs: starting auto-addressing sequence")

        # Step 1: Wake boxes from loader mode
        logger.debug("creality_cfs: step 1, CMD_LOADER_TO_APP broadcast")
        self._send_command(
            BROADCAST_ADDR_ALL,
            STATUS_ADDRESSING,
            CMD_LOADER_TO_APP,
            data=bytes([0x01]),
            timeout=TIMEOUT_SHORT,
            retries=1,
        )

        # Step 2: Discover all boxes via broadcast GET_SLAVE_INFO
        # TIMEOUT_LONG intentional: boxes may respond at different times
        logger.info(
            "creality_cfs: step 2, CMD_GET_SLAVE_INFO broadcast (%.1f s timeout)", TIMEOUT_LONG
        )
        # Send the broadcast with the MB broadcast address in the data field
        # (pattern from auto_addr_wrapper.py: send_data = [broadcast_addr, broadcast_addr])
        discovered: list = self._discover_slaves()
        logger.info("creality_cfs: discovered %d box(es)", len(discovered))

        # Step 3: Assign addresses
        logger.debug("creality_cfs: step 3, CMD_SET_SLAVE_ADDR for each discovered box")
        for attempt in range(MAX_SET_TIMES):
            for entry in self._box_table:
                if entry.mapped and entry.online in (
                    BoxAddressEntry.ONLINE_INIT, BoxAddressEntry.ONLINE_WAIT_ACK
                ):
                    self._set_slave_addr(BROADCAST_ADDR_MB, entry.addr, entry.uniid)

        # Step 4: Online check per box
        logger.debug("creality_cfs: step 4, CMD_ONLINE_CHECK per box")
        for entry in self._box_table:
            if entry.mapped:
                self._online_check(entry.addr)

        # Step 5: Confirm address table
        logger.debug("creality_cfs: step 5, CMD_GET_ADDR_TABLE per box")
        for attempt in range(MAX_GET_TIMES):
            for entry in self._box_table:
                if entry.online != BoxAddressEntry.ONLINE_ONLINE:
                    self._get_addr_table(entry.addr)

        online_count: int = sum(
            1 for e in self._box_table if e.online == BoxAddressEntry.ONLINE_ONLINE
        )
        logger.info(
            "creality_cfs: auto-addressing complete, %d/%d box(es) online",
            online_count, self.box_count,
        )
        return online_count

    def _discover_slaves(self) -> list:
        """Send CMD_GET_SLAVE_INFO broadcast and collect all responding UniIDs.

        The CFS boxes respond to the broadcast sequentially. Because this is
        half-duplex RS485, only one box responds at a time. The host must
        send one discovery message per expected box and collect responses.

        Returns:
            list: List of BoxAddressEntry objects that were newly discovered.
        """
        send_data: bytes = bytes([BROADCAST_ADDR_MB, BROADCAST_ADDR_MB])
        discovered: list = []

        # Send one broadcast per expected box slot to collect all responses
        for _ in range(self.box_count):
            resp = self._send_command(
                BROADCAST_ADDR_MB,
                STATUS_ADDRESSING,
                CMD_GET_SLAVE_INFO,
                data=send_data,
                timeout=TIMEOUT_LONG,
                retries=1,
            )
            if resp is None:
                logger.debug("creality_cfs: no response to GET_SLAVE_INFO broadcast")
                break

            data_bytes = resp.get("data", b"")
            if len(data_bytes) < 2:
                logger.debug("creality_cfs: GET_SLAVE_INFO response too short")
                continue

            dev_type: int = data_bytes[0]
            mode: int = data_bytes[1]
            uniid: list = list(data_bytes[2:])

            if dev_type != DEV_TYPE_MB:
                logger.debug(
                    "creality_cfs: ignoring non-MB device type 0x%02X in discovery", dev_type
                )
                continue

            addr: int = self._allocate_address(uniid)
            if addr < 0:
                logger.warning("creality_cfs: no free address slots for discovered box")
                continue

            logger.info(
                "creality_cfs: discovered box, addr=0x%02X mode=%d uniid=%s",
                addr, mode, " ".join(f"0x{b:02X}" for b in uniid),
            )
            discovered.append(self._box_table[addr - 1])

        return discovered

    def _allocate_address(self, uniid: list) -> int:
        """Find or assign an address slot for a discovered UniID.

        Priority order (from auto_addr_wrapper.py):
          1. Previously mapped slot with matching UniID (offline/init state).
          2. First unmapped slot.
          3. Mapped slot with non-matching UniID (offline/init state), overwrite.

        Args:
            uniid: Discovered device UniID as list of ints.

        Returns:
            int: Assigned address (0x01-0x04), or -1 if no slot available.
        """
        # Priority 1: previously mapped, matching UniID, not currently online
        for entry in self._box_table:
            if (entry.mapped
                    and entry.online in (BoxAddressEntry.ONLINE_OFFLINE, BoxAddressEntry.ONLINE_INIT)
                    and entry.uniid == uniid):
                entry.online = BoxAddressEntry.ONLINE_WAIT_ACK
                return entry.addr

        # Priority 2: unmapped slot
        for entry in self._box_table:
            if not entry.mapped:
                entry.mapped = True
                entry.online = BoxAddressEntry.ONLINE_WAIT_ACK
                entry.uniid = uniid
                return entry.addr

        # Priority 3: mapped, mismatched UniID, offline/init
        for entry in self._box_table:
            if (entry.mapped
                    and entry.online in (BoxAddressEntry.ONLINE_OFFLINE, BoxAddressEntry.ONLINE_INIT)
                    and entry.uniid != uniid):
                entry.uniid = uniid
                entry.mapped = True
                entry.online = BoxAddressEntry.ONLINE_WAIT_ACK
                return entry.addr

        return -1

    # -----------------------------------------------------------------------
    # Addressing command implementations
    # -----------------------------------------------------------------------

    def _set_slave_addr(self, broadcast_addr: int, target_addr: int, uniid: list) -> bool:
        """Send CMD_SET_SLAVE_ADDR to assign an address to a specific UniID.

        Payload: [target_addr(1B)][uniid(N bytes)]
        Response: ACK with dev_type, mode, uniid echo.

        Args:
            broadcast_addr: Broadcast address to use (BROADCAST_ADDR_MB).
            target_addr: The address to assign (0x01-0x04).
            uniid: The 12-byte UniID of the target device.

        Returns:
            bool: True if the assignment was acknowledged.
        """
        send_data: bytes = bytes([target_addr]) + bytes(uniid)
        resp = self._send_command(
            broadcast_addr,
            STATUS_ADDRESSING,
            CMD_SET_SLAVE_ADDR,
            data=send_data,
            timeout=TIMEOUT_SHORT,
            retries=1,
        )
        if resp is None:
            logger.debug("creality_cfs: SET_SLAVE_ADDR, no response for addr=0x%02X", target_addr)
            return False

        data_bytes = resp.get("data", b"")
        if len(data_bytes) >= 2 and data_bytes[0] == DEV_TYPE_MB:
            # Mark as acked in the table
            for entry in self._box_table:
                if entry.addr == target_addr:
                    entry.acked = True
                    entry.online = BoxAddressEntry.ONLINE_ONLINE
                    entry.lost_cnt = 0
                    logger.info("creality_cfs: addr=0x%02X acknowledged SET_SLAVE_ADDR", target_addr)
                    break
        return True

    def _online_check(self, addr: int) -> bool:
        """Send CMD_ONLINE_CHECK to verify a box is responding at its address.

        Payload: [] (empty, addressed directly to the box)
        Response: ACK with dev_type, mode, uniid echo.

        Args:
            addr: Box address to check (0x01-0x04).

        Returns:
            bool: True if the box responded.
        """
        resp = self._send_command(
            addr,
            STATUS_ADDRESSING,
            CMD_ONLINE_CHECK,
            data=b"",
            timeout=TIMEOUT_MEDIUM,
            retries=1,
        )
        if resp is None:
            for entry in self._box_table:
                if entry.addr == addr:
                    entry.lost_cnt += 1
                    if entry.lost_cnt > MAX_LOST_CNT:
                        entry.online = BoxAddressEntry.ONLINE_OFFLINE
                        logger.warning("creality_cfs: addr=0x%02X went offline", addr)
                    break
            return False

        for entry in self._box_table:
            if entry.addr == addr:
                entry.acked = True
                entry.online = BoxAddressEntry.ONLINE_ONLINE
                entry.lost_cnt = 0
                break
        return True

    def _get_addr_table(self, addr: int) -> dict:
        """Send CMD_GET_ADDR_TABLE to confirm a box's address assignment.

        Payload: [] (empty)
        Response: dev_type, mode, uniid echo from the box.

        Args:
            addr: Box address to query (0x01-0x04).

        Returns:
            dict: Parsed response, or None if no response.
        """
        resp = self._send_command(
            addr,
            STATUS_ADDRESSING,
            CMD_GET_ADDR_TABLE,
            data=b"",
            timeout=TIMEOUT_SHORT,
            retries=1,
        )
        if resp is not None:
            data_bytes = resp.get("data", b"")
            for entry in self._box_table:
                if entry.addr == addr:
                    if len(data_bytes) >= 2:
                        entry.mode = data_bytes[1]
                        if len(data_bytes) > 2:
                            entry.uniid = list(data_bytes[2:])
                    entry.mapped = True
                    entry.acked = True
                    entry.online = BoxAddressEntry.ONLINE_ONLINE
                    entry.lost_cnt = 0
                    break
        return resp

    # -----------------------------------------------------------------------
    # Operational command implementations
    # -----------------------------------------------------------------------

    def get_box_state(self, addr: int, timeout: float = None, retries: int = None) -> dict:
        """Query the operating state of a single CFS box.

        Command: CMD_GET_BOX_STATE (0x0A), STATUS=0xFF, EMPTY data payload.
        (v1.4.0: the pre-v1.4.0 request carried a param byte; the wire form is empty.)

        Response decode (wire-corrected 2026-06-20, CRC-verified across two boxes; the old
        [hi=0x1a class byte][lo 0x20=LOADED/0x1f=FEEDING] model is WIRE-DISPROVEN):
          RSP: f7 [addr] 07 [STATUS] 0a [b0][b1][b2][b3] [crc]
          - b0/b1: OPAQUE firmware base (0x1a20/0x1b26/0x1c24/0x1d21 all observed on identical
            hardware). Carries NO load information -- gating on it caused the reference
            stack's dry-purge bug. Exposed as fw_base for diagnostics only.
          - b2: substatus (0x00 = OK).
          - b3: the REAL load flag: 0x02 = loaded/print-locked, 0x00 = feed/change mode.
          - the frame STATUS byte is the async EVENT channel: 0x00 idle, 0x30 insert push
            (data becomes a 4-byte per-slot phase array; phase 0x03 in ANY byte = insert
            complete), 0x16 + b3==0x04 = busy/active-cal (normal transiently).

        Caveat: loaded (b3==0x02) means the box accepted print mode (box-side loaded/locked);
        it is NOT a filament-reached-the-hotend confirmation -- the toolhead
        [filament_switch_sensor] is that backstop.

        Args:
            addr: Box address (normally 0x01; the single 4-slot controller).
            timeout: Optional per-call timeout override (the connect probe passes the
                     wake-sized 12 s single shot here).
            retries: Optional retry override (the connect probe passes 1).

        Returns:
            dict with keys fw_base, substatus, loaded, feeding, event, insert_event,
            event_phase, busy, addr, raw -- or None on no response (silent-CFS tolerant;
            v1.4.0 changed this from raising RuntimeError so a missing box can never abort
            a caller mid-choreography).
        """
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_GET_BOX_STATE,
            data=b"",
            timeout=timeout,
            retries=retries,
        )
        if resp is None:
            logger.debug("creality_cfs: GET_BOX_STATE addr=0x%02X, no response", addr)
            return None

        data_bytes = resp.get("data", b"")
        if len(data_bytes) < 4:
            logger.warning("creality_cfs: GET_BOX_STATE addr=0x%02X short payload %s",
                           addr, data_bytes.hex())
            return None
        d = data_bytes
        status = resp.get("status")
        ev_phase = d[0] if status == BOX_EVENT_INSERT else None
        result = {
            "fw_base": (d[0] << 8) | d[1],   # opaque firmware base -- diagnostics only
            "substatus": d[2],
            "loaded": (d[3] == BOX_STATE_LOADED_B3),
            "feeding": (d[3] == BOX_STATE_FEEDING_B3),
            "event": status,                 # frame STATUS byte = async event channel
            "insert_event": (status == BOX_EVENT_INSERT
                             and BOX_INSERT_PHASE_COMPLETE in (d[0], d[1], d[2], d[3])),
            "event_phase": ev_phase,
            "busy": (status == BOX_EVENT_BUSY and d[3] == BOX_BUSY_SUBCODE),
            "addr": addr,
            "raw": data_bytes,
        }
        logger.debug("creality_cfs: GET_BOX_STATE addr=0x%02X raw=%s loaded=%s event=0x%02X",
                     addr, data_bytes.hex(), result["loaded"],
                     status if status is not None else 0xFF)
        return result

    def get_version_sn(self, addr: int) -> str:
        """Query the firmware version and serial number string from a CFS box.

        Command: CMD_GET_VERSION_SN (0x14), STATUS=0xFF, payload empty.
        Response: 22-byte ASCII string.
        Defensively handles shorter responses by returning what is available.

        Args:
            addr: Box address (0x01-0x04).

        Returns:
            str: Decoded ASCII version/SN string (stripped of null bytes).

        Raises:
            RuntimeError: If no valid response received after retries.
        """
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_GET_VERSION_SN,
            data=b"",
        )
        if resp is None:
            raise RuntimeError(f"No response from box 0x{addr:02X} for GET_VERSION_SN")

        data_bytes = resp.get("data", b"")
        if len(data_bytes) < 22:
            logger.warning(
                "creality_cfs: GET_VERSION_SN addr=0x%02X returned %d bytes (expected 22)",
                addr, len(data_bytes),
            )
        version_str: str = data_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
        logger.info("creality_cfs: GET_VERSION_SN addr=0x%02X version='%s'", addr, version_str)
        return version_str

    def get_version_info(self, addr: int) -> str:
        """Query the firmware version string via CMD_VERSION_INFO (0xF0).

        Confirmed from live RS485 capture. The CFS box responds with an ASCII
        string identifying its firmware build, e.g. 'cfs0_050_G32-cfs0_000_113'.
        Motor controller boards respond with e.g. 'mot2_023_C30-mot2_002_071'.

        Protocol:
          REQ: f7 [addr] 04 ff f0 00 [crc]
          RSP: f7 [addr] 1c 00 f0 [28 bytes ASCII] [crc]

        Args:
            addr: Box address (0x01-0x04).

        Returns:
            str: Decoded ASCII firmware version string.
        """
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_VERSION_INFO,
            data=bytes([0x00]),
        )
        if resp is None:
            logger.warning("creality_cfs: VERSION_INFO addr=0x%02X -- no response", addr)
            return ""

        data_bytes = resp.get("data", b"")
        version_str = data_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
        logger.info("creality_cfs: VERSION_INFO addr=0x%02X version='%s'", addr, version_str)
        return version_str

    def set_box_mode(self, addr: int, mode: int, param: int = 0x01) -> bool:
        """Set the operating mode of a CFS box.

        Command: CMD_SET_BOX_MODE (0x04), STATUS=0xFF, payload=[byte0][byte1].
        ACK response: b'\\xF7\\x01\\x03\\x00\\x04\\xA1'

        Two wire forms of the 0x04 payload exist (WIRE-CONFIRMED 2026-06-19, see
        hi_rs485_3color_print_2026-06-19.json):
          * ENTER form  = [mode, param], observed [0x00, 0x01]. Brackets a tool change;
            the host sends it before/after the per-channel forms. This is the default
            (mode=BOX_MODE_STANDBY/LOAD, param=0x01).
          * PER-CHANNEL (print-mode) form = [slot_bitmask, 0x00], observed 01 00 / 02 00 /
            04 00, keyed to the active slot during the tool change. Issue this via
            set_box_mode_channel() (or set_box_mode(addr, slot_bitmask, 0x00)) keying the
            mode byte to SLOT_BITMASKS[tool].

        Args:
            addr: Box address (0x01-0x04).
            mode: Mode/byte0 (ENTER: BOX_MODE_STANDBY=0x00 / BOX_MODE_LOAD=0x01;
                  PER-CHANNEL: the 1-hot slot bitmask SLOT_T0..SLOT_T3).
            param: byte1 (ENTER: 0x01; PER-CHANNEL: 0x00). Default 0x01.

        Returns:
            bool: True if command was acknowledged successfully.

        Raises:
            ValueError: If addr or mode are out of valid range.
        """
        if not (ADDR_BOX_MIN <= addr <= ADDR_BOX_MAX):
            raise ValueError(f"addr 0x{addr:02X} out of range [0x01, 0x04]")
        if not (0x00 <= mode <= 0xFF):
            raise ValueError(f"mode 0x{mode:02X} out of byte range")
        if not (0x00 <= param <= 0xFF):
            raise ValueError(f"param 0x{param:02X} out of byte range")

        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_SET_BOX_MODE,
            data=bytes([mode, param]),
        )
        if resp is None:
            logger.warning("creality_cfs: SET_BOX_MODE addr=0x%02X, no response", addr)
            return False

        resp_status = resp.get("status", 0xFF)
        logger.info(
            "creality_cfs: SET_BOX_MODE addr=0x%02X mode=0x%02X status=0x%02X",
            addr, mode, resp_status,
        )
        # ACK uses STATUS=0x00. NOTE: this is deliberately STRICTER than the reference
        # implementation (which accepts any reply); the documented ACK is status 0x00 and
        # no choreography caller gates on this return value, so the stricter check only
        # affects diagnostics.
        return resp_status == STATUS_ADDRESSING

    def set_box_mode_channel(self, addr: int, slot: int) -> bool:
        """Set the PER-CHANNEL (print-mode) box mode keyed to a slot bitmask.

        Sends the 0x04 per-channel form [slot_bitmask, 0x00] (WIRE-CONFIRMED 2026-06-19:
        01 00 / 02 00 / 04 00), used during a tool change to point the box at the active
        slot. The ENTER form ([00 01]) brackets these and is sent via set_box_mode().

        Args:
            addr: Box address (0x01-0x04).
            slot: 1-hot slot bitmask (SLOT_T0..SLOT_T3 = 0x01/0x02/0x04/0x08).

        Returns:
            bool: True if acknowledged.

        Raises:
            ValueError: If slot is not a 1-hot bitmask.
        """
        if slot not in SLOT_BITMASKS:
            raise ValueError(f"slot 0x{slot:02X} is not a 1-hot bitmask in {SLOT_BITMASKS}")
        # Per-channel form: channel byte = slot bitmask, second byte 0x00.
        return self.set_box_mode(addr, slot, 0x00)

    def enter_feed_mode(self, addr: int, slot: int = 0x01) -> bool:
        """Enter feed/change mode for the SELECTED slot: 0x04 payload [0x00][slot].

        This is the load's op-start frame -- without it the box is never placed into feed
        mode and the 0x0F engage does not drive the rollers (a fresh load never feeds).
        The boot/connect form uses slot 0x01; the load/unload paths pass the resolved slot.
        """
        return self.set_box_mode(addr, 0x00, slot)

    def set_print_mode(self, addr: int, slot: int) -> bool:
        """Enter per-slot PRINT mode: 0x04 payload [slot][0x00] (wire 01 00 / 02 00 / 04 00).

        The box latches this as its loaded/print-locked state (GET_BOX_STATE data[3]==0x02).
        """
        return self.set_box_mode(addr, slot, 0x00)

    def set_pre_loading(self, addr: int, mask: int, phase: int,
                        timeout: float = None, retries: int = None) -> bool:
        """CMD_SET_PRE_LOADING (0x0D): payload = [mask][phase] (generalized wire form).

        Wire pairs (see the PRELOAD_* constants):
          arm at start-print   [0x0f][0x00]     disarm at end-print  [0x0f][0x01]
          connect begin        [0x00][0x01]     connect phase 1      [0x0f][0x01]
          per-slot re-arm      [slot][0x02]     (BLOCKS ~38 s -- pass a real timeout)

        *** v1.4.0 fixes: (1) the old (mask, enable) form inverted the semantics -- ENABLE=1
        emitted [mask][0x01], the wire DISARM; (2) the reply STATUS byte was never checked.
        The 0x0D ACK is f7..03 00 0d.. (STATUS 0x00); a 0x16 STATUS is a NAK meaning the
        controller did NOT finish -- e.g. the host hung up before a blocking phase completed,
        which latches the box into its 0x16/d3=04 wedge. Any non-ACK returns False. ***

        Args:
            addr: Box address (0x01-0x04).
            mask: Slot bitmask byte (0x01/0x02/0x04/0x08, 0x0F all, 0x00 connect-begin form).
            phase: PRELOAD_PHASE_ARM (0x00), PRELOAD_PHASE_DISARM (0x01) or
                   PRELOAD_PHASE_SLOT_REARM (0x02).
            timeout: Per-call timeout. Blocking phases MUST pass one sized to the block
                     (PRELOAD_BLOCKING_TIMEOUT_S) so the host never NAK-wedges the box.
            retries: Optional retry override (choreography callers pass 1).

        Returns:
            bool: True only on a STATUS-0x00 ACK.
        """
        if not (ADDR_BOX_MIN <= addr <= ADDR_BOX_MAX):
            raise ValueError(f"addr 0x{addr:02X} out of range")
        if not (0x00 <= mask <= 0xFF):
            raise ValueError("mask must be a single byte")
        if not (0x00 <= phase <= 0xFF):
            raise ValueError("phase must be a single byte")

        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_SET_PRE_LOADING,
            data=bytes([mask, phase]),
            timeout=timeout,
            retries=retries,
        )
        if resp is None:
            logger.warning("creality_cfs: SET_PRE_LOADING addr=0x%02X [%02X %02X], no response",
                           addr, mask, phase)
            return False
        status = resp.get("status")
        if status != 0x00:
            logger.warning(
                "creality_cfs: SET_PRE_LOADING addr=0x%02X [%02X %02X] NOT ACKed "
                "(status=%s) -- pre-load incomplete", addr, mask, phase,
                ("0x%02X" % status) if status is not None else "None")
            return False
        logger.info("creality_cfs: SET_PRE_LOADING addr=0x%02X mask=0x%02X phase=0x%02X ACKed",
                    addr, mask, phase)
        return True

    def read_material(self, addr: int, slot_mask: int = PRELOAD_MASK_ALL,
                      timeout: float = None) -> str:
        """0x02 READ_MATERIAL with a slot bitmask (0x0F = all A-D, 0x01 = A, ...).

        RX is the ASCII per-slot material map 'A:unknown;B:none;C:none;D:none;' where
        'none' = empty slot, 'unknown' = inserted but no RFID match, a label = identified.
        Returns the decoded ASCII, or None on no response. The all-slot form can take ~11 s
        (the box scans all four bays) -- pass a long timeout for it.
        """
        resp = self._send_command(addr, STATUS_OPERATIONAL, CMD_GET_FILAMENT_SENSOR_STATE,
                                  data=bytes([slot_mask]), timeout=timeout, retries=1)
        if resp is None:
            return None
        return resp.get("data", b"").rstrip(b"\x00").decode("ascii", "replace")

    def read_remain(self, addr: int, slot_mask: int = PRELOAD_MASK_ALL,
                    timeout: float = None) -> list:
        """0x03 READ_REMAIN with a slot bitmask. RX is POSITIONAL: 4 bytes, one per slot
        A..D, with 0xFF sentinels for slots not selected in the mask. 0x00 = selected slot
        empty; any other value = present, value = remaining percent. Returns the raw byte
        list, or None on no response. Do NOT treat the 0xFF sentinels as filament (that
        misread caused a spurious-retrude bug on the reference stack).
        """
        resp = self._send_command(addr, STATUS_OPERATIONAL, CMD_GET_REMAIN_LEN,
                                  data=bytes([slot_mask]), timeout=timeout, retries=1)
        if resp is None:
            return None
        return list(resp.get("data", b""))

    def get_buffer_state(self, buffer_addr: int) -> dict:
        """0x0C GET_BUFFER_STATE on a buffer/feeder node (0x81+). RX is an 8-byte block;
        all-zero = buffer empty (filament parked short). Returns {"bytes","empty"} or None."""
        resp = self._send_command(buffer_addr, STATUS_OPERATIONAL, CMD_GET_BUFFER_STATE,
                                  data=bytes([0x0B]), retries=1)
        if resp is None:
            return None
        d = resp.get("data", b"")
        return {"bytes": d.hex(), "empty": all(b == 0 for b in d)}

    def _ingest_slot_reads(self, material, remain, slot_mask: int = PRELOAD_MASK_ALL) -> set:
        """Fold a 0x02 material map and/or a 0x03 remain byte list into the slot cache.

        Tolerant of None on either input; only slots with a signal are touched. Remain (0x03)
        is the primary presence signal, material (0x02) the fallback/identity. The 0x03 reply
        is positional with 0xFF not-in-mask sentinels (see read_remain). Returns the set of
        updated tool indices."""
        updated = set()
        mat_tokens = {}
        if material:
            for field in material.split(";"):
                field = field.strip()
                if not field or ":" not in field:
                    continue
                name, _, tok = field.partition(":")
                name = name.strip().upper()
                if name in ("A", "B", "C", "D"):
                    mat_tokens[ord(name) - ord("A")] = tok.strip()
        rem_bytes = {}
        if remain is not None:
            sel = [idx for idx in range(4) if slot_mask & (1 << idx)]
            if len(remain) >= 4:
                for idx in sel:
                    rem_bytes[idx] = remain[idx]
            else:
                for ri, idx in enumerate(sel):
                    if ri < len(remain):
                        rem_bytes[idx] = remain[ri]
        for idx in range(4):
            if not (slot_mask & (1 << idx)):
                continue
            present = None
            remain_val = -1
            rb = rem_bytes.get(idx)
            if rb is not None and rb != 0xFF:      # 0xFF = not-reported sentinel, skip
                present = (rb != 0x00)
                remain_val = int(rb)
            tok = mat_tokens.get(idx)
            material_val = None
            if tok is not None:
                tok_present = (tok.lower() != "none" and tok != "")
                if present is None:
                    present = tok_present
                if tok_present:
                    material_val = tok
            if present is None:
                continue
            self._slots[idx] = {"present": bool(present),
                                "material": material_val if present else None,
                                "remain": remain_val if present else -1}
            updated.add(idx)
        return updated

    def extrude_stage(self, addr: int, slot: int, stage_hi: int, stage_lo: int = 0x00,
                      timeout: float = EXTRUDE_STAGE_TIMEOUT_S) -> dict:
        """Send ONE 0x10 EXTRUDE stage frame [slot][stage_hi][stage_lo] and block on its reply.

        The box HOLDS each stage's reply until that stage's mechanical step completes
        (init/finalize ~4.5 s, push ~2 s) -- this blocking per-stage reply IS the ready
        mechanism; there is no host poll. Single-shot (retries=1): the reference host
        dispatches every stage frame once and never re-fires a stage into a busy box.

        Returns the parsed response dict, or None on no reply within `timeout`.
        """
        return self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_EXTRUDE_PROCESS,
            data=bytes([slot, stage_hi, stage_lo]),
            timeout=timeout,
            retries=1,
        )

    @staticmethod
    def _extrude_wheel(resp) -> float:
        """Decode the cumulative measuring-wheel float carried in a 0x05 push reply.

        The push reply payload is the SAME 4-byte BE IEEE-754 word as the 0x0E wheel read:
        negative, magnitude grows ~300 counts per REAL push and ~0 when the box fast-acks a
        self-limited no-op. Returns the float, or None for a non-wheel ack (the short 00/04/
        06/07 stage replies carry no wheel word).
        """
        if resp is None:
            return None
        d = resp.get("data", b"")
        if len(d) < 4:
            return None
        try:
            return struct.unpack(">f", bytes(d[0:4]))[0]
        except (struct.error, TypeError):
            return None

    def extrude_load_ramp_gated(self, addr: int, slot: int, sensor_fn, deadline_fn,
                                max_pushes: int = LOAD_TOPUP_MAX_BURSTS) -> bool:
        """ONE sensor-gated 0x10 load cycle: init -> engage -> looped pushes -> [switch] ->
        settle -> finalize.

        This is the validated load behavior (hardware-proven on the reference stack;
        supersedes the fixed 5-stage settle-based ramp): the 0x05 push REPEATS and the
        0x06/0x07 finalize is issued ONLY after sensor_fn() (the toolhead filament switch)
        latches True. The loop exit is the SWITCH, never a fixed push count. The box
        self-limits to ~3 real pushes per 0x00-init arm, then fast-acks no-op pushes (wheel
        advance ~0); when LOAD_PUSH_STALL_LIMIT consecutive pushes advance the wheel less
        than LOAD_PUSH_MIN_ADVANCE, the cycle breaks early so the CALLER re-arms with a
        fresh init instead of grinding dead pushes. Every blocking stage is clamped to the
        remaining wall budget via deadline_fn().

        Args:
            addr: Controller address.
            slot: 1-hot slot bitmask.
            sensor_fn: Callable returning True/False/None -- the toolhead filament switch.
            deadline_fn: Callable returning the remaining wall budget in seconds.
            max_pushes: Per-arm push cap (the box self-limit plus margin).

        Returns:
            bool: True if the switch latched during this cycle (settle/finalize were issued).
        """
        self.extrude_stage(addr, slot, EXTRUDE_SUB_INIT, 0x00,
                           timeout=min(EXTRUDE_STAGE_TIMEOUT_S, max(0.0, deadline_fn())))
        self.extrude_stage(addr, slot, EXTRUDE_SUB_ENGAGE, 0x00,
                           timeout=min(EXTRUDE_STAGE_TIMEOUT_S, max(0.0, deadline_fn())))
        pushes = 0
        last_wheel = None
        stalled = 0
        while pushes < max_pushes and deadline_fn() > 0:
            if sensor_fn() is True:
                break
            resp = self.extrude_stage(
                addr, slot, EXTRUDE_SUB_PUSH, 0x00,
                timeout=min(EXTRUDE_STAGE_TIMEOUT_S, max(0.0, deadline_fn())))
            pushes += 1
            wheel = self._extrude_wheel(resp)
            if wheel is not None and last_wheel is not None:
                if abs(wheel - last_wheel) < LOAD_PUSH_MIN_ADVANCE:
                    stalled += 1
                    if stalled >= LOAD_PUSH_STALL_LIMIT:
                        break              # box self-limited this arm -> caller re-arms
                else:
                    stalled = 0
            if wheel is not None:
                last_wheel = wheel
        switched = (sensor_fn() is True)
        if switched and deadline_fn() > 0:
            self.extrude_stage(addr, slot, EXTRUDE_SUB_SETTLE, 0x00,
                               timeout=min(EXTRUDE_STAGE_TIMEOUT_S, max(0.0, deadline_fn())))
            self.extrude_stage(addr, slot, EXTRUDE_SUB_FINALIZE, EXTRUDE_FINALIZE_DATA,
                               timeout=min(EXTRUDE_STAGE_TIMEOUT_S, max(0.0, deadline_fn())))
        return switched

    def extrude_process(self, addr: int, slot: int = SLOT_T1) -> dict:
        """CMD_EXTRUDE_PROCESS (0x10): drive the box feed toward the toolhead, sensor-gated.

        v1.4.0 REBUILD (behavioral source: the hardware-validated reference stack). The load
        runs extrude_load_ramp_gated() cycles -- looped 0x05 pushes gated on the toolhead
        filament switch, re-armed with a fresh init until the switch latches -- bounded by
        the load_wall_budget. The pre-v1.4.0 fixed 5-stage ramp with a position-settle exit
        is gone, as is its [state][uint16] reply misparse (the reply is the BE IEEE-754
        wheel float; see _extrude_wheel).

        Without a toolhead filament switch (sensor reads None) the load degrades to ONE
        ungated cycle: it cannot know when filament arrives, so it runs the box's own
        self-limited feed once and reports latched=False.

        NOTE: this drives the 0x10 feed ONLY. The full load choreography (feed-mode entry,
        0x0F engage/release bracket, temp guard, cut check, print mode) lives in
        load_process() / CFS_EXTRUDE.

        Returns:
            dict: {'latched': bool -- switch tripped and settle/finalize were issued,
                   'cycles': int -- gated cycles run,
                   'have_sensor': bool}
        """
        if not (ADDR_BOX_MIN <= addr <= ADDR_BOX_MAX):
            raise ValueError(f"addr 0x{addr:02X} out of range")
        if slot not in SLOT_BITMASKS:
            raise ValueError(f"slot 0x{slot:02X} is not a 1-hot bitmask in {SLOT_BITMASKS}")

        have_sensor = self._toolhead_filament_detected() is not None
        deadline = self.reactor.monotonic() + self.load_wall_budget
        deadline_fn = lambda: deadline - self.reactor.monotonic()
        latched = False
        cycles = 0
        while deadline_fn() > 0:
            cycles += 1
            latched = self.extrude_load_ramp_gated(
                addr, slot, self._toolhead_filament_detected, deadline_fn,
                self.load_max_bursts)
            if latched or not have_sensor:
                break
        logger.info(
            "creality_cfs: EXTRUDE_PROCESS addr=0x%02X slot=0x%02X latched=%s cycles=%d "
            "sensor=%s", addr, slot, latched, cycles, have_sensor)
        return {'latched': latched, 'cycles': cycles, 'have_sensor': have_sensor}

    def retrude_phase(self, addr: int, slot: int, phase: int, timeout: float) -> int:
        """Send ONE 0x11 RETRUDE frame [slot][phase] and return its reply STATUS byte.

        phase RETRUDE_PHASE_START (0x00) replies fast on an empty pull (~0.25 s) but a REAL
        pull holds the reply ~12-14 s; phase RETRUDE_PHASE_FINISH (0x01) is HELD ~9.6 s while
        the box reels the filament fully in. Callers MUST pass a timeout covering the hold
        (RETRUDE_START_TIMEOUT_S / RETRUDE_FINISH_TIMEOUT_S clamped to the wall budget).
        Single-shot. Returns the STATUS byte (0x00 on the wire for both frames), or None on
        no reply -- which is DIAGNOSTIC ONLY: unload completion gates on the toolhead
        filament switch, never on this byte (the 0x14/0x16 status model is wire-disproven).
        """
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_RETRUDE_PROCESS,
            data=bytes([slot, phase & 0xFF]),
            timeout=timeout,
            retries=1,
        )
        if resp is None:
            return None
        return resp.get("status")

    def retrude_process(self, addr: int, slot: int = SLOT_T1) -> bool:
        """CMD_RETRUDE_PROCESS (0x11): the bus-only START/FINISH unload pair.

        v1.4.0 REBUILD. The unload is a START/FINISH COMMAND PAIR -- [slot][0x00] then
        [slot][0x01] -- BOTH frames carrying the slot bitmask:
          REQ start:  f7 01 05 ff 11 [slot] 00 [crc]
          REQ finish: f7 01 05 ff 11 [slot] 01 [crc]
          RSP (both): f7 01 03 00 11 [crc]  (bare ACK; the FINISH ACK is HELD ~9.6 s)
        The pre-v1.4.0 version fired both frames back-to-back with 0.5 s timeouts and treated
        the ACKs as completion; on real hardware the held FINISH ACK ALWAYS timed out, so an
        unload could never be confirmed. This method now uses the validated hold-covering
        timeouts and reports the ACKs -- but the ACKs are still only transport truth, NOT
        unload completion. The full unload (interleaved toolhead pull, sensor prep reads,
        toolhead-switch completion gate, melt guard, wall budget) is unload_process() /
        CFS_RETRUDE; use that for a real unload.

        (v1.4.0 removal: the pre-v1.4.0 'buffer node 0x81 single-byte retrude' form is
        wire-disproven -- func-0x11 traffic on 0x81/0x82 is FOC-servo traffic on the shared
        bus of the reference printer, not a CFS retrude -- and was removed.)

        Returns:
            bool: True if both frames ACKed (transport-level only).
        """
        if not (ADDR_BOX_MIN <= addr <= ADDR_BOX_MAX):
            raise ValueError(f"addr 0x{addr:02X} out of range")
        if slot not in SLOT_BITMASKS:
            raise ValueError(f"slot 0x{slot:02X} is not a 1-hot bitmask in {SLOT_BITMASKS}")

        st_start = self.retrude_phase(addr, slot, RETRUDE_PHASE_START,
                                      timeout=RETRUDE_START_TIMEOUT_S)
        if st_start is None:
            logger.warning(
                "creality_cfs: RETRUDE_PROCESS addr=0x%02X slot=0x%02X START no reply",
                addr, slot)
            return False
        st_finish = self.retrude_phase(addr, slot, RETRUDE_PHASE_FINISH,
                                       timeout=RETRUDE_FINISH_TIMEOUT_S)
        if st_finish is None:
            logger.warning(
                "creality_cfs: RETRUDE_PROCESS addr=0x%02X slot=0x%02X FINISH no reply "
                "(the finish ACK is held ~9.6 s on a real pull)", addr, slot)
            return False
        logger.info(
            "creality_cfs: RETRUDE_PROCESS addr=0x%02X slot=0x%02X START/FINISH ACKed "
            "(status 0x%02X/0x%02X)", addr, slot, st_start, st_finish)
        return True

    def get_hardware_status(self, addr: int, channel: int) -> int:
        """CMD_GET_HARDWARE_STATUS (0x08): read toolhead filament-sensor / hardware status.

        WIRE-CONFIRMED 2026-06-09. This is the EXTRUDER filament-sensor read the load logic
        polls; on a 0x08 frame the response is a 1-byte status flag. (Box-state is a SEPARATE
        command, 0x0A; see get_box_state(). v1.1.0 conflated the two.)

        Protocol:
          REQ: f7 [addr] 04 ff 08 [channel] [crc]
          RSP: f7 [addr] 04 00 08 [flag] [crc]
        Flag values seen on the wire:
          0x00 = clear / no filament
          0x01 / 0x02 / 0x04 = busy / feeding
          0x07 = ready flags

        Args:
            addr: Box address (normally 0x01 on the Hi).
            channel: Channel byte sent in the request.

        Returns:
            int: The status flag byte, or -1 if no response.
        """
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_GET_HARDWARE_STATUS,
            data=bytes([channel]),
        )
        if resp is None:
            logger.warning(
                "creality_cfs: GET_HARDWARE_STATUS addr=0x%02X ch=0x%02X, no response", addr, channel
            )
            return -1

        data_bytes = resp.get("data", b"")
        flag = data_bytes[0] if len(data_bytes) >= 1 else 0xFF
        logger.info(
            "creality_cfs: GET_HARDWARE_STATUS addr=0x%02X ch=0x%02X flag=0x%02X", addr, channel, flag
        )
        return flag

    def cut_state_code(self, addr: int) -> int:
        """CMD_CUT_STATE (0x05): read the raw cut-state byte AFTER the mechanical cut.

        The physical cut is MECHANICAL (the toolhead rams the cutter); there is no dedicated
        cut func -- this only READS the state the controller latches afterwards.

        Protocol:
          REQ: f7 [addr] 03 ff 05 [crc]   (no data)
          RSP: f7 [addr] 04 00 05 [state] [crc]
        Decoded values (2026-06-22, stock-vs-empty capture comparison):
          0x00 = cut OK (every real cut returns this)
          0x01 = cut-state-set, seen transiently during a real cut
          0x02 = NOTHING CUT -- slot empty / no filament at the blade. NOT a failure; it
                 just means there was nothing there to cut.
        A failing-cut counter-example (filament present, blade jammed) is still uncaptured,
        so any other byte stays 'cut not confirmed'.

        Returns:
            int: The raw state byte, or None on no response.
        """
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_CUT_STATE,
            data=b"",
        )
        if resp is None:
            logger.warning("creality_cfs: CUT_STATE addr=0x%02X, no response", addr)
            return None
        data_bytes = resp.get("data", b"")
        if len(data_bytes) < 1:
            return None
        logger.info("creality_cfs: CUT_STATE addr=0x%02X state=0x%02X", addr, data_bytes[0])
        return data_bytes[0]

    def cut_state(self, addr: int) -> bool:
        """CMD_CUT_STATE (0x05) as a bool: True iff the state byte is 0x00 (cut OK).

        See cut_state_code() for the raw byte and the 0x00/0x01/0x02 semantics (0x02 =
        nothing-to-cut, which this bool form reports as False -- callers that need the
        distinction use cut_state_code).
        """
        code = self.cut_state_code(addr)
        return code == CUT_STATE_DONE

    def ctrl_connection_motor_action(self, addr: int, engage: bool) -> bool:
        """CMD_CTRL_CONNECTION_MOTOR_ACTION (0x0F): engage/release the feeder motor.

        WIRE-CONFIRMED 2026-06-09. These calls bracket a tool change: engage before, release
        after. Hi uses 0x0F; do NOT use the CAN binary's 0x07 for this on the Hi wire.

        Protocol:
          REQ: f7 [addr] 04 ff 0f [01|00] [crc]   (0x01 = engage, 0x00 = release)
          RSP: ACK

        Args:
            addr: Box address (normally 0x01 on the Hi).
            engage: True to engage the feeder motor, False to release it.

        Returns:
            bool: True if the command was acknowledged.
        """
        action = MOTOR_ACTION_ENGAGE if engage else MOTOR_ACTION_RELEASE
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_CTRL_CONNECTION_MOTOR_ACTION,
            data=bytes([action]),
        )
        if resp is None:
            logger.warning(
                "creality_cfs: CTRL_CONNECTION_MOTOR_ACTION addr=0x%02X action=0x%02X, no response",
                addr, action,
            )
            return False
        logger.info(
            "creality_cfs: CTRL_CONNECTION_MOTOR_ACTION addr=0x%02X %s acknowledged",
            addr, "engage" if engage else "release",
        )
        return True

    def measuring_wheel(self, addr: int) -> bytes:
        """CMD_MEASURING_WHEEL (0x0E): read the feed encoder / measuring-wheel word, raw.

        Protocol:
          REQ: f7 [addr] 04 ff 0e 01 [crc]   (data = [0x01])
          RSP: f7 [addr] .. 00 0e [4 bytes] [crc]

        DECODE RESOLVED (v1.4.0; was 'UNRESOLVED' pre-v1.4.0): the 4-byte word is a
        BIG-ENDIAN IEEE-754 FLOAT. The value is NEGATIVE and climbs in MAGNITUDE as filament
        feeds (e.g. -462 -> -761 -> -1077 mm across a load; 0xc499c5bf -> -1230.18). The
        0xC4/0xC5 'tag byte' the old captures saw was simply the float's exponent byte.
        Use measuring_wheel_mm() for the decoded mm value. This raw form is kept because
        the negative floats keep the sign bit set, so their raw big-endian word ALSO
        increases monotonically -- fine for advance/no-advance checks that need no units.

        Returns:
            bytes: The raw response data bytes (expected 4), or b"" if no response.
        """
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_MEASURING_WHEEL,
            data=bytes([0x01]),
        )
        if resp is None:
            logger.warning("creality_cfs: MEASURING_WHEEL addr=0x%02X, no response", addr)
            return b""

        data_bytes = resp.get("data", b"")
        logger.debug(
            "creality_cfs: MEASURING_WHEEL addr=0x%02X raw=%s",
            addr, data_bytes.hex() if data_bytes else "(none)",
        )
        return data_bytes

    def measuring_wheel_mm(self, addr: int) -> float:
        """CMD_MEASURING_WHEEL (0x0E) decoded as a SIGNED mm value (BE IEEE-754 float).

        The wheel is NEGATIVE and grows in magnitude as filament feeds; consumers compare
        the ABSOLUTE advance |now - start| against a target/threshold (the flush clog
        watchdog does exactly that). Returns the float mm, or None on no response / short
        frame -- callers MUST tolerate None (a printer without the wheel in the filament
        path, or a flaky read, must not false-trip a watchdog).
        """
        raw = self.measuring_wheel(addr)
        if raw is None or len(raw) < 4:
            return None
        try:
            return struct.unpack(">f", bytes(raw[0:4]))[0]
        except (struct.error, TypeError):
            return None

    # -----------------------------------------------------------------------
    # Choreography helpers (v1.4.0; behavioral source: the hardware-validated
    # reference stack -- see the module changelog)
    # -----------------------------------------------------------------------

    def _toolhead_filament_detected(self):
        """Toolhead filament-switch state: True/False, or None if no sensor exists.

        [creality_cfs] filament_sensor: names the [filament_switch_sensor <name>] section.
        This is the authoritative 'filament reached the toolhead' signal that gates the load
        finalize and the unload completion -- the 0x10/0x11 replies do NOT carry it. Never
        raises: a missing or odd sensor object returns None (callers degrade gracefully).
        """
        obj = self.printer.lookup_object(
            "filament_switch_sensor " + self.filament_sensor_name, None)
        if obj is None:
            return None
        try:
            return bool(obj.get_status(self.reactor.monotonic()).get("filament_detected"))
        except Exception:
            return None

    def _effective_temp(self, gcmd, label: str) -> float:
        """Resolve the effective melt temperature (TEMP= override, else extrude_temp) and
        enforce the MIN_EXTRUDE_TEMP floor. Raises gcmd.error below the floor -- both the
        hotend E moves (which mainline's min_extrude_temp would hard-error anyway) and the
        box-motor feed (which mainline does NOT protect) refuse to run cold."""
        temp = gcmd.get_float("TEMP", self.extrude_temp)
        if temp < MIN_EXTRUDE_TEMP:
            raise gcmd.error(
                "%s aborted: effective temperature %.0fC is below the %.0fC cold-extrude "
                "floor. Pushing or pulling solid filament through a cold hotend strips the "
                "gears / clogs the path (and any hotend E move hard-errors on mainline "
                "Klipper's min_extrude_temp). Heat the hotend first or pass TEMP=."
                % (label, temp, MIN_EXTRUDE_TEMP))
        return temp

    def _melt_guard(self, gcmd, label: str) -> float:
        """MIN_EXTRUDE_TEMP floor + BLOCKING M109 heat-and-wait.

        Called before ANY filament motion toward or out of the hotend: the box-motor feed
        (bypasses Klipper's cold-extrude protection entirely), the unload's toolhead pull,
        the flush purge and the cut. M109 blocks until the hotend actually reaches the
        target, so the following moves are legal on mainline Klipper (which KEEPS the
        min_extrude_temp raise the Creality fork deletes). Returns the effective temp."""
        temp = self._effective_temp(gcmd, label)
        self.gcode.run_script_from_command("M109 S%d" % int(temp))
        return temp

    def _toolhead_pull(self) -> None:
        """The SINGLE interleaved unload pull between the START and FINISH frames:
        G1 E-15 F360 (relative). The reference .so derives -15/360 internally regardless of
        config -- literal, and exactly ONCE per unload. Skipped if the hotend measurably
        reads below the melt floor (the caller melt-guards first; this is the last-line
        check so a failed heat never cold-grinds the gears)."""
        try:
            ext = self.printer.lookup_object("extruder", None)
            temp = None
            if ext is not None:
                temp = ext.get_status(self.reactor.monotonic()).get("temperature")
            if temp is not None and float(temp) < MIN_EXTRUDE_TEMP:
                logger.warning("creality_cfs: skipping the unload toolhead pull -- hotend "
                               "reads %.0fC (< %.0fC floor)", float(temp), MIN_EXTRUDE_TEMP)
                return
        except Exception:
            # Temperature unreadable (odd host object): the caller's blocking M109 already
            # ran, so proceed with the pull rather than silently skipping it.
            pass
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command(
            "G1 E-%.3f F%.0f" % (RETRUDE_TOOLHEAD_PULL_MM, RETRUDE_TOOLHEAD_PULL_VEL))

    def _dwell(self, seconds: float) -> None:
        """In-handler pacing dwell (G4). Stays inside the gcode context and lets the
        reactor service the serial fd between polls."""
        self.gcode.run_script_from_command("G4 P%d" % int(max(0.0, seconds) * 1000))

    def load_process(self, gcmd, addr: int, slot: int) -> None:
        """The FULL validated load choreography (CFS_EXTRUDE):

          M109 melt guard -> 0x04 [00][slot] enter feed mode -> 0x0F engage -> one-shot
          0x08 liveness ping (fire-and-log; NOT a gate) -> sensor-gated 0x10 ramp cycles
          (extrude_load_ramp_gated, re-armed until the toolhead switch latches, 90 s wall
          budget) -> 0x05 cut check -> 0x04 [slot][00] print mode -> 0x0F release.

        The hotend purge is NOT here -- it is the separate CFS_FLUSH, exactly as the
        validated stack sequences it (the load is strictly box-side; the box's blocking
        per-stage replies are the pacing). On a sensor-equipped rig a load whose switch
        never trips within the budget raises a RECOVERABLE gcmd.error (releases the gcode
        mutex so a retry macro can re-run it). Sensorless rigs run ONE ungated cycle.
        """
        # TEMP GUARD FIRST: the box-motor feed rams filament toward the hotend and bypasses
        # Klipper's cold-extrude protection entirely -- enforce our own floor + M109.
        self._melt_guard(gcmd, "CFS_EXTRUDE")
        self.enter_feed_mode(addr, slot)              # 0x04 [00][slot]
        self.ctrl_connection_motor_action(addr, True)  # 0x0F 01 engage
        flag = self.get_hardware_status(addr, 0x00)    # one-shot ping; do NOT gate on it
        logger.info("creality_cfs: load ready-ping 0x08 -> %s (one-shot, proceeding)",
                    ("0x%02X" % flag) if flag is not None and flag >= 0 else "no-resp")
        have_sensor = self._toolhead_filament_detected() is not None
        result = self.extrude_process(addr, slot)
        if have_sensor and not result['latched']:
            # Faithful to the validated implementation: the feeder is NOT released on a
            # failed load (a retry re-runs the whole choreography, which re-engages it).
            raise gcmd.error(
                "CFS_EXTRUDE: filament did not reach the toolhead -- the filament switch "
                "never tripped within %.0fs over %d ramp cycle(s) on slot 0x%02X. Clear any "
                "jam / check the slot is loaded, then retry the load."
                % (self.load_wall_budget, result['cycles'], slot))
        code = self.cut_state_code(addr)               # 0x05 post-load check (diagnostic)
        if code not in (None, CUT_STATE_DONE):
            gcmd.respond_info("CFS_EXTRUDE: cut_state 0x05 -> 0x%02X after load -- inspect."
                              % code)
        self.set_print_mode(addr, slot)                # 0x04 [slot][00]
        self.ctrl_connection_motor_action(addr, False)  # 0x0F 00 release
        try:
            self._active_tool = SLOT_BITMASKS.index(slot)
        except ValueError:
            self._active_tool = None
        if have_sensor:
            gcmd.respond_info(
                "CFS_EXTRUDE: filament reached the toolhead (switch tripped) after %d ramp "
                "cycle(s); print mode set, feeder released." % result['cycles'])
        else:
            gcmd.respond_info(
                "CFS_EXTRUDE: ran %d ungated ramp cycle (no toolhead filament switch "
                "configured -- cannot confirm arrival); print mode set, feeder released."
                % result['cycles'])

    def unload_process(self, gcmd, addr: int, slot: int) -> None:
        """The FULL validated unload choreography (CFS_RETRUDE):

          M109 melt guard -> 0x04 [00][slot] enter feed mode -> 0x08 [00] (material) ->
          START 0x11 [slot][00] -> ONE toolhead G1 E-15 F360 pull -> 0x08 [01]
          (connections) -> FINISH 0x11 [slot][01] (ACK held ~9.6 s; 13 s timeout) ->
          toolhead switch CLEARS = complete.

        The wall-clock deadline (60 s) is set before the START frame and every blocking
        call is clamped to the remaining budget, so the worst-case gcode-mutex hold is
        bounded even on a jam. The 0x11 reply statuses are logged as diagnostics only. If
        the switch never clears within the budget the unload raises a RECOVERABLE
        gcmd.error. Sensorless rigs fall back to box-state corroboration (not loaded and
        not feeding), else treat the completed FINISH frame as success.
        """
        deadline = self.reactor.monotonic() + RETRUDE_WALL_BUDGET_S
        # MELT GUARD FIRST: a cold hotend silently fails to pull filament out of the gears
        # (no-op unload), and the E-15 pull would hard-error on mainline anyway.
        self._melt_guard(gcmd, "CFS_RETRUDE")
        self.enter_feed_mode(addr, slot)                        # 0x04 [00][slot]
        self.get_hardware_status(addr, HW_SENSOR_MATERIAL)      # 0x08 00 (material), once
        remaining = deadline - self.reactor.monotonic()
        if remaining > 0:
            st = self.retrude_phase(addr, slot, RETRUDE_PHASE_START,
                                    timeout=min(RETRUDE_START_TIMEOUT_S, remaining))
            if st not in (None, 0x00):
                gcmd.respond_info("CFS_RETRUDE: START frame status 0x%02X (diagnostic only; "
                                  "completion gates on the toolhead switch)." % st)
        self._toolhead_pull()                                   # ONE G1 E-15 F360
        self.get_hardware_status(addr, HW_SENSOR_CONNECTIONS)   # 0x08 01 (connections), once
        remaining = deadline - self.reactor.monotonic()
        if remaining > 0:
            st = self.retrude_phase(addr, slot, RETRUDE_PHASE_FINISH,
                                    timeout=min(RETRUDE_FINISH_TIMEOUT_S, remaining))
            if st not in (None, 0x00):
                gcmd.respond_info("CFS_RETRUDE: FINISH frame status 0x%02X (diagnostic only)."
                                  % st)
        # COMPLETION GATE: the toolhead filament switch must clear (go not-detected).
        had_sensor = self._toolhead_filament_detected() is not None
        sensor_deadline = min(deadline,
                              self.reactor.monotonic() + RETRUDE_SENSOR_WAIT_S)
        done = False
        while self.reactor.monotonic() < sensor_deadline:
            det = self._toolhead_filament_detected()
            if det is False:
                done = True
                break
            if not had_sensor:
                # Sensorless corroboration: the box reports the slot no longer loaded AND
                # no longer in feed mode -> slot emptied.
                st = self.get_box_state(addr)
                if st is not None and not st.get("loaded") and not st.get("feeding"):
                    done = True
                    break
            self._dwell(RETRUDE_SENSOR_POLL_DT_S)
        if not done and not had_sensor:
            # Never fail a sensorless rig on the absence of a signal it cannot produce:
            # the completed START/pull/FINISH sequence is the best truth available.
            done = True
        if done:
            if self._active_tool is not None and SLOT_BITMASKS[self._active_tool] == slot:
                self._active_tool = None
            gcmd.respond_info("CFS_RETRUDE: unload complete on slot 0x%02X (toolhead "
                              "filament switch cleared)." % slot
                              if had_sensor else
                              "CFS_RETRUDE: unload sequence complete on slot 0x%02X "
                              "(no toolhead switch -- verify visually)." % slot)
            return
        raise gcmd.error(
            "CFS_RETRUDE: the toolhead filament switch did not clear within the %.0fs "
            "budget on slot 0x%02X -- filament is likely jammed between the gears and the "
            "buffer. Clear the jam and retry the unload." % (RETRUDE_WALL_BUDGET_S, slot))

    # ---- change-flush helpers (wire-verified split model) ----
    def _flush_cap(self) -> float:
        """The per-cycle purge cap, bounded by FLUSH_CAP_MAX so a mis-set config can never
        produce one oversized G1 E purge."""
        cap = self.flush_cycle_cap
        if cap is None or cap <= 0:
            cap = FLUSH_CYCLE_CAP_DEFAULT
        return min(float(cap), FLUSH_CAP_MAX)

    def _flush_cycles(self, total: float, cap: float = None) -> list:
        """Split a TOTAL flush purge length into per-cycle purges (wire-verified model):
        if total <= cap -> [total]; else [cap] + the remainder split EQUALLY across
        ceil(remainder/cap) cycles. Verified breakdowns: 158.75 -> [80, 78.75];
        343.33 -> [80, 65.83 x4]; 101.25 -> [80, 21.25]. Cycle count hard-capped at
        FLUSH_CYCLES_MAX."""
        import math
        if cap is None:
            cap = self._flush_cap()
        cap = float(cap)
        total = float(total)
        if total <= 0:
            return []
        if total <= cap:
            return [total]
        rest = total - cap
        n = max(1, int(math.ceil(rest / cap)))
        if n > FLUSH_CYCLES_MAX - 1:
            n = FLUSH_CYCLES_MAX - 1
        return [cap] + [rest / n] * n

    def _default_flush_total(self, gcmd) -> float:
        """The change-flush TOTAL purge length:
        LEN= (the explicit total) > VOLUME= (flush volume in mm^3, run through the
        wire-verified formula base + (5/12)*volume*multiplier with base = nozzle_volume/2.4)
        > flush_default_len."""
        explicit = gcmd.get_float("LEN", None, above=0.)
        if explicit is not None:
            return explicit
        base = self.nozzle_volume / 2.4
        vol = gcmd.get_float("VOLUME", None, above=0.)
        if vol is not None:
            return base + FLUSH_VOL_COEFF * vol * self.flush_multiplier
        return self.flush_default_len

    # -----------------------------------------------------------------------
    # Klipper status export (v1.4.0): lets macros resolve printer["creality_cfs"]
    # -----------------------------------------------------------------------

    def get_status(self, eventtime) -> dict:
        """Status dict for the printer object / Moonraker. Referenced by the shipped
        macros (box_count, active_tool) and useful for UIs (per-slot presence cache)."""
        online = {}
        for entry in self._box_table:
            online["box%d" % entry.addr] = (
                entry.online == BoxAddressEntry.ONLINE_ONLINE)
        return {
            "is_connected": self.is_connected,
            "box_count": self.box_count,
            "online": online,
            "active_tool": self._active_tool if self._active_tool is not None else -1,
            "slots": {str(k): dict(v) for k, v in self._slots.items()},
        }

    # -----------------------------------------------------------------------
    # G-code command handlers
    # -----------------------------------------------------------------------

    cmd_CFS_INIT_help: str = (
        "Run the CFS auto-addressing sequence to discover and assign addresses "
        "to all connected Creality Filament System boxes"
    )

    def cmd_CFS_INIT(self, gcmd) -> None:
        """G-code: CFS_INIT, run the full 5-step auto-addressing sequence.

        Usage: CFS_INIT
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected; check serial_port in config")
        try:
            online_count: int = self._run_auto_addressing()
            gcmd.respond_info(
                f"CFS auto-addressing complete: {online_count}/{self.box_count} box(es) online"
            )
        except Exception as exc:
            raise gcmd.error(f"CFS_INIT failed: {exc}")

    cmd_CFS_STATUS_help: str = (
        "Query the operating state of one or all CFS boxes. "
        "Optionally specify BOX=<1-4> for a single box."
    )

    def cmd_CFS_STATUS(self, gcmd) -> None:
        """G-code: CFS_STATUS [BOX=<1-4>], query box state.

        Usage: CFS_STATUS          # query all boxes
               CFS_STATUS BOX=2   # query box 2 only
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        box_param = gcmd.get_int("BOX", None, minval=1, maxval=4)
        addrs = [box_param] if box_param is not None else list(range(1, self.box_count + 1))

        results = []
        for addr in addrs:
            entry = self._box_table[addr - 1]
            if not entry.mapped:
                results.append(f"Box {addr}: not assigned (run CFS_INIT first)")
                continue
            try:
                st = self.get_box_state(addr)
                if st is None:
                    results.append(f"Box {addr} (0x{addr:02X}): NO RESPONSE")
                    continue
                name = ("LOADED" if st["loaded"]
                        else "FEEDING" if st["feeding"]
                        else "0x%s" % st["raw"].hex())
                event = st.get("event")
                extra = ""
                if event == BOX_EVENT_INSERT:
                    extra = " [insert event]"
                elif st.get("busy"):
                    extra = " [busy/cal active]"
                results.append(
                    f"Box {addr} (0x{addr:02X}): {name} raw={st['raw'].hex()}{extra}"
                )
            except Exception as exc:
                results.append(f"Box {addr}: ERROR: {exc}")

        gcmd.respond_info("\n".join(results))

    cmd_CFS_VERSION_help: str = (
        "Query firmware version and serial number from one or all CFS boxes. "
        "Optionally specify BOX=<1-4> for a single box."
    )

    def cmd_CFS_VERSION(self, gcmd) -> None:
        """G-code: CFS_VERSION [BOX=<1-4>], query version/SN.

        Usage: CFS_VERSION         # query all boxes
               CFS_VERSION BOX=1  # query box 1 only
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        box_param = gcmd.get_int("BOX", None, minval=1, maxval=4)
        addrs = [box_param] if box_param is not None else list(range(1, self.box_count + 1))

        results = []
        for addr in addrs:
            entry = self._box_table[addr - 1]
            if not entry.mapped:
                results.append(f"Box {addr}: not assigned (run CFS_INIT first)")
                continue
            try:
                version_str = self.get_version_sn(addr)
                results.append(f"Box {addr} (0x{addr:02X}): {version_str}")
            except Exception as exc:
                results.append(f"Box {addr}: ERROR: {exc}")

        gcmd.respond_info("\n".join(results))

    cmd_CFS_SET_MODE_help: str = (
        "Set operating mode on a CFS box. "
        "Parameters: BOX=<1-4> [TOOL=<0-3>] [MODE=<0-255>] [PARAM=<0-255>]"
    )

    def cmd_CFS_SET_MODE(self, gcmd) -> None:
        """G-code: CFS_SET_MODE BOX=<1-4> [TOOL=<0-3>] [MODE=<0-255>] [PARAM=<0-255>].

        Two forms (WIRE-CONFIRMED 2026-06-19), see set_box_mode():
          PER-CHANNEL (print-mode): supply TOOL to key the channel byte to the slot
            bitmask SLOT_BITMASKS[TOOL]. Sends [slot_bitmask, 0x00] (01 00 / 02 00 / 04 00).
          ENTER: supply MODE (and optional PARAM) with no TOOL. Sends [MODE, PARAM]
            (the bracketing 00 01 form for entering/exiting a tool change).

        Usage: CFS_SET_MODE BOX=1 TOOL=1       # per-channel print-mode for slot T1 (02 00)
               CFS_SET_MODE BOX=1 MODE=0 PARAM=1   # enter form (00 01)
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        addr = gcmd.get_int("BOX", minval=1, maxval=4)
        tool = gcmd.get_int("TOOL", None, minval=0, maxval=3)

        try:
            if tool is not None:
                # PER-CHANNEL form: channel byte = SLOT_BITMASKS[tool], second byte 0x00.
                slot = SLOT_BITMASKS[tool]
                ok = self.set_box_mode_channel(addr, slot)
                label = f"per-channel slot T{tool} (0x{slot:02X} 0x00)"
            else:
                # ENTER form: [MODE, PARAM], default param 0x01.
                mode = gcmd.get_int("MODE", minval=0, maxval=255)
                param = gcmd.get_int("PARAM", 0x01, minval=0, maxval=255)
                ok = self.set_box_mode(addr, mode, param)
                label = f"mode 0x{mode:02X} param 0x{param:02X}"
            if ok:
                gcmd.respond_info(f"CFS box {addr}: SET_MODE {label}")
            else:
                gcmd.respond_info(
                    f"CFS box {addr}: SET_MODE {label} sent (no explicit ACK received)"
                )
        except Exception as exc:
            raise gcmd.error(f"CFS_SET_MODE failed: {exc}")

    cmd_CFS_SET_PRELOAD_help: str = (
        "Configure pre-loading on a CFS box. "
        "Parameters: BOX=<1-4> MASK=<0-255> ENABLE=<0|1> | PHASE=<0-2>"
    )

    def cmd_CFS_SET_PRELOAD(self, gcmd) -> None:
        """G-code: CFS_SET_PRELOAD BOX=<1-4> MASK=<0-255> (ENABLE=<0|1> | PHASE=<0-2>).

        v1.4.0 INVERSION FIX: on the wire, ARM is phase 0x00 and DISARM is phase 0x01.
        The pre-v1.4.0 handler passed ENABLE straight through as the phase byte, so
        ENABLE=1 emitted [mask][0x01] -- the wire DISARM -- and vice versa. ENABLE now
        maps to the correct phase. The reply STATUS byte is checked (non-ACK reported).

        Advanced form: PHASE= sends an explicit phase byte (2 = per-slot re-arm, which
        BLOCKS ~38 s -- it is given the long blocking timeout automatically).

        Usage: CFS_SET_PRELOAD BOX=1 MASK=15 ENABLE=1   # arm pre-loading, all 4 slots
               CFS_SET_PRELOAD BOX=1 MASK=15 ENABLE=0   # disarm (end of print)
               CFS_SET_PRELOAD BOX=1 MASK=2 PHASE=2     # re-arm slot B (blocking ~38 s)
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        addr = gcmd.get_int("BOX", minval=1, maxval=4)
        mask = gcmd.get_int("MASK", minval=0, maxval=255)
        phase = gcmd.get_int("PHASE", None, minval=0, maxval=2)
        if phase is None:
            enable = gcmd.get_int("ENABLE", minval=0, maxval=1)
            # ARM = wire phase 0x00, DISARM = 0x01 (the inversion fix).
            phase = PRELOAD_PHASE_ARM if enable else PRELOAD_PHASE_DISARM
        # The per-slot re-arm blocks ~38 s while the controller settles the slot servo; a
        # short timeout would hang up mid-phase and NAK-wedge the box.
        timeout = (PRELOAD_BLOCKING_TIMEOUT_S
                   if phase == PRELOAD_PHASE_SLOT_REARM else None)

        try:
            # Single-shot (retries=1): the reference implementation never retries a 0x0D
            # frame, and a retried 90 s blocking phase on a silent box would otherwise hold
            # the gcode mutex for retry_count x 90 s (review finding).
            ok = self.set_pre_loading(addr, mask, phase, timeout=timeout, retries=1)
            label = {PRELOAD_PHASE_ARM: "armed",
                     PRELOAD_PHASE_DISARM: "disarmed",
                     PRELOAD_PHASE_SLOT_REARM: "slot re-arm run"}.get(
                         phase, "phase 0x%02X sent" % phase)
            if ok:
                gcmd.respond_info(
                    f"CFS box {addr}: pre-loading {label} for slot mask 0x{mask:02X}"
                )
            else:
                gcmd.respond_info(
                    "CFS box %d: SET_PRE_LOADING [%02X %02X] NOT ACKed -- the "
                    "controller did not confirm (see log)" % (addr, mask, phase)
                )
        except Exception as exc:
            raise gcmd.error(f"CFS_SET_PRELOAD failed: {exc}")

    cmd_CFS_ADDR_TABLE_help: str = (
        "Print the current CFS address assignment table (which boxes are online)"
    )

    def cmd_CFS_ADDR_TABLE(self, gcmd) -> None:
        """G-code: CFS_ADDR_TABLE, print address assignment table."""
        lines = ["CFS Address Table:"]
        for entry in self._box_table:
            online_str = {
                BoxAddressEntry.ONLINE_OFFLINE: "OFFLINE",
                BoxAddressEntry.ONLINE_ONLINE: "ONLINE",
                BoxAddressEntry.ONLINE_INIT: "INIT",
                BoxAddressEntry.ONLINE_WAIT_ACK: "WAIT_ACK",
            }.get(entry.online, f"UNKNOWN({entry.online})")
            mode_str = "APP" if entry.mode == BoxAddressEntry.MODE_APP else "LOADER"
            uniid_str = " ".join(f"{b:02X}" for b in entry.uniid) if entry.mapped else "-"
            lines.append(
                f"  Addr 0x{entry.addr:02X}: {online_str} | mode={mode_str} "
                f"| mapped={entry.mapped} | acked={entry.acked} "
                f"| lost={entry.lost_cnt} | uniid=[{uniid_str}]"
            )
        gcmd.respond_info("\n".join(lines))

    cmd_CFS_EXTRUDE_help: str = (
        "Load filament from a CFS slot to the toolhead (full sensor-gated choreography). "
        "Parameters: TOOL=<0-3> [BOX=<1-4>] [TEMP=<C>]"
    )

    def cmd_CFS_EXTRUDE(self, gcmd) -> None:
        """G-code: CFS_EXTRUDE TOOL=<0-3> [BOX=<1-4>] [TEMP=<C>] -- the full validated load.

        v1.4.0: runs the complete choreography (M109 melt guard, feed-mode entry, feeder
        engage, sensor-gated 0x10 push loop with re-arm cycles, cut check, print mode,
        feeder release) -- see load_process(). TOOL is REQUIRED: it selects the SLOT
        BITMASK (T0..T3 -> 0x01/0x02/0x04/0x08) on the controller. BOX selects the
        CONTROLLER bus address for multi-box daisy-chains and defaults to 1 -- it is a
        SEPARATE axis from the tool slot (the pre-v1.4.0 macros conflated the two).

        Usage: CFS_EXTRUDE TOOL=2
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        addr = gcmd.get_int("BOX", 1, minval=1, maxval=4)
        tool = gcmd.get_int("TOOL", minval=0, maxval=3)
        slot = SLOT_BITMASKS[tool]
        self.load_process(gcmd, addr, slot)

    cmd_CFS_RETRUDE_help: str = (
        "Unload filament from the toolhead back into the CFS (full validated choreography). "
        "Parameters: TOOL=<0-3> [BOX=<1-4>] [TEMP=<C>]"
    )

    def cmd_CFS_RETRUDE(self, gcmd) -> None:
        """G-code: CFS_RETRUDE TOOL=<0-3> [BOX=<1-4>] [TEMP=<C>] -- the full validated unload.

        v1.4.0: runs the complete choreography (M109 melt guard, feed-mode entry, 0x08
        sensor prep reads, the 0x11 START/FINISH pair with the single interleaved toolhead
        E-15 pull, and the toolhead-switch completion gate within a 60 s wall budget) --
        see unload_process(). TOOL is REQUIRED (the slot bitmask); BOX is the controller
        address (multi-box chains only, default 1).

        Usage: CFS_RETRUDE TOOL=0
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        addr = gcmd.get_int("BOX", 1, minval=1, maxval=4)
        tool = gcmd.get_int("TOOL", minval=0, maxval=3)
        slot = SLOT_BITMASKS[tool]
        self.unload_process(gcmd, addr, slot)

    cmd_CFS_CUT_help: str = (
        "Mechanical filament cut: ram the toolhead into the cutter, then confirm via the "
        "0x05 cut-state read. Parameters: [BOX=<1-4>] [TEMP=<C>]. Requires cut_switch_pin "
        "and the cut geometry in [creality_cfs]."
    )

    def cmd_CFS_CUT(self, gcmd) -> None:
        """G-code: CFS_CUT [BOX=<1-4>] [TEMP=<C>] -- the mechanical cut ram.

        The cut is MECHANICAL: the toolhead rams the blade lever against the frame-mounted
        cutter; there is no bus 'cut' command (0x05 only READS the latched result).
        Safety rails (all ported from the validated implementation):
          - HARD GUARD: refuses to run without cut_switch_pin configured (the cutter
            microswitch/hall). A blind ram with no switch could crash the toolhead.
          - ZERO-TRAVEL REFUSAL: refuses when the cut position equals the pre-cut position
            (an uncalibrated cut would move nowhere and leave the strand uncut -- the
            follow-up load then jams against it).
          - Travel bound: cut_pos_x_max caps the ram target.
          - M109 preheat to the melt temperature before severing (cold filament shatters
            or resists the blade).
        Post-check: 0x05 -- 0x00 cut OK; 0x02 nothing-to-cut (empty slot, not a failure);
        anything else is surfaced as 'cut not confirmed'.
        """
        # Connection guard FIRST -- before any heat or motion. Without it, a disconnected
        # CFS would let the ram run and then hard-error out of the 0x05 post-read (review
        # finding: partial mechanical action followed by a shutdown instead of a clean
        # recoverable error).
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")
        addr = gcmd.get_int("BOX", 1, minval=1, maxval=4)
        if not self.cut_switch_pin:
            raise gcmd.error(
                "CFS_CUT aborted: no cut_switch_pin configured in [creality_cfs]. Refusing "
                "to blind-ram the toolhead without a cutter switch.")
        pre_x = self.pre_cut_pos_x
        pre_y = self.pre_cut_pos_y
        cut_x = self.cut_pos_x
        cut_y = self.cut_pos_y
        if pre_x is None or pre_y is None or (cut_x is None and cut_y is None):
            raise gcmd.error(
                "CFS_CUT aborted: missing cut geometry (need pre_cut_pos_x/pre_cut_pos_y "
                "and cut_pos_x or cut_pos_y in [creality_cfs]).")
        x_max = self.cut_pos_x_max
        if x_max is not None and pre_x > x_max:
            raise gcmd.error("CFS_CUT aborted: pre_cut_pos_x %.2f > cut_pos_x_max %.2f"
                             % (pre_x, x_max))
        if x_max is not None and cut_x is not None and cut_x > x_max:
            raise gcmd.error("CFS_CUT aborted: cut_pos_x %.2f > cut_pos_x_max %.2f"
                             % (cut_x, x_max))
        # ZERO-TRAVEL REFUSAL: a cut position equal to the pre-cut position rams nowhere.
        if cut_x is not None:
            if abs(cut_x - pre_x) < 0.05:
                raise gcmd.error(
                    "CFS_CUT aborted: cut_pos_x (%.2f) equals pre_cut_pos_x -- the cut ram "
                    "would not move and the filament would stay uncut. Calibrate cut_pos_x."
                    % cut_x)
        elif cut_y is None or abs(cut_y - pre_y) < 0.05:
            raise gcmd.error(
                "CFS_CUT aborted: no cut_pos_x and cut_pos_y equals pre_cut_pos_y -- the "
                "cut ram would not move. Calibrate the cut position first.")
        # Melt guard: sever at temperature (blocking M109; also keeps any adjacent E moves
        # legal on mainline).
        self._melt_guard(gcmd, "CFS_CUT")
        fr = self.cut_velocity
        self.gcode.run_script_from_command("G90")
        self.gcode.run_script_from_command("G0 X%.3f Y%.3f F%.0f" % (pre_x, pre_y, fr))
        if cut_x is not None:
            gcmd.respond_info("CFS_CUT: X-axis ram (%.2f,%.2f) -> X%.2f at F%.0f, switch=%s"
                              % (pre_x, pre_y, cut_x, fr, self.cut_switch_pin))
            self.gcode.run_script_from_command("G0 X%.3f F%.0f" % (cut_x, fr))
            self.gcode.run_script_from_command("G0 X%.3f F%.0f" % (pre_x, fr))
        else:
            gcmd.respond_info("CFS_CUT: Y-axis ram (%.2f,%.2f) -> Y%.2f at F%.0f, switch=%s"
                              % (pre_x, pre_y, cut_y, fr, self.cut_switch_pin))
            self.gcode.run_script_from_command("G0 Y%.3f F%.0f" % (cut_y, fr))
            self.gcode.run_script_from_command("G0 Y%.3f F%.0f" % (pre_y, fr))
        self.gcode.run_script_from_command("M400")
        code = self.cut_state_code(addr)
        if code is None:
            gcmd.respond_info("CFS_CUT: cut_state 0x05 NO RESPONSE -- cut UNCONFIRMED. "
                              "Verify the cut visually.")
        elif code == CUT_STATE_DONE:
            gcmd.respond_info("CFS_CUT: cut confirmed (0x05 -> 0x00).")
        elif code == CUT_STATE_NOTHING:
            gcmd.respond_info("CFS_CUT: cut_state 0x05 -> 0x02 (NOTHING TO CUT -- slot "
                              "empty; not a failure).")
        else:
            gcmd.respond_info("CFS_CUT: cut_state 0x05 -> 0x%02X NOT-OK -- the cut may have "
                              "FAILED. Inspect before continuing." % code)

    cmd_CFS_FLUSH_help: str = (
        "Purge the old filament through the hotend after a tool change, in capped cycles "
        "with a measuring-wheel clog watchdog. Parameters: [BOX=<1-4>] [LEN=<mm>] "
        "[VOLUME=<mm3>] [VELOCITY=<mm/min>] [TEMP=<C>]"
    )

    def cmd_CFS_FLUSH(self, gcmd) -> None:
        """G-code: CFS_FLUSH [BOX=] [LEN=|VOLUME=] [VELOCITY=] [TEMP=] -- the change flush.

        The bulk flush is a HOTEND G1 E purge (relative E), split into per-cycle purges
        capped at flush_cycle_cap: cycle 1 = the cap, the remainder split equally
        (wire-verified split model). Total = LEN=, or nozzle_volume/2.4 +
        (5/12)*VOLUME*flush_multiplier, else flush_default_len.

        Per cycle: read the measuring wheel, purge, M400, re-read -- the wheel turns
        because the hotend pulls filament through it, so an advance below
        FLUSH_WHEEL_MIN_FRAC of the purged length means the path is clogging and the flush
        aborts with a recoverable error (the clog watchdog). The check is skipped whenever
        a wheel read returns None, so a printer whose filament path has no wheel (or a
        flaky read) can never false-abort. An optional nozzle_clean_macro runs once per
        cycle (the wipe). Ends with the 1.5 mm retract.

        MAINLINE NOTE: every purge is a hotend G1 E move -- the blocking M109 melt guard
        runs first, which both protects the hardware and satisfies mainline Klipper's
        min_extrude_temp raise.
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")
        addr = gcmd.get_int("BOX", 1, minval=1, maxval=4)
        total = self._default_flush_total(gcmd)
        velocity = gcmd.get_float("VELOCITY", self.flush_velocity, above=0.)
        if total > FLUSH_TOTAL_MAX:
            raise gcmd.error(
                "CFS_FLUSH aborted: computed flush total %.1f mm exceeds the %.0f mm "
                "ceiling. Check LEN=/VOLUME=/flush_default_len." % (total, FLUSH_TOTAL_MAX))
        if total <= 0:
            gcmd.respond_info("CFS_FLUSH: computed total %.1f mm -> nothing to flush." % total)
            return
        cap = self._flush_cap()
        cycles = self._flush_cycles(total, cap)
        gcmd.respond_info(
            "CFS_FLUSH: total %.2f mm in %d cycle(s) (cap %.0f mm) at F%.0f"
            % (total, len(cycles), cap, velocity))
        # Melt guard (blocking M109) before any purge.
        self._melt_guard(gcmd, "CFS_FLUSH")
        self.gcode.run_script_from_command("M83")
        for cyc in cycles:
            mm0 = self.measuring_wheel_mm(addr)
            self.gcode.run_script_from_command("G1 E%.3f F%.0f" % (cyc, velocity))
            self.gcode.run_script_from_command("M400")
            mm1 = self.measuring_wheel_mm(addr)
            if (mm0 is not None and mm1 is not None
                    and abs(mm1 - mm0) < cyc * FLUSH_WHEEL_MIN_FRAC):
                raise gcmd.error(
                    "CFS_FLUSH: under-feed/clog -- the hotend extruded %.1f mm but the "
                    "measuring wheel advanced only %.1f mm. Clear the filament path and "
                    "retry the flush." % (cyc, abs(mm1 - mm0)))
            if self.nozzle_clean_macro:
                try:
                    self.gcode.run_script_from_command(self.nozzle_clean_macro)
                except Exception:
                    logger.exception("creality_cfs: nozzle_clean_macro %r failed "
                                     "(non-fatal; the purge already ran)",
                                     self.nozzle_clean_macro)
        # The post-flush retract (relative E so absolute extruder state is undisturbed).
        self.gcode.run_script_from_command("G91")
        self.gcode.run_script_from_command(
            "G1 E-%.3f F%.0f" % (FLUSH_POST_RETRACT_LEN_MM, FLUSH_POST_RETRACT_VEL))
        self.gcode.run_script_from_command("G90")
        gcmd.respond_info("CFS_FLUSH: complete (%.2f mm purged in %d cycles)."
                          % (total, len(cycles)))

    cmd_CFS_FW_VERSION_help: str = (
        "Query firmware version string from CFS box via 0xF0 VERSION_INFO command. "
        "Parameters: BOX=<1-4>"
    )

    def cmd_CFS_FW_VERSION(self, gcmd) -> None:
        """G-code: CFS_FW_VERSION BOX=<1-4> -- query 0xF0 firmware version.

        Returns the ASCII firmware version string reported by the CFS box.
        Example output: 'cfs0_050_G32-cfs0_000_113'

        Usage: CFS_FW_VERSION BOX=1
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        addr = gcmd.get_int("BOX", minval=1, maxval=4)
        try:
            version = self.get_version_info(addr)
            if version:
                gcmd.respond_info(f"CFS box {addr} firmware: {version}")
            else:
                gcmd.respond_info(f"CFS box {addr}: no version response")
        except Exception as exc:
            raise gcmd.error(f"CFS_FW_VERSION failed: {exc}")


# ---------------------------------------------------------------------------
# Klipper module entry point
# ---------------------------------------------------------------------------

def load_config(config):
    """Klipper module load entry point.

    Called by Klipper when it processes a [creality_cfs] section in printer.cfg.

    Args:
        config: Klipper config object for the [creality_cfs] section.

    Returns:
        CrealityCFS: Configured module instance.
    """
    return CrealityCFS(config)