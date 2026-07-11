"""GlanceCam FastAPI app: middleware, routers, the go2rtc reverse proxy.

Middleware note (Starlette runs the LAST-ADDED middleware OUTERMOST): the
optional settings-auth gate is registered with @app.middleware BEFORE
SessionMiddleware is added, so the session layer wraps it and ``request.session``
is available inside the gate. Registration order is the reverse of execution
order.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocketDisconnect

from .config import APP_VERSION, settings
from .routers import (cameras, credentials, discovery, layouts,
                      settings as settings_router, system, ui)

_log = logging.getLogger("glancecam.main")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings.reload()
    # Fire-and-forget: push the saved cameras into go2rtc's stream table so a
    # fresh go2rtc container comes up already populated. Never block startup on
    # go2rtc being reachable yet.
    async def _sync():
        try:
            from .services import cameras as store
            from .services import go2rtc
            await go2rtc.sync_all(store.list_cameras())
        except Exception as exc:  # noqa: BLE001 - startup must survive a go2rtc miss
            _log.debug("initial go2rtc sync skipped: %s", exc)

    asyncio.create_task(_sync())
    yield


app = FastAPI(title="GlanceCam", version=APP_VERSION, lifespan=_lifespan)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


# --- Settings auth gate ------------------------------------------------------
# Registered first (runs INNERMOST) so the session layer added below wraps it.
# When a settings password is set, this gates /settings and every mutating
# method on /api/* behind a session login. The live grid, the stream proxy, and
# static assets stay open (the device is never locked, only its settings).
_LOGIN_EXEMPT = {"/api/login", "/api/logout"}


def _needs_settings_auth(request: Request) -> bool:
    path = request.url.path
    if path in _LOGIN_EXEMPT:
        return False
    if path == "/settings":
        return True
    if path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        return True
    return False


@app.middleware("http")
async def require_settings_auth(request: Request, call_next):
    if not settings.settings_password:
        return await call_next(request)
    # The local kiosk (loopback) is always trusted.
    if request.client and request.client.host in ("127.0.0.1", "::1"):
        return await call_next(request)
    if not _needs_settings_auth(request):
        return await call_next(request)
    if request.session.get("settings_auth"):
        return await call_next(request)
    if request.url.path == "/settings":
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"detail": "The settings password is required."},
                        status_code=401)


# Session cookie (30 days). Added LAST so it runs OUTERMOST and request.session
# is populated before the auth gate above runs.
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key,
                   max_age=60 * 60 * 24 * 30)


# --- Routers -----------------------------------------------------------------
app.include_router(ui.router)
app.include_router(cameras.router)
app.include_router(settings_router.router)
app.include_router(system.router)
app.include_router(discovery.router)
app.include_router(credentials.router)
app.include_router(layouts.router)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# --- go2rtc reverse proxy ----------------------------------------------------
# Browsers only ever talk to the app's own origin for streams; the app forwards
# to go2rtc. This keeps one port facing the LAN and lets the settings password
# gate stream access later if needed.

def _go2rtc_ws_base() -> str:
    base = settings.go2rtc_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):]
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):]
    return base


@app.websocket("/go2rtc/api/ws")
async def go2rtc_ws(client_ws: WebSocket):
    """Bridge a browser WebSocket to go2rtc's /api/ws, relaying both ways.

    go2rtc's video-stream element opens this socket to negotiate WebRTC/MSE. We
    relay text and binary frames verbatim in both directions until either side
    closes.
    """
    await client_ws.accept()
    import websockets

    query = client_ws.url.query
    upstream_url = f"{_go2rtc_ws_base()}/api/ws"
    if query:
        upstream_url += "?" + query

    try:
        async with websockets.connect(upstream_url, max_size=None) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        msg = await client_ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if msg.get("text") is not None:
                            await upstream.send(msg["text"])
                        elif msg.get("bytes") is not None:
                            await upstream.send(msg["bytes"])
                except (WebSocketDisconnect, RuntimeError):
                    pass

            # Media frames outpace a slow tab (a busy Pi kiosk, a throttled
            # background tab). Relaying them all makes the picture drift
            # further and further behind live, so binary media queues in a
            # small buffer and the backlog is dropped when it fills: the
            # video freezes for a moment and then catches up at the next
            # keyframe, which is what a glance viewer wants. Text frames are
            # signaling and are never dropped. The first binary frames carry
            # the MSE init segment, so a handful are always let through.
            queue: asyncio.Queue = asyncio.Queue(maxsize=48)
            _DROP_MARK = object()

            async def upstream_to_queue():
                try:
                    async for message in upstream:
                        if isinstance(message, (bytes, bytearray)):
                            if queue.full():
                                try:
                                    while True:
                                        queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    pass
                            await queue.put(bytes(message))
                        else:
                            await queue.put(message)
                except Exception:  # noqa: BLE001 - upstream close ends the relay
                    pass
                await queue.put(_DROP_MARK)

            async def queue_to_client():
                try:
                    while True:
                        message = await queue.get()
                        if message is _DROP_MARK:
                            break
                        if isinstance(message, bytes):
                            await client_ws.send_bytes(message)
                        else:
                            await client_ws.send_text(message)
                except Exception:  # noqa: BLE001 - client close ends the relay
                    pass

            await asyncio.gather(client_to_upstream(), upstream_to_queue(),
                                 queue_to_client())
    except Exception as exc:  # noqa: BLE001 - a proxy failure just closes the socket
        _log.debug("go2rtc ws proxy error: %s", exc)
    finally:
        try:
            await client_ws.close()
        except RuntimeError:
            pass


@app.api_route("/go2rtc/{path:path}", methods=["GET", "POST"])
async def go2rtc_http(path: str, request: Request):
    """Plain HTTP proxy for go2rtc (frames, MJPEG, the API and web assets)."""
    target = f"{settings.go2rtc_url.rstrip('/')}/{path}"
    if request.url.query:
        target += "?" + request.url.query
    body = await request.body()
    # Drop hop-by-hop headers; forward the rest so range requests etc. work.
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "connection", "content-length")}

    client = httpx.AsyncClient(timeout=None)
    try:
        upstream_req = client.build_request(request.method, target,
                                            headers=headers, content=body)
        upstream = await client.send(upstream_req, stream=True)
    except (httpx.HTTPError, OSError) as exc:
        await client.aclose()
        return JSONResponse({"detail": f"go2rtc is unreachable: {exc}"},
                            status_code=502)

    async def body_iter():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    resp_headers = {k: v for k, v in upstream.headers.items()
                    if k.lower() not in ("content-length", "transfer-encoding",
                                         "connection")}
    return StreamingResponse(body_iter(), status_code=upstream.status_code,
                             headers=resp_headers,
                             media_type=upstream.headers.get("content-type"))


# --- Health ------------------------------------------------------------------
@app.get("/health")
async def health():
    from .services import go2rtc
    return {
        "status": "ok",
        "app": "glancecam",
        "version": APP_VERSION,
        "go2rtc": "ok" if await go2rtc.healthy() else "error",
    }
