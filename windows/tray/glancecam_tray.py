r"""GlanceCam system-tray supervisor (Windows).

This is the standard-app face of the native Windows install: a small tray
icon named GlanceCam that a user can find in the system tray and use to start,
stop, open, or quit the whole thing. It supervises the two background
processes that actually serve the camera grid:

  * go2rtc.exe (-config go2rtc.yaml), the streaming engine, and
  * the FastAPI app (uvicorn app.main:app on port 9292).

Both run as child subprocesses with no console window, are restarted with
backoff if they die, and are stopped cleanly when the tray exits. There is no
Windows service and no Scheduled Task here on purpose: the process the user
sees in the tray IS the supervisor, so closing it stops GlanceCam, and the
"Start GlanceCam when I sign in" checkbox (an HKCU Run entry) is the only
startup wiring. That is the "found and turned off easily" the owner asked for.

Layout (matches the Inno installer and scripts/windows/install.ps1):

    <root>\tray\glancecam_tray.py   <- this file
    <root>\app\service\app\main.py  <- the FastAPI app (cwd is app\service)
    <root>\python\pythonw.exe       <- the private runtime
    <root>\go2rtc\go2rtc.exe        <- the streaming engine + go2rtc.yaml
    <root>\data\                     <- cameras/settings (or a path from the .ini)
    <root>\logs\                     <- app.log, go2rtc.log, tray.log

Data dir: the installer keeps cameras and settings out of Program Files, so it
writes <root>\glancecam.ini with a data_dir under ProgramData. This reads that
ini (falling back to <root>\data) and passes it to the app as
GLANCECAM_DATA_DIR.

Pure standard library except pystray + Pillow (see requirements.txt). It is
syntax-checked on Linux with py_compile; the Windows-only names (winreg, the
CREATE_NO_WINDOW flag) are guarded or defined as plain constants so the compile
never needs a real Windows box.
"""
from __future__ import annotations

import configparser
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

# winreg is Windows-only. Importing it is what wires the "start at sign-in"
# checkbox; on any other OS (and during the Linux py_compile check) it is
# simply absent and that menu item degrades to a no-op.
try:
    import winreg  # type: ignore
except ImportError:  # pragma: no cover - not present off Windows
    winreg = None  # type: ignore

from PIL import Image, ImageDraw

# pystray is imported lazily (inside the tray methods that build/run the menu)
# so the module can be imported with only Pillow present, e.g. when CI renders
# the Start Menu .ico from make_icon() without standing up a tray backend.


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
APP_NAME = "GlanceCam"
APP_PORT = 9292
APP_URL = f"http://localhost:{APP_PORT}"
SETTINGS_URL = f"{APP_URL}/settings"

# go2rtc REST API base the app talks to (loopback; the app reverse-proxies
# stream traffic so browsers only ever hit the app origin). Matches the
# go2rtc.yaml the installer writes (api.listen 127.0.0.1:1984).
GO2RTC_URL = "http://127.0.0.1:1984"

# Single-instance guard: a second launch fails to bind this and exits 0.
SINGLE_INSTANCE_PORT = 9291

# CREATE_NO_WINDOW keeps each child from flashing a console window. It only
# exists as subprocess.CREATE_NO_WINDOW on Windows, so define the literal
# (0x08000000) here and the file still compiles everywhere.
CREATE_NO_WINDOW = 0x08000000

# Log rotate-lite: truncate a service log once it passes this size, on open.
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

# Supervision/backoff. If a child dies MAX_RESTARTS times inside RESTART_WINDOW
# seconds, stop retrying it and surface an error state in the tooltip.
BACKOFF_START = 2.0
BACKOFF_MAX = 30.0
MAX_RESTARTS = 5
RESTART_WINDOW = 120.0

# Accent color of the app UI, reused for the tray glyph's dot/ring.
ACCENT = (127, 209, 255)  # #7fd1ff

# HKCU Run entry (start at sign-in). pythonw runs the tray with no console.
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "GlanceCam"


