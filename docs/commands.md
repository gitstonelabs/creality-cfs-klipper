# G-code Command Reference

This document describes the G-code commands registered by the CFS Klipper module,
plus the full inventory of commands identified from Creality firmware analysis.

---

## Module G-code Commands

These commands are registered by `creality_cfs.py` and available in the Klipper console
or from macros.

### CFS_INIT

Runs the full 5-step auto-addressing sequence to discover and assign addresses to
all connected CFS boxes. Should be run once on startup (or automatically via `auto_init: True`).

```
CFS_INIT
```

---

### CFS_STATUS

Queries the current operating state of one or all CFS boxes.

```
CFS_STATUS           # query all boxes
CFS_STATUS BOX=1     # query box 1 only
```

Parameters:
- `BOX`: Address of the CFS box (1-4, optional, default: all)

---

### CFS_VERSION

Retrieves the firmware version and serial number string from a CFS box.
Returns a 22-character ASCII string, e.g. `11010000843215B625AHSC`.

```
CFS_VERSION          # query all boxes
CFS_VERSION BOX=1    # query box 1 only
```

---

### CFS_SET_MODE

Sets the operating mode of a CFS box.

```
CFS_SET_MODE BOX=1 MODE=1        # load mode
CFS_SET_MODE BOX=1 MODE=0        # standby mode
CFS_SET_MODE BOX=1 MODE=1 PARAM=1
```

Parameters:
- `BOX`: Address of the CFS box (1-4, required)
- `MODE`: Mode byte (0=standby, 1=load; other values TBD)
- `PARAM`: Mode parameter byte (default: 1)

---

### CFS_SET_PRELOAD

Enables or disables pre-loading for specific slots on a CFS box.

```
CFS_SET_PRELOAD BOX=1 MASK=15 ENABLE=1    # enable all 4 slots
CFS_SET_PRELOAD BOX=1 MASK=1 ENABLE=0     # disable slot 0
```

Parameters:
- `BOX`: Address of the CFS box (1-4, required)
- `MASK`: Bitmask for slots (0x01=slot1, 0x02=slot2, 0x04=slot3, 0x08=slot4, 0x0F=all)
- `ENABLE`: 1 to enable, 0 to disable

---

### CFS_ADDR_TABLE

Prints the current address assignment table showing which boxes are online.

```
CFS_ADDR_TABLE
```

---

## Stubbed Commands (Not Yet Implemented)

These methods exist in `creality_cfs.py` but raise `NotImplementedError` until
the RS485 payload is captured and confirmed.

| Method | Function Code | Klipper Equivalent | Status |
|--------|--------------|-------------------|--------|
| `extrude_process()` | 0x10 | `BOX_EXTRUDE_PROCESS` | Stub — payload unknown |
| `retrude_process()` | 0x11 | `BOX_RETRUDE_PROCESS` | Stub — payload unknown |
| `get_rfid()` | 0x02 | `BOX_GET_RFID` | Partial — empty payload sent, response format unknown |

To implement these, capture RS485 traffic using `tools/capture_cfs_traffic.py`
while triggering the corresponding operation on a stock Creality Hi printer.

---

## Complete Creality Firmware Command Inventory

The following commands were identified from `strings` analysis of
`box_wrapper.cpython-39.so` and `filament_rack_wrapper.cpython-39.so` on the
Creality Hi. Function codes marked UNKNOWN require RS485 capture to determine.

### Box Commands (box_wrapper.so)

