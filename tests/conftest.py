# SPDX-License-Identifier: GPL-3.0-or-later
"""
conftest.py: Shared pytest fixtures for the CFS test suite.

Transport note (v1.3.0 / B1):
  The live serial transport was rewritten from blocking pyserial to a non-blocking
  reactor-fd model. The single raw request/response exchange now lives behind the
  CrealityCFS._txn(request_bytes, timeout, match) seam (the only method that touches
  the fd/reactor). The protocol logic in _send_command (build_message + retry +
  parse_message + CRC) wraps _txn, so the suite drives _txn instead of a live fd.

  To preserve the EXISTING tests' intent (canned-response queues, mock_hw integration,
  write-call assertions, chunked-read framing, SerialException break-loop) without
  rewriting every test, the fixtures install a fake _txn that behaves exactly like the
  pre-B1 per-attempt path did: it calls self._serial.reset_input_buffer() + write(req),
  then reassembles one frame from chunked self._serial.read() calls (the old
  _read_response framing). Tests still push bytes into _serial.response_queue and inspect
  _serial.write — only the plumbing underneath moved to the _txn seam.

Provides:
  - mock_serial: A MagicMock standing in for the byte transport, with a configurable
    response queue so tests can pre-load byte sequences without hardware.
  - cfs_controller: A CrealityCFS object wired to mock_serial via the _txn seam that can
    exercise _send_command and all operational methods with no real fd/reactor.
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

# pyserial is a real dependency; tests reference serial.SerialException to simulate a
# hardware-level write failure. The fake _txn maps that exception to the same
# break-the-retry-loop behavior the live os.write/OSError path produces.
import serial

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so creality_cfs can be imported
# without a full Klipper environment.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_PROJECT_ROOT, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

import creality_cfs
from creality_cfs import (
    crc8_cfs,
    build_message,
    parse_message,
    BoxAddressEntry,
    CrealityCFS,
    PACK_HEAD,
    MAX_DATA_LEN,
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
    """Return a minimal MagicMock that satisfies CrealityCFS.__init__.

    v1.4.0 harness updates:
      * reactor.monotonic() is a REAL advancing float clock (0.5 s per call). The
        choreography wall-budget loops (load/unload/flush) compare monotonic() against
        float deadlines; a bare MagicMock would compare truthy forever (infinite loop).
      * printer.lookup_object() returns the gcode mock ONLY for "gcode" and the caller's
        default for everything else, so optional-object probes (filament_switch_sensor,
        extruder) correctly resolve to None on the bare harness instead of a truthy mock
        (a truthy sensor mock would fake 'filament detected' on every read).
    """
    cfg = mock.MagicMock()

    # Printer / reactor / gcode sub-objects
    printer = mock.MagicMock()
    reactor = mock.MagicMock()
    gcode = mock.MagicMock()

    import itertools
    _clock = itertools.count()
    reactor.monotonic.side_effect = lambda: next(_clock) * 0.5

    def _lookup_object(name, default=mock.sentinel.no_default):
        if name == "gcode":
            return gcode
        if default is mock.sentinel.no_default:
            raise Exception("lookup_object(%r): not present in fake printer" % (name,))
        return default

    printer.get_reactor.return_value = reactor
    printer.lookup_object.side_effect = _lookup_object
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
        "rts_on_send": -1,
    }.get(key, default)
    cfg.getfloat.side_effect = lambda key, default=None, **kw: {
        "timeout": TIMEOUT_MEDIUM,
    }.get(key, default)
    cfg.getboolean.side_effect = lambda key, default=None: {
        "auto_init": auto_init,
    }.get(key, default)

    return cfg


# ---------------------------------------------------------------------------
# Fake byte transport + _txn seam wiring
# ---------------------------------------------------------------------------

def _frame_from_serial(ser):
    """Reassemble ONE response frame from chunked ser.read() calls.

    This is the exact framing the pre-B1 _read_response performed, kept here so the
    canned-response and chunked-read tests exercise identical observable behavior
    through the _txn seam:
      * read HEAD+ADDR+LEN (3 bytes); reject if <3 or HEAD != 0xF7
      * reject implausible LEN (<3 or >MAX_DATA_LEN+3)
      * read exactly LEN more bytes; reject if truncated
    Returns the full raw frame, or b"" on timeout/truncation/bad header.
    """
    header = ser.read(3)
    if header is None or len(header) < 3:
        return b""
    if header[0] != PACK_HEAD:
        return b""
    length_field = header[2]
    if length_field < 3 or length_field > (MAX_DATA_LEN + 3):
        return b""
    remainder = ser.read(length_field)
    if remainder is None or len(remainder) < length_field:
        return b""
    return bytes(header) + bytes(remainder)


def _install_fake_transport(cfs, ser):
    """Wire a CrealityCFS instance to a fake byte transport through the _txn seam.

    Replaces cfs._txn with a callable that reproduces the pre-B1 per-attempt path:
      reset_input_buffer() -> write(req) -> reassemble one frame via read().
    A serial.SerialException from write() maps to the _TXN_WRITE_ERROR sentinel so
    _send_command breaks the retry loop (same contract as the live OSError path).
    The fd/reactor are never touched; cfs._fd is set non-None so _send_command's
    connected-precondition (is_connected and _fd is not None) holds.
    """
    cfs._serial = ser
    # Non-None so _send_command's connected precondition (is_connected and _fd is not None)
    # holds. -1 is a deliberately invalid fd: the fake _txn never touches it, and if a
    # disconnect path calls os.close(_fd) it raises OSError (caught) instead of closing a
    # real descriptor like stdout.
    cfs._fd = -1
    cfs.is_connected = True
    cfs._shutdown = False

    def _fake_txn(request_bytes, timeout, match=None):
        try:
            ser.reset_input_buffer()
            ser.write(request_bytes)
        except serial.SerialException:
            return cfs._TXN_WRITE_ERROR
        return _frame_from_serial(ser)

    cfs._txn = _fake_txn
    return cfs


def _make_controller(fake_config, ser):
    """Construct a CrealityCFS off-POSIX and bolt the fake transport onto it."""
    cfs = CrealityCFS(fake_config)
    _install_fake_transport(cfs, ser)
    return cfs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_serial():
    """Return a MagicMock byte transport with a pre-loaded response queue.

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
    """Return a CrealityCFS instance wired to mock_serial via the _txn seam.

    The constructor runs off-POSIX (no live fd); the fake _txn drives mock_serial so
    tests can pre-load responses and inspect writes exactly as before.

    Usage:
        def test_something(cfs_controller, mock_serial):
            mock_serial.response_queue.append(good_response_bytes)
            result = cfs_controller.get_box_state(0x01)
            assert result["state"] == 0x14
    """
    fake_config = _make_fake_config(auto_init=False)
    return _make_controller(fake_config, mock_serial)


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
# Helper: wire mock_hw to cfs_controller transport
# ---------------------------------------------------------------------------

def make_wired_controller(mock_hw, box_count=4, retry_count=1):
    """Return a (cfs, mock_serial) pair where cfs talks to mock_hw via the _txn seam.

    The mock_serial is configured so that every write() immediately calls
    mock_hw.process_message() and queues the response for the next read().
    The fake _txn drives this transport, so timeouts never block.

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
    cfs = _make_controller(fake_config, ser)
    return cfs, ser
