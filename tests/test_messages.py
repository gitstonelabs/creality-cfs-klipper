"""
test_messages.py — Unit tests for build_message() and parse_message() in creality_cfs.py.

Tests cover:
  - Message frame construction: header, address, length, status, func, data, CRC
  - All 9 confirmed commands produce exact bytes matching interceptty captures
  - parse_message() field extraction: addr, status, func, data, crc_valid
  - Rejection of bad header, bad CRC, truncated frames
  - Round-trip: build then parse must recover all fields exactly
  - Length field formula: LENGTH = len(data) + 3

Message format:
  [0xF7][ADDR][LENGTH][STATUS][FUNC][DATA 0-N][CRC8]
  CRC scope = msg[2:-1] = [LENGTH][STATUS][FUNC][DATA...]

No hardware required.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from creality_cfs import (
    crc8_cfs,
    build_message,
    parse_message,
    BoxAddressEntry,
    PACK_HEAD,
    BROADCAST_ADDR_MB,
    BROADCAST_ADDR_ALL,
    STATUS_ADDRESSING,
    STATUS_OPERATIONAL,
    CMD_GET_ADDR_TABLE,
    CMD_SET_BOX_MODE,
    CMD_GET_VERSION_SN,
    CMD_GET_BOX_STATE,
    CMD_SET_PRE_LOADING,
    CMD_GET_SLAVE_INFO,
    CMD_ONLINE_CHECK,
    CMD_LOADER_TO_APP,
    CMD_SET_SLAVE_ADDR,
    MAX_DATA_LEN,
    MIN_MSG_LEN,
)


# ===========================================================================
# build_message() tests
# ===========================================================================

class TestBuildMessageBasic:
    """Core structure tests for build_message()."""

    def test_build_message_basic_header_is_0xF7(self):
        """Every message starts with PACK_HEAD (0xF7) regardless of other parameters.

        0xF7 is the fixed framing byte.
        """
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE)
        assert msg[0] == PACK_HEAD == 0xF7

    def test_build_message_basic_addr_byte_position(self):
        """ADDR byte is at index 1 and equals the addr argument."""
        for addr in [0x01, 0x02, 0x03, 0x04, 0xFE, 0xFF]:
            msg = build_message(addr, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE)
            assert msg[1] == addr, f"addr=0x{addr:02X}: byte[1] is 0x{msg[1]:02X}"

    def test_build_message_basic_length_field_position(self):
        """LENGTH byte is at index 2 and equals len(data) + 3."""
        # No data: LENGTH = 0 + 3 = 3
        msg_no_data = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE)
        assert msg_no_data[2] == 3

        # 2 data bytes: LENGTH = 2 + 3 = 5
        msg_two_bytes = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE, b'\x00\x01')
        assert msg_two_bytes[2] == 5

    def test_build_message_basic_status_field_position(self):
        """STATUS byte is at index 3."""
        msg_op = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE)
        assert msg_op[3] == STATUS_OPERATIONAL == 0xFF

        msg_addr = build_message(0x01, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE)
        assert msg_addr[3] == STATUS_ADDRESSING == 0x00

    def test_build_message_basic_func_field_position(self):
        """FUNC byte is at index 4."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE)
        assert msg[4] == CMD_GET_BOX_STATE == 0x0A

    def test_build_message_basic_crc_is_last_byte(self):
        """CRC is always the final byte, matching manual calculation of msg[2:-1]."""
        msg = build_message(0x01, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE)
        scope = msg[2:-1]
        assert msg[-1] == crc8_cfs(scope)

    def test_build_message_no_data_total_length_is_6(self):
        """A message with no data payload is exactly 6 bytes.

        6 = HEAD(1) + ADDR(1) + LENGTH(1) + STATUS(1) + FUNC(1) + CRC(1).
        """
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE)
        assert len(msg) == 6

    def test_build_message_two_data_bytes_total_length_is_8(self):
        """A message with 2 data bytes is exactly 8 bytes total."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE, b'\x00\x01')
        assert len(msg) == 8

    def test_build_message_length_formula_various_sizes(self):
        """Total message length = 5 + len(data) for any data length."""
        for data_len in [0, 1, 2, 5, 10, 50, 100]:
            data = bytes(range(data_len % 256)) if data_len <= 100 else bytes(data_len)
            msg = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE, data[:data_len])
            assert len(msg) == 5 + data_len + 1  # HEAD+ADDR+LEN+STATUS+FUNC + data + CRC

    def test_build_message_empty_data_default_matches_explicit_empty(self):
        """build_message() with default data arg produces identical output to b''."""
        implicit = build_message(0x01, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE)
        explicit = build_message(0x01, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE, b"")
        assert implicit == explicit

    def test_build_message_data_too_long_raises_value_error(self):
        """build_message() raises ValueError when data exceeds MAX_DATA_LEN."""
        with pytest.raises(ValueError, match=str(MAX_DATA_LEN)):
            build_message(0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE,
                          bytes(MAX_DATA_LEN + 1))

    def test_build_message_data_exactly_max_len_succeeds(self):
        """build_message() accepts exactly MAX_DATA_LEN bytes without error."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE,
                            bytes(MAX_DATA_LEN))
        assert len(msg) == 5 + MAX_DATA_LEN + 1
        # Verify CRC
        assert msg[-1] == crc8_cfs(msg[2:-1])


