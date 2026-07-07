# Troubleshooting Guide

This guide provides solutions to common issues encountered when using the CFS Klipper integration.

---

## General Setup Issues

### Module Not Found

**Symptom:**  
Klipper fails to start with an error like:

```

Unable to load module 'creality_cfs'

```

**Solution:**

- Ensure creality_cfs.py is located in your Klipper extras directory:
```

\~/klipper/klippy/extras/creality_cfs.py

    - Check for typos in the filename or printer.cfg section.
    - Restart Klipper after making changes:

sudo systemctl restart klipper

```

---

### ModuleNotFoundError: No module named 'serial'

**Symptom:**  
Error when starting Klipper:

```

ModuleNotFoundError: No module named 'serial'

```

**Solution:**

Since v1.3.0 the module does not use pyserial at all; it has its own non-blocking
reactor serial transport. This error means you are running a pre-1.3.0 copy of
`creality_cfs.py`. Replace it with the current version from this repo, then
restart Klipper.

***

## Communication Problems

### No Response from CFS

**Possible Causes:**

*   Incorrect serial port (e.g., /dev/ttyS5 vs /dev/ttyUSB0)
*   CFS not powered (check 24V input)
*   RS485 wiring issue (check A/B polarity and ground)
*   Baud rate mismatch (must be 230400)

**Suggested Fixes:**

*   Confirm the correct serial port using:
    ```bash
    ls /dev/tty*
    ```
*   Verify 24V power and ground are connected to the CFS.
*   Double-check RS485-A and RS485-B wiring.
*   Ensure no other device is using the same serial port.

***

### CRC Errors

**Symptom:**  
Log shows CRC mismatch or invalid CRC.

**Possible Causes:**

*   Electrical noise on RS485 line
*   Incorrect CRC implementation
*   Baud rate drift or unstable adapter

**Suggested Fixes:**

*   Use shielded twisted-pair cable for RS485.
*   Add 120Ω termination resistor if needed.
*   Use a high-quality USB-to-RS485 adapter (e.g., FTDI-based).

***

## Auto-Addressing Issues

### Boxes Not Found

**Symptom:**  
CFS_INIT reports “0 boxes found”.

**Possible Causes:**

*   CFS boxes not powered or connected
*   Broadcast messages not reaching devices
*   Boxes already have assigned addresses

**Suggested Fixes:**

*   Power cycle the CFS units.
*   Check RS485 wiring and ensure common ground.
*   Note the box slave-MCU needs roughly 9.5 s to wake after addressing; the module
    retries the first state probe, but run CFS_INIT again if a box stays offline.
*   Try querying known addresses directly:
        CFS_STATUS BOX=1

***

## Command Errors

### CFS_EXTRUDE / CFS_RETRUDE fail or time out

**Explanation:**  
The 0x10 load and 0x11 unload are fully implemented (since v1.1.0, rebuilt in v1.4.0
to the hardware-validated choreography). Both are gated on the TOOLHEAD filament
switch: the load feeds until the switch trips, the unload completes when it clears.
The box also HOLDS each stage reply until the mechanical step finishes (up to ~10 s),
so long per-stage waits are normal.

**Suggested Fixes:**

*   Configure `filament_sensor:` in `[creality_cfs]` to your toolhead
    `[filament_switch_sensor]` name and verify the switch actually toggles
    (QUERY_FILAMENT_SENSOR) when filament passes it.
*   Check the hotend heats: every load/unload blocks on M109 first
    (`extrude_temp`, or TEMP= on the command).
*   If a load times out with the switch never tripping, check for a jam at the
    4-way splitter or an uncut filament tip.
*   If it still fails, capture the traffic and open an issue:
    ```bash
    python3 tools/capture_cfs_traffic.py --port /dev/ttyUSB0 --baud 230400
    ```

***

## Testing Issues

### Tests Fail to Run

**Symptom:**  
pytest fails with import errors or missing dependencies.

**Suggested Fixes:**

*   Install development dependencies:
    ```bash
    pip install -r requirements-dev.txt
    ```
*   Ensure you are in the project root directory when running tests:
    ```bash
    pytest tests/ -v
    ```

***

## Still Need Help?

*   Check open issues: <https://github.com/gitstonelabs/creality-cfs-klipper/issues>
*   Ask a question: <https://github.com/gitstonelabs/creality-cfs-klipper/discussions>
*   Open a new issue with logs and configuration details