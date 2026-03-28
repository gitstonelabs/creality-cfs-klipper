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

All options with defaults:

```ini
[creality_cfs]
serial_port: /dev/ttyS5
baud: 230400
timeout: 0.1
retry_count: 3
box_count: 4
auto_init: True
```

### `cfs_macros.cfg`

Optional Klipper macro file providing convenience G-code wrappers:

| Macro | Description |
|-------|-------------|
| `CFS_INITIALIZE` | Run auto-addressing sequence with logging |
| `CFS_CHECK_STATUS` | Query state of all CFS boxes |
| `CFS_GET_VERSIONS` | Query firmware version from all boxes |
| `CFS_PRINT_START` | Pre-print init: addressing + status check |
| `CFS_PRINT_END` | Post-print cleanup (placeholder until 0x11 payload is known) |
| `CFS_ENABLE_PRELOAD` | Enable pre-loading on all boxes, all slots |
| `CFS_DISABLE_PRELOAD` | Disable pre-loading on all boxes, all slots |

To use the macros, add this line to `printer.cfg`:

```ini
[include cfs_macros.cfg]
```

Ensure `cfs_macros.cfg` is in the same directory as `printer.cfg` (typically `~/printer_data/config/`).

## See Also

- [Installation guide](../docs/installation.md)
- [G-code command reference](../docs/commands.md)
