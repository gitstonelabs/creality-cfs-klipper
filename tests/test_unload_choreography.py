# SPDX-License-Identifier: GPL-3.0-or-later
"""
test_unload_choreography.py: Behavioral tests for the CFS UNLOAD choreography.

Covers, wired end-to-end through MockCFSHardware (real bytes on the fake serial):
  - unload_process()        the full CFS_RETRUDE choreography (sensor-clear, sensorless,
                            and recoverable-jam paths)
  - retrude_process()       the transport-only START/FINISH pair (+ slot validation)
  - retrude_phase()         a single 0x11 frame returning its STATUS byte
  - cmd_CFS_RETRUDE()       the G-code entry point (drives unload_process; not-connected)
  - _toolhead_pull()        the single interleaved G1 E-15 F360 pull (M83 + move)
  - _dwell()                the in-handler G4 pacing dwell
  - get_hardware_status()   the 0x08 toolhead-sensor / hardware-status read

Every path drives the real _send_command -> build_message -> mock.process_message ->
parse_message stack via the wired controller; there is no mocking of the protocol.
The toolhead filament switch (absent on the bare harness) is monkeypatched per-test to
drive the sensor-equipped completion gate.
"""

import sys
import os
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from creality_cfs import (
    SLOT_T0,
    SLOT_T1,
    CMD_RETRUDE_PROCESS,
    CMD_GET_HARDWARE_STATUS,
    RETRUDE_PHASE_START,
)

from tests.mock_cfs import MockCFSHardware
from tests.conftest import make_wired_controller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wired():
    """Build a 1-box wired controller with addresses assigned (addr 0x01 mapped/online)."""
    hw = MockCFSHardware(box_count=1)
    cfs, ser = make_wired_controller(hw, box_count=1, retry_count=1)
    cfs._run_auto_addressing()
    return hw, cfs, ser


def _make_gcmd(temp=250.0):
    """Return a MagicMock GCodeCommand whose get_float('TEMP') yields `temp`.

    The choreography helpers taking gcmd directly (unload_process) read only TEMP; the
    G-code entry point (cmd_CFS_RETRUDE) additionally reads BOX/TOOL ints.
    """
    gcmd = mock.MagicMock()
    gcmd.get_float.side_effect = lambda key, default=None, **kw: {
        "TEMP": temp,
    }.get(key, default)
    gcmd.error.side_effect = lambda msg: Exception(msg)
    return gcmd


def _sequence_sensor(values):
    """Return a zero-arg callable yielding each value in `values` once, then repeating
    the last value forever -- models a filament switch that latches to a final state."""
    state = {"i": 0}
    seq = list(values)

    def _read():
        i = state["i"]
        if i < len(seq):
            state["i"] = i + 1
            return seq[i]
        return seq[-1]

    return _read


def _emitted_scripts(cfs):
    """Collect the ordered list of gcode scripts run via run_script_from_command."""
    return [c.args[0] for c in cfs.gcode.run_script_from_command.call_args_list]


# ===========================================================================
# unload_process(): SENSOR-CLEAR path (the healthy unload)
# ===========================================================================

class TestUnloadProcessSensorClear:
    """Sensor-equipped unload: switch reads loaded then clears -> completion."""

    def test_sensor_clear_unload_completes(self):
        hw, cfs, ser = _wired()
        hw.set_loaded(0, True)
        cfs._active_tool = 0
        # loaded (True) at had_sensor probe, then clears (False) at the completion gate.
        cfs._toolhead_filament_detected = _sequence_sensor([True, True, False, False])

        gcmd = _make_gcmd(temp=250.0)
        cfs.unload_process(gcmd, 0x01, SLOT_T0)

        # The transport-only START/FINISH pair ran (0x11) -- FINISH cleared the box loaded flag.
        hw.assert_command_received(CMD_RETRUDE_PROCESS)
        assert hw._loaded[0] is False

        scripts = _emitted_scripts(cfs)
        # The melt guard's blocking heat-and-wait ran.
        assert any(s.startswith("M109") for s in scripts), scripts
        # Exactly ONE interleaved toolhead pull between START and FINISH.
        pulls = [s for s in scripts if s == "G1 E-15.000 F360"]
        assert pulls == ["G1 E-15.000 F360"], scripts

        # Active tool cleared once the slot unloaded.
        assert cfs._active_tool is None

        # respond_info narrates completion / switch cleared.
        infos = " ".join(c.args[0] for c in gcmd.respond_info.call_args_list)
        assert "unload complete" in infos, infos
        assert "switch cleared" in infos, infos

    def test_sensor_clear_pull_is_single(self):
        """The G1 E-15 pull is literal and emitted exactly once (never per-cycle)."""
        hw, cfs, ser = _wired()
        hw.set_loaded(0, True)
        cfs._active_tool = 0
        cfs._toolhead_filament_detected = _sequence_sensor([True, False])

        cfs.unload_process(_make_gcmd(), 0x01, SLOT_T0)
        scripts = _emitted_scripts(cfs)
        assert sum(1 for s in scripts if s == "G1 E-15.000 F360") == 1, scripts
        # And it was preceded by the relative-extrude M83 the pull sets up.
        assert "M83" in scripts, scripts


