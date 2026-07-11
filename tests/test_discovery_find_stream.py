"""Auto-find endpoint: candidate ordering with a mocked go2rtc probe."""
import asyncio

import pytest

from app.routers import cameras as cameras_router
from app.routers import discovery
from app.services import credentials as cred_store


class FakeRequest:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


def _accept_only(*ok_urls):
    """A fake _probe_rtsp_url that decodes only the given URLs."""
    ok = set(ok_urls)

    async def _probe(url, username, password, timeout=0):
        if url in ok:
            return {"codec": "H264", "resolution": [1920, 1080]}
        return None
    return _probe


def _run(coro):
    return asyncio.run(coro)


def test_find_stream_returns_first_working_candidate(monkeypatch):
    monkeypatch.setattr(discovery.netguard, "is_blocked_fetch_host",
                        lambda host, fail_closed=False: False)
    dahua_main = "rtsp://10.0.0.9:554/cam/realmonitor?channel=1&subtype=0"
    monkeypatch.setattr(discovery, "_probe_rtsp_url", _accept_only(dahua_main))

    res = _run(discovery.find_stream(FakeRequest({"host": "10.0.0.9"})))
    assert res["ok"] is True
    assert res["brand"] == "dahua"
    assert res["main_url"] == dahua_main
    assert res["resolution"] == [1920, 1080]


def test_find_stream_hint_tries_that_brand_first(monkeypatch):
    monkeypatch.setattr(discovery.netguard, "is_blocked_fetch_host",
                        lambda host, fail_closed=False: False)
    calls = []

    async def _probe(url, username, password, timeout=0):
        calls.append(url)
        if "Streaming/Channels/101" in url:
            return {"codec": "H264", "resolution": [1280, 720]}
        return None
    monkeypatch.setattr(discovery, "_probe_rtsp_url", _probe)

    res = _run(discovery.find_stream(
        FakeRequest({"host": "10.0.0.9", "hint": "hikvision"})))
    assert res["ok"] is True
    assert res["brand"] == "hikvision"
    # The hinted brand's main URL was the very first thing tried.
    assert "Streaming/Channels/101" in calls[0]


def test_find_stream_pairs_sub_when_it_decodes(monkeypatch):
    monkeypatch.setattr(discovery.netguard, "is_blocked_fetch_host",
                        lambda host, fail_closed=False: False)
    main = "rtsp://10.0.0.9:554/h264Preview_01_main"
    sub = "rtsp://10.0.0.9:554/h264Preview_01_sub"
    monkeypatch.setattr(discovery, "_probe_rtsp_url", _accept_only(main, sub))

    res = _run(discovery.find_stream(FakeRequest({"host": "10.0.0.9"})))
    assert res["brand"] == "reolink"
    assert res["sub_url"] == sub


def test_find_stream_all_fail_reports_tried(monkeypatch):
    monkeypatch.setattr(discovery.netguard, "is_blocked_fetch_host",
                        lambda host, fail_closed=False: False)
    monkeypatch.setattr(discovery, "_probe_rtsp_url", _accept_only())

    res = _run(discovery.find_stream(FakeRequest({"host": "10.0.0.9"})))
    assert res["ok"] is False
    assert res["tried"] > 0
    assert "error" in res


def test_find_stream_blocked_host_is_clean(monkeypatch):
    monkeypatch.setattr(discovery.netguard, "is_blocked_fetch_host",
                        lambda host, fail_closed=False: True)
    res = _run(discovery.find_stream(FakeRequest({"host": "127.0.0.1"})))
    assert res["ok"] is False
    assert res["tried"] == 0


def test_find_stream_uses_saved_credential(monkeypatch, data_dir):
    monkeypatch.setattr(discovery.netguard, "is_blocked_fetch_host",
                        lambda host, fail_closed=False: False)
    saved = cred_store.add("NVR", "viewer", "secret")
    seen = {}

    async def _probe(url, username, password, timeout=0):
        seen["creds"] = (username, password)
        return {"codec": "H264", "resolution": [1, 1]}
    monkeypatch.setattr(discovery, "_probe_rtsp_url", _probe)

    res = _run(discovery.find_stream(
        FakeRequest({"host": "10.0.0.9", "credential_id": saved["id"]})))
    assert res["ok"] is True
    assert seen["creds"] == ("viewer", "secret")


def test_apply_credential_fills_camera_add(data_dir):
    saved = cred_store.add("Cams", "admin", "pw")
    data = cameras_router._apply_credential(
        {"name": "Front", "main_url": "rtsp://x/y", "credential_id": saved["id"]})
    assert data["username"] == "admin"
    assert data["password"] == "pw"
    assert "credential_id" not in data  # stripped, never persisted


def test_apply_credential_typed_values_win(data_dir):
    saved = cred_store.add("Cams", "admin", "pw")
    data = cameras_router._apply_credential(
        {"name": "Front", "main_url": "rtsp://x/y", "credential_id": saved["id"],
         "username": "typed", "password": "typedpw"})
    assert data["username"] == "typed"
    assert data["password"] == "typedpw"
