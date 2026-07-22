"""HTTP client for the GlanceCam app and the pure snapshot cache.

The controller only ever talks to the GlanceCam app, never to a camera directly:
``list_cameras`` reads ``GET /api/cameras`` and ``fetch_snapshot`` reads the
server-side proxy at ``GET /cam/{id}/snapshot``, which fetches the still with the
stored credentials and sizes it to the camera. That keeps every camera password
on the server.

``SnapshotCache`` and the ``usable_cameras`` / ``camera_ids`` helpers are pure,
so the redundant-write skip and the enabled/order filtering are unit tested with
plain dicts and byte strings, no network.
"""
from __future__ import annotations

import hashlib
from typing import Optional

import httpx


class SnapshotCache:
    """Latest snapshot bytes per camera, with a change check for USB-write skip.

    ``update`` stores a new frame and returns whether it differs from the last
    one seen for that camera (a new camera counts as changed). The draw loop uses
    that to avoid re-encoding and re-pushing a key whose picture has not moved, so
    a still scene does not churn the USB bus every poll. ``get`` returns the last
    good frame, so a transient fetch failure keeps showing the previous picture
    rather than blanking the key.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, tuple[str, bytes]] = {}

    @staticmethod
    def digest(data: bytes) -> str:
        """A stable content hash for snapshot bytes (process-independent)."""
        return hashlib.sha1(data).hexdigest()

    def update(self, camera_id: str, data: Optional[bytes]) -> bool:
        """Store ``data`` for ``camera_id``; return True when the frame changed.

        Empty or missing bytes are ignored (the previous frame is kept) and
        report no change, so a failed fetch never clears a good picture.
        """
        if not data:
            return False
        new_digest = self.digest(data)
        prev = self._by_id.get(camera_id)
        self._by_id[camera_id] = (new_digest, data)
        return prev is None or prev[0] != new_digest

    def get(self, camera_id: str) -> Optional[bytes]:
        entry = self._by_id.get(camera_id)
        return entry[1] if entry else None

    def digest_of(self, camera_id: str) -> Optional[str]:
        entry = self._by_id.get(camera_id)
        return entry[0] if entry else None


def usable_cameras(cameras: list[dict]) -> list[dict]:
    """Enabled cameras in their saved order.

    A camera without an explicit ``enabled`` value is treated as enabled (the
    store defaults it to True). Sorted by the ``order`` field so the deck matches
    the grid's own ordering.
    """
    enabled = [c for c in cameras if isinstance(c, dict) and c.get("enabled", True)]
    return sorted(enabled, key=lambda c: c.get("order", 0))


def camera_ids(cameras: list[dict]) -> list[str]:
    """The ids of the usable cameras, in order."""
    return [str(c.get("id")) for c in usable_cameras(cameras) if c.get("id")]


class GlanceCamClient:
    """Thin async wrapper over the two GlanceCam endpoints the deck needs."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)

    async def __aenter__(self) -> "GlanceCamClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_cameras(self) -> list[dict]:
        """Every camera the app knows, or an empty list when it is unreachable."""
        try:
            r = await self._client.get(f"{self.base_url}/api/cameras")
            if r.status_code == 200:
                data = r.json()
                return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []
        except (httpx.HTTPError, ValueError):
            pass
        return []

    async def show_camera(self, camera_id: str, seconds: float = 0) -> bool:
        """Ask the kiosk display to open this camera full screen.

        Posts to the app's on-screen event channel (the same one Home
        Assistant pop-ups use), so a deck press brings the camera up on the
        attached display. Returns True when the app accepted it. The endpoint
        only trusts private/loopback callers, which the deck always is.
        """
        if not camera_id:
            return False
        try:
            r = await self._client.post(
                f"{self.base_url}/events/camera-popup",
                json={"camera": camera_id, "seconds": seconds})
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def fetch_snapshot(self, camera_id: str) -> Optional[bytes]:
        """A single still for a camera via the server-side proxy, or None.

        The app proxies the camera's own snapshot URL (with credentials) and
        falls back to a go2rtc frame, so the deck gets a credential-safe JPEG
        sized to the camera. Any network or service error returns None so the
        caller keeps the last good frame.
        """
        if not camera_id:
            return None
        try:
            r = await self._client.get(f"{self.base_url}/cam/{camera_id}/snapshot")
            if r.status_code == 200 and r.content:
                return r.content
        except httpx.HTTPError:
            pass
        return None
