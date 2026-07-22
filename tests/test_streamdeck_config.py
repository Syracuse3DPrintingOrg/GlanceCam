"""Config defaults, TOML round-trip, and clamping for the Stream Deck controller."""
from __future__ import annotations

import sys
from pathlib import Path

_SD = Path(__file__).resolve().parent.parent / "streamdeck"
if str(_SD) not in sys.path:
    sys.path.insert(0, str(_SD))

from glancecam_streamdeck import config as cfg_mod  # noqa: E402


def test_defaults_are_sane():
    c = cfg_mod.Config().validated()
    assert c.base_url == "http://127.0.0.1:9292"
    assert c.api_key == ""
    assert c.brightness == 60
    assert c.poll_seconds == 1.0
    assert c.rotation == 0
    assert c.keys == []
    assert c.camera_list_refresh_seconds == 30


def test_load_missing_file_returns_defaults(tmp_path):
    c = cfg_mod.load(tmp_path / "does-not-exist.toml")
    assert c.base_url == "http://127.0.0.1:9292"
    assert c.keys == []


def test_toml_round_trip(tmp_path):
    original = cfg_mod.Config(
        base_url="http://192.168.1.50:9292/",  # trailing slash normalised on load
        api_key="secret-key",
        brightness=80,
        poll_seconds=0.75,
        camera_list_refresh_seconds=45,
        rotation=90,
        keys=["cam_aaa", "", "cam_bbb"],
        data_dir="/var/lib/glancecam",
        selection_path="/tmp/sel.json",
        background_color="#000000",
        label_color="#ffffff",
        offline_color="#111111",
        accent_color="#ff0000",
    ).validated()

    path = tmp_path / "streamdeck.toml"
    path.write_text(cfg_mod.dumps(original))
    loaded = cfg_mod.load(path)

    assert loaded.base_url == "http://192.168.1.50:9292"  # normalised
    assert loaded.api_key == "secret-key"
    assert loaded.brightness == 80
    assert loaded.poll_seconds == 0.75
    assert loaded.camera_list_refresh_seconds == 45
    assert loaded.rotation == 90
    assert loaded.keys == ["cam_aaa", "", "cam_bbb"]
    assert loaded.data_dir == "/var/lib/glancecam"
    assert loaded.selection_path == "/tmp/sel.json"
    assert loaded.background_color == "#000000"
    assert loaded.accent_color == "#ff0000"


def test_validation_clamps_and_normalises():
    c = cfg_mod.Config(
        brightness=999, poll_seconds=0.0, rotation=45,
        camera_list_refresh_seconds=0, base_url="http://x/",
    ).validated()
    assert c.brightness == 100
    assert c.poll_seconds == cfg_mod.MIN_POLL_SECONDS
    assert c.rotation == 0  # unsupported rotation falls back to 0
    assert c.camera_list_refresh_seconds == 1
    assert c.base_url == "http://x"


def test_keys_accept_table_form(tmp_path):
    path = tmp_path / "streamdeck.toml"
    path.write_text(
        'base_url = "http://127.0.0.1:9292"\n'
        "keys = [ {camera = \"cam_x\"}, {camera = \"cam_y\"} ]\n"
    )
    loaded = cfg_mod.load(path)
    assert loaded.keys == ["cam_x", "cam_y"]


def test_env_overrides_file(tmp_path, monkeypatch):
    path = tmp_path / "streamdeck.toml"
    path.write_text('base_url = "http://file-host:9292"\napi_key = "from-file"\n')
    monkeypatch.setenv(cfg_mod.ENV_BASE_URL, "http://env-host:9292")
    monkeypatch.setenv(cfg_mod.ENV_API_KEY, "from-env")
    loaded = cfg_mod.load(path)
    assert loaded.base_url == "http://env-host:9292"
    assert loaded.api_key == "from-env"


def test_resolved_selection_path_defaults_to_data_dir():
    c = cfg_mod.Config(data_dir="/data", selection_path="").validated()
    assert c.resolved_selection_path() == Path("/data/deck-selection.json")
    c2 = cfg_mod.Config(selection_path="/custom/path.json").validated()
    assert c2.resolved_selection_path() == Path("/custom/path.json")
