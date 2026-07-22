"""The on-screen Home Assistant event ring, the camera resolver, and the
private-origin webhook guard. Pure logic plus a couple of endpoint checks that
simulate the caller's address, so nothing here needs a live Home Assistant.
"""
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.routers.events import is_private_origin, resolve_camera
from app.services import ha_events


@pytest.fixture
def evstore(data_dir, monkeypatch):
    """A fresh, isolated event ring: the conftest data_dir fixture repoints
    settings.data_dir at a tmp dir; this also clears the ha_events store cache so
    it rebinds there."""
    monkeypatch.setattr(ha_events, "_store", None)
    monkeypatch.setattr(ha_events, "_store_path", None)
    return data_dir


# ---- Pure normalizers ------------------------------------------------------

def test_normalize_level_keeps_valid():
    for lvl in ("info", "success", "warning", "error"):
        assert ha_events.normalize_level(lvl) == lvl


def test_normalize_level_defaults_and_cases():
    assert ha_events.normalize_level("WARNING") == "warning"
    assert ha_events.normalize_level(" Error ") == "error"
    assert ha_events.normalize_level("nonsense") == "info"
    assert ha_events.normalize_level("") == "info"
    assert ha_events.normalize_level(None) == "info"


def test_clamp_seconds():
    assert ha_events.clamp_seconds(20) == 20
    assert ha_events.clamp_seconds(-5) == 0
    assert ha_events.clamp_seconds("12") == 12
    assert ha_events.clamp_seconds("abc") == 0
    assert ha_events.clamp_seconds(None) == 0


# ---- Prune: count cap and age cutoff (synthetic timestamps, no wall clock) --

def test_prune_drops_events_past_ttl():
    now = 1000.0
    events = [
        {"id": 1, "ts": now - 200},   # older than the 120s TTL: dropped
        {"id": 2, "ts": now - 10},    # fresh: kept
    ]
    kept = ha_events._prune(events, now)
    assert [e["id"] for e in kept] == [2]


def test_prune_caps_to_max_events():
    now = 1000.0
    events = [{"id": i, "ts": now} for i in range(1, 80)]
    kept = ha_events._prune(events, now)
    assert len(kept) == ha_events._MAX_EVENTS
    # The newest survive: the last id is present, the earliest is gone.
    assert kept[-1]["id"] == 79
    assert kept[0]["id"] == 79 - ha_events._MAX_EVENTS + 1


# ---- Ring add / poll -------------------------------------------------------

def test_add_camera_and_notify_assign_increasing_ids(evstore):
    a = ha_events.add_notify("hello", level="warning")
    b = ha_events.add_camera_popup("cam_1", seconds=15, name="Front Door")
    assert b == a + 1
    out = ha_events.poll(0)
    assert out["last_id"] == b
    types = {e["type"] for e in out["events"]}
    assert types == {"notify", "camera"}
    cam = next(e for e in out["events"] if e["type"] == "camera")
    assert cam["camera_id"] == "cam_1"
    assert cam["name"] == "Front Door"
    assert cam["seconds"] == 15
    note = next(e for e in out["events"] if e["type"] == "notify")
    assert note["level"] == "warning"
    assert note["message"] == "hello"


def test_poll_since_filters_to_newer_events(evstore):
    ha_events.add_notify("one")
    second = ha_events.add_notify("two")
    third = ha_events.add_notify("three")
    fresh = ha_events.poll(second)["events"]
    assert [e["id"] for e in fresh] == [third]


def test_poll_empty_ring_reports_zero(evstore):
    out = ha_events.poll(0)
    assert out == {"events": [], "last_id": 0}


def test_add_count_cap_holds_through_public_path(evstore):
    for i in range(ha_events._MAX_EVENTS + 12):
        ha_events.add_notify(f"n{i}")
    out = ha_events.poll(0)
    assert len(out["events"]) == ha_events._MAX_EVENTS
    # last_id keeps counting past the cap, so since-filtering never replays.
    assert out["last_id"] == ha_events._MAX_EVENTS + 12


# ---- Private-origin guard --------------------------------------------------

def test_is_private_origin_loopback_and_lan():
    assert is_private_origin("127.0.0.1")
    assert is_private_origin("::1")
    assert is_private_origin("192.168.1.50")
    assert is_private_origin("10.0.0.5")
    assert is_private_origin("172.16.4.4")


def test_is_private_origin_rejects_public_and_junk():
    assert not is_private_origin("8.8.8.8")
    assert not is_private_origin("1.1.1.1")
    assert not is_private_origin("")
    assert not is_private_origin("not-an-ip")
    assert not is_private_origin("testclient")   # Starlette's default test host


def test_is_private_origin_ipv4_mapped_loopback():
    assert is_private_origin("::ffff:127.0.0.1")


# ---- Camera resolver -------------------------------------------------------

def test_resolve_camera_by_id_name_and_fallback(data_dir):
    from app.services import cameras as camera_store
    a = camera_store.add({"name": "Front Door", "main_url": "rtsp://192.168.1.10/s"})
    camera_store.add({"name": "Back Yard", "main_url": "rtsp://192.168.1.11/s"})

    # Exact id.
    assert resolve_camera(a["id"]) == (a["id"], "Front Door")
    # Case-insensitive name.
    assert resolve_camera("front door") == (a["id"], "Front Door")
    # Empty token falls back to the first enabled camera.
    assert resolve_camera("")[1] == "Front Door"
    # Unknown token: no match.
    assert resolve_camera("nope") is None


def test_resolve_camera_none_when_no_cameras(data_dir):
    assert resolve_camera("anything") is None


# ---- Endpoint guard + flow (client address simulated) ----------------------

def test_camera_popup_refused_from_wan(evstore):
    client = TestClient(app, client=("8.8.8.8", 5000))
    r = client.post("/events/camera-popup", json={"camera": "x"})
    assert r.status_code == 403
    assert r.json()["ok"] is False


def test_camera_popup_from_lan_pops_and_polls(evstore):
    from app.services import cameras as camera_store
    cam = camera_store.add({"name": "Porch", "main_url": "rtsp://192.168.1.20/s"})

    client = TestClient(app, client=("192.168.1.9", 5000))
    r = client.post("/events/camera-popup", json={"camera": "Porch", "seconds": 8})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["camera"] == cam["id"]

    poll = client.get("/events/poll?since=0").json()
    ev = next(e for e in poll["events"] if e["type"] == "camera")
    assert ev["camera_id"] == cam["id"]
    assert ev["seconds"] == 8


def test_camera_popup_accepts_fractional_seconds(evstore):
    # The Stream Deck sends a float duration; the endpoint must accept it (not
    # 422) and round it to whole seconds rather than silently drop the pop-up.
    from app.services import cameras as camera_store
    cam = camera_store.add({"name": "Gate", "main_url": "rtsp://192.168.1.21/s"})
    client = TestClient(app, client=("127.0.0.1", 5000))
    r = client.post("/events/camera-popup", json={"camera": "Gate", "seconds": 12.5})
    assert r.status_code == 200 and r.json()["ok"] is True
    ev = next(e for e in client.get("/events/poll?since=0").json()["events"]
              if e["type"] == "camera" and e["camera_id"] == cam["id"])
    assert ev["seconds"] == 12


def test_notify_requires_message(evstore):
    client = TestClient(app, client=("127.0.0.1", 5000))
    assert client.post("/events/notify", json={"message": "  "}).json()["ok"] is False
    ok = client.post("/events/notify", json={"message": "hi", "level": "success"}).json()
    assert ok["ok"] is True
