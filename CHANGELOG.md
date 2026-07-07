# Changelog

All notable changes to this project will be documented in this file.

This project adheres to [Semantic Versioning](https://semver.org/).

Entries are a historical record and are kept as written. Several decodes that
pre-1.4.0 entries state as "confirmed" were later disproven by CRC-verified
captures: box-state is 0x0A, not 0x08; the 0x10 push reply is a 4-byte
big-endian IEEE-754 measuring-wheel float, not `[motor state 0xC3/0xC4][uint16
position in 0.01mm]`; the 0x11 unload is a held START/FINISH pair gated on the
toolhead filament switch, not a one-shot ACK; and the "protocol identical
across K1/K2/Hi" claim is downgraded to untested (the K1-family firmware is a
CAN build with remapped function codes). See the 1.4.0 entry below and
`docs/protocol.md` for the corrected wire truth.

---

## [Unreleased]

### Changed (v1.4.0 choreography rebuild from the hardware-validated reference stack, 2026-07-05)

- **`creality_cfs.py` LOAD (0x10) rebuilt sensor-gated.** The fixed 5-stage ramp with the position-settle exit is replaced by the validated model: the `0x05` push REPEATS and the `0x06`/`0x07 03` finalize fires only after the toolhead `[filament_switch_sensor]` trips, with the whole cycle RE-ARMED (fresh `[slot] 00 00`) until the switch latches, a per-push wheel-advance watchdog for the box's ~3-push per-arm self-limit (`LOAD_PUSH_MIN_ADVANCE`/`LOAD_PUSH_STALL_LIMIT`), 15 s blocking per-stage reply timeouts (the box HOLDS each reply until the mechanical step completes; this is the real ready mechanism, not a host poll) and a 90 s wall budget. The `0x10` push reply decode is CORRECTED: the payload is a 4-byte big-endian IEEE-754 wheel float (negative, magnitude-monotonic); the old `[motor state 0xC3/0xC4][uint16 0.01mm]` model was a misparse (the "state" byte was the float's exponent byte). `CFS_EXTRUDE` now runs the full choreography (melt guard, `0x04 [00][slot]` feed-mode entry, `0x0F` engage, one-shot `0x08` ping, gated ramp cycles, `0x05` cut check, `0x04 [slot][00]` print mode, `0x0F` release) and takes a REQUIRED `TOOL=<0-3>`.
- **`creality_cfs.py` UNLOAD (0x11) rebuilt to the START/FINISH pair.** `[slot][00]` then `[slot][01]`, BOTH frames carrying the slot bitmask, with ONE interleaved toolhead `G1 E-15 F360` pull between them, `0x08 00/01` sensor prep reads in stock order, and hold-covering timeouts (START 22 s; the FINISH ACK is held ~9.6 s so it gets 13 s -- the old 0.5 s timeouts ALWAYS timed the finish out on real hardware, so an unload could never confirm). Completion gates on the toolhead filament switch CLEARING within a 60 s wall budget (the `0x11` reply status is wire-disproven as a gate and is diagnostic-only). The "buffer node 0x81 single-byte retrude" form was removed as wire-disproven (that traffic is FOC-servo frames sharing the reference printer's bus, not a CFS retrude). Sensorless rigs fall back to box-state corroboration.
- **`creality_cfs.py` GET_BOX_STATE (0x0A) decode corrected.** The request is sent EMPTY (the old param byte is not on the wire) and the reply's `data[0]`/`data[1]` are an OPAQUE per-firmware base (`0x1a20`/`0x1b26`/`0x1c24`/`0x1d21` all observed on identical hardware) that carries no state; the real load flag is `data[3] == 0x02` (feed mode = `0x00`). The frame STATUS byte is surfaced as the async event channel (`0x30` insert push with per-slot phase array, `0x16`+`d3==0x04` busy/cal). `get_box_state()` returns `None` on no response instead of raising.
- **`creality_cfs.py` SET_PRE_LOADING (0x0D) inversion + NAK fix.** Payload generalized to `[mask][phase]`; on the wire ARM is phase `0x00` and DISARM is `0x01`, so the old `ENABLE` pass-through sent the exact opposite (CFS_ENABLE_PRELOAD emitted the wire DISARM). The reply STATUS byte is now checked (`0x00` ACK; `0x16` NAK = controller did not finish) and blocking phases get real timeouts (per-slot re-arm `[slot][02]` blocks ~38 s and gets 90 s) so the host can never hang up mid-phase and NAK-wedge the box into its `0x16/d3=04` state.
- **`creality_cfs.py` connect timing.** After addressing, each box gets a wake-sized 12 s single-shot `0x0A` probe with bounded retries (the box slave-MCU needs ~9.5 s after the `0xA0` assign and the first `0x0A` after quiet legitimately returns `None`; the old 0.05-0.1 s init reads missed the box entirely), then the stock connect-init burst: feed mode, `0x14` version, the TWO-frame pre-load self-check (`[00][01]` + `[0f][01]` with `0x08` reads between -- stock sends NO `[0f][02]`), and the all-slot `0x02`/`0x03` presence read.

### Added (v1.4.0)

- **`CFS_CUT`**: the mechanical cut ram (there is no bus "cut" command; `0x05` only reads the latched result). Safety rails ported from the validated implementation: hard guard on `cut_switch_pin`, zero-travel refusal when the cut position equals the pre-cut position, `cut_pos_x_max` travel bound, blocking M109 preheat, and the `0x05` post-check with the `0x02` nothing-to-cut decode (empty slot, not a failure).
- **`CFS_FLUSH`**: the change flush as a hotend `G1 E` purge loop. Total = `LEN=`, or `nozzle_volume/2.4 + (5/12)*VOLUME*flush_multiplier`, else `flush_default_len`; split at the per-cycle cap (cycle 1 = cap, remainder split equally -- the wire-verified breakdowns 158.75->[80,78.75], 343.33->[80,65.83x4], 101.25->[80,21.25]); per-cycle measuring-wheel under-feed/clog watchdog (<30 percent advance aborts recoverably, skipped when a wheel read is None so wheel-less printers can't false-abort); optional per-cycle `nozzle_clean_macro`; final 1.5 mm retract. Hard bounds on total (600), cycles (10) and per-cycle cap (160).
- **Temperature guards (critical for mainline Klipper)**: mainline KEEPS the `min_extrude_temp` raise the Creality fork deletes, so every hotend `G1 E` move this module issues is preceded by a blocking `M109`; and because the BOX-MOTOR feed bypasses Klipper's cold-extrude protection entirely (it is not an extruder move), the module enforces its own `MIN_EXTRUDE_TEMP` (170 C) floor + `M109` before any feed toward the hotend. `TEMP=` overrides per command; `extrude_temp` (default 220) configures the default.
- **Slot presence reads**: `0x02` READ_MATERIAL (slot-bitmask ASCII map; `none`=empty, `unknown`=inserted-no-tag) and `0x03` READ_REMAIN (positional 4-byte reply with `0xFF` not-in-mask sentinels -- do NOT read the sentinels as filament), plus `0x0C` GET_BUFFER_STATE on the buffer node. `measuring_wheel_mm()` returns the resolved BE-float wheel value (the "unresolved decode" TODO is closed).
- **`get_status()`**: `printer["creality_cfs"]` now resolves in macros (box_count, online map, active_tool, slot cache). The shipped `CFS_PRINT_END` uses `active_tool` instead of blind-retracting four bus addresses.
- New `[creality_cfs]` options (all optional, printer-agnostic): `filament_sensor`, `extrude_temp`, `load_max_bursts`, `load_wall_budget`, `cut_switch_pin`, `pre_cut_pos_x/y`, `cut_pos_x/y`, `cut_velocity`, `cut_pos_x_max`, `nozzle_volume`, `flush_multiplier`, `flush_cycle_cap`, `flush_default_len`, `flush_velocity`, `nozzle_clean_macro`.

### Fixed (v1.4.0 tool-change topology)

- **`configs/cfs_macros.cfg`**: T0..T3 now select the SLOT BITMASK (`TOOL=0..3`) on the single controller at bus address 0x01. The old macros mapped T0..T3 to `BOX=1..4` BUS ADDRESSES, so on real hardware T0 loaded slot B's bitmask-equivalent and T1/T2/T3 addressed absent controllers and no-op'd. Multi-box daisy-chains are documented as a separate axis (`BOX=` selects the controller; a chained second unit is T4..T7 on `BOX=2`). `CFS_PRINT_START`/`CFS_PRINT_END` arm/disarm pre-loading with the corrected wire phases; `CFS_PRINT_END` unloads only the tracked active tool. The `_CFS_TOOL_CHANGE` sequence follows the validated order (optional cut -> unload old -> load new -> optional flush) with opt-in `use_cut`/`use_flush` variables.
- **`configs/printer.cfg.example`**: `box_count` documented as CONTROLLERS (1 for a normal single CFS), not tool slots; K1/K1C/K2 compatibility claims downgraded to untested (the K1-family firmware is a CAN build with remapped function codes); the new choreography options documented.

### Security / hygiene (2026-07-05)

- **`captures/cfs_toolchange_capture_20260520_013844.bin`**: scrubbed the per-unit CFS box serial embedded in the two `0x14` GET_VERSION_SN reply frames (the serial tail replaced with a same-length placeholder; both frame CRC-8s recomputed so the capture still parses). The generic firmware-version prefix is kept.
- Removed the untracked root `BRAND.md` leftover (its tracked home is `docs/STYLE.md`).

### Fixed (v1.4.0 review pass)

- **`CFS_CUT`/`CFS_FLUSH` connection guards**: both new handlers now check `is_connected` up front (in `CFS_CUT`, BEFORE any heat or motion). Without the guard, a disconnected CFS raised a bare RuntimeError out of the first bus read, which mainline routes to `invoke_shutdown` -- and `CFS_CUT` would have completed the mechanical ram first. Found by the Klipper-review pass.
- **`CFS_SET_PRELOAD` single-shot**: the raw preload gcode now sends its `0x0D` frame once (`retries=1`) like every other choreography frame; the default 3 retries on the 90 s blocking per-slot re-arm could hold the gcode mutex for 270 s against a silent box.
- **Tool tracking unified**: `_CFS_TOOL_CHANGE` reads the previous tool from the module's `get_status()` `active_tool` (which also tracks standalone `CFS_EXTRUDE`/`CFS_RETRUDE` calls) instead of only its own macro variable.

### Tests (v1.4.0)

- The suite is re-pointed at the corrected protocol: the pre-v1.4.0 tests locked in the wire-disproven ramp/retrude/preload/state models and would have guarded the bugs. `tests/mock_cfs.py` now answers the full v1.4.0 command set (4-byte box state word with the `data[3]` flag, BE-float wheel words that advance per push, START/FINISH retrude ACKs, presence reads with `0xFF` sentinels); `tests/conftest.py` gives the fake reactor a real advancing clock and stops `lookup_object` from faking optional printer objects.

### Fixed (docs: buffer topology + termination, 2026-06-22)

- **`docs/hardware.md`, `INSTALL.md`**: corrected the filament-buffer wiring model. The earlier "per-segment, one GPIO per box" description (and the v0.x "Confirmed from live capture" note further down this file) was inferred from a SINGLE-box Hi capture and is not confirmed on a real multi-box chain. Replaced it with the confirmed model: a single filament buffer sits at the end of the chain; its state is a plain mechanical switch on pin 2 (referenced to pin 5 GND), NOT carried over RS485, read with ONE host GPIO. A 3-wire USB-RS485 dongle does not break out pin 2, so it must be tapped off a CFS 6-pin connector. The per-segment idea is now flagged as unverified, with a probe procedure for confirming it on multi-box setups. Added a 3.3V-logic caution (do not feed 5V into the buffer GPIO) and a polarity note (invert with `!` if the sensor reads backwards). Softened the 120 Ω termination guidance: most USB-RS485 adapters (e.g. Waveshare) self-terminate, so a resistor is usually unnecessary and should be added only if the bus is flaky; some Pi/Jetson HAT 120 Ω jumpers are for the CAN side. No code change. These pages remain best-current-understanding from single-box Hi captures and will be revised as the open `.so` decode and multi-box validation progress.

### Changed (B1 non-blocking reactor transport, 2026-06-19, v1.3.0)

- **`creality_cfs.py`**: rewrote the serial transport from blocking `pyserial` to a reactor-friendly, non-blocking model so it NEVER blocks the Klipper reactor greenlet (B1 mainline-acceptance blocker). On connect the dedicated port is opened non-blocking (`os.open` `O_NONBLOCK` + raw 8N1 `termios`, `VMIN=0`/`VTIME=0`) and registered with the reactor via `reactor.register_fd(fd, read_callback)`; the port is still OWNED by this module (the target is a portable mainline host with its own dedicated CFS port, not the Hi's shared `serial_485`). The read callback `_handle_readable` does a single non-blocking `os.read`, buffers the bytes, and `_parse_rx` extracts complete frames using the EXISTING geometry (`3 + buf[2]`), which `_dispatch_rx` matches to the in-flight waiter by the `(addr, func)` echo and completes its `reactor.completion`. `_send_command` now writes the request, registers the completion as the pending matcher, and parks the caller in `completion.wait(reactor.monotonic() + timeout, None)` so the reactor keeps servicing the MCU keepalive and every other event during the wait; it returns the same `parse_message()` dict (or `None`) as before. The OS-blocking `serial.read`/`_read_response` and the `reset_input_buffer` that ran on the reactor path were removed; partial reads are handled in the fd callback. A `reactor.mutex()` serializes the half-duplex bus. The deferred auto-init (`register_callback` off `klippy:ready`) is preserved; `klippy:shutdown`/`klippy:disconnect` now quiesce the bus (abort any parked waiter) and unregister the fd cleanly. RS-485 direction is left to an auto-direction adapter by default; opt in to kernel RTS-as-DE via `rts_on_send` (`-1` default = leave UART alone). Documented that `serial_port` is effectively REQUIRED off-Hi (`CFS_DEFAULT_PORT` `/dev/ttyS5` is Hi-specific). Also added a provisional-ACK note to `extrude_process()` `settle_ok`/`finalize_ok`/`complete` (any framed reply counts until a failing-stage counter-example is captured). Public API of every caller (`get_box_state`, `extrude_process` incl. STREAM/SETTLE/FINALIZE, `retrude_process`, `cut_state`, `get_hardware_status`, `measuring_wheel`, `ctrl_connection_motor_action`, `set_box_mode`, `set_box_mode_channel`) and `_send_command` is UNCHANGED (AST-verified). Verify: `python -m py_compile` passes on both 3.14 and 3.9; an offline rx-path harness (full frame / split read / wrong-`(addr,func)` drop / leading-noise resync / bad-LEN resync) passes; behavior on hardware UNVERIFIED (pending a live CFS run on a non-Hi host with a dedicated port). Revert: restore `src/creality_cfs.py.bak_2026-06-19_b1_reactorfd`.

### Fixed (second-pass wire corrections from the 3-color print, 2026-06-19, v1.2.1)

- **`creality_cfs.py`**: two more wire-evidenced corrections from the CRC-clean live 3-color print capture (`reverse-engineering/captures/cfs-re/hi_rs485_3color_print_2026-06-19.json`) and the `cfs_toolchange_reconfirm_2026-06-19.md` cross-check. (A) **SET_BOX_MODE (0x04) per-channel form wired to the slot.** The 0x04 payload has two wire forms: the ENTER form `[mode, param]` = `00 01` that brackets a tool change, and a PER-CHANNEL (print-mode) form `[slot_bitmask, 0x00]` observed `01 00 / 02 00 / 04 00` keyed to the active slot. `cmd_CFS_SET_MODE` now takes an optional `TOOL=<0-3>` (maps to `SLOT_BITMASKS`) that sends the per-channel form via the new `set_box_mode_channel()`; the ENTER form stays available via `MODE`/`PARAM`. (B) **`extrude_process()` load ramp completed.** The STREAM loop only issued `0000/0400/0500` and never the wire's `0600` (SETTLE) and `0703` (FINALIZE, data byte 0x03) stages, so a real load never finished the way stock does. SETTLE then FINALIZE are now issued after the STREAM loop converges or times out, per the 06-09 ramp `0000/0400/0500/0600/0703`. Added `EXTRUDE_SUB_SETTLE=0x06`, `EXTRUDE_SUB_FINALIZE=0x07`, `EXTRUDE_FINALIZE_DATA=0x03`, and `settle_ok`/`finalize_ok`/`complete` keys in the result dict. Verify: `python -m py_compile` passes; behavior unverified on hardware (pending a live CFS tool-change run). Revert: restore `src/creality_cfs.py.bak_2026-06-19_pass2`.

### Fixed (wire-evidenced protocol corrections, 2026-06-19)

- **`creality_cfs.py`**: applied 10 wire-evidenced protocol corrections from the CRC-verified live Hi RS-485 tool-change captures (`reverse-engineering/captures/cfs-re/cfs_func_code_map_2026-06-09.md` + `cfs_toolchange_reconfirm_2026-06-19.md`). `CMD_GET_BOX_STATE` 0x08 -> 0x0A (0x08 is a separate `GET_HARDWARE_STATUS`; the v1.1.0 "0x0A->0x08 fix" was itself wrong); `get_box_state()` now decodes the 0x0A 2-byte state word (lo 0x20=loaded, 0x1f=feeding); `extrude_process()`/`retrude_process()` un-slot-locked via a 1-hot slot bitmask (T0=0x01..T3=0x08) and the retrude payload corrected to `[slot, phase]` with phase 0x00 then 0x01 (buffer-node addr 0x81 takes a single channel byte); added `cut_state()` (0x05), `get_hardware_status()` (0x08), `ctrl_connection_motor_action()` (0x0F engage/release, Hi uses 0x0F not the CAN binary's 0x07), and `measuring_wheel()` (0x0E, raw 4 bytes; numeric decode left as a documented TODO, unresolved tag-vs-float32 encoding); `CMD_CREATE_CONNECT_TODO` guessed-0x01 aliased to `CMD_GET_ADDR_TABLE` (0xA3); the fixed `EXTRUDE_POLL_MAX=8` STREAM loop replaced with a settle-based loop (`EXTRUDE_SETTLE_THRESHOLD` over `EXTRUDE_SETTLE_READS` reads, path-length timeout). Verify: `python -m py_compile` passes; behavior unverified on hardware (pending a live CFS tool-change run). Revert: restore `src/creality_cfs.py.bak_2026-06-19_wirecorrections`.

### Added

- **`NOTICES.md`**: GPL-3.0 license-provenance record. Names the Creality `*.cpython-39.so` CFS wrappers this module replaces, states the clean-room method (live RS-485 capture, protocol decode, on-device open Python), and documents the unanswered GPL-3.0 source requests. Written because the upstream license terms were not honored and a downstream user is entitled to know exactly what they are running and where it came from.

### Changed

- **`README.md`**: link the license section to `NOTICES.md` for the clean-room method and GPL-3.0 provenance.

## [1.1.1] - 2026-06-03

### Added
- `CMD_*_TODO` placeholder constants for the 10 stock `BoxAction.communication_*` methods whose
  function codes still need a live CFS capture to confirm. The names are taken from the
  `box_wrapper.cpython-39.so` symbol table; each is left `None`, except an inferred
  `CMD_CREATE_CONNECT_TODO = 0x01`, so a capture can fill in the byte.

### Documented
- Cross-referenced `creality_cfs.py` against the stock `box_wrapper.cpython-39.so` and the rest of
  the on-board CFS stack. 3 of 5 CFS modules already ship as open Python on the Hi: `auto_addr`,
  `external_material` (RFID reader at addr 0x11, cmd 0x02), and `steer` (the CFS camera module at
  addr 0x41, GET_STATE 0x0A heartbeat; the long-unknown 0x41 device on the bus is the steer/camera,
  not a box).
- The 7 box commands implemented here are confirmed identical to the stock methods; the other 10
  are inventoried as TODO, pending a capture.
- Noted the Hi-side `serial_485`-wired sibling of this driver, the box module, which
  uses the shared transport because `/dev/ttyS5` on the Hi is owned by `serial_485`.

### Maintenance
- Editorial pass over the documentation, the module docstring and comments, the configs, and the
  test descriptions: consistent punctuation and tightened wording, with no change to any code,
  protocol value, hex code, CRC value, pin assignment, or test vector.
- Moved the visual style reference from `BRAND.md` to `docs/STYLE.md`, trimmed to the color palette,
  the Mermaid theme, and the typography note. The logo and mascot-rebrand sections were dropped.

---

## [1.1.0] - 2026-05-20

### Major: Physical Hardware Validation Complete

This release marks the transition from alpha to beta. All core protocol commands
have been confirmed on physical Creality Hi hardware with a real CFS box. The two
previously stubbed commands (0x10/0x11) are now fully implemented from live RS485
traffic captures. The module is confirmed working on mainline Klipper over a
USB-RS485 adapter, completely independent of Creality hardware and firmware.

### Added
- `CMD_EXTRUDE_PROCESS (0x10)` fully implemented from live RS485 capture
  - Three sub-commands confirmed from capture:
    - `0x02/0x00`: init/start extrusion motor
    - `0x02/0x04`: status poll (ACK-only response)
    - `0x02/0x05`: streaming position feedback (repeating)
  - Response format confirmed: 1-byte motor state + 2-byte uint16 position
    - Position units: 0.01mm (divide by 100 for mm)
    - Motor state `0xC3` = accelerating (wrap-around phase, position not valid)
    - Motor state `0xC4` = at speed (position valid)
  - Filament path length confirmed: ~398-400mm from CFS motor to toolhead sensor
  - Position profile per tool change: ~149mm → ~338mm → ~400mm (stable)
- `CMD_RETRUDE_PROCESS (0x11)` fully implemented from live RS485 capture
  - Payload confirmed: `0x02 0x01` (sub-command + mode flag)
  - One-shot command with ACK-only response, no streaming feedback
- `CMD_VERSION_INFO (0xF0)` new command decoded from capture
  - Returns ASCII firmware version string
  - CFS box example: `cfs0_050_G32-cfs0_000_113`
  - Motor controller example: `mot2_023_C30-mot2_002_071`
- Three new G-code commands:
  - `CFS_EXTRUDE BOX=N`: run full extrude sequence, reports final position
  - `CFS_RETRUDE BOX=N`: run retract sequence
  - `CFS_FW_VERSION BOX=N`: query 0xF0 firmware version string
- Box state constants confirmed from capture:
  - `BOX_STATE_IDLE = 0x0F`: standby/normal polling state
  - `BOX_STATE_BUSY = 0x00`: transitioning/executing command
  - `BOX_STATE_ACTIVE = 0x02`: active during retract sequence
- `FILAMENT_PATH_LENGTH_MM = 400.0` documented as confirmed physical constant
- Raw capture files added to `captures/`:
  - `cfs_toolchange_capture_20260520_013844.bin`: T0→T1→T2→T3 tool change sequence
  - `buffer_test_20260520_022430.bin`: buffer switch trigger + retract sequence

### Fixed
- **Critical: `CMD_GET_BOX_STATE` function code corrected from `0x0A` to `0x08`**
  - `0x0A` is `CMD_LOADER_TO_APP`. Every state poll was accidentally triggering
    a device reboot cycle on the CFS box. This bug existed since v0.1.0-alpha.
  - Correct code `0x08` confirmed from live capture
  - Frame format confirmed: 6-byte frame, state byte in last position
  - Short frames (length=4, no data) do not carry a separate CRC byte;
    the state value occupies the final byte position
- **`STATUS=0xFF` for operational commands confirmed from capture**
  - The uncertainty noted in v0.2.0-alpha is now fully resolved
  - All operational command requests use `STATUS=0xFF`
  - All responses (operational and addressing) use `STATUS=0x00`
  - `get_box_state()` updated to pass correct `param` byte and parse 1-byte state

### Confirmed from live capture
- Buffer switch signals (pins 2/3 on 6-pin connector) are direct GPIO lines,
  NOT RS485 commands. No RS485 traffic is generated by buffer state changes.
  Buffer state monitoring uses Klipper native `[filament_switch_sensor]` on a GPIO pin.
- 6-pin CFS daisy-chain connector pinout (confirmed with multimeter on Creality Hi):
  - Pin 1 (red):    RS485-A, ~1.75V idle
  - Pin 2 (white):  Buffer switch 1, 0.01V idle / 3.3V triggered
  - Pin 3 (black):  Buffer switch 2, 3.3V idle / 0.01V triggered (inverted pair)
  - Pin 4 (yellow): 24V power
  - Pin 5 (green):  GND
  - Pin 6 (blue):   RS485-B, ~1.74V idle
- Daisy-chain topology confirmed:
  `Printer → CFS1 → CFS2 → CFS3 → CFS4 → Filament buffer (terminator)`
  Buffer switch signals are per-segment, not bus-wide
- CFS firmware version: `cfs0_050_G32-cfs0_000_113` (hardware under test)
- Motor controller firmware version: `mot2_023_C30-mot2_002_071`
- USB-RS485 adapter (CH341 chip) confirmed working as drop-in replacement
  for mainboard RS485 port on mainline Klipper (Jetson Orin Nano, Ubuntu 22.04)

### Hardware findings documented
- RS485 transceiver failure mode: ESD or hot-plugging the CFS connector can
  damage the mainboard RS485 transceiver AND connected peripheral boards
  (Y-axis closed-loop motor controller, RFID reader board). Always power off
  before connecting or disconnecting CFS cables.
- After replacing a CFS board, clear the `[auto_addr] mb_addr_table_uniids`
  entry in SAVE_CONFIG to force fresh UniID discovery. The new board has a
  different UniID and will not be recognized until the table is cleared.
- If RS485 lines drag to ~0.01V on all four wires: check every device on the
  bus for shorts before replacing the mainboard. Damaged peripherals (Y-motor
  board, RFID board) will kill the new mainboard's transceiver too.

### GPL compliance update
- Formal source code request submitted to Creality twice for compiled `.so`
  binaries distributed under GPL-3.0 in `CrealityOfficial/Hi_Klipper`
- Creality initially claimed the Hi is "not open source". Responded with
  citations of their own open-source commitments and the specific GPL-3.0
  license on their repository
- Awaiting second response. Escalation to Software Freedom Conservancy
  planned if source code is not provided

---

## [0.2.0-alpha] - 2026-04-26

### Added
- `get_rfid()` method stub for CMD_GET_RFID (0x02)
- Filament rack command inventory in `commands.md`
- Full CFS command inventory in `commands.md`: 60+ commands from strings analysis
- Physical filament path diagram in `commands.md`
- Known error code table in `commands.md`
- Serial transport layer details in `protocol.md`
- `notifications_addr` and `notifications_cmd` constants documented

### Changed
- Clarified STATUS byte uncertainty for operational commands
- Improved docstrings on `extrude_process()` and `retrude_process()` stubs
- Updated `_discover_slaves()` comment
- Added `CMD_GET_RFID` to CMD_TIMEOUTS dict

### Fixed
- Variable name `attempt` reused in nested loops, renamed outer loop variables to `_`

### Research
- Confirmed 230400 baud rate from two independent sources
- Confirmed protocol is identical across K1/K2/Hi CFS hardware
- Confirmed `CrealityOfficial/Hi_Klipper` is GPL-3.0 on GitHub but .pyx source absent
- Identified related projects: fake-name/cfs-reverse-engineering, ityshchenko/klipper-cfs

---

## [0.1.0-alpha] - 2026-03-27

### Added
- Initial repository structure with src/, tests/, docs/, tools/, and configs/
- `creality_cfs.py` Klipper module with 9 implemented commands:
  - CMD_LOADER_TO_APP (0x0B)
  - CMD_GET_SLAVE_INFO (0xA1)
  - CMD_SET_SLAVE_ADDR (0xA0)
  - CMD_ONLINE_CHECK (0xA2)
  - CMD_GET_ADDR_TABLE (0xA3)
  - CMD_SET_BOX_MODE (0x04)
  - CMD_GET_BOX_STATE (0x0A) ← incorrect code, fixed in v1.1.0
  - CMD_SET_PRE_LOADING (0x0D)
  - CMD_GET_VERSION_SN (0x14)
- Stubbed commands: CMD_EXTRUDE_PROCESS (0x10), CMD_RETRUDE_PROCESS (0x11)
- Full pytest test suite with >80% coverage
- RS485 traffic capture tool (tools/capture_cfs_traffic.py)
- Documentation: README, CONTRIBUTING, protocol.md, commands.md,
  hardware.md, troubleshooting.md
- GitHub issue templates and CI workflow

### Known Limitations
- 0x10 and 0x11 payloads not yet implemented (resolved in v1.1.0)
- No physical hardware validation yet (resolved in v1.1.0)

---

## [Unreleased]

### Planned for v1.2.0
- T0/T1/T2/T3 tool change macro set replacing box_wrapper.so
- Full automated multi-material tool change on mainline Klipper
- Validated on BTT Octopus + Jetson Orin Nano host
- Buffer GPIO integration example config
- GET_RFID (0x02) response format from capture
- GET_REMAIN_LEN (0x0F) implementation from capture
- Filament rack command function codes from capture