# SPDX-License-Identifier: GPL-3.0-or-later
"""
test_box_status_features.py: Tests for the stock-parity box features added on top of
the wire choreography:

  - _flat_box_status / CFSBoxStatus   the flat `box` status shape (box_wrapper §5a)
  - CFS_ERROR_KEYS + _record_error     the key831..key864 error dictionary emission
  - BOX_ENABLE_AUTO_REFILL             the auto_refill toggle
  - BOX_UPDATE_SAME_MATERIAL_LIST      the slot-equivalence groups
  - BOX_CHECK_MATERIAL_REFILL / find_refill_slot   same-material resolution
  - BOX_ERROR_CLEAR                    clearing the latched error

Driven through the wired MockCFSHardware transport (real bytes) plus direct calls.
No Klipper env, no hardware.
"""

import sys
import os
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import creality_cfs
from creality_cfs import CFS_ERROR_KEYS, CFSBoxStatus

from tests.mock_cfs import MockCFSHardware
from tests.conftest import make_wired_controller


def _wired(box_count=1):
    hw = MockCFSHardware(box_count=box_count)
    cfs, ser = make_wired_controller(hw, box_count=box_count, retry_count=1)
    cfs._run_auto_addressing()
    return hw, cfs, ser


def _fake_gcmd(ints=None, gets=None):
    """A MagicMock GCodeCommand with get_int / get (string) maps; error() raises."""
    ints = ints or {}
    gets = gets or {}
    gcmd = mock.MagicMock()
    gcmd.get_int.side_effect = lambda k, d=None, **kw: ints.get(k, d)
    gcmd.get.side_effect = lambda k, d=None, **kw: gets.get(k, d)
    gcmd.error.side_effect = lambda m: Exception(m)
    return gcmd


def _infos(gcmd):
    return [c.args[0] for c in gcmd.respond_info.call_args_list]


# ===========================================================================
# Error dictionary (key831..key864)
# ===========================================================================

class TestErrorDict:

    def test_key864_present_and_is_the_build_b_delta(self):
        # key864 is the genuine build-B addition and MUST be in the emitted dict.
        assert 864 in CFS_ERROR_KEYS
        assert "buffer full limit" in CFS_ERROR_KEYS[864]
        # build A spanned 831..863; 863 is not a defined key in either doc table.
        assert set(CFS_ERROR_KEYS).issuperset(
            {831, 834, 841, 854, 856, 857, 859, 864})

    def test_record_error_latches_and_surfaces(self):
        _, cfs, _ = _wired()
        gcmd = _fake_gcmd()
        key = cfs._record_error(854, gcmd)
        assert key == "key854"
        assert cfs._last_error == {
            "code": 854, "key": "key854",
            "msg": CFS_ERROR_KEYS[854]}
        # surfaced to the user and mirrored in the nested status
        assert any("key854" in s for s in _infos(gcmd))
        assert cfs.get_status(0.0)["last_error"]["key"] == "key854"

    def test_record_unknown_code_still_latches_empty_msg(self):
        _, cfs, _ = _wired()
        cfs._record_error(999)
        assert cfs._last_error["key"] == "key999"
        assert cfs._last_error["msg"] == ""

    def test_error_clear_handler(self):
        _, cfs, _ = _wired()
        cfs._record_error(857)
        gcmd = _fake_gcmd()
        cfs.cmd_error_clear(gcmd)
        assert cfs._last_error is None
        assert cfs.get_status(0.0)["last_error"] is None
        assert any("cleared" in s for s in _infos(gcmd))


# ===========================================================================
# Flat `box` status (box_wrapper §5a)
# ===========================================================================

# The exact stock top-level key set a UI-driving port MUST emit.
_FLAT_TOP_KEYS = {
    "state", "filament", "map", "same_material", "cut_state",
    "auto_refill", "enable", "filament_useup", "T1", "T2", "T3", "T4",
}
_TN_KEYS = {
    "state", "filament", "temperature", "dry_and_humidity", "filament_detected",
    "measuring_wheel", "version", "sn", "mode", "vender", "remain_len",
    "color_value", "material_type", "uuid", "change_color_num",
}


