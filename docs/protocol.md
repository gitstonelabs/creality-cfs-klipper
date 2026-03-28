# CFS RS485 Protocol Specification

This document outlines the RS485 communication protocol used by the Creality Filament System (CFS) and implemented in this Klipper integration.

---

## Overview

The CFS communicates over RS485 using a custom binary protocol. This protocol has been reverse-engineered through analysis of firmware, captured traffic, and community documentation.

---

## Physical Layer

- Interface: RS485 (half-duplex)
- Baud rate: 230400
- Data format: 8 data bits, no parity, 1 stop bit (8N1)
- Connector: Yeonho SMW200-08 (2mm pitch, 8-pin)
- RS485-A and RS485-B lines are used for differential signaling

---

## Message Format

Each message follows this structure:

```

\[0xF7]\[ADDR]\[LENGTH]\[STATUS]\[FUNC]\[DATA...]\[CRC8]

````

- HEADER (0xF7): Start-of-frame byte
- ADDR: Device address (0x01–0x04 for individual boxes, 0xFE for broadcast)
- LENGTH: Number of bytes from STATUS through CRC (i.e., STATUS + FUNC + DATA + CRC)
- STATUS:
  - 0xFF: Host-to-box operational commands
  - 0x00: Addressing commands and all responses
- FUNC: Function code (command ID)
- DATA: Variable-length payload (0–251 bytes)
- CRC8: CRC-8/SMBUS checksum over bytes [LENGTH] through last DATA byte

---

## CRC Algorithm

- Type: CRC-8/SMBUS
- Polynomial: 0x07
- Initial value: 0x00
- No reflection
- No final XOR
- Scope: msg[2:-1] (LENGTH through last DATA byte)

Example implementation in Python:

```python
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
````

***

## Command Set

| Code | Name                   | Description                            | Status      |
| ---- | ---------------------- | -------------------------------------- | ----------- |
| 0x0B | CMD_LOADER_TO_APP   | Switch from bootloader to app mode        | Implemented |
| 0xA1 | CMD_GET_SLAVE_INFO  | Discover unassigned boxes (broadcast)     | Implemented |
| 0xA0 | CMD_SET_SLAVE_ADDR  | Assign address to box by UniID            | Implemented |
| 0xA2 | CMD_ONLINE_CHECK     | Heartbeat check                          | Implemented |
| 0xA3 | CMD_GET_ADDR_TABLE  | Read address table entry                  | Implemented |
| 0x04 | CMD_SET_BOX_MODE    | Set box operating mode                    | Implemented |
| 0x0A | CMD_GET_BOX_STATE   | Query box status (4-byte response)        | Implemented |
| 0x0D | CMD_SET_PRE_LOADING | Enable/disable pre-loading per slot       | Implemented |
| 0x14 | CMD_GET_VERSION_SN  | Get firmware version and serial number    | Implemented |
| 0x10 | CMD_EXTRUDE_PROCESS  | Load filament (payload unknown)          | Stubbed     |
| 0x11 | CMD_RETRUDE_PROCESS  | Unload filament (payload unknown)        | Stubbed     |

***

## Addressing

*   Valid box addresses: 0x01–0x04
*   Broadcast address (material boxes): 0xFE
*   Broadcast address (all devices): 0xFF

***

## Notes

*   The protocol is consistent across all known CFS hardware versions.
*   No version branching (V1/V2) has been observed in the protocol.
*   The 0x10 and 0x11 commands are confirmed but require payload capture for implementation.

***

For more details, see docs/commands.md and tools/capture_cfs_traffic.py.

```

✅ Let me know when you’ve saved this file, and I’ll provide the next one: docs/commands.md.
```
