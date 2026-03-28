## Summary

<!-- Briefly describe what this PR does and why. -->

## Type of change

- [ ] Bug fix
- [ ] New feature / command implementation
- [ ] Protocol documentation update
- [ ] Test improvement
- [ ] Documentation / config update
- [ ] Other: ___

## Related issues

Closes #

## Changes

<!-- List the key changes made. -->

## Testing

- [ ] All existing tests pass (`pytest tests/ -v`)
- [ ] New tests added for changed code
- [ ] Coverage remains ≥ 80% (`pytest --cov=src/creality_cfs --cov-fail-under=80 tests/`)
- [ ] Tested on real hardware (if applicable — describe setup below)

**Hardware test setup (if applicable):**

```
Printer model:
CFS firmware (from CFS_VERSION):
RS485 port:
Klipper version:
```

## Protocol evidence (for command payload PRs)

<!-- If this PR implements or corrects a command payload, attach or paste the raw RS485 capture frames here. -->

```
# Example captured frame (hex):
# f7 01 05 ff 10 XX YY ZZ CRC
```

## Checklist

- [ ] Code follows [PEP 8](https://peps.python.org/pep-0008/)
- [ ] Docstrings added/updated for all changed public methods
- [ ] Confidence annotations updated if protocol elements changed
- [ ] `CHANGELOG.md` entry added under `[Unreleased]`
- [ ] No new magic numbers (use named constants)