class TestFlatBoxStatus:

    def test_flat_shape_has_exact_stock_top_keys(self):
        _, cfs, _ = _wired()
        st = cfs._flat_box_status()
        assert set(st) == _FLAT_TOP_KEYS

    def test_map_is_16_slots_T1A_to_T4D(self):
        _, cfs, _ = _wired()
        st = cfs._flat_box_status()
        assert len(st["map"]) == 16
        assert st["map"]["T1A"] == 0
        assert st["map"]["T4D"] == 15
        assert set(st["map"]) == {
            "T%d%s" % (n, c) for n in range(1, 5) for c in "ABCD"}

    def test_tn_subdict_shape_and_types(self):
        _, cfs, _ = _wired()
        t1 = cfs._flat_box_status()["T1"]
        assert set(t1) == _TN_KEYS
        for arr in ("vender", "remain_len", "color_value",
                    "material_type", "change_color_num"):
            assert isinstance(t1[arr], list) and len(t1[arr]) == 4
        assert t1["uuid"] == "None"

    def test_online_box_reports_connect(self):
        _, cfs, _ = _wired()
        # the wired box came online, so top-level state + T1.state read 'connect'
        st = cfs._flat_box_status()
        assert st["state"] == "connect"
        assert st["T1"]["state"] == "connect"
        # boxes that do not exist on a single-box rig read 'disconnect'
        assert st["T4"]["state"] == "disconnect"

    def test_t1_slots_reflect_the_slot_cache(self):
        _, cfs, _ = _wired()
        cfs._slots = {
            0: {"present": True, "material": "PLA", "remain": 42},
            2: {"present": False, "material": None, "remain": -1},
        }
        t1 = cfs._flat_box_status()["T1"]
        assert t1["filament"] == 1
        assert t1["material_type"][0] == "PLA"
        assert t1["remain_len"][0] == 42
        # absent/empty slots stay at defaults
        assert t1["material_type"][2] == ""
        assert t1["remain_len"][1] == 0

    def test_cut_state_and_flags_surface(self):
        _, cfs, _ = _wired()
        cfs._cut_state = True
        cfs.auto_refill = 1
        cfs._filament_useup = 1
        st = cfs._flat_box_status()
        assert st["cut_state"] is True
        assert st["auto_refill"] == 1
        assert st["enable"] == 1
        assert st["filament_useup"] == 1

    def test_adapter_object_delegates_to_flat_status(self):
        _, cfs, _ = _wired()
        adapter = CFSBoxStatus(cfs)
        assert adapter.get_status(0.0) == cfs._flat_box_status()

    def test_box_object_registered_on_construction(self):
        # __init__ registers the flat adapter under the object name 'box' (guarded).
        _, cfs, _ = _wired()
        calls = cfs.printer.add_object.call_args_list
        names = [c.args[0] for c in calls]
        assert "box" in names
        obj = dict((c.args[0], c.args[1]) for c in calls)["box"]
        assert isinstance(obj, CFSBoxStatus)


# ===========================================================================
# auto_refill / same_material / refill resolution
# ===========================================================================

class TestAutoRefillAndSameMaterial:

    def test_enable_auto_refill_toggle(self):
        _, cfs, _ = _wired()
        cfs.cmd_set_enable_auto_refill(_fake_gcmd(ints={"ENABLE": 1}))
        assert cfs.auto_refill == 1
        assert cfs._flat_box_status()["auto_refill"] == 1
        cfs.cmd_set_enable_auto_refill(_fake_gcmd(ints={"ENABLE": 0}))
        assert cfs.auto_refill == 0

    def test_update_same_material_list_parse(self):
        _, cfs, _ = _wired()
        cfs.cmd_update_same_material_list(_fake_gcmd(gets={"GROUPS": "0,1|2,3"}))
        assert cfs.same_material == [[0, 1], [2, 3]]
        assert cfs._flat_box_status()["same_material"] == [[0, 1], [2, 3]]

    def test_update_same_material_list_dedupes_and_clears(self):
        _, cfs, _ = _wired()
        cfs.cmd_update_same_material_list(_fake_gcmd(gets={"GROUPS": "1,1,2"}))
        assert cfs.same_material == [[1, 2]]
        cfs.cmd_update_same_material_list(_fake_gcmd(gets={"GROUPS": ""}))
        assert cfs.same_material == []

    def test_update_same_material_list_rejects_bad_slot(self):
        _, cfs, _ = _wired()
        with pytest.raises(Exception):
            cfs.cmd_update_same_material_list(_fake_gcmd(gets={"GROUPS": "0,9"}))
        with pytest.raises(Exception):
            cfs.cmd_update_same_material_list(_fake_gcmd(gets={"GROUPS": "x"}))

    def test_find_refill_slot_picks_present_same_material_alternate(self):
        _, cfs, _ = _wired()
        cfs.same_material = [[0, 1, 2]]
        cfs._slots = {
            0: {"present": False, "material": None, "remain": -1},
            1: {"present": True, "material": "PLA", "remain": 30},
            2: {"present": True, "material": "PLA", "remain": 90},
        }
        # slot 0 ran out -> first present equivalent is slot 1
        assert cfs.find_refill_slot(0) == 1
        # a slot with no present equivalent -> None
        cfs._slots[1]["present"] = False
        cfs._slots[2]["present"] = False
        assert cfs.find_refill_slot(0) is None
        # a slot not in any group -> None
        assert cfs.find_refill_slot(3) is None

    def test_check_material_refill_handler_reports_candidate(self):
        _, cfs, _ = _wired()
        cfs.same_material = [[0, 3]]
        cfs._slots = {3: {"present": True, "material": "PETG", "remain": 50}}
        gcmd = _fake_gcmd(ints={"TOOL": 0})
        cfs.cmd_check_material_refill(gcmd)
        assert any("refill from T3" in s for s in _infos(gcmd))
        # none available
        gcmd2 = _fake_gcmd(ints={"TOOL": 1})
        cfs.cmd_check_material_refill(gcmd2)
        assert any("no same-material slot" in s for s in _infos(gcmd2))


