"""Pure client helpers: the snapshot cache and camera filtering/ordering."""
from __future__ import annotations

import sys
from pathlib import Path

_SD = Path(__file__).resolve().parent.parent / "streamdeck"
if str(_SD) not in sys.path:
    sys.path.insert(0, str(_SD))

from glancecam_streamdeck import client  # noqa: E402


def test_snapshot_cache_reports_new_and_changed_frames():
    cache = client.SnapshotCache()
    # A first frame for a camera is a change.
    assert cache.update("cam_a", b"frame-1") is True
    # The same bytes again is not a change (skip the USB write).
    assert cache.update("cam_a", b"frame-1") is False
    # New bytes are a change.
    assert cache.update("cam_a", b"frame-2") is True
    assert cache.get("cam_a") == b"frame-2"


def test_snapshot_cache_ignores_empty_data():
    cache = client.SnapshotCache()
    cache.update("cam_a", b"good")
    # An empty/failed fetch does not change or clear the last good frame.
    assert cache.update("cam_a", b"") is False
    assert cache.update("cam_a", None) is False
    assert cache.get("cam_a") == b"good"


def test_snapshot_cache_digest_is_stable_and_content_based():
    d1 = client.SnapshotCache.digest(b"abc")
    d2 = client.SnapshotCache.digest(b"abc")
    d3 = client.SnapshotCache.digest(b"abd")
    assert d1 == d2
    assert d1 != d3


def test_digest_of_tracks_the_stored_frame():
    cache = client.SnapshotCache()
    assert cache.digest_of("cam_a") is None
    cache.update("cam_a", b"xyz")
    assert cache.digest_of("cam_a") == client.SnapshotCache.digest(b"xyz")


def test_usable_cameras_filters_disabled_and_sorts_by_order():
    cameras = [
        {"id": "c2", "order": 2, "enabled": True},
        {"id": "c0", "order": 0},  # no enabled key -> treated as enabled
        {"id": "cx", "order": 1, "enabled": False},
        {"id": "c1", "order": 3, "enabled": True},
    ]
    usable = client.usable_cameras(cameras)
    assert [c["id"] for c in usable] == ["c0", "c2", "c1"]


def test_camera_ids_skips_entries_without_id():
    cameras = [
        {"id": "c0", "order": 0, "enabled": True},
        {"order": 1, "enabled": True},  # no id -> dropped
        {"id": "c1", "order": 2, "enabled": True},
    ]
    assert client.camera_ids(cameras) == ["c0", "c1"]


def test_show_camera_posts_to_events_and_reports_status():
    """A deck press asks the app to open the camera, returning the app's ok."""
    import asyncio
    import httpx
    from glancecam_streamdeck import client as client_mod

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        import json as _json
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "id": "cam_a"})

    async def run():
        c = client_mod.GlanceCamClient("http://127.0.0.1:9292")
        c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            ok = await c.show_camera("cam_a", 30)
        finally:
            await c.aclose()
        return ok

    ok = asyncio.run(run())
    assert ok is True
    assert seen["url"].endswith("/events/camera-popup")
    assert seen["body"] == {"camera": "cam_a", "seconds": 30}


def test_show_camera_empty_id_is_a_noop():
    import asyncio
    from glancecam_streamdeck import client as client_mod
    c = client_mod.GlanceCamClient("http://127.0.0.1:9292")
    assert asyncio.run(c.show_camera("", 10)) is False
