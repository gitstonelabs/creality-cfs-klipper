"""
test_stubs.py — Tests for stubbed commands CMD_EXTRUDE_PROCESS (0x10) and
CMD_RETRUDE_PROCESS (0x11) in CrealityCFS.

These commands raise NotImplementedError because their payload format is locked
inside the Creality binary firmware (.so) and could not be recovered during
reverse engineering.

Tests verify:
  1. NotImplementedError is raised
  2. The error message includes the command code (0x10 / 0x11)
  3. The error message includes actionable guidance about RS485 capture

The tests do NOT require a live serial connection — the stub raises before
any I/O is attempted.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from creality_cfs import CrealityCFS


# ---------------------------------------------------------------------------
# Helper: fake instance that bypasses __init__
# ---------------------------------------------------------------------------

class _FakeCFS:
    """Minimal stub instance to bind unbound methods for testing stubs."""
    pass


# ===========================================================================
# CMD_EXTRUDE_PROCESS (0x10)
# ===========================================================================

class TestExtrudeProcessStub:
    """Tests for CrealityCFS.extrude_process() (CMD_EXTRUDE_PROCESS, 0x10)."""

    def test_extrude_process_raises_not_implemented_error(self):
        """extrude_process() always raises NotImplementedError.

        The payload format for 0x10 is locked in the Creality .so binary.
        Calling this method must fail loudly rather than sending garbage bytes
        that could corrupt the RS485 bus.
        """
        instance = _FakeCFS()
        with pytest.raises(NotImplementedError):
            CrealityCFS.extrude_process(instance, 0x01)

    def test_extrude_process_error_message_contains_0x10_command_code(self):
        """extrude_process() error message references command code 0x10."""
        instance = _FakeCFS()
        with pytest.raises(NotImplementedError) as exc_info:
            CrealityCFS.extrude_process(instance, 0x01)
        assert "0x10" in str(exc_info.value), (
            f"Error message should reference 0x10, got: {exc_info.value}"
        )

    def test_extrude_process_error_message_contains_capture_guidance(self):
        """extrude_process() error message contains actionable capture guidance.

        The engineer reading this error should be told how to obtain the
        payload format (capture RS485 during tool-change).
        """
        instance = _FakeCFS()
        with pytest.raises(NotImplementedError) as exc_info:
            CrealityCFS.extrude_process(instance, 0x01)
        msg = str(exc_info.value).lower()
        # Should mention RS485 capture or tool-change
        assert any(kw in msg for kw in ["rs485", "capture", "payload", "tool"]), (
            f"Error message lacks capture guidance: {exc_info.value}"
        )

    def test_extrude_process_raises_regardless_of_addr_argument(self):
        """extrude_process() raises NotImplementedError for any addr value."""
        instance = _FakeCFS()
        for addr in [0x00, 0x01, 0x04, 0xFF]:
            with pytest.raises(NotImplementedError):
                CrealityCFS.extrude_process(instance, addr)

    def test_extrude_process_raises_regardless_of_extra_kwargs(self):
        """extrude_process() raises NotImplementedError even with extra kwargs."""
        instance = _FakeCFS()
        with pytest.raises(NotImplementedError):
            CrealityCFS.extrude_process(instance, 0x01, extra_param=42)


# ===========================================================================
# CMD_RETRUDE_PROCESS (0x11)
# ===========================================================================

class TestRetrudeProcessStub:
    """Tests for CrealityCFS.retrude_process() (CMD_RETRUDE_PROCESS, 0x11)."""

    def test_retrude_process_raises_not_implemented_error(self):
        """retrude_process() always raises NotImplementedError.

        Same limitation as extrude_process() — payload unknown.
        """
        instance = _FakeCFS()
        with pytest.raises(NotImplementedError):
            CrealityCFS.retrude_process(instance, 0x01)

    def test_retrude_process_error_message_contains_0x11_command_code(self):
        """retrude_process() error message references command code 0x11."""
        instance = _FakeCFS()
        with pytest.raises(NotImplementedError) as exc_info:
            CrealityCFS.retrude_process(instance, 0x01)
        assert "0x11" in str(exc_info.value), (
            f"Error message should reference 0x11, got: {exc_info.value}"
        )

    def test_retrude_process_error_message_contains_capture_guidance(self):
        """retrude_process() error message contains actionable capture guidance."""
        instance = _FakeCFS()
        with pytest.raises(NotImplementedError) as exc_info:
            CrealityCFS.retrude_process(instance, 0x01)
        msg = str(exc_info.value).lower()
        assert any(kw in msg for kw in ["rs485", "capture", "payload", "tool"]), (
            f"Error message lacks capture guidance: {exc_info.value}"
        )

    def test_retrude_process_raises_regardless_of_addr_argument(self):
        """retrude_process() raises NotImplementedError for any addr value."""
        instance = _FakeCFS()
        for addr in [0x00, 0x01, 0x04, 0xFF]:
            with pytest.raises(NotImplementedError):
                CrealityCFS.retrude_process(instance, addr)

    def test_retrude_process_raises_regardless_of_extra_args(self):
        """retrude_process() raises NotImplementedError even with positional args."""
        instance = _FakeCFS()
        with pytest.raises(NotImplementedError):
            CrealityCFS.retrude_process(instance, 0x01, b'\x01\x02', extra=True)

    def test_extrude_and_retrude_are_distinct_stubs_with_different_error_messages(self):
        """extrude_process and retrude_process have separate, distinct error messages.

        Both stubs must clearly identify WHICH command is unimplemented, so an
        engineer debugging a crash log can distinguish them without context.
        """
        instance = _FakeCFS()
        try:
            CrealityCFS.extrude_process(instance, 0x01)
        except NotImplementedError as e:
            extrude_msg = str(e)

        try:
            CrealityCFS.retrude_process(instance, 0x01)
        except NotImplementedError as e:
            retrude_msg = str(e)

        # They must not be identical strings
        assert extrude_msg != retrude_msg, (
            "extrude_process and retrude_process should have distinct error messages"
        )
        # Each must identify its own command code
        assert "0x10" in extrude_msg
        assert "0x11" in retrude_msg
