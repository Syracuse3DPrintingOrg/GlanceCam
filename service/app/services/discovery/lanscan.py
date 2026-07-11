"""LAN scan for IP cameras: TCP port probe plus snapshot-path fingerprinting.

Probe every host on a /24 (or smaller) for the ports a camera commonly answers
on, then, for hosts speaking HTTP(S), try a short list of well-known snapshot
paths and keep the first that returns an actual image. A host that only speaks
RTSP is still reported (with a note) so the user knows a camera is there even
though we cannot build a browser snapshot for it. A host that answers 401/403 is
reported as needing a login, so the user can re-probe it with credentials.

Ported from PantryRaider's ``camera_scan.py``. Kept pure where it counts: the
per-host probe takes an injectable ``fetch`` so tests exercise the tagging and
image-magic logic without a network, and the CIDR/interface helpers are plain
functions. Results are proposed Camera dicts (see ``discovery.PROPOSAL_FIELDS``)
that the settings UI can one-click add.
"""
from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import httpx

# Ports a network camera commonly answers on. 554/8554 are RTSP (not viewable in
# a browser); the rest are HTTP(S) front-ends that may expose a JPEG snapshot.
CAMERA_PORTS = (554, 8554, 80, 88, 443, 8000, 8080, 8443, 37777)
_HTTP_PORTS = (80, 88, 8000, 8080)
_HTTPS_PORTS = (443, 8443)
_RTSP_PORTS = (554, 8554)

# Snapshot paths across common camera brands, each tagged with the brand it
# implies. Tried in order; the first that returns an image wins. The brand label
# is surfaced so the user can recognise their camera. Kept short so a host is
# probed quickly.
SNAPSHOT_PATHS_BRANDS = (
    ("/snapshot.jpg",                          ""),
    ("/snap.jpg",                              ""),
    ("/image.jpg",                             ""),
    ("/jpg/image.jpg",                         ""),
    ("/cgi-bin/snapshot.cgi",                  ""),
    ("/axis-cgi/jpg/image.cgi",                "Axis"),
    ("/ISAPI/Streaming/channels/101/picture",  "Hikvision"),
    ("/cgi-bin/api.cgi?cmd=Snap&channel=0",    "Reolink"),
    ("/onvif-http/snapshot",                   "ONVIF"),
    ("/tmpfs/auto.jpg",                        "Dahua/Amcrest"),
)

# Refuse anything larger than a /22 (1024 hosts): a bigger sweep is slow and is
# almost never what a home user wants.
MAX_HOSTS = 1024


def _image_size(data: bytes) -> Optional[list]:
    """Best-effort ``[w, h]`` of a JPEG/PNG snapshot, or None.

    Pillow is optional: if it is not installed the scan still works, it just
    cannot report a resolution for a snapshot host.
    """
    try:
        from io import BytesIO

        from PIL import Image  # optional dependency, degrade gracefully
        with Image.open(BytesIO(data)) as im:
            w, h = im.size
        return [int(w), int(h)] if w and h else None
    except Exception:  # noqa: BLE001 - missing PIL or an unreadable image
        return None


def _looks_like_image(resp: httpx.Response) -> bool:
    """True when an HTTP response body is a JPEG/PNG image."""
    ctype = resp.headers.get("content-type", "").lower()
    if ctype.startswith("image/"):
        return True
    body = resp.content[:3]
    return body[:2] == b"\xff\xd8" or body[:3] == b"\x89PN"  # JPEG / PNG magic


def _port_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((ip, port)) == 0
    except OSError:
        return False


