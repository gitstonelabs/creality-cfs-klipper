# G-code Command Reference

This document describes the G-code commands registered by the CFS Klipper module.

---

## Overview

The CFS Klipper module registers custom G-code commands to control and monitor the Creality Filament System (CFS). These commands can be used in macros or manually from the Klipper console.

---

## Command List

### CFS_INIT

Initializes the CFS system by performing auto-addressing and preparing all connected boxes.

Usage:
```

CFS\_INIT

```

---

### CFS_STATUS

Queries the current status of a specific CFS box.

Usage:
```

CFS\_STATUS ADDR=<1-4>

```

- ADDR: Address of the CFS box (default: 1)

---

### CFS_VERSION

Retrieves the firmware version and serial number from a specific CFS box.

Usage:
```

CFS\_VERSION ADDR=<1-4>

```

- ADDR: Address of the CFS box (default: 1)

---

### CFS_SET_MODE

Sets the operating mode of a CFS box.

Usage:
```

CFS\_SET\_MODE ADDR=<1-4> MODE=<0-255> PARAM=<0-255>

```

- ADDR: Address of the CFS box (default: 1)
- MODE: Mode byte (e.g., 0)
- PARAM: Parameter byte (e.g., 1)

---

### CFS_SET_PRELOAD

Enables or disables pre-loading for specific slots on a CFS box.

Usage:
```

CFS\_SET\_PRELOAD ADDR=<1-4> SLOT\_MASK=<0-15> ENABLE=<0|1>

```

- ADDR: Address of the CFS box (default: 1)
- SLOT_MASK: Bitmask for slots (e.g., 0x0F = all 4 slots)
- ENABLE: 1 to enable, 0 to disable

---

### CFS_ADDR_TABLE

Reads the address table entry from a CFS box.

Usage:
```

CFS\_ADDR\_TABLE ADDR=<1-4>

```

- ADDR: Address of the CFS box (default: 1)

---

## Not Yet Implemented

The following commands are defined but not yet implemented due to unknown payload structures:

- CFS_LOAD_FILAMENT SLOT=<1-4> → CMD_EXTRUDE_PROCESS (0x10)
- CFS_UNLOAD_FILAMENT → CMD_RETRUDE_PROCESS (0x11)

These commands will raise NotImplementedError until the payloads are captured and decoded.

---

## Notes

- All commands are case-insensitive.
- Commands can be used in macros or manually from the Klipper terminal.
- For examples, see configs/cfs_macros.cfg.

---

For protocol-level details, see docs/protocol.md.