# --------------------------------------------------------------------------
# Paths, resolved relative to the install root (parent of this tray dir)
# --------------------------------------------------------------------------
class Paths:
    def __init__(self) -> None:
        self.tray_dir = Path(__file__).resolve().parent
        self.root = self.tray_dir.parent
        self.app_dir = self.root / "app"
        self.service_dir = self.app_dir / "service"
        self.python_dir = self.root / "python"
        self.go2rtc_dir = self.root / "go2rtc"
        self.go2rtc_exe = self.go2rtc_dir / "go2rtc.exe"
        self.go2rtc_yaml = self.go2rtc_dir / "go2rtc.yaml"
        self.logs_dir = self.root / "logs"
        self.ini = self.root / "glancecam.ini"
        self.data_dir = self._resolve_data_dir()

    def _resolve_data_dir(self) -> Path:
        """Data dir from <root>\\glancecam.ini, else <root>\\data.

        The installer writes the ini so cameras/settings live under ProgramData
        rather than inside Program Files (which a standard user cannot write).
        """
        default = self.root / "data"
        if self.ini.exists():
            try:
                cp = configparser.ConfigParser()
                cp.read(self.ini, encoding="utf-8")
                value = cp.get("glancecam", "data_dir", fallback="").strip()
                if value:
                    return Path(value)
            except (configparser.Error, OSError):
                pass
        return default

    def python_exe(self, windowed: bool = True) -> Path:
        """pythonw.exe (no console) when present, else python.exe."""
        pyw = self.python_dir / "pythonw.exe"
        if windowed and pyw.exists():
            return pyw
        return self.python_dir / "python.exe"


# --------------------------------------------------------------------------
# Log helpers
# --------------------------------------------------------------------------
def _rotate_lite(path: Path) -> None:
    """Truncate a log that has grown past LOG_MAX_BYTES.

    Deliberately simple (no numbered .1/.2 history): the child processes hold
    the file open for the whole run, so this only fires at start, which is
    enough to keep the two service logs from growing without bound.
    """
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            path.replace(path.with_suffix(path.suffix + ".old"))
    except OSError:
        pass


def _open_log(path: Path):
    """Open a service log for append (binary), rotating first. None on failure."""
    _rotate_lite(path)
    try:
        return open(path, "ab", buffering=0)
    except OSError:
        return None


