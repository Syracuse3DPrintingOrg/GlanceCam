from app.services import netguard
from app.services.netguard import _is_blocked_ip, _fetch_host


def test_loopback_blocked():
    assert _is_blocked_ip("127.0.0.1")
    assert _is_blocked_ip("::1")


def test_cloud_metadata_blocked():
    # 169.254.169.254 is link-local, which covers the cloud metadata endpoint.
    assert _is_blocked_ip("169.254.169.254")


def test_multicast_and_unspecified_blocked():
    assert _is_blocked_ip("224.0.0.1")
    assert _is_blocked_ip("0.0.0.0")


def test_private_lan_allowed():
    assert not _is_blocked_ip("192.168.1.50")
    assert not _is_blocked_ip("10.0.0.5")
    assert not _is_blocked_ip("172.16.4.4")


def test_public_allowed():
    assert not _is_blocked_ip("8.8.8.8")


def test_ipv4_mapped_loopback_blocked():
    assert _is_blocked_ip("::ffff:127.0.0.1")


def test_ipv6_zone_id_stripped():
    # A link-local address with a zone id is still link-local.
    assert _is_blocked_ip("fe80::1%eth0")


def test_garbage_ip_blocked():
    assert _is_blocked_ip("not-an-ip")


def test_fetch_host_parses_url_and_bare():
    assert _fetch_host("rtsp://192.168.1.5:554/stream") == "192.168.1.5"
    assert _fetch_host("192.168.1.5:80") == "192.168.1.5"
    assert _fetch_host("http://cam.local/snap.jpg") == "cam.local"
    assert _fetch_host("") == ""


def test_is_blocked_fetch_host_localhost_resolves_blocked():
    # localhost resolves to loopback, which must be blocked.
    assert netguard.is_blocked_fetch_host("http://localhost:8000/x")


def test_unresolvable_host_respects_fail_flag():
    bogus = "http://no-such-host.invalid/x"
    assert netguard.is_blocked_fetch_host(bogus, fail_closed=True)
    assert not netguard.is_blocked_fetch_host(bogus, fail_closed=False)


def test_guard_url_delegates():
    assert netguard.guard_url("http://127.0.0.1/x")
    assert not netguard.guard_url("http://192.168.1.9/x")
