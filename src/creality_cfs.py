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

Known limitations:
  - Half-duplex RS485 direction switching is left to a hardware auto-direction adapter
    by default. Opt in to the kernel RS485 RTS-as-DE mode with rts_on_send=1 (or 0 for
    RTS-low-on-send); rts_on_send=-1 (default) leaves the UART alone.
  - Serial I/O is fully non-blocking and reactor-driven (v1.3.0). A registered fd callback
    parses incoming frames; a reactor.completion delivers the matched response to the waiting
    caller, which parks in completion.wait() bounded by a reactor timer. No call blocks the
    reactor greenlet. Long timeouts (TIMEOUT_LONG = 1.0 s) occur only during initial
    auto-addressing discovery and now yield the greenlet instead of blocking it.
  - 0x0E MEASURING_WHEEL numeric decode is UNRESOLVED (0xc5-tag+3-byte-BE vs float32-LE
    across captures). measuring_wheel() returns the raw 4-byte word; do not assume a scale.
  - 0x05 CUT_STATE has only the success value (0x00) wire-locked; a failing-cut counter-example
    (RX != 0x00) is still uncaptured, so cut_state() is conservative (True only on a confirmed 0x00).

Resolved in v1.2.0:
  - 0x10 STREAM poll is now settle-based (delta < EXTRUDE_SETTLE_THRESHOLD for
    EXTRUDE_SETTLE_READS consecutive reads, with a path-length timeout), not a fixed count.
