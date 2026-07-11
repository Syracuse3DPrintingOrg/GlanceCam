"""Camera CRUD API. Responses never carry credentials (public_view)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..services import cameras as store
from ..services import go2rtc
from ..services import netguard

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


async def _payload(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        data = dict(await request.form())
    return data if isinstance(data, dict) else {}


@router.get("")
async def list_all():
    return [store.public_view(c) for c in store.list_cameras()]


@router.post("")
async def create(request: Request):
    data = await _payload(request)
    try:
        cam = store.add(data)
    except store.CameraError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    await go2rtc.sync_camera(cam)
    return store.public_view(cam)


@router.patch("/{camera_id}")
async def edit(camera_id: str, request: Request):
    data = await _payload(request)
    try:
        cam = store.update(camera_id, data)
    except store.CameraError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    if cam is None:
        return JSONResponse({"detail": "No such camera."}, status_code=404)
    await go2rtc.sync_camera(cam)
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
    data = await _payload(request)
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
