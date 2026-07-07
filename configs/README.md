# Configuration Files

This directory contains example configuration files for the Creality Filament System (CFS) Klipper integration.

## Files

### `printer.cfg.example`

A complete example `[creality_cfs]` configuration block for `printer.cfg`. Copy the relevant section into your own `printer.cfg`.

Minimum required configuration:

```ini
[creality_cfs]
serial_port: /dev/ttyS5
```

Core options for the normal single-CFS setup:

```ini
[creality_cfs]
serial_port: /dev/ttyS5
baud: 230400
timeout: 0.1
retry_count: 3
box_count: 1                        # CONTROLLERS in the daisy chain, not slots;
                                    # one CFS (four slots) = 1
auto_init: True
filament_sensor: filament_sensor    # toolhead switch that gates loads/unloads
extrude_temp: 220                   # M109 melt guard before any filament move
```

The optional cutter (`cut_switch_pin`, `pre_cut_pos_x/y`, `cut_pos_x`,
`cut_pos_x_max`, `cut_velocity`), flush (`nozzle_volume`, `flush_multiplier`,
`flush_cycle_cap`, `flush_default_len`, `flush_velocity`,
`nozzle_clean_macro`), and load-tuning (`load_max_bursts`,
`load_wall_budget`) options are documented inline in `printer.cfg.example`.

### `cfs_macros.cfg`

Optional Klipper macro file providing convenience G-code wrappers:

| Macro | Description |
|-------|-------------|
| `CFS_INITIALIZE` | Run auto-addressing sequence with logging |
| `CFS_CHECK_STATUS` | Query state of all CFS boxes |
| `CFS_GET_VERSIONS` | Query firmware version from all boxes |
| `CFS_PRINT_START` | Pre-print init: status check + arm pre-loading (wire phase 0x00) |
| `CFS_PRINT_END` | Post-print cleanup: unload the tracked active tool, disarm pre-loading |
| `CFS_ENABLE_PRELOAD` | Arm pre-loading, all slots |
| `CFS_DISABLE_PRELOAD` | Disarm pre-loading, all slots |
| `_CFS_TOOL_CHANGE` | Shared tool-change sequence (optional cut, unload old, load new, optional flush) |
| `T0` / `T1` / `T2` / `T3` | Select slot 0-3 on the controller at `BOX=1`. Tools are SLOTS on one controller (bitmask 0x01/0x02/0x04/0x08), not bus addresses; a daisy-chained second box would carry T4-T7 on `BOX=2` |

To use the macros, add this line to `printer.cfg`:

```ini
[include cfs_macros.cfg]
```

Ensure `cfs_macros.cfg` is in the same directory as `printer.cfg` (typically `~/printer_data/config/`).

## See Also

- [Installation guide](../docs/installation.md)
- [G-code command reference](../docs/commands.md)
