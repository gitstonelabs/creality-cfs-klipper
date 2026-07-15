# SPDX-License-Identifier: GPL-3.0-or-later
"""
test_connect_init_slotreads.py: coverage for the CONNECT-INIT path and the
per-slot read/ingest/status seams of CrealityCFS.

Covers (via the wired MockCFSHardware transport, real bytes end-to-end):
  _auto_init_callback        reactor callback that runs addressing then arms the probe
  _connect_probe             bounded self-re-arming post-addressing connect probe
  _connect_init              the stock connect-init burst for one box
  _run_preload_sequence      the one-shot startup pre-load self-check (idempotence guard)
  read_material              0x02 slot-material ASCII map
  read_remain                0x03 positional per-slot remain bytes
  get_buffer_state           0x0C buffer-node block (None branch on a non-responder addr)
  _ingest_slot_reads         pure fold of material+remain into the slot cache
  get_status                 Klipper status export dict
  get_version_info           0xF0 ASCII firmware string (no-response branch)

All tests wire a fresh MockCFSHardware and call cfs._run_auto_addressing() first so the
target box is mapped/online and the transport is wired. No physical hardware.
"""

import sys
import os
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from creality_cfs import (
    BoxAddressEntry,
    CMD_SET_BOX_MODE,
    CMD_GET_VERSION_SN,
    CMD_SET_PRE_LOADING,
    CMD_GET_HARDWARE_STATUS,
    CMD_GET_FILAMENT_SENSOR_STATE,
    CMD_GET_REMAIN_LEN,
    CMD_GET_BOX_STATE,
    PRELOAD_MASK_ALL,
)

from tests.mock_cfs import MockCFSHardware
from tests.conftest import make_wired_controller


def _wired(box_count=1, retry_count=1):
    """Fresh mock + wired controller with addresses already assigned/online."""
    hw = MockCFSHardware(box_count=box_count)
    cfs, ser = make_wired_controller(hw, box_count=box_count, retry_count=retry_count)
    cfs._run_auto_addressing()
    return hw, cfs, ser


# ===========================================================================
# _connect_init  (drives the full stock burst end-to-end)
# ===========================================================================

class TestConnectInit:

    def test_connect_init_runs_full_burst_and_populates_slots(self):
        hw, cfs, _ = _wired()
        # sanity: the box came online so the burst has a live target
        assert cfs._box_table[0].online == BoxAddressEntry.ONLINE_ONLINE

        cfs._connect_init(0x01)

        funcs = hw.get_received_funcs()
        # enter feed mode (0x04), version/SN (0x14), pre-load (0x0D),
        # hardware status (0x08), material read (0x02), remain read (0x03) all fired
        assert CMD_SET_BOX_MODE in funcs
        assert CMD_GET_VERSION_SN in funcs
        assert CMD_SET_PRE_LOADING in funcs
        assert CMD_GET_HARDWARE_STATUS in funcs
        assert CMD_GET_FILAMENT_SENSOR_STATE in funcs
        assert CMD_GET_REMAIN_LEN in funcs

        # slot A present from the mock's read_remain [0x64,0,0,0] + material 'A:unknown;...'
        assert 0 in cfs._slots
        assert cfs._slots[0]["present"] is True
        # 'unknown' is a present-but-unidentified token, kept as the material label
        assert cfs._slots[0]["material"] == "unknown"
        assert cfs._slots[0]["remain"] == 0x64
        # B-D report remain 0x00 (empty, but reported) -> folded as present:False
        for idx in (1, 2, 3):
            assert idx in cfs._slots
            assert cfs._slots[idx]["present"] is False
            assert cfs._slots[idx]["material"] is None
            assert cfs._slots[idx]["remain"] == -1

    def test_connect_init_never_raises_on_silent_box(self):
        hw, cfs, _ = _wired()
        # every command times out -> the burst must swallow it, not raise
        for func in (CMD_SET_BOX_MODE, CMD_GET_VERSION_SN, CMD_SET_PRE_LOADING,
                     CMD_GET_HARDWARE_STATUS, CMD_GET_FILAMENT_SENSOR_STATE,
                     CMD_GET_REMAIN_LEN):
            hw.inject_error(hw.ERROR_TIMEOUT, on_command=func)
        # should return cleanly (non-fatal on any silent step)
        cfs._connect_init(0x01)


# ===========================================================================
# _run_preload_sequence  (one-shot idempotence guard)
# ===========================================================================

