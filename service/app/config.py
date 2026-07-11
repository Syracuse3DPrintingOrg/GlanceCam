"""Application settings.

Pydantic-settings drives configuration. Precedence, highest first:

1. Environment variables (prefix ``GLANCECAM_``).
2. The ``data/settings.json`` overlay written by the settings page.
3. The field defaults below.

Env vars always win: the overlay only fills fields the environment did not set
(tracked via ``model_fields_set``). ``_SAVEABLE`` is the allowlist of keys the
settings page may persist. ``save()`` is hardened against data loss (corrupt
file preserved aside, a ``.bak`` rollback copy, atomic write at chmod 0600) and
hashes the settings password at rest.
"""
from __future__ import annotations

import json
import logging
import os
import secrets as _secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Single source of truth for the app version (shown in the UI and reported by
# /health and /api/system). Bump on each release.
APP_VERSION = "0.1.0"

# Display name. The internal identifier stays "glancecam" everywhere else.
APP_NAME = "GlanceCam"

_log = logging.getLogger("glancecam.config")

# The service directory (parent of app/), so the default data dir resolves to
# service/data regardless of the process working directory.
_SERVICE_DIR = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def is_raspberry_pi() -> bool:
    """True on a Raspberry Pi, read from the device tree model string.

    Cached and file-reading only on first call, so importing this name is free.
    Used to gate kiosk behaviors; a settings override can force it either way.
    """
    try:
        model = Path("/proc/device-tree/model").read_text(errors="ignore")
    except OSError:
        return False
    return "raspberry pi" in model.lower()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GLANCECAM_", extra="ignore")

    app_name: str = APP_NAME
    # Where settings.json, cameras.json, and the secret live. Relative values
    # resolve against the service directory so `data` means service/data.
    data_dir: str = str(_SERVICE_DIR / "data")

    # go2rtc REST API base. In Docker this is set to http://go2rtc:1984; the
    # native default is loopback.
    go2rtc_url: str = "http://127.0.0.1:1984"
    # Public path the app reverse-proxies go2rtc under, so browsers only ever
    # talk to the app's origin for streams.
    go2rtc_public_path: str = "/go2rtc"

    # Optional settings password, hashed at rest. Empty means settings are open.
    settings_password: str = ""

    # Display / kiosk.
    theme: str = "dark"
    fullscreen_uses_main: bool = True
    snapshot_refresh_seconds: int = 10
    kiosk_rotation: int = 0

    # Auto-detected on a Pi; a settings override can force it. Kept out of the
    # env-only path so the settings page can toggle it.
    is_pi: bool = False

    # Home Assistant connection for camera discovery. The token is a long-lived
    # access token; it is a secret (stored server-side, never returned in an API
    # response) but it is NOT hashed, since the app must replay it as a bearer
    # header to fetch HA camera feeds.
    ha_base_url: str = ""
    ha_token: str = ""

    # Signs the session cookie. Auto-generated and persisted on first run.
    secret_key: str = ""

    def is_pi_effective(self) -> bool:
        """The kiosk gate: the stored flag OR real Pi hardware."""
        return bool(self.is_pi) or is_raspberry_pi()

    def apply(self, data: dict) -> None:
        for k, v in data.items():
            if k in _SAVEABLE and hasattr(self, k) and v is not None:
                object.__setattr__(self, k, v)

    def reload(self) -> dict:
        """Re-read settings.json from disk and apply it to the live object.

        Returns the applied dict, or {} on any read/parse error."""
        sf = Path(self.data_dir) / "settings.json"
        try:
            data = json.loads(sf.read_text())
        except (OSError, ValueError):
            return {}
        if isinstance(data, dict):
            self.apply(data)
            return data
        return {}

    def save(self, data: dict) -> None:
        """Merge ``data`` into settings.json and apply it to the live object.

        Hardened against data loss: if the existing file is present but cannot
        be read or parsed, it is preserved aside as settings.json.corrupt.<n>
        rather than clobbered with only the new fields; a settings.json.bak
        rollback copy of the last good content is kept; and the write is atomic
        (temp file plus rename) so an interrupted write cannot truncate the live
        file. The settings password is hashed at rest.
        """
        sf = Path(self.data_dir) / "settings.json"
        sf.parent.mkdir(parents=True, exist_ok=True)

        existing: dict = {}
        good_raw = None  # last valid on-disk content, kept for a rollback copy
        if sf.exists():
            raw = None
            try:
                raw = sf.read_text()
            except OSError as exc:
                raw = None
                _log.error("settings.json could not be read (%s); preserving it "
                           "before saving", exc)
            if raw is not None and raw.strip():
                try:
                    existing = json.loads(raw)
                    good_raw = raw
                except ValueError as exc:
                    _log.error("settings.json is corrupt (%s); preserving it as "
                               "a backup", exc)
                    raw = None  # force the preserve-aside path below
            if raw is None and sf.exists():
                # Unreadable or corrupt and non-empty: move it aside so the data
                # stays recoverable instead of being clobbered by this save.
                for n in range(1, 1000):
                    bak = sf.with_name(f"settings.json.corrupt.{n}")
                    if not bak.exists():
                        try:
                            sf.replace(bak)
                        except OSError:
                            pass
                        break

        # Hash the settings password at rest. A value that is already hashed (a
        # re-save) is left alone to avoid double-hashing.
        from .passwords import hash_secret, looks_hashed
        if data.get("settings_password") and not looks_hashed(data["settings_password"]):
            data["settings_password"] = hash_secret(data["settings_password"])

        existing.update({k: v for k, v in data.items()
                         if k in _SAVEABLE and v is not None})

        # One-step rollback copy of the last good settings. Best effort.
        if good_raw is not None:
            try:
                bak = sf.with_name("settings.json.bak")
                bak.write_text(good_raw)
                bak.chmod(0o600)
            except OSError:
                pass

        # Atomic write: temp file in the same dir, then rename over the target.
        tmp = sf.with_name("settings.json.tmp")
        tmp.write_text(json.dumps(existing, indent=2))
        tmp.chmod(0o600)  # settings.json may hold the hashed password: owner-only
        os.replace(tmp, sf)
        self.apply(existing)


# Fields the settings page may persist. secret_key is here so first-run
# generation can save it, but it is never accepted from a settings POST (the
# router filters the payload to the user-facing keys).
_SAVEABLE = [
    "app_name",
    "go2rtc_url",
    "go2rtc_public_path",
    "settings_password",
    "theme",
    "fullscreen_uses_main",
    "snapshot_refresh_seconds",
    "kiosk_rotation",
    "is_pi",
    "ha_base_url",
    "ha_token",
    "secret_key",
]


settings = Settings()

# Overlay: fill any field the environment did not set from data/settings.json.
_sf = Path(settings.data_dir) / "settings.json"
if _sf.exists():
    try:
        _saved = json.loads(_sf.read_text())
        for _k, _v in _saved.items():
            if _k in _SAVEABLE and _k not in settings.model_fields_set:
                object.__setattr__(settings, _k, _v)
    except (OSError, ValueError) as _exc:
        # A corrupt settings.json must be loud, not silent: save() preserves it
        # aside on the next write.
        _log.error("Could not load settings.json at startup (%s); it will be "
                   "preserved on the next save", _exc)

# Auto-generate SECRET_KEY on first run so it stays stable across restarts.
# Persisting is best-effort: if data_dir is not writable (tests, or an import
# before the volume is mounted) keep the in-memory key rather than crashing.
if not settings.secret_key:
    object.__setattr__(settings, "secret_key", _secrets.token_hex(32))
    try:
        settings.save({"secret_key": settings.secret_key})
    except OSError:
        pass
