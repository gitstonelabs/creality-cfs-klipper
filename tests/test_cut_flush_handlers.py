# SPDX-License-Identifier: GPL-3.0-or-later
"""
test_cut_flush_handlers.py: Tests for the mechanical-cut and change-flush G-code
handlers and their low-level protocol/split helpers in CrealityCFS.

Covered functions (src/creality_cfs.py):
  - cmd_CFS_CUT            (the mechanical cut ram + 0x05 post-read)
  - cmd_CFS_FLUSH          (the capped-cycle hotend purge + clog watchdog)
  - cut_state_code / cut_state          (0x05 read, raw byte + bool form)
  - ctrl_connection_motor_action        (0x0F feeder-motor engage/release)
  - measuring_wheel / measuring_wheel_mm (0x0E raw word + decoded signed mm)
  - _flush_cap / _flush_cycles / _default_flush_total (the split model)

Everything is driven through the wired MockCFSHardware transport (real bytes on
the wire), except the two deliberately-stubbed clog-watchdog cases which override
measuring_wheel_mm to force / suppress the abort. No Klipper env, no hardware.
"""

import sys
import os
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from creality_cfs import (
    CMD_CUT_STATE,
    CMD_MEASURING_WHEEL,
    CMD_CTRL_CONNECTION_MOTOR_ACTION,
    CUT_STATE_DONE,
    FLUSH_CAP_MAX,
    FLUSH_TOTAL_MAX,
    FLUSH_VELOCITY_DEFAULT,
)

from tests.mock_cfs import MockCFSHardware
from tests.conftest import make_wired_controller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wired():
    """A single-box wired controller with addressing already run.

    _run_auto_addressing() assigns 0x01 so operational commands target a mapped,
    online box; the fake serial calls hw.process_message() on every write so no
    read ever blocks.
    """
    hw = MockCFSHardware(box_count=1)
    cfs, ser = make_wired_controller(hw, box_count=1, retry_count=1)
    cfs._run_auto_addressing()
    return hw, cfs, ser


def _fake_gcmd(ints=None, floats=None):
    """A MagicMock GCodeCommand: get_int/get_float honor the given maps and
    fall back to the caller's default; gcmd.error() raises a real Exception so
    pytest.raises can catch it."""
    ints = ints or {}
    floats = floats or {}
    gcmd = mock.MagicMock()
    gcmd.get_int.side_effect = lambda k, d=None, **kw: ints.get(k, d)
    gcmd.get_float.side_effect = lambda k, d=None, **kw: floats.get(k, d)
    gcmd.error.side_effect = lambda m: Exception(m)
    return gcmd


def _scripts(cfs):
    """All gcode scripts emitted through run_script_from_command, in order."""
    return [c.args[0] for c in cfs.gcode.run_script_from_command.call_args_list]


def _infos(gcmd):
    """All respond_info strings, in order."""
    return [c.args[0] for c in gcmd.respond_info.call_args_list]


def _set_cut_geometry(cfs, cut_x=40.0, cut_y=None):
    """Wire in a valid cut configuration on a freshly-built controller (the
    conftest fake config supplies none of these)."""
    cfs.cut_switch_pin = "PA1"
    cfs.pre_cut_pos_x = 10.0
    cfs.pre_cut_pos_y = 200.0
    cfs.cut_pos_x = cut_x
    cfs.cut_pos_y = cut_y
    cfs.cut_pos_x_max = 100.0


# ===========================================================================
# cmd_CFS_CUT -- happy path (X-axis ram)
# ===========================================================================

