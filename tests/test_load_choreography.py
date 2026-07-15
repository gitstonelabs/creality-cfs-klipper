# SPDX-License-Identifier: GPL-3.0-or-later
"""
test_load_choreography.py: End-to-end coverage of the CFS_EXTRUDE load choreography.

These tests drive the full validated load path through MockCFSHardware over the wired
_txn transport (real framed bytes, CRC, parse), exercising:

  load_process              the full M109 -> feed-mode -> engage -> sensor-gated ramp ->
                            cut check -> print mode -> release choreography, in all three
                            gate outcomes (switch latches / sensorless degraded / never trips)
  extrude_process           addr + slot validation and the gated-cycle driver
  extrude_load_ramp_gated   one sensor-gated 0x10 cycle (driven via load_process)
  extrude_stage             a single 0x10 stage frame's parsed reply
  _extrude_wheel            the 4-byte BE IEEE-754 wheel-word decode
  cmd_CFS_EXTRUDE           the G-code handler (TOOL->slot bitmask, not-connected guard)
  enter_feed_mode           0x04 [00][slot] feed-mode entry (True on ACK)
  set_print_mode            0x04 [slot][00] print-mode latch (True on ACK)
  _melt_guard / _effective_temp   the MIN_EXTRUDE_TEMP floor + blocking M109

The MockCFSHardware is stateful: a 0x05 push reply carries the advancing measuring-wheel
float; every other stage is a bare status-0x00 ACK. A closure toolhead sensor (returning
False a couple times then True) latches the load quickly so the wall-budget loop exits in
a few iterations. reactor.monotonic() is the real advancing clock from conftest.
"""

import sys
import os
import struct
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from creality_cfs import (
    CrealityCFS,
    CMD_EXTRUDE_PROCESS,
    CMD_SET_BOX_MODE,
    SLOT_T0,
    SLOT_T1,
    SLOT_BITMASKS,
    EXTRUDE_SUB_PUSH,
    MIN_EXTRUDE_TEMP,
)

from tests.mock_cfs import MockCFSHardware
from tests.conftest import make_wired_controller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wired():
    """Build a single-box wired controller, auto-address it (assigns addr 0x01), and
    return (cfs, hw). After addressing, addr 0x01 is mapped/online so operational frames
    reach a live box over the wired transport."""
    hw = MockCFSHardware(box_count=1)
    cfs, ser = make_wired_controller(hw, box_count=1, retry_count=1)
    cfs._run_auto_addressing()
    return cfs, hw


def _sequence_sensor(values):
    """Return a closure that yields each value from `values` in turn, then repeats the
    last value forever (the toolhead filament switch reading)."""
    state = {"i": 0}
    seq = list(values)

    def _read():
        i = state["i"]
        if i < len(seq):
            state["i"] = i + 1
            return seq[i]
        return seq[-1]

    return _read


def _load_gcmd(temp=250.0):
    """A fake Klipper gcmd whose only load_process input is get_float('TEMP')."""
    gcmd = mock.MagicMock()
    gcmd.get_float.side_effect = lambda key, default=None, **kw: {
        "TEMP": temp,
    }.get(key, default)
    gcmd.get_int.side_effect = lambda key, default=None, **kw: default
    gcmd.error.side_effect = lambda msg: Exception(msg)
    return gcmd


def _scripts(cfs):
    """Collect every G-code script string emitted through run_script_from_command."""
    return [c.args[0] for c in cfs.gcode.run_script_from_command.call_args_list]


# ===========================================================================
# load_process: sensor latches (the happy path)
# ===========================================================================

class TestLoadProcessSensorLatch:

    def test_load_process_switch_trips_completes_choreography(self):
        """A toolhead switch that reads False for the first couple pushes then latches True
        drives the FULL load: M109 guard, the sensor-gated 0x10 ramp fires, print mode is
        set, active_tool records the slot, and respond_info reports the switch tripped."""
        cfs, hw = _wired()
        cfs._toolhead_filament_detected = _sequence_sensor([False, False, True])
        gcmd = _load_gcmd(temp=250.0)

        cfs.load_process(gcmd, 0x01, SLOT_T0)

        # The 0x10 EXTRUDE_PROCESS choreography actually ran on the wire.
        hw.assert_command_received(CMD_EXTRUDE_PROCESS)
        # M109 melt guard was emitted before any feed.
        scripts = _scripts(cfs)
        assert any("M109" in s for s in scripts)
        # active_tool now records slot T0 (0-based tool index 0).
        assert cfs._active_tool == 0
        # respond_info confirms the switch tripped.
        infos = [c.args[0] for c in gcmd.respond_info.call_args_list]
        assert any("switch tripped" in s for s in infos)

    def test_load_process_sends_feed_and_print_mode_frames(self):
        """The load brackets the ramp with 0x04 feed-mode entry and 0x04 print-mode latch,
        so at least two SET_BOX_MODE frames land on the wire."""
        cfs, hw = _wired()
        cfs._toolhead_filament_detected = _sequence_sensor([False, True])
        gcmd = _load_gcmd()

        cfs.load_process(gcmd, 0x01, SLOT_T0)

        funcs = hw.get_received_funcs()
        assert funcs.count(CMD_SET_BOX_MODE) >= 2


