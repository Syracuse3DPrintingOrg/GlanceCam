"""Minimal, hand-rolled ONVIF discovery: no external onvif library.

Two jobs, kept small:

1. WS-Discovery: send a multicast Probe for ``NetworkVideoTransmitter`` to
   239.255.255.250:3702 and collect the ``ProbeMatch`` replies, each carrying
   the device's service address (XAddrs) and scopes (name, hardware, location).
2. Media queries: with credentials, POST ``GetProfiles`` then ``GetStreamUri``
   to the device's media service to read each profile's RTSP address, then
   classify the highest-resolution profile as the main stream and the lowest as
   the sub stream.

ONVIF uses WS-Security ``UsernameToken`` with a ``PasswordDigest``: the digest is
``base64(sha1(nonce + created + password))``. That, and every SOAP body and
reply, is built and parsed by pure functions so they unit-test against fixture
XML with no network. The network functions are thin wrappers around them.

XML is parsed with defusedxml when present (it hardens against entity-expansion
and external-entity attacks on data that ultimately comes off the LAN), falling
back to the stdlib parser. Element lookups match on local tag name so the many
ONVIF namespaces do not have to be tracked.
"""
from __future__ import annotations

import base64
import hashlib
import socket
import struct
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

try:  # defusedxml hardens XML parsing; the stdlib parser is the fallback.
    from defusedxml import ElementTree as _ET
except Exception:  # noqa: BLE001 - defusedxml is optional
    import xml.etree.ElementTree as _ET  # type: ignore

_WSD_ADDR = "239.255.255.250"
_WSD_PORT = 3702

_NS_SOAP = "http://www.w3.org/2003/05/soap-envelope"
_NS_WSA = "http://schemas.xmlsoap.org/ws/2004/08/addressing"
_NS_WSD = "http://schemas.xmlsoap.org/ws/2005/04/discovery"
_NS_DN = "http://www.onvif.org/ver10/network/wsdl"
_NS_TRT = "http://www.onvif.org/ver10/media/wsdl"
_NS_TT = "http://www.onvif.org/ver10/schema"
_NS_WSSE = ("http://docs.oasis-open.org/wss/2004/01/"
            "oasis-200401-wss-wssecurity-secext-1.0.xsd")
_NS_WSU = ("http://docs.oasis-open.org/wss/2004/01/"
           "oasis-200401-wss-wssecurity-utility-1.0.xsd")
_PW_DIGEST_TYPE = ("http://docs.oasis-open.org/wss/2004/01/"
                   "oasis-200401-wss-username-token-profile-1.0#PasswordDigest")
_B64_ENCODING = ("http://docs.oasis-open.org/wss/2004/01/"
                 "oasis-200401-wss-soap-message-security-1.0#Base64Binary")


# --- pure builders ----------------------------------------------------------

def build_probe(message_id: Optional[str] = None) -> str:
    """The WS-Discovery Probe envelope for NetworkVideoTransmitter devices."""
    mid = message_id or f"uuid:{uuid.uuid4()}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<e:Envelope xmlns:e="{_NS_SOAP}" xmlns:w="{_NS_WSA}" '
        f'xmlns:d="{_NS_WSD}" xmlns:dn="{_NS_DN}">'
        "<e:Header>"
        f"<w:MessageID>{mid}</w:MessageID>"
        '<w:To e:mustUnderstand="true">'
        "urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
        '<w:Action e:mustUnderstand="true">'
        "http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
        "</e:Header>"
        "<e:Body>"
        "<d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe>"
        "</e:Body></e:Envelope>"
    )


