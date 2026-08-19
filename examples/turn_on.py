"""Turn a physical Trimlight controller on through the high-level client API."""

import argparse
import asyncio

from aiohttp import ClientSession

from aiotrimlight import TrimlightClient


async def async_main(host: str, brightness: int) -> None:
    """Get device information and turn the whole installation on."""
    async with ClientSession(trust_env=False) as session:
        client = TrimlightClient(host, session)
        device_info = await client.get_device_info()
        print(
            "Connected to",
            host,
            f"firmware={device_info.firmware_version}",
            f"ic={device_info.ic_type.name}",
        )
        state = await client.set_light_state(
            on=True,
            brightness=brightness,
            red=255,
            green=255,
            blue=255,
            warm_white=0,
            cold_white=0,
        )
        print(f"Controller reported state: {state}")


def main() -> None:
    """Run the physical-device command."""
    parser = argparse.ArgumentParser(
        description="Turn on a Trimlight controller; this changes a physical light."
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--brightness", type=int, default=64)
    args = parser.parse_args()
    asyncio.run(async_main(args.host, args.brightness))


if __name__ == "__main__":
    main()
