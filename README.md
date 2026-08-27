# aiotrimlight

[![CI](https://github.com/spdevpro/trimlight-ha-api/actions/workflows/ci.yml/badge.svg)](https://github.com/spdevpro/trimlight-ha-api/actions/workflows/ci.yml)

`aiotrimlight` is an asynchronous Python client for the Trimlight V3 local HTTP API. It provides device metadata, runtime light state, and high-level power, brightness, and static color control.

## Requirements

- Python 3.14 or newer.
- A Trimlight controller with the V3 local HTTP API.
- The client and controller must be reachable on the same local network.

## Installation

```shell
python -m pip install aiotrimlight
```

For source development:

```shell
git clone https://github.com/spdevpro/trimlight-ha-api.git
cd aiotrimlight
uv sync --locked --group test --python 3.14
```

## Usage

```python
import asyncio

from aiohttp import ClientSession

from aiotrimlight import TrimlightClient


async def main() -> None:
    async with ClientSession(trust_env=False) as session:
        client = TrimlightClient("192.0.2.10", session)

        device = await client.get_device_info()
        state = await client.get_light_state()
        print(device, state)

        await client.set_light_state(
            on=True,
            brightness=64,
            red=255,
            green=255,
            blue=255,
            warm_white=0,
            cold_white=0,
        )


asyncio.run(main())
```

Replace the example address with the controller's LAN IP address.

`trust_env=False` prevents system proxy settings from routing local controller traffic through an HTTP proxy.

## Development

```shell
uv sync --locked --group test --python 3.14
uv run --no-sync pytest --cov=aiotrimlight --cov-branch --cov-report=term-missing
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests
uv build --no-sources --clear
uv run --no-sync twine check dist/*
```

> [!WARNING]
> `examples/turn_on.py` sends commands to real Trimlight hardware. Review the configured host and output values before running it.

## Releases

Stable releases are built and published through GitHub Actions using PyPI
Trusted Publishing.

## License

`aiotrimlight` is distributed under the MIT License. See [LICENSE](LICENSE).
