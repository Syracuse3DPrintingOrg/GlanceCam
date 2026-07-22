"""The hardware-facing controller loop.

This is the only module that touches the Stream Deck device library, and it does
so through lazy imports inside the functions that need it, so the package (and
its tests) import fine on a machine with no ``StreamDeck`` wheel and no deck
attached.

What the deck does today: it authenticates to the GlanceCam app (an optional
API key), reads the camera list, maps cameras onto the keys (honouring a saved
per-key assignment or auto-filling in the grid's order), and refreshes each key's
thumbnail about once a second. A press opens that camera full screen on the
attached display (it posts to the app's on-screen event channel) and also
records the choice to a small signal file under the data dir. Losing or
unplugging the deck is recovered in-process with a backoff, and a config-file
rewrite (the editor saving a new layout) is picked up without a restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from . import client as client_mod
from . import config as config_mod
from . import layout, render
from .config import BRIGHTNESS_STEPS, Config

log = logging.getLogger("glancecam.streamdeck")

# Reconnect backoff for a missing deck: a brief USB glitch recovers almost at
# once, then the poll eases off to a steady idle interval so a box with no deck
# attached waits quietly instead of enumerating every few seconds.
RECONNECT_BACKOFF_START = 1.0
RECONNECT_BACKOFF_MAX = 30.0
RECONNECT_BACKOFF_FACTOR = 2.0

# How often the watchdog checks deck health and the config file mtime.
WATCHDOG_INTERVAL = 5.0


def _next_backoff(current: float,
                  start: float = RECONNECT_BACKOFF_START,
                  maximum: float = RECONNECT_BACKOFF_MAX,
                  factor: float = RECONNECT_BACKOFF_FACTOR) -> float:
    """Next wait in the reconnect backoff sequence, capped at ``maximum``."""
    if current <= 0:
        return start
    return min(current * factor, maximum)


def write_selection(path: "str | os.PathLike", camera_id: str, name: str = "",
                    ts: Optional[float] = None) -> bool:
    """Atomically record the deck's chosen camera to the signal file.

    Writes ``{"camera_id", "name", "ts", "source"}`` to ``path`` via a temp file
    plus ``os.replace`` so a reader never sees a half-written file, creating the
    parent directory if needed. Returns True on success; any filesystem error is
    swallowed and returns False, because a press must never crash the controller.

    The kiosk can watch this file to learn which camera to show full screen. That
    read side is not built yet (it needs a small server route); until then the
    file plus the log line are the record of the press.
    """
    payload = {
        "camera_id": camera_id,
        "name": name,
        "ts": time.time() if ts is None else ts,
        "source": "streamdeck",
    }
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, target)
        return True
    except OSError as exc:
        log.warning("could not write deck selection to %s: %s", path, exc)
        return False


class Controller:
    def __init__(self, deck, config: Config, config_path: Optional[str] = None) -> None:
        self.deck = deck
        self.config = config
        self.config_path = config_path
        self.client: Optional[client_mod.GlanceCamClient] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        self.key_count: int = deck.key_count()
        self.snapshots = client_mod.SnapshotCache()
        # Live camera list, keyed by id, refreshed on its own cadence.
        self._cameras_by_id: dict[str, dict] = {}
        self._live_ids: list[str] = []
        # Pages of slots (a camera id, None for blank, or layout.PAGE_NEXT).
        self.pages: list[list[Optional[str]]] = [[None] * self.key_count]
        self.page = 0
        # Last face pushed to each physical key, so an unchanged key skips both
        # the PIL render and the USB write.
        self._face_cache: dict[int, tuple] = {}
        self._config_mtime = self._read_config_mtime()

        try:
            self._bright_idx = BRIGHTNESS_STEPS.index(
                min(BRIGHTNESS_STEPS, key=lambda s: abs(s - config.brightness))
            )
        except ValueError:
            self._bright_idx = len(BRIGHTNESS_STEPS) // 2

        # Reconnect/backoff bookkeeping.
        self._deck_live = True
        self._deck_lost_logged = False
        self._reconnect_delay = 0.0

    # -- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        async with client_mod.GlanceCamClient(
            self.config.base_url, self.config.api_key
        ) as client:
            self.client = client
            self._open_deck()
            self._show_splash("GlanceCam")
            await self._refresh_camera_list()
            await self._refresh_snapshots()
            self._draw_page()
            log.info("Connected to %s (%d keys, %d page(s))",
                     self.deck.deck_type(), self.key_count, len(self.pages))
            await asyncio.gather(self._poll_forever(), self._watchdog_loop())

    def _open_deck(self) -> None:
        """Open the HID device and wire the key callback and brightness."""
        try:
            already_open = bool(self.deck.is_open())
        except Exception:  # noqa: BLE001 - no is_open() on this handle/fake
            already_open = False
        if not already_open:
            self.deck.open()
            self.deck.reset()
        self.deck.set_brightness(BRIGHTNESS_STEPS[self._bright_idx])
        self.deck.set_key_callback(self._on_key)
        self._face_cache.clear()

    def _teardown_deck(self) -> None:
        for step in (self.deck.reset, self.deck.close):
            try:
                step()
            except Exception:  # noqa: BLE001 - the handle may already be dead
                pass

    def close(self) -> None:
        self._teardown_deck()

    # -- geometry ----------------------------------------------------------

    def _key_size(self) -> tuple[int, int]:
        w, h = self.deck.key_image_format()["size"]
        return w, h

    def _display_grid(self) -> tuple[int, int]:
        """(rows, cols) of the key grid as the user sees it after rotation."""
        if self.key_count in layout.GRID:
            d_cols, d_rows = layout.display_dims(self.key_count, self.config.rotation)
            return d_rows, d_cols
        rows, cols = self.deck.key_layout()
        return rows, cols

    # -- camera data -------------------------------------------------------

    async def _refresh_camera_list(self) -> None:
        """Re-read the camera list and rebuild the pages when it changed."""
        if self.client is None:
            return
        cameras = await self.client.list_cameras()
        # An unreachable app returns []; keep the last known list rather than
        # blanking the whole deck on a transient miss.
        if not cameras and self._cameras_by_id:
            return
        usable = client_mod.usable_cameras(cameras)
        self._cameras_by_id = {str(c.get("id")): c for c in usable if c.get("id")}
        self._live_ids = [str(c.get("id")) for c in usable if c.get("id")]
        slots = layout.resolve_slots(self.config.keys, self._live_ids)
        new_pages = layout.build_camera_pages(slots, self.key_count)
        if new_pages != self.pages:
            self.pages = new_pages
            self.page = self.page % len(self.pages)
            self._face_cache.clear()

    def _current(self) -> list[Optional[str]]:
        return self.pages[self.page % len(self.pages)]

    def _shown_camera_ids(self) -> list[str]:
        """Distinct camera ids on the current page (skips blanks and the page key)."""
        seen: list[str] = []
        for slot in self._current():
            if slot and slot != layout.PAGE_NEXT and slot not in seen:
                seen.append(slot)
        return seen

    async def _refresh_snapshots(self) -> None:
        """Fetch a fresh snapshot for each camera on the current page."""
        if self.client is None:
            return
        changed = False
        for cam_id in self._shown_camera_ids():
            data = await self.client.fetch_snapshot(cam_id)
            if self.snapshots.update(cam_id, data):
                changed = True
        if changed:
            self._draw_page()

    def _camera_name(self, cam_id: str) -> str:
        cam = self._cameras_by_id.get(cam_id)
        if cam and cam.get("name"):
            return str(cam["name"])
        return cam_id

    # -- rendering ---------------------------------------------------------

    def _draw_page(self) -> None:
        from StreamDeck.ImageHelpers import PILHelper

        rotation = self.config.rotation
        width, height = self._key_size()
        for index, slot in enumerate(self._current()):
            phys = layout.rotated_index(index, self.key_count, rotation)
            if slot is None:
                face_key: tuple = ("blank", self.config.background_color, rotation)
                if self._face_cache.get(phys) == face_key:
                    continue
                image = render.blank_key(width, height, self.config.background_color)
            elif slot == layout.PAGE_NEXT:
                face_key = ("page", self.page, len(self.pages), rotation)
                if self._face_cache.get(phys) == face_key:
                    continue
                image = render.page_key(width, height, accent_color=self.config.accent_color,
                                        label_color=self.config.label_color)
            else:
                name = self._camera_name(slot)
                digest = self.snapshots.digest_of(slot)
                face_key = ("camera", slot, name, digest, rotation)
                if self._face_cache.get(phys) == face_key:
                    continue
                image = render.compose_key(
                    width, height, self.snapshots.get(slot), name,
                    background_color=self.config.background_color,
                    label_color=self.config.label_color,
                    offline_color=self.config.offline_color,
                )
            if rotation:
                # PIL rotates counter-clockwise, so negate to turn the face
                # clockwise, matching how the deck is physically turned.
                image = image.rotate(-rotation, expand=True)
            self.deck.set_key_image(phys, PILHelper.to_native_format(self.deck, image))
            self._face_cache[phys] = face_key

    def _show_splash(self, text: str) -> None:
        """Paint a short message across the whole deck (connecting, no cameras)."""
        try:
            from StreamDeck.ImageHelpers import PILHelper
            rows, cols = self._display_grid()
            tiles = render.message_across_deck(
                rows, cols, self._key_size(), text,
                background_color=self.config.background_color,
                label_color=self.config.label_color,
            )
            self._face_cache.clear()
            rotation = self.config.rotation
            for index, tile in enumerate(tiles):
                if index >= self.key_count:
                    break
                image = tile.rotate(-rotation, expand=True) if rotation else tile
                phys = layout.rotated_index(index, self.key_count, rotation)
                self.deck.set_key_image(phys, PILHelper.to_native_format(self.deck, image))
        except Exception:  # noqa: BLE001 - a splash must never block startup
            pass

    # -- input -------------------------------------------------------------

    def _on_key(self, deck, key: int, pressed: bool) -> None:
        # Act on release so a press-and-hold does not fire repeatedly.
        if pressed or self.loop is None:
            return
        slot_idx = layout.slot_for_physical(key, self.key_count, self.config.rotation)
        page = self._current()
        if slot_idx >= len(page):
            return
        slot = page[slot_idx]
        if slot is None:
            return
        if slot == layout.PAGE_NEXT:
            self.loop.call_soon_threadsafe(self._next_page)
            return
        # A camera key: record the selection and log it.
        self.loop.call_soon_threadsafe(self._select_camera, slot)

    def _next_page(self) -> None:
        self.page = (self.page + 1) % len(self.pages)
        self._draw_page()
        # Prime the newly shown page's thumbnails promptly.
        if self.loop is not None and self.loop.is_running():
            self.loop.create_task(self._refresh_snapshots())

    def _select_camera(self, cam_id: str) -> None:
        name = self._camera_name(cam_id)
        write_selection(self.config.resolved_selection_path(), cam_id, name)
        log.info("Selected camera %s (%s)", cam_id, name)
        # Ask the display to open it full screen. Fire-and-forget: the file
        # write above is the durable record, and the app may be briefly
        # unreachable without the press being lost.
        if self.client is not None and self.loop is not None and self.loop.is_running():
            self.loop.create_task(
                self.client.show_camera(cam_id, self.config.popup_seconds))

    # -- polling -----------------------------------------------------------

    async def _poll_forever(self) -> None:
        last_list_refresh = time.monotonic()
        while True:
            await asyncio.sleep(self.config.poll_seconds)
            try:
                now = time.monotonic()
                if now - last_list_refresh >= self.config.camera_list_refresh_seconds:
                    last_list_refresh = now
                    await self._refresh_camera_list()
                await self._refresh_snapshots()
            except Exception as exc:  # noqa: BLE001 - keep polling
                log.debug("poll cycle failed: %s", exc)

    # -- watchdog / config watch -------------------------------------------

    def _read_config_mtime(self) -> float:
        if not self.config_path:
            return 0.0
        try:
            return os.path.getmtime(self.config_path)
        except OSError:
            return 0.0

    def _deck_is_healthy(self) -> bool:
        try:
            self.deck.key_count()
            self.deck.key_image_format()
            read_thread = getattr(self.deck, "_read_thread", None)
            if read_thread is not None and not read_thread.is_alive():
                return False
            return True
        except Exception:  # noqa: BLE001 - any failure means re-init
            return False

    async def reinit(self, reload_config: bool = False) -> bool:
        """Tear the deck down and bring it back, optionally reloading config."""
        self._teardown_deck()
        if reload_config and self.config_path:
            try:
                self._apply_config(config_mod.load(self.config_path))
            except Exception as exc:  # noqa: BLE001 - keep the old config
                log.warning("config reload failed, keeping current: %s", exc)
        try:
            fresh = find_deck()
            if fresh is not None:
                self.deck = fresh
        except Exception as exc:  # noqa: BLE001 - fall back to the old handle
            log.debug("re-enumerate failed, reusing handle: %s", exc)
        try:
            self._open_deck()
            self._draw_page()
            self._deck_live = True
            return True
        except Exception as exc:  # noqa: BLE001 - watchdog will retry
            self._deck_live = False
            log.debug("Stream Deck re-init failed (will retry): %s", exc)
            return False

    def _apply_config(self, cfg: Config) -> None:
        self.config = cfg
        self.key_count = self.deck.key_count()
        slots = layout.resolve_slots(cfg.keys, self._live_ids)
        self.pages = layout.build_camera_pages(slots, self.key_count)
        self.page = self.page % len(self.pages)
        try:
            self._bright_idx = BRIGHTNESS_STEPS.index(
                min(BRIGHTNESS_STEPS, key=lambda s: abs(s - cfg.brightness))
            )
        except ValueError:
            self._bright_idx = len(BRIGHTNESS_STEPS) // 2

    async def _watchdog_once(self) -> None:
        mtime = self._read_config_mtime()
        if mtime and mtime != self._config_mtime:
            self._config_mtime = mtime
            log.info("config file changed; re-initialising deck")
            await self.reinit(reload_config=True)
            return
        if self._deck_live and self._deck_is_healthy():
            self._deck_lost_logged = False
            return
        if not self._deck_lost_logged:
            log.warning("Stream Deck disconnected; polling for it to return.")
            self._deck_lost_logged = True
        await self.reinit(reload_config=False)

    def _next_watchdog_delay(self) -> float:
        if self._deck_live:
            self._reconnect_delay = 0.0
            return WATCHDOG_INTERVAL
        self._reconnect_delay = _next_backoff(self._reconnect_delay)
        return self._reconnect_delay

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(self._next_watchdog_delay())
            try:
                await self._watchdog_once()
            except Exception as exc:  # noqa: BLE001 - never let the watchdog die
                log.debug("watchdog tick failed: %s", exc)


def find_deck():
    """Return the first attached Stream Deck, or None."""
    from StreamDeck.DeviceManager import DeviceManager

    decks = DeviceManager().enumerate()
    return decks[0] if decks else None


async def wait_for_deck(enumerate_fn=find_deck, sleep=asyncio.sleep,
                        should_continue=None):
    """Block until a Stream Deck is attached, then return the handle.

    Enumerates on the reconnect backoff so a deck plugged in seconds after boot
    is picked up almost at once, while a box with no deck idle-waits quietly.
    ``enumerate_fn`` and ``sleep`` are injectable for tests; ``should_continue``
    lets a test bound the wait (in production it loops forever).
    """
    deck = enumerate_fn()
    if deck is not None:
        return deck
    log.warning("No Stream Deck attached; waiting for one to be plugged in.")
    delay = 0.0
    while should_continue is None or should_continue():
        delay = _next_backoff(delay)
        await sleep(delay)
        deck = enumerate_fn()
        if deck is not None:
            log.info("Stream Deck attached; starting up.")
            return deck
    return None


async def main_async(config: Config, config_path: Optional[str] = None,
                     deck=None) -> int:
    """Run the controller, idle-waiting in-process for a deck rather than exiting."""
    if deck is None:
        deck = await wait_for_deck()
    if deck is None:
        log.error("No Stream Deck found. Check the USB connection and udev rule.")
        return 1
    controller = Controller(deck, config, config_path=config_path)
    try:
        await controller.run()
    finally:
        controller.close()
    return 0
