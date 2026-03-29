"""
conftest.py — Shared pytest fixtures for the CFS test suite.

Provides:
  - mock_serial: A MagicMock replacing serial.Serial, with a configurable
    response queue so tests can pre-load byte sequences without hardware.
  - cfs_controller: A CrealityCFS-like object wired to mock_serial that can
    exercise _send_command, _read_response, and all operational methods.
  - test_vectors: CRC and message test vectors loaded from test_data/.
  - mock_hw: A MockCFSHardware instance for integration-style tests.

All fixtures are function-scoped (default) so tests cannot share state.
"""

import json
import os
import sys
import types
import unittest.mock as mock

import pytest

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so creality_cfs can be imported
# without a full Klipper environment.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_PROJECT_ROOT, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

from creality_cfs import (
    crc8_cfs,
    build_message,
    parse_message,
    BoxAddressEntry,
    CrealityCFS,
    STATUS_ADDRESSING,
    STATUS_OPERATIONAL,
    CMD_GET_BOX_STATE,
    CMD_GET_VERSION_SN,
    CMD_SET_BOX_MODE,
    CMD_SET_PRE_LOADING,
    CMD_GET_SLAVE_INFO,
    CMD_SET_SLAVE_ADDR,
    CMD_ONLINE_CHECK,
    CMD_GET_ADDR_TABLE,
    CMD_LOADER_TO_APP,
    BROADCAST_ADDR_MB,
    BROADCAST_ADDR_ALL,
    TIMEOUT_SHORT,
    TIMEOUT_MEDIUM,
    TIMEOUT_LONG,
)

from tests.mock_cfs import MockCFSHardware


# ---------------------------------------------------------------------------
# Helper: build a minimal fake Klipper config object
# ---------------------------------------------------------------------------

def _make_fake_config(port="/dev/null", baud=230400, box_count=4,
                      retry_count=3, auto_init=False):
    """Return a minimal MagicMock that satisfies CrealityCFS.__init__."""
    cfg = mock.MagicMock()

    # Printer / reactor / gcode sub-objects
    printer = mock.MagicMock()
    reactor = mock.MagicMock()
    gcode = mock.MagicMock()

    printer.get_reactor.return_value = reactor
    printer.lookup_object.return_value = gcode
    cfg.get_printer.return_value = printer

    # Config getters
    cfg.get_name.return_value = "creality_cfs"
    cfg.get.side_effect = lambda key, default=None: {
        "serial_port": port,
    }.get(key, default)
    cfg.getint.side_effect = lambda key, default=None, **kw: {
        "baud": baud,
        "retry_count": retry_count,
        "box_count": box_count,
    }.get(key, default)
    cfg.getfloat.side_effect = lambda key, default=None, **kw: {
        "timeout": TIMEOUT_MEDIUM,
    }.get(key, default)
    cfg.getboolean.side_effect = lambda key, default=None: {
        "auto_init": auto_init,
    }.get(key, default)

    return cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_serial():
    """Return a MagicMock for serial.Serial with a pre-loaded response queue.

    Tests push bytes into mock_serial.response_queue; each call to read()
    returns and removes the front item.  write() is recorded for inspection.

    Usage:
        mock_serial.response_queue.append(b'\\xf7\\x01\\x03\\x00\\x0a\\x5c')
        # ... then call cfs._send_command(...)
        written = mock_serial.write.call_args_list
    """
    ser = mock.MagicMock()
    ser.is_open = True
    ser.response_queue = []

    def _read(n):
        if ser.response_queue:
            chunk = ser.response_queue.pop(0)
            return chunk[:n]
        return b""

    ser.read.side_effect = _read
    ser.reset_input_buffer.return_value = None
    ser.write.return_value = None
    return ser


@pytest.fixture
def cfs_controller(mock_serial):
    """Return a CrealityCFS instance wired to mock_serial (no real hardware).

    The fixture patches serial.Serial so the constructor does not try to open
    a real port.  The mock serial object is injected directly so tests can
    control it.

    Usage:
        def test_something(cfs_controller, mock_serial):
            mock_serial.response_queue.append(good_response_bytes)
            result = cfs_controller.get_box_state(0x01)
            assert result["state"] == 0x1C
    """
    fake_config = _make_fake_config(auto_init=False)

    with mock.patch("creality_cfs.serial.Serial", return_value=mock_serial):
        cfs = CrealityCFS(fake_config)
        cfs._serial = mock_serial
        cfs.is_connected = True

    return cfs


@pytest.fixture
def mock_hw():
    """Return a fresh MockCFSHardware with 4 boxes, discovery queue filled."""
    hw = MockCFSHardware(box_count=4)
    return hw


@pytest.fixture
def test_vectors():
    """Load CRC and message test vectors from tests/test_data/test_vectors.json."""
    path = os.path.join(_TESTS_DIR, "test_data", "test_vectors.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def valid_messages():
    """Load known-good message fixtures from tests/test_data/valid_messages.json."""
    path = os.path.join(_TESTS_DIR, "test_data", "valid_messages.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def invalid_messages():
    """Load invalid message fixtures from tests/test_data/invalid_messages.json."""
    path = os.path.join(_TESTS_DIR, "test_data", "invalid_messages.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Helper: wire mock_hw to cfs_controller serial transport
# ---------------------------------------------------------------------------

def make_wired_controller(mock_hw, box_count=4, retry_count=1):
    """Return a (cfs, mock_serial) pair where cfs talks to mock_hw.

    The mock_serial is configured so that every write() immediately calls
    mock_hw.process_message() and queues the response for the next read().
    Timeouts are mocked to zero to keep tests fast.

    Args:
        mock_hw: MockCFSHardware instance.
        box_count: Number of boxes (must match mock_hw.box_count).
        retry_count: Override for CrealityCFS retry_count (default 1).

    Returns:
        (cfs_controller, mock_serial)
    """
    ser = mock.MagicMock()
    ser.is_open = True
    ser.response_queue = []

    def _write(data):
        resp = mock_hw.process_message(data)
        if resp:
            # Queue response as two chunks to simulate real framing read
            ser.response_queue.append(resp[:3])      # HEAD+ADDR+LEN
            ser.response_queue.append(resp[3:])      # STATUS+FUNC+DATA+CRC

    def _read(n):
        if ser.response_queue:
            chunk = ser.response_queue.pop(0)
            return chunk[:n]
        return b""

    ser.write.side_effect = _write
    ser.read.side_effect = _read
    ser.reset_input_buffer.return_value = None

    fake_config = _make_fake_config(box_count=box_count, retry_count=retry_count,
                                    auto_init=False)
    with mock.patch("creality_cfs.serial.Serial", return_value=ser):
        cfs = CrealityCFS(fake_config)
        cfs._serial = ser
        cfs.is_connected = True

    return cfs, ser
