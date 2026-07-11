"""Common RTSP stream and snapshot paths for the major camera brands.

Most cameras do not serve a stream at the bare ``rtsp://host:554/`` root: they
expect a brand-specific path (Reolink's ``/h264Preview_01_main``, Hikvision's
``/Streaming/Channels/101``, and so on). This table lets the app offer every
brand's default addresses up front, so the user never has to guess a path, and
lets the auto-finder try them one by one until a stream answers.

Everything here is pure data plus two small functions, so it is unit tested with
no network. ``candidate_urls`` builds the ordered try-list (hinted brand first);
``likely_brand`` infers a brand from the open-port signature a scan reports.
"""
from __future__ import annotations

from typing import Optional

# One entry per brand. ``main``/``sub`` are RTSP path templates and ``snapshot``
# is an optional http still-image template. Each is formatted with ``host`` and
# the channel numbers: ``ch`` is 1-based, ``ch0`` is 0-based, so a brand that
# numbers channels either way is covered without special cases at the call site.
# Ordered roughly by how common the brand is on a home LAN.
_BRANDS = (
    {
        "key": "reolink",
        "label": "Reolink",
        "main": "rtsp://{host}:554/h264Preview_{ch:02d}_main",
        "sub": "rtsp://{host}:554/h264Preview_{ch:02d}_sub",
        "snapshot": "http://{host}/cgi-bin/api.cgi?cmd=Snap&channel={ch0}",
    },
    {
        "key": "hikvision",
        "label": "Hikvision",
        # Hikvision encodes channel and stream in one number: 101 is channel 1
        # main, 102 is channel 1 sub.
        "main": "rtsp://{host}:554/Streaming/Channels/{ch}01",
        "sub": "rtsp://{host}:554/Streaming/Channels/{ch}02",
        "snapshot": "http://{host}/ISAPI/Streaming/channels/{ch}01/picture",
    },
    {
        "key": "dahua",
        "label": "Dahua / Amcrest",
        "main": "rtsp://{host}:554/cam/realmonitor?channel={ch}&subtype=0",
        "sub": "rtsp://{host}:554/cam/realmonitor?channel={ch}&subtype=1",
        "snapshot": "http://{host}/cgi-bin/snapshot.cgi?channel={ch}",
    },
    {
        "key": "tplink",
        "label": "TP-Link Tapo / Vigi",
        "main": "rtsp://{host}:554/stream1",
        "sub": "rtsp://{host}:554/stream2",
        "snapshot": None,
    },
    {
        "key": "ubiquiti",
        "label": "Ubiquiti UniFi",
        "main": "rtsp://{host}:554/s0",
        "sub": "rtsp://{host}:554/s1",
        "snapshot": None,
    },
    {
        "key": "axis",
        "label": "Axis",
        "main": "rtsp://{host}:554/axis-media/media.amp",
        "sub": "rtsp://{host}:554/axis-media/media.amp?resolution=640x360",
        "snapshot": "http://{host}/axis-cgi/jpg/image.cgi",
    },
    {
        "key": "wyze",
        "label": "Wyze bridge",
        # docker-wyze-bridge serves each camera on 8554 under its own name; the
        # user renames as needed, "cam" is the common default.
        "main": "rtsp://{host}:8554/cam",
        "sub": None,
        "snapshot": None,
    },
)

# Generic ONVIF/RTSP paths seen across no-name cameras. Each is a bare main path;
# a paired sub path is given where the brandless firmware exposes one.
_GENERIC = (
    ("Generic /stream1", "/stream1", "/stream2"),
    ("Generic /live", "/live", None),
    ("Generic /h264", "/h264", None),
    ("Generic /videoMain", "/videoMain", "/videoSub"),
    ("Generic /ch0_0.h264", "/ch0_0.h264", "/ch0_1.h264"),
    ("Generic /11", "/11", "/12"),
)


def _fmt(tpl: Optional[str], host: str, ch: int) -> Optional[str]:
    """Format one path template for ``host`` and channel ``ch`` (1-based)."""
    if not tpl:
        return None
    return tpl.format(host=host, ch=ch, ch0=ch - 1)


def _brand_candidate(brand: dict, host: str, ch: int) -> dict:
    out = {
        "brand": brand["key"],
        "label": brand["label"],
        "main_url": _fmt(brand["main"], host, ch),
        "sub_url": _fmt(brand.get("sub"), host, ch),
    }
    snap = _fmt(brand.get("snapshot"), host, ch)
    if snap:
        out["snapshot_url"] = snap
    return out


def candidate_urls(host: str, hint: Optional[str] = None,
                   channel: int = 1) -> list[dict]:
    """Ordered candidate stream addresses for ``host``, hinted brand first.

    Returns a list of ``{brand, label, main_url, sub_url, snapshot_url?}``. The
    brand matching ``hint`` (a brand key or label, case-insensitive) is placed
    first so the auto-finder tries the most likely path before the rest; the
    generic ONVIF/RTSP paths come last. ``channel`` picks a channel on a
    multi-channel NVR (1-based). Pure, so it is unit tested.
    """
    host = (host or "").strip()
    if not host:
        return []
    h = (hint or "").strip().lower()

    ordered = list(_BRANDS)
    if h:
        matched = [b for b in _BRANDS
                   if b["key"] == h or b["label"].lower() == h or h in b["key"]]
        if matched:
            first = matched[0]
            ordered = [first] + [b for b in _BRANDS if b is not first]

    out = [_brand_candidate(b, host, channel) for b in ordered]
    for label, main_path, sub_path in _GENERIC:
        out.append({
            "brand": "generic",
            "label": label,
            "main_url": f"rtsp://{host}:554{main_path}",
            "sub_url": f"rtsp://{host}:554{sub_path}" if sub_path else None,
        })
    return out


def likely_brand(ports) -> Optional[str]:
    """Infer a brand key from a scanned host's open-port signature, or None.

    Dahua/Amcrest answer on 37777. Reolink typically opens 554, 443 and 8000
    together; a Hikvision-style device opens 554 and 8000 without 443. Anything
    else is left unguessed so the UI does not assert a brand it cannot support.
    """
    try:
        s = {int(p) for p in (ports or [])}
    except (TypeError, ValueError):
        return None
    if 37777 in s:
        return "dahua"
    if {554, 443, 8000} <= s:
        return "reolink"
    if {554, 8000} <= s:
        return "hikvision"
    return None
