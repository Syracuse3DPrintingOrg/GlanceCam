"""Saved credentials store: round-trip, masked public view, resolution."""
import pytest

from app.services import credentials as store


def test_add_and_list_public_masks_password(data_dir):
    store.add("Front cams", "admin", "hunter2")
    listed = store.list_public()
    assert len(listed) == 1
    entry = listed[0]
    assert entry["name"] == "Front cams"
    assert entry["username"] == "admin"
    assert entry["password"] == store.SECRET_SENTINEL  # never the real one
    assert entry["id"].startswith("cred_")


def test_add_requires_name(data_dir):
    with pytest.raises(ValueError):
        store.add("", "admin", "x")


def test_resolve_returns_real_credentials(data_dir):
    added = store.add("NVR", "viewer", "secret")
    user, pw = store.resolve(added["id"])
    assert (user, pw) == ("viewer", "secret")


def test_resolve_unknown_is_none(data_dir):
    assert store.resolve("cred_nope") is None
    assert store.resolve("") is None


def test_roundtrip_persists(data_dir):
    a = store.add("A", "u", "p")
    store.add("B", "u2", "p2")
    # A fresh read reflects both, ordered by name.
    listed = store.list_public()
    assert [e["name"] for e in listed] == ["A", "B"]
    assert store.resolve(a["id"]) == ("u", "p")


def test_update_keeps_password_with_sentinel(data_dir):
    added = store.add("A", "u", "p")
    store.update(added["id"], name="A2", password=store.SECRET_SENTINEL)
    assert store.resolve(added["id"]) == ("u", "p")
    assert store.list_public()[0]["name"] == "A2"


def test_remove(data_dir):
    added = store.add("A", "u", "p")
    assert store.remove(added["id"]) is True
    assert store.list_public() == []
    assert store.remove(added["id"]) is False


def test_empty_password_stays_empty_in_public_view(data_dir):
    added = store.add("No pass", "guest", "")
    entry = next(e for e in store.list_public() if e["id"] == added["id"])
    assert entry["password"] == ""