# ===========================================================================
# load_process: sensorless degraded path
# ===========================================================================

class TestLoadProcessSensorless:

    def test_load_process_sensorless_runs_one_cycle_no_raise(self):
        """With no toolhead filament switch (sensor reads None) the load must NOT raise:
        it runs one ungated cycle, sets active_tool, and respond_info notes the missing
        switch."""
        cfs, hw = _wired()
        cfs._toolhead_filament_detected = lambda: None
        gcmd = _load_gcmd()

        cfs.load_process(gcmd, 0x01, SLOT_T0)   # must not raise

        assert cfs._active_tool == 0
        infos = [c.args[0] for c in gcmd.respond_info.call_args_list]
        assert any("no toolhead filament switch" in s for s in infos)
        # The feed still ran on the box.
        hw.assert_command_received(CMD_EXTRUDE_PROCESS)


# ===========================================================================
# load_process: failed load (switch never trips) -- recoverable error
# ===========================================================================

class TestLoadProcessFailed:

    def test_load_process_switch_never_trips_raises_recoverable(self):
        """On a sensor-equipped rig whose switch never latches within the wall budget, the
        load raises a RECOVERABLE gcmd.error ('did not reach the toolhead') so a retry macro
        can re-run the whole choreography."""
        cfs, hw = _wired()
        cfs._toolhead_filament_detected = lambda: False   # never latches
        gcmd = _load_gcmd()

        with pytest.raises(Exception, match="did not reach the toolhead"):
            cfs.load_process(gcmd, 0x01, SLOT_T0)


# ===========================================================================
# extrude_process: validation guards
# ===========================================================================

class TestExtrudeProcessValidation:

    def test_extrude_process_non_1hot_slot_raises_value_error(self):
        """slot 0x03 is not a 1-hot bitmask -> ValueError."""
        cfs, hw = _wired()
        with pytest.raises(ValueError):
            cfs.extrude_process(0x01, 0x03)

    def test_extrude_process_addr_out_of_range_raises_value_error(self):
        """addr 0x05 is outside the box address range -> ValueError."""
        cfs, hw = _wired()
        with pytest.raises(ValueError):
            cfs.extrude_process(0x05, SLOT_T0)


# ===========================================================================
# _extrude_wheel: BE float decode
# ===========================================================================

class TestExtrudeWheel:

    def test_extrude_wheel_none_input_returns_none(self):
        assert CrealityCFS._extrude_wheel(None) is None

    def test_extrude_wheel_short_payload_returns_none(self):
        """A 2-byte payload is too short for a 4-byte wheel word -> None."""
        assert CrealityCFS._extrude_wheel({"data": b"\x00\x00"}) is None

    def test_extrude_wheel_decodes_four_byte_be_float(self):
        """A 4-byte >f word decodes back to the source float (the measuring-wheel value)."""
        word = struct.pack(">f", -462.0)
        assert CrealityCFS._extrude_wheel({"data": word}) == pytest.approx(-462.0, abs=0.01)


# ===========================================================================
# extrude_stage: single 0x10 stage frame
# ===========================================================================

class TestExtrudeStage:

    def test_extrude_stage_push_returns_wheel_dict(self):
        """A 0x05 PUSH sub-stage reply carries the 4-byte wheel word, so the parsed dict's
        data decodes to a float via _extrude_wheel."""
        cfs, hw = _wired()
        resp = cfs.extrude_stage(0x01, SLOT_T0, EXTRUDE_SUB_PUSH, 0x00)
        assert isinstance(resp, dict)
        assert CrealityCFS._extrude_wheel(resp) is not None

    def test_extrude_stage_init_returns_bare_ack_dict(self):
        """An init (0x00) sub-stage is a bare ACK: a parsed dict with no 4-byte wheel word."""
        cfs, hw = _wired()
        resp = cfs.extrude_stage(0x01, SLOT_T0, 0x00, 0x00)
        assert isinstance(resp, dict)
        assert CrealityCFS._extrude_wheel(resp) is None


