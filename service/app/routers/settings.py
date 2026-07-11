"""Settings pages plus the settings-save and login/logout API.

The auth gate itself lives in main.py middleware; these routes just render the
pages and accept the saves. ``/api/settings`` applies only the user-facing
fields present in the payload (exclude-unset semantics), so a per-section Save
posts just its own fields without clobbering the rest.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import settings
from ..passwords import verify_secret
from ..templating import templates, base_context

router = APIRouter(tags=["settings"])

# The settings keys the settings page is allowed to write. secret_key and the
# camera list are managed elsewhere, so they are not accepted here.
_USER_SETTABLE = {
    "app_name",
    "theme",
    "fullscreen_uses_main",
    "snapshot_refresh_seconds",
    "kiosk_rotation",
    "is_pi",
    "settings_password",
    "go2rtc_url",
    "go2rtc_public_path",
}


@router.get("/settings")
async def settings_page(request: Request):
    ctx = base_context(request)
    ctx["settings"] = settings
    return templates.TemplateResponse("settings.html", ctx)


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", base_context(request))


@router.post("/api/settings")
async def save_settings(request: Request):
    """Persist the user-facing settings fields present in the payload."""
    try:
        payload = await request.json()
    except ValueError:
        form = await request.form()
        payload = dict(form)
    if not isinstance(payload, dict):
        return JSONResponse({"detail": "Invalid settings payload."}, status_code=400)

    data = {k: v for k, v in payload.items() if k in _USER_SETTABLE}
    # An empty password field means "leave the password as it is", never "clear
    # it": clearing is a distinct explicit action, not an accidental blank save.
    if "settings_password" in data and data["settings_password"] == "":
        data.pop("settings_password")
    # Coerce the checkbox/number fields that arrive as strings from a plain form.
    for bool_key in ("fullscreen_uses_main", "is_pi"):
        if bool_key in data:
            data[bool_key] = str(data[bool_key]).lower() in ("1", "true", "on", "yes")
    for int_key in ("snapshot_refresh_seconds", "kiosk_rotation"):
        if int_key in data:
            try:
                data[int_key] = int(data[int_key])
            except (TypeError, ValueError):
                data.pop(int_key)

    settings.save(data)
    return {"ok": True}


@router.post("/api/login")
async def login(request: Request):
    try:
        payload = await request.json()
    except ValueError:
        form = await request.form()
        payload = dict(form)
    password = str(payload.get("password", "")) if isinstance(payload, dict) else ""
    if not settings.settings_password:
        # No password configured: nothing to log into. Treat as already open.
        request.session["settings_auth"] = True
        return {"ok": True}
    if verify_secret(password, settings.settings_password):
        request.session["settings_auth"] = True
        return {"ok": True}
    return JSONResponse({"ok": False, "detail": "That password did not match."},
                        status_code=401)


@router.post("/api/logout")
async def logout(request: Request):
    request.session.pop("settings_auth", None)
    return {"ok": True}
