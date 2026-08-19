"""Trimlight mDNS discovery parsing."""

import re
from collections.abc import Mapping

from .exceptions import TrimlightDiscoveryError
from .models import TrimlightDiscoveryInfo

_DID_PATTERN = re.compile(r"[0-9a-fA-F]{20}")


def parse_discovery_properties(
    properties: Mapping[str, object],
) -> TrimlightDiscoveryInfo:
    """Parse the firmware's DID and optional name TXT properties."""
    did = properties.get("did")
    if not isinstance(did, str):
        raise TrimlightDiscoveryError("did TXT property must be a string")
    if _DID_PATTERN.fullmatch(did) is None:
        raise TrimlightDiscoveryError(
            "did TXT property must be 20 hexadecimal characters"
        )

    raw_name = properties.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else None
    if not name:
        name = None

    return TrimlightDiscoveryInfo(name=name, did=did.lower())
