"""System info: version, hardware class, go2rtc health, stream budget."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

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
async def system_budget(cores: Optional[int] = None,
                        width: Optional[int] = None,
                        height: Optional[int] = None) -> dict:
    """The live-tile budget for the calling surface.

    A remote browser passes its ``navigator.hardwareConcurrency`` and viewport
    as query hints; the kiosk (loopback) can omit them and the host hardware is
    used. The real math is owned by the resources agent.
    """
    client_hint = {"cores": cores, "width": width, "height": height}
    return resources.budget(client_hint=client_hint)
