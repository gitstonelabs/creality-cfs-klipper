"""
test_integration.py — Integration tests for the CrealityCFS module.

These tests exercise complete request-response cycles using MockCFSHardware
wired through a mock serial transport.  Every test path exercises:
  1. build_message() — frame construction
  2. serial write — transmission
  3. MockCFSHardware.process_message() — CRC validation + response generation
  4. _read_response() — frame reassembly from chunked reads
  5. parse_message() — response parsing and CRC validation

No physical hardware is required.

Test suites:
  TestInitializationWorkflow  — full 5-step auto-addressing
  TestStatusPolling           — GET_BOX_STATE for all boxes
  TestVersionQuery            — GET_VERSION_SN for all boxes
  TestSingleBoxWorkflow       — end-to-end single-box sequence
  TestBoxAddressAllocation    — _allocate_address() priority logic
"""

import sys
import os
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from creality_cfs import (
    BoxAddressEntry,
    STATUS_ADDRESSING,
    STATUS_OPERATIONAL,
    CMD_LOADER_TO_APP,
    CMD_GET_SLAVE_INFO,
    CMD_SET_SLAVE_ADDR,
    CMD_ONLINE_CHECK,
    CMD_GET_ADDR_TABLE,
    CMD_SET_BOX_MODE,
    CMD_GET_BOX_STATE,
    CMD_SET_PRE_LOADING,
    CMD_GET_VERSION_SN,
)

from tests.mock_cfs import MockCFSHardware
from tests.conftest import make_wired_controller


# ===========================================================================
# TestInitializationWorkflow
# ===========================================================================

class TestInitializationWorkflow:
    """Test the full 5-step auto-addressing initialization workflow."""

    def test_initialization_workflow_4_boxes_all_online(self):
        """Full init with 4 boxes results in 4 boxes at ONLINE_ONLINE state.

        Tests the complete path: LOADER_TO_APP broadcast -> 4x GET_SLAVE_INFO
        discovery -> 4x SET_SLAVE_ADDR -> 4x ONLINE_CHECK -> 4x GET_ADDR_TABLE.
        """
        hw = MockCFSHardware(box_count=4)
        cfs, _ = make_wired_controller(hw, box_count=4, retry_count=1)

        count = cfs._run_auto_addressing()

        assert count == 4
        for i in range(4):
            assert cfs._box_table[i].online == BoxAddressEntry.ONLINE_ONLINE

    def test_initialization_workflow_1_box(self):
        """Init with 1 box assigns only addr 0x01 and returns count=1."""
        hw = MockCFSHardware(box_count=1)
        cfs, _ = make_wired_controller(hw, box_count=1, retry_count=1)

        count = cfs._run_auto_addressing()

        assert count == 1
        assert cfs._box_table[0].online == BoxAddressEntry.ONLINE_ONLINE

    def test_initialization_workflow_2_boxes(self):
        """Init with 2 boxes assigns addr 0x01 and 0x02."""
        hw = MockCFSHardware(box_count=2)
        cfs, _ = make_wired_controller(hw, box_count=2, retry_count=1)

        count = cfs._run_auto_addressing()

        assert count == 2
        assert cfs._box_table[0].online == BoxAddressEntry.ONLINE_ONLINE
        assert cfs._box_table[1].online == BoxAddressEntry.ONLINE_ONLINE

    def test_initialization_workflow_cmd_sequence_order(self):
        """Init sends commands in the correct 5-step order.

        Verifies: LOADER_TO_APP -> GET_SLAVE_INFO -> SET_SLAVE_ADDR ->
        ONLINE_CHECK -> GET_ADDR_TABLE (per auto_addr_wrapper.py pattern).
        """
        hw = MockCFSHardware(box_count=1)
        cfs, ser = make_wired_controller(hw, box_count=1, retry_count=1)

        from creality_cfs import parse_message as _parse
        written_funcs = []
        orig = ser.write.side_effect

        def _spy(data):
            p = _parse(data)
            if p:
                written_funcs.append(p["func"])
            if orig:
                orig(data)

        ser.write.side_effect = _spy
        cfs._run_auto_addressing()

        # First command must always be LOADER_TO_APP
        assert written_funcs[0] == CMD_LOADER_TO_APP

        # GET_SLAVE_INFO must appear before SET_SLAVE_ADDR
        if CMD_SET_SLAVE_ADDR in written_funcs:
            assert written_funcs.index(CMD_GET_SLAVE_INFO) < written_funcs.index(CMD_SET_SLAVE_ADDR)

        # ONLINE_CHECK must appear before GET_ADDR_TABLE (when both present)
        if CMD_ONLINE_CHECK in written_funcs and CMD_GET_ADDR_TABLE in written_funcs:
            assert written_funcs.index(CMD_ONLINE_CHECK) < written_funcs.index(CMD_GET_ADDR_TABLE)

    def test_initialization_workflow_box_tables_have_uniids(self):
        """After init, each mapped box entry has a non-default UniID."""
        hw = MockCFSHardware(box_count=2)
        cfs, _ = make_wired_controller(hw, box_count=2, retry_count=1)

        cfs._run_auto_addressing()

        for i in range(2):
            entry = cfs._box_table[i]
            assert entry.mapped is True
            # UniID should not be the initial [0x00] default
            assert entry.uniid != [0x00], f"Box {i+1} has default UniID after init"

    def test_initialization_workflow_is_connected_precondition(self, cfs_controller):
        """_run_auto_addressing() only runs when is_connected=True.

        If serial is disconnected, _send_command raises RuntimeError before
        any bytes are sent.
        """
        cfs_controller.is_connected = False
        with pytest.raises(RuntimeError, match="not connected"):
            cfs_controller._run_auto_addressing()