def _probe_http(ip: str, port: int, scheme: str, timeout: float,
                fetch: Optional[Callable] = None) -> tuple[str, bool, str, Optional[list]]:
    """Probe snapshot paths on ``scheme://ip:port``.

    Returns ``(snapshot_url, auth_required, brand, resolution)``. A 200 image
    wins immediately, with the brand implied by the matching path and the
    resolution where Pillow can read it; a 401/403 on any path records
    auth_required, since a password-protected camera is still a camera.
    ``fetch`` is injectable for tests. Always verify=True: a self-signed camera
    is re-probed explicitly by the user later, never trusted blindly here.
    """
    def _default_fetch(url: str):
        return httpx.get(url, timeout=timeout, follow_redirects=True)
    fetch = fetch or _default_fetch
    default_port = 80 if scheme == "http" else 443
    base = f"{scheme}://{ip}" if port == default_port else f"{scheme}://{ip}:{port}"
    auth = False
    for path, brand in SNAPSHOT_PATHS_BRANDS:
        url = base + path
        try:
            resp = fetch(url)
        except Exception:  # noqa: BLE001 - one dead path never stops the probe
            continue
        code = getattr(resp, "status_code", 0)
        if code == 200 and _looks_like_image(resp):
            res = _image_size(getattr(resp, "content", b"") or b"")
            return url, False, brand, res
        if code in (401, 403):
            auth = True
    return "", auth, "", None


def _proposal(ip: str, open_ports: list, snapshot_url: str, rtsp: bool,
              auth: bool, brand: str, resolution: Optional[list]) -> dict:
    """Build a proposed Camera dict from a probe result.

    Scanned hosts are proposed as ``manual`` cameras: the scan cannot know the
    RTSP stream path for a generic host, so ``main_url`` is left blank for the
    user (or the ONVIF/Reolink probes) to fill. The brand hint and any caveat go
    in ``notes``.
    """
    notes = []
    if brand:
        notes.append(f"Looks like {brand}.")
    if auth and not snapshot_url:
        notes.append("Needs a login: re-probe with the camera's username and "
                     "password.")
    if rtsp and not snapshot_url:
        notes.append("Answers RTSP only. Add its RTSP stream address to view it.")
    return {
        "source": "manual",
        "name": f"Camera at {ip}",
        "main_url": None,
        "sub_url": None,
        "snapshot_url": snapshot_url or None,
        "main_resolution": resolution,
        "notes": " ".join(notes),
        # Extra fields the UI uses to drive the add/re-probe flow; harmless in a
        # saved camera because the store drops keys it does not allow.
        "ip": ip,
        "ports": open_ports,
        "brand": brand,
        "rtsp": rtsp,
        "auth_required": auth,
    }


def probe_camera(ip: str, timeout: float = 0.4,
                 fetch: Optional[Callable] = None) -> Optional[dict]:
    """Probe one host. Returns a proposal dict when it looks like a camera, else
    None (no camera port open, or an open port that fingerprints as nothing).

    A host is proposed when a snapshot was found, it answers RTSP, or a snapshot
    path needed auth. Hosts that merely have a camera port open but return
    nothing image-like are dropped so the results stay meaningful.
    """
    open_ports = [p for p in CAMERA_PORTS if _port_open(ip, p, timeout)]
    if not open_ports:
        return None
    snapshot_url = ""
    auth = False
    brand = ""
    resolution: Optional[list] = None
    for p in open_ports:
        scheme = "http" if p in _HTTP_PORTS else ("https" if p in _HTTPS_PORTS else "")
        if not scheme:
            continue
        url, a, b, res = _probe_http(ip, p, scheme, timeout, fetch=fetch)
        auth = auth or a
        if url:
            snapshot_url, brand, resolution = url, b, res
            break
    rtsp = any(p in _RTSP_PORTS for p in open_ports)
    if not (snapshot_url or rtsp or auth):
        return None
    return _proposal(ip, open_ports, snapshot_url, rtsp, auth, brand, resolution)


