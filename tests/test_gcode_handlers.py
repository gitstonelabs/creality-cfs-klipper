"""
test_gcode_handlers.py — Tests for Klipper G-code command handlers in CrealityCFS.

Tests cover:
  - cmd_CFS_INIT: not connected error, success path, exception path
  - cmd_CFS_STATUS: not connected, single box, all boxes, unmapped box, exception
  - cmd_CFS_VERSION: not connected, single box, all boxes, unmapped box, exception
  - cmd_CFS_SET_MODE: not connected, success, failure, exception
  - cmd_CFS_SET_PRELOAD: not connected, success, failure, exception
  - cmd_CFS_ADDR_TABLE: all online states and modes printed
  - _connect_serial / _disconnect_serial lifecycle
  - _handle_ready / _handle_shutdown lifecycle handlers

The Klipper GCodeCommand interface is mocked so no Klipper environment is needed.
"""

import sys
import os
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from creality_cfs import (
    CrealityCFS,
    BoxAddressEntry,
    STATUS_ADDRESSING,
    STATUS_OPERATIONAL,
    CMD_GET_BOX_STATE,
    CMD_GET_VERSION_SN,
)

from tests.conftest import _make_fake_config
from tests.mock_cfs import MockCFSHardware
from tests.conftest import make_wired_controller


# ---------------------------------------------------------------------------
# Helper: build a mock gcmd that records calls
# ---------------------------------------------------------------------------

def _make_gcmd(box=None, mode=None, param=None, mask=None, enable=None):
    """Return a MagicMock mimicking Klipper's GCodeCommand."""
    gcmd = mock.MagicMock()
    gcmd.get_int.side_effect = lambda key, default=None, **kw: {
        "BOX": box,
        "MODE": mode,
        "PARAM": param if param is not None else 0x01,
        "MASK": mask,
        "ENABLE": enable,
    }.get(key, default)

    # error() should return an Exception that callers raise
    gcmd.error.side_effect = lambda msg: Exception(msg)
    return gcmd


# ===========================================================================
# _connect_serial / _disconnect_serial
# ===========================================================================

class TestSerialLifecycle:
    """Tests for serial port open/close lifecycle."""

    def test_connect_serial_sets_is_connected_true(self):
        """_connect_serial() sets is_connected=True on success."""
        cfg = _make_fake_config(auto_init=False)
        mock_ser = mock.MagicMock()
        mock_ser.is_open = True

        with mock.patch("creality_cfs.serial.Serial", return_value=mock_ser):
            cfs = CrealityCFS(cfg)
            cfs._connect_serial()

        assert cfs.is_connected is True

    def test_connect_serial_raises_on_serial_exception(self):
        """_connect_serial() re-raises SerialException and sets is_connected=False."""
        import serial as serial_mod
        cfg = _make_fake_config(auto_init=False)

        with mock.patch("creality_cfs.serial.Serial",
                        side_effect=serial_mod.SerialException("port busy")):
            cfs = CrealityCFS(cfg)
            with pytest.raises(serial_mod.SerialException):
                cfs._connect_serial()

        assert cfs.is_connected is False

    def test_disconnect_serial_sets_is_connected_false(self, cfs_controller):
        """_disconnect_serial() sets is_connected=False."""
        cfs_controller._disconnect_serial()
        assert cfs_controller.is_connected is False

    def test_disconnect_serial_sets_serial_to_none(self, cfs_controller):
        """_disconnect_serial() sets _serial=None."""
        cfs_controller._disconnect_serial()
        assert cfs_controller._serial is None

    def test_disconnect_serial_is_safe_when_already_disconnected(self, cfs_controller):
        """_disconnect_serial() is idempotent — calling twice does not raise."""
        cfs_controller._disconnect_serial()
        cfs_controller._disconnect_serial()  # second call must not raise
        assert cfs_controller._serial is None


# ===========================================================================
# _handle_ready / _handle_shutdown
# ===========================================================================

