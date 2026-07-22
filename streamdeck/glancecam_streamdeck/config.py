"""Controller configuration.

Settings come from a TOML file (default ``config.toml`` next to the package,
overridable with ``--config`` or the ``GLANCECAM_STREAMDECK_CONFIG`` environment
variable). Every value has a sane default, so a deck plugged into a fresh
GlanceCam appliance works with an empty file as long as the app is on localhost.

The dataclass is deliberately small and self-contained: ``load`` reads the file
and layers the environment over it, ``dumps`` writes the same schema back out, so
the pair round-trips cleanly and stays easy to unit test without any hardware.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ENV_CONFIG = "GLANCECAM_STREAMDECK_CONFIG"
ENV_API_KEY = "GLANCECAM_STREAMDECK_API_KEY"
ENV_BASE_URL = "GLANCECAM_STREAMDECK_BASE_URL"

# Brightness steps cycled by a brightness press, low to high.
BRIGHTNESS_STEPS: tuple[int, ...] = (20, 40, 60, 80, 100)

# The only rotations we support, in degrees clockwise.
ALLOWED_ROTATIONS: tuple[int, ...] = (0, 90, 180, 270)

# Snapshots poll about once a second; this floor keeps a stray tiny value from
# spinning the refresh loop into a tight busy wait.
MIN_POLL_SECONDS = 0.25


@dataclass
class Config:
    # Where the GlanceCam app lives. On the appliance this is the local app; a
    # thin-client deck points at the server on the LAN.
    base_url: str = "http://127.0.0.1:9292"
    # Optional. GlanceCam's public grid and snapshot proxy are open on the LAN,
    # so a key is only needed if a reverse proxy in front of the app requires
    # one; when set it is sent as the X-API-Key header on every request.
    api_key: str = ""
    # Deck brightness, 5 to 100.
    brightness: int = 60
    # How often, in seconds, each key's thumbnail refreshes. About one second
    # keeps the wall lively without hammering the cameras.
    poll_seconds: float = 1.0
    # How often, in seconds, to re-read the camera list so added, removed, or
    # reordered cameras land on the keys without a restart.
    camera_list_refresh_seconds: int = 30
    # How long the display holds a camera full screen after its key is pressed,
    # in seconds. 0 leaves it up until another press or a viewer tap.
    popup_seconds: float = 30.0
    # Clockwise rotation of the rendered key faces, in degrees (0/90/180/270).
    # Use 180 when the deck is mounted upside down; presses are remapped to
    # match. The kiosk display rotation is a separate OS-level setting.
    rotation: int = 0
    # Per-key camera assignment, in reading order (left to right, top to
    # bottom). Each entry is a camera id, or an empty string for a blank key.
    # Empty list means auto-fill from the live camera list in its saved order,
    # which is what the drag-and-drop editor writes when nothing is customised.
    keys: list[str] = field(default_factory=list)
    # Directory the press-selection signal file is written into. The kiosk can
    # read it to learn which camera the deck last chose (see selection_path).
    data_dir: str = "/var/lib/glancecam"
    # Full path of the selection signal file. Empty resolves to
    # ``<data_dir>/deck-selection.json``.
    selection_path: str = ""
    # Key face colours. Backgrounds and the name label adapt to these so the
    # deck can be tuned to match the kiosk without touching code.
    background_color: str = "#101216"  # blank keys and letterbox bars
    label_color: str = "#f4f4f5"       # the camera name text
    offline_color: str = "#2a2d33"     # a key whose camera has no frame yet
    accent_color: str = "#3b82f6"      # the "More" page key and label underline

    def resolved_selection_path(self) -> Path:
        """Absolute path of the selection signal file the press handler writes."""
        if self.selection_path:
            return Path(self.selection_path)
        return Path(self.data_dir) / "deck-selection.json"

    def validated(self) -> "Config":
        """Clamp numbers into sane ranges and normalise the base URL."""
        self.base_url = self.base_url.rstrip("/")
        self.brightness = _clamp(self.brightness, 5, 100)
        self.poll_seconds = max(MIN_POLL_SECONDS, float(self.poll_seconds))
        self.camera_list_refresh_seconds = max(1, int(self.camera_list_refresh_seconds))
        if self.rotation not in ALLOWED_ROTATIONS:
            self.rotation = 0
        # Keep only string entries in the key list; a blank stays an empty
        # string so it maps to a blank slot.
        self.keys = [str(k) for k in self.keys]
        return self


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config.toml"


def resolved_config_path(path: str | os.PathLike | None = None) -> Path:
    """The TOML path :func:`load` would read for the given (optional) override."""
    if path:
        return Path(path)
    if os.environ.get(ENV_CONFIG):
        return Path(os.environ[ENV_CONFIG])
    return default_config_path()


def load(path: str | os.PathLike | None = None) -> Config:
    """Load configuration: defaults, then the TOML file, then the environment.

    The environment wins so a systemd unit can inject the base URL or API key
    without writing either to disk.
    """
    cfg = Config()

    resolved = resolved_config_path(path)
    if resolved.exists():
        data = tomllib.loads(resolved.read_text())
        _apply(cfg, data)

    if os.environ.get(ENV_BASE_URL):
        cfg.base_url = os.environ[ENV_BASE_URL]
    if os.environ.get(ENV_API_KEY):
        cfg.api_key = os.environ[ENV_API_KEY]

    return cfg.validated()


_STR_FIELDS = (
    "base_url", "api_key", "data_dir", "selection_path",
    "background_color", "label_color", "offline_color", "accent_color",
)
_INT_FIELDS = ("brightness", "camera_list_refresh_seconds", "rotation")


def _apply(cfg: Config, data: dict) -> None:
    for name in _STR_FIELDS:
        if isinstance(data.get(name), str):
            setattr(cfg, name, data[name])
    for name in _INT_FIELDS:
        # isinstance(True, int) holds, so exclude booleans explicitly.
        value = data.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            setattr(cfg, name, value)
    for fname in ("poll_seconds", "popup_seconds"):
        value = data.get(fname)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            setattr(cfg, fname, float(value))
    raw_keys = data.get("keys")
    if isinstance(raw_keys, list):
        # Accept a plain list of camera ids or an array of tables each with a
        # "camera" field, so a hand-written or editor-written file both load.
        names: list[str] = []
        for entry in raw_keys:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("camera"), str):
                names.append(entry["camera"])
        cfg.keys = names


def dumps(cfg: Config) -> str:
    """Serialise a config to a TOML string that :func:`load` reads back exactly.

    Only the documented fields are emitted, so ``load(write(dumps(cfg)))``
    reproduces ``cfg``. Kept deliberately simple (no third-party TOML writer) so
    the round trip is easy to test.
    """
    def q(value: str) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [
        f"base_url = {q(cfg.base_url)}",
        f"api_key = {q(cfg.api_key)}",
        f"brightness = {int(cfg.brightness)}",
        f"poll_seconds = {float(cfg.poll_seconds)!r}",
        f"camera_list_refresh_seconds = {int(cfg.camera_list_refresh_seconds)}",
        f"rotation = {int(cfg.rotation)}",
        f"data_dir = {q(cfg.data_dir)}",
        f"selection_path = {q(cfg.selection_path)}",
        f"background_color = {q(cfg.background_color)}",
        f"label_color = {q(cfg.label_color)}",
        f"offline_color = {q(cfg.offline_color)}",
        f"accent_color = {q(cfg.accent_color)}",
        "keys = [" + ", ".join(q(k) for k in cfg.keys) + "]",
    ]
    return "\n".join(lines) + "\n"