class TestCutHappyPath:
    def test_x_axis_ram_emits_moves_and_confirms(self):
        hw, cfs, ser = _wired()
        _set_cut_geometry(cfs, cut_x=40.0)
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"TEMP": 250.0})

        cfs.cmd_CFS_CUT(gcmd)

        scripts = _scripts(cfs)
        # Absolute positioning, then the pre -> cut -> pre X ram sequence.
        assert "G90" in scripts
        assert "G0 X10.000 Y200.000 F3000" in scripts        # move to pre-cut pose
        assert "G0 X40.000 F3000" in scripts                 # ram into the cutter
        assert "G0 X10.000 F3000" in scripts                 # retreat to pre-cut X
        # order: pre-pose, then ram, then retreat
        i_pose = scripts.index("G0 X10.000 Y200.000 F3000")
        i_ram = scripts.index("G0 X40.000 F3000")
        i_back = scripts.index("G0 X10.000 F3000")
        assert i_pose < i_ram < i_back
        # Melt guard (blocking heat) and the settle barrier ran.
        assert any(s.startswith("M109") for s in scripts)
        assert "M400" in scripts

        # The 0x05 cut-state read actually went out on the wire.
        hw.assert_command_received(CMD_CUT_STATE)
        # Mock returns 0x00 -> cut confirmed.
        assert any("cut confirmed" in s for s in _infos(gcmd))

    def test_m109_precedes_the_ram_moves(self):
        # Heat-before-move: the blocking M109 must be emitted before any G0 ram.
        hw, cfs, ser = _wired()
        _set_cut_geometry(cfs, cut_x=40.0)
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"TEMP": 250.0})
        cfs.cmd_CFS_CUT(gcmd)
        scripts = _scripts(cfs)
        i_m109 = next(i for i, s in enumerate(scripts) if s.startswith("M109"))
        i_ram = scripts.index("G0 X40.000 F3000")
        assert i_m109 < i_ram


# ===========================================================================
# cmd_CFS_CUT -- guard rails (each raises)
# ===========================================================================

class TestCutGuards:
    def test_no_cut_switch_pin(self):
        hw, cfs, ser = _wired()
        _set_cut_geometry(cfs, cut_x=40.0)
        cfs.cut_switch_pin = None
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"TEMP": 250.0})
        with pytest.raises(Exception, match="no cut_switch_pin"):
            cfs.cmd_CFS_CUT(gcmd)

    def test_missing_geometry(self):
        hw, cfs, ser = _wired()
        _set_cut_geometry(cfs, cut_x=40.0)
        cfs.cut_pos_x = None
        cfs.cut_pos_y = None
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"TEMP": 250.0})
        with pytest.raises(Exception, match="missing cut geometry"):
            cfs.cmd_CFS_CUT(gcmd)

    def test_zero_travel_refusal(self):
        hw, cfs, ser = _wired()
        _set_cut_geometry(cfs, cut_x=40.0)
        # cut_pos_x == pre_cut_pos_x -> the ram would not move.
        cfs.cut_pos_x = cfs.pre_cut_pos_x
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"TEMP": 250.0})
        with pytest.raises(Exception, match="would not move"):
            cfs.cmd_CFS_CUT(gcmd)

    def test_pre_x_exceeds_max(self):
        hw, cfs, ser = _wired()
        _set_cut_geometry(cfs, cut_x=40.0)
        cfs.pre_cut_pos_x = 150.0          # > cut_pos_x_max (100.0)
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"TEMP": 250.0})
        with pytest.raises(Exception, match="cut_pos_x_max"):
            cfs.cmd_CFS_CUT(gcmd)

    def test_cold_temp_melt_guard_floor(self):
        hw, cfs, ser = _wired()
        _set_cut_geometry(cfs, cut_x=40.0)
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"TEMP": 100.0})   # below the 170C floor
        with pytest.raises(Exception, match="cold-extrude floor"):
            cfs.cmd_CFS_CUT(gcmd)

    def test_not_connected(self):
        hw, cfs, ser = _wired()
        _set_cut_geometry(cfs, cut_x=40.0)
        cfs.is_connected = False
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"TEMP": 250.0})
        with pytest.raises(Exception, match="not connected"):
            cfs.cmd_CFS_CUT(gcmd)


# ===========================================================================
# cmd_CFS_CUT -- Y-axis branch
# ===========================================================================

class TestCutYAxis:
    def test_y_axis_ram(self):
        hw, cfs, ser = _wired()
        # No cut_pos_x -> the Y-axis branch. pre_y=200, cut_y=250 -> real travel.
        _set_cut_geometry(cfs, cut_x=None, cut_y=250.0)
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"TEMP": 250.0})

        cfs.cmd_CFS_CUT(gcmd)

        scripts = _scripts(cfs)
        assert "G0 Y250.000 F3000" in scripts        # ram in Y
        assert "G0 Y200.000 F3000" in scripts        # retreat in Y
        assert any("Y-axis ram" in s for s in _infos(gcmd))
        hw.assert_command_received(CMD_CUT_STATE)


