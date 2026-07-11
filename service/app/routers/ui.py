"""User-facing pages and the server-side snapshot proxy.

The grid page renders the camera list (public view, no credentials). The
snapshot proxy fetches a still for a camera server-side: from its snapshot URL
(with stored credentials, which a browser <img> could not send), or, failing
that, from go2rtc's frame endpoint. Snapshots are used for paused tiles and as
a fallback while a live stream warms up.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..config import settings
from ..services import cameras as store
from ..services import go2rtc
from ..services import netguard
from ..templating import templates, base_context

_log = logging.getLogger("glancecam.ui")

router = APIRouter(tags=["ui"])

_NO_CACHE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


def _render_grid(request: Request):
    ctx = base_context(request)
    ctx["cameras"] = [store.public_view(c) for c in store.list_cameras()]
    return templates.TemplateResponse("index.html", ctx)


@router.get("/")
async def index(request: Request):
    return _render_grid(request)


@router.get("/display")
async def display(request: Request):
    # Alias of the grid, kept as a distinct path so a kiosk can be pointed at a
    # stable URL that never redirects.
    return _render_grid(request)


def _basic_auth(camera: dict):
    user = camera.get("username") or ""
    pw = camera.get("password") or ""
    if user:
        return httpx.BasicAuth(user, pw)
    return None


@router.get("/cam/{camera_id}/snapshot")
async def snapshot(camera_id: str):
    """A single still for a camera, fetched server-side."""
    camera = store.get(camera_id)
    if camera is None:
        return JSONResponse({"detail": "No such camera."}, status_code=404)

    snap_url = (camera.get("snapshot_url") or "").strip()
    verify = not camera.get("allow_self_signed", False)
    if snap_url:
        # Saved camera: fail OPEN on an unresolvable host (a DNS hiccup should
        # not blank a real camera), but still refuse a resolved internal target.
        if not netguard.guard_url(snap_url, fail_closed=False):
            try:
                async with httpx.AsyncClient(timeout=8.0, verify=verify,
                                             follow_redirects=True) as client:
                    resp = await client.get(snap_url, auth=_basic_auth(camera))
                    if resp.status_code == 200 and resp.content:
                        media = resp.headers.get("content-type", "image/jpeg")
                        return Response(content=resp.content, media_type=media,
                                        headers=_NO_CACHE)
            except (httpx.HTTPError, OSError) as exc:
                _log.debug("snapshot fetch failed for %s: %s", camera_id, exc)

    # Fall back to a go2rtc frame from the sub stream, then the main stream.
    for suffix in ("sub", "main"):
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{go2rtc._base()}/api/frame.jpeg",
                                        params={"src": f"{camera_id}_{suffix}"})
                if resp.status_code == 200 and resp.content:
                    return Response(content=resp.content, media_type="image/jpeg",
                                    headers=_NO_CACHE)
        except (httpx.HTTPError, OSError) as exc:
            _log.debug("go2rtc frame failed for %s_%s: %s", camera_id, suffix, exc)

    return JSONResponse({"detail": "No snapshot available."}, status_code=404,
                        headers=_NO_CACHE)


def _strip_credentials(url: str) -> str:
    """A URL with any ``user:pass@`` authority removed, for safe display."""
    if not url:
        return ""
    from urllib.parse import urlsplit, urlunsplit
    try:
        p = urlsplit(url)
    except ValueError:
        return ""
    netloc = p.netloc.split("@", 1)[-1] if "@" in p.netloc else p.netloc
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


@router.get("/cam/{camera_id}/diag")
async def diag(camera_id: str):
    """Troubleshooting JSON for one camera. Never includes secrets."""
    camera = store.get(camera_id)
    if camera is None:
        return JSONResponse({"detail": "No such camera."}, status_code=404)

    snap_url = (camera.get("snapshot_url") or "").strip()
    snapshot_ok = None
    snapshot_status = None
    if snap_url and not netguard.guard_url(snap_url, fail_closed=False):
        try:
            async with httpx.AsyncClient(
                    timeout=8.0, verify=not camera.get("allow_self_signed", False),
                    follow_redirects=True) as client:
                resp = await client.get(snap_url, auth=_basic_auth(camera))
                snapshot_status = resp.status_code
                snapshot_ok = resp.status_code == 200 and bool(resp.content)
        except (httpx.HTTPError, OSError):
            snapshot_ok = False

    return {
        "id": camera_id,
        "name": camera.get("name"),
        "source": camera.get("source"),
        "main_url": _strip_credentials(camera.get("main_url") or ""),
        "sub_url": _strip_credentials(camera.get("sub_url") or ""),
        "snapshot_url": _strip_credentials(snap_url),
        "has_credentials": bool(camera.get("username") or camera.get("password")),
        "go2rtc_main": await go2rtc.probe(f"{camera_id}_main"),
        "go2rtc_sub": await go2rtc.probe(f"{camera_id}_sub"),
        "snapshot_ok": snapshot_ok,
        "snapshot_status": snapshot_status,
    }
