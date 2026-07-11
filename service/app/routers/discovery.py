"""Discovery endpoints (LAN scan, ONVIF, Reolink, Home Assistant).

STUB: another agent owns this router in a later wave. It is mounted now so the
route namespace and the app wiring are stable. The design calls for a simple
polling job model (``POST /api/discovery/scan`` starts a job, ``GET
/api/discovery/scan/{job}`` polls it, one job at a time in a module-level dict).
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


@router.get("/status")
async def discovery_status() -> dict:
    # TODO(discovery agent): real scan job model and per-protocol probes.
    return {"available": False, "detail": "Discovery is not implemented yet."}
