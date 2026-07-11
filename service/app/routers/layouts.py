"""Saved grid layout API.

Reads (``GET``) are open so the grid can fetch the active layout without a
login. Mutations (``POST``/``DELETE``) are gated by the settings-auth middleware
in main.py like every other ``/api/*`` write.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..services import layouts as store

router = APIRouter(prefix="/api/layouts", tags=["layouts"])


async def _payload(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        data = dict(await request.form())
    return data if isinstance(data, dict) else {}


@router.get("")
async def list_all():
    return {"active": store.active_id(), "layouts": store.list_layouts()}


@router.post("")
async def create_or_update(request: Request):
    try:
        layout = store.save_layout(await _payload(request))
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return layout


@router.delete("/{layout_id}")
async def delete(layout_id: str):
    if not store.remove(layout_id):
        return JSONResponse({"detail": "No such layout."}, status_code=404)
    return {"ok": True}


@router.post("/active")
async def set_active(request: Request):
    data = await _payload(request)
    target = str(data.get("id") or "auto")
    try:
        active = store.set_active(target)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return {"active": active}