class TestLifecycleHandlers:
    """Tests for Klipper lifecycle event handlers."""

    def test_handle_ready_calls_connect_serial(self):
        """_handle_ready() calls _connect_serial() once."""
        cfg = _make_fake_config(auto_init=False)
        mock_ser = mock.MagicMock()
        mock_ser.is_open = True

        with mock.patch("creality_cfs.serial.Serial", return_value=mock_ser):
            cfs = CrealityCFS(cfg)
            cfs._connect_serial = mock.MagicMock()
            cfs._handle_ready()

        cfs._connect_serial.assert_called_once()

    def test_handle_ready_logs_error_on_serial_exception(self):
        """_handle_ready() logs an error but does not raise when port fails."""
        import serial as serial_mod
        cfg = _make_fake_config(auto_init=False)

        with mock.patch("creality_cfs.serial.Serial",
                        side_effect=serial_mod.SerialException("no port")):
            cfs = CrealityCFS(cfg)
            # Should not raise
            cfs._handle_ready()

        assert cfs.is_connected is False

    def test_handle_ready_registers_auto_init_callback_when_enabled(self):
        """_handle_ready() registers reactor callback when auto_init=True."""
        cfg = _make_fake_config(auto_init=True)
        mock_ser = mock.MagicMock()
        mock_ser.is_open = True

        with mock.patch("creality_cfs.serial.Serial", return_value=mock_ser):
            cfs = CrealityCFS(cfg)
            cfs._connect_serial = mock.MagicMock()
            cfs._handle_ready()

        cfs.reactor.register_callback.assert_called_once()

    def test_handle_shutdown_calls_disconnect_serial(self, cfs_controller):
        """_handle_shutdown() calls _disconnect_serial()."""
        cfs_controller._disconnect_serial = mock.MagicMock()
        cfs_controller._handle_shutdown()
        cfs_controller._disconnect_serial.assert_called_once()

    def test_handle_shutdown_does_not_raise_on_error(self, cfs_controller):
        """_handle_shutdown() swallows exceptions from _disconnect_serial()."""
        cfs_controller._disconnect_serial = mock.MagicMock(side_effect=Exception("port error"))
        cfs_controller._handle_shutdown()  # must not raise


# ===========================================================================
# cmd_CFS_INIT
# ===========================================================================

class TestCmdCFSInit:
    """Tests for the CFS_INIT G-code handler."""

    def test_cmd_cfs_init_not_connected_raises_gcmd_error(self, cfs_controller):
        """CFS_INIT raises via gcmd.error() when serial is not connected."""
        cfs_controller.is_connected = False
        gcmd = _make_gcmd()

        with pytest.raises(Exception, match="not connected"):
            cfs_controller.cmd_CFS_INIT(gcmd)

    def test_cmd_cfs_init_success_responds_with_box_count(self, cfs_controller):
        """CFS_INIT calls gcmd.respond_info() with online count on success."""
        cfs_controller._run_auto_addressing = mock.MagicMock(return_value=4)
        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)

        cfs_controller.cmd_CFS_INIT(gcmd)

        gcmd.respond_info.assert_called_once()
        info_text = gcmd.respond_info.call_args[0][0]
        assert "4" in info_text

    def test_cmd_cfs_init_exception_raises_gcmd_error(self, cfs_controller):
        """CFS_INIT raises via gcmd.error() when auto-addressing throws."""
        cfs_controller._run_auto_addressing = mock.MagicMock(
            side_effect=RuntimeError("bus error")
        )
        gcmd = _make_gcmd()

        with pytest.raises(Exception, match="bus error"):
            cfs_controller.cmd_CFS_INIT(gcmd)


# ===========================================================================
# cmd_CFS_STATUS
# ===========================================================================

