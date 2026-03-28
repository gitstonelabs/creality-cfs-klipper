# CFS Test Suite

Comprehensive pytest test suite for `creality_cfs.py` — the Klipper extra module
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
| `test_crc.py` | unit | 30+ | CRC-8/SMBUS algorithm: 16 vectors, edge cases, SMBUS check value, performance |
| `test_messages.py` | unit | 40+ | `build_message()` and `parse_message()`: format, captured frames, round-trip |
| `test_commands.py` | unit | 45+ | Per-command message format, success/failure paths, boundary values |
| `test_integration.py` | integration | 25+ | Full workflows: init, polling, version query, address allocation |
| `test_errors.py` | integration | 20+ | Timeouts, retries, CRC errors, malformed frames, serial exceptions |
| `test_stubs.py` | unit | 12 | NotImplementedError for 0x10/0x11, error message content |
| `mock_cfs.py` | helper | — | `MockCFSHardware`: CRC-validating simulator for all 9 commands |

---

## Coverage Target

Target: **>80%** of `creality_cfs.py`

The following code paths are intentionally excluded from coverage:
- `load_config()` — requires a live Klipper config object
- G-code handlers (`cmd_CFS_*`) — require `gcmd` mock; covered by integration plan
- `extrude_process()` / `retrude_process()` — raise NotImplementedError (no payload)

The `raise NotImplementedError` lines in stubs are excluded via `.coveragerc`
`exclude_lines` because they cannot be exercised without knowing the payload.

---

## CRC Test Vectors

All 16 test vectors are derived from RS485 frames captured with `interceptty`
during live multi-color printing on a Creality K2 Plus.

Source: `klipper-cfs/tests/test_structures.py` (10 TX frames + 6 RX frames)

The vectors confirm:
- Algorithm: CRC-8/SMBUS (poly=0x07, init=0x00, no reflection, no final XOR)
- Scope: `msg[2:-1]` (LENGTH byte through last DATA byte, excludes HEAD+ADDR+CRC)
- Standard SMBUS check value: `crc8_cfs(b"123456789") == 0xF4`

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
- `ERROR_TIMEOUT` — returns None (no response)
- `ERROR_CRC` — corrupts the CRC byte of the response
- `ERROR_TRUNCATED` — truncates the response to 3 bytes
- `ERROR_GARBAGE` — returns `b'\xAA\xBB\xCC\xDD\xEE'`
- `ERROR_NACK` — (reserved for future use)

---

## Known Gaps

### 0x10 CMD_EXTRUDE_PROCESS and 0x11 CMD_RETRUDE_PROCESS

These commands are **not testable** because their payload format is locked inside
the Creality `.so` binary and was not recoverable during reverse engineering.

Both methods raise `NotImplementedError` with a message directing you to capture
RS485 traffic on `/dev/ttyS5` during a T0-T3 tool-change.

The stubs are tested in `test_stubs.py` to confirm they raise correctly with
a helpful error message.

### G-code handlers

`cmd_CFS_INIT`, `cmd_CFS_STATUS`, `cmd_CFS_VERSION`, `cmd_CFS_SET_MODE`,
`cmd_CFS_SET_PRELOAD`, and `cmd_CFS_ADDR_TABLE` require a `gcmd` mock
matching the Klipper GCodeCommand interface.  These are excluded from this
suite but can be added by mocking `gcmd.get_int`, `gcmd.respond_info`, and
`gcmd.error`.

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
