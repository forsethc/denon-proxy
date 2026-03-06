# Tests

This project uses **pytest** (with `pytest-asyncio` for async tests and `pytest-cov` for coverage).

## Install test dependencies

After installing runtime deps from `requirements.txt`:

```bash
pip install -r tests/requirements.txt
```

## Running tests

From the project root:

```bash
pytest
```

Run a specific test file or directory:

```bash
pytest tests/unit/test_avr_state_volume.py      # single file
pytest tests/unit                               # all unit tests
```

Run tests with coverage:

```bash
pytest --cov                                 # text summary + missing lines
# or override the report format, e.g. HTML:
pytest --cov --cov-report=html               # then open htmlcov/index.html
```

## Test classification

Tests are classified and stored by type. Use `pytest -m <marker>` to run by class.

| Class        | Directory       | Marker         | Description |
|-------------|-----------------|----------------|-------------|
| **Unit**    | `tests/unit/`   | `pytest -m unit` | Single component, mocked or pure dependencies. Fast, no real servers. |
| **Integration** | `tests/integration/` | `pytest -m integration` | Multiple real components together (e.g. proxy + VirtualAVR, discovery server only). Not the full app. |
| **E2E**     | `tests/e2e/`    | `pytest -m e2e` | Full application stack, production-like (e.g. DenonProxyServer + discovery). |

## Examples

```bash
pytest -m unit
pytest -m integration
pytest -m e2e
pytest tests/unit tests/integration   # skip e2e
```
