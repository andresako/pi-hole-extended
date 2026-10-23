# Development and tests

The supported test baseline is Home Assistant 2025.1.0, matching the minimum
declared in `hacs.json`, on Python 3.12. The exact local interpreter and all
Python dependencies are pinned in `.python-version`, `pyproject.toml`, and
`uv.lock`.

Install the environment and run the API tests:

```sh
uv python install
uv sync --locked --prerelease allow
uv run --locked --prerelease allow pytest tests/test_api.py
```

The test configuration disables sockets except for loopback. Tests use a local
HTTP server and synthetic `example.test` query records; no Pi-hole, Home
Assistant installation, private DNS data, or credentials are needed.

The local Home Assistant runtime recipe for future visual work belongs to the
P09 work package; backend tests do not claim to validate the frontend.
