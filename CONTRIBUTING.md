# Contributing to CFS Klipper Integration

Thank you for considering contributing to this project!

---

## How to Contribute

### Reporting Bugs

Please use the GitHub Issues page to report bugs:
https://github.com/gitstonelabs/creality-cfs-klipper/issues

Include the following in your report:

- Klipper version
- CFS hardware model and firmware version
- Steps to reproduce the issue
- Expected vs. actual behavior
- Relevant log output (e.g., from klippy.log)

### Suggesting Features

Open a feature request using the GitHub issue template:
https://github.com/gitstonelabs/creality-cfs-klipper/issues/new?template=feature_request.md

Include:

- A clear description of the feature
- Why it would be useful
- Any proposed implementation ideas

---

## Contributing Code

1. Fork the repository
2. Create a new branch:
   ```bash
   git checkout -b feature/my-feature
````

3.  Make your changes
4.  Add or update tests
5.  Run the test suite:
    ```bash
    pytest tests/ -v
    ```
6.  Run the linter:
    ```bash
    flake8 src/ tests/
    ```
7.  Commit your changes with a clear message
8.  Push to your fork and open a pull request

***

## Coding Standards

*   Follow PEP 8 (Python style guide)
*   Use type hints where appropriate
*   Include docstrings for all public functions and classes
*   Keep functions focused and readable
*   Avoid unnecessary dependencies

***

## Testing Requirements

*   All new features must include tests
*   Maintain ≥80% test coverage
*   Use pytest and pytest-cov
*   Use mock hardware where possible (no physical hardware required for tests)

***

## Areas Where Help Is Needed

*   Capturing RS485 traffic for 0x10 and 0x11 commands
*   Physical hardware testing on Creality K2 Plus, K2 Max, Hi, and K1
*   Validating response field semantics (e.g., GET_BOX_STATE)
*   Improving documentation and examples
*   Expanding test coverage

***

## Code of Conduct

Please review and follow our CODE_OF_CONDUCT.md.

***

## Questions?

Open a discussion at:
<https://github.com/gitstonelabs/creality-cfs-klipper/discussions>

Or comment on an existing issue.

***

Thank you for helping improve the CFS Klipper integration!