# ===========================================================================
# unload_process(): SENSORLESS path (degraded, box-state corroboration)
# ===========================================================================

class TestUnloadProcessSensorless:
    """No toolhead switch: completion falls back to box-state corroboration."""

    def test_sensorless_unload_completes_via_box_state(self):
        hw, cfs, ser = _wired()
        # No sensor object -> None; and the box starts not-loaded.
        cfs._toolhead_filament_detected = lambda: None
        hw.set_loaded(0, False)
        cfs._active_tool = 0

        gcmd = _make_gcmd(temp=250.0)
        # Must not raise on a sensorless rig.
        cfs.unload_process(gcmd, 0x01, SLOT_T0)

        hw.assert_command_received(CMD_RETRUDE_PROCESS)
        infos = " ".join(c.args[0] for c in gcmd.respond_info.call_args_list)
        assert infos != "", "sensorless unload must still respond_info"
        # The sensorless branch names the no-switch caveat.
        assert "no toolhead switch" in infos.lower(), infos


# ===========================================================================
# unload_process(): recoverable JAM (switch never clears)
# ===========================================================================

class TestUnloadProcessJam:
    """Sensor-equipped rig whose switch never clears -> recoverable gcmd.error."""

    def test_jam_raises_did_not_clear(self):
        hw, cfs, ser = _wired()
        hw.set_loaded(0, True)
        cfs._active_tool = 0
        # Switch stuck detecting filament forever: completion gate never trips.
        cfs._toolhead_filament_detected = lambda: True

        gcmd = _make_gcmd(temp=250.0)
        with pytest.raises(Exception, match="did not clear"):
            cfs.unload_process(gcmd, 0x01, SLOT_T0)

        # The START/FINISH frames still went out before the gate timed out.
        hw.assert_command_received(CMD_RETRUDE_PROCESS)


# ===========================================================================
# retrude_process(): transport-only START/FINISH pair
# ===========================================================================

class TestRetrudeProcess:
    """The bus-only unload pair: both frames ACK -> True; bad slot -> ValueError."""

    def test_retrude_process_both_frames_ack(self):
        hw, cfs, ser = _wired()
        hw.set_loaded(0, True)
        result = cfs.retrude_process(0x01, SLOT_T0)
        assert result is True
        # Both frames landed and the FINISH one cleared the box loaded flag.
        hw.assert_command_received(CMD_RETRUDE_PROCESS, times=2)
        assert hw._loaded[0] is False

    def test_retrude_process_rejects_non_bitmask_slot(self):
        hw, cfs, ser = _wired()
        with pytest.raises(ValueError):
            cfs.retrude_process(0x01, 0x03)   # 0x03 is not a 1-hot bitmask

    def test_retrude_process_rejects_bad_addr(self):
        hw, cfs, ser = _wired()
        with pytest.raises(ValueError):
            cfs.retrude_process(0x00, SLOT_T0)  # addr below ADDR_BOX_MIN


# ===========================================================================
# retrude_phase(): a single 0x11 frame -> STATUS byte
# ===========================================================================

class TestRetrudePhase:
    """One 0x11 START frame returns the wire STATUS byte (0x00 ACK on the mock)."""

    def test_retrude_phase_start_returns_status_zero(self):
        hw, cfs, ser = _wired()
        st = cfs.retrude_phase(0x01, SLOT_T0, RETRUDE_PHASE_START, timeout=1.0)
        assert st == 0x00
        hw.assert_command_received(CMD_RETRUDE_PROCESS, times=1)


