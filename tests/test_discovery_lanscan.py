"""LAN scan: pure tagging, proposal shape, and the scan orchestration."""
from app.services.discovery import lanscan


def _make_jpeg(w=1, h=1):
    """A real JPEG so both the image-magic check and Pillow's size read work.

    Falls back to a bare JPEG magic prefix when Pillow is not installed (the
    resolution assertions are then skipped by the callers that need them).
    """
    try:
        from io import BytesIO

        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (w, h), (255, 0, 0)).save(buf, format="JPEG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return b"\xff\xd8\xff\xe0" + b"\x00" * 16


_JPEG = _make_jpeg(1, 1)


class FakeResp:
    def __init__(self, status_code=200, content=b"", content_type="image/jpeg"):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}


def test_looks_like_image_by_magic_when_no_ctype():
    resp = FakeResp(content=_JPEG, content_type="application/octet-stream")
    assert lanscan._looks_like_image(resp)


def test_looks_like_image_false_for_html():
    resp = FakeResp(content=b"<html>nope", content_type="text/html")
    assert not lanscan._looks_like_image(resp)


def test_probe_http_tags_brand_from_matching_path():
    # Only the Reolink CGI path returns an image; the rest 404.
    def fetch(url):
        if "cmd=Snap" in url:
            return FakeResp(200, _JPEG, "image/jpeg")
        return FakeResp(404, b"", "text/html")

    url, auth, brand, res = lanscan._probe_http("192.168.1.5", 80, "http",
                                                0.1, fetch=fetch)
    assert "cmd=Snap" in url
    assert brand == "Reolink"
    assert auth is False
    assert res == [1, 1]


def test_probe_http_records_auth_required():
    def fetch(url):
        return FakeResp(401, b"", "text/html")

    url, auth, brand, res = lanscan._probe_http("192.168.1.5", 80, "http",
                                                0.1, fetch=fetch)
    assert url == ""
    assert auth is True
    assert res is None


def test_proposal_shape_and_notes():
    p = lanscan._proposal("192.168.1.9", [80], "http://192.168.1.9/snap.jpg",
                          rtsp=False, auth=False, brand="Axis",
                          resolution=[1280, 720])
    assert p["source"] == "manual"
    assert p["snapshot_url"] == "http://192.168.1.9/snap.jpg"
    assert p["main_resolution"] == [1280, 720]
    assert "Axis" in p["notes"]
    assert p["ip"] == "192.168.1.9"


def test_proposal_rtsp_only_note():
    p = lanscan._proposal("192.168.1.9", [554], "", rtsp=True, auth=False,
                          brand="", resolution=None)
    assert p["snapshot_url"] is None
    assert "RTSP" in p["notes"]
    assert p["rtsp"] is True


def test_looks_dockerish():
    assert lanscan.looks_dockerish("172.17.0.0/24")
    assert not lanscan.looks_dockerish("192.168.1.0/24")
    assert not lanscan.looks_dockerish("garbage")


def test_rank_prefers_home_lan_over_docker():
    assert lanscan._rank_ip("192.168.1.5") < lanscan._rank_ip("172.17.0.2")
    assert lanscan._rank_ip("10.0.0.5") < lanscan._rank_ip("172.17.0.2")


def test_scan_rejects_bad_cidr():
    assert "error" in lanscan.scan("not-a-cidr")


def test_scan_rejects_too_large():
    assert "too large" in lanscan.scan("10.0.0.0/8")["error"]


def test_scan_collects_and_sorts(monkeypatch):
    # Avoid real sockets: fake the per-host probe.
    def fake_probe(ip, timeout, fetch=None):
        if ip in ("192.168.1.5", "192.168.1.3"):
            return lanscan._proposal(ip, [80], f"http://{ip}/snap.jpg",
                                     False, False, "", None)
        return None

    monkeypatch.setattr(lanscan, "probe_camera", fake_probe)
    seen = []
    out = lanscan.scan("192.168.1.0/29", report=lambda d, t: seen.append((d, t)))
    ips = [c["ip"] for c in out["cameras"]]
    assert ips == ["192.168.1.3", "192.168.1.5"]  # numeric sort, not lexical
    assert out["scanned"] == 6  # /29 usable hosts
    assert seen and seen[-1][0] == seen[-1][1]  # final progress reaches total


def test_probe_with_auth_no_http_port(monkeypatch):
    monkeypatch.setattr(lanscan, "_port_open", lambda ip, p, t: False)
    out = lanscan.probe_with_auth("192.168.1.9", "admin", "pw")
    assert out["ok"] is False
    assert "web port" in out["error"]


def test_probe_with_auth_finds_snapshot(monkeypatch):
    monkeypatch.setattr(lanscan, "_port_open",
                        lambda ip, p, t: p == 80)

    def fetch(url):
        return FakeResp(200, _JPEG, "image/jpeg")

    out = lanscan.probe_with_auth("192.168.1.9", "admin", "pw", fetch=fetch)
    assert out["ok"] is True
    assert out["snapshot_url"].startswith("http://192.168.1.9")
    assert out["resolution"] == [1, 1]
