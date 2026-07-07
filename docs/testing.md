# CFS Test Suite

Full pytest test suite for `creality_cfs.py`, the Klipper extra module
for the Creality Filament System (CFS) RS485 protocol.

No physical hardware is required.  All tests use `MockCFSHardware` to simulate
RS485 responses.

---

## Prerequisites

```
pip install pytest pytest-cov pyserial
```

---

## Running Tests

### All tests
```bash
pytest tests/ -v
```

### With coverage report
```bash
pytest --cov=creality_cfs --cov-report=html --cov-report=term-missing tests/
# Open htmlcov/index.html for the full report
```

### Coverage with fail threshold
```bash
pytest --cov=creality_cfs --cov-fail-under=80 tests/
```

### Specific test file
```bash
pytest tests/test_crc.py -v
pytest tests/test_messages.py -v
pytest tests/test_commands.py -v
pytest tests/test_integration.py -v
pytest tests/test_errors.py -v
pytest tests/test_stubs.py -v
pytest tests/test_transport.py -v
pytest tests/test_gcode_handlers.py -v
```

### Exclude slow performance tests
```bash
pytest tests/ -m "not slow"
```

### Run only integration tests
```bash
pytest tests/ -m integration
```

---

## Test Organization

| File | Category | Tests | Purpose |
|---|---|---|---|
| `test_crc.py` | unit | 15 | CRC-8/SMBUS algorithm: 16 vectors, edge cases, SMBUS check value, performance |
| `test_messages.py` | unit | 43 | `build_message()` and `parse_message()`: format, captured frames, round-trip |
| `test_commands.py` | unit | 53 | Per-command message format, success/failure paths, boundary values |
| `test_integration.py` | integration | 22 | Full workflows: init, polling, version query, address allocation |
| `test_errors.py` | integration | 21 | Timeouts, retries, CRC errors, malformed frames, serial exceptions |
| `test_stubs.py` | unit | 22 | 0x10/0x11 choreography: sensor-gated load loop, START/FINISH unload pair, wheel-float decode, watchdogs |
| `test_transport.py` | unit | 52 | Non-blocking reactor serial transport: fd callback, frame resync, waiter matching |
| `test_gcode_handlers.py` | unit | 37 | `cmd_CFS_*` G-code handlers against a mocked `gcmd` |
| `mock_cfs.py` | helper | n/a | `MockCFSHardware`: CRC-validating simulator answering the full v1.4.0 command set |

---

## Coverage Target

Target: **>80%** of `creality_cfs.py`

The following code paths are intentionally excluded from coverage (see
`.coveragerc` `exclude_lines`):
- `load_config()`: requires a live Klipper config object
- The `register_event_handler` registration lines: require a live klippy event
  system

G-code handlers (`cmd_CFS_*`) are covered by `test_gcode_handlers.py` against a
mocked `gcmd`; `extrude_process()` / `retrude_process()` are fully implemented
since v1.4.0 and covered by `test_stubs.py` (the filename is historical, from
when 0x10/0x11 were stubs).

---

## CRC Test Vectors

All 16 test vectors are derived from RS485 frames captured with `interceptty`
during live multi-color printing on a Creality K2 Plus.

Source: `klipper-cfs/tests/test_structures.py` (10 TX frames + 6 RX frames)

The vectors confirm:
- Algorithm: CRC-8/SMBUS (poly=0x07, init=0x00, no reflection, no final XOR)
- Scope: `msg[2:-1]` (LENGTH byte through last DATA byte, excludes HEAD+ADDR+CRC)
- Standard SMBUS check value: `crc8_cfs(b"123456789") == 0xF4`

They validate the CRC algorithm and frame geometry only. They say nothing
about function-code identity across printer families; the K1-family firmware
is a CAN build that remaps several function codes, so K1/K1C/K2 compatibility
remains untested.

---

## MockCFSHardware

`tests/mock_cfs.py` provides a complete CFS hardware simulator:

```python
from tests.mock_cfs import MockCFSHardware

hw = MockCFSHardware(box_count=4)

# Process a raw frame and get response bytes
response = hw.process_message(raw_frame_bytes)

# Inject errors
hw.inject_error(MockCFSHardware.ERROR_TIMEOUT, on_command=0x0A)
hw.inject_error(MockCFSHardware.ERROR_CRC, on_command=0x14, after_n=2)

# Inspect received messages
hw.assert_command_received(0x0A, times=4)
funcs = hw.get_received_funcs()  # [0x0B, 0xA1, 0xA0, 0xA2, 0xA3, ...]

# Reset state
hw.reset()
```

Supported error types:
- `ERROR_TIMEOUT`: returns None (no response)
- `ERROR_CRC`: corrupts the CRC byte of the response
- `ERROR_TRUNCATED`: truncates the response to 3 bytes
- `ERROR_GARBAGE`: returns `b'\xAA\xBB\xCC\xDD\xEE'`
- `ERROR_NACK`: (reserved for future use)

---

## Known Gaps

### Hardware confirmation of the v1.4.0 choreography port

The 0x10 load and 0x11 unload choreography implemented here was
hardware-validated on the reference implementation this module ports from
(same wire protocol, Creality Hi + CFS v1). This module's port of it is
wire-faithful and fully covered by the mock-based suite (`test_stubs.py`),
but it has not yet been exercised on hardware itself. A live CFS load/unload
run on this module is the remaining validation step.

---

## Adding New Tests

1. Place test files in `tests/` following the `test_*.py` naming convention.
2. Import the module under test with the `sys.path.insert` pattern used in
   existing files (no Klipper environment required).
3. Use `make_wired_controller(hw, ...)` from `conftest.py` for integration
   tests that need a full serial transport.
4. Add a docstring to every test method explaining what it tests and why.
5. Use `pytest.mark.parametrize` for boundary value and multi-case tests.

---

## CI/CD

A GitHub Actions workflow is provided at `.github/workflows/ci.yml`.
It runs on push and pull_request, tests Python 3.9/3.10/3.11, and fails
if coverage drops below 80%.

```bash
# Equivalent local command:
pytest --cov=creality_cfs --cov-fail-under=80 -m "not slow" tests/
```
