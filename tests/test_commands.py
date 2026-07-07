# SPDX-License-Identifier: GPL-3.0-or-later
"""
test_commands.py: Tests for individual CFS command implementations in CrealityCFS.

Tests use a MockCFSHardware wired through a mock serial transport so every
test exercises real build_message / _send_command / _read_response code paths
without physical hardware.

Covered commands:
  0x0B CMD_LOADER_TO_APP     wake broadcast
  0xA1 CMD_GET_SLAVE_INFO    discovery broadcast
  0xA0 CMD_SET_SLAVE_ADDR    address assignment
  0xA2 CMD_ONLINE_CHECK      verify assignment
  0xA3 CMD_GET_ADDR_TABLE    confirm full table
  0x04 CMD_SET_BOX_MODE      set operating mode (addr, mode, param)
  0x0A CMD_GET_BOX_STATE     query 4-byte box state
  0x0D CMD_SET_PRE_LOADING   configure pre-loading slot mask
  0x14 CMD_GET_VERSION_SN    query 22-byte ASCII version/SN

Covered elsewhere (tests/test_stubs.py):
  0x10 CMD_EXTRUDE_PROCESS   sensor-gated load choreography (v1.4.0)
  0x11 CMD_RETRUDE_PROCESS   START/FINISH unload pair (v1.4.0)

All tests are independent: no shared mutable state between tests.
"""

import sys
import os
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from creality_cfs import (
    build_message,
    parse_message,
    CrealityCFS,
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
    BOX_STATE_LOADED_B3,
    BOX_STATE_FEEDING_B3,
    BOX_EVENT_IDLE,
    PRELOAD_PHASE_DISARM,
    PRELOAD_PHASE_SLOT_REARM,
    BROADCAST_ADDR_MB,
    BROADCAST_ADDR_ALL,
    ADDR_BOX_MIN,
    ADDR_BOX_MAX,
    DEV_TYPE_MB,
)

from tests.mock_cfs import MockCFSHardware
from tests.conftest import make_wired_controller


# ===========================================================================
# CMD_LOADER_TO_APP (0x0B)
# ===========================================================================

class TestCmdLoaderToApp:
    """Tests for CMD_LOADER_TO_APP (0x0B) wake broadcast."""

    def test_cmd_loader_to_app_message_format_status_addressing(self):
        """LOADER_TO_APP must use STATUS=0x00 (addressing phase) per protocol."""
        msg = build_message(BROADCAST_ADDR_ALL, STATUS_ADDRESSING, CMD_LOADER_TO_APP,
                            data=bytes([0x01]))
        assert msg[3] == STATUS_ADDRESSING == 0x00

    def test_cmd_loader_to_app_message_format_addr_broadcast_all(self):
        """LOADER_TO_APP is sent to BROADCAST_ADDR_ALL (0xFF)."""
        msg = build_message(BROADCAST_ADDR_ALL, STATUS_ADDRESSING, CMD_LOADER_TO_APP,
                            data=bytes([0x01]))
        assert msg[1] == BROADCAST_ADDR_ALL == 0xFF

    def test_cmd_loader_to_app_message_format_func_code(self):
        """LOADER_TO_APP has func code 0x0B."""
        msg = build_message(BROADCAST_ADDR_ALL, STATUS_ADDRESSING, CMD_LOADER_TO_APP,
                            data=bytes([0x01]))
        assert msg[4] == CMD_LOADER_TO_APP == 0x0B

    def test_cmd_loader_to_app_data_payload_is_0x01(self):
        """LOADER_TO_APP data payload is [0x01] (one byte)."""
        msg = build_message(BROADCAST_ADDR_ALL, STATUS_ADDRESSING, CMD_LOADER_TO_APP,
                            data=bytes([0x01]))
        assert msg[5] == 0x01

    def test_cmd_loader_to_app_sent_during_auto_addressing(self, cfs_controller):
        """_run_auto_addressing() sends CMD_LOADER_TO_APP in step 1.

        The controller's write() must receive a frame with func=0x0B as
        the first transmission.
        """
        written_frames = []

        def _capture_write(data):
            written_frames.append(data)
            # No response queued: LOADER_TO_APP expects no reply
            return None

        cfs_controller._serial.write.side_effect = _capture_write
        # Patch _discover_slaves to avoid looping on empty queue
        cfs_controller._discover_slaves = mock.MagicMock(return_value=[])

        cfs_controller._run_auto_addressing()

        assert len(written_frames) >= 1
        first = parse_message(written_frames[0])
        assert first is not None
        assert first["func"] == CMD_LOADER_TO_APP