class TestPreloadSequence:

    def test_preload_runs_once_then_guards(self):
        hw, cfs, _ = _wired()

        before = hw.get_received_funcs().count(CMD_SET_PRE_LOADING)
        assert before == 0

        cfs._run_preload_sequence(0x01)
        after_first = hw.get_received_funcs().count(CMD_SET_PRE_LOADING)
        # the stock burst sends TWO 0x0D frames ([00 01] begin + [0f 01] phase 1)
        assert after_first == 2
        assert cfs._preload_done.get(0x01) is True

        # second call is short-circuited by the _preload_done guard: returns None,
        # emits NO further 0x0D frames on the wire
        result = cfs._run_preload_sequence(0x01)
        assert result is None
        after_second = hw.get_received_funcs().count(CMD_SET_PRE_LOADING)
        assert after_second == after_first  # no new preload burst

    def test_preload_returns_hardware_flag(self):
        hw, cfs, _ = _wired()
        # mock's get_hardware_status returns flag byte 0x01
        flag = cfs._run_preload_sequence(0x01)
        assert flag == 0x01


# ===========================================================================
# _connect_probe  (single-shot per online box + silent re-arm)
# ===========================================================================

class TestConnectProbe:

    def test_probe_connects_online_box(self):
        hw, cfs, _ = _wired()
        cfs._connected = set()
        cfs._probe_attempts = 0

        cfs._connect_probe(0.0)

        # the online box answered get_box_state -> _connect_init ran -> addr added
        assert 0x01 in cfs._connected
        # the burst commands appeared on the wire
        funcs = hw.get_received_funcs()
        assert CMD_GET_BOX_STATE in funcs
        assert CMD_SET_BOX_MODE in funcs

    def test_silent_box_stays_out_and_rearms(self):
        hw, cfs, _ = _wired()
        cfs._connected = set()
        cfs._probe_attempts = 0
        # the probe read (GET_BOX_STATE) returns None -> box stays unconnected
        hw.inject_error(hw.ERROR_TIMEOUT, on_command=CMD_GET_BOX_STATE)

        # isolate the re-arm assertion from the register_callback calls made during setup
        cfs.reactor.register_callback.reset_mock()

        cfs._connect_probe(0.0)

        # box never connect-inited
        assert 0x01 not in cfs._connected
        # and the probe re-registered itself (remaining online box + attempts under the cap)
        assert cfs.reactor.register_callback.called
        cb_args = [c.args[0] for c in cfs.reactor.register_callback.call_args_list]
        assert cfs._connect_probe in cb_args


# ===========================================================================
# _auto_init_callback
# ===========================================================================

class TestAutoInitCallback:

    def test_auto_init_arms_probe_on_success(self):
        hw, cfs, _ = _wired()
        cfs._run_auto_addressing = mock.MagicMock(return_value=1)
        cfs._probe_attempts = 99
        cfs.reactor.register_callback.reset_mock()

        cfs._auto_init_callback(0.0)

        # addressing ran, the attempt counter reset, the connect probe was armed
        cfs._run_auto_addressing.assert_called_once()
        assert cfs._probe_attempts == 0
        assert cfs.reactor.register_callback.called
        cb_args = [c.args[0] for c in cfs.reactor.register_callback.call_args_list]
        assert cfs._connect_probe in cb_args

    def test_auto_init_swallows_addressing_error_and_does_not_arm(self):
        hw, cfs, _ = _wired()
        cfs._run_auto_addressing = mock.MagicMock(side_effect=RuntimeError("boom"))
        cfs.reactor.register_callback.reset_mock()

        # must not raise
        cfs._auto_init_callback(0.0)

        # the probe was NOT registered (early return after logging)
        cb_args = [c.args[0] for c in cfs.reactor.register_callback.call_args_list]
        assert cfs._connect_probe not in cb_args


# ===========================================================================
# read_material / read_remain / get_buffer_state
# ===========================================================================

class TestSlotReads:

    def test_read_material_all_slots(self):
        hw, cfs, _ = _wired()
        mat = cfs.read_material(0x01, PRELOAD_MASK_ALL)
        assert mat == "A:unknown;B:none;C:none;D:none;"

    def test_read_material_none_on_timeout(self):
        hw, cfs, _ = _wired()
        hw.inject_error(hw.ERROR_TIMEOUT, on_command=CMD_GET_FILAMENT_SENSOR_STATE)
        assert cfs.read_material(0x01, PRELOAD_MASK_ALL) is None

    def test_read_remain_all_slots(self):
        hw, cfs, _ = _wired()
        rem = cfs.read_remain(0x01, PRELOAD_MASK_ALL)
        assert rem == [0x64, 0x00, 0x00, 0x00]

    def test_read_remain_masked_uses_sentinels(self):
        hw, cfs, _ = _wired()
        # mask 0x01 (slot A only) -> B/C/D positions carry the 0xFF not-in-mask sentinel
        rem = cfs.read_remain(0x01, 0x01)
        assert rem == [0x64, 0xFF, 0xFF, 0xFF]

    def test_read_remain_none_on_timeout(self):
        hw, cfs, _ = _wired()
        hw.inject_error(hw.ERROR_TIMEOUT, on_command=CMD_GET_REMAIN_LEN)
        assert cfs.read_remain(0x01, PRELOAD_MASK_ALL) is None

    def test_get_buffer_state_none_on_nonresponder(self):
        hw, cfs, _ = _wired()
        # the mock has no responder for a buffer/feeder node addr like 0x81 ->
        # process_message returns None -> get_buffer_state hits the None branch
        assert cfs.get_buffer_state(0x81) is None