| G-code Command | Function Code | Description |
|----------------|--------------|-------------|
| `BOX_GET_BOX_STATE` / `GET_BOX_STATE` | 0x0A | Query 4-byte box operating state |
| `BOX_GET_VERSION_SN` | 0x14 | Query 22-byte firmware version + serial number |
| `BOX_GET_RFID` | 0x02 (unconfirmed) | Read RFID tag from active spool slot |
| `BOX_GET_REMAIN_LEN` | UNKNOWN | Query remaining filament length on spool |
| `BOX_GET_BUFFER_STATE` | UNKNOWN | Query buffer/feeder sensor state |
| `BOX_GET_FILAMENT_SENSOR_STATE` | UNKNOWN | Query per-slot filament sensor state |
| `BOX_GET_HARDWARE_STATUS` | UNKNOWN | Hardware diagnostic query |
| `BOX_SET_BOX_MODE` / `SET_BOX_MODE` | 0x04 | Set box operating mode |
| `BOX_SET_PRE_LOADING` | 0x0D | Configure pre-loading slot mask |
| `BOX_SET_CURRENT_BOX_IDLE_MODE` | UNKNOWN | Set per-slot idle mode |
| `BOX_SET_TEMP` | UNKNOWN | Set temperature target |
| `BOX_EXTRUDE_MATERIAL` | UNKNOWN | Push filament from box toward extruder |
| `BOX_EXTRUDE_PROCESS` | 0x10 | Full extrude state machine |
| `BOX_EXTRUDE_2_PROCESS` | UNKNOWN | Secondary extrude process |
| `BOX_EXTRUDER_EXTRUDE` | UNKNOWN | Extrude through extruder gear |
| `BOX_EXTRUDE_ZLIFT` | UNKNOWN | Z-lift during extrude |
| `BOX_EXTRUSION_ALL_MATERIALS` | UNKNOWN | Extrude all loaded materials |
| `BOX_GO_TO_EXTRUDE_POS` | UNKNOWN | Move toolhead to extrude position |
| `BOX_TN_EXTRUDE` | UNKNOWN | Tool-N extrude (channel-specific) |
| `BOX_RETRUDE_MATERIAL` | UNKNOWN | Retract filament into box |
| `BOX_RETRUDE_MATERIAL_WITH_TNN` | UNKNOWN | Retract with channel selector |
| `BOX_RETRUDE_PROCESS` | 0x11 | Full retract state machine |
| `BOX_CUT_MATERIAL` | UNKNOWN | Cut filament |
| `BOX_CUT_POS_DETECT` | UNKNOWN | Detect/calibrate cutter position |
| `BOX_CUT_STATE` | UNKNOWN | Query cutter state |
| `BOX_CUT_HALL_ZERO` | UNKNOWN | Zero the cutter hall sensor |
| `BOX_CUT_HALL_TEST` | UNKNOWN | Test the cutter hall sensor |
| `BOX_MOVE_TO_CUT` | UNKNOWN | Move toolhead to cut position |
| `BOX_MATERIAL_FLUSH` | UNKNOWN | Basic filament flush/purge |
| `BOX_MATERIAL_CHANGE_FLUSH` | UNKNOWN | Flush during material change |
| `BOX_GENERATE_FLUSH_ARRAY` | UNKNOWN | Pre-compute flush schedule |
| `BOX_GET_FLUSH_LEN` | UNKNOWN | Get required flush length |
| `BOX_GET_FLUSH_VELOCITY_TEST` | UNKNOWN | Test flush speed |
| `BOX_SHOW_FLUSH_LIST` | UNKNOWN | Display flush schedule |
| `BOX_CREATE_CONNECT` | UNKNOWN | Establish CFS connection |
| `BOX_UPDATE_CONNECT` | UNKNOWN | Update connection state (ADDR, NUM params) |
| `BOX_CTRL_CONNECTION_MOTOR_ACTION` | UNKNOWN | Control connection motor |
| `BOX_COMMUNICATION_TEST` | UNKNOWN | Diagnostic communication test |
| `BOX_MODIFY_TN` | UNKNOWN | Modify tool-N slot data |
| `BOX_MODIFY_TN_DATA` | UNKNOWN | Modify TN slot data |
| `BOX_MODIFY_TN_INNER_DATA` | UNKNOWN | Modify TN inner data |
| `BOX_SHOW_TNN_INNER_DATA` | UNKNOWN | Display TN inner data |
| `BOX_UPDATE_SAME_MATERIAL_LIST` | UNKNOWN | Update materials with same color |
| `BOX_START_PRINT` | UNKNOWN | Start print sequence |
| `BOX_END_PRINT` | UNKNOWN | End print sequence |
| `BOX_END` | UNKNOWN | Finalize box session |
| `BOX_POWER_LOSS_RESTORE` | UNKNOWN | Power-loss recovery |
| `BOX_NOZZLE_CLEAN` | UNKNOWN | Trigger nozzle cleaning routine |
| `BOX_BLOW` | UNKNOWN | Air blow for path cleaning |
| `BOX_MOVE_TO_SAFE_POS` | UNKNOWN | Emergency safe position |
| `BOX_ENABLE_AUTO_REFILL` | UNKNOWN | Enable automatic refill |
| `BOX_CHECK_MATERIAL_REFILL` | UNKNOWN | Check if refill is needed |
| `BOX_ENABLE_CFS_PRINT` | UNKNOWN | Enable CFS during print |
| `BOX_TIGHTEN_UP_ENABLE` | UNKNOWN | Tension control |
| `BOX_MEASURING_WHEEL` | UNKNOWN | Measuring wheel calibration |
| `BOX_SAVE_FAN` | UNKNOWN | Save fan state |
| `BOX_RESTORE_FAN` | UNKNOWN | Restore fan state |
| `BOX_ERROR_CLEAR` | UNKNOWN | Clear error state |
| `BOX_ERROR_RESUME_PROCESS` | UNKNOWN | Resume after error |
| `BOX_TNN_RETRY_PROCESS` | UNKNOWN | Retry TN operation |
| `BOX_TEST_MAKE_ERROR` | UNKNOWN | Inject test error |
| `BOX_SEND_DATA` | UNKNOWN | Generic low-level data send |
| `BOX_NUM_POS` | UNKNOWN | Number/position query |