# ===========================================================================
# CMD_GET_SLAVE_INFO (0xA1)
# ===========================================================================

class TestCmdGetSlaveInfo:
    """Tests for CMD_GET_SLAVE_INFO (0xA1) discovery broadcast."""

    def test_cmd_get_slave_info_message_to_broadcast_mb_addr(self):
        """GET_SLAVE_INFO is addressed to BROADCAST_ADDR_MB (0xFE)."""
        msg = build_message(BROADCAST_ADDR_MB, STATUS_ADDRESSING, CMD_GET_SLAVE_INFO,
                            bytes([BROADCAST_ADDR_MB, BROADCAST_ADDR_MB]))
        assert msg[1] == BROADCAST_ADDR_MB == 0xFE

    def test_cmd_get_slave_info_message_status_addressing(self):
        """GET_SLAVE_INFO uses STATUS=0x00."""
        msg = build_message(BROADCAST_ADDR_MB, STATUS_ADDRESSING, CMD_GET_SLAVE_INFO,
                            bytes([BROADCAST_ADDR_MB, BROADCAST_ADDR_MB]))
        assert msg[3] == STATUS_ADDRESSING

    def test_cmd_get_slave_info_matches_captured_frame(self):
        """GET_SLAVE_INFO broadcast matches interceptty capture exactly."""
        msg = build_message(BROADCAST_ADDR_MB, STATUS_ADDRESSING, CMD_GET_SLAVE_INFO,
                            bytes([BROADCAST_ADDR_MB, BROADCAST_ADDR_MB]))
        assert msg == b'\xf7\xfe\x05\x00\xa1\xfe\xfe\xf8'

    def test_cmd_get_slave_info_response_parsed_box_1(self):
        """GET_SLAVE_INFO response from box 1 is parsed correctly in _discover_slaves."""
        hw = MockCFSHardware(box_count=1)
        cfs, _ = make_wired_controller(hw, box_count=1, retry_count=1)

        discovered = cfs._discover_slaves()
        assert len(discovered) == 1
        assert discovered[0].addr == 0x01
        assert discovered[0].mapped is True


# ===========================================================================
# CMD_SET_SLAVE_ADDR (0xA0)
# ===========================================================================

