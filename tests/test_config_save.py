import json
from pathlib import Path

from app.config import settings, _SAVEABLE
from app.passwords import looks_hashed, verify_secret


def _settings_file(data_dir) -> Path:
    return Path(data_dir) / "settings.json"


def test_saveable_allowlist_drops_unknown_keys(data_dir):
    settings.save({"theme": "light", "not_a_setting": "x"})
    saved = json.loads(_settings_file(data_dir).read_text())
    assert saved["theme"] == "light"
    assert "not_a_setting" not in saved


def test_password_hashed_at_rest(data_dir):
    settings.save({"settings_password": "hunter2"})
    saved = json.loads(_settings_file(data_dir).read_text())
    assert looks_hashed(saved["settings_password"])
    assert saved["settings_password"] != "hunter2"
    assert verify_secret("hunter2", saved["settings_password"])
    # Re-saving the already-hashed value must not double-hash it.
    stored = saved["settings_password"]
    settings.save({"settings_password": stored})
    saved2 = json.loads(_settings_file(data_dir).read_text())
    assert saved2["settings_password"] == stored


def test_corrupt_file_preserved_aside(data_dir):
    sf = _settings_file(data_dir)
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text("{ this is not json")
    settings.save({"theme": "dark"})
    # The corrupt content is moved aside, not clobbered.
    corrupt = Path(data_dir) / "settings.json.corrupt.1"
    assert corrupt.exists()
    assert "not json" in corrupt.read_text()
    # And the new file is valid.
    assert json.loads(sf.read_text())["theme"] == "dark"


def test_bak_rollback_kept(data_dir):
    settings.save({"theme": "dark", "snapshot_refresh_seconds": 10})
    settings.save({"theme": "light"})
    bak = Path(data_dir) / "settings.json.bak"
    assert bak.exists()
    # The .bak holds the previous good content.
    prev = json.loads(bak.read_text())
    assert prev["theme"] == "dark"


def test_atomic_write_leaves_no_temp(data_dir):
    settings.save({"theme": "dark"})
    assert not (Path(data_dir) / "settings.json.tmp").exists()


def test_file_permissions_owner_only(data_dir):
    settings.save({"theme": "dark"})
    mode = _settings_file(data_dir).stat().st_mode & 0o777
    assert mode == 0o600


def test_secret_key_not_user_settable_is_still_saveable_key():
    # secret_key is in the allowlist (first-run save uses it) even though the
    # settings router never accepts it from a POST.
    assert "secret_key" in _SAVEABLE
