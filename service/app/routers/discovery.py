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
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import settings
from ..services import netguard
from ..services.discovery import homeassistant, jobs, lanscan, onvif, reolink

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


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