class TestCmdSetSlaveAddr:
    """Tests for CMD_SET_SLAVE_ADDR (0xA0) address assignment."""

    def test_cmd_set_slave_addr_payload_starts_with_target_addr(self):
        """SET_SLAVE_ADDR payload byte[0] is the target address."""
        uniid = [0x01, 0x00, 0x5C, 0x51, 0x30, 0x03, 0x14, 0x91, 0xB0, 0x15, 0x4C, 0x30]
        msg = build_message(BROADCAST_ADDR_MB, STATUS_ADDRESSING, CMD_SET_SLAVE_ADDR,
                            bytes([0x01]) + bytes(uniid))
        assert msg[5] == 0x01  # target_addr is first data byte

    def test_cmd_set_slave_addr_payload_contains_uniid(self):
        """SET_SLAVE_ADDR payload bytes[1:] are the UniID."""
        uniid = [0x01, 0x00, 0x5C, 0x51, 0x30, 0x03, 0x14, 0x91, 0xB0, 0x15, 0x4C, 0x30]
        msg = build_message(BROADCAST_ADDR_MB, STATUS_ADDRESSING, CMD_SET_SLAVE_ADDR,
                            bytes([0x01]) + bytes(uniid))
        extracted_uniid = list(msg[6:-1])
        assert extracted_uniid == uniid

    def test_cmd_set_slave_addr_marks_entry_acked_on_success(self, cfs_controller):
        """_set_slave_addr() sets entry.acked=True and online=ONLINE when response received."""
        # Build a mock response that includes DEV_TYPE_MB
        uniid = [0x01, 0x00, 0x5C, 0x51, 0x30, 0x03, 0x14, 0x91, 0xB0, 0x15, 0x4C, 0x30]
        resp_data = bytes([DEV_TYPE_MB, 0x00]) + bytes(uniid)
        resp_msg = build_message(0x01, STATUS_ADDRESSING, CMD_SET_SLAVE_ADDR, resp_data)

        cfs_controller._serial.response_queue.append(resp_msg[:3])
        cfs_controller._serial.response_queue.append(resp_msg[3:])
        cfs_controller._box_table[0].mapped = True

        result = cfs_controller._set_slave_addr(BROADCAST_ADDR_MB, 0x01, uniid)

        assert result is True
        assert cfs_controller._box_table[0].acked is True
        assert cfs_controller._box_table[0].online == BoxAddressEntry.ONLINE_ONLINE

    def test_cmd_set_slave_addr_returns_false_on_no_response(self, cfs_controller):
        """_set_slave_addr() returns False when no response is received."""
        # Queue is empty: no response
        uniid = [0x01, 0x00, 0x5C, 0x51, 0x30, 0x03, 0x14, 0x91, 0xB0, 0x15, 0x4C, 0x30]
        result = cfs_controller._set_slave_addr(BROADCAST_ADDR_MB, 0x01, uniid)
        assert result is False


# ===========================================================================
# CMD_ONLINE_CHECK (0xA2)
# ===========================================================================

class TestCmdOnlineCheck:
    """Tests for CMD_ONLINE_CHECK (0xA2) per-box verification."""

    def test_cmd_online_check_message_format_slave_1(self):
        """CMD_ONLINE_CHECK to slave 1 matches captured b'\\xf7\\x01\\x03\\x00\\xa2\\xda'."""
        msg = build_message(0x01, STATUS_ADDRESSING, CMD_ONLINE_CHECK)
        assert msg == b'\xf7\x01\x03\x00\xa2\xda'

    def test_cmd_online_check_empty_data_payload(self):
        """CMD_ONLINE_CHECK has no data payload: LENGTH=3."""
        msg = build_message(0x01, STATUS_ADDRESSING, CMD_ONLINE_CHECK)
        assert msg[2] == 3
        assert len(msg) == 6

    def test_cmd_online_check_marks_box_online_on_response(self, cfs_controller):
        """_online_check() sets entry.online=ONLINE_ONLINE when box responds."""
        # Build a valid response from box 1
        uniid = [0x01, 0x00, 0x5C, 0x51, 0x30, 0x03, 0x14, 0x91, 0xB0, 0x15, 0x4C, 0x30]
        resp_data = bytes([DEV_TYPE_MB, 0x00]) + bytes(uniid)
        resp_msg = build_message(0x01, STATUS_ADDRESSING, CMD_ONLINE_CHECK, resp_data)

        cfs_controller._serial.response_queue.append(resp_msg[:3])
        cfs_controller._serial.response_queue.append(resp_msg[3:])
        cfs_controller._box_table[0].mapped = True

        result = cfs_controller._online_check(0x01)

        assert result is True
        assert cfs_controller._box_table[0].online == BoxAddressEntry.ONLINE_ONLINE
        assert cfs_controller._box_table[0].lost_cnt == 0

    def test_cmd_online_check_increments_lost_cnt_on_timeout(self, cfs_controller):
        """_online_check() increments lost_cnt when no response is received."""
        cfs_controller._box_table[0].mapped = True
        cfs_controller._box_table[0].lost_cnt = 0
        # No response in queue

        result = cfs_controller._online_check(0x01)

        assert result is False
        assert cfs_controller._box_table[0].lost_cnt == 1

    def test_cmd_online_check_marks_offline_after_max_lost(self, cfs_controller):
        """_online_check() marks box OFFLINE when lost_cnt exceeds MAX_LOST_CNT."""
        from creality_cfs import MAX_LOST_CNT
        cfs_controller._box_table[0].mapped = True
        cfs_controller._box_table[0].lost_cnt = MAX_LOST_CNT  # at the threshold

        cfs_controller._online_check(0x01)  # one more failure

        assert cfs_controller._box_table[0].online == BoxAddressEntry.ONLINE_OFFLINE


