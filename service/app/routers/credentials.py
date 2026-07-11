"""Saved credential-set API. Passwords never come back in a response.

Reads (``GET``) are open: a listing only ever carries the masked sentinel, no
real password. Mutations (``POST``/``DELETE``) are gated by the settings-auth
middleware in main.py like every other ``/api/*`` write.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..services import credentials as store

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


async def _payload(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        data = dict(await request.form())
    return data if isinstance(data, dict) else {}


@router.get("")
async def list_all():
    return store.list_public()


@router.post("")
async def create(request: Request):
    data = await _payload(request)
    try:
        entry = store.add(data.get("name") or "", data.get("username") or "",
                          data.get("password") or "")
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return entry


@router.delete("/{cred_id}")
async def delete(cred_id: str):
    if not store.remove(cred_id):
        return JSONResponse({"detail": "No such credential set."}, status_code=404)
    return {"ok": True}
