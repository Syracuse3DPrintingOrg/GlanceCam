"""Shared Jinja2 templates instance and the base render context.

Every page renders with ``base_context(request)`` merged in, so base.html can
read the app version, the display/kiosk settings, and whether the settings area
is currently locked, without each router rebuilding it.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import settings, APP_NAME, APP_VERSION

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def settings_locked(request: Request) -> bool:
    """True when the settings area requires a login for this request.

    A password must be set, and the client must be neither logged in this
    session nor on loopback (the local kiosk is always trusted).
    """
    if not settings.settings_password:
        return False
    if request.session.get("settings_auth"):
        return False
    client = request.client.host if request.client else ""
    if client in ("127.0.0.1", "::1"):
        return False
    return True


def base_context(request: Request) -> dict:
    """Context shared by every template render."""
    return {
        "request": request,
        "app_name": settings.app_name or APP_NAME,
        "app_version": APP_VERSION,
        "theme": settings.theme,
        "is_pi": settings.is_pi_effective(),
        "kiosk_rotation": settings.kiosk_rotation,
        "fullscreen_uses_main": settings.fullscreen_uses_main,
        "snapshot_refresh_seconds": settings.snapshot_refresh_seconds,
        "go2rtc_public_path": settings.go2rtc_public_path,
        "settings_locked": settings_locked(request),
        "settings_password_set": bool(settings.settings_password),
    }