class TestCmdCFSStatus:
    """Tests for the CFS_STATUS G-code handler."""

    def test_cmd_cfs_status_not_connected_raises_gcmd_error(self, cfs_controller):
        """CFS_STATUS raises via gcmd.error() when not connected."""
        cfs_controller.is_connected = False
        gcmd = _make_gcmd(box=None)

        with pytest.raises(Exception, match="not connected"):
            cfs_controller.cmd_CFS_STATUS(gcmd)

    def test_cmd_cfs_status_unmapped_box_reports_not_assigned(self, cfs_controller):
        """CFS_STATUS for unmapped box includes 'not assigned' in respond_info."""
        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1
        }.get(key, default)

        # Box 1 is unmapped by default
        cfs_controller.cmd_CFS_STATUS(gcmd)

        gcmd.respond_info.assert_called_once()
        assert "not assigned" in gcmd.respond_info.call_args[0][0]

    def test_cmd_cfs_status_single_box_queries_only_that_box(self, cfs_controller):
        """CFS_STATUS BOX=2 only calls get_box_state(2), not other boxes."""
        # Mark box 2 as mapped
        cfs_controller._box_table[1].mapped = True
        cfs_controller.get_box_state = mock.MagicMock(
            return_value={"state": 0x1C, "raw": b'\x1c\x14\x00\x00', "addr": 2}
        )

        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 2
        }.get(key, default)

        cfs_controller.cmd_CFS_STATUS(gcmd)

        cfs_controller.get_box_state.assert_called_once_with(2)

    def test_cmd_cfs_status_all_boxes_queried_when_no_box_param(self, cfs_controller):
        """CFS_STATUS without BOX param queries all mapped boxes."""
        # Mark all 4 boxes as mapped
        for entry in cfs_controller._box_table:
            entry.mapped = True
        cfs_controller.get_box_state = mock.MagicMock(
            return_value={"state": 0x1C, "raw": b'\x1c\x14\x00\x00', "addr": 1}
        )

        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": None
        }.get(key, default)

        cfs_controller.cmd_CFS_STATUS(gcmd)

        assert cfs_controller.get_box_state.call_count == 4

    def test_cmd_cfs_status_handles_get_box_state_exception_gracefully(self, cfs_controller):
        """CFS_STATUS includes 'ERROR' in output when get_box_state raises."""
        cfs_controller._box_table[0].mapped = True
        cfs_controller.get_box_state = mock.MagicMock(
            side_effect=RuntimeError("timeout")
        )

        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1
        }.get(key, default)

        cfs_controller.cmd_CFS_STATUS(gcmd)

        text = gcmd.respond_info.call_args[0][0]
        assert "ERROR" in text


# ===========================================================================
# cmd_CFS_VERSION
# ===========================================================================

class TestCmdCFSVersion:
    """Tests for the CFS_VERSION G-code handler."""

    def test_cmd_cfs_version_not_connected_raises_gcmd_error(self, cfs_controller):
        """CFS_VERSION raises via gcmd.error() when not connected."""
        cfs_controller.is_connected = False
        gcmd = _make_gcmd()

        with pytest.raises(Exception, match="not connected"):
            cfs_controller.cmd_CFS_VERSION(gcmd)

    def test_cmd_cfs_version_unmapped_box_reports_not_assigned(self, cfs_controller):
        """CFS_VERSION for unmapped box reports 'not assigned'."""
        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1
        }.get(key, default)

        cfs_controller.cmd_CFS_VERSION(gcmd)

        assert "not assigned" in gcmd.respond_info.call_args[0][0]

    def test_cmd_cfs_version_single_box_queries_version(self, cfs_controller):
        """CFS_VERSION BOX=1 calls get_version_sn(1) once."""
        cfs_controller._box_table[0].mapped = True
        cfs_controller.get_version_sn = mock.MagicMock(return_value="11010000843215B625AHSC")

        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1
        }.get(key, default)

        cfs_controller.cmd_CFS_VERSION(gcmd)

        cfs_controller.get_version_sn.assert_called_once_with(1)
        assert "11010000843215B625AHSC" in gcmd.respond_info.call_args[0][0]

    def test_cmd_cfs_version_handles_exception_gracefully(self, cfs_controller):
        """CFS_VERSION includes 'ERROR' when get_version_sn raises."""
        cfs_controller._box_table[0].mapped = True
        cfs_controller.get_version_sn = mock.MagicMock(side_effect=RuntimeError("fail"))

        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {"BOX": 1}.get(key, default)

        cfs_controller.cmd_CFS_VERSION(gcmd)
        assert "ERROR" in gcmd.respond_info.call_args[0][0]