# ===========================================================================
# CMD_GET_ADDR_TABLE (0xA3)
# ===========================================================================

class TestCmdGetAddrTable:
    """Tests for CMD_GET_ADDR_TABLE (0xA3) address table confirmation."""

    def test_cmd_get_addr_table_message_matches_capture(self):
        """CMD_GET_ADDR_TABLE slave 1 matches captured frame exactly."""
        msg = build_message(0x01, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE)
        assert msg == b'\xf7\x01\x03\x00\xa3\xdd'

    def test_cmd_get_addr_table_updates_box_entry_on_response(self, cfs_controller):
        """_get_addr_table() sets mapped=True, acked=True, online=ONLINE on response."""
        # Simulate response: b'\xf7\x01\x11\x00\xa3\x01\x00\x5c...\x48'
        uniid = [0x01, 0x00, 0x5C, 0x51, 0x30, 0x03, 0x14, 0x91, 0xB0, 0x15, 0x4C, 0x30]
        resp_data = bytes([DEV_TYPE_MB, 0x00, 0x01, 0x00]) + bytes(uniid)
        resp_msg = build_message(0x01, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE, resp_data)

        cfs_controller._serial.response_queue.append(resp_msg[:3])
        cfs_controller._serial.response_queue.append(resp_msg[3:])

        result = cfs_controller._get_addr_table(0x01)

        assert result is not None
        entry = cfs_controller._box_table[0]
        assert entry.mapped is True
        assert entry.acked is True
        assert entry.online == BoxAddressEntry.ONLINE_ONLINE

    def test_cmd_get_addr_table_returns_none_on_no_response(self, cfs_controller):
        """_get_addr_table() returns None when no response arrives."""
        result = cfs_controller._get_addr_table(0x01)
        assert result is None


# ===========================================================================
# CMD_SET_BOX_MODE (0x04)
# ===========================================================================

