"""Verify an installed aiotrimlight distribution."""

from importlib.metadata import version

import aiotrimlight

PUBLIC_EXPORTS = {
    "TrimlightClient",
    "TrimlightDeviceInfo",
    "TrimlightLightState",
    "parse_discovery_properties",
}


def main() -> None:
    """Check the installed version and primary public exports."""
    assert version("aiotrimlight") == "0.2.0"
    assert set(aiotrimlight.__all__) >= PUBLIC_EXPORTS
    for export in PUBLIC_EXPORTS:
        assert hasattr(aiotrimlight, export)


if __name__ == "__main__":
    main()
