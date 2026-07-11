"""go2rtc REST client: mirror the camera store into go2rtc's stream table.

Each enabled camera becomes one or two go2rtc streams named ``{id}_main`` and
``{id}_sub``. Credentials are embedded in the RTSP URL handed to go2rtc only
(server to server); they never reach a browser. All network errors are caught
and logged, never raised to callers, so a go2rtc outage degrades the grid
rather than crashing a request.

The URL and probe-response parsing helpers are kept pure so they are unit-tested
against fixture payloads with no network.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from ..config import settings

_log = logging.getLogger("glancecam.go2rtc")

# go2rtc is local (a sibling container or a loopback service), so calls should
# be quick; keep timeouts short so a stall never hangs a request.
_TIMEOUT = 5.0
_HEALTH_TIMEOUT = 2.0


def _base() -> str:
    return settings.go2rtc_url.rstrip("/")


def embed_credentials(url: str, username: str, password: str) -> str:
    """Return ``url`` with ``username:password@`` inserted into the authority.

    Only applies when a username is given and the URL does not already carry
    credentials. Username and password are percent-encoded. A non-RTSP URL or a
    URL that already has an ``@`` in its authority is returned unchanged.
    """
    if not username:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.netloc or "@" in parts.netloc:
        return url
    user = quote(username, safe="")
    pw = quote(password or "", safe="")
    cred = f"{user}:{pw}@" if pw else f"{user}@"
    netloc = cred + parts.netloc
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _stream_src(camera: dict, url: str) -> str:
    """The source string for go2rtc: an RTSP URL with creds embedded if needed."""
    username = camera.get("username") or ""
    password = camera.get("password") or ""
    if url.lower().startswith("rtsp://"):
        return embed_credentials(url, username, password)
    return url


async def sync_camera(camera: dict) -> None:
    """Create/update the go2rtc streams for one camera (main and sub)."""
    cid = camera.get("id")
    if not cid:
        return
    main_url = (camera.get("main_url") or "").strip()
    sub_url = (camera.get("sub_url") or "").strip()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            if main_url:
                await client.put(f"{_base()}/api/streams",
                                 params={"name": f"{cid}_main",
                                         "src": _stream_src(camera, main_url)})
            if sub_url:
                await client.put(f"{_base()}/api/streams",
                                 params={"name": f"{cid}_sub",
                                         "src": _stream_src(camera, sub_url)})
    except (httpx.HTTPError, OSError) as exc:
        _log.warning("go2rtc sync failed for %s: %s", cid, exc)


async def remove_camera(camera_id: str) -> None:
    """Delete both go2rtc streams for a camera."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for suffix in ("main", "sub"):
                await client.delete(f"{_base()}/api/streams",
                                    params={"src": f"{camera_id}_{suffix}"})
    except (httpx.HTTPError, OSError) as exc:
        _log.warning("go2rtc remove failed for %s: %s", camera_id, exc)


async def sync_all(cameras: list[dict]) -> None:
    """Push every enabled camera's streams into go2rtc (best effort)."""
    for cam in cameras:
        if cam.get("enabled", True):
            await sync_camera(cam)


def parse_probe(payload: Any) -> Optional[dict]:
    """Extract codec and resolution from a go2rtc stream info response.

    go2rtc returns a producers/medias structure that varies by source; this
    reads it defensively and returns ``{"codec": str|None, "resolution":
    [w, h]|None}`` or None if nothing usable is present. Pure, so it is unit
    tested against fixture payloads.
    """
    if not isinstance(payload, dict):
        return None
    producers = payload.get("producers")
    if not isinstance(producers, list):
        return None
    codec = None
    resolution = None
    for producer in producers:
        if not isinstance(producer, dict):
            continue
        medias = producer.get("medias")
        if not isinstance(medias, list):
            continue
        for media in medias:
            text = ""
            if isinstance(media, str):
                text = media
            elif isinstance(media, dict):
                text = str(media.get("codec") or media.get("name") or "")
            # A go2rtc media line looks like "video, recvonly, H264, 2560x1440".
            parts = [p.strip() for p in text.split(",")]
            for part in parts:
                if codec is None and part.upper() in (
                        "H264", "H265", "HEVC", "MJPEG", "AV1", "VP8", "VP9"):
                    codec = part.upper()
                if resolution is None and "x" in part:
                    dims = part.lower().split("x")
                    if len(dims) == 2 and dims[0].isdigit() and dims[1].isdigit():
                        resolution = [int(dims[0]), int(dims[1])]
    if codec is None and resolution is None:
        return None
    return {"codec": codec, "resolution": resolution}


async def probe(stream_name: str) -> Optional[dict]:
    """Ask go2rtc about a stream and return its parsed codec/resolution.

    Returns None on any failure (stream missing, go2rtc down, unparseable
    response) so a probe never breaks a caller.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base()}/api/streams",
                                    params={"src": stream_name})
            if resp.status_code != 200:
                return None
            return parse_probe(resp.json())
    except (httpx.HTTPError, OSError, ValueError) as exc:
        _log.debug("go2rtc probe failed for %s: %s", stream_name, exc)
        return None


async def healthy() -> bool:
    """True when go2rtc's API answers. Short timeout, never raises."""
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
            resp = await client.get(f"{_base()}/api")
            return resp.status_code == 200
    except (httpx.HTTPError, OSError) as exc:
        _log.debug("go2rtc health check failed: %s", exc)
        return False
