"""GET /api/cameras/urls: the credential-free already-added map for discovery."""
import asyncio

from app.routers import cameras as cameras_router
from app.services import cameras as store


def _run(coro):
    return asyncio.run(coro)


def test_url_host_parses_and_lowercases():
    assert cameras_router._url_host("rtsp://192.168.1.50:554/stream1") == "192.168.1.50"
    assert cameras_router._url_host("http://Cam.Local/snap.jpg") == "cam.local"
    assert cameras_router._url_host("") == ""
    assert cameras_router._url_host("not a url") == ""


def test_list_urls_exposes_no_credentials(data_dir):
    store.add({"name": "Front", "main_url": "rtsp://10.0.0.5:554/h264Preview_01_main",
               "username": "admin", "password": "secret"})
    store.add({"name": "HA Cam", "source": "homeassistant",
               "ha_entity": "camera.driveway"})

    rows = _run(cameras_router.list_urls())
    assert len(rows) == 2
    by_name = {r["host"]: r for r in rows}
    assert "10.0.0.5" in by_name
    front = by_name["10.0.0.5"]
    assert front["main_url"] == "rtsp://10.0.0.5:554/h264Preview_01_main"
    assert front["ha_entity"] is None
    # Only the four whitelisted keys, and never a secret.
    for row in rows:
        assert set(row.keys()) == {"id", "host", "main_url", "ha_entity"}
        assert "password" not in row and "username" not in row

    ha = next(r for r in rows if r["ha_entity"] == "camera.driveway")
    assert ha["host"] == ""
    assert ha["main_url"] is None
