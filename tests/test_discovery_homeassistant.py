"""Home Assistant camera discovery: pure state parsing and guarded listing."""
from app.services.discovery import homeassistant


_STATES = [
    {"entity_id": "camera.front_door",
     "attributes": {"friendly_name": "Front Door"}},
    {"entity_id": "sensor.temperature", "attributes": {}},
    {"entity_id": "camera.backyard", "attributes": {}},
    "not-a-dict",
]


def test_parse_camera_states_filters_and_names():
    props = homeassistant.parse_camera_states(_STATES)
    names = [p["name"] for p in props]
    # Only the two camera.* entities, sorted by name.
    assert names == ["Backyard", "Front Door"]
    assert all(p["source"] == "homeassistant" for p in props)


def test_parse_camera_states_derives_name_from_entity():
    props = homeassistant.parse_camera_states(
        [{"entity_id": "camera.backyard", "attributes": {}}])
    assert props[0]["name"] == "Backyard"
    assert props[0]["ha_entity"] == "camera.backyard"


def test_parse_camera_states_notes_snapshot_only():
    props = homeassistant.parse_camera_states(_STATES)
    assert "snapshot" in props[0]["notes"].lower()
    assert props[0]["main_url"] is None


def test_parse_camera_states_empty():
    assert homeassistant.parse_camera_states(None) == []
    assert homeassistant.parse_camera_states([]) == []


def test_list_cameras_requires_url_and_token():
    out = homeassistant.list_cameras("", "")
    assert out["ok"] is False
    out2 = homeassistant.list_cameras("http://192.168.1.10:8123", "")
    assert out2["ok"] is False


def test_list_cameras_blocks_loopback():
    out = homeassistant.list_cameras("http://127.0.0.1:8123", "token")
    assert out["ok"] is False
    # The SSRF guard message, not a connection error.
    assert "internal address" in out["error"] or "device" in out["error"]
