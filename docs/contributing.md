# Developer Contribution Guide

This document provides detailed guidance for developers who want to contribute to the CFS Klipper integration project.

---

## Getting Started

1. Fork the repository on GitHub: https://github.com/gitstonelabs/creality-cfs-klipper
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/creality-cfs-klipper.git
   cd creality-cfs-klipper
````

3.  Add the upstream repository:
    ```bash
    git remote add upstream https://github.com/gitstonelabs/creality-cfs-klipper.git
    ```

***

## Development Setup

1.  Create a virtual environment:
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

2.  Install development dependencies:
    ```bash
    pip install -r requirements-dev.txt
    ```

3.  Run the test suite:
    ```bash
    pytest tests/ -v
    ```

4.  Run with coverage:
    ```bash
    pytest --cov=src/creality_cfs --cov-report=html tests/
    ```

5.  Run the linter:
    ```bash
    flake8 src/ tests/
    ```

***

## Coding Standards

*   Follow PEP 8 (Python style guide)
*   Use type hints where appropriate
*   Include docstrings for all public functions and classes
*   Keep functions focused and readable
*   Avoid unnecessary dependencies

***

## Testing Guidelines

*   All new features must include tests
*   Maintain ≥80% test coverage
*   Use pytest and pytest-cov
*   Use mock hardware where possible (no physical hardware required for tests)

***

## Submitting a Pull Request

1.  Create a new branch:
    ```bash
    git checkout -b feature/my-feature
    ```

2.  Make your changes and commit:
    ```bash
    git add .
    git commit -m "feat: Add support for XYZ command"
    ```

3.  Push your branch:
    ```bash
    git push origin feature/my-feature
    ```

4.  Open a pull request on GitHub with a clear description of your changes.

***

## Areas Where Help Is Needed

*   Capturing RS485 traffic for 0x10 and 0x11 commands
*   Physical hardware testing on Creality K2 Plus, K2 Max, Hi, and K1
*   Validating response field semantics (e.g., GET\_BOX\_STATE)
*   Improving documentation and examples
*   Expanding test coverage

***

## Code of Conduct

Please review and follow our CODE\_OF\_CONDUCT.md.

***

## Questions?

Open a discussion at:  
<https://github.com/gitstonelabs/creality-cfs-klipper/discussions>

Or comment on an existing issue.

***

Thank you for helping improve the CFS Klipper integration!
