# RS485 Traffic Captures

Raw binary captures of live RS485 traffic between a Creality Hi printer and
CFS box (v1 hardware, 1 box, 4 slots). Captured with a CH341 USB-RS485 adapter
in passive tap mode (sniffer only, not driving the bus).

**Hardware:** Creality Hi (F018), CFS box firmware `cfs0_050_G32-cfs0_000_113`,
motor controller firmware `mot2_023_C30-mot2_002_071`.

**Capture setup:**
```bash
stty -F /dev/ttyUSB0 230400 cs8 -cstopb -parenb raw -echo -ixon -ixoff
cat /dev/ttyUSB0 > capture_file.bin &
# ... trigger the operation ...
kill %1
```

---

## Files

### `cfs_toolchange_capture_20260520_013844.bin`

**Size:** ~49KB  
**Date:** 2026-05-20  
**Operation:** T0 → T1 → T2 → T3 tool changes triggered via Klipper console
on a Creality Hi with 3 of 4 slots loaded (ABS filament). Captured using a
Jetson Orin Nano running mainline Klipper as a passive sniffer.

**Contains:**
- Full auto-addressing boot sequence (0xA2, 0xA1, 0x0A, 0xA0, 0xA3)
- CMD_VERSION_INFO (0xF0): firmware version strings for CFS box and motor controller
- CMD_GET_VERSION_SN (0x14): version/serial number string
- CMD_GET_BOX_STATE (0x08): state polling throughout sequence
- CMD_SET_BOX_MODE (0x04): mode transitions before/after tool changes
- CMD_SET_PRE_LOADING (0x0D): slot configuration
- CMD_GET_RFID (0x02): RFID queries (returned "unknown", RFID board disconnected)
- **CMD_EXTRUDE_PROCESS (0x10)**: complete sequence with all three sub-commands,
  multiple tool changes, full streaming position feedback
- **CMD_RETRUDE_PROCESS (0x11)**: multiple retract cycles
- CMD_GET_REMAIN_LEN (0x0F): remaining filament queries

**Key frame counts:**
```
cmd 0x02 = 11 frames   (GET_RFID)
cmd 0x03 = 6 frames    (unknown)
cmd 0x04 = 18 frames   (SET_BOX_MODE)
cmd 0x08 = 18 frames   (GET_BOX_STATE)
cmd 0x0a = 1650 frames (LOADER_TO_APP, boot sequence)
cmd 0x0b = 2 frames    (LOADER_TO_APP broadcast)
cmd 0x0d = 13 frames   (SET_PRE_LOADING)
cmd 0x0f = 6 frames    (GET_REMAIN_LEN)
cmd 0x10 = 114 frames  (EXTRUDE_PROCESS, primary target)
cmd 0x11 = 10 frames   (RETRUDE_PROCESS, primary target)
cmd 0x14 = 4 frames    (GET_VERSION_SN)
cmd 0x56 = 2 frames    (unknown)
cmd 0xa0 = 16 frames   (SET_SLAVE_ADDR)
cmd 0xa1 = 1036 frames (GET_SLAVE_INFO)
cmd 0xa2 = 2014 frames (ONLINE_CHECK)
cmd 0xa3 = 14 frames   (GET_ADDR_TABLE)
cmd 0xf0 = 28 frames   (VERSION_INFO)
```

**What was decoded from this capture:**
- CMD_EXTRUDE_PROCESS (0x10) sub-commands and response format (see protocol.md)
- CMD_RETRUDE_PROCESS (0x11) payload confirmed
- CMD_GET_BOX_STATE corrected from 0x0A to 0x08
- STATUS=0xFF for operational commands confirmed
- Filament path length confirmed: ~398-400mm
- CFS firmware version strings decoded from 0xF0 frames

---

### `buffer_test_20260520_022430.bin`

**Size:** ~3.3KB  
**Date:** 2026-05-20  
**Operation:** Buffer switch manually triggered 3-4 times before and after
a retract sequence. Captured to determine whether buffer switch triggers
generate RS485 traffic.

**Contains:**
- CMD_GET_BOX_STATE (0x08) polling
- CMD_SET_BOX_MODE (0x04): mode transition
- CMD_RETRUDE_PROCESS (0x11): one retract cycle
- Auto-addressing polling (0xA1, 0xA2)

**Key finding:** Buffer switch triggers generate **no RS485 traffic**.
The buffer state is communicated via direct GPIO lines (pins 2 and 3 on the
6-pin connector), not over RS485. This means:
- No RS485 command is needed to read buffer state
- `BOX_GET_BUFFER_STATE` from box_wrapper.so strings may not exist as an RS485
  command, or it reads the GPIO state via a different mechanism
- Buffer integration on any Klipper host uses `[filament_switch_sensor]` on a
  GPIO pin wired to pin 2 or 3 of the CFS connector

**BOX_STATE values observed:**
```
0x0F = IDLE   (standby, normal polling)
0x00 = BUSY   (transitioning)
0x02 = ACTIVE (during retract sequence)
```

---

## Parsing Captures

Use Python to analyze capture files:

```python
data = open('cfs_toolchange_capture_20260520_013844.bin', 'rb').read()
i = 0
frames = []
while i < len(data) - 5:
    if data[i] == 0xf7:
        length = data[i+2] if i+2 < len(data) else 0
        cmd = data[i+4] if i+4 < len(data) else 0
        status = data[i+3]
        frame = data[i:i+length+2]
        frames.append((i, data[i+1], status, cmd, frame))
        i += max(length+2, 1)
    else:
        i += 1

# Show all unique command codes
from collections import Counter
for cmd, count in sorted(Counter(f[3] for f in frames).items()):
    print(f'cmd {cmd:#04x} = {count} frames')

# Show all non-addressing frames
for offset, addr, status, cmd, frame in frames:
    if cmd not in {0xa0, 0xa1, 0xa2, 0xa3, 0x0a}:
        direction = 'REQ' if status == 0xff else 'RSP'
        payload = frame[5:-1].hex()
        print(f'{offset:#06x} addr={addr:#04x} {direction} cmd={cmd:#04x} payload={payload}')
```

Filter for specific commands:

```python
# Show only 0x10 EXTRUDE_PROCESS frames
for offset, addr, status, cmd, frame in frames:
    if cmd == 0x10:
        direction = 'REQ' if status == 0xff else 'RSP'
        payload = frame[5:-1]
        if direction == 'RSP' and len(payload) == 3:
            state = payload[0]
            pos = (payload[1] << 8) | payload[2]
            state_str = 'ACCEL' if state == 0xc3 else 'SPEED'
            print(f'{offset:#06x} RSP state={state_str} pos={pos/100:.2f}mm')
        else:
            print(f'{offset:#06x} {direction} payload={payload.hex()}')
```

---

## Verification

The CRC algorithm can be verified against any frame in these captures:

```python
def crc8_cfs(data):
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) if crc & 0x80 else crc << 1
            crc &= 0xFF
    return crc

# Verify: f7 01 03 00 a2 da
# CRC scope = msg[2:-1] = [03, 00, a2]
frame = bytes.fromhex('f70103 00a2da')
assert crc8_cfs(frame[2:-1]) == frame[-1], "CRC mismatch"
print("CRC verified")
```