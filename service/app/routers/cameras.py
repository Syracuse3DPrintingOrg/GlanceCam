"""Camera CRUD API. Responses never carry credentials (public_view)."""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..services import cameras as store
from ..services import credentials
from ..services import go2rtc
from ..services import netguard

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


async def _backfill_soon(camera: dict) -> None:
    """After a save, give go2rtc a moment to connect the stream, then learn and
    store its codec/resolution. Fire-and-forget so the save response is instant;
    any failure is swallowed by ``backfill_camera``."""
    await asyncio.sleep(3)
    await go2rtc.backfill_camera(camera)


async def _payload(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        data = dict(await request.form())
    return data if isinstance(data, dict) else {}


def _apply_credential(data: dict) -> dict:
    """Fill username/password from a saved set when ``credential_id`` is given.

    A typed username/password already in the payload wins, so a saved set only
    fills what was left blank. ``credential_id`` is not a camera field, so it is
    stripped here and never persisted.
    """
    cred_id = data.pop("credential_id", None)
    resolved = credentials.resolve(cred_id or "")
    if resolved is not None:
        user, pw = resolved
        if not data.get("username"):
            data["username"] = user
        if not data.get("password"):
            data["password"] = pw
    return data


def _url_host(url: str) -> str:
    """The host of a stream/snapshot URL, lowercased, or empty for a bad URL.

    Pure and side-effect free so it is unit tested on its own. Feeds
    ``GET /api/cameras/urls`` so the discovery UI can match a scanned device to
    a camera already on the grid without ever seeing a password.
    """
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


@router.get("")
async def list_all():
    return [store.public_view(c) for c in store.list_cameras()]


@router.get("/urls")
async def list_urls():
    """A credential-free map of what is already on the grid, for discovery.

    The add-camera flow marks a scanned device as "already added" by matching
    its host (and path) or Home Assistant entity against this list. Only
    ``id``, ``host``, ``main_url`` and ``ha_entity`` come back, so no username,
    password, or other field is exposed to the browser.
    """
    out = []
    for cam in store.list_cameras():
        main_url = cam.get("main_url") or ""
        out.append({
            "id": cam.get("id"),
            "host": _url_host(main_url),
            "main_url": main_url or None,
            "ha_entity": cam.get("ha_entity"),
        })
    return out


@router.post("")
async def create(request: Request):
    data = _apply_credential(await _payload(request))
    try:
        cam = store.add(data)
    except store.CameraError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    await go2rtc.sync_camera(cam)
    asyncio.create_task(_backfill_soon(cam))
    return store.public_view(cam)


@router.patch("/{camera_id}")
async def edit(camera_id: str, request: Request):
    data = _apply_credential(await _payload(request))
    try:
        cam = store.update(camera_id, data)
    except store.CameraError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    if cam is None:
        return JSONResponse({"detail": "No such camera."}, status_code=404)
    await go2rtc.sync_camera(cam)
    asyncio.create_task(_backfill_soon(cam))
    return store.public_view(cam)


@router.delete("/{camera_id}")
async def delete(camera_id: str):
    if not store.remove(camera_id):
        return JSONResponse({"detail": "No such camera."}, status_code=404)
    await go2rtc.remove_camera(camera_id)
    return {"ok": True}


@router.post("/reorder")
async def reorder(request: Request):
    data = await _payload(request)
    ids = data.get("ids")
    if not isinstance(ids, list):
        return JSONResponse({"detail": "Expected an 'ids' list."}, status_code=400)
    cams = store.reorder([str(i) for i in ids])
    return [store.public_view(c) for c in cams]


@router.post("/test")
async def test(request: Request):
    """Probe a URL (or camera fields) WITHOUT saving, SSRF fail-closed.

    Adds a temporary go2rtc stream, probes it for a resolution, then removes it.
    Returns ``{ok, resolution?, error?}``.
    """
    data = _apply_credential(await _payload(request))
    url = (data.get("url") or data.get("main_url") or "").strip()
    if not url:
        return {"ok": False, "error": "Enter a camera address to test."}

    # A test URL is arbitrary user input, so refuse an internal/unresolvable
    # target (fail closed) before handing it to go2rtc.
    if netguard.guard_url(url, fail_closed=True):
        return {"ok": False, "error": netguard.BLOCKED_HOST_MESSAGE}

    src = go2rtc._stream_src(data if data.get("username") else {"id": "test"}, url)
    test_name = "glancecam_test_probe"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=go2rtc._TIMEOUT) as client:
            await client.put(f"{go2rtc._base()}/api/streams",
                             params={"name": test_name, "src": src})
        result = await go2rtc.probe(test_name)
    except (Exception,):  # noqa: BLE001 - a failed probe is a normal outcome here
        result = None
    finally:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=go2rtc._TIMEOUT) as client:
                await client.delete(f"{go2rtc._base()}/api/streams",
                                    params={"src": test_name})
        except Exception:  # noqa: BLE001
            pass

    if result and result.get("resolution"):
        return {"ok": True, "resolution": result["resolution"],
                "codec": result.get("codec")}
    return {"ok": False, "error": "Could not read a stream from that address."}
