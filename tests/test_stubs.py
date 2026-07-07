# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_stubs.py

Tests for the 0x10 EXTRUDE / 0x11 RETRUDE implementations.

v1.4.0 REWRITE: these tests now validate the hardware-validated choreography model
(the sensor-gated push loop and the START/FINISH unload pair) instead of the
wire-disproven fixed ramp / position-settle / back-to-back-retrude behavior the
pre-v1.4.0 suite locked in:
  * the 0x05 push reply is a BE IEEE-754 wheel float (the old [state][uint16]
    decode was a misparse),
  * the 0x06/0x07 finalize only fires after the toolhead switch latches,
  * the per-push wheel-advance watchdog breaks a self-limited arm early,
  * the retrude START/FINISH frames both carry the slot bitmask and use the
    hold-covering timeouts (start 22 s / finish 13 s),
  * the buffer-node (0x81) retrude form is wire-disproven and now rejected.
"""

import itertools
import struct

import pytest
from unittest.mock import MagicMock
from src.creality_cfs import (
    CrealityCFS,
    CMD_EXTRUDE_PROCESS,
    CMD_RETRUDE_PROCESS,
    ADDR_BUFFER_NODE,
    SLOT_T0,
    SLOT_T1,
    EXTRUDE_SUB_INIT,
    EXTRUDE_SUB_ENGAGE,
    EXTRUDE_SUB_PUSH,
    EXTRUDE_SUB_SETTLE,
    EXTRUDE_SUB_FINALIZE,
    EXTRUDE_FINALIZE_DATA,
    LOAD_TOPUP_MAX_BURSTS,
    LOAD_PUSH_STALL_LIMIT,
    RETRUDE_PHASE_START,
    RETRUDE_PHASE_FINISH,
    RETRUDE_START_TIMEOUT_S,
    RETRUDE_FINISH_TIMEOUT_S,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_reactor():
    """Return a reactor stub whose monotonic() advances 1s per call.

    The choreography wall budgets are measured on self.reactor.monotonic() (the
    clock _send_command yields the greenlet against); a monotonic() that advances
    makes the loops terminate promptly without real sleeps.
    """
    reactor = MagicMock()
    counter = itertools.count()
    reactor.monotonic.side_effect = lambda: float(next(counter))
    return reactor


def _wheel_frame(mm):
    """A fake parsed 0x05 push reply carrying the BE float wheel word."""
    return {"data": struct.pack(">f", mm), "status": 0x00}


def make_cfs_with_mock_send(send_return=None):
    """Create a bare CrealityCFS instance with _send_command mocked out."""
    instance = object.__new__(CrealityCFS)
    instance._send_command = MagicMock(return_value=send_return)
    instance.is_connected = True
    instance.reactor = _stub_reactor()
    # Choreography attributes normally set from config in __init__:
    instance.load_wall_budget = 90.0
    instance.load_max_bursts = LOAD_TOPUP_MAX_BURSTS
    instance.filament_sensor_name = "filament_sensor"
    # Bare printer with no optional objects (no toolhead filament switch).
    printer = MagicMock()
    printer.lookup_object.side_effect = lambda name, default=None: default
    instance.printer = printer
    return instance


def _sent_payloads(instance):
    """Extract the data payloads of every _send_command call, in order."""
    payloads = []
    for call in instance._send_command.call_args_list:
        data = call.kwargs.get("data")
        if data is None and len(call.args) > 3:
            data = call.args[3]
        payloads.append(bytes(data) if data is not None else b"")
    return payloads


# ---------------------------------------------------------------------------
# CMD_EXTRUDE_PROCESS (0x10): sensor-gated load
# ---------------------------------------------------------------------------

class TestExtrudeProcess:

    def test_extrude_process_returns_dict(self):
        """extrude_process() returns a dict with the v1.4.0 keys."""
        instance = make_cfs_with_mock_send(send_return=None)
        result = instance.extrude_process(0x01)
        assert isinstance(result, dict)
        assert "latched" in result
        assert "cycles" in result
        assert "have_sensor" in result

    def test_extrude_process_sensorless_runs_one_cycle(self):
        """Without a toolhead filament switch the load runs exactly ONE ungated cycle
        (it cannot know when filament arrives) and reports latched=False."""
        instance = make_cfs_with_mock_send(send_return={"data": b"", "status": 0x00})
        result = instance.extrude_process(0x01, slot=SLOT_T0)
        assert result["have_sensor"] is False
        assert result["cycles"] == 1
        assert result["latched"] is False

    def test_extrude_process_invalid_addr_raises_value_error(self):
        instance = make_cfs_with_mock_send()
        with pytest.raises(ValueError):
            instance.extrude_process(0x00)
        with pytest.raises(ValueError):
            instance.extrude_process(0x05)

    def test_extrude_process_invalid_slot_raises_value_error(self):
        instance = make_cfs_with_mock_send()
        with pytest.raises(ValueError):
            instance.extrude_process(0x01, slot=0x03)   # not a 1-hot bitmask

    def test_extrude_process_uses_correct_command_code(self):
        instance = make_cfs_with_mock_send(send_return=None)
        instance.extrude_process(0x01)
        cmd_codes = [c[0][2] for c in instance._send_command.call_args_list]
        assert CMD_EXTRUDE_PROCESS in cmd_codes

    def test_extrude_cycle_starts_with_init_then_engage(self):
        """The gated cycle opens with [slot] 00 00 (init/arm) then [slot] 04 00 (engage);
        every stage frame carries THREE data bytes."""
        instance = make_cfs_with_mock_send(send_return=None)
        instance.extrude_process(0x01, slot=SLOT_T1)
        payloads = _sent_payloads(instance)
        assert payloads[0] == bytes([SLOT_T1, EXTRUDE_SUB_INIT, 0x00])
        assert payloads[1] == bytes([SLOT_T1, EXTRUDE_SUB_ENGAGE, 0x00])

    def test_extrude_no_finalize_without_switch(self):
        """SETTLE (0x06) / FINALIZE (0x07 03) are GATED on the toolhead switch: with no
        sensor they must never be issued (the wire-disproven fixed ramp always fired
        them)."""
        instance = make_cfs_with_mock_send(send_return={"data": b"", "status": 0x00})
        instance.extrude_process(0x01, slot=SLOT_T0)
        payloads = _sent_payloads(instance)
        stage_bytes = [p[1] for p in payloads if len(p) >= 2]
        assert EXTRUDE_SUB_SETTLE not in stage_bytes
        assert EXTRUDE_SUB_FINALIZE not in stage_bytes

    def test_gated_ramp_finalizes_after_switch_latches(self):
        """extrude_load_ramp_gated(): once sensor_fn() latches True, the 0x05 push loop
        exits and 0600 + 0703 are issued (the finalize carries data byte 0x03)."""
        instance = make_cfs_with_mock_send(send_return={"data": b"", "status": 0x00})
        sensor_reads = iter([False, False, True, True, True])
        latched = instance.extrude_load_ramp_gated(
            0x01, SLOT_T0,
            sensor_fn=lambda: next(sensor_reads, True),
            deadline_fn=lambda: 60.0)
        assert latched is True
        payloads = _sent_payloads(instance)
        assert payloads[-2] == bytes([SLOT_T0, EXTRUDE_SUB_SETTLE, 0x00])
        assert payloads[-1] == bytes([SLOT_T0, EXTRUDE_SUB_FINALIZE, EXTRUDE_FINALIZE_DATA])

    def test_gated_ramp_push_watchdog_breaks_on_self_limit(self):
        """The per-push wheel-advance watchdog: when consecutive pushes advance the
        wheel by ~0 (the box's per-arm self-limit fast-acks), the cycle breaks after
        LOAD_PUSH_STALL_LIMIT dead pushes instead of grinding max_pushes."""
        instance = make_cfs_with_mock_send()
        # Every push reply reports the SAME wheel value -> zero advance.
        instance._send_command.return_value = _wheel_frame(-500.0)
        latched = instance.extrude_load_ramp_gated(
            0x01, SLOT_T0,
            sensor_fn=lambda: False,
            deadline_fn=lambda: 60.0,
            max_pushes=10)
        assert latched is False
        payloads = _sent_payloads(instance)
        pushes = [p for p in payloads if len(p) >= 2 and p[1] == EXTRUDE_SUB_PUSH]
        # First push sets the baseline; each subsequent zero-advance push increments the
        # stall counter, so the loop stops at 1 + LOAD_PUSH_STALL_LIMIT pushes.
        assert len(pushes) == 1 + LOAD_PUSH_STALL_LIMIT

    def test_gated_ramp_keeps_pushing_while_wheel_advances(self):
        """Pushes whose wheel advance is real (>= LOAD_PUSH_MIN_ADVANCE) never trip the
        watchdog: the loop runs to max_pushes when the sensor stays clear."""
        instance = make_cfs_with_mock_send()
        wheel = itertools.count(0)
        instance._send_command.side_effect = (
            lambda *a, **kw: _wheel_frame(-300.0 * next(wheel)))
        latched = instance.extrude_load_ramp_gated(
            0x01, SLOT_T0,
            sensor_fn=lambda: False,
            deadline_fn=lambda: 60.0,
            max_pushes=4)
        assert latched is False
        payloads = _sent_payloads(instance)
        pushes = [p for p in payloads if len(p) >= 2 and p[1] == EXTRUDE_SUB_PUSH]
        assert len(pushes) == 4

    def test_extrude_wheel_decodes_be_float(self):
        """_extrude_wheel(): the 0x05 push reply payload is a 4-byte BE IEEE-754 float
        (the pre-v1.4.0 [state][uint16] model was a misparse)."""
        assert CrealityCFS._extrude_wheel(_wheel_frame(-1230.18)) == pytest.approx(
            -1230.18, abs=0.01)

    def test_extrude_wheel_none_for_short_ack(self):
        """Bare stage ACKs (no 4-byte payload) decode to None, not a bogus value."""
        assert CrealityCFS._extrude_wheel({"data": b"", "status": 0x00}) is None
        assert CrealityCFS._extrude_wheel(None) is None


# ---------------------------------------------------------------------------
# CMD_RETRUDE_PROCESS (0x11): START/FINISH pair
# ---------------------------------------------------------------------------

class TestRetrudeProcess:

    def test_retrude_process_returns_bool(self):
        instance = make_cfs_with_mock_send(send_return={"data": b"", "status": 0x00})
        result = instance.retrude_process(0x01)
        assert isinstance(result, bool)

    def test_retrude_process_returns_true_on_ack(self):
        instance = make_cfs_with_mock_send(send_return={"data": b"", "status": 0x00})
        assert instance.retrude_process(0x01) is True

    def test_retrude_process_returns_false_on_no_response(self):
        instance = make_cfs_with_mock_send(send_return=None)
        assert instance.retrude_process(0x01) is False

    def test_retrude_process_invalid_addr_raises_value_error(self):
        instance = make_cfs_with_mock_send()
        with pytest.raises(ValueError):
            instance.retrude_process(0x00)
        with pytest.raises(ValueError):
            instance.retrude_process(0x05)

    def test_retrude_buffer_node_form_removed(self):
        """v1.4.0: the 'buffer node 0x81 single-byte retrude' is wire-disproven (that
        traffic is FOC-servo frames on the shared bus, not a CFS retrude) -- the addr
        is now rejected instead of emitting a bogus frame."""
        instance = make_cfs_with_mock_send()
        with pytest.raises(ValueError):
            instance.retrude_process(ADDR_BUFFER_NODE, slot=0x01)

    def test_retrude_process_uses_correct_command_code(self):
        instance = make_cfs_with_mock_send(send_return=None)
        instance.retrude_process(0x01)
        cmd_code = instance._send_command.call_args[0][2]
        assert cmd_code == CMD_RETRUDE_PROCESS

    def test_retrude_process_sends_start_with_slot_bitmask(self):
        """The START frame is [slot][0x00] -- the slot bitmask rides in BOTH frames."""
        instance = make_cfs_with_mock_send(send_return=None)
        instance.retrude_process(0x01, slot=SLOT_T1)
        payloads = _sent_payloads(instance)
        assert payloads[0] == bytes([SLOT_T1, RETRUDE_PHASE_START])

    def test_retrude_process_aborts_when_start_unacked(self):
        """A silent START aborts before the FINISH frame (one call total)."""
        instance = make_cfs_with_mock_send(send_return=None)
        instance.retrude_process(0x01)
        assert instance._send_command.call_count == 1

    def test_retrude_process_sends_both_phases_when_acked(self):
        """START [slot,00] then FINISH [slot,01] when both ACK."""
        instance = make_cfs_with_mock_send(send_return={"data": b"", "status": 0x00})
        instance.retrude_process(0x01, slot=SLOT_T1)
        payloads = _sent_payloads(instance)
        assert payloads == [bytes([SLOT_T1, RETRUDE_PHASE_START]),
                            bytes([SLOT_T1, RETRUDE_PHASE_FINISH])]

    def test_retrude_timeouts_cover_the_held_acks(self):
        """The START frame gets the 22 s pull timeout and the FINISH frame the 13 s
        hold timeout (the box HOLDS the finish ACK ~9.6 s on a real pull; the old
        0.5 s timeout could never see it, so unloads could never confirm)."""
        instance = make_cfs_with_mock_send(send_return={"data": b"", "status": 0x00})
        instance.retrude_process(0x01, slot=SLOT_T0)
        timeouts = [c.kwargs.get("timeout")
                    for c in instance._send_command.call_args_list]
        assert timeouts == [RETRUDE_START_TIMEOUT_S, RETRUDE_FINISH_TIMEOUT_S]