# ---------------------------------------------------------------------------
# Exact-byte tests against interceptty captures
# ---------------------------------------------------------------------------

class TestBuildMessageCapturedFrames:
    """Verify build_message() reproduces exact bytes from captured RS485 frames.

    Each test uses the specific frame from klipper-cfs/tests/test_structures.py.
    Failing any of these tests means the frame format has changed from the
    version(16/16 test vectors).
    """

    def test_build_cmd_get_addr_table_slave_1(self):
        """CMD_GET_ADDR_TABLE to slave 1 matches captured b'\\xf7\\x01\\x03\\x00\\xa3\\xdd'."""
        msg = build_message(0x01, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE)
        assert msg == b'\xf7\x01\x03\x00\xa3\xdd'

    def test_build_cmd_get_addr_table_slave_2(self):
        """CMD_GET_ADDR_TABLE to slave 2 matches captured b'\\xf7\\x02\\x03\\x00\\xa3\\xdd'."""
        msg = build_message(0x02, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE)
        assert msg == b'\xf7\x02\x03\x00\xa3\xdd'

    def test_build_cmd_get_addr_table_slave_3(self):
        """CMD_GET_ADDR_TABLE to slave 3 matches capture."""
        msg = build_message(0x03, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE)
        assert msg == b'\xf7\x03\x03\x00\xa3\xdd'

    def test_build_cmd_get_addr_table_slave_4(self):
        """CMD_GET_ADDR_TABLE to slave 4 matches capture."""
        msg = build_message(0x04, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE)
        assert msg == b'\xf7\x04\x03\x00\xa3\xdd'

    def test_build_cmd_set_box_mode_slave_1(self):
        """CMD_SET_BOX_MODE slave 1, mode=0x00 param=0x01 matches b'\\xf7\\x01\\x05\\xff\\x04\\x00\\x01\\x90'."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE, b'\x00\x01')
        assert msg == b'\xf7\x01\x05\xff\x04\x00\x01\x90'

    def test_build_cmd_get_version_sn_slave_1(self):
        """CMD_GET_VERSION_SN slave 1 matches b'\\xf7\\x01\\x03\\xff\\x14\\x06'."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_VERSION_SN)
        assert msg == b'\xf7\x01\x03\xff\x14\x06'

    def test_build_cmd_set_pre_loading_slave_1(self):
        """CMD_SET_PRE_LOADING slave 1 mask=0x0F enable=0x01 matches b'\\xf7\\x01\\x05\\xff\\x0d\\x0f\\x01\\x69'."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_PRE_LOADING, b'\x0f\x01')
        assert msg == b'\xf7\x01\x05\xff\x0d\x0f\x01\x69'

    def test_build_cmd_get_box_state_slave_1(self):
        """CMD_GET_BOX_STATE slave 1 matches b'\\xf7\\x01\\x03\\xff\\x0a\\x5c'."""
        msg = build_message(0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE)
        assert msg == b'\xf7\x01\x03\xff\x0a\x5c'

    def test_build_cmd_get_slave_info_broadcast(self):
        """CMD_GET_SLAVE_INFO broadcast matches b'\\xf7\\xfe\\x05\\x00\\xa1\\xfe\\xfe\\xf8'."""
        msg = build_message(
            BROADCAST_ADDR_MB,
            STATUS_ADDRESSING,
            CMD_GET_SLAVE_INFO,
            bytes([BROADCAST_ADDR_MB, BROADCAST_ADDR_MB]),
        )
        assert msg == b'\xf7\xfe\x05\x00\xa1\xfe\xfe\xf8'

    def test_build_cmd_online_check_slave_1(self):
        """CMD_ONLINE_CHECK slave 1 matches b'\\xf7\\x01\\x03\\x00\\xa2\\xda'."""
        msg = build_message(0x01, STATUS_ADDRESSING, CMD_ONLINE_CHECK)
        assert msg == b'\xf7\x01\x03\x00\xa2\xda'


# ===========================================================================
# parse_message() tests
# ===========================================================================

class TestParseMessageValid:
    """Tests for parse_message() with well-formed input."""

    def test_parse_message_valid_returns_dict_not_none(self):
        """parse_message() on a valid frame returns a dict (not None)."""
        raw = b'\xf7\x01\x03\x00\xa3\xdd'
        result = parse_message(raw)
        assert result is not None
        assert isinstance(result, dict)

    def test_parse_message_returns_all_expected_keys(self):
        """parse_message() result contains addr, length, status, func, data, crc, crc_valid."""
        raw = b'\xf7\x01\x03\x00\xa3\xdd'
        result = parse_message(raw)
        for key in ("addr", "length", "status", "func", "data", "crc", "crc_valid"):
            assert key in result, f"Missing key: {key}"

    def test_parse_message_addr_field_correct(self):
        """parse_message() extracts addr from byte[1] correctly."""
        raw = b'\xf7\x02\x03\x00\xa3\xdd'
        result = parse_message(raw)
        assert result["addr"] == 0x02

    def test_parse_message_status_field_correct(self):
        """parse_message() extracts STATUS from byte[3]."""
        raw = b'\xf7\x01\x03\xff\x0a\x5c'   # GET_BOX_STATE, STATUS=0xFF
        result = parse_message(raw)
        assert result["status"] == 0xFF

    def test_parse_message_func_field_correct(self):
        """parse_message() extracts func from byte[4]."""
        raw = b'\xf7\x01\x03\xff\x0a\x5c'
        result = parse_message(raw)
        assert result["func"] == 0x0A  # CMD_GET_BOX_STATE

    def test_parse_message_data_is_bytes_type(self):
        """parse_message() 'data' field is always bytes."""
        raw = b'\xf7\x01\x05\xff\x04\x00\x01\x90'
        result = parse_message(raw)
        assert isinstance(result["data"], bytes)

    def test_parse_message_data_extracted_correctly(self):
        """parse_message() extracts data bytes between FUNC and CRC correctly."""
        # Frame: b'\xf7\x01\x05\xff\x04\x00\x01\x90'
        # data bytes = b'\x00\x01'
        raw = b'\xf7\x01\x05\xff\x04\x00\x01\x90'
        result = parse_message(raw)
        assert result["data"] == b'\x00\x01'

    def test_parse_message_empty_data_for_no_payload_command(self):
        """parse_message() returns empty bytes for data on commands with no payload."""
        raw = b'\xf7\x01\x03\xff\x0a\x5c'  # GET_BOX_STATE, no data
        result = parse_message(raw)
        assert result["data"] == b""

    def test_parse_message_crc_valid_true_for_good_frame(self):
        """parse_message() sets crc_valid=True for a frame with correct CRC."""
        raw = b'\xf7\x01\x03\x00\xa3\xdd'
        result = parse_message(raw)
        assert result["crc_valid"] is True

    def test_parse_message_length_field_value(self):
        """parse_message() stores the LENGTH field value from the frame."""
        raw = b'\xf7\x01\x03\x00\xa3\xdd'  # LENGTH=3
        result = parse_message(raw)
        assert result["length"] == 3

    def test_parse_message_crc_byte_stored(self):
        """parse_message() stores the received CRC byte in the 'crc' key."""
        raw = b'\xf7\x01\x03\x00\xa3\xdd'  # CRC=0xDD
        result = parse_message(raw)
        assert result["crc"] == 0xDD


class TestParseMessageAllCapturedFrames:
    """Verify parse_message() parses all 16 captured frames correctly."""

    @pytest.mark.parametrize("raw,exp_addr,exp_status,exp_func,exp_data", [
        (b'\xf7\x01\x03\x00\xa3\xdd', 0x01, 0x00, 0xA3, b""),
        (b'\xf7\x02\x03\x00\xa3\xdd', 0x02, 0x00, 0xA3, b""),
        (b'\xf7\x01\x05\xff\x04\x00\x01\x90', 0x01, 0xFF, 0x04, b'\x00\x01'),
        (b'\xf7\x01\x03\xff\x14\x06', 0x01, 0xFF, 0x14, b""),
        (b'\xf7\x01\x05\xff\x0d\x0f\x01\x69', 0x01, 0xFF, 0x0D, b'\x0f\x01'),
        (b'\xf7\x01\x03\xff\x0a\x5c', 0x01, 0xFF, 0x0A, b""),
        (b'\xf7\x01\x03\x00\xa2\xda', 0x01, 0x00, 0xA2, b""),
        (b'\xf7\xfe\x05\x00\xa1\xfe\xfe\xf8', 0xFE, 0x00, 0xA1, b'\xfe\xfe'),
    ])
    def test_parse_captured_tx_frame_all_fields(self, raw, exp_addr, exp_status, exp_func, exp_data):
        """All 8 captured TX frames parse correctly with exact field values."""
        result = parse_message(raw)
        assert result is not None
        assert result["crc_valid"] is True
        assert result["addr"] == exp_addr
        assert result["status"] == exp_status
        assert result["func"] == exp_func
        assert result["data"] == exp_data

    @pytest.mark.parametrize("raw", [
        b'\xf7\x01\x11\x00\xa3\x01\x00\x5c\x51\x30\x03\x14\x91\xb0\x15\x4c\x30\x39\x33\x48',
        b'\xf7\x01\x07\x00\x0a\x1c\x14\x00\x00\x48',
        b'\xf7\x01\x03\x00\x04\xa1',
        b'\xf7\x01\x19\x00\x14\x31\x31\x30\x31\x30\x30\x30\x30\x38\x34\x33\x32\x31\x35'
        b'\x42\x36\x32\x35\x41\x48\x53\x43\x84',
        b'\xf7\x01\x03\x00\x0d\x9e',
        b'\xf7\x01\x11\x00\xa2\x01\x00\x5c\x51\x30\x03\x14\x91\xb0\x15\x4c\x30\x39\x33\xfd',
    ])
    def test_parse_captured_rx_frame_crc_valid(self, raw):
        """All 6 captured RX frames parse with crc_valid=True."""
        result = parse_message(raw)
        assert result is not None, f"Frame {raw.hex()} returned None"
        assert result["crc_valid"] is True, f"CRC failed for frame {raw.hex()}"


class TestParseMessageRejection:
    """Tests for parse_message() rejection of malformed frames."""

    def test_parse_message_bad_header_returns_none(self):
        """parse_message() returns None when the first byte is not 0xF7.

        The header byte 0xF7 is the fixed sync word.  An incorrect header
        means the frame is either garbage or from a different protocol.
        """
        bad = b'\xAA\x01\x03\x00\xa3\xdd'
        assert parse_message(bad) is None

    def test_parse_message_zero_header_returns_none(self):
        """parse_message() returns None for 0x00 header."""
        bad = b'\x00\x01\x03\x00\xa3\xdd'
        assert parse_message(bad) is None

    def test_parse_message_bad_crc_sets_crc_valid_false(self):
        """parse_message() returns a dict with crc_valid=False for wrong CRC byte.

        A bad CRC should not return None — the caller needs to know that a
        frame arrived but was corrupted, so it can log the error and retry.
        """
        valid = b'\xf7\x01\x03\x00\xa3\xdd'
        corrupted = valid[:-1] + bytes([valid[-1] ^ 0xFF])
        result = parse_message(corrupted)
        assert result is not None
        assert result["crc_valid"] is False

    def test_parse_message_truncated_below_min_len_returns_none(self):
        """parse_message() returns None for frames shorter than MIN_MSG_LEN (6)."""
        for length in range(MIN_MSG_LEN):
            data = bytes([0xF7, 0x01, 0x03, 0x00, 0xA3])[:length]
            assert parse_message(data) is None, f"Expected None for {length}-byte frame"

    def test_parse_message_truncated_mid_payload_returns_none(self):
        """parse_message() returns None when LENGTH field claims more bytes than present.

        Simulates a truncated RS485 transmission where the last bytes were
        cut off (e.g., collision or timeout mid-frame).
        """
        valid = build_message(0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE, b'\x00\x01')
        truncated = valid[:-2]  # remove last 2 bytes, frame claims LENGTH=5
        assert parse_message(truncated) is None

    def test_parse_message_empty_input_returns_none(self):
        """parse_message() returns None for completely empty input."""
        assert parse_message(b"") is None

    def test_parse_message_single_byte_returns_none(self):
        """parse_message() returns None for a single-byte input."""
        assert parse_message(b"\xF7") is None


# ===========================================================================
# Round-trip tests
# ===========================================================================

class TestRoundTrip:
    """Verify build_message + parse_message identity for all commands."""

    @pytest.mark.parametrize("addr,status,func,data", [
        (0x01, STATUS_OPERATIONAL, CMD_GET_BOX_STATE, b""),
        (0x01, STATUS_OPERATIONAL, CMD_GET_VERSION_SN, b""),
        (0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE, b'\x00\x01'),
        (0x01, STATUS_OPERATIONAL, CMD_SET_BOX_MODE, b'\x01\x01'),
        (0x01, STATUS_OPERATIONAL, CMD_SET_PRE_LOADING, b'\x0f\x01'),
        (0x01, STATUS_ADDRESSING, CMD_GET_ADDR_TABLE, b""),
        (0x01, STATUS_ADDRESSING, CMD_ONLINE_CHECK, b""),
        (0xFE, STATUS_ADDRESSING, CMD_GET_SLAVE_INFO, b'\xfe\xfe'),
        (0x04, STATUS_OPERATIONAL, CMD_GET_BOX_STATE, b""),
    ])
    def test_roundtrip_build_parse_recovers_all_fields(self, addr, status, func, data):
        """build_message() output parsed by parse_message() recovers exact field values.

        Ensures the two functions are inverses of each other for all valid
        command/payload combinations.  CRC must be valid after the round-trip.
        """
        msg = build_message(addr, status, func, data)
        parsed = parse_message(msg)
        assert parsed is not None
        assert parsed["crc_valid"] is True
        assert parsed["addr"] == addr
        assert parsed["status"] == status
        assert parsed["func"] == func
        assert parsed["data"] == data