# ===========================================================================
# cmd_CFS_SET_MODE
# ===========================================================================

class TestCmdCFSSetMode:
    """Tests for the CFS_SET_MODE G-code handler."""

    def test_cmd_cfs_set_mode_not_connected_raises_gcmd_error(self, cfs_controller):
        """CFS_SET_MODE raises via gcmd.error() when not connected."""
        cfs_controller.is_connected = False
        gcmd = _make_gcmd(box=1, mode=0)

        with pytest.raises(Exception, match="not connected"):
            cfs_controller.cmd_CFS_SET_MODE(gcmd)

    def test_cmd_cfs_set_mode_success_responds_with_mode(self, cfs_controller):
        """CFS_SET_MODE responds with mode value on success."""
        cfs_controller.set_box_mode = mock.MagicMock(return_value=True)

        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1, "MODE": 1, "PARAM": 1
        }.get(key, default)

        cfs_controller.cmd_CFS_SET_MODE(gcmd)

        gcmd.respond_info.assert_called_once()
        assert "0x01" in gcmd.respond_info.call_args[0][0]

    def test_cmd_cfs_set_mode_no_ack_still_responds(self, cfs_controller):
        """CFS_SET_MODE responds even when set_box_mode returns False (no ACK)."""
        cfs_controller.set_box_mode = mock.MagicMock(return_value=False)

        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1, "MODE": 0, "PARAM": 1
        }.get(key, default)

        cfs_controller.cmd_CFS_SET_MODE(gcmd)

        gcmd.respond_info.assert_called_once()

    def test_cmd_cfs_set_mode_exception_raises_gcmd_error(self, cfs_controller):
        """CFS_SET_MODE raises via gcmd.error() when set_box_mode throws ValueError."""
        cfs_controller.set_box_mode = mock.MagicMock(side_effect=ValueError("bad addr"))

        gcmd = _make_gcmd(box=5, mode=0)

        with pytest.raises(Exception, match="bad addr"):
            cfs_controller.cmd_CFS_SET_MODE(gcmd)


# ===========================================================================
# cmd_CFS_SET_PRELOAD
# ===========================================================================

class TestCmdCFSSetPreload:
    """Tests for the CFS_SET_PRELOAD G-code handler."""

    def test_cmd_cfs_set_preload_not_connected_raises_gcmd_error(self, cfs_controller):
        """CFS_SET_PRELOAD raises via gcmd.error() when not connected."""
        cfs_controller.is_connected = False
        gcmd = _make_gcmd(box=1, mask=15, enable=1)

        with pytest.raises(Exception, match="not connected"):
            cfs_controller.cmd_CFS_SET_PRELOAD(gcmd)

    def test_cmd_cfs_set_preload_success_enable_responds(self, cfs_controller):
        """CFS_SET_PRELOAD responds with 'enabled' text on success with enable=1."""
        cfs_controller.set_pre_loading = mock.MagicMock(return_value=True)

        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1, "MASK": 15, "ENABLE": 1
        }.get(key, default)

        cfs_controller.cmd_CFS_SET_PRELOAD(gcmd)

        text = gcmd.respond_info.call_args[0][0]
        assert "enabled" in text

    def test_cmd_cfs_set_preload_success_disable_responds(self, cfs_controller):
        """CFS_SET_PRELOAD responds with 'disabled' text when enable=0."""
        cfs_controller.set_pre_loading = mock.MagicMock(return_value=True)

        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1, "MASK": 1, "ENABLE": 0
        }.get(key, default)

        cfs_controller.cmd_CFS_SET_PRELOAD(gcmd)

        text = gcmd.respond_info.call_args[0][0]
        assert "disabled" in text

    def test_cmd_cfs_set_preload_no_ack_still_responds(self, cfs_controller):
        """CFS_SET_PRELOAD responds even when set_pre_loading returns False."""
        cfs_controller.set_pre_loading = mock.MagicMock(return_value=False)

        gcmd = mock.MagicMock()
        gcmd.error.side_effect = lambda msg: Exception(msg)
        gcmd.get_int.side_effect = lambda key, default=None, **kw: {
            "BOX": 1, "MASK": 15, "ENABLE": 1
        }.get(key, default)

        cfs_controller.cmd_CFS_SET_PRELOAD(gcmd)
        gcmd.respond_info.assert_called_once()

    def test_cmd_cfs_set_preload_exception_raises_gcmd_error(self, cfs_controller):
        """CFS_SET_PRELOAD raises via gcmd.error() when set_pre_loading throws."""
        cfs_controller.set_pre_loading = mock.MagicMock(
            side_effect=ValueError("bad mask")
        )
        gcmd = _make_gcmd(box=1, mask=999, enable=1)

        with pytest.raises(Exception, match="bad mask"):
            cfs_controller.cmd_CFS_SET_PRELOAD(gcmd)