# ===========================================================================
# 0x0A pushed-status fault listener (buffer-spec gap-fill, 2026-07-19)
# ===========================================================================

class TestBoxStatusFaultListener:
    """The box firmware runs the buffer feed loop internally and raises its own faults
    as abnormal 0x0A STATUS bytes (0x50 FILAMENT_ERR / 0x51 SPEED_ERR / 0x52 ENWIND_ERR).
    The sink must latch key846/key847, set the runout flag on 0x50, and reach the host
    for BOTH the polled reply path and an unsolicited push through _dispatch_rx."""

    def test_speed_err_latches_key846(self):
        _, cfs, _ = _wired()
        cfs._note_box_status(0x01, 0x51, b"\x1c\x21\x00\x00")
        assert cfs._last_error["code"] == 846
        assert "box speed" in cfs._last_error["msg"]

    def test_enwind_err_latches_key847(self):
        _, cfs, _ = _wired()
        cfs._note_box_status(0x01, 0x52, b"\x1c\x21\x00\x00")
        assert cfs._last_error["code"] == 847
        assert "enwind" in cfs._last_error["msg"]

    def test_filament_err_sets_useup_flag_not_an_error_key(self):
        _, cfs, _ = _wired()
        cfs._note_box_status(0x01, 0x50, b"\x1c\x21\x00\x00")
        assert cfs._filament_useup == 1
        assert cfs._last_error is None
        # surfaces in the flat box status
        assert cfs._flat_box_status()["filament_useup"] == 1

    def test_normal_statuses_do_not_latch(self):
        _, cfs, _ = _wired()
        for st in (0x00, 0x16, 0x30):
            cfs._note_box_status(0x01, st, b"\x1c\x21\x00\x00")
        assert cfs._last_error is None
        assert cfs._filament_useup == 0

    def test_repeat_fault_does_not_relatch(self):
        _, cfs, _ = _wired()
        cfs._note_box_status(0x01, 0x51, b"")
        first = cfs._last_error
        cfs._note_box_status(0x01, 0x51, b"")
        assert cfs._last_error is first or cfs._last_error == first

    def test_unsolicited_0x0a_push_reaches_the_sink(self):
        """A CRC-valid func-0x0A frame with NO waiter armed must be routed to the sink
        (pre-fix it was dropped, so a box-pushed fault never reached the host)."""
        from creality_cfs import build_message, CMD_GET_BOX_STATE
        _, cfs, _ = _wired()
        cfs._pending = None
        cfs._pending_match = None
        frame = build_message(0x01, 0x51, CMD_GET_BOX_STATE, b"\x1c\x21\x00\x00")
        cfs._dispatch_rx(frame, 0.0)
        assert cfs._last_error["code"] == 846

    def test_unsolicited_bad_crc_0x0a_is_not_dispatched(self):
        """Garbage must not latch an error: the unsolicited path is CRC-gated."""
        from creality_cfs import build_message, CMD_GET_BOX_STATE
        _, cfs, _ = _wired()
        cfs._pending = None
        cfs._pending_match = None
        frame = bytearray(build_message(0x01, 0x51, CMD_GET_BOX_STATE, b"\x1c\x21\x00\x00"))
        frame[-1] ^= 0xFF   # corrupt the CRC
        cfs._dispatch_rx(bytes(frame), 0.0)
        assert cfs._last_error is None

    def test_polled_get_box_state_dispatches_fault_status(self):
        """The polled path also routes the reply STATUS byte through the sink."""
        from creality_cfs import build_message, CMD_GET_BOX_STATE
        hw, cfs, ser = _wired()
        reply = build_message(0x01, 0x52, CMD_GET_BOX_STATE, b"\x1c\x21\x00\x00")

        def _write(data):
            ser.response_queue.append(reply[:3])
            ser.response_queue.append(reply[3:])

        ser.write.side_effect = _write
        st = cfs.get_box_state(0x01)
        assert st is not None
        assert cfs._last_error["code"] == 847


# ===========================================================================
# Buffer-related error keys (buffer spec additions)
# ===========================================================================

class TestBufferErrorKeys:

    def test_buffer_spec_keys_present(self):
        assert CFS_ERROR_KEYS[845] == "the nozzle is blocked"
        assert "enwind" in CFS_ERROR_KEYS[847]
        assert "buffer empty limit" in CFS_ERROR_KEYS[851]
        assert CFS_ERROR_KEYS[860] == "buffer error"
