"""Discovery endpoints: LAN scan, ONVIF, Reolink, and Home Assistant.

The LAN scan is a background job (a /24 sweep is slower than a request should
block on): ``POST /api/discovery/scan`` starts one and returns a ``job_id``,
``GET /api/discovery/scan/{job_id}`` polls it, and only one scan runs at a time
(a second start answers 409). ONVIF, Reolink, and HA are quick enough to answer
synchronously.

Every user-supplied host or URL is passed through the SSRF guard fail-closed
before any server-side fetch, so an internal or unresolvable target is refused
rather than probed.

Passwords never come back. A discovery proposal carries the ``username`` the
user typed but no password: the browser keeps the password it collected and
sends it again with the add or test call. This router only ever receives a
password (to probe a device the user chose) and never returns one.
"""
from __future__ import annotations

import anyio
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..config import settings
from ..services import go2rtc, netguard
from ..services.discovery import homeassistant, jobs, lanscan, onvif, reolink

router = APIRouter(prefix="/api/discovery", tags=["discovery"])

_NO_CACHE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
_PREVIEW_TIMEOUT = 5.0


def classify_preview_url(url: str) -> str:
    """Classify a preview target as ``'image'``, ``'rtsp'``, or ``'unknown'``.

    Pure and side-effect free so it is unit tested on its own. Only http(s) and
    rtsp are honoured; anything else (file://, a bare host, a data: URL) is
    ``'unknown'`` and the endpoint refuses it before any fetch.
    """
    scheme = (url or "").strip().split("://", 1)[0].lower() if "://" in (url or "") else ""
    if scheme in ("http", "https"):
        return "image"
    if scheme == "rtsp":
        return "rtsp"
    return "unknown"


