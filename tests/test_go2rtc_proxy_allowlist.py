"""The /go2rtc HTTP proxy must expose only the JPEG/MJPEG frame endpoints.

go2rtc's control API (api/config, api/streams) returns every stream's source
URL with the camera's RTSP credentials embedded, so the proxy has to be an
allowlist. A regression here would hand any LAN client every camera password,
which is exactly the leak this test guards against.
"""
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize("path", [
    "api/config",
    "api/streams",
    "api/restart",
    "api",
    "",
    "api/frame.jpeg/../config",
])
def test_control_api_paths_are_refused(client, path):
    # Never proxied: a 404 from our own allowlist, never go2rtc's answer. A 502
    # (reached the proxy, go2rtc down) would mean the path slipped the gate.
    resp = client.get(f"/go2rtc/{path}")
    assert resp.status_code == 404


def test_post_is_not_allowed(client):
    # Only GET is registered now; a POST to the frame endpoint must not proxy.
    resp = client.post("/go2rtc/api/frame.jpeg")
    assert resp.status_code in (404, 405)


def test_allowed_frame_path_reaches_the_proxy(client):
    # go2rtc is not running in tests, so the allowed path gets past the gate
    # and fails trying to reach it (502), proving it is not blocked at 404.
    resp = client.get("/go2rtc/api/frame.jpeg?src=demo")
    assert resp.status_code == 502
