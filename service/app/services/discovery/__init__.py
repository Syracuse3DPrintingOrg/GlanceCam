"""Camera discovery: LAN scan, ONVIF, Reolink, and Home Assistant probes.

Each submodule turns what it finds on the network into *proposed Camera* dicts
the settings UI can one-click add. A proposal mirrors the stored Camera shape
(see the design data model) but is never persisted directly: the user confirms
it, the app tests it, and only then does it enter the camera store. There is no
aggregate fan-out here on purpose: each protocol is triggered separately from
the UI (a LAN scan is a long job, ONVIF is a multicast burst, Reolink and HA
need credentials), so the router calls the submodules directly.

Proposals never carry a password. A discovered camera that needs one carries the
``username`` the user typed; the UI holds the password client-side and sends it
with the add/test call. That keeps secrets from ever round-tripping through a
discovery response.
"""
from __future__ import annotations

from . import homeassistant, jobs, lanscan, onvif, reolink, streampaths

__all__ = ["jobs", "lanscan", "onvif", "reolink", "homeassistant",
           "streampaths", "PROPOSAL_FIELDS"]

# The keys a discovery proposal may carry, documented in one place so every
# probe stays consistent and the UI knows what to expect. Not every probe fills
# every field: a LAN-scanned host has no RTSP path, an HA feed has no main/sub,
# a Reolink NVR fills all of them.
PROPOSAL_FIELDS = {
    "source": "manual | reolink | onvif | homeassistant",
    "name": "suggested display name",
    "main_url": "main RTSP stream, when derivable, else null",
    "sub_url": "sub RTSP stream, when derivable, else null",
    "snapshot_url": "still-image URL, when derivable, else null",
    "main_resolution": "[w, h] when read from a snapshot, else null",
    "username": "the login the user supplied (never a password)",
    "ha_entity": "homeassistant source only: the camera.* entity id",
    "notes": "user-forward hints: brand, auth needed, RTSP-only, HA caveat",
    # LAN-scan extras the add/re-probe flow uses; harmless in a saved camera
    # because the store keeps only its allowed keys.
    "ip": "scanned host address",
    "ports": "open camera ports on a scanned host",
    "brand": "brand hint from a matching snapshot path",
    "brand_hint": "brand guessed from the open-port signature (shown as a guess)",
    "rtsp": "scanned host answers RTSP",
    "auth_required": "scanned host returned 401/403",
    "channel": "reolink source only: 0-based channel index",
}
