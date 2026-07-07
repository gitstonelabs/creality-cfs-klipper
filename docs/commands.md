# G-code Command Reference

Commands registered by `creality_cfs.py` (v1.4.0) plus the full inventory from
Creality firmware analysis.

**Validation status.** The transport, CRC, and addressing layers are
capture-validated on this module. The load/unload/cut/flush choreography was
hardware-validated on the reference implementation (the open `box.py` stack,
exercised on a real Creality Hi with a CFS v1 box; identical wire protocol).
This module's port of that choreography is wire-faithful but has not itself
been exercised on hardware yet. Neither an untested guess nor fully validated
here: the wire frames are proven, this specific code path is not.

---

## Topology: TOOL= vs BOX=

A CFS is ONE controller on the RS485 bus (normally address 0x01, so `BOX=1`)
with FOUR slots selected by a data-byte bitmask: T0=0x01, T1=0x02, T2=0x04,
T3=0x08. `TOOL=` selects the slot bitmask. `BOX=` selects the controller bus
address and only matters for multi-box daisy-chains (a second 4-slot unit at
address 2, giving tools T4..T7). The two are separate axes. Older versions of
this module mapped T0..T3 to BOX=1..4, which addressed absent controllers and
no-op'd on real hardware.

The bundled `configs/cfs_macros.cfg` provides T0..T3 macros that select
`TOOL=0..3` on `BOX=1` and run the full change sequence (cut, unload, load,
flush).

---

## Module G-code Commands

### CFS_INIT

Runs the full 5-step auto-addressing sequence to discover and assign addresses
to all connected CFS boxes. Runs automatically at klippy:ready unless
`auto_init` is disabled. After the 0xA0 address assign the box slave-MCU needs
about 9.5 s to wake, so the module follows addressing with a 12 s single-shot
0x0A probe (up to 8 retries), then the stock connect-init burst: feed mode,
0x14 version read, the two-frame pre-load self-check with 0x08 reads, and the
all-slot 0x02/0x03 presence read (the all-slot scan takes about 11 s).

```
CFS_INIT
```

---

### CFS_STATUS

Queries box state via CMD_GET_BOX_STATE (0x0A). The request is sent with an
EMPTY data payload. The reply data is 4 bytes `[b0][b1][b2][b3]`:

- `b0`/`b1`: an opaque firmware base that drifts per box/firmware (0x1a20,
  0x1b26, 0x1c24, 0x1d21 all observed on identical hardware). It carries no
  load information. Never gate on it.
- `b2`: substatus (0x00 = OK).
- `b3`: the real load flag. 0x02 = loaded/print-locked, 0x00 = feed/change
  mode.

The frame STATUS byte doubles as the box's async event channel and is surfaced
in the output: 0x00 idle, 0x30 insert/update push (data becomes a 4-byte
per-slot phase array; phase 0x03 in any slot byte = insert complete), 0x16
busy/active-cal (normal transiently, a wedge only if it never settles).

```
CFS_STATUS           # query all boxes
CFS_STATUS BOX=1     # query box 1 only
```

Example output: `Box 1 (0x01): LOADED raw=1c240002`

Parameters:
- `BOX`: CFS controller address (1-4, optional, default: all)

History: this doc once claimed GET_BOX_STATE was 0x08, "corrected from 0x0A".
That was itself the error. On the Hi wire 0x0A IS box-state and 0x08 is a
separate command, GET_HARDWARE_STATUS (the sensor flag read). Corrected in
v1.2.0 from CRC-verified captures. The old `IDLE (0x0F)` / `BUSY (0x00)` /
`ACTIVE (0x02)` single-flag return model never matched the 0x0A payload and
was removed with it.

---

### CFS_VERSION

Retrieves the firmware version and serial number string via CMD_GET_VERSION_SN
(0x14). Returns a 22-character ASCII string.

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
- `BOX`: CFS controller address (1-4, required)

---

### CFS_EXTRUDE

Loads filament from a CFS slot to the toolhead: the full sensor-gated
choreography from the validated reference stack.

```
CFS_EXTRUDE TOOL=2
CFS_EXTRUDE TOOL=0 BOX=1 TEMP=235
```

Sequence: blocking M109 melt guard, 0x04 `[00][slot]` enter feed mode, 0x0F
engage the feeder motor, one-shot 0x08 liveness ping (logged, not a gate),
the sensor-gated 0x10 push loop, 0x05 cut check, 0x04 `[slot][00]` print
mode, 0x0F release.

