#!/usr/bin/env python3
"""
CFS RS485 Traffic Capture Tool

Captures and logs RS485 traffic from the Creality Filament System (CFS).
By default logs ALL valid frames to JSONL for later analysis.
Use --filter-func to restrict to specific function codes (e.g. 0x10 0x11).

Usage:
    python3 capture_cfs_traffic.py --port /dev/ttyUSB0 --baud 230400
    python3 capture_cfs_traffic.py --port /dev/ttyUSB0 --filter-func 0x10 0x11

Author: gitstonelabs
License: GPL-3.0
"""

import argparse
import datetime
import json
import logging
import sys
import time

import serial

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol constants (mirrors creality_cfs.py)
# ---------------------------------------------------------------------------
PACK_HEAD: int = 0xF7
MIN_FRAME_LEN: int = 6  # HEAD + ADDR + LEN + STATUS + FUNC + CRC

FUNC_NAMES: dict = {
    0x0B: "CMD_LOADER_TO_APP",
    0xA1: "CMD_GET_SLAVE_INFO",
    0xA0: "CMD_SET_SLAVE_ADDR",
    0xA2: "CMD_ONLINE_CHECK",
    0xA3: "CMD_GET_ADDR_TABLE",
    0x04: "CMD_SET_BOX_MODE",
    0x0A: "CMD_GET_BOX_STATE",
    0x0D: "CMD_SET_PRE_LOADING",
    0x14: "CMD_GET_VERSION_SN",
    0x10: "CMD_EXTRUDE_PROCESS",  # TARGET
    0x11: "CMD_RETRUDE_PROCESS",  # TARGET
}

STATUS_NAMES: dict = {
    0x00: "ADDRESSING/RESPONSE",
    0xFF: "OPERATIONAL",
}

# ---------------------------------------------------------------------------
# CRC-8/SMBUS (identical to creality_cfs.py — must stay in sync)
# ---------------------------------------------------------------------------

def crc8_cfs(data: bytes) -> int:
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x07
            else:
                crc <<= 1
            crc &= 0xFF
    return crc


# ---------------------------------------------------------------------------
# Frame parser with CRC validation
# ---------------------------------------------------------------------------

def parse_frame(raw: bytes) -> dict | None:
    """Parse and validate a raw CFS frame.

    Returns a dict with all fields plus crc_valid flag, or None if
    the frame is malformed (wrong header, implausible length).
    A frame with a bad CRC is returned with crc_valid=False rather
    than discarded — so the caller can decide whether to log it.
    """
    if len(raw) < MIN_FRAME_LEN:
        return None
    if raw[0] != PACK_HEAD:
        return None

    addr = raw[1]
    length = raw[2]
    expected_total = 3 + length  # HEAD + ADDR + LEN + (STATUS+FUNC+DATA+CRC)

    if len(raw) < expected_total:
        return None

    status = raw[3]
    func = raw[4]
    data = raw[5:expected_total - 1]
    crc_received = raw[expected_total - 1]

    crc_scope = raw[2:expected_total - 1]  # LENGTH through last DATA byte
    crc_calculated = crc8_cfs(crc_scope)
    crc_valid = crc_received == crc_calculated

    return {
        "addr": addr,
        "length": length,
        "status": status,
        "status_name": STATUS_NAMES.get(status, f"UNKNOWN(0x{status:02X})"),
        "func": func,
        "func_name": FUNC_NAMES.get(func, f"UNKNOWN(0x{func:02X})"),
        "data": data,
        "data_hex": data.hex() if data else "",
        "data_len": len(data),
        "crc_received": crc_received,
        "crc_calculated": crc_calculated,
        "crc_valid": crc_valid,
        "raw_hex": raw[:expected_total].hex(),
    }


# ---------------------------------------------------------------------------
# Frame reader — syncs on 0xF7, reads exact frame length
# ---------------------------------------------------------------------------

def read_frame(ser: serial.Serial, buf: bytearray) -> tuple[bytes | None, bytearray]:
    """Extract one complete frame from the buffer.

    Returns (frame_bytes, remaining_buffer). frame_bytes is None if
    there isn't enough data yet for a complete frame.
    """
    # Sync to header byte
    while buf and buf[0] != PACK_HEAD:
        logger.debug("sync: discarding stray byte 0x%02X", buf[0])
        buf.pop(0)

    if len(buf) < 3:
        return None, buf

    length = buf[2]
    frame_len = 3 + length

    if length < 3 or length > 254:
        logger.debug("implausible LENGTH=0x%02X, discarding header", length)
        buf.pop(0)
        return None, buf

    if len(buf) < frame_len:
        return None, buf  # wait for more data

    frame = bytes(buf[:frame_len])
    buf = buf[frame_len:]
    return frame, buf


# ---------------------------------------------------------------------------
# JSONL writer
# ---------------------------------------------------------------------------

def write_jsonl(log_file, record: dict) -> None:
    log_file.write(json.dumps(record) + "\n")
    log_file.flush()


# ---------------------------------------------------------------------------
# Main capture loop
# ---------------------------------------------------------------------------