def password_digest(nonce: bytes, created: str, password: str) -> str:
    """The WS-Security PasswordDigest: base64(sha1(nonce + created + password))."""
    digest = hashlib.sha1(nonce + created.encode("utf-8")
                          + password.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def _security_header(username: str, password: str,
                     nonce: Optional[bytes] = None,
                     created: Optional[str] = None) -> str:
    """A WS-Security UsernameToken header, or "" when no username is given."""
    if not username:
        return ""
    nonce = nonce if nonce is not None else uuid.uuid4().bytes
    created = created or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    b64_nonce = base64.b64encode(nonce).decode("ascii")
    digest = password_digest(nonce, created, password or "")
    return (
        f'<s:Header><Security xmlns="{_NS_WSSE}" '
        's:mustUnderstand="1"><UsernameToken>'
        f"<Username>{_esc(username)}</Username>"
        f'<Password Type="{_PW_DIGEST_TYPE}">{digest}</Password>'
        f'<Nonce EncodingType="{_B64_ENCODING}">{b64_nonce}</Nonce>'
        f'<Created xmlns="{_NS_WSU}">{created}</Created>'
        "</UsernameToken></Security></s:Header>"
    )


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def build_get_profiles(username: str = "", password: str = "",
                       nonce: Optional[bytes] = None,
                       created: Optional[str] = None) -> str:
    """The GetProfiles SOAP request (optionally authenticated)."""
    header = _security_header(username, password, nonce, created)
    return (
        f'<s:Envelope xmlns:s="{_NS_SOAP}" xmlns:trt="{_NS_TRT}">'
        f"{header}<s:Body><trt:GetProfiles/></s:Body></s:Envelope>"
    )


def build_get_stream_uri(profile_token: str, username: str = "",
                         password: str = "", nonce: Optional[bytes] = None,
                         created: Optional[str] = None) -> str:
    """The GetStreamUri SOAP request for one profile (RTP-Unicast over RTSP)."""
    header = _security_header(username, password, nonce, created)
    return (
        f'<s:Envelope xmlns:s="{_NS_SOAP}" xmlns:trt="{_NS_TRT}" '
        f'xmlns:tt="{_NS_TT}">{header}<s:Body><trt:GetStreamUri>'
        "<trt:StreamSetup>"
        "<tt:Stream>RTP-Unicast</tt:Stream>"
        "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
        "</trt:StreamSetup>"
        f"<trt:ProfileToken>{_esc(profile_token)}</trt:ProfileToken>"
        "</trt:GetStreamUri></s:Body></s:Envelope>"
    )


# --- pure parsers -----------------------------------------------------------

def _local(tag: str) -> str:
    """The local name of a possibly-namespaced ``{ns}tag``."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _findall_local(root, name: str) -> list:
    """Every descendant element whose local tag name is ``name``."""
    return [el for el in root.iter() if _local(el.tag) == name]


def _first_text(root, name: str) -> str:
    for el in _findall_local(root, name):
        if el.text and el.text.strip():
            return el.text.strip()
    return ""


def parse_probe_matches(xml_text) -> list:
    """Parse a WS-Discovery ProbeMatch reply into device dicts.

    Each dict is ``{xaddrs: [...], scopes: [...], types: str, urn: str,
    name: str, hardware: str}``. Names and hardware are read from the ONVIF
    scope URIs (``.../name/Foo``, ``.../hardware/Bar``). Returns [] on any parse
    error so a malformed reply from one device never breaks the sweep.
    """
    try:
        root = _ET.fromstring(xml_text)
    except Exception:  # noqa: BLE001 - a garbled reply is just skipped
        return []
    out = []
    for match in _findall_local(root, "ProbeMatch"):
        xaddrs = _first_text(match, "XAddrs")
        scopes = _first_text(match, "Scopes")
        types = _first_text(match, "Types")
        urn = _first_text(match, "Address")
        scope_list = scopes.split() if scopes else []
        out.append({
            "xaddrs": xaddrs.split() if xaddrs else [],
            "scopes": scope_list,
            "types": types,
            "urn": urn,
            "name": _scope_value(scope_list, "name"),
            "hardware": _scope_value(scope_list, "hardware"),
        })
    return out


def _scope_value(scopes: list, key: str) -> str:
    """Pull a value out of an ONVIF scope URI list (``onvif://.../key/value``)."""
    marker = f"/{key}/"
    for scope in scopes:
        idx = scope.find(marker)
        if idx != -1:
            from urllib.parse import unquote
            return unquote(scope[idx + len(marker):]).strip()
    return ""


def parse_profiles(xml_text) -> list:
    """Parse a GetProfiles reply into ``[{token, name, width, height, encoding}]``.

    Resolution is read from the VideoEncoderConfiguration's Resolution element.
    Returns [] on a parse error or a SOAP Fault.
    """
    try:
        root = _ET.fromstring(xml_text)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for prof in _findall_local(root, "Profiles"):
        token = prof.attrib.get("token") or prof.attrib.get("Token") or ""
        name = _first_text(prof, "Name")
        width = height = 0
        encoding = ""
        for res in _findall_local(prof, "Resolution"):
            w = _first_text(res, "Width")
            h = _first_text(res, "Height")
            if w.isdigit() and h.isdigit():
                width, height = int(w), int(h)
                break
        encoding = _first_text(prof, "Encoding")
        out.append({"token": token, "name": name, "width": width,
                    "height": height, "encoding": encoding})
    return out


def parse_stream_uri(xml_text) -> str:
    """Pull the RTSP URI out of a GetStreamUri reply, or "" on a fault/parse error."""
    try:
        root = _ET.fromstring(xml_text)
    except Exception:  # noqa: BLE001
        return ""
    return _first_text(root, "Uri")


def classify_profiles(profiles: list) -> dict:
    """Pick the main (highest resolution) and sub (lowest) profile tokens.

    Returns ``{"main": token|None, "sub": token|None}``. With a single profile,
    main is set and sub is None. Profiles missing a resolution sort as smallest
    so a real HD profile is preferred for main.
    """
    usable = [p for p in profiles if p.get("token")]
    if not usable:
        return {"main": None, "sub": None}

    def area(p) -> int:
        return int(p.get("width", 0)) * int(p.get("height", 0))

    ordered = sorted(usable, key=area)
    main = ordered[-1]["token"]
    sub = ordered[0]["token"] if len(ordered) > 1 else None
    return {"main": main, "sub": sub}


# --- network ----------------------------------------------------------------

def ws_discovery(timeout: float = 3.0) -> list:
    """Multicast a WS-Discovery Probe and collect ProbeMatch replies.

    Returns a de-duplicated list of device dicts (see ``parse_probe_matches``).
    Never raises: on a socket error it returns whatever was collected so far
    (usually nothing), so a locked-down network degrades to "found none".
    """
    probe = build_probe().encode("utf-8")
    devices: dict[str, dict] = {}
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                        struct.pack("b", 2))
        sock.settimeout(timeout)
        sock.sendto(probe, (_WSD_ADDR, _WSD_PORT))
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                sock.settimeout(max(0.1, deadline - time.monotonic()))
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            except OSError:
                break
            for dev in parse_probe_matches(data):
                key = dev.get("urn") or (dev.get("xaddrs") or [""])[0]
                if key and key not in devices:
                    devices[key] = dev
    except OSError:
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return list(devices.values())


def get_streams(xaddr: str, username: str = "", password: str = "",
                timeout: float = 5.0) -> dict:
    """Fetch a device's profiles and resolve the main/sub RTSP addresses.

    ``xaddr`` is the media (or device) service URL from a ProbeMatch. Returns
    ``{ok, main_url, sub_url, profiles, error}``. Never raises: a timeout, an
    auth rejection, or a SOAP fault comes back as ``ok=False`` with a message.
    Verify is left on (verify=True): an ONVIF device on https is uncommon, and a
    self-signed one is a deliberate per-camera choice made elsewhere.
    """
    if not xaddr:
        return {"ok": False, "error": "No device service address."}
    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            pr = client.post(xaddr, content=build_get_profiles(username, password),
                             headers=headers)
            if pr.status_code in (401, 403):
                return {"ok": False,
                        "error": "The camera rejected that username or password."}
            profiles = parse_profiles(pr.text)
            if not profiles:
                return {"ok": False,
                        "error": "The camera returned no video profiles."}
            picks = classify_profiles(profiles)
            uris: dict[str, Optional[str]] = {}
            for role in ("main", "sub"):
                token = picks.get(role)
                if not token:
                    uris[role] = None
                    continue
                sr = client.post(
                    xaddr, headers=headers,
                    content=build_get_stream_uri(token, username, password))
                uris[role] = parse_stream_uri(sr.text) or None
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Could not reach the camera: {exc}"}
    return {
        "ok": True,
        "main_url": uris.get("main"),
        "sub_url": uris.get("sub"),
        "profiles": profiles,
        "error": "",
    }
