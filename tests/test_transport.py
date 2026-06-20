"""
test_transport.py: Genuine behavior tests for the v1.3.0 (B1) reactor-fd serial
transport in creality_cfs.py.

The B1 rewrite moved the live serial I/O off blocking pyserial onto a non-blocking
reactor-fd model. The protocol logic in _send_command drives that I/O through the
CrealityCFS._txn(request, timeout, match) seam, and the rest of the suite replaces
_txn with a fake. As a result the actual transport code -- _connect_serial,
_config_tty, _config_rs485, _resolve_baud_const, the real _txn body, and the read
path _handle_readable / _parse_rx / _dispatch_rx -- was never exercised, leaving
coverage under the CI gate.

These tests drive the REAL transport code and assert its real behavior. They do NOT
mock _txn. Instead they install:
  * a controllable FakeReactor whose completion()/monotonic()/mutex()/register_fd()
    are deterministic and synchronous, and
  * fake os / termios / fcntl modules patched onto creality_cfs,
so the same tests run identically whether or not the host actually has fcntl/termios.

PLATFORM-AGNOSTIC NOTE (critical):
  The CI runs on Linux where creality_cfs.fcntl/termios are the real modules and
  creality_cfs._HAS_POSIX_SERIAL is True. This dev/test box is Windows, where the
  module sets fcntl=termios=None and _HAS_POSIX_SERIAL=False. Every test here patches
  creality_cfs.os, creality_cfs.termios, creality_cfs.fcntl, and forces
  _HAS_POSIX_SERIAL where the path under test depends on it, so the assertions never
  depend on the host's real fcntl/termios. The one test that asserts the off-POSIX
  RuntimeError forces _HAS_POSIX_SERIAL=False explicitly rather than relying on the
  host being Windows.
"""

import sys
import os as _real_os
import types
import unittest.mock as mock

import pytest

sys.path.insert(
    0, _real_os.path.join(_real_os.path.dirname(_real_os.path.dirname(_real_os.path.abspath(__file__))), "src")
)

import creality_cfs
from creality_cfs import (
    CrealityCFS,
    build_message,
    PACK_HEAD,
    MAX_DATA_LEN,
    STATUS_ADDRESSING,
    STATUS_OPERATIONAL,
    CMD_GET_BOX_STATE,
    CMD_GET_VERSION_SN,
    CMD_VERSION_INFO,
    CMD_SET_BOX_MODE,
    CMD_SET_PRE_LOADING,
    CMD_GET_HARDWARE_STATUS,
    CMD_CUT_STATE,
    CMD_CTRL_CONNECTION_MOTOR_ACTION,
    CMD_MEASURING_WHEEL,
    CMD_RETRUDE_PROCESS,
    ADDR_BUFFER_NODE,
    SLOT_T0,
    SLOT_T1,
    BOX_STATE_CLASS_BYTE,
    BOX_STATE_LO_LOADED,
    BOX_STATE_LO_FEEDING,
    CUT_STATE_DONE,
    CUT_STATE_SET,
    MOTOR_ACTION_ENGAGE,
    TIOCSRS485,
    SER_RS485_ENABLED,
    SER_RS485_RTS_ON_SEND,
    SER_RS485_RTS_AFTER_SEND,
)

from tests.conftest import _make_fake_config


# ===========================================================================
# Deterministic, synchronous fakes for the reactor completion / fd machinery
# ===========================================================================

class FakeCompletion:
    """A synchronous stand-in for reactor.completion().

    Mirrors the three reactor.completion methods the transport uses:
      * test()                  -> True once completed
      * complete(value)         -> store the result, mark done
      * wait(waketime, default) -> return the completed value if complete(...) ran
                                   before wait() was called, else `default` (timeout).
    The transport always completes a pending completion from the fd read callback
    (_dispatch_rx), so a test that wants a "response" arranges os.write to drive
    _handle_readable, which completes this object BEFORE _txn calls wait().
    """

    def __init__(self):
        self._done = False
        self._value = None

    def test(self):
        return self._done

    def complete(self, value):
        self._done = True
        self._value = value

    def wait(self, waketime=None, waketime_result=None):
        if self._done:
            return self._value
        return waketime_result