class TestCmdSetBoxMode:
    """Tests for CMD_SET_BOX_MODE (0x04)."""

    def test_cmd_set_box_mode_message_format_matches_capture(self):
        """SET_BOX_MODE slave 1, mode=0 param=1 matches captured frame."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE, b'\x00\x01')
        assert msg == b'\xf7\x01\x05\xff\x04\x00\x01\x90'

    def test_cmd_set_box_mode_uses_status_operational(self):
        """SET_BOX_MODE uses STATUS=0xFF (operational phase)."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE, b'\x00\x01')
        assert msg[3] == STATUS_OPERATIONAL == 0xFF

    def test_cmd_set_box_mode_returns_true_on_ack(self, cfs_controller):
        """set_box_mode() returns True when response status=0x00 (addressing ACK)."""
        # Exact captured ACK: b'\xf7\x01\x03\x00\x04\xa1'
        ack = b'\xf7\x01\x03\x00\x04\xa1'
        cfs_controller._serial.response_queue.append(ack[:3])
        cfs_controller._serial.response_queue.append(ack[3:])

        result = cfs_controller.set_box_mode(0x01, 0x00, 0x01)
        assert result is True

    def test_cmd_set_box_mode_returns_false_on_no_response(self, cfs_controller):
        """set_box_mode() returns False when no response received."""
        result = cfs_controller.set_box_mode(0x01, 0x00, 0x01)
        assert result is False

    def test_cmd_set_box_mode_invalid_addr_below_min_raises(self, cfs_controller):
        """set_box_mode() raises ValueError for addr=0x00 (below ADDR_BOX_MIN=1)."""
        with pytest.raises(ValueError, match="addr"):
            cfs_controller.set_box_mode(0x00, 0x00, 0x01)

    def test_cmd_set_box_mode_invalid_addr_above_max_raises(self, cfs_controller):
        """set_box_mode() raises ValueError for addr=0x05 (above ADDR_BOX_MAX=4)."""
        with pytest.raises(ValueError, match="addr"):
            cfs_controller.set_box_mode(0x05, 0x00, 0x01)

    @pytest.mark.parametrize("addr", [1, 2, 3, 4])
    def test_cmd_set_box_mode_valid_addr_range_builds_message(self, addr):
        """set_box_mode() accepts addresses 1-4 without raising ValueError."""
        msg = build_message(addr, STATUS_OPERATIONAL, CMD_SET_BOX_MODE, b'\x00\x01')
        assert msg[1] == addr


# ===========================================================================
# CMD_GET_BOX_STATE (0x0A)
# ===========================================================================

