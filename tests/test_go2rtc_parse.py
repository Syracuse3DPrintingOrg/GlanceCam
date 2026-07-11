from app.services.go2rtc import parse_probe, embed_credentials


def test_parse_probe_string_medias():
    payload = {
        "producers": [
            {"medias": ["video, recvonly, H264, 2560x1440",
                        "audio, recvonly, AAC"]}
        ]
    }
    result = parse_probe(payload)
    assert result == {"codec": "H264", "resolution": [2560, 1440]}


def test_parse_probe_hevc_sub_stream():
    payload = {"producers": [{"medias": ["video, recvonly, H265, 640x360"]}]}
    assert parse_probe(payload) == {"codec": "H265", "resolution": [640, 360]}


def test_parse_probe_no_producers():
    assert parse_probe({"producers": []}) is None
    assert parse_probe({}) is None
    assert parse_probe(None) is None
    assert parse_probe("nope") is None


def test_parse_probe_audio_only_returns_none():
    payload = {"producers": [{"medias": ["audio, recvonly, AAC"]}]}
    assert parse_probe(payload) is None


def test_parse_probe_dict_media():
    payload = {"producers": [{"medias": [{"codec": "video, recvonly, MJPEG, 1920x1080"}]}]}
    assert parse_probe(payload) == {"codec": "MJPEG", "resolution": [1920, 1080]}


def test_parse_probe_malformed_producer_ignored():
    payload = {"producers": ["not-a-dict", {"medias": ["video, H264, 800x600"]}]}
    assert parse_probe(payload) == {"codec": "H264", "resolution": [800, 600]}


def test_embed_credentials_rtsp():
    out = embed_credentials("rtsp://192.168.1.5:554/stream", "admin", "p@ss word")
    # Password special chars are percent-encoded.
    assert out == "rtsp://admin:p%40ss%20word@192.168.1.5:554/stream"


def test_embed_credentials_username_only():
    out = embed_credentials("rtsp://host/s", "admin", "")
    assert out == "rtsp://admin@host/s"


def test_embed_credentials_no_username_unchanged():
    url = "rtsp://host/s"
    assert embed_credentials(url, "", "x") == url


def test_embed_credentials_existing_creds_unchanged():
    url = "rtsp://existing:cred@host/s"
    assert embed_credentials(url, "admin", "pw") == url
