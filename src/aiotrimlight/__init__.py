"""Asynchronous Trimlight V3 HTTP client."""

from .client import TrimlightClient
from .discovery import parse_discovery_properties
from .exceptions import (
    TrimlightCommandError,
    TrimlightConnectionError,
    TrimlightDiscoveryError,
    TrimlightError,
    TrimlightHTTPError,
    TrimlightProtocolError,
    TrimlightUnsupportedICError,
)
from .models import (
    TrimlightDeviceInfo,
    TrimlightDiscoveryInfo,
    TrimlightEffect,
    TrimlightICType,
    TrimlightLightState,
    TrimlightOutputMode,
    TrimlightZoneState,
)

__all__ = [
    "TrimlightClient",
    "TrimlightCommandError",
    "TrimlightConnectionError",
    "TrimlightDeviceInfo",
    "TrimlightDiscoveryError",
    "TrimlightDiscoveryInfo",
    "TrimlightEffect",
    "TrimlightError",
    "TrimlightHTTPError",
    "TrimlightICType",
    "TrimlightLightState",
    "TrimlightOutputMode",
    "TrimlightProtocolError",
    "TrimlightUnsupportedICError",
    "TrimlightZoneState",
    "parse_discovery_properties",
]
