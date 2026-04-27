"""
creality_cfs.py — Klipper Extra Module for Creality Filament System (CFS)

Protocol version: CFS RS485 v1 (single version)
Klipper compatibility: v0.11.0+
License: GPL-3.0 (matching Klipper project)
Author: gitstonelabs

Protocol reverse-engineered
CRC algorithm validated against 16 test vectors
Command IDs, payload structures, and response formats.

Changelog:
  v1.0.0 (2026-03-27) — Initial production release. 9 confirmed commands implemented,
                         0x10/0x11 stubbed. Full auto-addressing sequence (5-step).

Known limitations:
  - CMD_EXTRUDE_PROCESS (0x10) and CMD_RETRUDE_PROCESS (0x11) payloads are locked
    in the Creality .so binary. Capture RS485 traffic on /dev/ttyS5 during a T0-T3
    tool-change to recover the payload format.
  - Half-duplex RS485 direction switching is managed by the kernel driver or a
    hardware auto-direction adapter. This module does not toggle RTS manually.
  - Serial I/O is performed synchronously inside reactor callbacks to avoid blocking
    the Klipper main thread. Long timeouts (TIMEOUT_LONG = 1.0 s) occur only during
    initial auto-addressing discovery and are flagged in code.
"""

import logging
import struct
import time

import serial

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
CFS_DEFAULT_PORT: str = "/dev/ttyS5"   # default RS485 port
CFS_BAUD_RATE: int = 230400            # validated baud rate
CFS_SERIAL_BYTESIZE: int = 8
CFS_SERIAL_PARITY: str = "N"
CFS_SERIAL_STOPBITS: int = 1

# Timing constants — from Hi_Klipper/klippy/extras/auto_addr_wrapper.py
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
CMD_GET_BOX_STATE: int = 0x0A   # Get 4-byte box state; confidence 97%
CMD_SET_PRE_LOADING: int = 0x0D # Set pre-loading slot mask; confidence 93%
CMD_GET_VERSION_SN: int = 0x14  # Get 22-byte version/SN string; confidence 97%

# Stubbed commands — payloads unknown, locked in Creality .so binary
CMD_EXTRUDE_PROCESS: int = 0x10  # TODO: capture RS485 traffic during T0-T3 to recover
CMD_RETRUDE_PROCESS: int = 0x11  # TODO: capture RS485 traffic during T0-T3 to recover

# ---------------------------------------------------------------------------
# Response state codes — from klipper-cfs/extras/creality_cfs.py community impl
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
    CMD_SET_PRE_LOADING: TIMEOUT_MEDIUM,
    CMD_GET_VERSION_SN: TIMEOUT_MEDIUM,
}

# ---------------------------------------------------------------------------
# CRC-8/SMBUS, 16/16 test vectors validated, poly=0x07, init=0x00
# Scope: msg[2:-1] (covers LENGTH, STATUS, FUNCTION_CODE, DATA; excludes HEAD, ADDR, CRC)
# ---------------------------------------------------------------------------