# ===========================================================================
# _toolhead_pull() / _dwell()
# ===========================================================================

class TestToolheadPullAndDwell:
    """The single interleaved pull emits M83 + G1 E-15 F360; _dwell emits G4."""

    def test_toolhead_pull_emits_m83_and_move(self):
        hw, cfs, ser = _wired()
        # printer.lookup_object('extruder', None) -> None on the bare harness, so the
        # temp-floor short-circuit is skipped and the pull always proceeds.
        cfs._toolhead_pull()
        scripts = _emitted_scripts(cfs)
        assert "M83" in scripts, scripts
        assert "G1 E-15.000 F360" in scripts, scripts
        # Order: M83 sets relative extrude before the retract.
        assert scripts.index("M83") < scripts.index("G1 E-15.000 F360"), scripts

    def test_dwell_emits_g4_milliseconds(self):
        hw, cfs, ser = _wired()
        cfs._dwell(0.25)
        scripts = _emitted_scripts(cfs)
        # 0.25 s -> G4 P250.
        assert "G4 P250" in scripts, scripts

    def test_dwell_clamps_negative_to_zero(self):
        hw, cfs, ser = _wired()
        cfs._dwell(-5.0)
        scripts = _emitted_scripts(cfs)
        assert "G4 P0" in scripts, scripts


# ===========================================================================
# cmd_CFS_RETRUDE(): the G-code entry point
# ===========================================================================

class TestCmdCFSRetrude:
    """CFS_RETRUDE BOX=/TOOL= drives unload_process on the resolved slot."""

    def test_cmd_retrude_drives_unload_process(self):
        hw, cfs, ser = _wired()
        hw.set_loaded(0, True)
        cfs._active_tool = 0
        cfs._toolhead_filament_detected = _sequence_sensor([True, False])

        gcmd = mock.MagicMock()
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1,
            "TOOL": 0,
        }.get(key, default)
        gcmd.get_float.side_effect = lambda key, default=None, **kw: {
            "TEMP": 250.0,
        }.get(key, default)
        gcmd.error.side_effect = lambda msg: Exception(msg)

        cfs.cmd_CFS_RETRUDE(gcmd)

        # TOOL=0 resolves to SLOT_T0 -> the 0x11 pair ran and the box unloaded.
        hw.assert_command_received(CMD_RETRUDE_PROCESS)
        assert hw._loaded[0] is False
        assert cfs._active_tool is None

    def test_cmd_retrude_not_connected_raises(self):
        hw, cfs, ser = _wired()
        cfs.is_connected = False
        gcmd = mock.MagicMock()
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1, "TOOL": 0}.get(key, default)
        gcmd.get_float.side_effect = lambda key, default=None, **kw: {"TEMP": 250.0}.get(key, default)
        gcmd.error.side_effect = lambda msg: Exception(msg)
        with pytest.raises(Exception, match="not connected"):
            cfs.cmd_CFS_RETRUDE(gcmd)


# ===========================================================================
# get_hardware_status(): the 0x08 read + choreography pings
# ===========================================================================

class TestGetHardwareStatus:
    """The 0x08 toolhead-sensor / hardware-status read returns the flag byte."""

    def test_get_hardware_status_returns_flag(self):
        hw, cfs, ser = _wired()
        flag = cfs.get_hardware_status(0x01, 0x00)
        assert flag == 0x01
        hw.assert_command_received(CMD_GET_HARDWARE_STATUS)

    def test_unload_fires_08_pings(self):
        """The unload choreography lands both 0x08 sensor-prep pings on the bus."""
        hw, cfs, ser = _wired()
        hw.set_loaded(0, True)
        cfs._active_tool = 0
        cfs._toolhead_filament_detected = _sequence_sensor([True, False])

        cfs.unload_process(_make_gcmd(), 0x01, SLOT_T0)
        # material (0x00) and connections (0x01) 0x08 reads both went out.
        assert CMD_GET_HARDWARE_STATUS in hw.get_received_funcs()
        hw.assert_command_received(CMD_GET_HARDWARE_STATUS, times=2)