# ===========================================================================
# cmd_CFS_ADDR_TABLE
# ===========================================================================

class TestCmdCFSAddrTable:
    """Tests for the CFS_ADDR_TABLE G-code handler."""

    def test_cmd_cfs_addr_table_includes_header_line(self, cfs_controller):
        """CFS_ADDR_TABLE response includes 'CFS Address Table:' header."""
        gcmd = mock.MagicMock()
        cfs_controller.cmd_CFS_ADDR_TABLE(gcmd)
        text = gcmd.respond_info.call_args[0][0]
        assert "CFS Address Table" in text

    def test_cmd_cfs_addr_table_shows_all_4_addresses(self, cfs_controller):
        """CFS_ADDR_TABLE response includes entries for all 4 box addresses."""
        gcmd = mock.MagicMock()
        cfs_controller.cmd_CFS_ADDR_TABLE(gcmd)
        text = gcmd.respond_info.call_args[0][0]
        for addr in ["0x01", "0x02", "0x03", "0x04"]:
            assert addr in text, f"Address {addr} missing from addr table output"

    def test_cmd_cfs_addr_table_online_state_strings(self, cfs_controller):
        """CFS_ADDR_TABLE shows correct online state strings for each state value."""
        cfs_controller._box_table[0].online = BoxAddressEntry.ONLINE_ONLINE
        cfs_controller._box_table[1].online = BoxAddressEntry.ONLINE_OFFLINE
        cfs_controller._box_table[2].online = BoxAddressEntry.ONLINE_INIT
        cfs_controller._box_table[3].online = BoxAddressEntry.ONLINE_WAIT_ACK

        gcmd = mock.MagicMock()
        cfs_controller.cmd_CFS_ADDR_TABLE(gcmd)
        text = gcmd.respond_info.call_args[0][0]

        assert "ONLINE" in text
        assert "OFFLINE" in text
        assert "INIT" in text
        assert "WAIT_ACK" in text

    def test_cmd_cfs_addr_table_mode_strings(self, cfs_controller):
        """CFS_ADDR_TABLE shows 'APP' and 'LOADER' mode strings."""
        cfs_controller._box_table[0].mode = BoxAddressEntry.MODE_APP
        cfs_controller._box_table[1].mode = BoxAddressEntry.MODE_LOADER
        cfs_controller._box_table[1].mapped = True

        gcmd = mock.MagicMock()
        cfs_controller.cmd_CFS_ADDR_TABLE(gcmd)
        text = gcmd.respond_info.call_args[0][0]

        assert "APP" in text
        assert "LOADER" in text

    def test_cmd_cfs_addr_table_shows_uniid_when_mapped(self, cfs_controller):
        """CFS_ADDR_TABLE shows hex UniID bytes when box is mapped."""
        cfs_controller._box_table[0].mapped = True
        cfs_controller._box_table[0].uniid = [0x01, 0x00, 0x5C]

        gcmd = mock.MagicMock()
        cfs_controller.cmd_CFS_ADDR_TABLE(gcmd)
        text = gcmd.respond_info.call_args[0][0]

        assert "01" in text
        assert "5C" in text