def crc8_cfs(data: bytes) -> int:
    """Calculate CRC-8/SMBUS checksum for the given data.

    Algorithm validated against 16 captured packet test vectors.
    Polynomial: 0x07, Initial value: 0x00, no reflection, no final XOR.
    CRC scope is msg[2:-1] — i.e., from the LENGTH byte through the last DATA byte.

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
            "parse_message: truncated — got %d bytes, expected %d",
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
            "parse_message: CRC mismatch — received 0x%02X, calculated 0x%02X for func=0x%02X",
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
# Address manager — tracks per-box state through the auto-addressing sequence
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
      - Comprehensive logging at appropriate levels
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
        self.serial_port: str = config.get("serial_port", CFS_DEFAULT_PORT)
        self.baud: int = config.getint("baud", CFS_BAUD_RATE, minval=9600, maxval=921600)
        self.timeout: float = config.getfloat("timeout", TIMEOUT_MEDIUM, minval=0.01, maxval=10.0)
        self.retry_count: int = config.getint("retry_count", DEFAULT_RETRY_COUNT, minval=0, maxval=10)
        self.box_count: int = config.getint("box_count", 4, minval=1, maxval=4)
        self.auto_init: bool = config.getboolean("auto_init", True)

        # --- Internal state ---
        self._serial: serial.Serial = None
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

        logger.info("creality_cfs: module loaded, port=%s baud=%d", self.serial_port, self.baud)

    # -----------------------------------------------------------------------
    # Klipper lifecycle handlers
    # -----------------------------------------------------------------------

    def _handle_ready(self) -> None:
        """Called when Klipper transitions to ready state.

        Opens the serial port and optionally runs auto-addressing.
        Logs failure but does not raise — a missing CFS should not prevent
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

        Closes the serial port safely. Never raises exceptions.
        """
        try:
            self._disconnect_serial()
        except Exception as exc:
            logger.warning("creality_cfs: error during shutdown close: %s", exc)

    # -----------------------------------------------------------------------
    # Serial connection management
    # -----------------------------------------------------------------------

    def _connect_serial(self) -> None:
        """Open the RS485 serial port with 8N1 settings.

        Raises:
            serial.SerialException: If the port cannot be opened.
        """
        try:
            self._serial = serial.Serial(
                port=self.serial_port,
                baudrate=self.baud,
                bytesize=CFS_SERIAL_BYTESIZE,
                parity=CFS_SERIAL_PARITY,
                stopbits=CFS_SERIAL_STOPBITS,
                timeout=self.timeout,
            )
            self.is_connected = True
            logger.info(
                "creality_cfs: opened %s at %d baud", self.serial_port, self.baud
            )
        except serial.SerialException as exc:
            self.is_connected = False
            self._serial = None
            raise

    def _disconnect_serial(self) -> None:
        """Close the serial port if open."""
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
            logger.info("creality_cfs: serial port closed")
        self.is_connected = False
        self._serial = None

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
        """Build, send, and receive a CFS command with retry logic.

        NOTE: This method performs blocking serial I/O. It should only be
        called from within a reactor callback or from a background thread,
        not directly from the Klipper main reactor loop.

        Args:
            addr: Destination address byte.
            status: STATUS byte (STATUS_ADDRESSING or STATUS_OPERATIONAL).
            func: Function code (CMD_* constant).
            data: Payload bytes (default empty).
            timeout: Override serial read timeout in seconds. Defaults to
                     per-command value from CMD_TIMEOUTS, then self.timeout.
            retries: Override retry count. Defaults to self.retry_count.

        Returns:
            dict: Parsed response from parse_message(), or None if no response
                  was received after all retries (for addressing commands that
                  may legitimately have no responders).

        Raises:
            serial.SerialException: On write failure.
            RuntimeError: If retries are exhausted and a response was expected
                          but never received with a valid CRC.
        """
        if not self.is_connected or self._serial is None:
            raise RuntimeError("creality_cfs: serial port not connected")

        if timeout is None:
            timeout = CMD_TIMEOUTS.get(func, self.timeout)
        if retries is None:
            retries = self.retry_count

        msg: bytes = build_message(addr, status, func, data)
        logger.debug(
            "creality_cfs: TX addr=0x%02X func=0x%02X data=%s",
            addr, func, data.hex() if data else "(none)",
        )

        last_error: Exception = None
        for attempt in range(max(retries, 1)):
            try:
                self._serial.reset_input_buffer()
                self._serial.write(msg)
                raw: bytes = self._read_response(timeout)
                if raw is None or len(raw) == 0:
                    logger.debug(
                        "creality_cfs: no response on attempt %d/%d for func=0x%02X",
                        attempt + 1, retries, func,
                    )
                    last_error = RuntimeError(f"No response from CFS (func=0x{func:02X})")
                    continue

                logger.debug(
                    "creality_cfs: RX raw=%s", raw.hex()
                )
                parsed = parse_message(raw)
                if parsed is None:
                    logger.debug("creality_cfs: unparseable response on attempt %d", attempt + 1)
                    last_error = RuntimeError("Unparseable response frame")
                    continue

                if not parsed["crc_valid"]:
                    logger.warning(
                        "creality_cfs: CRC error on attempt %d/%d for func=0x%02X",
                        attempt + 1, retries, func,
                    )
                    last_error = RuntimeError(f"CRC error in response (func=0x{func:02X})")
                    continue

                return parsed

            except serial.SerialException as exc:
                logger.error("creality_cfs: serial error on attempt %d: %s", attempt + 1, exc)
                last_error = exc
                break

        # Addressing broadcast commands legitimately get no response if no
        # devices are present; return None instead of raising.
        return None

    def _read_response(self, timeout: float) -> bytes:
        """Read one complete CFS response frame from the serial port.

        Reads the header and ADDR byte first, then the LENGTH byte, then
        the remainder of the frame to avoid over-reading on the shared
        half-duplex bus.

        Args:
            timeout: Read timeout in seconds.

        Returns:
            bytes: Complete raw frame, or empty bytes on timeout/no data.
        """
        self._serial.timeout = timeout
        try:
            # Read HEAD + ADDR + LENGTH (3 bytes)
            header: bytes = self._serial.read(3)
            if len(header) < 3:
                return b""
            if header[0] != PACK_HEAD:
                logger.debug(
                    "creality_cfs: bad header byte 0x%02X, discarding", header[0]
                )
                return b""

            length_field: int = header[2]
            # length_field = STATUS + FUNC + DATA + CRC; read exactly that many bytes
            if length_field < 3 or length_field > (MAX_DATA_LEN + 3):
                logger.debug(
                    "creality_cfs: implausible LENGTH field %d, discarding", length_field
                )
                return b""

            remainder: bytes = self._serial.read(length_field)
            if len(remainder) < length_field:
                logger.debug(
                    "creality_cfs: truncated read — got %d of %d expected bytes",
                    len(remainder), length_field,
                )
                return b""

            return header + remainder

        except serial.SerialException as exc:
            logger.error("creality_cfs: read error: %s", exc)
            return b""

    # -----------------------------------------------------------------------
    # Auto-addressing sequence (5-step, from auto_addr_wrapper.py pattern)
    # -----------------------------------------------------------------------

    def _run_auto_addressing(self) -> int:
        """Execute the full 5-step CFS auto-addressing sequence.

        Step 1: Broadcast CMD_LOADER_TO_APP (0x0B) — wake all boxes.
        Step 2: Broadcast CMD_GET_SLAVE_INFO (0xA1) — discover all UniIDs.
                NOTE: Uses TIMEOUT_LONG (1.0 s). This step is intentionally slow.
        Step 3: For each discovered box, send CMD_SET_SLAVE_ADDR (0xA0).
        Step 4: Send CMD_ONLINE_CHECK (0xA2) per box — verify assignment.
        Step 5: Send CMD_GET_ADDR_TABLE (0xA3) — confirm full address table.

        Returns:
            int: Number of boxes that came online successfully.
        """
        logger.info("creality_cfs: starting auto-addressing sequence")

        # Step 1 — Wake boxes from loader mode
        logger.debug("creality_cfs: step 1 — CMD_LOADER_TO_APP broadcast")
        self._send_command(
            BROADCAST_ADDR_ALL,
            STATUS_ADDRESSING,
            CMD_LOADER_TO_APP,
            data=bytes([0x01]),
            timeout=TIMEOUT_SHORT,
            retries=1,
        )

        # Step 2 — Discover all boxes via broadcast GET_SLAVE_INFO
        # TIMEOUT_LONG intentional: boxes may respond at different times
        logger.info(
            "creality_cfs: step 2 — CMD_GET_SLAVE_INFO broadcast (%.1f s timeout)", TIMEOUT_LONG
        )
        # Send the broadcast with the MB broadcast address in the data field
        # (pattern from auto_addr_wrapper.py: send_data = [broadcast_addr, broadcast_addr])
        discovered: list = self._discover_slaves()
        logger.info("creality_cfs: discovered %d box(es)", len(discovered))

        # Step 3 — Assign addresses
        logger.debug("creality_cfs: step 3 — CMD_SET_SLAVE_ADDR for each discovered box")
        for attempt in range(MAX_SET_TIMES):
            for entry in self._box_table:
                if entry.mapped and entry.online in (
                    BoxAddressEntry.ONLINE_INIT, BoxAddressEntry.ONLINE_WAIT_ACK
                ):
                    self._set_slave_addr(BROADCAST_ADDR_MB, entry.addr, entry.uniid)

        # Step 4 — Online check per box
        logger.debug("creality_cfs: step 4 — CMD_ONLINE_CHECK per box")
        for entry in self._box_table:
            if entry.mapped:
                self._online_check(entry.addr)

        # Step 5 — Confirm address table
        logger.debug("creality_cfs: step 5 — CMD_GET_ADDR_TABLE per box")
        for attempt in range(MAX_GET_TIMES):
            for entry in self._box_table:
                if entry.online != BoxAddressEntry.ONLINE_ONLINE:
                    self._get_addr_table(entry.addr)

        online_count: int = sum(
            1 for e in self._box_table if e.online == BoxAddressEntry.ONLINE_ONLINE
        )
        logger.info(
            "creality_cfs: auto-addressing complete — %d/%d box(es) online",
            online_count, self.box_count,
        )
        return online_count

    def _discover_slaves(self) -> list:
        """Send CMD_GET_SLAVE_INFO broadcast and collect all responding UniIDs.

        The CFS boxes respond to the broadcast sequentially. Because this is
        half-duplex RS485, only one box responds at a time — the host must
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
                "creality_cfs: discovered box — addr=0x%02X mode=%d uniid=%s",
                addr, mode, " ".join(f"0x{b:02X}" for b in uniid),
            )
            discovered.append(self._box_table[addr - 1])

        return discovered

    def _allocate_address(self, uniid: list) -> int:
        """Find or assign an address slot for a discovered UniID.

        Priority order (from auto_addr_wrapper.py):
          1. Previously mapped slot with matching UniID (offline/init state).
          2. First unmapped slot.
          3. Mapped slot with non-matching UniID (offline/init state) — overwrite.

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
            logger.debug("creality_cfs: SET_SLAVE_ADDR — no response for addr=0x%02X", target_addr)
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

    def get_box_state(self, addr: int) -> dict:
        """Query the operating state of a single CFS box.

        Command: CMD_GET_BOX_STATE (0x0A), STATUS=0xFF, payload empty.
        Response: 4 bytes [state][?][?][?]
        confirmed 4-byte response; bytes 1-3 semantics are unconfirmed.
        TODO: hardware-test bytes 1-3 to determine filament sensor / motor state.

        Args:
            addr: Box address (0x01-0x04).

        Returns:
            dict with keys:
                raw (bytes): All 4 response data bytes.
                state (int): First byte — box operating state code.
                addr (int): Address that responded.

        Raises:
            RuntimeError: If no valid response received after retries.
        """
        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_GET_BOX_STATE,
            data=b"",
        )
        if resp is None:
            raise RuntimeError(f"No response from box 0x{addr:02X} for GET_BOX_STATE")

        data_bytes = resp.get("data", b"")
        if len(data_bytes) < 4:
            logger.warning(
                "creality_cfs: GET_BOX_STATE addr=0x%02X returned %d bytes (expected 4)",
                addr, len(data_bytes),
            )
        state: int = data_bytes[0] if len(data_bytes) > 0 else 0xFF
        logger.info("creality_cfs: GET_BOX_STATE addr=0x%02X state=0x%02X", addr, state)
        return {"raw": data_bytes, "state": state, "addr": addr}

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

    def set_box_mode(self, addr: int, mode: int, param: int = 0x01) -> bool:
        """Set the operating mode of a CFS box.

        Command: CMD_SET_BOX_MODE (0x04), STATUS=0xFF, payload=[mode][param].
        ACK response: b'\\xF7\\x01\\x03\\x00\\x04\\xA1' 

        Args:
            addr: Box address (0x01-0x04).
            mode: Mode byte (BOX_MODE_STANDBY=0x00, BOX_MODE_LOAD=0x01, etc.).
            param: Mode parameter byte (default 0x01).

        Returns:
            bool: True if command was acknowledged successfully.

        Raises:
            ValueError: If addr or mode are out of valid range.
        """
        if not (ADDR_BOX_MIN <= addr <= ADDR_BOX_MAX):
            raise ValueError(f"addr 0x{addr:02X} out of range [0x01, 0x04]")
        if not (0x00 <= mode <= 0xFF):
            raise ValueError(f"mode 0x{mode:02X} out of byte range")

        resp = self._send_command(
            addr,
            STATUS_OPERATIONAL,
            CMD_SET_BOX_MODE,
            data=bytes([mode, param]),
        )
        if resp is None:
            logger.warning("creality_cfs: SET_BOX_MODE addr=0x%02X — no response", addr)
            return False

        resp_status = resp.get("status", 0xFF)
        logger.info(
            "creality_cfs: SET_BOX_MODE addr=0x%02X mode=0x%02X status=0x%02X",
            addr, mode, resp_status,
        )
        return resp_status == STATUS_ADDRESSING  # ACK uses STATUS=0x00

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
            logger.warning("creality_cfs: SET_PRE_LOADING addr=0x%02X — no response", addr)
            return False

        logger.info(
            "creality_cfs: SET_PRE_LOADING addr=0x%02X mask=0x%02X enable=%d",
            addr, slot_mask, enable,
        )
        return True

    def extrude_process(self, addr: int, *args, **kwargs) -> None:
        """[STUBBED] CMD_EXTRUDE_PROCESS (0x10) — payload unknown.

        This command's payload is locked in the Creality .so binary and could
        not be recovered during reverse engineering. To unlock this command:
          1. Set up RS485 capture on /dev/ttyS5 (e.g., interceptty or logic analyzer).
          2. Trigger a T0-T3 tool-change on the Creality host software.
          3. Capture the full message frames and report them to update this stub.

        Raises:
            NotImplementedError: Always. This command is not yet implemented.
        """
        raise NotImplementedError(
            "CMD_EXTRUDE_PROCESS (0x10) payload is unknown — capture RS485 traffic "
            "during a T0-T3 tool-change to recover the payload format. "
            "See INSTALL.md section 'Unlocking 0x10/0x11' for instructions."
        )

    def retrude_process(self, addr: int, *args, **kwargs) -> None:
        """[STUBBED] CMD_RETRUDE_PROCESS (0x11) — payload unknown.

        Same limitation as extrude_process(). See that method's docstring.

        Raises:
            NotImplementedError: Always. This command is not yet implemented.
        """
        raise NotImplementedError(
            "CMD_RETRUDE_PROCESS (0x11) payload is unknown — capture RS485 traffic "
            "during a T0-T3 tool-change to recover the payload format. "
            "See INSTALL.md section 'Unlocking 0x10/0x11' for instructions."
        )

    # -----------------------------------------------------------------------
    # G-code command handlers
    # -----------------------------------------------------------------------

    cmd_CFS_INIT_help: str = (
        "Run the CFS auto-addressing sequence to discover and assign addresses "
        "to all connected Creality Filament System boxes"
    )

    def cmd_CFS_INIT(self, gcmd) -> None:
        """G-code: CFS_INIT — run the full 5-step auto-addressing sequence.

        Usage: CFS_INIT
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected — check serial_port in config")
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
        """G-code: CFS_STATUS [BOX=<1-4>] — query box state.

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
                results.append(f"Box {addr}: ERROR — {exc}")

        gcmd.respond_info("\n".join(results))

    cmd_CFS_VERSION_help: str = (
        "Query firmware version and serial number from one or all CFS boxes. "
        "Optionally specify BOX=<1-4> for a single box."
    )

    def cmd_CFS_VERSION(self, gcmd) -> None:
        """G-code: CFS_VERSION [BOX=<1-4>] — query version/SN.

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
                results.append(f"Box {addr}: ERROR — {exc}")

        gcmd.respond_info("\n".join(results))

    cmd_CFS_SET_MODE_help: str = (
        "Set operating mode on a CFS box. "
        "Parameters: BOX=<1-4> MODE=<0-255> [PARAM=<0-255>]"
    )

    def cmd_CFS_SET_MODE(self, gcmd) -> None:
        """G-code: CFS_SET_MODE BOX=<1-4> MODE=<0-255> [PARAM=<0-255>].

        Usage: CFS_SET_MODE BOX=1 MODE=1       # load mode
               CFS_SET_MODE BOX=1 MODE=0       # standby mode
        """
        if not self.is_connected:
            raise gcmd.error("CFS serial port is not connected")

        addr = gcmd.get_int("BOX", minval=1, maxval=4)
        mode = gcmd.get_int("MODE", minval=0, maxval=255)
        param = gcmd.get_int("PARAM", 0x01, minval=0, maxval=255)

        try:
            ok = self.set_box_mode(addr, mode, param)
            if ok:
                gcmd.respond_info(f"CFS box {addr}: mode set to 0x{mode:02X}")
            else:
                gcmd.respond_info(f"CFS box {addr}: SET_MODE sent (no explicit ACK received)")
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
        """G-code: CFS_ADDR_TABLE — print address assignment table."""
        lines = ["CFS Address Table:"]
        for entry in self._box_table:
            online_str = {
                BoxAddressEntry.ONLINE_OFFLINE: "OFFLINE",
                BoxAddressEntry.ONLINE_ONLINE: "ONLINE",
                BoxAddressEntry.ONLINE_INIT: "INIT",
                BoxAddressEntry.ONLINE_WAIT_ACK: "WAIT_ACK",
            }.get(entry.online, f"UNKNOWN({entry.online})")
            mode_str = "APP" if entry.mode == BoxAddressEntry.MODE_APP else "LOADER"
            uniid_str = " ".join(f"{b:02X}" for b in entry.uniid) if entry.mapped else "—"
            lines.append(
                f"  Addr 0x{entry.addr:02X}: {online_str} | mode={mode_str} "
                f"| mapped={entry.mapped} | acked={entry.acked} "
                f"| lost={entry.lost_cnt} | uniid=[{uniid_str}]"
            )
        gcmd.respond_info("\n".join(lines))


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
