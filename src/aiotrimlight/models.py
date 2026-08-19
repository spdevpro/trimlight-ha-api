"""Data models for aiotrimlight."""

from dataclasses import dataclass
from enum import IntEnum


class TrimlightICType(IntEnum):
    """Trimlight light IC capability."""

    RGB = 0
    RGBW = 1
    RGBCW = 2


@dataclass(frozen=True, slots=True)
class TrimlightDiscoveryInfo:
    """Identity advertised by Trimlight mDNS discovery."""

    name: str | None
    did: str


@dataclass(frozen=True, slots=True)
class TrimlightDeviceInfo:
    """Device information returned by the HTTP API."""

    firmware_version: str | None
    ic_type: TrimlightICType


@dataclass(frozen=True, slots=True)
class TrimlightLightState:
    """Current whole-installation light state reported by the controller."""

    is_on: bool = False
    brightness: int | None = None
    red: int | None = None
    green: int | None = None
    blue: int | None = None
    warm_white: int | None = None
    cold_white: int | None = None