class FakeMutex:
    """Greenlet-aware mutex stand-in: a plain context manager (single-threaded test)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeReactor:
    """Deterministic reactor: synchronous completions, controllable clock.

    register_fd records the callback so a test (or the patched os.write) can drive
    _handle_readable directly. monotonic() returns a controllable, monotonically
    advancing clock so STREAM-style deadline loops terminate without real time.
    """

    def __init__(self):
        self._clock = 0.0
        self.fd_callback = None
        self.registered_fd = None
        self.unregister_called = False
        self.completions = []

    def get_reactor(self):
        return self

    def completion(self):
        comp = FakeCompletion()
        self.completions.append(comp)
        return comp

    def monotonic(self):
        # Advance a touch on each read so wall-clock deadline loops make progress.
        self._clock += 0.001
        return self._clock

    def mutex(self):
        return FakeMutex()

    def register_fd(self, fd, callback):
        self.registered_fd = fd
        self.fd_callback = callback
        return ("fd-handle", fd)

    def unregister_fd(self, handle):
        self.unregister_called = True

    def register_callback(self, cb):
        return None


# ===========================================================================
# Fake POSIX modules (os / termios / fcntl) -- so the transport runs and is
# asserted identically on a host with or without real fcntl/termios.
# ===========================================================================

# termios attribute layout the transport touches (indices + flag constants).
VMIN = 4
VTIME = 5


def make_fake_termios():
    """Return a fake `termios` module exposing exactly what _config_tty uses."""
    t = types.SimpleNamespace()
    # Flag bits (arbitrary but distinct so masking is observable).
    t.IGNPAR = 0x0004
    t.CSIZE = 0x0030
    t.CS8 = 0x0030
    t.CREAD = 0x0080
    t.CLOCAL = 0x0800
    t.PARENB = 0x0100
    t.CSTOPB = 0x0040
    t.CRTSCTS = 0x80000000
    t.VMIN = VMIN
    t.VTIME = VTIME
    t.TCSANOW = 0
    t.TCIOFLUSH = 2
    # A common termios baud constant the transport resolves by name (B230400).
    t.B230400 = 0x1004
    t.B115200 = 0x1002

    # tcgetattr returns a 7-element attr list: [iflag,oflag,cflag,lflag,ispeed,ospeed,cc]
    # cc must be index-assignable at VMIN/VTIME.
    captured = {}

    def tcgetattr(fd):
        return [0, 0, 0, 0, 0, 0, [0] * 32]

    def tcsetattr(fd, when, attr):
        captured["when"] = when
        captured["attr"] = attr

    def tcflush(fd, queue):
        captured["flush"] = queue

    t.tcgetattr = tcgetattr
    t.tcsetattr = tcsetattr
    t.tcflush = tcflush
    t._captured = captured
    return t


def make_fake_fcntl():
    """Return a fake `fcntl` module recording ioctl calls."""
    f = types.SimpleNamespace()
    f.calls = []

    def ioctl(fd, request, arg):
        f.calls.append((fd, request, arg))
        return 0

    f.ioctl = ioctl
    return f


def make_fake_os(read_chunks=None, write_sink=None, open_fd=7, open_raises=None,
                 write_raises=None):
    """Return a fake `os` module covering everything the transport calls.

    Delegates the harmless bits (path, sep, environ) to the real os via attribute
    fallback, but overrides open/close/read/write and the O_* flag constants.

    Args:
        read_chunks: list of byte chunks os.read pops one per call (then b"").
        write_sink: list that captures os.write(fd, data) payloads.
        open_fd: fd value os.open returns.
        open_raises: if set, os.open raises this.
        write_raises: if set, os.write raises this.
    """
    read_chunks = list(read_chunks or [])
    write_sink = write_sink if write_sink is not None else []

    fake = types.SimpleNamespace()
    # POSIX open flags the connect path ORs together.
    fake.O_RDWR = 0x0002
    fake.O_NOCTTY = 0x0100
    fake.O_NONBLOCK = 0x0800

    record = {"open_flags": None, "open_path": None, "closed": []}

    def _open(path, flags):
        if open_raises is not None:
            raise open_raises
        record["open_path"] = path
        record["open_flags"] = flags
        return open_fd

    def _close(fd):
        record["closed"].append(fd)

    def _read(fd, n):
        if read_chunks:
            return read_chunks.pop(0)
        return b""

    def _write(fd, data):
        if write_raises is not None:
            raise write_raises
        write_sink.append(bytes(data))
        return len(data)

    fake.open = _open
    fake.close = _close
    fake.read = _read
    fake.write = _write
    fake._record = record
    fake._write_sink = write_sink
    return fake


# ===========================================================================
# Builders
# ===========================================================================

def _bare_cfs(baud=230400, rts=-1):
    """Construct a CrealityCFS with a FakeReactor wired in, off any real fd.

    The constructor reads config and builds the bus lock from the reactor; we
    inject a FakeReactor so the real _txn/_send_command path can run synchronously.
    """
    cfg = _make_fake_config(baud=baud, auto_init=False)
    reactor = FakeReactor()
    cfg.get_printer.return_value.get_reactor.return_value = reactor
    cfg.getint.side_effect = lambda key, default=None, **kw: {
        "baud": baud,
        "retry_count": 1,
        "box_count": 4,
        "rts_on_send": rts,
    }.get(key, default)
    cfs = CrealityCFS(cfg)
    assert isinstance(cfs.reactor, FakeReactor)
    return cfs


def _good_frame(addr, func, data=b""):
    """Build a CRC-valid response frame the transport will accept."""
    return build_message(addr, STATUS_ADDRESSING, func, data)


# ===========================================================================
# _resolve_baud_const
# ===========================================================================

class TestResolveBaudConst:
    def test_resolves_known_baud_to_termios_constant(self):
        cfs = _bare_cfs(baud=230400)
        fake_t = make_fake_termios()
        with mock.patch.object(creality_cfs, "termios", fake_t):
            const = cfs._resolve_baud_const()
        assert const == fake_t.B230400
        assert cfs._baud_const == fake_t.B230400

    def test_unknown_baud_raises_runtime_error(self):
        # 250000 has no B250000 on the fake termios -> clear RuntimeError.
        cfs = _bare_cfs(baud=230400)
        cfs.baud = 250000
        fake_t = make_fake_termios()
        with mock.patch.object(creality_cfs, "termios", fake_t):
            with pytest.raises(RuntimeError, match="unsupported baud"):
                cfs._resolve_baud_const()


# ===========================================================================
# _config_tty
# ===========================================================================

class TestConfigTty:
    def test_sets_raw_8n1_via_termios(self):
        cfs = _bare_cfs(baud=230400)
        fake_t = make_fake_termios()
        with mock.patch.object(creality_cfs, "termios", fake_t):
            cfs._config_tty(fd=7)

        attr = fake_t._captured["attr"]
        # iflag raw / ignore parity
        assert attr[0] == fake_t.IGNPAR
        # oflag raw
        assert attr[1] == 0
        # cflag: CS8 set, CREAD + CLOCAL set, PARENB + CSTOPB + CRTSCTS cleared (8N1)
        assert attr[2] & fake_t.CS8 == fake_t.CS8
        assert attr[2] & fake_t.CREAD
        assert attr[2] & fake_t.CLOCAL
        assert not (attr[2] & fake_t.PARENB)
        assert not (attr[2] & fake_t.CSTOPB)
        assert not (attr[2] & fake_t.CRTSCTS)
        # lflag raw (no echo/canon/sig)
        assert attr[3] == 0
        # ispeed/ospeed both set to the resolved baud constant
        assert attr[4] == fake_t.B230400
        assert attr[5] == fake_t.B230400
        # VMIN=0 / VTIME=0 -> reads never block
        assert attr[6][VMIN] == 0
        assert attr[6][VTIME] == 0
        # applied immediately and flushed
        assert fake_t._captured["when"] == fake_t.TCSANOW
        assert fake_t._captured["flush"] == fake_t.TCIOFLUSH


# ===========================================================================
# _config_rs485 (opt-in only)
# ===========================================================================

class TestConfigRs485:
    def test_skipped_when_rts_on_send_none(self):
        """Default (rts_on_send=-1 -> None) leaves the UART alone: no ioctl."""
        cfs = _bare_cfs(rts=-1)
        assert cfs.rts_on_send is None
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._config_rs485(fd=7)
        assert fake_f.calls == []

    def test_enables_rts_on_send_when_opted_in_high(self):
        cfs = _bare_cfs(rts=1)
        assert cfs.rts_on_send is True
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._config_rs485(fd=9)
        assert len(fake_f.calls) == 1
        fd, request, arg = fake_f.calls[0]
        assert fd == 9
        assert request == TIOCSRS485
        # struct serial_rs485: first u32 = flags. RTS_ON_SEND form.
        import struct
        flags = struct.unpack("8I", arg)[0]
        assert flags & SER_RS485_ENABLED
        assert flags & SER_RS485_RTS_ON_SEND
        assert not (flags & SER_RS485_RTS_AFTER_SEND)

    def test_enables_rts_after_send_when_opted_in_low(self):
        cfs = _bare_cfs(rts=0)
        assert cfs.rts_on_send is False
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._config_rs485(fd=3)
        import struct
        flags = struct.unpack("8I", fake_f.calls[0][2])[0]
        assert flags & SER_RS485_ENABLED
        assert flags & SER_RS485_RTS_AFTER_SEND
        assert not (flags & SER_RS485_RTS_ON_SEND)

    def test_unsupported_ioctl_is_swallowed(self):
        """A kernel without TIOCSRS485 (OSError) must not crash the connect path."""
        cfs = _bare_cfs(rts=1)
        fake_f = make_fake_fcntl()
        fake_f.ioctl = mock.MagicMock(side_effect=OSError("not supported"))
        with mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._config_rs485(fd=7)  # must not raise


# ===========================================================================
# _connect_serial
# ===========================================================================

class TestConnectSerial:
    def test_off_posix_raises_clear_runtime_error(self):
        """When _HAS_POSIX_SERIAL is False, opening raises a clear RuntimeError.

        Forced explicitly so the test passes on BOTH a POSIX host and a non-POSIX one.
        """
        cfs = _bare_cfs()
        with mock.patch.object(creality_cfs, "_HAS_POSIX_SERIAL", False):
            with pytest.raises(RuntimeError, match="requires a POSIX host"):
                cfs._connect_serial()
        assert cfs.is_connected is False

    def test_open_uses_nonblocking_noctty_rdwr_flags(self):
        cfs = _bare_cfs()
        fake_os = make_fake_os(open_fd=11)
        fake_t = make_fake_termios()
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "_HAS_POSIX_SERIAL", True), \
             mock.patch.object(creality_cfs, "os", fake_os), \
             mock.patch.object(creality_cfs, "termios", fake_t), \
             mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._connect_serial()

        flags = fake_os._record["open_flags"]
        assert flags & fake_os.O_RDWR
        assert flags & fake_os.O_NONBLOCK
        assert flags & fake_os.O_NOCTTY
        assert fake_os._record["open_path"] == cfs.serial_port

    def test_connect_registers_fd_and_sets_connected(self):
        cfs = _bare_cfs()
        fake_os = make_fake_os(open_fd=11)
        fake_t = make_fake_termios()
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "_HAS_POSIX_SERIAL", True), \
             mock.patch.object(creality_cfs, "os", fake_os), \
             mock.patch.object(creality_cfs, "termios", fake_t), \
             mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._connect_serial()

        assert cfs._fd == 11
        assert cfs.is_connected is True
        # the reactor was handed the fd and the read callback (_handle_readable)
        assert cfs.reactor.registered_fd == 11
        assert cfs.reactor.fd_callback == cfs._handle_readable
        # tty was configured raw 8N1 (termios.tcsetattr ran)
        assert fake_t._captured.get("attr") is not None

    def test_connect_closes_fd_if_config_fails(self):
        """If termios config raises, _connect_serial closes the fd and re-raises."""
        cfs = _bare_cfs()
        fake_os = make_fake_os(open_fd=11)
        fake_t = make_fake_termios()
        fake_t.tcsetattr = mock.MagicMock(side_effect=OSError("tty config failed"))
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "_HAS_POSIX_SERIAL", True), \
             mock.patch.object(creality_cfs, "os", fake_os), \
             mock.patch.object(creality_cfs, "termios", fake_t), \
             mock.patch.object(creality_cfs, "fcntl", fake_f):
            with pytest.raises(OSError, match="tty config failed"):
                cfs._connect_serial()

        # fd was opened then closed on the failure path; not registered.
        assert 11 in fake_os._record["closed"]
        assert cfs.reactor.registered_fd is None


# ===========================================================================
# _txn (the real transport seam)
# ===========================================================================

class TestTxn:
    def _connected(self, cfs, read_chunks=None, write_sink=None, write_raises=None):
        """Open the fake transport on a connected cfs and return (fake_os, fake_t, fake_f)."""
        fake_os = make_fake_os(read_chunks=read_chunks, write_sink=write_sink,
                               open_fd=11, write_raises=write_raises)
        fake_t = make_fake_termios()
        fake_f = make_fake_fcntl()
        return fake_os, fake_t, fake_f

    def test_registers_pending_before_write_then_returns_completed_frame(self):
        """_txn registers the pending completion BEFORE os.write, and the read path
        (driven by the patched os.write) completes it; wait() returns that frame.

        This exercises _txn + _handle_readable + _parse_rx + _dispatch_rx together.
        """
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_GET_BOX_STATE, bytes([0x1A, 0x20]))
        order = []

        # os.write drives the fd readable callback so the response arrives "during" the txn,
        # completing the pending completion before wait() is reached.
        def driving_write(fd, data):
            order.append("write")
            # pending must already be registered (before write) for a fast reply to match.
            assert cfs._pending is not None
            cfs._handle_readable(eventtime=1.0)
            return len(data)

        fake_os = make_fake_os(read_chunks=[frame], open_fd=11)
        fake_os.write = driving_write
        fake_t = make_fake_termios()
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "_HAS_POSIX_SERIAL", True), \
             mock.patch.object(creality_cfs, "os", fake_os), \
             mock.patch.object(creality_cfs, "termios", fake_t), \
             mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._connect_serial()
            req = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE, bytes([0x00]))
            raw = cfs._txn(req, timeout=0.1, match=(0x01, CMD_GET_BOX_STATE))

        assert raw == frame
        # pending cleared after the exchange
        assert cfs._pending is None
        assert cfs._pending_match is None
        assert order == ["write"]

    def test_writes_the_request_bytes(self):
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_GET_BOX_STATE, bytes([0x1A, 0x20]))
        sink = []

        def driving_write(fd, data):
            sink.append(bytes(data))
            cfs._handle_readable(eventtime=1.0)
            return len(data)

        fake_os = make_fake_os(read_chunks=[frame], open_fd=11)
        fake_os.write = driving_write
        fake_t = make_fake_termios()
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "_HAS_POSIX_SERIAL", True), \
             mock.patch.object(creality_cfs, "os", fake_os), \
             mock.patch.object(creality_cfs, "termios", fake_t), \
             mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._connect_serial()
            req = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE, bytes([0x00]))
            cfs._txn(req, timeout=0.1, match=(0x01, CMD_GET_BOX_STATE))

        assert sink == [req]

    def test_returns_none_on_timeout_when_no_frame_arrives(self):
        """If nothing completes the pending completion, wait() returns the default None."""
        cfs = _bare_cfs()
        # os.write does NOT drive a response, and read returns nothing.
        fake_os = make_fake_os(read_chunks=[], write_sink=[], open_fd=11)
        fake_t = make_fake_termios()
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "_HAS_POSIX_SERIAL", True), \
             mock.patch.object(creality_cfs, "os", fake_os), \
             mock.patch.object(creality_cfs, "termios", fake_t), \
             mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._connect_serial()
            req = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE, bytes([0x00]))
            raw = cfs._txn(req, timeout=0.05, match=(0x01, CMD_GET_BOX_STATE))

        assert raw is None
        assert cfs._pending is None
        assert cfs._pending_match is None

    def test_returns_write_error_sentinel_on_oserror(self):
        """When os.write raises OSError, _txn returns the _TXN_WRITE_ERROR sentinel
        and clears the pending so the bus is not left half-armed."""
        cfs = _bare_cfs()
        fake_os = make_fake_os(open_fd=11, write_raises=OSError("bus write failed"))
        fake_t = make_fake_termios()
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "_HAS_POSIX_SERIAL", True), \
             mock.patch.object(creality_cfs, "os", fake_os), \
             mock.patch.object(creality_cfs, "termios", fake_t), \
             mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._connect_serial()
            req = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE, bytes([0x00]))
            raw = cfs._txn(req, timeout=0.1, match=(0x01, CMD_GET_BOX_STATE))

        assert raw is cfs._TXN_WRITE_ERROR
        assert cfs._pending is None
        assert cfs._pending_match is None


# ===========================================================================
# Read path: _handle_readable / _parse_rx / _dispatch_rx
# ===========================================================================

class TestReadPath:
    def _armed(self, cfs, match):
        """Arm a pending completion (as _txn would) and return it."""
        comp = cfs.reactor.completion()
        cfs._pending = comp
        cfs._pending_match = match
        cfs._rx_buf = bytearray()
        cfs._fd = 11
        return comp

    def test_complete_frame_in_one_read_completes_pending(self):
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_GET_BOX_STATE, bytes([0x1A, 0x20]))
        comp = self._armed(cfs, (0x01, CMD_GET_BOX_STATE))
        fake_os = make_fake_os(read_chunks=[frame])
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._handle_readable(eventtime=1.0)
        assert comp.test() is True
        assert comp._value == frame
        # pending consumed
        assert cfs._pending is None

    def test_partial_frame_then_remainder_assembles_and_completes(self):
        """A frame split across two reads is buffered and completed only when whole."""
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_GET_VERSION_SN, b"V1234")
        comp = self._armed(cfs, (0x01, CMD_GET_VERSION_SN))
        head = frame[:4]   # partial: HEAD ADDR LEN STATUS (not enough)
        rest = frame[4:]

        # First read: only the partial -> nothing completes yet.
        fake_os1 = make_fake_os(read_chunks=[head])
        with mock.patch.object(creality_cfs, "os", fake_os1):
            cfs._handle_readable(eventtime=1.0)
        assert comp.test() is False
        assert cfs._pending is comp        # still armed
        assert len(cfs._rx_buf) == len(head)

        # Second read: the remainder -> frame assembles and completes.
        fake_os2 = make_fake_os(read_chunks=[rest])
        with mock.patch.object(creality_cfs, "os", fake_os2):
            cfs._handle_readable(eventtime=2.0)
        assert comp.test() is True
        assert comp._value == frame

    def test_bad_length_byte_resyncs_to_next_header(self):
        """A 0xF7 with an implausible LEN is skipped; the real frame after it completes."""
        cfs = _bare_cfs()
        good = _good_frame(0x01, CMD_GET_BOX_STATE, bytes([0x1A, 0x20]))
        # Leading noise: a stray 0xF7 with an impossible LEN field (0xFF > MAX_DATA_LEN+3),
        # then the genuine frame. The parser must resync past the bad header byte.
        noisy = bytes([PACK_HEAD, 0x01, 0xFF]) + good
        comp = self._armed(cfs, (0x01, CMD_GET_BOX_STATE))
        fake_os = make_fake_os(read_chunks=[noisy])
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._handle_readable(eventtime=1.0)
        assert comp.test() is True
        assert comp._value == good

    def test_leading_noise_before_header_is_dropped(self):
        cfs = _bare_cfs()
        good = _good_frame(0x01, CMD_CUT_STATE, bytes([0x00]))
        noisy = b"\x00\x11\x22" + good   # junk before the 0xF7 header
        comp = self._armed(cfs, (0x01, CMD_CUT_STATE))
        fake_os = make_fake_os(read_chunks=[noisy])
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._handle_readable(eventtime=1.0)
        assert comp.test() is True
        assert comp._value == good

    def test_unmatched_addr_frame_is_dropped(self):
        """A correctly framed reply from a DIFFERENT address must not complete us."""
        cfs = _bare_cfs()
        wrong = _good_frame(0x02, CMD_GET_BOX_STATE, bytes([0x1A, 0x20]))
        comp = self._armed(cfs, (0x01, CMD_GET_BOX_STATE))  # we want addr 0x01
        fake_os = make_fake_os(read_chunks=[wrong])
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._handle_readable(eventtime=1.0)
        assert comp.test() is False
        # still armed, waiting for the right reply
        assert cfs._pending is comp

    def test_unmatched_func_frame_is_dropped(self):
        cfs = _bare_cfs()
        wrong = _good_frame(0x01, CMD_GET_VERSION_SN, b"X")
        comp = self._armed(cfs, (0x01, CMD_GET_BOX_STATE))  # we want func 0x0A
        fake_os = make_fake_os(read_chunks=[wrong])
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._handle_readable(eventtime=1.0)
        assert comp.test() is False
        assert cfs._pending is comp

    def test_frame_arriving_with_no_pending_is_dropped_silently(self):
        """A late/unsolicited frame with no waiter is dropped without error."""
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_GET_BOX_STATE, bytes([0x1A, 0x20]))
        cfs._pending = None
        cfs._pending_match = None
        cfs._rx_buf = bytearray()
        cfs._fd = 11
        fake_os = make_fake_os(read_chunks=[frame])
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._handle_readable(eventtime=1.0)  # must not raise
        # buffer drained, nothing pending
        assert cfs._pending is None

    def test_handle_readable_noop_when_fd_none(self):
        cfs = _bare_cfs()
        cfs._fd = None
        # os.read should never be called; a fake that would raise proves it.
        fake_os = make_fake_os()
        fake_os.read = mock.MagicMock(side_effect=AssertionError("read must not run"))
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._handle_readable(eventtime=1.0)  # returns immediately

    def test_handle_readable_swallows_blocking_io_error(self):
        cfs = _bare_cfs()
        cfs._fd = 11
        cfs._rx_buf = bytearray()
        fake_os = make_fake_os()
        fake_os.read = mock.MagicMock(side_effect=BlockingIOError())
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._handle_readable(eventtime=1.0)  # must not raise

    def test_handle_readable_returns_on_empty_read(self):
        cfs = _bare_cfs()
        cfs._fd = 11
        cfs._rx_buf = bytearray(b"\xf7")  # pre-seed; empty read must not touch it
        fake_os = make_fake_os(read_chunks=[b""])
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._handle_readable(eventtime=1.0)
        assert bytes(cfs._rx_buf) == b"\xf7"


# ===========================================================================
# _quiesce / _disconnect_serial (shutdown path)
# ===========================================================================

class TestShutdownPath:
    def test_quiesce_sets_shutdown_and_aborts_parked_pending_with_none(self):
        cfs = _bare_cfs()
        comp = cfs.reactor.completion()
        cfs._pending = comp
        cfs._pending_match = (0x01, CMD_GET_BOX_STATE)
        cfs._quiesce()
        assert cfs._shutdown is True
        # the parked waiter is completed with None so a blocked greenlet wakes
        assert comp.test() is True
        assert comp._value is None
        assert cfs._pending is None
        assert cfs._pending_match is None

    def test_quiesce_no_pending_is_safe(self):
        cfs = _bare_cfs()
        cfs._pending = None
        cfs._quiesce()  # must not raise
        assert cfs._shutdown is True

    def test_quiesce_does_not_recomplete_already_done_pending(self):
        cfs = _bare_cfs()
        comp = cfs.reactor.completion()
        comp.complete("already")     # mark done
        cfs._pending = comp
        cfs._quiesce()
        # value untouched since comp.test() was already True
        assert comp._value == "already"

    def test_disconnect_unregisters_fd_and_closes(self):
        cfs = _bare_cfs()
        cfs._fd = 11
        cfs._fd_handle = ("fd-handle", 11)
        fake_os = make_fake_os()
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._disconnect_serial()
        assert cfs.reactor.unregister_called is True
        assert 11 in fake_os._record["closed"]
        assert cfs._fd is None
        assert cfs._fd_handle is None
        assert cfs.is_connected is False

    def test_disconnect_swallows_close_oserror(self):
        cfs = _bare_cfs()
        cfs._fd = 11
        cfs._fd_handle = None
        fake_os = make_fake_os()
        fake_os.close = mock.MagicMock(side_effect=OSError("already closed"))
        with mock.patch.object(creality_cfs, "os", fake_os):
            cfs._disconnect_serial()  # must not raise
        assert cfs._fd is None

    def test_disconnect_swallows_unregister_error(self):
        cfs = _bare_cfs()
        cfs._fd = None
        cfs._fd_handle = ("fd-handle", 11)
        cfs.reactor.unregister_fd = mock.MagicMock(side_effect=Exception("reactor gone"))
        cfs._disconnect_serial()  # must not raise
        assert cfs._fd_handle is None


# ===========================================================================
# End-to-end through the public API with the REAL transport (no _txn mock)
# ===========================================================================

class TestPublicApiThroughRealTransport:
    """Drive public commands through _send_command -> real _txn -> real read path.

    A single canned frame is delivered by having the patched os.write drive
    _handle_readable. This asserts both the transport AND the command decode.
    """

    def _run(self, cfs, response_frame, call):
        """Connect, arrange os.write to deliver `response_frame`, then run `call`."""
        def driving_write(fd, data):
            # deliver the canned frame on each write so multi-step sequences progress
            cfs._rx_buf += response_frame
            cfs._parse_rx(eventtime=1.0)
            return len(data)

        fake_os = make_fake_os(open_fd=11)
        fake_os.write = driving_write
        fake_t = make_fake_termios()
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "_HAS_POSIX_SERIAL", True), \
             mock.patch.object(creality_cfs, "os", fake_os), \
             mock.patch.object(creality_cfs, "termios", fake_t), \
             mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._connect_serial()
            return call()

    def test_get_box_state_decodes_loaded(self):
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_GET_BOX_STATE,
                            bytes([BOX_STATE_CLASS_BYTE, BOX_STATE_LO_LOADED]))
        result = self._run(cfs, frame, lambda: cfs.get_box_state(0x01))
        assert result["state"] == BOX_STATE_LO_LOADED
        assert result["state_str"] == "LOADED"
        assert result["class_byte"] == BOX_STATE_CLASS_BYTE
        assert result["addr"] == 0x01

    def test_get_box_state_decodes_feeding(self):
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_GET_BOX_STATE,
                            bytes([BOX_STATE_CLASS_BYTE, BOX_STATE_LO_FEEDING]))
        result = self._run(cfs, frame, lambda: cfs.get_box_state(0x01))
        assert result["state_str"] == "FEEDING"

    def test_get_version_sn_decodes_ascii(self):
        cfs = _bare_cfs()
        ver = b"11010000843215B625AHSC"
        frame = _good_frame(0x01, CMD_GET_VERSION_SN, ver)
        result = self._run(cfs, frame, lambda: cfs.get_version_sn(0x01))
        assert result == ver.decode("ascii")

    def test_get_version_info_decodes_firmware_string(self):
        cfs = _bare_cfs()
        fw = b"cfs0_050_G32-cfs0_000_113"
        frame = _good_frame(0x01, CMD_VERSION_INFO, fw)
        result = self._run(cfs, frame, lambda: cfs.get_version_info(0x01))
        assert result == fw.decode("ascii")

    def test_set_box_mode_ack_returns_true(self):
        cfs = _bare_cfs()
        # ACK uses STATUS=0x00 (STATUS_ADDRESSING); set_box_mode returns True on that.
        frame = build_message(0x01, STATUS_ADDRESSING, CMD_SET_BOX_MODE)
        result = self._run(cfs, frame, lambda: cfs.set_box_mode(0x01, 0x00, 0x01))
        assert result is True

    def test_set_pre_loading_ack_returns_true(self):
        cfs = _bare_cfs()
        frame = build_message(0x01, STATUS_ADDRESSING, CMD_SET_PRE_LOADING)
        result = self._run(cfs, frame, lambda: cfs.set_pre_loading(0x01, 0x0F, 1))
        assert result is True

    def test_get_hardware_status_returns_flag_byte(self):
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_GET_HARDWARE_STATUS, bytes([0x07]))
        result = self._run(cfs, frame, lambda: cfs.get_hardware_status(0x01, 0x01))
        assert result == 0x07

    def test_cut_state_true_on_done(self):
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_CUT_STATE, bytes([CUT_STATE_DONE]))
        result = self._run(cfs, frame, lambda: cfs.cut_state(0x01))
        assert result is True

    def test_cut_state_false_on_set(self):
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_CUT_STATE, bytes([CUT_STATE_SET]))
        result = self._run(cfs, frame, lambda: cfs.cut_state(0x01))
        assert result is False

    def test_ctrl_connection_motor_action_engage_returns_true(self):
        cfs = _bare_cfs()
        frame = _good_frame(0x01, CMD_CTRL_CONNECTION_MOTOR_ACTION, bytes([MOTOR_ACTION_ENGAGE]))
        result = self._run(cfs, frame, lambda: cfs.ctrl_connection_motor_action(0x01, True))
        assert result is True

    def test_measuring_wheel_returns_raw_word(self):
        cfs = _bare_cfs()
        word = bytes([0xC5, 0x00, 0x12, 0x34])
        frame = _good_frame(0x01, CMD_MEASURING_WHEEL, word)
        result = self._run(cfs, frame, lambda: cfs.measuring_wheel(0x01))
        assert result == word

    def test_retrude_buffer_node_single_channel_ack(self):
        cfs = _bare_cfs()
        # Buffer-node frames echo addr 0x81, status 0x00.
        frame = build_message(ADDR_BUFFER_NODE, STATUS_ADDRESSING, CMD_RETRUDE_PROCESS)
        result = self._run(
            cfs, frame, lambda: cfs.retrude_process(ADDR_BUFFER_NODE, slot=0x01)
        )
        assert result is True

    def test_send_command_raises_when_not_connected(self):
        cfs = _bare_cfs()
        cfs.is_connected = False
        cfs._fd = None
        with pytest.raises(RuntimeError, match="not connected"):
            cfs._send_command(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE, b"")

    def test_send_command_returns_none_when_shutdown(self):
        cfs = _bare_cfs()
        cfs.is_connected = True
        cfs._fd = 11
        cfs._shutdown = True
        assert cfs._send_command(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE, b"") is None

    def test_send_command_breaks_retry_on_write_error_sentinel(self):
        """A write OSError (sentinel) stops the retry loop and yields None to the caller."""
        cfs = _bare_cfs()
        cfs.retry_count = 3
        fake_os = make_fake_os(open_fd=11, write_raises=OSError("dead bus"))
        fake_t = make_fake_termios()
        fake_f = make_fake_fcntl()
        with mock.patch.object(creality_cfs, "_HAS_POSIX_SERIAL", True), \
             mock.patch.object(creality_cfs, "os", fake_os), \
             mock.patch.object(creality_cfs, "termios", fake_t), \
             mock.patch.object(creality_cfs, "fcntl", fake_f):
            cfs._connect_serial()
            result = cfs._send_command(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE, b"")
        assert result is None
