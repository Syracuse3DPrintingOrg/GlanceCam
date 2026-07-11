"""Home Assistant camera discovery.

Given a Home Assistant base URL and a long-lived access token, read
``GET /api/states`` and keep the ``camera.*`` entities. Each becomes a proposed
Camera the app serves by proxying HA's ``camera_proxy`` endpoint with the token
in a header (a browser cannot set that header, so these feeds are always fetched
server-side). HA cameras are snapshot/MJPEG only: there is no separate main/sub
RTSP stream, which the proposal notes so the UI can say so.

The base URL is checked through the SSRF guard fail-closed before any request,
since it is user-supplied. The token stays server-side: it is never echoed in a
proposal.
"""
from __future__ import annotations

from typing import Optional

import httpx

from .. import netguard


def parse_camera_states(states) -> list:
    """Turn an HA /api/states payload into proposed Camera dicts.

    Pure, so it is unit-tested against a fixture states list. Keeps the
    ``camera.*`` entities and derives a friendly name from the attributes, or
    from the entity id when none is set.
    """
    out = []
    for st in states if isinstance(states, list) else []:
        if not isinstance(st, dict):
            continue
        entity = str(st.get("entity_id", ""))
        if not entity.startswith("camera."):
            continue
        attrs = st.get("attributes") or {}
        name = (attrs.get("friendly_name")
                or entity.split(".", 1)[1].replace("_", " ").title())
        out.append({
            "source": "homeassistant",
            "name": name,
            "ha_entity": entity,
            "main_url": None,
            "sub_url": None,
            "snapshot_url": None,
            "notes": "Home Assistant feed: snapshot or MJPEG only, no separate "
                     "main and sub stream.",
        })
    out.sort(key=lambda c: c["name"].lower())
    return out


def list_cameras(base_url: str, token: str, timeout: float = 8.0) -> dict:
    """List HA camera entities as proposals. Returns ``{ok, proposals, error}``.

    The base URL is SSRF-guarded fail-closed (arbitrary user input). Never
    raises: an unreachable HA, a rejected token, or a bad URL comes back as
    ``ok=False`` with a user-forward message.
    """
    base = (base_url or "").strip().rstrip("/")
    token = (token or "").strip()
    if not base or not token:
        return {"ok": False,
                "error": "Enter the Home Assistant address and a long-lived "
                         "access token."}
    if netguard.is_blocked_fetch_host(base, fail_closed=True):
        return {"ok": False, "error": netguard.BLOCKED_HOST_MESSAGE}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(f"{base}/api/states",
                              headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Could not reach Home Assistant: {exc}"}
    if resp.status_code in (401, 403):
        return {"ok": False,
                "error": "Home Assistant rejected the token."}
    if resp.status_code != 200:
        return {"ok": False,
                "error": f"Home Assistant returned HTTP {resp.status_code}."}
    try:
        states = resp.json()
    except ValueError:
        return {"ok": False,
                "error": "Home Assistant sent back an unexpected response."}
    return {"ok": True, "proposals": parse_camera_states(states), "error": ""}
