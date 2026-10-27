"""Tests for Trimlight data models."""

import pytest

from aiotrimlight import TrimlightDeviceInfo, TrimlightICType


@pytest.mark.parametrize(
    "did",
    [
        pytest.param("544c0003a1b2c3d4e5f6", id="lowercase"),
        pytest.param("544C0003A1B2C3D4E5F6", id="uppercase"),
        pytest.param("deadbeefa1b2c3d4e5f6", id="other-product"),
    ],
)
def test_device_info_mac_address(did: str) -> None:
    """Test deriving the MAC without changing the device identity."""
    info = TrimlightDeviceInfo(
        firmware_version=None,
        ic_type=TrimlightICType.RGB,
        did=did,
        device_name="Test controller",
    )

    assert info.mac_address == "a1:b2:c3:d4:e5:f6"
    assert info.did == did
