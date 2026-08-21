# aiotrimlight

[![CI](https://github.com/bsholy/aiotrimlight/actions/workflows/ci.yml/badge.svg)](https://github.com/bsholy/aiotrimlight/actions/workflows/ci.yml)
[![Release](https://github.com/bsholy/aiotrimlight/actions/workflows/release.yml/badge.svg)](https://github.com/bsholy/aiotrimlight/actions/workflows/release.yml)

`aiotrimlight` is a small, fully asynchronous Python client for the Trimlight
V3 local HTTP API. It provides device discovery metadata, authoritative runtime
state, and high-level whole-installation light control without exposing
protocol commands to consumers such as Home Assistant.

## Requirements

- Python 3.14 or newer.
- A Trimlight controller with the V3 local HTTP API, including
  `get_runtime_state`, `set_static_output`, and `switch`.
- The Python host and controller must be reachable on the same local network.

## Installation

> [!IMPORTANT]
> `aiotrimlight` is not yet published to PyPI. Until the first release, install
> it from an authorized source checkout.

After the first PyPI release, the package can be installed with:

```shell
python -m pip install aiotrimlight
```

For current source development:

```shell
git clone git@github.com:bsholy/aiotrimlight.git
cd aiotrimlight
uv sync --locked --group test --python 3.14
```

Repository access is required while the GitHub project remains private.

## Usage

The following example reads a controller and then sets a whole-installation
static output. Replace the RFC 5737 documentation address with the
controller's LAN address.

```python
import asyncio

from aiohttp import ClientSession

from aiotrimlight import TrimlightClient


async def main() -> None:
    async with ClientSession(trust_env=False) as session:
        client = TrimlightClient("192.0.2.10", session)

        device = await client.get_device_info()
        current_state = await client.get_light_state()
        print(device, current_state)

        new_state = await client.set_light_state(
            on=True,
            brightness=64,
            red=255,
            green=255,
            blue=255,
            warm_white=0,
            cold_white=0,
        )
        print(new_state)


asyncio.run(main())
```

`trust_env=False` prevents system and shell proxy settings from routing local
controller traffic through an HTTP proxy. An application that supplies a
shared `ClientSession` is responsible for configuring equivalent proxy bypass
behavior.

## Discovery and protocol behavior

- mDNS discovery uses `_tlight._tcp.local.`.
- The firmware's 20-character hexadecimal TXT `did` is the only stable device
  identity parsed by the client. It is normalized to lowercase.
- HTTP requests always use `http://<host>:80/api/light`. The mDNS SRV port is
  associated with the native Trimlight TCP protocol and is not used by this
  HTTP adapter.
- `get_light_state()` reads the controller's real runtime state. A uniform
  whole-installation static output provides switch, brightness, and color
  values.
- During an effect, when no static output exists, or when zone outputs differ,
  the real switch state remains available while brightness and color are
  reported as `None`.
- Partial static updates merge with the last uniform static output. Before one
  has been read, the fallback is full-brightness RGB white with both white
  channels off.

## Development

Install the locked development environment and run the same checks as CI:

```shell
uv sync --locked --group test --python 3.14
uv run --no-sync pytest --cov=aiotrimlight --cov-branch --cov-report=term-missing
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests
uv build --no-sources --clear
uv run --no-sync twine check dist/*
uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py
uv run --isolated --no-project --with dist/*.tar.gz tests/smoke_test.py
```

> [!WARNING]
> `examples/turn_on.py` sends commands to real hardware. It is not run by the
> automated test suite. Review its host and output values before using it.

## Releasing

Publishing uses GitHub Actions and PyPI Trusted Publishing. Pushes, pull
requests, manual workflow runs, drafts, and prereleases never publish. Only a
formally published stable GitHub Release starts the release workflow.

The workflow reruns CI, requires the Release tag to equal `v<package version>`,
builds and validates both distributions without publishing credentials, and
then gives only the separate PyPI job an OIDC identity token. The PyPI Trusted
Publisher must use these values:

- PyPI project: `aiotrimlight`
- GitHub owner: `bsholy`
- GitHub repository: `aiotrimlight`
- Workflow: `release.yml`
- Environment: `pypi`

For the first publication, update the project version, lock file, and smoke
test together to `0.2.1`; commit those changes, create tag `v0.2.1`, and publish
a stable GitHub Release for that exact tag. Do not reuse `v0.2.0`.

## License

`aiotrimlight` is distributed under the MIT License. See [LICENSE](LICENSE).
