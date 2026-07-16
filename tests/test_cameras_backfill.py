"""Backfilling probed codec/resolution onto a stored camera (pure logic)."""
from app.services import cameras as store


# ---- probe_patch: the pure merge rules -------------------------------------

def test_probe_patch_fills_codec_and_resolution():
    cam = {"main_resolution": None, "main_codec": None,
           "sub_resolution": None, "sub_codec": None}
    patch = store.probe_patch(
        cam,
        {"codec": "H265", "resolution": [2560, 1440]},
        {"codec": "H264", "resolution": [640, 360]})
    assert patch == {
        "main_codec": "H265", "main_resolution": [2560, 1440],
        "sub_codec": "H264", "sub_resolution": [640, 360],
    }


def test_probe_patch_skips_unchanged_values():
    cam = {"main_resolution": [1920, 1080], "main_codec": "H264"}
    patch = store.probe_patch(
        cam, {"codec": "H264", "resolution": [1920, 1080]}, None)
    assert patch == {}


def test_probe_patch_reports_only_the_changed_key():
    cam = {"main_resolution": [1920, 1080], "main_codec": "H264"}
    # Same resolution, but the camera is now known to be H265.
    patch = store.probe_patch(
        cam, {"codec": "H265", "resolution": [1920, 1080]}, None)
    assert patch == {"main_codec": "H265"}


def test_probe_patch_ignores_none_probe():
    cam = {"main_resolution": [1920, 1080], "main_codec": "H264"}
    assert store.probe_patch(cam, None, None) == {}


def test_probe_patch_never_clears_a_known_value():
    cam = {"main_resolution": [1920, 1080], "main_codec": "H264"}
    # A probe with no codec/resolution must not wipe what we already learned.
    assert store.probe_patch(cam, {"codec": None, "resolution": None}, None) == {}


def test_probe_patch_ignores_malformed_resolution():
    cam = {"sub_resolution": None, "sub_codec": None}
    patch = store.probe_patch(cam, None, {"codec": "H264", "resolution": [640]})
    assert patch == {"sub_codec": "H264"}


# ---- backfill: applies the patch to the store ------------------------------

def test_backfill_persists_codec_and_resolution(data_dir):
    cam = store.add({"name": "Front", "main_url": "rtsp://x/main",
                     "sub_url": "rtsp://x/sub"})
    updated = store.backfill(
        cam["id"],
        {"codec": "H265", "resolution": [2560, 1440]},
        {"codec": "H264", "resolution": [640, 360]})
    assert updated["main_codec"] == "H265"
    assert updated["sub_codec"] == "H264"
    stored = store.get(cam["id"])
    assert stored["main_resolution"] == [2560, 1440]
    assert stored["sub_resolution"] == [640, 360]


def test_backfill_no_change_does_not_error(data_dir):
    cam = store.add({"name": "Front", "main_url": "rtsp://x/main"})
    store.backfill(cam["id"], {"codec": "H264", "resolution": [1920, 1080]}, None)
    # A second identical probe is a no-op that still returns the camera.
    again = store.backfill(cam["id"],
                           {"codec": "H264", "resolution": [1920, 1080]}, None)
    assert again["main_codec"] == "H264"


def test_backfill_unknown_id_returns_none(data_dir):
    assert store.backfill("cam_nope", {"codec": "H265"}, None) is None


def test_backfilled_codec_survives_public_view(data_dir):
    cam = store.add({"name": "Front", "main_url": "rtsp://x/main"})
    store.backfill(cam["id"], {"codec": "H265", "resolution": [2560, 1440]}, None)
    pv = store.public_view(store.get(cam["id"]))
    assert pv["main_codec"] == "H265"
