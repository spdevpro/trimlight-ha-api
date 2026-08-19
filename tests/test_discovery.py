"""Tests for Trimlight discovery parsing."""

import pytest

from aiotrimlight import TrimlightDiscoveryError, parse_discovery_properties

VALID_DID = "544c0003a1b2c3d4e5f6"


def test_parse_discovery_properties() -> None:
    """Test normalizing the firmware DID and stripping the name."""
    info = parse_discovery_properties(
        {"did": VALID_DID.upper(), "name": " Test controller ", "mfr": "invalid"}
    )

    assert info.name == "Test controller"
    assert info.did == VALID_DID


def test_parse_discovery_properties_without_name() -> None:
    """Test that the optional device name may be absent."""
    info = parse_discovery_properties({"did": VALID_DID})

    assert info.name is None


def test_parse_discovery_properties_with_invalid_name() -> None:
    """Test ignoring a non-string optional device name."""
    info = parse_discovery_properties({"did": VALID_DID, "name": 123})

    assert info.name is None


@pytest.mark.parametrize(
    ("properties", "error"),
    [
        pytest.param({}, "did TXT property must be a string", id="missing"),
        pytest.param(
            {"mfr": "ignored"},
            "did TXT property must be a string",
            id="manufacturer-data-only",
        ),
        pytest.param(
            {"did": 123},
            "did TXT property must be a string",
            id="invalid-type",
        ),
        pytest.param(
            {"did": "544c0003a1b2c3d4e5f"},
            "did TXT property must be 20 hexadecimal characters",
            id="too-short",
        ),
        pytest.param(
            {"did": "544c0003a1b2c3d4e5f60"},
            "did TXT property must be 20 hexadecimal characters",
            id="too-long",
        ),
        pytest.param(
            {"did": "544c0003a1b2c3d4e5fg"},
            "did TXT property must be 20 hexadecimal characters",
            id="non-hexadecimal",
        ),
    ],
)
def test_parse_discovery_properties_errors(
    properties: dict[str, str], error: str
) -> None:
    """Test rejecting invalid discovery identities."""
    with pytest.raises(TrimlightDiscoveryError, match=error):
        parse_discovery_properties(properties)