def probe_with_auth(host: str, username: str = "", password: str = "",
                    timeout: float = 1.5,
                    fetch: Optional[Callable] = None) -> dict:
    """Re-probe one host's snapshot paths using HTTP credentials.

    Used when a scan reports a password-protected camera: with the login we can
    find a working snapshot path and read the brand and resolution. Tries Digest
    then Basic auth (cameras use both). Returns
    ``{ok, snapshot_url, brand, resolution, error}``. Credentials are NOT baked
    into the returned URL: the camera store keeps them server-side and the app
    fetches the snapshot with the header the camera expects.
    """
    open_ports = [p for p in CAMERA_PORTS if _port_open(host, p, min(timeout, 0.6))]
    http_ports = [p for p in open_ports if p in _HTTP_PORTS or p in _HTTPS_PORTS]
    if not http_ports:
        return {"ok": False, "error": "No web port is open on that host."}

    def _auth_fetch(auth_obj):
        def _f(url: str):
            return httpx.get(url, timeout=timeout, follow_redirects=True,
                             auth=auth_obj)
        return _f

    for p in http_ports:
        scheme = "http" if p in _HTTP_PORTS else "https"
        for auth_obj in (httpx.DigestAuth(username, password) if username else None,
                         (username, password) if username else None):
            f = fetch or _auth_fetch(auth_obj)
            url, _a, brand, res = _probe_http(host, p, scheme, timeout, fetch=f)
            if url:
                return {"ok": True, "snapshot_url": url, "brand": brand,
                        "resolution": res, "error": ""}
            if fetch is not None:
                break  # a test injects a single fetch; do not loop auth schemes
    return {"ok": False,
            "error": "No snapshot path worked with those credentials."}


def _outbound_ip() -> Optional[str]:
    """This host's outbound interface address, or None.

    Connecting a UDP socket toward a public address sends nothing but lets the OS
    pick the outbound interface, whose address we read back.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def _local_ips() -> set:
    """Addresses that resolve to this host, so a scan can skip itself."""
    ips = {"127.0.0.1"}
    out = _outbound_ip()
    if out:
        ips.add(out)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return ips


def _candidate_ips() -> set:
    """All non-loopback IPv4 addresses this host can see."""
    return {ip for ip in _local_ips() if not ip.startswith("127.")}


def _rank_ip(ip: str) -> int:
    """Lower rank = more likely a real home/office LAN. Docker's default bridge
    lives in 172.16/12, so that range is ranked last."""
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("10."):
        return 1
    if ip.startswith("172."):
        return 3
    return 2


def looks_dockerish(cidr: str) -> bool:
    """True when a CIDR is in Docker's default bridge range (172.16/12)."""
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        return net.subnet_of(ipaddress.ip_network("172.16.0.0/12"))
    except (ValueError, TypeError):
        return False


def best_lan_cidr() -> Optional[str]:
    """Best guess at the host's real LAN /24, preferring 192.168/10 over a Docker
    172.x interface. Returns None when nothing is found."""
    cands = _candidate_ips()
    if not cands:
        return None
    ip = sorted(cands, key=lambda x: (_rank_ip(x), x))[0]
    try:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    except ValueError:
        return None


def scan(cidr: str, timeout: float = 0.4, concurrency: int = 128,
         fetch: Optional[Callable] = None,
         report: Optional[Callable[[int, int], None]] = None) -> dict:
    """Scan a CIDR for IP cameras.

    Returns ``{"cameras": [proposal, ...], "scanned": int}`` or
    ``{"error": ...}``. ``report(done, total)`` is called as hosts complete so a
    polling UI can show progress.
    """
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        return {"error": f"That is not a valid network range: {exc}"}
    if net.num_addresses > MAX_HOSTS:
        return {"error": "That range is too large (max 1024 hosts). Use a /22 "
                         "or smaller."}
    skip = _local_ips()
    hosts = [str(h) for h in net.hosts() if str(h) not in skip]
    total = len(hosts)
    if report:
        report(0, total)

    def _safe(ip: str) -> Optional[dict]:
        try:
            return probe_camera(ip, timeout, fetch=fetch)
        except Exception:  # noqa: BLE001 - one dead host never aborts the sweep
            return None

    cameras: list[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for result in pool.map(_safe, hosts):
            done += 1
            if report and (done % 16 == 0 or done == total):
                report(done, total)
            if result:
                cameras.append(result)
    cameras.sort(key=lambda c: tuple(int(o) for o in c["ip"].split(".")))
    return {"cameras": cameras, "scanned": total}
