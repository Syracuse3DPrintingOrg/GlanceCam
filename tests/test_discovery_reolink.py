"""Reolink: pure JSON parsers and proposal building against fixture payloads."""
from app.services.discovery import reolink


_LOGIN_OK = [{"cmd": "Login", "code": 0,
              "value": {"Token": {"name": "tok-abc", "leaseTime": 3600}}}]
_LOGIN_FAIL = [{"cmd": "Login", "code": 1,
                "error": {"detail": "login failed", "rspCode": -6}}]

_DEV_INFO = [{"cmd": "GetDevInfo", "code": 0,
              "value": {"DevInfo": {"name": "Backyard NVR", "model": "RLN8-410",
                                    "channelNum": 2}}}]
_DEV_INFO_SINGLE = [{"cmd": "GetDevInfo", "code": 0,
                     "value": {"DevInfo": {"name": "Front Door", "model": "RLC-810A",
                                           "channelNum": 1}}}]

_CHANNELS = [{"cmd": "GetChannelStatus", "code": 0,
              "value": {"count": 2, "status": [
                  {"channel": 0, "name": "Driveway", "online": 1},
                  {"channel": 1, "name": "Patio", "online": 0}]}}]


def test_parse_login_token_ok():
    assert reolink.parse_login_token(_LOGIN_OK) == "tok-abc"


def test_parse_login_token_rejected():
    assert reolink.parse_login_token(_LOGIN_FAIL) is None
    assert reolink.parse_login_token([]) is None
    assert reolink.parse_login_token({"garbage": 1}) is None


def test_parse_dev_info():
    info = reolink.parse_dev_info(_DEV_INFO)
    assert info["model"] == "RLN8-410"
    assert info["channelNum"] == 2


def test_parse_channels_from_status():
    chans = reolink.parse_channels(reolink.parse_dev_info(_DEV_INFO), _CHANNELS)
    assert len(chans) == 2
    assert chans[0] == {"channel": 0, "name": "Driveway", "online": True}
    assert chans[1]["online"] is False


def test_parse_channels_falls_back_to_channelnum():
    chans = reolink.parse_channels({"channelNum": 3}, None)
    assert [c["channel"] for c in chans] == [0, 1, 2]


def test_parse_channels_always_at_least_one():
    assert reolink.parse_channels({}, None) == [
        {"channel": 0, "name": "", "online": True}]


def test_rtsp_url_channel_is_one_based_in_path():
    assert reolink.rtsp_url("192.168.1.20", 0, "main") == \
        "rtsp://192.168.1.20/h264Preview_01_main"
    assert reolink.rtsp_url("192.168.1.20", 1, "sub") == \
        "rtsp://192.168.1.20/h264Preview_02_sub"


def test_rtsp_url_has_no_credentials():
    url = reolink.rtsp_url("192.168.1.20", 0, "main")
    assert "@" not in url  # creds stay server-side, never in the URL


def test_snapshot_url():
    assert reolink.snapshot_url("192.168.1.20", 0) == \
        "http://192.168.1.20/cgi-bin/api.cgi?cmd=Snap&channel=0"
    assert reolink.snapshot_url("192.168.1.20", 1, "https") == \
        "https://192.168.1.20/cgi-bin/api.cgi?cmd=Snap&channel=1"


def test_build_proposals_multi_channel():
    dev = reolink.parse_dev_info(_DEV_INFO)
    chans = reolink.parse_channels(dev, _CHANNELS)
    props = reolink.build_proposals("192.168.1.20", chans, dev,
                                    username="admin")
    assert len(props) == 2
    assert props[0]["source"] == "reolink"
    assert props[0]["name"] == "Driveway"
    assert props[0]["main_url"] == "rtsp://192.168.1.20/h264Preview_01_main"
    assert props[0]["sub_url"] == "rtsp://192.168.1.20/h264Preview_01_sub"
    assert props[0]["username"] == "admin"
    assert "password" not in props[0]  # never proposed
    assert props[1]["channel"] == 1
    assert "offline" in props[1]["notes"].lower()


def test_build_proposals_single_camera_uses_device_name():
    dev = reolink.parse_dev_info(_DEV_INFO_SINGLE)
    chans = reolink.parse_channels(dev, None)
    props = reolink.build_proposals("192.168.1.30", chans, dev)
    assert len(props) == 1
    assert props[0]["name"] == "Front Door"


def test_clean_host_strips_scheme():
    assert reolink._clean_host("https://192.168.1.20/") == "192.168.1.20"
    assert reolink._clean_host("http://cam.local") == "cam.local"
    assert reolink._clean_host("192.168.1.20") == "192.168.1.20"


def test_probe_empty_host():
    assert reolink.probe("")["ok"] is False