# ===========================================================================
# cmd_CFS_FLUSH -- happy path (explicit LEN split)
# ===========================================================================

class TestFlushHappyPath:
    def test_len_split_and_post_retract(self):
        hw, cfs, ser = _wired()
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"LEN": 158.75, "TEMP": 250.0})

        cfs.cmd_CFS_FLUSH(gcmd)

        scripts = _scripts(cfs)
        # 158.75 total, cap 80 -> [80.0, 78.75] (wire-verified split).
        assert "G1 E80.000 F360" in scripts
        assert "G1 E78.750 F360" in scripts
        # Relative-E mode set before the purge.
        assert "M83" in scripts
        # The 1.5 mm post-flush retract.
        assert "G1 E-1.500 F600" in scripts
        # And it reports completion.
        assert any("complete" in s for s in _infos(gcmd))

    def test_default_velocity_used(self):
        hw, cfs, ser = _wired()
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"LEN": 50.0, "TEMP": 250.0})
        cfs.cmd_CFS_FLUSH(gcmd)
        scripts = _scripts(cfs)
        # No VELOCITY= -> flush_velocity default (360).
        assert "G1 E50.000 F%.0f" % FLUSH_VELOCITY_DEFAULT in scripts


# ===========================================================================
# cmd_CFS_FLUSH -- VOLUME form
# ===========================================================================

class TestFlushVolume:
    def test_volume_formula(self):
        hw, cfs, ser = _wired()
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"VOLUME": 100.0, "TEMP": 250.0})

        cfs.cmd_CFS_FLUSH(gcmd)

        # total = nozzle_volume/2.4 + (5/12)*VOLUME*flush_multiplier
        expected = cfs.nozzle_volume / 2.4 + (5.0 / 12.0) * 100.0 * cfs.flush_multiplier
        infos = " ".join(_infos(gcmd))
        assert ("%.2f" % expected) in infos


# ===========================================================================
# cmd_CFS_FLUSH -- ceiling
# ===========================================================================

class TestFlushCeiling:
    def test_len_over_ceiling_raises(self):
        hw, cfs, ser = _wired()
        # LEN=999 > FLUSH_TOTAL_MAX (600) -> refused.
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"LEN": 999.0, "TEMP": 250.0})
        assert 999.0 > FLUSH_TOTAL_MAX
        with pytest.raises(Exception, match="exceeds"):
            cfs.cmd_CFS_FLUSH(gcmd)


# ===========================================================================
# cmd_CFS_FLUSH -- clog watchdog
# ===========================================================================

class TestFlushClogWatchdog:
    def test_constant_wheel_trips_abort(self):
        hw, cfs, ser = _wired()
        # Constant wheel reading -> zero advance -> the under-feed watchdog fires.
        cfs.measuring_wheel_mm = mock.MagicMock(return_value=-100.0)
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"LEN": 80.0, "TEMP": 250.0})
        with pytest.raises(Exception, match="under-feed/clog"):
            cfs.cmd_CFS_FLUSH(gcmd)

    def test_none_wheel_no_false_abort(self):
        hw, cfs, ser = _wired()
        # A wheel-less path (None reads) must NEVER false-trip the watchdog.
        cfs.measuring_wheel_mm = mock.MagicMock(return_value=None)
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"LEN": 80.0, "TEMP": 250.0})
        cfs.cmd_CFS_FLUSH(gcmd)   # completes cleanly
        assert any("complete" in s for s in _infos(gcmd))


# ===========================================================================
# cmd_CFS_FLUSH -- nozzle clean macro
# ===========================================================================

class TestFlushNozzleMacro:
    def test_macro_runs_once_per_cycle(self):
        hw, cfs, ser = _wired()
        cfs.nozzle_clean_macro = "CLEAN_NOZZLE"
        # LEN=50 -> a single cycle -> the wipe macro fires exactly once.
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"LEN": 50.0, "TEMP": 250.0})
        cfs.cmd_CFS_FLUSH(gcmd)
        scripts = _scripts(cfs)
        assert scripts.count("CLEAN_NOZZLE") == 1

    def test_macro_runs_per_cycle_on_multi_cycle(self):
        hw, cfs, ser = _wired()
        cfs.nozzle_clean_macro = "CLEAN_NOZZLE"
        # LEN=158.75 -> two cycles -> the wipe macro fires twice.
        gcmd = _fake_gcmd(ints={"BOX": 1}, floats={"LEN": 158.75, "TEMP": 250.0})
        cfs.cmd_CFS_FLUSH(gcmd)
        scripts = _scripts(cfs)
        assert scripts.count("CLEAN_NOZZLE") == 2


