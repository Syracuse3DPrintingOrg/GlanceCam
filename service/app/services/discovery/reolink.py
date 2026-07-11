"""Reolink discovery over the camera's HTTP JSON API.

Given a host and credentials, sign in for a short-lived token (``cmd=Login``),
read the device info and channel list (an NVR fronts several cameras on one
address), and build a proposed Camera per channel: the RTSP main and sub stream
addresses and the snapshot CGI. Reolink channels are 0-based in the API and
1-based in the RTSP path (``h264Preview_01_main``), which the URL builders below
handle.

This is one-shot discovery, so the token is used inline and thrown away: no
lease cache like the always-on kiosk poll needs. TLS is tried with verification
on; if the camera only offers a self-signed cert the code falls back to plain
HTTP rather than turning verification off, so a proxy or MITM can never be
silently accepted.

The JSON parsers are pure and unit-tested against fixture payloads; the network
wrapper around them catches every error into the result dict.
"""
from __future__ import annotations

import ssl
from typing import Optional

import httpx


# --- pure parsers -----------------------------------------------------------

def _first_item(payload) -> dict:
    """Reolink replies are a list of one command result; unwrap it defensively."""
    if isinstance(payload, list) and payload:
        item = payload[0]
        return item if isinstance(item, dict) else {}
    if isinstance(payload, dict):
        return payload
    return {}


def parse_login_token(payload) -> Optional[str]:
    """The token name from a Login reply, or None when the login was rejected."""
    item = _first_item(payload)
    if item.get("code", 1) != 0 or item.get("error"):
        return None
    token = (((item.get("value") or {}).get("Token") or {}).get("name"))
    return str(token) if token else None


def parse_dev_info(payload) -> dict:
    """The device info block from a GetDevInfo reply (name, model, channelNum)."""
    item = _first_item(payload)
    value = item.get("value") if isinstance(item.get("value"), dict) else {}
    return value.get("DevInfo") if isinstance(value.get("DevInfo"), dict) else {}


def parse_channels(dev_info: dict, channel_status_payload=None) -> list:
    """Work out the channel list as ``[{channel, name, online}]``.

    Prefers an explicit GetChannelStatus reply (an NVR reports each channel's
    name and online flag there); otherwise falls back to the DevInfo
    ``channelNum`` count. Always returns at least channel 0 so a single camera
    that reports nothing useful still yields one proposal.
    """
    item = _first_item(channel_status_payload) if channel_status_payload else {}
    value = item.get("value") if isinstance(item.get("value"), dict) else {}
    status = value.get("status")
    if isinstance(status, list) and status:
        chans = []
        for entry in status:
            if not isinstance(entry, dict):
                continue
            ch = entry.get("channel")
            if ch is None:
                continue
            chans.append({
                "channel": int(ch),
                "name": str(entry.get("name", "") or ""),
                "online": bool(entry.get("online", 1)),
            })
        if chans:
            return chans
    try:
        count = int(dev_info.get("channelNum", 1) or 1)
    except (TypeError, ValueError):
        count = 1
    count = max(1, count)
    return [{"channel": ch, "name": "", "online": True} for ch in range(count)]


def rtsp_url(host: str, channel: int, quality: str = "main",
             port: int = 554) -> str:
    """The Reolink RTSP address for a channel. Channels are 1-based in the path.

    Credentials are deliberately left out: the camera store holds them
    server-side and go2rtc receives them embedded at sync time, so they never
    appear in a browser-facing field.
    """
    quality = "sub" if str(quality).lower() == "sub" else "main"
    portpart = "" if port == 554 else f":{int(port)}"
    return f"rtsp://{host}{portpart}/h264Preview_{channel + 1:02d}_{quality}"


def snapshot_url(host: str, channel: int, scheme: str = "http",
                 port: Optional[int] = None) -> str:
    """The Reolink Snap CGI address for a channel (credentials added server-side)."""
    default = 80 if scheme == "http" else 443
    portpart = "" if not port or port == default else f":{int(port)}"
    return (f"{scheme}://{host}{portpart}/cgi-bin/api.cgi"
            f"?cmd=Snap&channel={channel}")


