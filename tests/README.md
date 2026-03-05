# Test classification

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