# ===========================================================================
# TestStatusPolling
# ===========================================================================

class TestStatusPolling:
    """Test GET_BOX_STATE polling for all boxes."""

    @pytest.mark.parametrize("addr", [1, 2, 3, 4])
    def test_status_polling_each_box_returns_state_dict(self, addr):
        """get_box_state() for each addr returns dict with state, raw, addr keys."""
        hw = MockCFSHardware(box_count=4)
        cfs, _ = make_wired_controller(hw, box_count=4, retry_count=1)

        # Initialize so the transport is wired
        cfs._run_auto_addressing()

        # Reset discovery queue for re-use; hw should respond to GET_BOX_STATE
        result = cfs.get_box_state(addr)
        assert "state" in result
        assert "raw" in result
        assert "addr" in result
        assert result["addr"] == addr

    def test_status_polling_raw_is_4_bytes(self):
        """get_box_state() 'raw' field is exactly 4 bytes from the mock response.

        Mock returns [0x1C, 0x14, 0x00, 0x00] per captured frame.
        """
        hw = MockCFSHardware(box_count=1)
        cfs, _ = make_wired_controller(hw, box_count=1, retry_count=1)
        cfs._run_auto_addressing()

        result = cfs.get_box_state(0x01)
        assert len(result["raw"]) == 4

    def test_status_polling_state_byte_matches_first_data_byte(self):
        """get_box_state() 'state' is the first byte of the 4-byte response data."""
        hw = MockCFSHardware(box_count=1)
        cfs, _ = make_wired_controller(hw, box_count=1, retry_count=1)
        cfs._run_auto_addressing()

        result = cfs.get_box_state(0x01)
        assert result["state"] == result["raw"][0]

    def test_status_polling_all_4_boxes_sequential(self):
        """get_box_state() can be called sequentially for all 4 boxes."""
        hw = MockCFSHardware(box_count=4)
        cfs, _ = make_wired_controller(hw, box_count=4, retry_count=1)
        cfs._run_auto_addressing()

        results = []
        for addr in range(1, 5):
            results.append(cfs.get_box_state(addr))

        assert len(results) == 4
        for i, r in enumerate(results):
            assert r["addr"] == i + 1


# ===========================================================================
# TestVersionQuery
# ===========================================================================

class TestVersionQuery:
    """Test GET_VERSION_SN for all boxes."""

    @pytest.mark.parametrize("addr", [1, 2, 3, 4])
    def test_version_query_each_box_returns_string(self, addr):
        """get_version_sn() returns a non-empty string for each box."""
        hw = MockCFSHardware(box_count=4)
        cfs, _ = make_wired_controller(hw, box_count=4, retry_count=1)
        cfs._run_auto_addressing()

        version = cfs.get_version_sn(addr)
        assert isinstance(version, str)
        assert len(version) > 0

    def test_version_query_box_1_returns_expected_prefix(self):
        """get_version_sn() for box 1 returns version starting with '11010000'."""
        hw = MockCFSHardware(box_count=1)
        cfs, _ = make_wired_controller(hw, box_count=1, retry_count=1)
        cfs._run_auto_addressing()

        version = cfs.get_version_sn(0x01)
        assert version.startswith("11010000"), f"Version '{version}' missing expected prefix"

    def test_version_query_each_box_returns_unique_version(self):
        """All 4 boxes return different version strings.

        The mock assigns unique suffixes to each box to reflect real hardware
        where each box has a distinct serial number.
        """
        hw = MockCFSHardware(box_count=4)
        cfs, _ = make_wired_controller(hw, box_count=4, retry_count=1)
        cfs._run_auto_addressing()

        versions = [cfs.get_version_sn(addr) for addr in range(1, 5)]
        assert len(set(versions)) == 4, f"Expected 4 unique versions, got: {versions}"