def build_proposals(host: str, channels: list, dev_info: dict,
                    username: str = "", scheme: str = "http",
                    http_port: Optional[int] = None) -> list:
    """Build one proposed Camera dict per channel (see PROPOSAL_FIELDS)."""
    model = str(dev_info.get("model", "") or "").strip()
    dev_name = str(dev_info.get("name", "") or "").strip()
    proposals = []
    multi = len(channels) > 1
    for entry in channels:
        ch = int(entry.get("channel", 0))
        ch_name = str(entry.get("name", "") or "").strip()
        if ch_name:
            name = ch_name
        elif multi:
            name = f"{dev_name or model or 'Reolink'} channel {ch + 1}"
        else:
            name = dev_name or model or f"Reolink at {host}"
        notes = []
        if model:
            notes.append(f"Reolink {model}.")
        else:
            notes.append("Reolink.")
        if not entry.get("online", True):
            notes.append("This channel reports offline right now.")
        proposals.append({
            "source": "reolink",
            "name": name,
            "main_url": rtsp_url(host, ch, "main"),
            "sub_url": rtsp_url(host, ch, "sub"),
            "snapshot_url": snapshot_url(host, ch, scheme, http_port),
            "username": username,
            "notes": " ".join(notes),
            "channel": ch,
        })
    return proposals


# --- network ----------------------------------------------------------------

def _clean_host(host: str) -> str:
    """A bare host from what a user might paste (drops a scheme and trailing /)."""
    h = (host or "").strip().rstrip("/")
    for prefix in ("https://", "http://"):
        if h.lower().startswith(prefix):
            h = h[len(prefix):]
            break
    return h.split("/", 1)[0]


def _login_and_read(base: str, username: str, password: str,
                    timeout: float, verify: bool) -> dict:
    """One transport attempt: Login, then GetDevInfo and GetChannelStatus.

    Returns ``{ok, token?, dev_info?, channel_status?, error?}``. Raises
    httpx.HTTPError (including an SSLError) so the caller can decide whether to
    retry over plain HTTP.
    """
    api = f"{base}/cgi-bin/api.cgi"
    with httpx.Client(timeout=timeout, verify=verify,
                      follow_redirects=True) as client:
        login = client.post(api, params={"cmd": "Login"}, json=[{
            "cmd": "Login",
            "param": {"User": {"userName": username, "password": password}},
        }])
        if login.status_code in (401, 403):
            return {"ok": False,
                    "error": "The camera rejected that username or password."}
        token = parse_login_token(login.json())
        if not token:
            return {"ok": False,
                    "error": "The camera rejected that username or password."}
        dev = client.post(api, params={"cmd": "GetDevInfo", "token": token},
                          json=[{"cmd": "GetDevInfo", "param": {}}])
        chan = client.post(api, params={"cmd": "GetChannelStatus", "token": token},
                           json=[{"cmd": "GetChannelStatus", "param": {}}])
        return {
            "ok": True,
            "token": token,
            "dev_info": dev.json(),
            "channel_status": chan.json(),
        }


def probe(host: str, username: str = "", password: str = "",
          timeout: float = 5.0) -> dict:
    """Sign in to a Reolink camera/NVR and return per-channel proposals.

    Returns ``{ok, proposals, device, error}``. Tries HTTPS with certificate
    verification first; on an SSL error (a self-signed camera cert) it falls
    back to plain HTTP rather than disabling verification. Never raises: a
    timeout or a rejected login comes back as ``ok=False``.
    """
    h = _clean_host(host)
    if not h:
        return {"ok": False, "error": "Enter the camera's address."}

    attempts = [("https", f"https://{h}", True), ("http", f"http://{h}", False)]
    last_error = "Could not reach the camera."
    for scheme, base, verify in attempts:
        try:
            result = _login_and_read(base, username, password, timeout, verify)
        except (ssl.SSLError, httpx.ConnectError) as exc:
            # An https cert problem or a refused connection: fall through to the
            # next scheme (usually plain HTTP) rather than trusting a bad cert.
            last_error = f"Could not reach the camera: {exc}"
            continue
        except httpx.HTTPError as exc:
            last_error = f"Could not reach the camera: {exc}"
            continue
        if not result.get("ok"):
            return result  # a real auth rejection: do not retry another scheme
        dev_info = parse_dev_info(result.get("dev_info"))
        channels = parse_channels(dev_info, result.get("channel_status"))
        proposals = build_proposals(h, channels, dev_info, username=username,
                                    scheme=scheme)
        return {"ok": True, "proposals": proposals, "device": dev_info,
                "error": ""}
    return {"ok": False, "error": last_error}
