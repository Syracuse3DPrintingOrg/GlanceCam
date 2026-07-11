"""Camera discovery (LAN scan, ONVIF, Reolink, Home Assistant).

STUB: another agent owns the real implementation in a later wave. The public
seam is ``discover()``, which will fan out to the per-protocol probes and
return proposed Camera dicts the UI can one-click add.
"""
from __future__ import annotations


async def discover(*args, **kwargs) -> list[dict]:
    """Aggregate discovery fan-out. Returns proposed Camera dicts.

    TODO(discovery agent): implement the LAN/ONVIF/Reolink/HA probes under this
    package and fan out to them here. Returns an empty list until then.
    """
    return []
