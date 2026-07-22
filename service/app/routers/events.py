"""On-screen Home Assistant event channel for the kiosk grid.

Home Assistant posts events here (an automation's rest_command); the kiosk grid
polls ``/events/poll`` and acts on them: a camera pop-up expands that camera to
fullscreen for a few seconds, a notify shows a small toast.

  POST /events/camera-popup   {camera, seconds?}   camera by id or name
  POST /events/notify         {message, level?}
  GET  /events/poll?since=<id> events newer than <id>, plus the current last id

The two POSTs are the Home Assistant webhook. They live under ``/events``, not
``/api``, so the settings-auth gate (which only covers /api/* mutations) never
touches them. To keep a random WAN origin from driving the kiosk, they are
refused unless the caller is on a loopback or private (LAN) address, which is
all a LAN webhook needs for v1. ``GET /events/poll`` is open: the kiosk polls it.

Guard note: behind a reverse proxy ``request.client.host`` is the proxy's
address, so the private-origin check assumes Home Assistant reaches this
endpoint directly on the LAN (the documented setup), not through a public proxy.
"""
from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services import cameras as camera_store
from ..services import ha_events

router = APIRouter(prefix="/events", tags=["events"])

_WAN_REFUSED = {"ok": False,
                "error": "This endpoint accepts requests from the local network only."}

# Default seconds a popped camera stays fullscreen when the caller does not say.
_DEFAULT_POPUP_SECONDS = 20


class CameraPopupPayload(BaseModel):
    camera: str = ""             # camera id or name; empty = the first camera
    # Accept a float so a caller (e.g. the Stream Deck, whose duration is a
    # float) is never rejected; clamp_seconds rounds it to whole seconds.
    seconds: float = 0           # 0 = the default popup duration


class NotifyPayload(BaseModel):
    message: str = ""
    level: str = "info"          # info | success | warning | error


def is_private_origin(host: str) -> bool:
    """True when a request comes from a loopback or private (LAN) address.

    The webhook POSTs are refused from anywhere else so a random WAN origin
    cannot drive the kiosk. Only a classifiable IP literal can be vouched for; a
    bare hostname (rare for ``request.client.host``) is treated as not private.
    Pure, so the rule is unit-tested without a live request.
    """
    h = (host or "").split("%", 1)[0].strip()   # drop any IPv6 zone id
    if not h:
        return False
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # is_private already covers loopback and link-local; OR is_loopback for clarity.
    return bool(ip.is_loopback or ip.is_private)


def _origin_allowed(request: Request) -> bool:
    return is_private_origin(request.client.host if request.client else "")


def resolve_camera(camera: str):
    """Resolve an incoming token (camera id or name) to ``(id, name)`` or None.

    Matches an enabled camera by exact id first, then by case-insensitive name.
    An empty token falls back to the first enabled camera so a bare pop-up still
    shows something. Pure over the store read, so the match rule is testable.
    """
    cams = [c for c in camera_store.list_cameras() if c.get("enabled") is not False]
    if not cams:
        return None
    want = (camera or "").strip()
    if not want:
        first = cams[0]
        return first.get("id", ""), first.get("name", "")
    for c in cams:
        if str(c.get("id", "")) == want:
            return c.get("id", ""), c.get("name", "")
    low = want.lower()
    for c in cams:
        if str(c.get("name", "")).strip().lower() == low:
            return c.get("id", ""), c.get("name", "")
    return None


@router.post("/camera-popup")
async def camera_popup(payload: CameraPopupPayload, request: Request):
    """Queue a camera pop-up for the display (for example on person detected)."""
    if not _origin_allowed(request):
        return JSONResponse(_WAN_REFUSED, status_code=403)
    match = resolve_camera(payload.camera)
    if not match:
        return {"ok": False, "error": "No matching camera is configured."}
    cam_id, name = match
    seconds = payload.seconds if payload.seconds and payload.seconds > 0 else _DEFAULT_POPUP_SECONDS
    eid = ha_events.add_camera_popup(cam_id, seconds=seconds, name=name)
    return {"ok": True, "id": eid, "camera": cam_id}


@router.post("/notify")
async def notify(payload: NotifyPayload, request: Request):
    """Queue a notification toast for the display."""
    if not _origin_allowed(request):
        return JSONResponse(_WAN_REFUSED, status_code=403)
    if not payload.message.strip():
        return {"ok": False, "error": "message is required"}
    eid = ha_events.add_notify(payload.message, level=payload.level)
    return {"ok": True, "id": eid}


@router.get("/poll")
async def poll(since: int = 0):
    """New events since ``since``, plus the current last id. Open (the kiosk polls
    it); it reflects on-screen state and carries no secrets."""
    return ha_events.poll(since)