def capture_traffic(
    port: str,
    baudrate: int,
    output_path: str,
    filter_funcs: list[int] | None,
    log_crc_errors: bool,
    segment_label: str,
) -> None:
    logger.info("Opening %s at %d baud (segment: %s)", port, baudrate, segment_label)
    logger.info("Output: %s", output_path)

    if filter_funcs:
        names = [FUNC_NAMES.get(f, f"0x{f:02X}") for f in filter_funcs]
        logger.info("Filter: %s", ", ".join(names))
    else:
        logger.info("Filter: ALL frames")

    try:
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.05,
        )
    except serial.SerialException as exc:
        logger.error("Failed to open port: %s", exc)
        sys.exit(1)

    buf = bytearray()
    frame_count = 0
    logged_count = 0
    crc_error_count = 0

    logger.info("Capture running — press Ctrl+C to stop")

    with open(output_path, "a", encoding="utf-8") as log_file:
        # Write a session header so multiple runs in the same file are separated
        session_record = {
            "type": "session_start",
            "timestamp": datetime.datetime.now().isoformat(),
            "port": port,
            "baudrate": baudrate,
            "segment": segment_label,
            "filter_funcs": [f"0x{f:02X}" for f in filter_funcs] if filter_funcs else "ALL",
        }
        write_jsonl(log_file, session_record)

        try:
            while True:
                waiting = ser.in_waiting
                if waiting:
                    buf += ser.read(waiting)

                frame_bytes, buf = read_frame(ser, buf)
                if frame_bytes is None:
                    time.sleep(0.001)
                    continue

                frame_count += 1
                parsed = parse_frame(frame_bytes)

                if parsed is None:
                    continue

                if not parsed["crc_valid"]:
                    crc_error_count += 1
                    if log_crc_errors:
                        record = {
                            "type": "crc_error",
                            "timestamp": datetime.datetime.now().isoformat(),
                            "segment": segment_label,
                            **parsed,
                            # convert bytes to hex string for JSON serialisation
                            "data": parsed["data_hex"],
                        }
                        write_jsonl(log_file, record)
                    logger.warning(
                        "CRC error: func=0x%02X addr=0x%02X "
                        "got=0x%02X expected=0x%02X raw=%s",
                        parsed["func"], parsed["addr"],
                        parsed["crc_received"], parsed["crc_calculated"],
                        parsed["raw_hex"],
                    )
                    continue

                # Apply function code filter
                if filter_funcs and parsed["func"] not in filter_funcs:
                    continue

                logged_count += 1
                record = {
                    "type": "frame",
                    "timestamp": datetime.datetime.now().isoformat(),
                    "segment": segment_label,
                    "seq": logged_count,
                    "addr": f"0x{parsed['addr']:02X}",
                    "status": f"0x{parsed['status']:02X}",
                    "status_name": parsed["status_name"],
                    "func": f"0x{parsed['func']:02X}",
                    "func_name": parsed["func_name"],
                    "data_hex": parsed["data_hex"],
                    "data_len": parsed["data_len"],
                    "crc": f"0x{parsed['crc_received']:02X}",
                    "crc_valid": True,
                    "raw_hex": parsed["raw_hex"],
                }
                write_jsonl(log_file, record)

                # Console summary
                target_flag = " *** TARGET ***" if parsed["func"] in (0x10, 0x11) else ""
                logger.info(
                    "[%s] addr=0x%02X %s data(%d)=%s%s",
                    segment_label,
                    parsed["addr"],
                    parsed["func_name"],
                    parsed["data_len"],
                    parsed["data_hex"] or "(empty)",
                    target_flag,
                )

        except KeyboardInterrupt:
            logger.info("\nCapture stopped by user.")
        finally:
            ser.close()
            session_end = {
                "type": "session_end",
                "timestamp": datetime.datetime.now().isoformat(),
                "frames_seen": frame_count,
                "frames_logged": logged_count,
                "crc_errors": crc_error_count,
            }
            write_jsonl(log_file, session_end)
            logger.info(
                "Summary: %d frames seen, %d logged, %d CRC errors",
                frame_count, logged_count, crc_error_count,
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Capture CFS RS485 traffic and log to JSONL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Capture all traffic on Tap A (Hi <-> CFS):
  python3 capture_cfs_traffic.py --port /dev/ttyAMA0 --segment tap-a

  # Capture only 0x10/0x11 on Tap B (CFS <-> buffer):
  python3 capture_cfs_traffic.py --port /dev/ttyUSB0 --segment tap-b \\
      --filter-func 0x10 0x11

  # Capture all traffic including CRC errors:
  python3 capture_cfs_traffic.py --port /dev/ttyAMA0 --log-crc-errors
        """,
    )
    parser.add_argument(
        "--port", default="/dev/ttyUSB0",
        help="Serial port (default: /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--baud", type=int, default=230400,
        help="Baud rate (default: 230400)",
    )
    parser.add_argument(
        "--output", default="cfs_capture.jsonl",
        help="Output JSONL log file (default: cfs_capture.jsonl)",
    )
    parser.add_argument(
        "--filter-func", nargs="*", metavar="FUNC",
        help="Only log these function codes e.g. 0x10 0x11 (default: log all)",
    )
    parser.add_argument(
        "--segment", default="unknown",
        help="Label for this tap point e.g. tap-a or tap-b (default: unknown)",
    )
    parser.add_argument(
        "--log-crc-errors", action="store_true",
        help="Also log frames that fail CRC validation (tagged type=crc_error)",
    )

    args = parser.parse_args()

    filter_funcs = None
    if args.filter_func:
        try:
            filter_funcs = [int(f, 16) if f.startswith("0x") else int(f)
                            for f in args.filter_func]
        except ValueError as exc:
            logger.error("Invalid --filter-func value: %s", exc)
            sys.exit(1)

    capture_traffic(
        port=args.port,
        baudrate=args.baud,
        output_path=args.output,
        filter_funcs=filter_funcs,
        log_crc_errors=args.log_crc_errors,
        segment_label=args.segment,
    )
