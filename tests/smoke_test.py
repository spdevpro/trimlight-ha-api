"""Verify an installed aiotrimlight distribution."""

from importlib.metadata import version

import aiotrimlight

PUBLIC_EXPORTS = {
    "TrimlightClient",
    "TrimlightDeviceInfo",
    "TrimlightDiscoveryInfo",
    "TrimlightLightState",
    "TrimlightEffect",
    "TrimlightOutputMode",
    "TrimlightZoneState",
    "parse_discovery_properties",
}


def main() -> None:
    """Check the installed version and primary public exports."""
    assert version("aiotrimlight") == "0.2.2"
    assert set(aiotrimlight.__all__) >= PUBLIC_EXPORTS
    for export in PUBLIC_EXPORTS:
        assert hasattr(aiotrimlight, export)

    discovery = aiotrimlight.parse_discovery_properties({"did": "544c0003020000000001"})
    assert discovery.mac_address == "02:00:00:00:00:01"
    assert callable(aiotrimlight.TrimlightClient.get_effect_list)
    assert callable(aiotrimlight.TrimlightClient.play_effect)
    scene = aiotrimlight.TrimlightEffect(1, "Scene")
    zone = aiotrimlight.TrimlightZoneState(
        1, True, aiotrimlight.TrimlightOutputMode.EFFECT
    )
    state = aiotrimlight.TrimlightLightState(scene_id=scene.id, zones=(zone,))
    assert state.scene_id == 1
    assert state.zones == (zone,)


if __name__ == "__main__":
    main()
