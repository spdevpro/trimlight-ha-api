"""Data models for aiotrimlight."""

from dataclasses import dataclass
from enum import IntEnum


class TrimlightICType(IntEnum):
    """Trimlight light IC capability."""

    RGB = 0
    RGBW = 1
    RGBCW = 2


class TrimlightOutputMode(IntEnum):
    """Numeric output mode reported for a zone."""

    EFFECT = 0
    STATIC = 1
    NONE = 2


@dataclass(frozen=True, slots=True)
class TrimlightEffect:
    """Saved library scene identity, not a zone's internal effect preset."""

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class TrimlightZoneState:
    """Reported zone output, independent of the device power state."""

    zone_id: int
    output_enabled: bool
    output_mode: TrimlightOutputMode


@dataclass(frozen=True, slots=True)
class TrimlightDiscoveryInfo:
    """Identity advertised by Trimlight mDNS discovery."""

    name: str | None
    did: str

    @property
    def mac_address(self) -> str:
        """Return the controller MAC address encoded in the DID."""
        mac = self.did[-12:].lower()
        return ":".join(mac[index : index + 2] for index in range(0, 12, 2))


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
    scene_id: int | None = None
    zones: tuple[TrimlightZoneState, ...] = ()
