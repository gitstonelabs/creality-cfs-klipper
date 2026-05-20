"""
tests/test_stubs.py

Tests for previously-stubbed commands that are now fully implemented
in v1.1.0. These tests validate the real implementations rather than
checking for NotImplementedError.
"""

import pytest
from unittest.mock import MagicMock, patch
from src.creality_cfs import CrealityCFS, CMD_EXTRUDE_PROCESS, CMD_RETRUDE_PROCESS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_cfs_with_mock_send(send_return=None):
    """Create a CrealityCFS instance with _send_command mocked out."""
    instance = object.__new__(CrealityCFS)
    instance._send_command = MagicMock(return_value=send_return)
    instance.is_connected = True
    return instance


# ---------------------------------------------------------------------------
# CMD_EXTRUDE_PROCESS (0x10) — real implementation tests
# ---------------------------------------------------------------------------

class TestExtrudeProcess:

    def test_extrude_process_returns_dict(self):
        """extrude_process() returns a dict with expected keys."""
        # Mock: INIT returns ok, POLL returns ACK, STREAM returns no response
        def side_effect(addr, status, cmd, data, **kwargs):
            sub = data[1] if len(data) > 1 else 0
            if sub == 0x00:  # INIT
                return {"data": bytes([0x00])}
            return None  # POLL and STREAM timeout

        instance = make_cfs_with_mock_send()
        instance._send_command.side_effect = side_effect

        result = instance.extrude_process(0x01)

        assert isinstance(result, dict)
        assert "init_ok" in result
        assert "final_pos" in result
        assert "final_state" in result
        assert "polls" in result

    def test_extrude_process_init_ok_true_on_success(self):
        """init_ok=True when INIT sub-command returns status 0x00."""
        def side_effect(addr, status, cmd, data, **kwargs):
            if data[1] == 0x00:
                return {"data": bytes([0x00])}
            return None

        instance = make_cfs_with_mock_send()
        instance._send_command.side_effect = side_effect

        result = instance.extrude_process(0x01)
        assert result["init_ok"] is True

    def test_extrude_process_init_ok_false_on_no_response(self):
        """init_ok=False when INIT sub-command returns no response."""
        instance = make_cfs_with_mock_send(send_return=None)
        result = instance.extrude_process(0x01)
        assert result["init_ok"] is False

    def test_extrude_process_polls_stream_and_reports_position(self):
        """STREAM sub-command responses are decoded into final_pos."""
        call_count = [0]

        def side_effect(addr, status, cmd, data, **kwargs):
            sub = data[1] if len(data) > 1 else 0
            if sub == 0x00:  # INIT
                return {"data": bytes([0x00])}
            if sub == 0x04:  # POLL
                return {"data": b""}
            if sub == 0x05:  # STREAM
                call_count[0] += 1
                # pos = 40000 = 400.00mm, state = 0xC4 (SPEED)
                return {"data": bytes([0xC4, 0x9C, 0x40])}
            return None

        instance = make_cfs_with_mock_send()
        instance._send_command.side_effect = side_effect

        result = instance.extrude_process(0x01)
        assert result["final_pos"] == pytest.approx(400.00, abs=1.0)
        assert result["final_state"] == 0xC4
        assert result["polls"] > 0

    def test_extrude_process_invalid_addr_raises_value_error(self):
        """Out-of-range address raises ValueError."""
        instance = make_cfs_with_mock_send()
        with pytest.raises(ValueError):
            instance.extrude_process(0x00)
        with pytest.raises(ValueError):
            instance.extrude_process(0x05)

    def test_extrude_process_valid_addrs_accepted(self):
        """Addresses 0x01-0x04 are accepted without ValueError."""
        instance = make_cfs_with_mock_send(send_return=None)
        for addr in [0x01, 0x02, 0x03, 0x04]:
            # Should not raise — returns dict even with no serial response
            result = instance.extrude_process(addr)
            assert isinstance(result, dict)

    def test_extrude_process_uses_correct_command_code(self):
        """_send_command is called with CMD_EXTRUDE_PROCESS (0x10)."""
        instance = make_cfs_with_mock_send(send_return=None)
        instance.extrude_process(0x01)
        calls = instance._send_command.call_args_list
        cmd_codes = [c[0][2] for c in calls]  # positional arg index 2 = func
        assert CMD_EXTRUDE_PROCESS in cmd_codes

    def test_extrude_process_sends_init_subcommand(self):
        """First call sends INIT sub-command 0x02/0x00."""
        instance = make_cfs_with_mock_send(send_return=None)
        instance.extrude_process(0x01)
        first_call_data = instance._send_command.call_args_list[0][0][3]
        assert first_call_data[0] == 0x02
        assert first_call_data[1] == 0x00  # EXTRUDE_SUB_INIT


# ---------------------------------------------------------------------------
# CMD_RETRUDE_PROCESS (0x11) — real implementation tests
# ---------------------------------------------------------------------------

class TestRetrudeProcess:

    def test_retrude_process_returns_bool(self):
        """retrude_process() returns a bool."""
        instance = make_cfs_with_mock_send(send_return={"data": b""})
        result = instance.retrude_process(0x01)
        assert isinstance(result, bool)

    def test_retrude_process_returns_true_on_ack(self):
        """Returns True when CFS acknowledges the retract command."""
        instance = make_cfs_with_mock_send(send_return={"data": b""})
        assert instance.retrude_process(0x01) is True

    def test_retrude_process_returns_false_on_no_response(self):
        """Returns False when no response received."""
        instance = make_cfs_with_mock_send(send_return=None)
        assert instance.retrude_process(0x01) is False

    def test_retrude_process_invalid_addr_raises_value_error(self):
        """Out-of-range address raises ValueError."""
        instance = make_cfs_with_mock_send()
        with pytest.raises(ValueError):
            instance.retrude_process(0x00)
        with pytest.raises(ValueError):
            instance.retrude_process(0x05)

    def test_retrude_process_valid_addrs_accepted(self):
        """Addresses 0x01-0x04 are accepted without ValueError."""
        instance = make_cfs_with_mock_send(send_return=None)
        for addr in [0x01, 0x02, 0x03, 0x04]:
            result = instance.retrude_process(addr)
            assert isinstance(result, bool)

    def test_retrude_process_uses_correct_command_code(self):
        """_send_command is called with CMD_RETRUDE_PROCESS (0x11)."""
        instance = make_cfs_with_mock_send(send_return=None)
        instance.retrude_process(0x01)
        cmd_code = instance._send_command.call_args[0][2]
        assert cmd_code == CMD_RETRUDE_PROCESS

    def test_retrude_process_sends_correct_payload(self):
        """Payload is [0x02, 0x01] as confirmed from live capture."""
        instance = make_cfs_with_mock_send(send_return=None)
        instance.retrude_process(0x01)
        data = instance._send_command.call_args[0][3]
        assert data == bytes([0x02, 0x01])

    def test_retrude_process_is_single_command(self):
        """Retrude is one-shot — only one _send_command call."""
        instance = make_cfs_with_mock_send(send_return=None)
        instance.retrude_process(0x01)
        assert instance._send_command.call_count == 1

    def test_extrude_and_retrude_use_different_command_codes(self):
        """0x10 and 0x11 are distinct command codes."""
        assert CMD_EXTRUDE_PROCESS != CMD_RETRUDE_PROCESS
        assert CMD_EXTRUDE_PROCESS == 0x10
        assert CMD_RETRUDE_PROCESS == 0x11
