"""
mock_cfs.py — MockCFSHardware simulator for CFS RS485 protocol testing.

Simulates the response behavior of 1-4 Creality Filament System boxes over
RS485, including CRC validation of received messages, state tracking, and
configurable error injection.  No physical hardware is required.

Usage:
    mock = MockCFSHardware(box_count=4)
    response = mock.process_message(raw_bytes)
    mock.assert_command_received(0x0A, times=1)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from creality_cfs import (
    crc8_cfs,
    build_message,
    parse_message,
    PACK_HEAD,
    STATUS_ADDRESSING,
    STATUS_OPERATIONAL,
    CMD_LOADER_TO_APP,
    CMD_GET_SLAVE_INFO,
    CMD_SET_SLAVE_ADDR,
    CMD_ONLINE_CHECK,
    CMD_GET_ADDR_TABLE,
    CMD_SET_BOX_MODE,
    CMD_GET_BOX_STATE,
    CMD_SET_PRE_LOADING,
    CMD_GET_VERSION_SN,
    DEV_TYPE_MB,
    MIN_MSG_LEN,
)

# ---------------------------------------------------------------------------
# Default UniIDs for simulated boxes (12 bytes each, derived from captured frame
# data in test_structures.py: b'\x01\x00\x5c\x51\x30\x03\x14\x91\xb0\x15\x4c\x30')
# ---------------------------------------------------------------------------
_DEFAULT_UNIIDS = [
    [0x01, 0x00, 0x5C, 0x51, 0x30, 0x03, 0x14, 0x91, 0xB0, 0x15, 0x4C, 0x30],
    [0x02, 0x00, 0x5C, 0x51, 0x30, 0x03, 0x14, 0x91, 0xB0, 0x15, 0x4C, 0x31],
    [0x03, 0x00, 0x5C, 0x51, 0x30, 0x03, 0x14, 0x91, 0xB0, 0x15, 0x4C, 0x32],
    [0x04, 0x00, 0x5C, 0x51, 0x30, 0x03, 0x14, 0x91, 0xB0, 0x15, 0x4C, 0x33],
]

# Default version strings per box (22 bytes, ASCII, from captured RX frame)
_DEFAULT_VERSIONS = [
    b"11010000843215B625AHSC",
    b"11010000843215B625AHSD",
    b"11010000843215B625AHSE",
    b"11010000843215B625AHSF",
]


class MockCFSHardware:
    """Simulates CFS material box RS485 responses without physical hardware.

    Maintains per-box state (addressed, mode, pre-loading) and returns
    correctly framed, CRC-validated responses for all 9 confirmed commands.
    Error injection allows testing of timeout, CRC corruption, truncation,
    and garbage-response scenarios.

    Args:
        box_count: Number of boxes to simulate (1-4).
        uniids: Optional list of 12-byte UniID lists, one per box.
        versions: Optional list of 22-byte version strings, one per box.
    """

    # Error type constants
    ERROR_TIMEOUT = "timeout"
    ERROR_CRC = "crc_error"
    ERROR_NACK = "nack"
    ERROR_TRUNCATED = "truncated"
    ERROR_GARBAGE = "garbage"

    def __init__(self, box_count=4, uniids=None, versions=None):
        """Initialize simulator with given number of boxes."""
        if not 1 <= box_count <= 4:
            raise ValueError(f"box_count must be 1-4, got {box_count}")

        self.box_count = box_count
        self._uniids = (uniids or _DEFAULT_UNIIDS)[:box_count]
        self._versions = (versions or _DEFAULT_VERSIONS)[:box_count]

        # Per-box state: indexed by 0-based slot (addr = slot + 1)
        self._addressed = [False] * box_count   # has received SET_SLAVE_ADDR
        self._modes = [0x00] * box_count         # current box mode
        self._pre_loading = [0x00] * box_count  # pre-loading slot mask

        # Discovery queue: each GET_SLAVE_INFO returns one box, FIFO
        self._discovery_queue = list(range(box_count))

        # Error injection: list of (func, remaining_successes, error_type)
        self._error_injections = []

        # Call history: list of (func, parsed_request)
        self._received = []

    # -----------------------------------------------------------------------
    # Primary interface
    # -----------------------------------------------------------------------

    def process_message(self, raw_bytes):
        """Validate an incoming message and return the appropriate response.

        Validates the CRC of the received frame before processing.  If the
        incoming CRC is bad, returns None (simulates ignoring corrupt frames).

        Args:
            raw_bytes: Complete raw frame bytes from the host.

        Returns:
            bytes: Response frame, or None if no response (timeout/bad CRC).
        """
        parsed = parse_message(raw_bytes)
        if parsed is None:
            return None
        if not parsed["crc_valid"]:
            return None

        func = parsed["func"]
        self._received.append((func, parsed))

        # Apply error injection if configured for this command
        err = self._consume_error(func)
        if err == self.ERROR_TIMEOUT:
            return None
        if err == self.ERROR_GARBAGE:
            return b"\xAA\xBB\xCC\xDD\xEE"
        if err == self.ERROR_TRUNCATED:
            # Build a valid response then slice it short
            resp = self._dispatch(func, parsed)
            return resp[:3] if resp and len(resp) >= 3 else b"\xF7"

        resp = self._dispatch(func, parsed)

        if err == self.ERROR_CRC and resp is not None:
            # Corrupt the last byte (CRC byte)
            resp = resp[:-1] + bytes([(resp[-1] ^ 0xFF) & 0xFF])

        return resp

    def _dispatch(self, func, parsed):
        """Route the parsed command to the appropriate response builder."""
        addr = parsed["addr"]
        data = parsed["data"]

        if func == CMD_LOADER_TO_APP:
            return self._resp_loader_to_app(addr, data)
        elif func == CMD_GET_SLAVE_INFO:
            return self._resp_get_slave_info(addr, data)
        elif func == CMD_SET_SLAVE_ADDR:
            return self._resp_set_slave_addr(addr, data)
        elif func == CMD_ONLINE_CHECK:
            return self._resp_online_check(addr, data)
        elif func == CMD_GET_ADDR_TABLE:
            return self._resp_get_addr_table(addr, data)
        elif func == CMD_SET_BOX_MODE:
            return self._resp_set_box_mode(addr, data)
        elif func == CMD_GET_BOX_STATE:
            return self._resp_get_box_state(addr, data)
        elif func == CMD_SET_PRE_LOADING:
            return self._resp_set_pre_loading(addr, data)
        elif func == CMD_GET_VERSION_SN:
            return self._resp_get_version_sn(addr, data)
        else:
            # Unknown command — return None (no response)
            return None

    # -----------------------------------------------------------------------
    # Response builders
    # -----------------------------------------------------------------------

    def _resp_loader_to_app(self, addr, data):
        """CMD_LOADER_TO_APP: broadcast wakeup — no response expected per protocol."""
        # Boxes do not respond to the broadcast wake command
        return None

    def _resp_get_slave_info(self, addr, data):
        """CMD_GET_SLAVE_INFO: return next box from discovery queue."""
        if not self._discovery_queue:
            return None
        slot = self._discovery_queue.pop(0)
        uniid = self._uniids[slot]
        resp_data = bytes([DEV_TYPE_MB, 0x00]) + bytes(uniid)
        # Response address is the box's future address (slot + 1)
        return build_message(slot + 1, STATUS_ADDRESSING, CMD_GET_SLAVE_INFO, resp_data)

    def _resp_set_slave_addr(self, addr, data):
        """CMD_SET_SLAVE_ADDR: ACK with dev_type, mode, uniid echo."""
        if len(data) < 1:
            return None
        target_addr = data[0]
        slot = target_addr - 1
        if 0 <= slot < self.box_count:
            self._addressed[slot] = True
            uniid = self._uniids[slot]
            resp_data = bytes([DEV_TYPE_MB, self._modes[slot]]) + bytes(uniid)
            return build_message(target_addr, STATUS_ADDRESSING, CMD_SET_SLAVE_ADDR, resp_data)
        return None

    def _resp_online_check(self, addr, data):
        """CMD_ONLINE_CHECK: return UniID echo for the addressed box."""
        slot = addr - 1
        if not (0 <= slot < self.box_count):
            return None
        uniid = self._uniids[slot]
        resp_data = bytes([DEV_TYPE_MB, self._modes[slot]]) + bytes(uniid)
        return build_message(addr, STATUS_ADDRESSING, CMD_ONLINE_CHECK, resp_data)

    def _resp_get_addr_table(self, addr, data):
        """CMD_GET_ADDR_TABLE: return full UniID table entry for this box."""
        slot = addr - 1
        if not (0 <= slot < self.box_count):
            return None
        uniid = self._uniids[slot]
        # Captured frame: b'\xf7\x01\x11\x00\xa3\x01\x00\x5c\x51\x30\x03\x14\x91\xb0\x15\x4c\x30\x39\x33\x48'
        # data = [dev_type(1), mode(1), uniid(12-ish)] -> total 14 bytes
        resp_data = bytes([DEV_TYPE_MB, self._modes[slot], slot + 1, 0x00]) + bytes(uniid)
        return build_message(addr, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE, resp_data)

    def _resp_set_box_mode(self, addr, data):
        """CMD_SET_BOX_MODE: apply mode and return ACK."""
        slot = addr - 1
        if 0 <= slot < self.box_count and len(data) >= 1:
            self._modes[slot] = data[0]
        # Exact ACK captured: b'\xf7\x01\x03\x00\x04\xa1'
        return build_message(addr, STATUS_ADDRESSING, CMD_SET_BOX_MODE)

    def _resp_get_box_state(self, addr, data):
        """CMD_GET_BOX_STATE: return 4-byte state response."""
        slot = addr - 1
        if not (0 <= slot < self.box_count):
            return None
        # Captured: b'\xf7\x01\x07\x00\x0a\x1c\x14\x00\x00\x48'
        # data = [0x1C, 0x14, 0x00, 0x00] (4 bytes)
        state_byte = 0x1C  # nominal operational state
        resp_data = bytes([state_byte, 0x14, 0x00, 0x00])
        return build_message(addr, STATUS_ADDRESSING, CMD_GET_BOX_STATE, resp_data)

    def _resp_set_pre_loading(self, addr, data):
        """CMD_SET_PRE_LOADING: store mask and return ACK."""
        slot = addr - 1
        if 0 <= slot < self.box_count and len(data) >= 1:
            self._pre_loading[slot] = data[0]
        # Exact ACK captured: b'\xf7\x01\x03\x00\x0d\x9e'
        return build_message(addr, STATUS_ADDRESSING, CMD_SET_PRE_LOADING)

    def _resp_get_version_sn(self, addr, data):
        """CMD_GET_VERSION_SN: return 22-byte ASCII version/SN."""
        slot = addr - 1
        if not (0 <= slot < self.box_count):
            return None
        version_bytes = self._versions[slot]
        if len(version_bytes) < 22:
            version_bytes = version_bytes + b"\x00" * (22 - len(version_bytes))
        # Captured: b'\xf7\x01\x19\x00\x14\x31\x31\x30\x31\x30\x30\x30\x30\x38\x34\x33\x32\x31\x35\x42\x36\x32\x35\x41\x48\x53\x43\x84'
        return build_message(addr, STATUS_ADDRESSING, CMD_GET_VERSION_SN, version_bytes[:22])

    # -----------------------------------------------------------------------
    # Error injection
    # -----------------------------------------------------------------------

    def inject_error(self, error_type, on_command=None, after_n=0):
        """Schedule an error response for a specific command.

        Args:
            error_type: One of ERROR_TIMEOUT, ERROR_CRC, ERROR_NACK,
                        ERROR_TRUNCATED, ERROR_GARBAGE.
            on_command: Function code to apply error to, or None for next cmd.
            after_n: Trigger after this many successful responses.
        """
        self._error_injections.append({
            "type": error_type,
            "func": on_command,
            "remaining": after_n,
        })

    def _consume_error(self, func):
        """Check if an error should be triggered for this command.

        Returns the error type string if triggered, else None.
        """
        for i, inj in enumerate(self._error_injections):
            if inj["func"] is not None and inj["func"] != func:
                continue
            if inj["remaining"] > 0:
                inj["remaining"] -= 1
                continue
            self._error_injections.pop(i)
            return inj["type"]
        return None

    # -----------------------------------------------------------------------
    # State management
    # -----------------------------------------------------------------------

    def reset(self):
        """Reset all box state and error injections to initial values."""
        self._addressed = [False] * self.box_count
        self._modes = [0x00] * self.box_count
        self._pre_loading = [0x00] * self.box_count
        self._discovery_queue = list(range(self.box_count))
        self._error_injections.clear()
        self._received.clear()

    def reset_discovery_queue(self):
        """Repopulate the discovery queue without clearing other state."""
        self._discovery_queue = list(range(self.box_count))

    # -----------------------------------------------------------------------
    # Assertion helpers
    # -----------------------------------------------------------------------

    def get_received_messages(self):
        """Return list of (func_code, parsed_dict) tuples for all received messages."""
        return list(self._received)

    def get_received_funcs(self):
        """Return ordered list of function codes received (useful for sequence checks)."""
        return [func for func, _ in self._received]

    def assert_command_received(self, func, times=None):
        """Assert that a specific command was received the expected number of times.

        Args:
            func: Function code to check.
            times: Expected call count.  If None, asserts at least once.

        Raises:
            AssertionError: If the assertion fails.
        """
        actual = sum(1 for f, _ in self._received if f == func)
        if times is None:
            assert actual >= 1, (
                f"Expected command 0x{func:02X} to be received at least once, "
                f"got {actual}. Received: {self.get_received_funcs()}"
            )
        else:
            assert actual == times, (
                f"Expected command 0x{func:02X} exactly {times} time(s), "
                f"got {actual}. Received: {self.get_received_funcs()}"
            )

    def is_box_addressed(self, slot_index):
        """Return True if the box at the given 0-based slot index is addressed."""
        return self._addressed[slot_index]

    def get_box_mode(self, slot_index):
        """Return the current mode byte for the given 0-based slot index."""
        return self._modes[slot_index]

    def get_box_pre_loading(self, slot_index):
        """Return the current pre-loading mask for the given 0-based slot index."""
        return self._pre_loading[slot_index]