# ===========================================================================
# _flush_cap / _flush_cycles / _default_flush_total -- unit
# ===========================================================================

class TestFlushSplitUnit:
    def test_cycles_343(self):
        hw, cfs, ser = _wired()
        cfs.flush_cycle_cap = 80.0
        cycles = [round(c, 2) for c in cfs._flush_cycles(343.33, 80.0)]
        assert cycles == [80.0, 65.83, 65.83, 65.83, 65.83]

    def test_cycles_single(self):
        hw, cfs, ser = _wired()
        # total <= cap -> a single cycle equal to the total.
        assert cfs._flush_cycles(50.0, 80.0) == [50.0]

    def test_cycles_158(self):
        hw, cfs, ser = _wired()
        cycles = [round(c, 2) for c in cfs._flush_cycles(158.75, 80.0)]
        assert cycles == [80.0, 78.75]

    def test_cap_clamps_to_max(self):
        hw, cfs, ser = _wired()
        # An oversized flush_cycle_cap is clamped to FLUSH_CAP_MAX.
        cfs.flush_cycle_cap = 500.0
        assert cfs._flush_cap() == FLUSH_CAP_MAX

    def test_cap_uses_config_when_reasonable(self):
        hw, cfs, ser = _wired()
        cfs.flush_cycle_cap = 80.0
        assert cfs._flush_cap() == 80.0

    def test_default_total_prefers_len(self):
        hw, cfs, ser = _wired()
        gcmd = _fake_gcmd(floats={"LEN": 123.0})
        assert cfs._default_flush_total(gcmd) == 123.0

    def test_default_total_volume(self):
        hw, cfs, ser = _wired()
        gcmd = _fake_gcmd(floats={"VOLUME": 100.0})
        expected = cfs.nozzle_volume / 2.4 + (5.0 / 12.0) * 100.0 * cfs.flush_multiplier
        assert cfs._default_flush_total(gcmd) == pytest.approx(expected)

    def test_default_total_fallback(self):
        hw, cfs, ser = _wired()
        # No LEN=, no VOLUME= -> flush_default_len.
        gcmd = _fake_gcmd()
        assert cfs._default_flush_total(gcmd) == cfs.flush_default_len


# ===========================================================================
# cut_state / cut_state_code -- direct
# ===========================================================================

class TestCutStateDirect:
    def test_cut_state_bool_true(self):
        hw, cfs, ser = _wired()
        # Mock returns 0x00 (cut OK) -> bool form True.
        assert cfs.cut_state(0x01) is True

    def test_cut_state_code_is_zero(self):
        hw, cfs, ser = _wired()
        assert cfs.cut_state_code(0x01) == CUT_STATE_DONE   # 0x00
        hw.assert_command_received(CMD_CUT_STATE)


# ===========================================================================
# ctrl_connection_motor_action -- direct
# ===========================================================================

class TestMotorActionDirect:
    def test_engage_acked(self):
        hw, cfs, ser = _wired()
        assert cfs.ctrl_connection_motor_action(0x01, True) is True
        hw.assert_command_received(CMD_CTRL_CONNECTION_MOTOR_ACTION)

    def test_release_acked(self):
        hw, cfs, ser = _wired()
        assert cfs.ctrl_connection_motor_action(0x01, False) is True


# ===========================================================================
# measuring_wheel / measuring_wheel_mm -- direct
# ===========================================================================

class TestMeasuringWheelDirect:
    def test_raw_returns_four_bytes(self):
        hw, cfs, ser = _wired()
        raw = cfs.measuring_wheel(0x01)
        assert len(raw) == 4
        hw.assert_command_received(CMD_MEASURING_WHEEL)

    def test_mm_decodes_negative_float(self):
        hw, cfs, ser = _wired()
        # The mock wheel starts at -100.0 and grows in magnitude; the decode is a
        # signed BE IEEE-754 float, so the first read is a negative mm value.
        val = cfs.measuring_wheel_mm(0x01)
        assert val is not None
        assert val < 0.0