# ===========================================================================
# _ingest_slot_reads  (pure fold, no bus)
# ===========================================================================

class TestIngestSlotReads:

    def test_material_and_remain_fold(self):
        hw, cfs, _ = _wired()
        updated = cfs._ingest_slot_reads("A:PLA;B:none;C:none;D:none;", [100, 0, 0, 0])
        # B/C/D report remain 0x00 (reported-but-empty) so they too fold in as present:False
        assert updated == {0, 1, 2, 3}
        assert cfs._slots[0] == {"present": True, "material": "PLA", "remain": 100}
        assert cfs._slots[1]["present"] is False
        assert cfs._slots[1]["material"] is None
        assert cfs._slots[1]["remain"] == -1

    def test_ff_sentinel_does_not_mark_present(self):
        hw, cfs, _ = _wired()
        # remain [100,255,255,255] with the all-slots mask: B/C/D carry the 0xFF
        # not-reported sentinel, so remain gives NO signal there; only the 'none'
        # material fallback resolves them -> present:False (never present:True).
        updated = cfs._ingest_slot_reads("A:PLA;B:none;C:none;D:none;",
                                         [100, 255, 255, 255], slot_mask=0x0F)
        # only slot A is present; the 0xFF sentinel never promotes a slot to present
        assert cfs._slots[0]["present"] is True
        for idx in (1, 2, 3):
            assert cfs._slots[idx]["present"] is False
        # and no 0xFF remain leaked into the cache as a phantom filament reading
        assert cfs._slots[0]["remain"] == 100

    def test_material_only_fallback_when_remain_none(self):
        hw, cfs, _ = _wired()
        # remain None -> material becomes the presence signal
        updated = cfs._ingest_slot_reads("A:PETG;B:none;C:none;D:none;", None)
        # A present via material; B/C/D 'none' fold as present:False
        assert 0 in updated
        assert cfs._slots[0]["present"] is True
        assert cfs._slots[0]["material"] == "PETG"
        # remain unknown via the material-only path -> the empty-signal default
        assert cfs._slots[0]["remain"] == -1
        for idx in (1, 2, 3):
            assert cfs._slots[idx]["present"] is False

    def test_both_none_yields_empty_set(self):
        hw, cfs, _ = _wired()
        assert cfs._ingest_slot_reads(None, None) == set()
        assert cfs._slots == {}


# ===========================================================================
# get_status
# ===========================================================================

class TestGetStatus:

    def test_status_shape_and_defaults(self):
        hw, cfs, _ = _wired()
        st = cfs.get_status(0.0)

        assert st["is_connected"] is True
        assert st["box_count"] == 1
        # the single box is online
        assert st["online"] == {"box1": True}
        # no active tool -> -1 sentinel
        assert st["active_tool"] == -1
        # empty slot cache before any read
        assert st["slots"] == {}

    def test_status_mirrors_slots_and_active_tool(self):
        hw, cfs, _ = _wired()
        cfs._connect_init(0x01)          # populate the slot cache
        cfs._active_tool = 0
        st = cfs.get_status(0.0)

        assert st["active_tool"] == 0
        # slots mirror is keyed by str(idx) and is a copy of the cache
        assert "0" in st["slots"]
        assert st["slots"]["0"]["present"] is True
        # returned dicts are copies, not the live cache objects
        st["slots"]["0"]["present"] = False
        assert cfs._slots[0]["present"] is True


# ===========================================================================
# get_version_info  (0xF0, no-response branch)
# ===========================================================================

class TestGetVersionInfo:

    def test_version_info_empty_on_no_response(self):
        hw, cfs, _ = _wired()
        # the mock has NO 0xF0 responder -> _send_command returns None ->
        # get_version_info returns '' (empty string, NOT a raise)
        result = cfs.get_version_info(0x01)
        assert result == ""