class TestCmdGetBoxState:
    """Tests for CMD_GET_BOX_STATE (0x0A)."""

    def test_cmd_get_box_state_message_matches_capture(self):
        """GET_BOX_STATE slave 1 matches captured b'\xf7\x01\x03\xff\x0a\x5c'.

        Func 0x0A (WIRE-CONFIRMED 2026-06-09/06-19); the prior \x08\x52 frame was stale from
        the pre-v1.2.0 model. 0x08 is GET_HARDWARE_STATUS, a separate command.
        """
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE)
        assert msg == b'\xf7\x01\x03\xff\x0a\x5c'


    def test_cmd_get_box_state_no_data_payload(self):
        """GET_BOX_STATE has no data payload: LENGTH=3, total 6 bytes."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE)
        assert msg[2] == 3
        assert len(msg) == 6

    def test_cmd_get_box_state_parses_4_byte_response(self, cfs_controller):
        """get_box_state() decodes the 4-byte 0x0A word into the v1.4.0 keys.

        Captured response: b'\\xf7\\x01\\x07\\x00\\x0a\\x1c\\x14\\x00\\x00\\x48'
        data = b'\\x1c\\x14\\x00\\x00'.

        v1.4.0: the [class_byte][state] decode of b0/b1 is WIRE-DISPROVEN -- b0/b1 are an
        opaque drifting firmware base (exposed as fw_base, diagnostics only). The real load
        flag is data[3]: 0x02 = loaded/print-locked, 0x00 = feed/change mode. The frame
        STATUS byte is the async event channel (0x00 = idle).
        """
        resp = b'\xf7\x01\x07\x00\x0a\x1c\x14\x00\x00\x48'
        cfs_controller._serial.response_queue.append(resp[:3])
        cfs_controller._serial.response_queue.append(resp[3:])

        result = cfs_controller.get_box_state(0x01)
        assert result["fw_base"] == 0x1C14     # opaque b0/b1 base, diagnostics only
        assert result["substatus"] == 0x00
        assert result["feeding"] is True       # data[3] == 0x00 = feed/change mode
        assert result["loaded"] is False
        assert result["event"] == BOX_EVENT_IDLE
        assert result["raw"] == b'\x1c\x14\x00\x00'
        assert result["addr"] == 0x01

    def test_cmd_get_box_state_loaded_flag_from_data3(self, cfs_controller):
        """get_box_state() reads loaded/print-locked from data[3] == 0x02.

        v1.4.0: loaded is keyed 1:1 to the SET_BOX_MODE print-mode latch (data[3]),
        NOT to the old wire-disproven [hi][lo 0x20/0x1f] word.
        """
        resp = build_message(0x01, STATUS_ADDRESSING, CMD_GET_BOX_STATE,
                             bytes([0x1C, 0x24, 0x00, BOX_STATE_LOADED_B3]))
        cfs_controller._serial.response_queue.append(resp[:3])
        cfs_controller._serial.response_queue.append(resp[3:])

        result = cfs_controller.get_box_state(0x01)
        assert result["loaded"] is True
        assert result["feeding"] is False
        assert BOX_STATE_LOADED_B3 == 0x02 and BOX_STATE_FEEDING_B3 == 0x00

    def test_cmd_get_box_state_returns_none_on_no_response(self, cfs_controller):
        """get_box_state() returns None when no response received.

        v1.4.0: silent-CFS tolerant -- no longer raises RuntimeError, so a missing box
        can never abort a caller mid-choreography.
        """
        assert cfs_controller.get_box_state(0x01) is None

    def test_cmd_get_box_state_returns_none_on_short_payload(self, cfs_controller):
        """get_box_state() returns None for a <4-byte data payload (v1.4.0)."""
        resp = build_message(0x01, STATUS_ADDRESSING, CMD_GET_BOX_STATE, b'\x1c\x24')
        cfs_controller._serial.response_queue.append(resp[:3])
        cfs_controller._serial.response_queue.append(resp[3:])

        assert cfs_controller.get_box_state(0x01) is None

    @pytest.mark.parametrize("addr", [1, 2, 3, 4])
    def test_cmd_get_box_state_message_addr_byte(self, addr):
        """GET_BOX_STATE message addr byte matches the requested box address."""
        msg = build_message(addr, STATUS_OPERATIONAL, CMD_GET_BOX_STATE)
        assert msg[1] == addr


# ===========================================================================
# CMD_SET_PRE_LOADING (0x0D)
# ===========================================================================

class TestCmdSetPreLoading:
    """Tests for CMD_SET_PRE_LOADING (0x0D)."""

    def test_cmd_set_pre_loading_message_matches_capture(self):
        """SET_PRE_LOADING slave 1 mask=0x0F phase=0x01 matches captured frame.

        v1.4.0: payload byte order is [mask][phase]; phase 0x01 is the wire DISARM
        (the old ENABLE=1 -> 0x01 mapping was inverted vs the wire).
        """
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_PRE_LOADING,
                            bytes([0x0F, PRELOAD_PHASE_DISARM]))
        assert msg == b'\xf7\x01\x05\xff\x0d\x0f\x01\x69'
        assert msg[5] == 0x0F                    # data[0] = slot mask
        assert msg[6] == PRELOAD_PHASE_DISARM    # data[1] = phase

    def test_cmd_set_pre_loading_returns_true_on_ack(self, cfs_controller):
        """set_pre_loading() returns True on a STATUS-0x00 ACK reply.

        Captured ACK: b'\\xf7\\x01\\x03\\x00\\x0d\\x9e'
        v1.4.0: the reply STATUS byte is now checked -- True ONLY on 0x00.
        """
        ack = b'\xf7\x01\x03\x00\x0d\x9e'
        cfs_controller._serial.response_queue.append(ack[:3])
        cfs_controller._serial.response_queue.append(ack[3:])

        result = cfs_controller.set_pre_loading(0x01, 0x0F, PRELOAD_PHASE_DISARM)
        assert result is True

    def test_cmd_set_pre_loading_returns_false_on_nak_status(self, cfs_controller):
        """set_pre_loading() returns False on a non-0x00 reply STATUS (0x16 NAK).

        v1.4.0: a 0x16 STATUS means the controller did not finish the pre-load;
        the pre-v1.4.0 code treated any reply as success.
        """
        nak = build_message(0x01, 0x16, CMD_SET_PRE_LOADING)
        cfs_controller._serial.response_queue.append(nak[:3])
        cfs_controller._serial.response_queue.append(nak[3:])

        result = cfs_controller.set_pre_loading(0x01, 0x0F, PRELOAD_PHASE_DISARM)
        assert result is False

    def test_cmd_set_pre_loading_returns_false_on_no_response(self, cfs_controller):
        """set_pre_loading() returns False when no response received."""
        result = cfs_controller.set_pre_loading(0x01, 0x0F, PRELOAD_PHASE_DISARM)
        assert result is False

    def test_cmd_set_pre_loading_invalid_addr_raises(self, cfs_controller):
        """set_pre_loading() raises ValueError for addr=0 (below minimum)."""
        with pytest.raises(ValueError):
            cfs_controller.set_pre_loading(0x00, 0x0F, PRELOAD_PHASE_DISARM)

    def test_cmd_set_pre_loading_invalid_addr_above_max_raises(self, cfs_controller):
        """set_pre_loading() raises ValueError for addr=5 (above maximum)."""
        with pytest.raises(ValueError):
            cfs_controller.set_pre_loading(0x05, 0x0F, PRELOAD_PHASE_DISARM)

    def test_cmd_set_pre_loading_phase_slot_rearm_is_valid(self, cfs_controller):
        """set_pre_loading() accepts phase=0x02 (per-slot re-arm).

        v1.4.0: phase 0x02 is a REAL wire phase (blocks ~38 s on hardware); the old
        'enable must be 0 or 1' ValueError encoded the wire-disproven enable model.
        """
        ack = build_message(0x01, STATUS_ADDRESSING, CMD_SET_PRE_LOADING)
        cfs_controller._serial.response_queue.append(ack[:3])
        cfs_controller._serial.response_queue.append(ack[3:])

        result = cfs_controller.set_pre_loading(0x01, 0x01, PRELOAD_PHASE_SLOT_REARM)
        assert result is True

    def test_cmd_set_pre_loading_invalid_phase_raises(self, cfs_controller):
        """set_pre_loading() raises ValueError for phase > 0xFF (v1.4.0: out-of-byte-range,
        replacing the wire-disproven 'enable not 0/1' check)."""
        with pytest.raises(ValueError, match="phase"):
            cfs_controller.set_pre_loading(0x01, 0x0F, 0x100)

    def test_cmd_set_pre_loading_invalid_mask_raises(self, cfs_controller):
        """set_pre_loading() raises ValueError for mask > 0xFF (out-of-byte-range)."""
        with pytest.raises(ValueError, match="mask"):
            cfs_controller.set_pre_loading(0x01, 0x100, PRELOAD_PHASE_DISARM)

    @pytest.mark.parametrize("slot_mask", [0x00, 0x0F, 0xFF])
    def test_cmd_set_pre_loading_slot_mask_boundary_values(self, slot_mask):
        """SET_PRE_LOADING accepts all valid slot_mask values (0x00-0xFF)."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_PRE_LOADING,
                            bytes([slot_mask, 0x01]))
        assert msg[5] == slot_mask


