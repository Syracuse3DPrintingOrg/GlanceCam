"""System info: version, hardware class, go2rtc health, stream budget."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request

from ..config import APP_VERSION, settings
from ..services import cameras as camera_store
from ..services import go2rtc
from ..services import resources

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("")
async def system_info() -> dict:
    hw = resources.detect_hardware()
    return {
        "version": APP_VERSION,
        "is_pi": settings.is_pi_effective(),
        "hardware_class": hw["class"],
        "go2rtc_healthy": await go2rtc.healthy(),
        "camera_count": len(camera_store.list_cameras()),
    }


@router.get("/budget")
async def system_budget(request: Request,
                        cores: Optional[int] = None,
                        width: Optional[int] = None,
                        height: Optional[int] = None,
                        surface: Optional[str] = None) -> dict:
    """The live-tile budget for the calling surface.

    A remote browser passes its ``navigator.hardwareConcurrency`` and viewport
    as query hints; the kiosk (loopback) can omit them and the host hardware is
    used. A loopback caller with no explicit ``surface`` on a Pi defaults to
    the kiosk surface (no client hint), even if it happens to pass cores.
    """
    client_hint: Optional[dict] = None
    if cores is not None:
        client_hint = {"cores": cores, "width": width, "height": height}

    client_host = request.client.host if request.client else None
    is_loopback = client_host in ("127.0.0.1", "::1", "localhost")
    if surface is None and is_loopback and settings.is_pi_effective():
        client_hint = None

    cameras = [camera_store.public_view(c) for c in camera_store.list_cameras()]
    return resources.budget(cameras, client_hint=client_hint)