### Addressing Commands (auto_addr_wrapper.py — full source available)

| Command | Function Code | Description |
|---------|--------------|-------------|
| `CMD_LOADER_TO_APP` | 0x0B | Wake boxes from bootloader mode |
| `CMD_GET_SLAVE_INFO` | 0xA1 | Discover unaddressed boxes by UniID |
| `CMD_SET_SLAVE_ADDR` | 0xA0 | Assign RS485 address to a specific UniID |
| `CMD_ONLINE_CHECK` | 0xA2 | Heartbeat ping to verify box is online |
| `CMD_GET_ADDR_TABLE` | 0xA3 | Query box's current address assignment |

### Filament Rack Commands (filament_rack_wrapper.so)

The filament rack is the multi-spool carousel inside the CFS box. It appears to be
a separate addressable entity from the box controller.

| G-code Command | Function Code | Description |
|----------------|--------------|-------------|
| `FILAMENT_RACK` | UNKNOWN | Main rack control command |
| `FILAMENT_RACK_FLUSH` | UNKNOWN | Flush filament through rack |
| `FILAMENT_RACK_MODIFY` | UNKNOWN | Modify rack slot parameters |
| `FILAMENT_RACK_PRE_FLUSH` | UNKNOWN | Pre-flush preparation |
| `FILAMENT_RACK_SET_TEMP` | UNKNOWN | Set rack temperature target |
| `FILAMENT_RUNOUT_FLUSH` | UNKNOWN | Flush on filament runout event |
| `SET_COOL_TEMP` | UNKNOWN | Set cooling temperature |

---

## Known Error Codes (from box_wrapper.so strings)

| Key | Message |
|-----|---------|
| key835 | extrude error, maybe it's blocked at the connections |
| key836 | extrude error, maybe there's a blockage between the connections and the filament sensor |
| key837 | extrude error, maybe there is a blockage between the filament sensor and the extrusion gear |
| key838 | extrude error, through the connections but not extrude |
| key839 | filament error, no filament detected at box extrude position |
| key841 | cut error, cut sensor not detected, cutting not rebound |
| key846 | empty printing, box speed is smaller than extruder |
| key852 | check extruder filament sensor and box sensor state |
| key854 | the presence of filament when cutting detected |
| key855 | cut position error |
| key856 | no cutter |
| key857 | motor load error |
| key864 | extrude error, extrude but not trigger buffer full limit |

These error keys correspond to messages in Creality's UI translation files.

---

## Physical Filament Path (inferred from error messages)

```
CFS Box
  [filament reel / spool slot 1-4]
       ↓
  [connections / coupling joint] ← key835: blockage here
       ↓
  [box-side filament sensor] ← key836: blockage between joint and sensor
       ↓
  [box extrusion gear] ← key837: blockage between sensor and gear
       ↓              ← key838: gear turned but filament didn't exit
  [in-line buffer]     ← key864: extrude successful but buffer not filled
       ↓
  [Bowden tube to printer]
       ↓
  [cutter] ← key841: cut sensor not triggered; key854: filament present at cut
       ↓
  [extruder]
       ↓
  [hotend]
```

---

## Notes

- All commands use 8N1 serial framing at 230400 baud (confirmed).
- Commands marked UNKNOWN require RS485 traffic capture to determine function codes and payloads.
- STATUS byte for operational command requests is currently assumed 0xFF but unconfirmed.
  Capture will validate this. If commands fail on non-Creality hardware, try STATUS=0x00.
- The CFS does not require RTS pin toggling; direction control is handled by auto-direction
  hardware on the CFS side.

For protocol-level details including frame format and CRC algorithm, see `protocol.md`.