# --------------------------------------------------------------------------
# Supervised child process
# --------------------------------------------------------------------------
class ManagedProcess:
    """One supervised child (go2rtc or the app), restarted with backoff.

    A single worker thread owns the child's whole lifecycle: it (re)spawns the
    process, waits for it to exit, and either backs off and retries or gives up
    and flips ``failed`` after too many crashes in a short window. ``stop()``
    signals the thread to quit and terminates the child (terminate, then kill
    after a grace period).
    """

    def __init__(self, name: str, argv, cwd: Path, log_path: Path,
                 env=None, on_state_change=None) -> None:
        self.name = name
        self.argv = [str(a) for a in argv]
        self.cwd = str(cwd)
        self.log_path = log_path
        self.env = env
        self._on_state_change = on_state_change

        self._proc = None
        self._thread = None
        self._want_run = threading.Event()
        self._lock = threading.Lock()
        self.failed = False

    # -- public API --------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.failed = False
        self._want_run.set()
        self._thread = threading.Thread(
            target=self._run, name=f"supervise-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._want_run.clear()
        self._terminate_child(timeout)
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout + 2.0)

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    # -- internals ---------------------------------------------------------
    def _spawn(self):
        log = _open_log(self.log_path)
        try:
            proc = subprocess.Popen(
                self.argv,
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=log if log else subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
                close_fds=True,
            )
        finally:
            # Popen dup'd the handle into the child; the parent copy can close.
            if log is not None:
                try:
                    log.close()
                except OSError:
                    pass
        return proc

    def _terminate_child(self, grace: float = 5.0) -> None:
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError:
            pass
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.2)
        try:
            proc.kill()
        except OSError:
            pass

    def _run(self) -> None:
        backoff = BACKOFF_START
        crash_times = []
        while self._want_run.is_set():
            try:
                proc = self._spawn()
            except OSError as exc:
                log.error("%s failed to launch: %s", self.name, exc)
                self.failed = True
                self._notify()
                return
            with self._lock:
                self._proc = proc
            self._notify()
            log.info("%s started (pid %s)", self.name, proc.pid)

            # Wait for it to exit, checking the stop flag periodically.
            while self._want_run.is_set() and proc.poll() is None:
                time.sleep(0.5)

            if not self._want_run.is_set():
                # Asked to stop: make sure it is down and leave quietly.
                self._terminate_child()
                self._notify()
                return

            code = proc.poll()
            log.warning("%s exited (code %s); will restart", self.name, code)
            self._notify()

            # Crash accounting inside a sliding window.
            now = time.monotonic()
            crash_times = [t for t in crash_times if now - t < RESTART_WINDOW]
            crash_times.append(now)
            if len(crash_times) >= MAX_RESTARTS:
                log.error("%s crashed %d times in %.0fs; giving up",
                          self.name, len(crash_times), RESTART_WINDOW)
                self.failed = True
                self._notify()
                return

            # Backoff before the next attempt, but wake immediately on stop.
            self._want_run.wait(timeout=backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
        self._notify()

    def _notify(self) -> None:
        if self._on_state_change is not None:
            try:
                self._on_state_change()
            except Exception:  # pragma: no cover - never let UI break the loop
                log.exception("state-change callback failed")


# --------------------------------------------------------------------------
# Icon glyph (generated, no committed binary asset)
# --------------------------------------------------------------------------
def make_icon(state: str = "running") -> Image.Image:
    """A small lens/eye glyph: dark disc, pale ring, accent pupil.

    ``state`` tints the pupil so the tray art itself reads running (accent) /
    stopped (grey) / error (red), matching the tooltip.
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    dark = (24, 28, 34, 255)
    ring = (200, 214, 224, 255)
    if state == "error":
        pupil = (232, 76, 76, 255)
    elif state == "stopped":
        pupil = (128, 136, 144, 255)
    else:
        pupil = (ACCENT[0], ACCENT[1], ACCENT[2], 255)

    # Outer dark disc.
    d.ellipse((2, 2, size - 3, size - 3), fill=dark)
    # Pale lens ring.
    d.ellipse((10, 10, size - 11, size - 11), outline=ring, width=4)
    # Accent pupil.
    r = 10
    c = size // 2
    d.ellipse((c - r, c - r, c + r, c + r), fill=pupil)
    # Small catch-light so it reads as a lens.
    d.ellipse((c + 1, c - 7, c + 6, c - 2), fill=(255, 255, 255, 220))
    return img


# --------------------------------------------------------------------------
# Startup (HKCU Run) toggle
# --------------------------------------------------------------------------
def _run_command() -> str:
    """The command HKCU\\...\\Run should launch: pythonw + this script."""
    exe = PATHS.python_exe(windowed=True)
    return f'"{exe}" "{Path(__file__).resolve()}"'


def startup_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            winreg.QueryValueEx(key, RUN_VALUE_NAME)
        return True
    except OSError:
        return False


def set_startup(enabled: bool) -> None:
    if winreg is None:
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ,
                                  _run_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE_NAME)
                except OSError:
                    pass
    except OSError:
        log.exception("could not update the start-at-sign-in setting")


# --------------------------------------------------------------------------
# The tray application
# --------------------------------------------------------------------------
class TrayApp:
    def __init__(self) -> None:
        self.icon = None
        self._supervising = False
        self._children = []

        env = os.environ.copy()
        env["GLANCECAM_GO2RTC_URL"] = GO2RTC_URL
        env["GLANCECAM_DATA_DIR"] = str(PATHS.data_dir)
        # The app tree lives under Program Files (read-only for a standard user),
        # so don't let Python try to drop __pycache__ next to it.
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        self.go2rtc = ManagedProcess(
            name="go2rtc",
            argv=[PATHS.go2rtc_exe, "-config", PATHS.go2rtc_yaml],
            cwd=PATHS.go2rtc_dir,
            log_path=PATHS.logs_dir / "go2rtc.log",
            env=env,
            on_state_change=self.refresh,
        )
        self.app = ManagedProcess(
            name="app",
            argv=[PATHS.python_exe(windowed=False), "-m", "uvicorn",
                  "app.main:app", "--host", "0.0.0.0", "--port", str(APP_PORT),
                  "--no-proxy-headers"],
            cwd=PATHS.service_dir,
            log_path=PATHS.logs_dir / "app.log",
            env=env,
            on_state_change=self.refresh,
        )
        self._children = [self.go2rtc, self.app]

    # -- supervision -------------------------------------------------------
    def start_all(self) -> None:
        self._supervising = True
        # go2rtc first so its REST API is up before the app reaches for it (the
        # app retries anyway, this just keeps the first log clean).
        self.go2rtc.start()
        self.app.start()
        self.refresh()

    def stop_all(self) -> None:
        self._supervising = False
        for child in self._children:
            child.stop()
        self.refresh()

    def toggle_supervision(self, icon=None, item=None) -> None:
        if self._supervising:
            self.stop_all()
        else:
            self.start_all()

    # -- state / tooltip ---------------------------------------------------
    def state(self) -> str:
        if any(c.failed for c in self._children):
            return "error"
        if self._supervising and all(c.is_running() for c in self._children):
            return "running"
        if self._supervising:
            return "starting"
        return "stopped"

    def tooltip(self) -> str:
        return {
            "running": f"{APP_NAME}: running",
            "starting": f"{APP_NAME}: starting...",
            "stopped": f"{APP_NAME}: stopped",
            "error": f"{APP_NAME}: error, see logs",
        }[self.state()]

    def refresh(self) -> None:
        icon = self.icon
        if icon is None:
            return
        try:
            icon.icon = make_icon(self.state())
            icon.title = self.tooltip()
            icon.update_menu()
        except Exception:  # pragma: no cover - UI backend quirks
            log.exception("tray refresh failed")

    # -- menu actions ------------------------------------------------------
    def on_open(self, icon=None, item=None) -> None:
        webbrowser.open(APP_URL)

    def on_settings(self, icon=None, item=None) -> None:
        webbrowser.open(SETTINGS_URL)

    def on_toggle_startup(self, icon=None, item=None) -> None:
        set_startup(not startup_enabled())
        self.refresh()

    def on_exit(self, icon=None, item=None) -> None:
        self.stop_all()
        if self.icon is not None:
            self.icon.stop()

    def _menu(self):
        import pystray
        return pystray.Menu(
            pystray.MenuItem(f"Open {APP_NAME}", self.on_open, default=True),
            pystray.MenuItem("Settings", self.on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: "Stop GlanceCam" if self._supervising
                else "Start GlanceCam",
                self.toggle_supervision),
            pystray.MenuItem(
                "Start GlanceCam when I sign in",
                self.on_toggle_startup,
                checked=lambda item: startup_enabled(),
                enabled=lambda item: winreg is not None),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.on_exit),
        )

    def run(self) -> None:
        import pystray
        self.icon = pystray.Icon(
            APP_NAME.lower(),
            icon=make_icon("stopped"),
            title=f"{APP_NAME}: starting...",
            menu=self._menu())
        # Kick supervision off once the tray loop is up.
        self.icon.run(setup=lambda icon: self.start_all())


# --------------------------------------------------------------------------
# Single instance
# --------------------------------------------------------------------------
def acquire_single_instance():
    """Bind a loopback port as a process-wide mutex.

    Returns the held socket on success, or None if another tray already owns
    it. The socket stays open for the life of the process; the OS drops it on
    exit, which frees the next launch.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


# --------------------------------------------------------------------------
# Logging (tray's own log)
# --------------------------------------------------------------------------
log = logging.getLogger("glancecam.tray")


def _setup_logging() -> None:
    PATHS.logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        PATHS.logs_dir / "tray.log", maxBytes=LOG_MAX_BYTES, backupCount=1,
        encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)


# Resolved once at import so the module-level helpers can use it.
PATHS = Paths()


def main() -> int:
    # Make sure the tree we need exists before we log or spawn anything.
    try:
        PATHS.logs_dir.mkdir(parents=True, exist_ok=True)
        PATHS.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    _setup_logging()

    guard = acquire_single_instance()
    if guard is None:
        # Tray-less fallback: nothing to click, just say why and leave 0.
        print(f"{APP_NAME} is already running (tray icon is in the "
              f"notification area).")
        log.info("another instance already holds the single-instance port")
        return 0

    log.info("%s tray starting; root=%s data=%s", APP_NAME, PATHS.root,
             PATHS.data_dir)
    try:
        TrayApp().run()
    finally:
        try:
            guard.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
