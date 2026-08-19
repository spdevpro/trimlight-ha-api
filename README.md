# aiotrimlight

`aiotrimlight` is a small, fully asynchronous Python client for the Trimlight
V3 local HTTP API. It keeps Trimlight protocol details independent from Home
Assistant and accepts an injected `aiohttp.ClientSession`.

> [!IMPORTANT]
> Version 0.2.0 is not yet published to PyPI. Install it from this source
> checkout for development.

## Protocol support

- Parse the firmware's 20-character hexadecimal `did` mDNS TXT property and
  expose it as the controller's stable identity.
- Read firmware information with `get_device_data` and IC capability with
  `get_runtime_state`.
- Read whole-installation switch and static-output state with
  `get_runtime_state`.
- Control whole-installation static output with `set_static_output` and ON/OFF
  with `switch` through the high-level `set_light_state()` API.

The client always communicates with `http://<host>:80/api/light`. The port in
the `_tlight._tcp.local.` SRV record belongs to Trimlight's native TCP protocol
and is deliberately ignored by the HTTP adapter.

`get_light_state()` performs authoritative HTTP readback. When the controller
is running an effect, has no static output, or reports different outputs for
multiple zones, the switch state remains available while brightness and color
are returned as `None`. Partial static updates use the last uniform static
output, or full-brightness white before one has been read.

## Usage

```python
from aiohttp import ClientSession

from aiotrimlight import TrimlightClient


async def turn_on(host: str) -> None:
    async with ClientSession(trust_env=False) as session:
        client = TrimlightClient(host, session)
        device = await client.get_device_info()
        state = await client.set_light_state(
            on=True,
            brightness=64,
            red=255,
            green=255,
            blue=255,
        )
        print(device, state)
```

Using `trust_env=False` prevents shell proxy variables from routing local
controller traffic through an HTTP proxy.

## Development

Python 3.14 or newer and [uv](https://docs.astral.sh/uv/) are required.

```shell
uv sync --locked --group test --python 3.14
uv run --no-sync pytest --cov=aiotrimlight --cov-branch
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests
uv build --no-sources
uv run --no-sync twine check dist/*
uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py
uv run --isolated --no-project --with dist/*.tar.gz tests/smoke_test.py
```

The standalone `examples/turn_on.py` script changes a physical light. It is
never run by the automated test suite.

## License

`aiotrimlight` is distributed under the MIT License. See [LICENSE](LICENSE).