# ===========================================================================
# cmd_CFS_EXTRUDE: the G-code handler
# ===========================================================================

class TestCmdCFSExtrude:

    def test_cmd_cfs_extrude_maps_tool_to_slot_bitmask(self):
        """CFS_EXTRUDE BOX=1 TOOL=2 routes to load_process with SLOT_BITMASKS[2] (0x04)."""
        cfs, hw = _wired()
        gcmd = mock.MagicMock()
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1,
            "TOOL": 2,
        }.get(key, default)
        gcmd.get_float.side_effect = lambda key, default=None, **kw: default
        gcmd.error.side_effect = lambda msg: Exception(msg)

        with mock.patch.object(cfs, "load_process") as lp:
            cfs.cmd_CFS_EXTRUDE(gcmd)

        lp.assert_called_once()
        args = lp.call_args.args
        assert args[1] == 0x01                       # BOX=1 -> addr 0x01
        assert args[2] == SLOT_BITMASKS[2]           # TOOL=2 -> slot 0x04

    def test_cmd_cfs_extrude_not_connected_raises(self):
        """A disconnected port refuses the load with a 'not connected' gcmd.error."""
        cfs, hw = _wired()
        cfs.is_connected = False
        gcmd = mock.MagicMock()
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1, "TOOL": 0,
        }.get(key, default)
        gcmd.error.side_effect = lambda msg: Exception(msg)

        with pytest.raises(Exception, match="not connected"):
            cfs.cmd_CFS_EXTRUDE(gcmd)


# ===========================================================================
# enter_feed_mode / set_print_mode: 0x04 SET_BOX_MODE ACK -> True
# ===========================================================================

class TestFeedAndPrintMode:

    def test_enter_feed_mode_returns_true_on_ack(self):
        """enter_feed_mode routes through set_box_mode (0x04 [00][slot]); the mock's
        status-0x00 ACK satisfies the strict check -> True."""
        cfs, hw = _wired()
        assert cfs.enter_feed_mode(0x01, SLOT_T0) is True
        hw.assert_command_received(CMD_SET_BOX_MODE)

    def test_set_print_mode_returns_true_on_ack(self):
        """set_print_mode routes through set_box_mode (0x04 [slot][00]) -> True on ACK."""
        cfs, hw = _wired()
        assert cfs.set_print_mode(0x01, SLOT_T0) is True
        hw.assert_command_received(CMD_SET_BOX_MODE)


# ===========================================================================
# _effective_temp / _melt_guard: MIN_EXTRUDE_TEMP floor
# ===========================================================================

class TestMeltGuard:

    def test_effective_temp_below_floor_raises(self):
        """_effective_temp raises gcmd.error when the effective temp is under the cold-
        extrude floor (TEMP=100 < 170)."""
        cfs, hw = _wired()
        gcmd = _load_gcmd(temp=100.0)
        with pytest.raises(Exception, match="cold-extrude floor"):
            cfs._effective_temp(gcmd, "CFS_EXTRUDE")

    def test_effective_temp_at_or_above_floor_returns_temp(self):
        """At/above the floor _effective_temp returns the resolved temperature."""
        cfs, hw = _wired()
        gcmd = _load_gcmd(temp=250.0)
        assert cfs._effective_temp(gcmd, "CFS_EXTRUDE") == pytest.approx(250.0)

    def test_melt_guard_emits_blocking_m109(self):
        """_melt_guard resolves the temp then emits a blocking M109 at that target."""
        cfs, hw = _wired()
        gcmd = _load_gcmd(temp=245.0)
        temp = cfs._melt_guard(gcmd, "CFS_EXTRUDE")
        assert temp == pytest.approx(245.0)
        assert any(s.startswith("M109") for s in _scripts(cfs))

    def test_melt_guard_below_floor_raises_before_m109(self):
        """A cold target aborts in the floor check -- no M109 is emitted."""
        cfs, hw = _wired()
        gcmd = _load_gcmd(temp=100.0)
        with pytest.raises(Exception, match="cold-extrude floor"):
            cfs._melt_guard(gcmd, "CFS_EXTRUDE")
        assert not any(s.startswith("M109") for s in _scripts(cfs))
