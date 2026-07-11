"""Brand stream-path table: candidate ordering and port-signature inference."""
from app.services.discovery import streampaths


def _mains(cands):
    return [c["main_url"] for c in cands]


def test_candidate_urls_lists_every_brand():
    cands = streampaths.candidate_urls("192.168.1.9")
    brands = {c["brand"] for c in cands}
    for expected in ("reolink", "hikvision", "dahua", "tplink", "ubiquiti",
                     "axis", "wyze", "generic"):
        assert expected in brands


def test_candidate_urls_fill_host_and_channel():
    cands = streampaths.candidate_urls("cam.local")
    by_brand = {c["brand"]: c for c in cands}
    assert by_brand["reolink"]["main_url"] == \
        "rtsp://cam.local:554/h264Preview_01_main"
    assert by_brand["reolink"]["sub_url"] == \
        "rtsp://cam.local:554/h264Preview_01_sub"
    assert by_brand["hikvision"]["main_url"] == \
        "rtsp://cam.local:554/Streaming/Channels/101"
    assert by_brand["hikvision"]["sub_url"] == \
        "rtsp://cam.local:554/Streaming/Channels/102"
    assert by_brand["dahua"]["main_url"] == \
        "rtsp://cam.local:554/cam/realmonitor?channel=1&subtype=0"


def test_hint_puts_brand_first():
    cands = streampaths.candidate_urls("10.0.0.5", hint="hikvision")
    assert cands[0]["brand"] == "hikvision"
    # Label matching is case-insensitive too.
    cands2 = streampaths.candidate_urls("10.0.0.5", hint="Dahua / Amcrest")
    assert cands2[0]["brand"] == "dahua"


def test_unknown_hint_keeps_default_order():
    cands = streampaths.candidate_urls("10.0.0.5", hint="nosuchbrand")
    assert cands[0]["brand"] == "reolink"


def test_generic_paths_present_and_last():
    cands = streampaths.candidate_urls("10.0.0.5")
    generic = [c for c in cands if c["brand"] == "generic"]
    assert generic
    # The generic candidates come after every named brand (a contiguous tail).
    first_generic = next(i for i, c in enumerate(cands)
                         if c["brand"] == "generic")
    assert all(c["brand"] == "generic" for c in cands[first_generic:])
    mains = _mains(generic)
    assert "rtsp://10.0.0.5:554/stream1" in mains
    assert "rtsp://10.0.0.5:554/videoMain" in mains


def test_empty_host_returns_nothing():
    assert streampaths.candidate_urls("") == []
    assert streampaths.candidate_urls("   ") == []


def test_snapshot_only_where_defined():
    by_brand = {c["brand"]: c for c in streampaths.candidate_urls("h")}
    assert "snapshot_url" in by_brand["reolink"]
    assert "snapshot_url" not in by_brand["tplink"]  # Tapo has no default still


def test_likely_brand_signatures():
    assert streampaths.likely_brand([554, 443, 8000]) == "reolink"
    assert streampaths.likely_brand([554, 8000]) == "hikvision"
    assert streampaths.likely_brand([37777, 554]) == "dahua"
    assert streampaths.likely_brand([80]) is None
    assert streampaths.likely_brand([]) is None
    assert streampaths.likely_brand(None) is None


def test_likely_brand_reolink_beats_hik_when_443_present():
    # A superset with 443 is Reolink, not the 554+8000 Hikvision case.
    assert streampaths.likely_brand([554, 443, 8000, 80]) == "reolink"