Every 0x10 frame carries three data bytes `[slot][stage_hi][stage_lo]`:
`00 00` init/arm, `04 00` engage, `05 00` push+measure, `06 00` settle,
`07 03` finalize. The load is SENSOR-GATED, not position-settled: the 0x05
push repeats, and the 06/07 finalize fires only after the toolhead filament
switch trips; the whole cycle re-arms (fresh `00 00`) until the switch
latches. The box self-limits to about 3 real pushes per arm, then fast-acks
no-op pushes; a per-push wheel-advance watchdog detects that and re-arms
immediately. The box HOLDS each stage reply until the mechanical step
completes, so the blocking reply is the ready mechanism. There is no host
status poll. Wall budget: 90 s (`load_wall_budget`).

Push reply decode (corrected in v1.4.0): the 0x05 reply payload is a 4-byte
big-endian IEEE-754 float, the cumulative measuring-wheel position (negative;
magnitude grows as filament feeds). The pre-v1.4.0
`[motor state 0xC3/0xC4][uint16 position in 0.01mm]` model was a misparse of
that float (the "state" byte was the float's exponent byte), and the old
position-profile milestones derived from it are withdrawn. The physical path
length remains about 398-400 mm from box motor to toolhead sensor on the
reference printer.

Parameters:
- `TOOL`: slot 0-3 (required; selects the slot bitmask)
- `BOX`: controller address (1-4, optional, default 1; multi-box chains only)
- `TEMP`: melt temperature in C (optional, default `extrude_temp`; hard floor
  170 C. The box-motor feed bypasses Klipper's cold-extrude protection, so
  the module enforces its own floor and blocks on M109 first)

The hotend purge is NOT part of the load. Run CFS_FLUSH separately, exactly
as the validated stack sequences it. Without a configured `filament_sensor`
the load runs one ungated cycle and cannot confirm arrival.

---

### CFS_RETRUDE

Unloads filament from the toolhead back into the CFS: the full validated
choreography.

```
CFS_RETRUDE TOOL=0
CFS_RETRUDE TOOL=1 TEMP=235
```

Sequence: blocking M109 melt guard, 0x04 `[00][slot]` enter feed mode, 0x08
`[00]` material sensor read, START 0x11 `[slot][00]`, ONE toolhead
`G1 E-15 F360` pull, 0x08 `[01]` connections read, FINISH 0x11 `[slot][01]`.
Both 0x11 frames carry the slot bitmask and both ACK with the same bare
status-0x00 frame. The FINISH ACK is held about 9.6 s while the box reels the
filament fully in. Completion is gated on the toolhead filament switch
CLEARING, not on any reply status; the reply statuses are logged as
diagnostics only (the old in-progress/NAK status-poll model is
wire-disproven). Wall budget: 60 s. Sensorless rigs fall back to box-state
corroboration.

Parameters:
- `TOOL`: slot 0-3 (required)
- `BOX`: controller address (1-4, optional, default 1)
- `TEMP`: melt temperature in C (optional, default `extrude_temp`)

---

### CFS_CUT

Mechanical filament cut. The cut is MECHANICAL: the toolhead rams the
frame-mounted blade lever. There is no bus cut command; 0x05 CUT_STATE only
reads the result the controller latches afterward.

```
CFS_CUT
CFS_CUT BOX=1 TEMP=220
```

Safety rails (ported from the validated implementation):
- Hard guard: refuses to run without `cut_switch_pin` configured (a blind ram
  with no cutter switch could crash the toolhead).
- Zero-travel refusal: refuses when the cut position equals the pre-cut
  position (an uncalibrated cut would move nowhere; the follow-up load then
  jams against the uncut strand).
- Travel bound: `cut_pos_x_max` caps the ram target.
- Blocking M109 preheat before severing (cold filament shatters or resists
  the blade).

Post-check via 0x05: 0x00 = cut OK, 0x02 = nothing to cut (empty slot, not a
failure), anything else is surfaced as cut not confirmed.

Parameters:
- `BOX`: controller address (1-4, optional, default 1)
- `TEMP`: melt temperature in C (optional, default `extrude_temp`)

Requires the cut geometry in `[creality_cfs]`: `cut_switch_pin`,
`pre_cut_pos_x`/`pre_cut_pos_y`, `cut_pos_x` (or `cut_pos_y`),
`cut_velocity`, `cut_pos_x_max`. See `configs/printer.cfg.example`
(reference Hi values: pre-cut 240,130; cut_pos_x 283.5; max 285).

---

### CFS_FLUSH

Purges the old filament through the hotend after a tool change, in capped
cycles with a measuring-wheel clog watchdog.

```
CFS_FLUSH VOLUME=250
CFS_FLUSH LEN=120 VELOCITY=300
```

Total purge length = `LEN=` if given, else
`nozzle_volume/2.4 + (5/12) * VOLUME * flush_multiplier`, else
`flush_default_len` (140). The total is split into per-cycle purges capped at
`flush_cycle_cap` (80 mm): cycle 1 = the cap, the remainder split equally.
Wire-verified breakdowns: 158.75 -> [80, 78.75]; 343.33 -> [80, 65.83 x4];
101.25 -> [80, 21.25].

Each cycle: read the measuring wheel (0x0E), `G1 E` purge, M400, re-read. If
the wheel advanced less than 30 percent of the purged length, the path is
clogging and the flush aborts with a recoverable error. The check is skipped
whenever a wheel read returns None, so a rig without the wheel in the path
never false-aborts. An optional `nozzle_clean_macro` runs once per cycle.
Ends with a 1.5 mm retract.

Every purge is a hotend `G1 E` move, so the blocking M109 melt guard runs
first. This also satisfies mainline Klipper's min_extrude_temp raise, which
the Creality fork deletes but mainline keeps.

Parameters:
- `BOX`: controller address (1-4, optional, default 1; the wheel read target)
- `LEN`: explicit total purge length in mm (optional)
- `VOLUME`: flush volume in mm^3, run through the formula above (optional)
- `VELOCITY`: purge feedrate in mm/min (optional, default `flush_velocity`)
- `TEMP`: melt temperature in C (optional, default `extrude_temp`)

---

### CFS_SET_MODE

Sends a raw CMD_SET_BOX_MODE (0x04) frame. Two wire forms, both
wire-confirmed 2026-06-19:

- Per-slot print mode: supply `TOOL=` to send `[slot_bitmask][0x00]`
  (01 00 / 02 00 / 04 00 / 08 00, keyed to the active slot; locks the slot
  for printing).
- Enter/feed form: supply `MODE=` (and optional `PARAM=`, default 1) to send
  `[MODE][PARAM]`. `MODE=0 PARAM=<slot bitmask>` is the `[00][slot]` enter
  feed mode frame that brackets a tool change.

```
CFS_SET_MODE BOX=1 TOOL=1            # print mode for slot T1 (02 00)
CFS_SET_MODE BOX=1 MODE=0 PARAM=1    # enter feed mode for slot T0 (00 01)
```

Parameters:
- `BOX`: controller address (1-4, required)
- `TOOL`: slot 0-3 (optional; selects the per-slot print-mode form)
- `MODE`, `PARAM`: raw bytes for the enter form (used when TOOL is absent)

CFS_EXTRUDE/CFS_RETRUDE send these frames themselves; this command is a
diagnostic.

---

### CFS_SET_PRELOAD

Configures pre-loading via CMD_SET_PRE_LOADING (0x0D). The payload is
`[mask][phase]`: arm = phase 0x00, disarm = phase 0x01, per-slot re-arm =
phase 0x02 (blocks about 38 s while the controller settles the slot).

```
CFS_SET_PRELOAD BOX=1 MASK=15 ENABLE=1   # arm pre-loading, all 4 slots
CFS_SET_PRELOAD BOX=1 MASK=15 ENABLE=0   # disarm (end of print)
CFS_SET_PRELOAD BOX=1 MASK=2 PHASE=2     # re-arm slot B (blocking ~38 s)
```

Inversion fix (v1.4.0): the pre-v1.4.0 handler passed ENABLE straight through
as the phase byte, so `ENABLE=1` emitted `[mask][0x01]`, the wire DISARM, and
vice versa. `ENABLE=1` now correctly sends the wire ARM (phase 0x00).

The reply STATUS byte is checked: 0x00 = ACK, 0x16 = NAK (the controller did
not finish). Blocking phases get long timeouts automatically; a host that
hangs up mid-phase NAK-wedges the box into its busy state. Note the stock
connect-time self-check is `[00][01]` then `[0f][01]` ONLY; stock never sends
a `[0f][02]` phase at connect (a fabricated one is NAKed and holds the box
active so inserts never latch).

Parameters:
- `BOX`: controller address (1-4, required)
- `MASK`: slot bitmask (0x01=T0, 0x02=T1, 0x04=T2, 0x08=T3, 0x0F=all)
- `ENABLE`: 1 = arm (wire phase 0x00), 0 = disarm (wire phase 0x01)
- `PHASE`: explicit phase byte 0-2 (advanced form, overrides ENABLE)

---

### CFS_ADDR_TABLE

Prints the current address assignment table showing which boxes are online,
their UniIDs, and their current mode (APP or LOADER).

```
CFS_ADDR_TABLE
```

---

## Timeout model

The box paces the choreography by HOLDING replies: a 0x10/0x11 frame is not
ACKed until the mechanical step it commands has finished. The blocking
per-stage reply is the ready mechanism; there is no host status poll. Module
timeouts are therefore sized to the longest observed hold, and every blocking
call inside a choreography is clamped to the remaining wall budget.

| Operation | Timeout | Observed wire behavior |
|-----------|---------|------------------------|
| 0x10 per stage | 15 s | reply held until the step completes: init/finalize ~4.5 s, push ~2 s |
| 0x11 START | 22 s | a real pull replies in ~12-14 s |
| 0x11 FINISH | 13 s | ACK held ~9.6 s while the box reels the filament in |
| 0x08 prep reads | 2 s | fast sensor flag reads |
| 0x0D begin `[00][01]` | 2 s | ACK lands ~0.98 s |
| 0x0D phase 1 `[0f][01]` | 5 s | ACK ~0.07 s |
| 0x0D blocking phases (slot re-arm) | 90 s | blocks ~38 s; a short timeout NAK-wedges the box |
| Load wall budget | 90 s | whole sensor-gated load |
| Unload wall budget | 60 s | whole unload including the switch-clear wait |
| Connect wake probe (0x0A) | 12 s single shot, up to 8 retries | the slave-MCU needs ~9.5 s after the 0xA0 assign; the first 0x0A after quiet legitimately returns None |
| Short queries (0x02/0x03/0x05/0x08/0x0A/0x0E/0x14/0xF0...) | 0.05-1.0 s, with retries | normal request/reply; the all-slot 0x02/0x03 presence read gets a long timeout (~11 s scan) |

The pre-v1.4.0 0.5 s timeout on 0x10/0x11 could never see the held replies,
which is why an unload could never be confirmed on real hardware.

---

## Command Status Summary

| G-code Command | Function code(s) | Status |
|----------------|------------------|--------|
| `CFS_INIT` | 0x0B, 0xA0, 0xA1, 0xA2, 0xA3, then the connect-init burst | Capture-validated on this module |
| `CFS_STATUS` | 0x0A | Wire-confirmed (0x0A corrected from 0x08 in v1.2.0; 0x08 is GET_HARDWARE_STATUS) |
| `CFS_VERSION` | 0x14 | Wire-confirmed |
| `CFS_FW_VERSION` | 0xF0 | Wire-confirmed from capture |
| `CFS_EXTRUDE` | 0x04, 0x0F, 0x08, 0x10, 0x05 | Choreography hardware-validated on the reference implementation; this port not yet exercised on hardware |
| `CFS_RETRUDE` | 0x04, 0x08, 0x11 | Same as CFS_EXTRUDE |
| `CFS_CUT` | 0x05 (read-only post-check; the cut itself is mechanical) | Same as CFS_EXTRUDE |
| `CFS_FLUSH` | 0x0E wheel reads + hotend G1 E | Same as CFS_EXTRUDE; the split formula is wire-verified |
| `CFS_SET_MODE` | 0x04 | Wire-confirmed (both forms) |
| `CFS_SET_PRELOAD` | 0x0D | Wire-confirmed; gcode phase mapping inversion fixed in v1.4.0 |
| `CFS_ADDR_TABLE` | n/a | Local table |
| internal: slot presence | 0x02 READ_MATERIAL, 0x03 READ_REMAIN | Wire-confirmed; used by the connect init and the slot cache (no dedicated g-code) |
| internal: buffer state | 0x0C GET_BUFFER_STATE (buffer node 0x81+) | Wire-confirmed; 8-byte block, all-zero = empty |
| internal: RFID/material label | shares 0x02 | Tag-label byte decode pending a tagged-spool capture |

---

## Complete Creality Firmware Command Inventory

Commands identified from `strings` analysis of `box_wrapper.cpython-39.so`
and `filament_rack_wrapper.cpython-39.so` on the Creality Hi. Function codes
marked wire-confirmed were CRC-verified on the Hi RS-485 wire.

**K1/K1C/K2 caveat:** the K1-family firmware is a CAN build that REMAPS
0x02/0x05/0x08/0x0C. The codes below are the Hi RS-485 numbering; do not
cross-use the CAN binary's numbering on this wire. K1-family compatibility is
UNTESTED.

### Box Commands (box_wrapper.so)

| G-code Command | Function Code | Description |
|----------------|--------------|-------------|
| `BOX_GET_BOX_STATE` | **0x0A** | 4-byte state word, empty request; b3 is the load flag (an earlier revision of this doc had 0x08 here, which is GET_HARDWARE_STATUS) |
| `BOX_GET_VERSION_SN` | 0x14 | 22-byte firmware version + serial number |
| `BOX_GET_RFID` | 0x02 | Shares the READ_MATERIAL func; tag-label decode pending a tagged-spool capture |
| `BOX_GET_REMAIN_LEN` | **0x03** | Slot-bitmask selected; positional 4-byte reply, 0xFF = not-in-mask sentinel (an earlier revision wrongly listed 0x0F, which is the connection-motor action) |
| `BOX_GET_BUFFER_STATE` | **0x0C** | Buffer/feeder node (0x81+) 8-byte block; all-zero = empty |
| `BOX_GET_FILAMENT_SENSOR_STATE` | **0x02** | ASCII per-slot material map `A:unknown;B:none;...`, slot-bitmask selected |
| `BOX_GET_HARDWARE_STATUS` | **0x08** | `[channel]` -> 1 flag byte; 0x01 is the idle value |
| `BOX_SET_BOX_MODE` | 0x04 | `[00][slot]` enter feed mode; `[slot][00]` per-slot print mode |
| `BOX_SET_PRE_LOADING` | 0x0D | `[mask][phase]`; arm 0x00, disarm 0x01, slot re-arm 0x02 |
| `BOX_SET_CURRENT_BOX_IDLE_MODE` | UNKNOWN | Set per-slot idle mode |
| `BOX_SET_TEMP` | UNKNOWN | Set temperature target |
| `BOX_EXTRUDE_MATERIAL` | UNKNOWN | Push filament from box toward extruder |
| `BOX_EXTRUDE_PROCESS` | **0x10** | Sensor-gated load state machine (stages 00/04/05/06/07-03) |
| `BOX_EXTRUDE_2_PROCESS` | UNKNOWN | Secondary extrude process |
| `BOX_EXTRUDER_EXTRUDE` | UNKNOWN | Extrude through extruder gear |
| `BOX_EXTRUDE_ZLIFT` | UNKNOWN | Z-lift during extrude |
| `BOX_EXTRUSION_ALL_MATERIALS` | UNKNOWN | Extrude all loaded materials |
| `BOX_GO_TO_EXTRUDE_POS` | UNKNOWN | Move toolhead to extrude position |
| `BOX_TN_EXTRUDE` | UNKNOWN | Tool-N extrude (channel-specific) |
| `BOX_RETRUDE_MATERIAL` | UNKNOWN | Retract filament into box |
| `BOX_RETRUDE_MATERIAL_WITH_TNN` | UNKNOWN | Retract with channel selector |
| `BOX_RETRUDE_PROCESS` | **0x11** | START/FINISH unload pair, both frames carry the slot bitmask |
| `BOX_CUT_MATERIAL` | UNKNOWN | Cut filament (the observed cut is mechanical, toolhead-driven) |
| `BOX_CUT_POS_DETECT` | UNKNOWN | Detect/calibrate cutter position |
| `BOX_CUT_STATE` | **0x05** | Read-only cut state: 0x00 cut OK, 0x01 transient, 0x02 nothing to cut |
| `BOX_CUT_HALL_ZERO` | UNKNOWN | Zero the cutter hall sensor |
| `BOX_CUT_HALL_TEST` | UNKNOWN | Test the cutter hall sensor |
| `BOX_MOVE_TO_CUT` | UNKNOWN | Move toolhead to cut position |
| `BOX_MATERIAL_FLUSH` | UNKNOWN | Basic filament flush/purge |
| `BOX_MATERIAL_CHANGE_FLUSH` | UNKNOWN | Flush during material change (the flush itself is hotend G1 E, host-side) |
| `BOX_GENERATE_FLUSH_ARRAY` | UNKNOWN | Pre-compute flush schedule |
| `BOX_GET_FLUSH_LEN` | UNKNOWN | Get required flush length |
| `BOX_GET_FLUSH_VELOCITY_TEST` | UNKNOWN | Test flush speed |
| `BOX_SHOW_FLUSH_LIST` | UNKNOWN | Display flush schedule |
| `BOX_CREATE_CONNECT` | 0xA3 | The connect / get-addr-table func on the wire (an earlier guess of 0x01 was wrong) |
| `BOX_UPDATE_CONNECT` | UNKNOWN | Update connection state |
| `BOX_CTRL_CONNECTION_MOTOR_ACTION` | **0x0F** | Feeder motor engage (0x01) / release (0x00); Hi uses 0x0F, not the CAN binary's 0x07 |
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
| `BOX_MEASURING_WHEEL` | **0x0E** | Data `[0x01]`; 4-byte big-endian IEEE-754 float, negative, magnitude grows as filament feeds (decode RESOLVED in v1.4.0) |
| `BOX_SAVE_FAN` | UNKNOWN | Save fan state |
| `BOX_RESTORE_FAN` | UNKNOWN | Restore fan state |
| `BOX_ERROR_CLEAR` | UNKNOWN | Clear error state |
| `BOX_ERROR_RESUME_PROCESS` | UNKNOWN | Resume after error |
| `BOX_TNN_RETRY_PROCESS` | UNKNOWN | Retry TN operation |
| `BOX_TEST_MAKE_ERROR` | UNKNOWN | Inject test error |
| `BOX_SEND_DATA` | UNKNOWN | Generic low-level data send |
| `BOX_NUM_POS` | UNKNOWN | Number/position query |

### Addressing Commands (auto_addr_wrapper.py, full source available)

All addressing frames use STATUS 0x00.

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
       ↓                            ← 0x0C on the buffer node (0x81+) over RS485;
       ↓                              the buffer-empty runout switch is a GPIO line
  [Bowden tube to printer]
       ↓
  [filament cutter]                 ← key841: cut sensor not triggered
       ↓                            ← key854: filament present at cut position
  [Nebula / toolhead extruder]
       ↓
  [hotend]
```

During a LOAD the feed toward the hotend is 100 percent box-motor: the stock
choreography issues no toolhead `G1 E` moves while loading (wire-confirmed
from the stock capture decode). The toolhead extruder only runs during
printing, the single unload pull (`G1 E-15 F360`), and the flush purge. The
buffer absorbs the rate difference between the box motor and the toolhead
extruder during printing.

The cutter is mechanical: the toolhead rams past the right side of travel
into the frame-mounted blade lever, which depresses the blade
(`cut_pos_x: 283.5` past `pre_cut_pos_x: 240` on the reference Hi, see
`configs/printer.cfg.example`). A microswitch or hall sensor
(`cut_switch_pin`) confirms the mechanism; 0x05 CUT_STATE reads the latched
result afterward.

---

## Notes

- All commands use 8N1 at 230400 baud (confirmed from capture).
- STATUS byte: 0xFF for host operational requests, 0x00 for addressing frames
  AND all box replies. In box replies the STATUS byte is also the async event
  channel: 0x00 idle, 0x30 insert/update push, 0x16 busy/active-cal.
- Frame layout `[0xF7][ADDR][LEN][STATUS][FUNC][DATA...][CRC8]` with
  LEN = len(DATA)+3 (counts STATUS, FUNC, DATA and CRC); CRC-8 poly 0x07
  init 0x00 over `frame[2:-1]`.
- Buffer/feeder nodes at 0x81+ answer ONLY 0x0C here. The func-0x11 frames
  seen at 0x81/0x82 on the reference printer are X/Y FOC-servo traffic
  sharing the bus, not CFS retrude. The separate buffer-empty runout switch
  is a GPIO line (see `configs/printer.cfg.example`).
- K1/K1C/K2 compatibility is NOT confirmed: the K1-family firmware is a CAN
  build that remaps 0x02/0x05/0x08/0x0C.
- Mainline Klipper temperature note: mainline keeps the min_extrude_temp
  raise the Creality fork deletes, so the module blocks on M109 before any
  hotend `G1 E` move and enforces its own 170 C floor before any box-motor
  feed toward the hotend (which bypasses Klipper's protection entirely).

For protocol details see `docs/protocol.md`.
For hardware pinout see `docs/hardware.md`.
For the configuration options see `configs/printer.cfg.example`.
