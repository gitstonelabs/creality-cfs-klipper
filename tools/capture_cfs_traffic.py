#!/usr/bin/env python3
"""
CFS RS485 Traffic Capture Tool

Captures and logs RS485 traffic from the Creality Filament System (CFS),
filtering for 0x10 (EXTRUDE_PROCESS) and 0x11 (RETRUDE_PROCESS) commands.

Usage:
    python3 capture_cfs_traffic.py --port /dev/ttyUSB0 --baud 230400

Author: gitstonelabs
License: GPL-3.0
"""

import serial
import argparse
import datetime

def parse_frame(frame):
    if len(frame) < 6 or frame[0] != 0xF7:
        return None
    addr = frame[1]
    length = frame[2]
    status = frame[3]
    func = frame[4]
    data = frame[5:-1]
    crc = frame[-1]
    return {
        "addr": addr,
        "length": length,
        "status": status,
        "func": func,
        "data": data,
        "crc": crc
    }

def capture_traffic(port, baudrate, output_file):
    print(f"Starting capture on {port} at {baudrate} baud")
    print(f"Logging to {output_file}")
    ser = serial.Serial(port, baudrate, timeout=0.1)
    buffer = bytearray()
    with open(output_file, "a") as log:
        try:
            while True:
                if ser.in_waiting:
                    buffer += ser.read(ser.in_waiting)
                    while len(buffer) >= 6:
                        if buffer[0] != 0xF7:
                            buffer.pop(0)
                            continue
                        length = buffer[2]
                        frame_len = 3 + length
                        if len(buffer) < frame_len:
                            break
                        frame = buffer[:frame_len]
                        buffer = buffer[frame_len:]
                        parsed = parse_frame(frame)
                        if parsed and parsed["func"] in [0x10, 0x11]:
                            timestamp = datetime.datetime.now().isoformat()
                            cmd = "EXTRUDE_PROCESS" if parsed["func"] == 0x10 else "RETRUDE_PROCESS"
                            log.write(f"[{timestamp}] {cmd} (0x{parsed['func']:02X})\n")
                            log.write(f"ADDR: 0x{parsed['addr']:02X}, STATUS: 0x{parsed['status']:02X}, CRC: 0x{parsed['crc']:02X}\n")
                            log.write(f"DATA ({len(parsed['data'])} bytes): {parsed['data'].hex()}\n")
                            log.write("-" * 60 + "\n")
                            print(f"[{timestamp}] Captured {cmd} with {len(parsed['data'])} bytes of data")
        except KeyboardInterrupt:
            print("\nCapture stopped.")
        finally:
            ser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capture CFS RS485 traffic")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="Serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=230400, help="Baud rate (default: 230400)")
    parser.add_argument("--output", default="cfs_capture.log", help="Output log file")
    args = parser.parse_args()
    capture_traffic(args.port, args.baud, args.output)