"""

import fcntl
import logging
import os
import struct
import termios

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
CMD_EXTRUDE_PROCESS: int = 0x10  # CONFIRMED v1.1.0 - see extrude_process()
CMD_RETRUDE_PROCESS: int = 0x11  # CONFIRMED v1.1.0 - see retrude_process()
CMD_VERSION_INFO: int = 0xF0     # CONFIRMED v1.1.0 - ASCII firmware version string

# Stock box_wrapper.so BoxAction.communication_* methods whose func codes still need a live CFS
# capture to confirm (method names lifted from the .so symbol table; v1.2.0). Left None so a future
# capture just fills the byte. See the project's CFS protocol notes. NOTE: as of v1.2.0 the
# hardware-status, cut-state, measuring-wheel, and motor-action codes were wire-confirmed and
# promoted to the named constants above; the remainder still await a capture.
# CMD_CREATE_CONNECT was a guessed 0x01; the connect / get-addr-table func is 0xA3 on the wire and
# in stock box.py. Aliased to CMD_GET_ADDR_TABLE so no stale 0x01 guess survives.
CMD_CREATE_CONNECT_TODO: int = CMD_GET_ADDR_TABLE  # = 0xA3 (addressing layer), NOT an app connect
CMD_COMMUNICATION_TEST_TODO = None
CMD_GET_BUFFER_STATE_TODO = None
CMD_GET_FILAMENT_SENSOR_STATE_TODO = None
CMD_GET_RFID_TODO = None
CMD_GET_REMAIN_LEN_TODO = None
CMD_EXTRUDE2_PROCESS_TODO = None
CMD_TIGHTEN_UP_ENABLE_TODO = None

# ---------------------------------------------------------------------------
# 0x10 EXTRUDE_PROCESS sub-command constants (confirmed from capture)
# ---------------------------------------------------------------------------
EXTRUDE_SUB_INIT: int = 0x00     # Initialize/start extrusion motor
EXTRUDE_SUB_POLL: int = 0x04     # Poll status (ACK-only response)
EXTRUDE_SUB_STREAM: int = 0x05   # Stream position feedback
EXTRUDE_SUB_SETTLE: int = 0x06   # Settle stage after STREAM converges (WIRE-CONFIRMED 2026-06-19)
EXTRUDE_SUB_FINALIZE: int = 0x07 # Finalize/commit the load (WIRE-CONFIRMED 2026-06-19)

# 0x07 FINALIZE carries a data byte 0x03 on the wire ([slot] 0x07 0x03), the only
# stage whose second byte is non-zero. WIRE-CONFIRMED 2026-06-19 (1001 07 03 / 1002 07 03 /
# 1004 07 03 across the three loads in hi_rs485_3color_print_2026-06-19.json).
EXTRUDE_FINALIZE_DATA: int = 0x03

# 0x10 response motor state byte values
EXTRUDE_STATE_ACCEL: int = 0xC3  # Motor accelerating (wrap-around phase)
EXTRUDE_STATE_SPEED: int = 0xC4  # Motor at speed, position valid

# 0x10 streaming position config
EXTRUDE_POLL_MAX: int = 8               # legacy fixed poll count (retained for reference; the
                                        # STREAM loop is now settle-based, see extrude_process())
EXTRUDE_SETTLE_THRESHOLD: float = 2.0   # mm - position stable within this delta
EXTRUDE_SETTLE_READS: int = 3           # consecutive sub-threshold reads required to call settled
EXTRUDE_TIMEOUT: float = 0.5            # seconds per streaming poll
EXTRUDE_STREAM_TIMEOUT: float = 15.0    # s - wall-clock budget for the STREAM phase; sized to let
                                        # the filament travel the full path (FILAMENT_PATH_LENGTH_MM)
                                        # before the loop gives up even if it never settles
EXTRUDE_MAX_POLLS: int = 64             # hard safety cap on STREAM polls regardless of timeout

# Filament path length reference (confirmed from capture, units: mm)
# Stabilizes at ~398-400mm = physical path from CFS motor to toolhead sensor
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

# Separate buffer/feeder node base address; per-channel feed ops (0x11 retrude, 0x0c buffer)
# go here with a single channel byte (0x01/0x02), not the slot bitmask.
ADDR_BUFFER_NODE: int = 0x81

# ---------------------------------------------------------------------------
# 0x0A GET_BOX_STATE state word (WIRE-CONFIRMED 2026-06-09/06-19)
# RX data = 2-byte state word [hi=0x1a class byte][lo]; lo 0x20=LOADED, 0x1f=FEEDING.
# ---------------------------------------------------------------------------
BOX_STATE_CLASS_BYTE: int = 0x1A   # constant high/class byte of the 0x0A state word
BOX_STATE_LO_LOADED: int = 0x20    # lo byte: filament loaded / idle-loaded
BOX_STATE_LO_FEEDING: int = 0x1F   # lo byte: feeding

# ---------------------------------------------------------------------------
# 0x08 GET_HARDWARE_STATUS flags (WIRE-CONFIRMED 2026-06-09)
# TX data = [channel]; RX = 1 status flag byte.
# ---------------------------------------------------------------------------
HW_STATUS_CLEAR: int = 0x00    # sensor clear / no filament
HW_STATUS_BUSY: int = 0x01     # busy / feeding (0x01/0x02/0x04 seen busy)
HW_STATUS_READY: int = 0x07    # ready flags

# 0x05 CUT_STATE RX byte (WIRE-CONFIRMED 2026-06-09): 0x00 = cut done/clear, 0x01 = cut-state set.
CUT_STATE_DONE: int = 0x00
CUT_STATE_SET: int = 0x01

# 0x0F CTRL_CONNECTION_MOTOR_ACTION TX byte (WIRE-CONFIRMED 2026-06-09).
MOTOR_ACTION_RELEASE: int = 0x00
MOTOR_ACTION_ENGAGE: int = 0x01

# 0x11 RETRUDE_PROCESS phase byte (WIRE-CONFIRMED 2026-06-09 on addr 0x01).
RETRUDE_PHASE_START: int = 0x00
RETRUDE_PHASE_RUNNING: int = 0x01

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
    CMD_EXTRUDE_PROCESS: EXTRUDE_TIMEOUT,
    CMD_RETRUDE_PROCESS: EXTRUDE_TIMEOUT,
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
        # Map the requested baud to a termios B-constant for the raw tty config.
        self._baud_const = getattr(termios, "B%d" % self.baud, None)
        if self._baud_const is None:
            raise config.error(
                "creality_cfs: unsupported baud %d (no termios B%d)" % (self.baud, self.baud)
            )

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
        during the klippy:ready event dispatch phase.
        """
        try:
            self._run_auto_addressing()
        except Exception as exc:
            logger.error("creality_cfs: auto-init failed: %s", exc)

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
        """
        fd = os.open(self.serial_port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
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

    def _config_tty(self, fd: int) -> None:
        """Put the tty in raw 8N1 mode at the configured baud (VMIN=0/VTIME=0, non-blocking)."""
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
                # Fresh completion per attempt; register it as the pending matcher BEFORE the
                # write so a fast reply cannot race ahead of us.
                comp = self.reactor.completion()
                self._pending = comp
                self._pending_match = match
                try:
                    os.write(self._fd, msg)
                except OSError as exc:
                    logger.error("creality_cfs: write error on attempt %d: %s",
                                 attempt + 1, exc)
                    self._pending = None
                    self._pending_match = None
                    break

                # Park the caller (yields the greenlet) until the fd callback completes us with
                # a frame, or the reactor timer wakes us with None at the deadline.
                raw = comp.wait(self.reactor.monotonic() + timeout, None)
                self._pending = None
                self._pending_match = None

                if self._shutdown:
                    return None
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

    # Box state word lo-byte constants, WIRE-CONFIRMED 2026-06-09/06-19.
    # The 0x0A response payload is a 2-byte state word [hi=0x1a class byte][lo].
    BOX_STATE_LOADED: int = BOX_STATE_LO_LOADED    # lo 0x20: filament loaded / idle-loaded
    BOX_STATE_FEEDING: int = BOX_STATE_LO_FEEDING  # lo 0x1f: feeding

    def get_box_state(self, addr: int, param: int = 0x00) -> dict:
        """Query the operating state of a single CFS box.

        Command: CMD_GET_BOX_STATE (0x0A), STATUS=0xFF, 1-byte param.
        Response: the 0x0A state word.

        WIRE-CONFIRMED 2026-06-09 and re-confirmed 2026-06-19 (186 polls):
          REQ: f7 [addr] 04 ff 0a [param] [crc]
          RSP: f7 [addr] .. 00 0a [hi=0x1a][lo] .. [crc]   (e.g. f70107000a1a200100 63)
        The meaningful field is the lo byte (data[1]):
          0x20 = LOADED   : filament loaded / idle-loaded
          0x1f = FEEDING  : feeding
        data[0] is the constant 0x1a class byte.

        NOTE (v1.2.0): the func code was corrected from 0x08 to 0x0A and the decode model from a
        single 0x0f/0x00/0x02 flag to this 2-byte word. 0x08 is a SEPARATE command,
        GET_HARDWARE_STATUS (see get_hardware_status()).

        Args:
            addr: Box address (normally 0x01 on the Hi; the single 4-slot controller).
            param: Request parameter byte (0x00=standard poll).

        Returns:
            dict with keys:
                state (int): Decoded lo byte (0x20=loaded, 0x1f=feeding).
                state_str (str): Human-readable state name.
                class_byte (int): The 0x1a class/high byte as received (or 0xFF if absent).
                addr (int): Address that responded.
                raw (bytes): Raw response data bytes.

        Raises:
            RuntimeError: If no valid response received after retries.
        """
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_GET_BOX_STATE,
            data=bytes([param]),
        )
        if resp is None:
            raise RuntimeError(f"No response from box 0x{addr:02X} for GET_BOX_STATE")

        # The 0x0A payload is a 2-byte word [hi=0x1a class byte][lo state byte].
        data_bytes = resp.get("data", b"")
        if len(data_bytes) >= 2:
            class_byte = data_bytes[0]
            state = data_bytes[1]
        elif len(data_bytes) == 1:
            # Degenerate frame: treat the single byte as the lo state byte.
            class_byte = 0xFF
            state = data_bytes[0]
        else:
            class_byte = 0xFF
            state = 0xFF

        state_str = {
            self.BOX_STATE_LOADED:  "LOADED",
            self.BOX_STATE_FEEDING: "FEEDING",
        }.get(state, f"UNKNOWN(0x{state:02X})")

        logger.info(
            "creality_cfs: GET_BOX_STATE addr=0x%02X word=0x%02X%02X state=0x%02X (%s)",
            addr, class_byte, state, state, state_str,
        )
        return {
            "state": state,
            "state_str": state_str,
            "class_byte": class_byte,
            "addr": addr,
            "raw": data_bytes,
        }

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
        return resp_status == STATUS_ADDRESSING  # ACK uses STATUS=0x00

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

    def set_pre_loading(self, addr: int, slot_mask: int, enable: int) -> bool:
        """Configure pre-loading for specified filament slots.

        Command: CMD_SET_PRE_LOADING (0x0D), STATUS=0xFF, payload=[slot_mask][enable].
        TODO: Confirm exact slot_mask bit layout with hardware test.

        Args:
            addr: Box address (0x01-0x04).
            slot_mask: Bitmask of slots to configure (e.g. 0x0F for all 4 slots).
            enable: 0x00 to disable, 0x01 to enable pre-loading.

        Returns:
            bool: True if acknowledged.
        """
        if not (ADDR_BOX_MIN <= addr <= ADDR_BOX_MAX):
            raise ValueError(f"addr 0x{addr:02X} out of range")
        if not (0x00 <= slot_mask <= 0xFF):
            raise ValueError("slot_mask must be a single byte")
        if enable not in (0x00, 0x01):
            raise ValueError("enable must be 0 or 1")

        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_SET_PRE_LOADING,
            data=bytes([slot_mask, enable]),
        )
        if resp is None:
            logger.warning("creality_cfs: SET_PRE_LOADING addr=0x%02X, no response", addr)
            return False

        logger.info(
            "creality_cfs: SET_PRE_LOADING addr=0x%02X mask=0x%02X enable=%d",
            addr, slot_mask, enable,
        )
        return True

    def extrude_process(self, addr: int, slot: int = SLOT_T1) -> dict:
        """CMD_EXTRUDE_PROCESS (0x10): drive CFS filament motor to load filament.

        Confirmed from live RS485 capture during T0->T1->T2->T3 tool-change on
        Creality Hi, re-confirmed 2026-06-09/06-19 (see cfs_func_code_map_2026-06-09.md).

        Protocol sequence. Each TX payload is [slot_bitmask][stage_hi][stage_lo]:
          1. INIT     ([slot] 0x00 0x00): Start extrusion motor. Response: 1-byte status.
          2. POLL     ([slot] 0x04 0x00): Poll ready status. Response: ACK only.
          3. STREAM   ([slot] 0x05 0x00): Stream position feedback, polled until the
             position settles. Response: [state(1B)][pos_hi(1B)][pos_lo(1B)] where:
               state 0xC3 = motor accelerating (wrap-around phase, pos not valid)
               state 0xC4 = motor at speed, position valid
               pos = uint16 big-endian, units 0.01mm (divide by 100 for mm)
             Position climbs from ~149mm -> ~338mm -> ~398-400mm as filament
             travels from CFS motor through buffer/Bowden to toolhead sensor.
          4. SETTLE   ([slot] 0x06 0x00): Settle stage after STREAM converges.
          5. FINALIZE ([slot] 0x07 0x03): Commit/finalize the load. The second byte is
             0x03 (EXTRUDE_FINALIZE_DATA), the only non-zero stage_lo on the wire.
        Stages 4-5 (WIRE-CONFIRMED 2026-06-19, hi_rs485_3color_print_2026-06-19.json:
        1001 06 00 / 1001 07 03, and likewise for 0x02 / 0x04) complete the ramp the way
        stock does; without them a real load never finishes.

        NOTE (v1.2.0): the leading payload byte is the 1-hot SLOT bitmask (was hardcoded
        0x02 = T1, so every call was slot-locked to tool 1). The STREAM loop is now
        settle-based instead of a fixed EXTRUDE_POLL_MAX count: it stops once the reported
        position changes by less than EXTRUDE_SETTLE_THRESHOLD for EXTRUDE_SETTLE_READS
        consecutive valid reads, or when a wall-clock timeout sized to the filament path
        length elapses.
        NOTE (v1.2.1): the SETTLE (0x06) and FINALIZE (0x07 0x03) stages are now issued
        after the STREAM loop, completing the 0000/0400/0500/0600/0703 ramp.

        Args:
            addr: Box address (normally ADDR_BUFFER_NODE's box, i.e. 0x01 on the Hi).
            slot: 1-hot slot/tool bitmask (SLOT_T0..SLOT_T3 = 0x01/0x02/0x04/0x08).

        Returns:
            dict with keys:
              'init_ok'    (bool): True if INIT sub-command was acknowledged.
              'final_pos'  (float): Last reported filament position in mm, or 0.0.
              'final_state' (int): Last reported motor state byte (0xC3 or 0xC4).
              'polls'      (int): Number of STREAM polls that received a response.
              'settled'    (bool): True if the position settled before timeout.
              'settle_ok'  (bool): True if the SETTLE (0x06) stage was acknowledged.
              'finalize_ok' (bool): True if the FINALIZE (0x07 0x03) stage was acknowledged.
              'complete'   (bool): True if both SETTLE and FINALIZE were acknowledged.
        """
        if not (ADDR_BOX_MIN <= addr <= ADDR_BOX_MAX):
            raise ValueError(f"addr 0x{addr:02X} out of range")
        if slot not in SLOT_BITMASKS:
            raise ValueError(f"slot 0x{slot:02X} is not a 1-hot bitmask in {SLOT_BITMASKS}")

        result = {
            'init_ok': False,
            'final_pos': 0.0,
            'final_state': 0x00,
            'polls': 0,
            'settled': False,
            'settle_ok': False,
            'finalize_ok': False,
            'complete': False,
        }

        # Step 1, INIT: start extrusion motor for this slot
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_EXTRUDE_PROCESS,
            data=bytes([slot, EXTRUDE_SUB_INIT, 0x00]),
            timeout=EXTRUDE_TIMEOUT,
        )
        if resp is None:
            logger.warning(
                "creality_cfs: EXTRUDE_PROCESS INIT addr=0x%02X slot=0x%02X, no response",
                addr, slot,
            )
            return result

        init_data = resp.get("data", b"")
        result['init_ok'] = (len(init_data) >= 1 and init_data[0] == 0x00)
        logger.info(
            "creality_cfs: EXTRUDE_PROCESS INIT addr=0x%02X slot=0x%02X status=0x%02X init_ok=%s",
            addr, slot, init_data[0] if init_data else 0xFF, result['init_ok'],
        )

        # Step 2, POLL: status check (ACK only)
        self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_EXTRUDE_PROCESS,
            data=bytes([slot, EXTRUDE_SUB_POLL, 0x00]),
            timeout=EXTRUDE_TIMEOUT,
        )

        # Step 3, STREAM: poll position feedback until it settles or a path-length
        # timeout elapses. The fixed EXTRUDE_POLL_MAX count was replaced (v1.2.0) because
        # 8 polls could finalize before the filament reached the toolhead (~398-400mm).
        # Wall-clock budget: time to travel FILAMENT_PATH_LENGTH_MM at the observed feed
        # rate, with EXTRUDE_TIMEOUT per poll as the floor. We bound the loop both by the
        # settle condition and by EXTRUDE_MAX_POLLS as a hard safety cap.
        stable_reads = 0
        prev_pos = None
        # Use the reactor clock (not time.time): _send_command yields the greenlet, so the
        # wall-clock budget must be measured on the same monotonic source the reactor uses.
        deadline = self.reactor.monotonic() + EXTRUDE_STREAM_TIMEOUT
        poll_num = 0
        while self.reactor.monotonic() < deadline and poll_num < EXTRUDE_MAX_POLLS:
            stream_resp = self._send_command(
                addr,
                STATUS_OPERATIONAL,
                CMD_EXTRUDE_PROCESS,
                data=bytes([slot, EXTRUDE_SUB_STREAM, 0x00]),
                timeout=EXTRUDE_TIMEOUT,
            )
            poll_num += 1
            if stream_resp is None:
                logger.debug(
                    "creality_cfs: EXTRUDE_PROCESS STREAM addr=0x%02X slot=0x%02X poll=%d, no response",
                    addr, slot, poll_num,
                )
                continue

            stream_data = stream_resp.get("data", b"")
            if len(stream_data) < 3:
                logger.debug(
                    "creality_cfs: EXTRUDE_PROCESS STREAM addr=0x%02X slot=0x%02X poll=%d, "
                    "short response %d bytes", addr, slot, poll_num, len(stream_data),
                )
                continue

            motor_state = stream_data[0]
            pos_raw = (stream_data[1] << 8) | stream_data[2]
            pos_mm = pos_raw / 100.0
            result['polls'] += 1
            result['final_state'] = motor_state
            result['final_pos'] = pos_mm

            state_str = (
                "ACCEL" if motor_state == EXTRUDE_STATE_ACCEL
                else "SPEED" if motor_state == EXTRUDE_STATE_SPEED
                else f"0x{motor_state:02X}"
            )
            logger.info(
                "creality_cfs: EXTRUDE_PROCESS STREAM addr=0x%02X slot=0x%02X poll=%d state=%s pos=%.2fmm",
                addr, slot, poll_num, state_str, pos_mm,
            )

            # Settle detection: only count position-valid reads (state 0xC4) where the
            # delta is below threshold. ACCEL (0xC3) reads carry an invalid wrap-around
            # position and reset the settle counter.
            if motor_state == EXTRUDE_STATE_SPEED and prev_pos is not None:
                if abs(pos_mm - prev_pos) < EXTRUDE_SETTLE_THRESHOLD:
                    stable_reads += 1
                    if stable_reads >= EXTRUDE_SETTLE_READS:
                        result['settled'] = True
                        logger.info(
                            "creality_cfs: EXTRUDE_PROCESS settled addr=0x%02X slot=0x%02X "
                            "at %.2fmm after %d stable reads",
                            addr, slot, pos_mm, stable_reads,
                        )
                        break
                else:
                    stable_reads = 0
            else:
                stable_reads = 0
            prev_pos = pos_mm

            # Interleave POLL between STREAM calls (matches observed Creality sequence)
            if poll_num % 4 == 0:
                self._send_command(
                    addr,
                    STATUS_OPERATIONAL,
                    CMD_EXTRUDE_PROCESS,
                    data=bytes([slot, EXTRUDE_SUB_POLL, 0x00]),
                    timeout=EXTRUDE_TIMEOUT,
                )

        # Step 4, SETTLE ([slot] 0x06 0x00): issued after the STREAM loop converges (or
        # times out), per the wire ramp 0000/0400/0500/0600/0703. WIRE-CONFIRMED 2026-06-19.
        # NOTE (provisional ACK): settle_ok/finalize_ok/complete below treat ANY framed reply
        # as success (resp is not None). The wire only ever showed a success reply for these
        # stages, so a per-stage failing status byte is not yet known. Until a failing-stage
        # counter-example is captured, do NOT tighten this to a status-byte check; over-
        # constraining it could reject a valid load. Revisit once such a capture exists.
        settle_resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_EXTRUDE_PROCESS,
            data=bytes([slot, EXTRUDE_SUB_SETTLE, 0x00]),
            timeout=EXTRUDE_TIMEOUT,
        )
        result['settle_ok'] = settle_resp is not None
        if not result['settle_ok']:
            logger.warning(
                "creality_cfs: EXTRUDE_PROCESS SETTLE addr=0x%02X slot=0x%02X, no response",
                addr, slot,
            )
        else:
            logger.info(
                "creality_cfs: EXTRUDE_PROCESS SETTLE addr=0x%02X slot=0x%02X acknowledged",
                addr, slot,
            )

        # Step 5, FINALIZE ([slot] 0x07 0x03): commit the load. The second byte is the
        # non-zero EXTRUDE_FINALIZE_DATA (0x03). WIRE-CONFIRMED 2026-06-19.
        finalize_resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_EXTRUDE_PROCESS,
            data=bytes([slot, EXTRUDE_SUB_FINALIZE, EXTRUDE_FINALIZE_DATA]),
            timeout=EXTRUDE_TIMEOUT,
        )
        result['finalize_ok'] = finalize_resp is not None
        if not result['finalize_ok']:
            logger.warning(
                "creality_cfs: EXTRUDE_PROCESS FINALIZE addr=0x%02X slot=0x%02X, no response",
                addr, slot,
            )
        else:
            logger.info(
                "creality_cfs: EXTRUDE_PROCESS FINALIZE addr=0x%02X slot=0x%02X acknowledged",
                addr, slot,
            )

        result['complete'] = result['settle_ok'] and result['finalize_ok']

        logger.info(
            "creality_cfs: EXTRUDE_PROCESS complete addr=0x%02X slot=0x%02X final_pos=%.2fmm "
            "polls=%d settled=%s settle_ok=%s finalize_ok=%s complete=%s",
            addr, slot, result['final_pos'], result['polls'], result['settled'],
            result['settle_ok'], result['finalize_ok'], result['complete'],
        )
        return result

    def retrude_process(self, addr: int, slot: int = SLOT_T1) -> bool:
        """CMD_RETRUDE_PROCESS (0x11): retract filament back into CFS box.

        WIRE-CONFIRMED 2026-06-09 (cfs_func_code_map_2026-06-09.md), re-confirmed 2026-06-19.

        On the box controller (addr 0x01) the payload is [slot_bitmask][phase], where
        phase 0x00 = start and phase 0x01 = running. The host issues phase 0x00 then 0x01.
        On the buffer/feeder node (ADDR_BUFFER_NODE = 0x81) the payload is a SINGLE channel
        byte (0x01/0x02); this method picks the form by address.

        Protocol (box controller, addr 0x01):
          REQ start:   f7 01 05 ff 11 [slot] 00 [crc]
          REQ running: f7 01 05 ff 11 [slot] 01 [crc]
          RSP:         f7 01 03 00 11 [crc]  (ACK only, no payload)
        Protocol (buffer node, addr 0x81), e.g. f781040011011d / f7810400110214:
          REQ:         f7 81 04 00 11 [channel] [crc]

        NOTE (v1.2.0): the previous payload [0x02, 0x01] was slot-locked to T1 and the docstring
        inverted the byte meaning (it read it as [sub, slot]). The wire layout is [slot, phase],
        and phase 0x00 was being skipped entirely.

        Args:
            addr: Box address. ADDR_BOX_MIN..ADDR_BOX_MAX = box controller (slot+phase form);
                  ADDR_BUFFER_NODE = buffer/feeder node (single channel-byte form).
            slot: On the box controller, the 1-hot slot bitmask (SLOT_T0..SLOT_T3). On the
                  buffer node it is used directly as the single channel byte (0x01/0x02).

        Returns:
            bool: True if the CFS acknowledged the retract command (both phases on the box
                  controller; the single frame on the buffer node).
        """
        # Buffer/feeder node form: a single channel byte, one frame, no phase.
        if addr == ADDR_BUFFER_NODE:
            resp = self._send_command(
                addr,
                STATUS_ADDRESSING,  # buffer-node frames use status 0x00 on the wire
                CMD_RETRUDE_PROCESS,
                data=bytes([slot]),
                timeout=EXTRUDE_TIMEOUT,
            )
            if resp is None:
                logger.warning(
                    "creality_cfs: RETRUDE_PROCESS buffer addr=0x%02X ch=0x%02X, no response",
                    addr, slot,
                )
                return False
            logger.info(
                "creality_cfs: RETRUDE_PROCESS buffer addr=0x%02X ch=0x%02X acknowledged",
                addr, slot,
            )
            return True

        # Box controller form: [slot, phase], phase 0x00 (start) then 0x01 (running).
        if not (ADDR_BOX_MIN <= addr <= ADDR_BOX_MAX):
            raise ValueError(f"addr 0x{addr:02X} out of range")
        if slot not in SLOT_BITMASKS:
            raise ValueError(f"slot 0x{slot:02X} is not a 1-hot bitmask in {SLOT_BITMASKS}")

        acked = True
        for phase in (RETRUDE_PHASE_START, RETRUDE_PHASE_RUNNING):
            resp = self._send_command(
                addr,
                STATUS_OPERATIONAL,
                CMD_RETRUDE_PROCESS,
                data=bytes([slot, phase]),
                timeout=EXTRUDE_TIMEOUT,
            )
            if resp is None:
                logger.warning(
                    "creality_cfs: RETRUDE_PROCESS addr=0x%02X slot=0x%02X phase=0x%02X, no response",
                    addr, slot, phase,
                )
                acked = False
                # The start phase is a prerequisite for running; abort the sequence if it fails.
                break
            logger.info(
                "creality_cfs: RETRUDE_PROCESS addr=0x%02X slot=0x%02X phase=0x%02X acknowledged",
                addr, slot, phase,
            )
        return acked

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

    def cut_state(self, addr: int) -> bool:
        """CMD_CUT_STATE (0x05): read the cut-state AFTER the mechanical cut.

        WIRE-CONFIRMED 2026-06-09. The physical cut is MECHANICAL (the toolhead rams the
        cutter); there is no dedicated cut func. This command only READS the cut-state that the
        controller latches after the mechanical cut.

        Protocol:
          REQ: f7 [addr] 03 ff 05 [crc]   (no data)
          RSP: f7 [addr] 04 00 05 [state] [crc]
        State byte: 0x00 = cut done / clear; 0x01 = cut-state set.

        Returns True only if the read returned 0x00 (cut done). Conservative on a None or
        non-zero response, because a FAILING-cut counter-example (RX != 0x00) is still
        uncaptured, so anything that is not a confirmed 0x00 is treated as not-done.

        Args:
            addr: Box address (normally 0x01 on the Hi).

        Returns:
            bool: True iff the response state byte == 0x00 (cut done / clear).
        """
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_CUT_STATE,
            data=b"",
        )
        if resp is None:
            logger.warning("creality_cfs: CUT_STATE addr=0x%02X, no response", addr)
            return False

        data_bytes = resp.get("data", b"")
        state = data_bytes[0] if len(data_bytes) >= 1 else 0xFF
        done = (state == CUT_STATE_DONE)
        logger.info(
            "creality_cfs: CUT_STATE addr=0x%02X state=0x%02X done=%s", addr, state, done
        )
        return done

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
        """CMD_MEASURING_WHEEL (0x0E): read the feed encoder / measuring-wheel word.

        WIRE-CONFIRMED 2026-06-09 (func code and 4-byte response shape); read ~6x during a
        load to verify feed progress.

        Protocol:
          REQ: f7 [addr] 04 ff 0e 01 [crc]   (data = [0x01])
          RSP: f7 [addr] .. 00 0e [4 bytes] [crc]

        OPEN QUESTION (numeric decode UNRESOLVED): the 2026-06-09 capture read the 4-byte RX as
        a constant 0xc5 tag followed by a 3-byte big-endian accumulator; the 2026-06-19 re-capture
        saw the lead byte be 0xc4 as often as 0xc5 and the high byte vary (e.g. c4aec547,
        c4ad8ebe), which fits a float32-LE encoder value instead. Same on-wire bytes, two
        candidate decodes. This method therefore returns the RAW 4 bytes and does NOT apply any
        scale. Resolve the encoding with a controlled feed of a known length before any host
        acts on a wheel magnitude.  TODO(v1.2.x): decode once the encoding is pinned.

        Args:
            addr: Box address (normally 0x01 on the Hi).

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
        # Return the raw word untouched; numeric decode is an open TODO (see docstring).
        logger.info(
            "creality_cfs: MEASURING_WHEEL addr=0x%02X raw=%s (decode TODO)",
            addr, data_bytes.hex() if data_bytes else "(none)",
        )
        return data_bytes

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
                state_info = self.get_box_state(addr)
                raw_hex = state_info["raw"].hex() if state_info["raw"] else "?"
                results.append(
                    f"Box {addr} (0x{addr:02X}): state=0x{state_info['state']:02X} raw={raw_hex}"
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
        "Parameters: BOX=<1-4> MASK=<0-255> ENABLE=<0|1>"
    )

    def cmd_CFS_SET_PRELOAD(self, gcmd) -> None:
        """G-code: CFS_SET_PRELOAD BOX=<1-4> MASK=<0-255> ENABLE=<0|1>.

        Usage: CFS_SET_PRELOAD BOX=1 MASK=15 ENABLE=1   # enable all 4 slots
               CFS_SET_PRELOAD BOX=1 MASK=1 ENABLE=0    # disable slot 0
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        addr = gcmd.get_int("BOX", minval=1, maxval=4)
        mask = gcmd.get_int("MASK", minval=0, maxval=255)
        enable = gcmd.get_int("ENABLE", minval=0, maxval=1)

        try:
            ok = self.set_pre_loading(addr, mask, enable)
            if ok:
                gcmd.respond_info(
                    f"CFS box {addr}: pre-loading {'enabled' if enable else 'disabled'} "
                    f"for slot mask 0x{mask:02X}"
                )
            else:
                gcmd.respond_info(f"CFS box {addr}: SET_PRE_LOADING sent (no ACK)")
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
        "Drive CFS filament motor to load filament into toolhead. "
        "Parameters: BOX=<1-4> [TOOL=<0-3>]"
    )

    def cmd_CFS_EXTRUDE(self, gcmd) -> None:
        """G-code: CFS_EXTRUDE BOX=<1-4> [TOOL=<0-3>] -- run extrude_process sequence.

        Drives the CFS filament motor for the specified box/tool through the full
        init/poll/stream sequence. Reports final position and motor state.

        TOOL selects the slot (0=T0..3=T3); defaults to T1 to match prior behavior.

        Usage: CFS_EXTRUDE BOX=1 TOOL=2
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        addr = gcmd.get_int("BOX", minval=1, maxval=4)
        tool = gcmd.get_int("TOOL", 1, minval=0, maxval=3)
        slot = SLOT_BITMASKS[tool]
        try:
            result = self.extrude_process(addr, slot=slot)
            gcmd.respond_info(
                f"CFS box {addr} tool T{tool} EXTRUDE: init_ok={result['init_ok']} "
                f"final_pos={result['final_pos']:.2f}mm "
                f"state=0x{result['final_state']:02X} "
                f"polls={result['polls']} settled={result['settled']} "
                f"complete={result['complete']}"
            )
        except Exception as exc:
            raise gcmd.error(f"CFS_EXTRUDE failed: {exc}")

    cmd_CFS_RETRUDE_help: str = (
        "Retract filament back into CFS box. Parameters: BOX=<1-4> [TOOL=<0-3>]"
    )

    def cmd_CFS_RETRUDE(self, gcmd) -> None:
        """G-code: CFS_RETRUDE BOX=<1-4> [TOOL=<0-3>] -- run retrude_process.

        Sends the two-phase retract command (start then running) to the specified
        CFS box/tool. TOOL selects the slot (0=T0..3=T3); defaults to T1.

        Usage: CFS_RETRUDE BOX=1 TOOL=0
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        addr = gcmd.get_int("BOX", minval=1, maxval=4)
        tool = gcmd.get_int("TOOL", 1, minval=0, maxval=3)
        slot = SLOT_BITMASKS[tool]
        try:
            ok = self.retrude_process(addr, slot=slot)
            gcmd.respond_info(
                f"CFS box {addr} tool T{tool} RETRUDE: {'acknowledged' if ok else 'no response'}"
            )
        except Exception as exc:
            raise gcmd.error(f"CFS_RETRUDE failed: {exc}")

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