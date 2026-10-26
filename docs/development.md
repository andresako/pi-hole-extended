# Development and tests

The supported test baseline is Home Assistant 2025.1.0, matching the minimum
declared in `hacs.json`, on Python 3.12. The exact local interpreter and all
Python dependencies are pinned in `.python-version`, `pyproject.toml`, and
`uv.lock`.

Install the environment:

```sh
uv python install
uv sync --locked --prerelease allow
```

Run the Python test suite, or a single file:

```sh
uv run --locked --prerelease allow pytest
uv run --locked --prerelease allow pytest tests/test_api.py
```

Run the activity card tests (Node.js; pass the file, not the directory):

```sh
node --test tests/js/card.test.js
```

The test configuration disables sockets except for loopback. Tests use a local
HTTP server and synthetic `example.test` query records; no Pi-hole, Home
Assistant installation, private DNS data, or credentials are needed.

`tests/benchmark_driver.py` is a manual benchmark helper, not a test.

Backend tests do not validate the Home Assistant frontend; the JS tests cover
the card logic only.