# ===========================================================================
# CMD_GET_VERSION_SN (0x14)
# ===========================================================================

class TestCmdGetVersionSN:
    """Tests for CMD_GET_VERSION_SN (0x14)."""

    def test_cmd_get_version_sn_message_matches_capture(self):
        """GET_VERSION_SN slave 1 matches captured b'\\xf7\\x01\\x03\\xff\\x14\\x06'."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_VERSION_SN)
        assert msg == b'\xf7\x01\x03\xff\x14\x06'

    def test_cmd_get_version_sn_parses_22_byte_ascii_response(self, cfs_controller):
        """get_version_sn() returns '11010000843215B625AHSC' from captured response.

        Captured: b'\\xf7\\x01\\x19\\x00\\x14\\x31\\x31\\x30...'
        """
        resp = (b'\xf7\x01\x19\x00\x14'
                b'\x31\x31\x30\x31\x30\x30\x30\x30'
                b'\x38\x34\x33\x32\x31\x35\x42\x36'
                b'\x32\x35\x41\x48\x53\x43'
                b'\x84')
        cfs_controller._serial.response_queue.append(resp[:3])
        cfs_controller._serial.response_queue.append(resp[3:])

        version_str = cfs_controller.get_version_sn(0x01)
        assert version_str == "11010000843215B625AHSC"

    def test_cmd_get_version_sn_raises_on_no_response(self, cfs_controller):
        """get_version_sn() raises RuntimeError when no response received."""
        with pytest.raises(RuntimeError, match="0x01"):
            cfs_controller.get_version_sn(0x01)

    def test_cmd_get_version_sn_strips_null_bytes(self, cfs_controller):
        """get_version_sn() strips trailing null bytes from the version string."""
        # Build a padded version: 10 chars + 12 null bytes
        version_bytes = b"11010000843215B625AHSC"[:10].ljust(22, b"\x00")
        resp = build_message(0x01, STATUS_ADDRESSING, CMD_GET_VERSION_SN, version_bytes)
        cfs_controller._serial.response_queue.append(resp[:3])
        cfs_controller._serial.response_queue.append(resp[3:])

        result = cfs_controller.get_version_sn(0x01)
        assert "\x00" not in result

    def test_cmd_get_version_sn_returns_string_type(self, cfs_controller):
        """get_version_sn() always returns a str, not bytes."""
        resp = (b'\xf7\x01\x19\x00\x14'
                b'\x31\x31\x30\x31\x30\x30\x30\x30'
                b'\x38\x34\x33\x32\x31\x35\x42\x36'
                b'\x32\x35\x41\x48\x53\x43'
                b'\x84')
        cfs_controller._serial.response_queue.append(resp[:3])
        cfs_controller._serial.response_queue.append(resp[3:])

        result = cfs_controller.get_version_sn(0x01)
        assert isinstance(result, str)


# ===========================================================================
# Auto-addressing sequence
# ===========================================================================

class TestAutoAddressingSequence:
    """Tests for the full 5-step _run_auto_addressing() sequence."""

    def test_auto_addressing_full_sequence_all_4_boxes_online(self):
        """_run_auto_addressing() returns 4 when all 4 boxes respond.

        Wires a MockCFSHardware with 4 boxes through the serial transport,
        runs _run_auto_addressing(), and verifies 4 boxes reach ONLINE_ONLINE.
        """
        hw = MockCFSHardware(box_count=4)
        cfs, _ = make_wired_controller(hw, box_count=4, retry_count=1)

        count = cfs._run_auto_addressing()

        assert count == 4

    def test_auto_addressing_cmd_loader_to_app_is_sent_first(self):
        """Auto-addressing step 1 sends CMD_LOADER_TO_APP before any other command."""
        hw = MockCFSHardware(box_count=1)
        cfs, ser = make_wired_controller(hw, box_count=1, retry_count=1)

        written_frames = []
        orig_write = ser.write.side_effect

        def _spy_write(data):
            written_frames.append(data)
            if orig_write:
                orig_write(data)

        ser.write.side_effect = _spy_write

        cfs._run_auto_addressing()

        first_func = parse_message(written_frames[0])["func"]
        assert first_func == CMD_LOADER_TO_APP

    def test_auto_addressing_single_box_is_assigned_addr_1(self):
        """Single-box auto-addressing assigns address 0x01."""
        hw = MockCFSHardware(box_count=1)
        cfs, _ = make_wired_controller(hw, box_count=1, retry_count=1)

        cfs._run_auto_addressing()

        entry = cfs._box_table[0]
        assert entry.addr == 0x01
        assert entry.online == BoxAddressEntry.ONLINE_ONLINE
