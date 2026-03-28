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

### pyserial Not Installed

**Symptom:**  
Error when starting Klipper or running tests:

```

ModuleNotFoundError: No module named 'serial'

````

**Solution:**

Install pyserial in your Klipper environment:

```bash
~/klippy-env/bin/pip install pyserial
````

Then restart Klipper.

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
*   Try querying known addresses directly:
        CFS_STATUS ADDR=1

***

## Command Errors

### NotImplementedError for 0x10 or 0x11

**Symptom:**  
Error message when calling filament load/unload commands.

**Explanation:**  
These commands are defined but not yet implemented. The payload structure is unknown and must be captured from live RS485 traffic.

**Suggested Fixes:**

*   Use the capture tool:
    ```bash
    python3 tools/capture_cfs_traffic.py --port /dev/ttyUSB0 --baud 230400
    ```
*   Trigger a T0–T3 tool change on the printer to capture 0x10/0x11 traffic.
*   Share findings via GitHub Issues or Discussions.

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