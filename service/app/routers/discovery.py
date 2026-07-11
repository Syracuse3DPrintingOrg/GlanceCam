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
from ..services import credentials, go2rtc, netguard
from ..services.discovery import (homeassistant, jobs, lanscan, onvif, reolink,
                                  streampaths)

router = APIRouter(prefix="/api/discovery", tags=["discovery"])

_NO_CACHE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
_PREVIEW_TIMEOUT = 5.0

# Auto-find: keep a probe short so trying ten candidates stays bounded (~10 * 4s
# worst case), and cap how many are tried so a single camera is never hammered.
_FIND_TIMEOUT = 4.0
_FIND_MAX = 10


def _resolve_creds(data: dict) -> tuple[str, str]:
    """The (username, password) to probe with, from a saved set or the payload.

    When ``credential_id`` names a saved set it wins, so the browser never has
    to hold the password. Otherwise the username/password typed into the request
    are used. Either way the values are used only to reach the device and are
    never echoed back.
    """
    resolved = credentials.resolve(data.get("credential_id") or "")
    if resolved is not None:
        return resolved
    return data.get("username") or "", data.get("password") or ""


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
    username, password = _resolve_creds(data)
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
    username, password = _resolve_creds(data)
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
            # Ask for the frame BEFORE probing: go2rtc connects to a camera
            # only when something consumes the stream, and the frame request
            # is that consumer. Even when the JPEG fails (an H265 stream with
            # no ffmpeg on the host) the attempt leaves the producer connected
            # so the probe can still read the codec and resolution.
            try:
                fr = await client.get(f"{go2rtc._base()}/api/frame.jpeg",
                                      params={"src": test_name})
                if fr.status_code == 200 and fr.content:
                    frame = fr.content
            except (httpx.HTTPError, OSError):
                pass
            result = await go2rtc.probe(test_name)
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
    # A readable stream is a success even without a thumbnail: go2rtc needs
    # ffmpeg on the host to turn an H265 stream into a JPEG, so codec-only
    # results are common and the stream itself is fine.
    if result and (result.get("resolution") or result.get("codec")):
        return JSONResponse({"ok": True, "resolution": result.get("resolution"),
                             "codec": result.get("codec")})
    return JSONResponse(
        {"ok": False, "error": "Could not read a stream from that address."})


async def _probe_rtsp_url(url: str, username: str, password: str,
                          timeout: float = _FIND_TIMEOUT) -> "dict | None":
    """Probe one RTSP URL through go2rtc, returning its parsed codec/resolution.

    Mirrors ``_preview_rtsp``'s probe (a temp stream, always cleaned up) but
    hands back the parse result instead of a frame, so the auto-finder can rank
    candidates. Returns None when go2rtc could not read a stream. The temp
    stream name is unique per call so parallel-looking cleanups never collide.
    """
    src = go2rtc._stream_src(
        {"username": username, "password": password} if username else {"id": "find"},
        url)
    import secrets as _secrets
    test_name = f"glancecam_find_{_secrets.token_hex(3)}"
    result = None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.put(f"{go2rtc._base()}/api/streams",
                             params={"name": test_name, "src": src})
            # Force the producer to connect (see _preview_rtsp): without a
            # consumer go2rtc never dials the camera and the probe sees an
            # empty stream, so every candidate would look dead.
            try:
                await client.get(f"{go2rtc._base()}/api/frame.jpeg",
                                 params={"src": test_name})
            except (httpx.HTTPError, OSError):
                pass
            result = await go2rtc.probe(test_name)
    except (httpx.HTTPError, OSError):
        result = None
    finally:
        try:
            async with httpx.AsyncClient(timeout=go2rtc._TIMEOUT) as client:
                await client.delete(f"{go2rtc._base()}/api/streams",
                                    params={"src": test_name})
        except (httpx.HTTPError, OSError):
            pass
    return result


@router.get("/stream-paths")
async def stream_paths(host: str = "", hint: str = ""):
    """Every brand's default stream addresses for ``host``, hinted brand first.

    Feeds the preview candidate dropdown so it always lists a main and sub
    address for each major brand, whether or not a scan detected the brand. When
    ``host`` is blank the addresses come back with an empty list.
    """
    return {"ok": True, "hint": hint or None,
            "candidates": streampaths.candidate_urls(host.strip(), hint or None)}


@router.post("/find-stream")
async def find_stream(request: Request):
    """Try the common stream paths for a host and return the first that works.

    Builds the brand candidate list (hinted by an explicit ``hint`` or inferred
    from the reported open ``ports``), then probes each candidate's main URL
    through go2rtc in turn, stopping at the first that decodes. The paired sub
    URL is probed best effort. SSRF fail-closed on the host; credentials (typed
    or a saved set) are used only to reach the device and never echoed.
    """
    data = await _payload(request)
    host = (data.get("host") or data.get("ip") or "").strip()
    if not host:
        return {"ok": False, "error": "No camera address given.", "tried": 0}
    if netguard.is_blocked_fetch_host(host, fail_closed=True):
        return {"ok": False, "error": netguard.BLOCKED_HOST_MESSAGE, "tried": 0}

    hint = data.get("hint") or streampaths.likely_brand(data.get("ports"))
    username, password = _resolve_creds(data)
    candidates = streampaths.candidate_urls(host, hint)[:_FIND_MAX]

    tried = 0
    for cand in candidates:
        main_url = cand.get("main_url")
        if not main_url:
            continue
        tried += 1
        result = await _probe_rtsp_url(main_url, username, password)
        if not (result and (result.get("resolution") or result.get("codec"))):
            continue
        out = {"ok": True, "brand": cand.get("brand"), "label": cand.get("label"),
               "main_url": main_url}
        if result.get("resolution"):
            out["resolution"] = result["resolution"]
        sub_url = cand.get("sub_url")
        if sub_url:
            sub = await _probe_rtsp_url(sub_url, username, password)
            if sub and (sub.get("resolution") or sub.get("codec")):
                out["sub_url"] = sub_url
        return out

    return {"ok": False, "tried": tried,
            "error": "None of the common stream addresses answered. Pick one by "
                     "hand from the list, or check the login."}


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
    username, password = _resolve_creds(data)
    if kind == "image":
        return await _preview_image(url, username, password)
    return await _preview_rtsp(url, username, password)
