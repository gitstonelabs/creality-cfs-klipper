# G-code Command Reference

Commands registered by `creality_cfs.py` plus the full inventory from Creality
firmware analysis.

---

## Module G-code Commands

All commands confirmed working on physical Creality Hi hardware (v1.1.0).

### CFS_INIT

Runs the full 5-step auto-addressing sequence to discover and assign addresses
to all connected CFS boxes. Run once on startup, or set `auto_init: True` in config.

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

Returns one of: `IDLE (0x0F)`, `BUSY (0x00)`, `ACTIVE (0x02)`

Parameters:
- `BOX`: CFS box address (1-4, optional, default: all)

---

### CFS_VERSION

Retrieves the firmware version and serial number string via CMD_GET_VERSION_SN (0x14).
Returns a 22-character ASCII string.

```
CFS_VERSION          # query all boxes
CFS_VERSION BOX=1    # query box 1 only
```

---

### CFS_FW_VERSION

Retrieves the firmware version string via CMD_VERSION_INFO (0xF0).
Returns a more detailed build string than CFS_VERSION.

```
CFS_FW_VERSION BOX=1
```

Example output: `cfs0_050_G32-cfs0_000_113`

Parameters:
- `BOX`: CFS box address (1-4, required)

---

### CFS_EXTRUDE

Runs the full CMD_EXTRUDE_PROCESS (0x10) sequence: starts the CFS motor,
polls status, streams position feedback. Reports final filament position in mm.

```
CFS_EXTRUDE BOX=1
```

Example output:
```
CFS box 1 EXTRUDE: init_ok=True final_pos=399.84mm state=0xC4 polls=8
```

Position profile (confirmed from capture):
- `~149mm` = filament moving, just started
- `~338mm` = filament mid-path through buffer
- `~400mm` = filament arrived at toolhead sensor (stable)

Parameters:
- `BOX`: CFS box address (1-4, required)

---

### CFS_RETRUDE

Sends CMD_RETRUDE_PROCESS (0x11) to retract filament back into the CFS box.
One-shot command, acknowledges when retraction is complete.

```
CFS_RETRUDE BOX=1
```

Parameters:
- `BOX`: CFS box address (1-4, required)

---

### CFS_SET_MODE

Sets the operating mode of a CFS box via CMD_SET_BOX_MODE (0x04).

```
CFS_SET_MODE BOX=1 MODE=1        # load mode
CFS_SET_MODE BOX=1 MODE=0        # standby mode
CFS_SET_MODE BOX=1 MODE=1 PARAM=1
```

Parameters:
- `BOX`: CFS box address (1-4, required)
- `MODE`: Mode byte (0=standby, 1=load)
- `PARAM`: Mode parameter byte (default: 1)

---

### CFS_SET_PRELOAD

Enables or disables pre-loading for specific slots via CMD_SET_PRE_LOADING (0x0D).

```
CFS_SET_PRELOAD BOX=1 MASK=15 ENABLE=1    # enable all 4 slots
CFS_SET_PRELOAD BOX=1 MASK=1 ENABLE=0     # disable slot 1
```

Parameters:
- `BOX`: CFS box address (1-4, required)
- `MASK`: Bitmask (0x01=slot1, 0x02=slot2, 0x04=slot3, 0x08=slot4, 0x0F=all)
- `ENABLE`: 1 to enable, 0 to disable

---

### CFS_ADDR_TABLE

Prints the current address assignment table showing which boxes are online,
their UniIDs, and their current mode (APP or LOADER).

```
CFS_ADDR_TABLE
```

---

## Command Status Summary

| G-code Command | Function Code | Status |
|----------------|--------------|--------|
| `CFS_INIT` | 0xA0/0xA1/0xA2/0xA3/0x0B | ✅ Confirmed |
| `CFS_STATUS` | 0x08 | ✅ Confirmed (corrected from 0x0A in v1.1.0) |
| `CFS_VERSION` | 0x14 | ✅ Confirmed |
| `CFS_FW_VERSION` | 0xF0 | ✅ Confirmed from capture |
| `CFS_EXTRUDE` | 0x10 | ✅ Confirmed from capture |
| `CFS_RETRUDE` | 0x11 | ✅ Confirmed from capture |
| `CFS_SET_MODE` | 0x04 | ✅ Confirmed |
| `CFS_SET_PRELOAD` | 0x0D | ✅ Confirmed |
| `CFS_ADDR_TABLE` | n/a | ✅ Local table |
| `get_rfid()` | 0x02 | 🔵 Partial, response format unconfirmed |

---

## Complete Creality Firmware Command Inventory

Commands identified from `strings` analysis of `box_wrapper.cpython-39.so`
and `filament_rack_wrapper.cpython-39.so` on the Creality Hi.

### Box Commands (box_wrapper.so)