async def _payload(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        data = dict(await request.form())
    return data if isinstance(data, dict) else {}


@router.get("/scan/default")
async def scan_default():
    """The CIDR the LAN scan would default to, so the UI can pre-fill it."""
    cidr = lanscan.best_lan_cidr() or ""
    return {"cidr": cidr,
            "dockerish": lanscan.looks_dockerish(cidr) if cidr else False}


@router.post("/scan")
async def start_scan(request: Request):
    """Start a LAN camera scan. Returns ``{job_id}`` or 409 if one is running."""
    data = await _payload(request)
    cidr = (data.get("cidr") or "").strip() or (lanscan.best_lan_cidr() or "")
    if not cidr:
        return JSONResponse(
            {"detail": "Could not work out a network range to scan. Enter one "
                       "like 192.168.1.0/24."},
            status_code=400)

    def _job(report):
        return lanscan.scan(cidr, report=report)

    try:
        job_id = jobs.start(_job)
    except jobs.JobBusy as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    return {"job_id": job_id, "cidr": cidr}


@router.get("/scan/{job_id}")
async def scan_status(job_id: str):
    """Poll a scan job: ``{id, status, progress, results, error}``."""
    job = jobs.public(jobs.get(job_id))
    if job is None:
        return JSONResponse({"detail": "No such scan (it may have expired)."},
                            status_code=404)
    return job


@router.post("/scan/probe")
async def scan_probe(request: Request):
    """Re-probe one scanned host with credentials to find a working snapshot."""
    data = await _payload(request)
    host = (data.get("host") or data.get("ip") or "").strip()
    if not host:
        return {"ok": False, "error": "No camera address given."}
    if netguard.is_blocked_fetch_host(host, fail_closed=True):
        return {"ok": False, "error": netguard.BLOCKED_HOST_MESSAGE}
    username = data.get("username") or ""
    password = data.get("password") or ""
    result = await anyio.to_thread.run_sync(
        lambda: lanscan.probe_with_auth(host, username, password))
    return result


@router.post("/onvif")
async def onvif_discover(request: Request):
    """Multicast an ONVIF WS-Discovery probe. Returns the devices found."""
    data = await _payload(request)
    try:
        timeout = float(data.get("timeout") or 3.0)
    except (TypeError, ValueError):
        timeout = 3.0
    timeout = max(1.0, min(timeout, 10.0))
    devices = await anyio.to_thread.run_sync(lambda: onvif.ws_discovery(timeout))
    return {"ok": True, "devices": devices}


@router.post("/onvif/streams")
async def onvif_streams(request: Request):
    """Resolve an ONVIF device's main/sub RTSP addresses from its service URL."""
    data = await _payload(request)
    xaddr = (data.get("xaddr") or "").strip()
    if not xaddr:
        return {"ok": False, "error": "No device service address given."}
    if netguard.is_blocked_fetch_host(xaddr, fail_closed=True):
        return {"ok": False, "error": netguard.BLOCKED_HOST_MESSAGE}
    username = data.get("username") or ""
    password = data.get("password") or ""
    result = await anyio.to_thread.run_sync(
        lambda: onvif.get_streams(xaddr, username, password))
    return result


@router.post("/reolink")
async def reolink_discover(request: Request):
    """Probe a Reolink camera or NVR and return per-channel proposals."""
    data = await _payload(request)
    host = (data.get("host") or "").strip()
    if not host:
        return {"ok": False, "error": "Enter the camera's address."}
    if netguard.is_blocked_fetch_host(host, fail_closed=True):
        return {"ok": False, "error": netguard.BLOCKED_HOST_MESSAGE}
    username = data.get("username") or ""
    password = data.get("password") or ""
    result = await anyio.to_thread.run_sync(
        lambda: reolink.probe(host, username, password))
    return result


@router.post("/homeassistant")
async def homeassistant_discover(request: Request):
    """List Home Assistant camera entities as proposals.

    Falls back to the saved HA connection when the request omits it. When the
    request supplies a base URL and token with ``save: true`` and the listing
    succeeds, the connection is persisted so later discovery does not need it
    re-entered. The token is never echoed back in the response.
    """
    data = await _payload(request)
    base = (data.get("base_url") or settings.ha_base_url or "").strip()
    token = (data.get("token") or settings.ha_token or "").strip()
    result = await anyio.to_thread.run_sync(
        lambda: homeassistant.list_cameras(base, token))
    if result.get("ok") and data.get("save") and data.get("base_url") \
            and data.get("token"):
        try:
            settings.save({"ha_base_url": base, "ha_token": token})
        except OSError:
            pass  # a read-only data dir must not fail the discovery itself
    return result


async def _preview_image(url: str, username: str, password: str) -> Response:
    """Fetch a snapshot URL server-side and return the image, or a JSON error.

    Tries no-auth first, then digest and basic when credentials are given (a
    camera may use either). Validates image magic bytes before returning so an
    HTML error page is never handed back as a picture. Credentials are used only
    to fetch; they are never echoed.
    """
    attempts = [None]
    if username:
        attempts.append(httpx.DigestAuth(username, password))
        attempts.append(httpx.BasicAuth(username, password))
    try:
        async with httpx.AsyncClient(timeout=_PREVIEW_TIMEOUT,
                                     follow_redirects=True) as client:
            for auth in attempts:
                try:
                    resp = await client.get(url, auth=auth)
                except (httpx.HTTPError, OSError):
                    continue
                if resp.status_code == 200 and \
                        lanscan.looks_like_image_bytes(resp.content):
                    media = resp.headers.get("content-type", "image/jpeg")
                    if not media.lower().startswith("image/"):
                        media = "image/jpeg"
                    return Response(content=resp.content, media_type=media,
                                    headers=_NO_CACHE)
    except (httpx.HTTPError, OSError):
        pass
    return JSONResponse(
        {"ok": False, "error": "Could not load a snapshot from that address."})


async def _preview_rtsp(url: str, username: str, password: str) -> Response:
    """Round-trip an RTSP URL through go2rtc and return a real JPEG frame.

    Mirrors ``POST /api/cameras/test``: add a temporary stream, probe it, and
    ask go2rtc for a frame, then always delete the temp stream. Returns the
    frame as an image when go2rtc could decode one, otherwise JSON with the
    resolution (when known) or a clean error.
    """
    src = go2rtc._stream_src(
        {"username": username, "password": password} if username else {"id": "preview"},
        url)
    test_name = "glancecam_preview_probe"
    result = None
    frame = None
    try:
        async with httpx.AsyncClient(timeout=go2rtc._TIMEOUT) as client:
            await client.put(f"{go2rtc._base()}/api/streams",
                             params={"name": test_name, "src": src})
            result = await go2rtc.probe(test_name)
            try:
                fr = await client.get(f"{go2rtc._base()}/api/frame.jpeg",
                                      params={"src": test_name})
                if fr.status_code == 200 and fr.content:
                    frame = fr.content
            except (httpx.HTTPError, OSError):
                pass
    except (httpx.HTTPError, OSError):
        pass
    finally:
        try:
            async with httpx.AsyncClient(timeout=go2rtc._TIMEOUT) as client:
                await client.delete(f"{go2rtc._base()}/api/streams",
                                    params={"src": test_name})
        except (httpx.HTTPError, OSError):
            pass

    if frame:
        return Response(content=frame, media_type="image/jpeg", headers=_NO_CACHE)
    if result and result.get("resolution"):
        return JSONResponse({"ok": True, "resolution": result["resolution"],
                             "codec": result.get("codec")})
    return JSONResponse(
        {"ok": False, "error": "Could not read a stream from that address."})


@router.post("/preview")
async def preview(request: Request):
    """Preview one candidate URL for a device, SSRF fail-closed.

    An http(s) snapshot URL is fetched and returned as an image; an rtsp:// URL
    is decoded by go2rtc into a JPEG frame. Any other scheme, or an internal or
    unresolvable target, is refused with clean JSON (never a 500). Credentials
    are used only to reach the device and are never returned.
    """
    data = await _payload(request)
    url = (data.get("url") or "").strip()
    if not url:
        return JSONResponse({"ok": False, "error": "Enter a URL to preview."},
                            status_code=400)
    kind = classify_preview_url(url)
    if kind == "unknown":
        return JSONResponse(
            {"ok": False, "error": "That is not a snapshot or RTSP address."},
            status_code=400)
    if netguard.guard_url(url, fail_closed=True):
        return JSONResponse({"ok": False, "error": netguard.BLOCKED_HOST_MESSAGE},
                            status_code=400)
    username = data.get("username") or ""
    password = data.get("password") or ""
    if kind == "image":
        return await _preview_image(url, username, password)
    return await _preview_rtsp(url, username, password)