# ===========================================================================
# TestSingleBoxWorkflow
# ===========================================================================

class TestSingleBoxWorkflow:
    """End-to-end test of connecting, addressing, and operating a single box."""

    def test_single_box_connect_address_query_state(self):
        """Full single-box workflow: init -> get_box_state -> verify state."""
        hw = MockCFSHardware(box_count=1)
        cfs, _ = make_wired_controller(hw, box_count=1, retry_count=1)

        # Step 1: Initialize (5-step addressing)
        online_count = cfs._run_auto_addressing()
        assert online_count == 1

        # Step 2: Query state
        state = cfs.get_box_state(0x01)
        assert state["state"] == 0x1C

    def test_single_box_connect_address_set_mode_standby(self):
        """Single-box: init -> set_box_mode(standby=0x00) -> verify ACK."""
        hw = MockCFSHardware(box_count=1)
        cfs, _ = make_wired_controller(hw, box_count=1, retry_count=1)

        cfs._run_auto_addressing()
        result = cfs.set_box_mode(0x01, 0x00, 0x01)
        assert result is True

    def test_single_box_connect_address_set_preload(self):
        """Single-box: init -> set_pre_loading(mask=0x0F, enable=1) -> verify ACK."""
        hw = MockCFSHardware(box_count=1)
        cfs, _ = make_wired_controller(hw, box_count=1, retry_count=1)

        cfs._run_auto_addressing()
        result = cfs.set_pre_loading(0x01, 0x0F, 0x01)
        assert result is True

    def test_single_box_connect_version_query(self):
        """Single-box: init -> get_version_sn -> returns non-empty string."""
        hw = MockCFSHardware(box_count=1)
        cfs, _ = make_wired_controller(hw, box_count=1, retry_count=1)

        cfs._run_auto_addressing()
        version = cfs.get_version_sn(0x01)
        assert len(version) > 0


# ===========================================================================
# TestBoxAddressAllocation
# ===========================================================================

class TestBoxAddressAllocation:
    """Tests for _allocate_address() priority logic in CrealityCFS."""

    def test_allocate_address_unmapped_slot_uses_first_free(self, cfs_controller):
        """_allocate_address() assigns the first unmapped slot (priority 2)."""
        uniid = [0x01, 0x02, 0x03]
        addr = cfs_controller._allocate_address(uniid)
        assert addr == 0x01  # first slot
        assert cfs_controller._box_table[0].mapped is True

    def test_allocate_address_second_call_uses_next_slot(self, cfs_controller):
        """Two consecutive new-device allocations get sequential addresses."""
        uniid1 = [0x01, 0x02, 0x03]
        uniid2 = [0x04, 0x05, 0x06]
        addr1 = cfs_controller._allocate_address(uniid1)
        addr2 = cfs_controller._allocate_address(uniid2)
        assert addr1 == 0x01
        assert addr2 == 0x02

    def test_allocate_address_returns_negative_when_all_slots_taken(self, cfs_controller):
        """_allocate_address() returns -1 when all 4 slots are online (no free slot)."""
        # Fill all slots as online
        for entry in cfs_controller._box_table:
            entry.mapped = True
            entry.online = BoxAddressEntry.ONLINE_ONLINE

        result = cfs_controller._allocate_address([0x99, 0x88])
        assert result == -1

    def test_allocate_address_priority1_remaps_known_uniid(self, cfs_controller):
        """Priority 1: existing offline slot with matching UniID is reused."""
        uniid = [0x01, 0x02, 0x03, 0x04]
        # Pre-populate slot 0 as mapped but offline
        cfs_controller._box_table[0].mapped = True
        cfs_controller._box_table[0].uniid = uniid
        cfs_controller._box_table[0].online = BoxAddressEntry.ONLINE_OFFLINE

        addr = cfs_controller._allocate_address(uniid)
        assert addr == 0x01  # reuses slot 0
        assert cfs_controller._box_table[0].online == BoxAddressEntry.ONLINE_WAIT_ACK

    def test_allocate_address_priority3_overwrites_mismatched_offline_slot(self, cfs_controller):
        """Priority 3: when all slots are mapped but offline, overwrite mismatched UniID."""
        old_uniid = [0x11, 0x22, 0x33]
        new_uniid = [0x44, 0x55, 0x66]

        # Mark all slots as mapped + offline with old UniID
        for entry in cfs_controller._box_table:
            entry.mapped = True
            entry.uniid = list(old_uniid)
            entry.online = BoxAddressEntry.ONLINE_OFFLINE

        addr = cfs_controller._allocate_address(new_uniid)
        # Should have been assigned to slot 0 (first match for priority 3)
        assert addr >= 1
        # The slot's UniID should be updated
        slot = addr - 1
        assert cfs_controller._box_table[slot].uniid == new_uniid