| G-code Command | Function Code | Description |
|----------------|--------------|-------------|
| `BOX_GET_BOX_STATE` | **0x08** | Get box state byte (corrected from 0x0A) |
| `BOX_GET_VERSION_SN` | 0x14 | 22-byte firmware version + serial number |
| `BOX_GET_RFID` | 0x02 | Read RFID tag from active spool slot |
| `BOX_GET_REMAIN_LEN` | 0x0F | Query remaining filament length (seen in capture) |
| `BOX_GET_BUFFER_STATE` | UNKNOWN | Buffer sensor state (buffer is GPIO, may not exist as RS485 cmd) |
| `BOX_GET_FILAMENT_SENSOR_STATE` | UNKNOWN | Per-slot filament sensor state |
| `BOX_GET_HARDWARE_STATUS` | UNKNOWN | Hardware diagnostic query |
| `BOX_SET_BOX_MODE` | 0x04 | Set box operating mode |
| `BOX_SET_PRE_LOADING` | 0x0D | Configure pre-loading slot mask |
| `BOX_SET_CURRENT_BOX_IDLE_MODE` | UNKNOWN | Set per-slot idle mode |
| `BOX_SET_TEMP` | UNKNOWN | Set temperature target |
| `BOX_EXTRUDE_MATERIAL` | UNKNOWN | Push filament from box toward extruder |
| `BOX_EXTRUDE_PROCESS` | **0x10** | Full extrude state machine (confirmed) |
| `BOX_EXTRUDE_2_PROCESS` | UNKNOWN | Secondary extrude process |
| `BOX_EXTRUDER_EXTRUDE` | UNKNOWN | Extrude through extruder gear |
| `BOX_EXTRUDE_ZLIFT` | UNKNOWN | Z-lift during extrude |
| `BOX_EXTRUSION_ALL_MATERIALS` | UNKNOWN | Extrude all loaded materials |
| `BOX_GO_TO_EXTRUDE_POS` | UNKNOWN | Move toolhead to extrude position |
| `BOX_TN_EXTRUDE` | UNKNOWN | Tool-N extrude (channel-specific) |
| `BOX_RETRUDE_MATERIAL` | UNKNOWN | Retract filament into box |
| `BOX_RETRUDE_MATERIAL_WITH_TNN` | UNKNOWN | Retract with channel selector |
| `BOX_RETRUDE_PROCESS` | **0x11** | Full retract state machine (confirmed) |
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
| `BOX_UPDATE_CONNECT` | UNKNOWN | Update connection state |
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
| `BOX_CHECK_MATERIAL_REFILL` | UNKNOWN | Check if refill needed |
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

### Addressing Commands (auto_addr_wrapper.py, full source available)

| Command | Function Code | Description |
|---------|--------------|-------------|
| `CMD_LOADER_TO_APP` | 0x0B | Wake boxes from bootloader mode |
| `CMD_GET_SLAVE_INFO` | 0xA1 | Discover unaddressed boxes by UniID |
| `CMD_SET_SLAVE_ADDR` | 0xA0 | Assign RS485 address to a specific UniID |
| `CMD_ONLINE_CHECK` | 0xA2 | Heartbeat ping to verify box is online |
| `CMD_GET_ADDR_TABLE` | 0xA3 | Query box's current address assignment |

### Filament Rack Commands (filament_rack_wrapper.so)

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

## Known Error Codes

| Key | Message |
|-----|---------|
| key835 | extrude error, maybe it's blocked at the connections |
| key836 | extrude error, blockage between connections and filament sensor |
| key837 | extrude error, blockage between filament sensor and extrusion gear |
| key838 | extrude error, through the connections but not extruding |
| key839 | filament error, no filament detected at box extrude position |
| key841 | cut error, cut sensor not detected, cutting not rebound |
| key846 | empty printing, box speed is smaller than extruder speed |
| key852 | check extruder filament sensor and box sensor state |
| key854 | the presence of filament when cutting detected |
| key855 | cut position error |
| key856 | no cutter |
| key857 | motor load error |
| key864 | extrude error, extrude but not trigger buffer full limit |

---

## Physical Filament Path

```
CFS Box (slots 1-4)
  [filament reel / spool]
       ↓
  [connections / coupling joint]    ← key835: blockage here
       ↓
  [box-side filament sensor]        ← key836: blockage between joint and sensor
       ↓
  [box extrusion gear / motor]      ← key837: blockage between sensor and gear
       ↓                            ← key838: gear turned, filament didn't exit
  [4-way splitter/junction]         ← merges 4 slot paths into 1 Bowden tube
       ↓
  [filament buffer]                 ← key864: extrude OK but buffer not filled
       ↓                            ← buffer state via GPIO pins 2/3, NOT RS485
  [Bowden tube to printer]
       ↓
  [filament cutter]                 ← key841: cut sensor not triggered
       ↓                            ← key854: filament present at cut position
  [Nebula / toolhead extruder]
       ↓
  [hotend]
```

The box extruder motor and toolhead extruder run simultaneously during loading
box pushes, toolhead pulls. The buffer absorbs the rate difference.

The cutter is triggered mechanically by the toolhead reaching the right X-rail
limit (X=260 on the Creality Hi 260x260 bed). The lever at that position
depresses the cutter blade. A hall sensor or mechanical switch confirms the cut.

---

## Notes

- All commands use 8N1 at 230400 baud (confirmed from capture)
- `STATUS=0xFF` for operational requests confirmed from live capture (v1.1.0)
- Buffer state is GPIO-only, no RS485 command needed or observed
- `CMD_GET_BUFFER_STATE` from box_wrapper.so strings may not exist as a
  separate RS485 command since the buffer uses direct GPIO lines

For protocol details see `docs/protocol.md`.
For hardware pinout see `docs/hardware.md`.