# local-board

A dependency-free SQLite project board with CLI, HTTP and MCP transports.

## Tests

```sh
python -m pip install -e '.[test]'
pytest tests/unit tests/integration
```

Coverage is branch-aware, lists missing lines, and enforces a 75% minimum. Browser tests are isolated because they download a browser:

```sh
python -m pip install -e '.[test,e2e]'
playwright install --with-deps chromium
pytest tests/e2e -m browser
```

GitHub Actions runs unit and integration tests on Python 3.10 through 3.13,
publishes the Python 3.13 coverage XML as an artifact, and runs agent/browser
E2E scenarios in a separate Chromium job.
