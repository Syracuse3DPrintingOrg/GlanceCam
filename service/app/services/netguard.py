"""SSRF guard for server-side camera fetches.

A camera address is meant to point at a camera on the LAN, not at this device
or an internal-only service. This module resolves an address to every IP it
maps to and refuses the fetch if any of them is one no real camera would use
(loopback, link-local including the cloud metadata address 169.254.169.254,
the unspecified address, or a multicast/reserved range). RFC1918 private
ranges (192.168/16, 10/8, 172.16/12) are where real cameras live, so they stay
allowed alongside ordinary public addresses.

Semantics match PantryRaider's ``is_blocked_fetch_host``: block if ANY resolved
address is disallowed (so a name that points partly at loopback is caught), and
let the caller choose fail-open or fail-closed for a host that will not resolve.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# A short, user-forward refusal shared by every guarded fetch. Kept vague on
# purpose: it should read as "that address will not work" without hinting at
# what is behind it.
BLOCKED_HOST_MESSAGE = (
    "That camera address points at this device or an internal address, so it "
    "cannot be used. Enter the camera's own address on your network.")


def _fetch_host(host_or_url: str) -> str:
    """The bare hostname/IP from a URL or a plain ``host[:port]`` string.

    Accepts a full ``scheme://host:port/path`` URL, or just ``host`` /
    ``host:port``. Returns "" when nothing usable is present. Pure, so the block
    rule stays unit-testable.
    """
    s = (host_or_url or "").strip()
    if not s:
        return ""
    # Give a bare host[:port] a scheme-less authority so urlparse reads it as a
    # netloc rather than a path (urlparse("host:80") treats "host" as a scheme).
    if "://" not in s:
        s = "//" + s
    try:
        return urlparse(s).hostname or ""
    except ValueError:
        return ""


def _is_blocked_ip(addr: str) -> bool:
    """True when a single resolved IP is one no real camera would use.

    Blocks loopback (127.0.0.0/8, ::1), link-local (169.254.0.0/16, which
    covers the cloud metadata address 169.254.169.254, and fe80::/10), the
    unspecified address (0.0.0.0, ::), and multicast/reserved ranges. Does NOT
    block private LAN ranges: those are where real cameras live.
    """
    a = (addr or "").split("%", 1)[0]  # drop an IPv6 zone id like %eth0
    try:
        ip = ipaddress.ip_address(a)
    except ValueError:
        return True
    # An IPv4 address wrapped as ::ffff:127.0.0.1 must be judged as its IPv4.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(ip.is_loopback or ip.is_link_local or ip.is_unspecified
                or ip.is_multicast or ip.is_reserved)


def is_blocked_fetch_host(host: str, fail_closed: bool = False) -> bool:
    """True when a server-side fetch to this host/URL must be refused.

    Resolves the hostname to every IP it maps to and blocks the fetch if ANY
    resolved address is disallowed, so a name that points partly at loopback (a
    rebinding-style trick) is caught. Ordinary LAN cameras and public addresses
    are allowed.

    ``fail_closed`` decides what to do with a host that cannot be resolved. The
    arbitrary-URL preview passes True (an unresolvable host is unreachable
    anyway, and refusing is the safe default). A saved camera passes False, so a
    momentary DNS hiccup does not turn a real camera into a blocked one: it is
    only refused when it actively resolves to a disallowed address.
    """
    h = _fetch_host(host)
    if not h:
        return fail_closed
    try:
        infos = socket.getaddrinfo(h, None)
    except OSError:
        return fail_closed
    addrs = {info[4][0] for info in infos if info and info[4]}
    if not addrs:
        return fail_closed
    return any(_is_blocked_ip(addr) for addr in addrs)


def guard_url(url: str, fail_closed: bool = False) -> bool:
    """Convenience: parse ``url`` and return True when it must be refused.

    Same fail-open/fail-closed contract as ``is_blocked_fetch_host``.
    """
    return is_blocked_fetch_host(url, fail_closed=fail_closed)
