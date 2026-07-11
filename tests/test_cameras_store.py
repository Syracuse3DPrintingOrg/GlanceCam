import pytest

from app.services import cameras as store


def test_add_generates_id_and_order(data_dir):
    a = store.add({"name": "Front", "main_url": "rtsp://192.168.1.5/main"})
    b = store.add({"name": "Back", "main_url": "rtsp://192.168.1.6/main"})
    assert a["id"].startswith("cam_") and len(a["id"]) == 10  # cam_ + 6 hex
    assert a["id"] != b["id"]
    assert a["order"] == 0
    assert b["order"] == 1


def test_roundtrip_persists(data_dir):
    store.add({"name": "Front", "main_url": "rtsp://192.168.1.5/main"})
    cams = store.list_cameras()
    assert len(cams) == 1
    assert cams[0]["name"] == "Front"


def test_name_required(data_dir):
    with pytest.raises(store.CameraError):
        store.add({"main_url": "rtsp://192.168.1.5/main"})


def test_main_url_required_for_manual(data_dir):
    with pytest.raises(store.CameraError):
        store.add({"name": "No URL"})


def test_ha_source_allows_missing_main_url(data_dir):
    cam = store.add({"name": "HA Cam", "source": "homeassistant",
                     "ha_entity": "camera.front"})
    assert cam["source"] == "homeassistant"


def test_unknown_keys_dropped(data_dir):
    cam = store.add({"name": "Front", "main_url": "rtsp://x/y",
                     "evil": "nope", "order": 999})
    assert "evil" not in cam


def test_public_view_hides_secrets(data_dir):
    cam = store.add({"name": "Front", "main_url": "rtsp://x/y",
                     "username": "admin", "password": "secret"})
    pv = store.public_view(cam)
    assert pv["username"] == store.SECRET_SENTINEL
    assert pv["password"] == store.SECRET_SENTINEL
    # The stored dict still holds the real values.
    assert store.get(cam["id"])["password"] == "secret"


def test_public_view_leaves_empty_secrets_empty(data_dir):
    cam = store.add({"name": "Front", "main_url": "rtsp://x/y"})
    pv = store.public_view(cam)
    assert pv["username"] == ""
    assert pv["password"] == ""


def test_update_sentinel_keeps_stored_secret(data_dir):
    cam = store.add({"name": "Front", "main_url": "rtsp://x/y",
                     "username": "admin", "password": "secret"})
    updated = store.update(cam["id"], {"name": "Front Door",
                                       "password": store.SECRET_SENTINEL})
    assert updated["name"] == "Front Door"
    assert updated["password"] == "secret"


def test_update_new_secret_replaces(data_dir):
    cam = store.add({"name": "Front", "main_url": "rtsp://x/y",
                     "password": "old"})
    updated = store.update(cam["id"], {"password": "new"})
    assert updated["password"] == "new"


def test_update_unknown_returns_none(data_dir):
    assert store.update("cam_nope", {"name": "x"}) is None


def test_remove(data_dir):
    cam = store.add({"name": "Front", "main_url": "rtsp://x/y"})
    assert store.remove(cam["id"]) is True
    assert store.get(cam["id"]) is None
    assert store.remove(cam["id"]) is False


def test_reorder(data_dir):
    a = store.add({"name": "A", "main_url": "rtsp://x/1"})
    b = store.add({"name": "B", "main_url": "rtsp://x/2"})
    c = store.add({"name": "C", "main_url": "rtsp://x/3"})
    ordered = store.reorder([c["id"], a["id"], b["id"]])
    assert [x["name"] for x in ordered] == ["C", "A", "B"]


def test_reorder_partial_keeps_missing(data_dir):
    a = store.add({"name": "A", "main_url": "rtsp://x/1"})
    b = store.add({"name": "B", "main_url": "rtsp://x/2"})
    # Only mention b; a must still be present, ordered after.
    ordered = store.reorder([b["id"]])
    names = [x["name"] for x in ordered]
    assert names[0] == "B"
    assert "A" in names
