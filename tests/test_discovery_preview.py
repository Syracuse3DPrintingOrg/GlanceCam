"""Discovery preview: URL classification and the image-magic check.

Pure parts only (no network, no go2rtc): the endpoint's fetching is exercised
end to end by the manual curl checks in the task, but the branch that decides
image vs rtsp vs refuse, and the magic-byte gate that stops an HTML error page
being served as a picture, are unit tested here.
"""
from app.routers.discovery import classify_preview_url
from app.services.discovery import lanscan


def test_classify_http_and_https_are_image():
    assert classify_preview_url("http://192.168.1.9/snap.jpg") == "image"
    assert classify_preview_url("https://192.168.1.9:8443/image.jpg") == "image"


def test_classify_rtsp():
    assert classify_preview_url("rtsp://192.168.1.9:554/stream1") == "rtsp"


def test_classify_case_insensitive_scheme():
    assert classify_preview_url("RTSP://192.168.1.9/s") == "rtsp"
    assert classify_preview_url("HTTP://192.168.1.9/s") == "image"


def test_classify_rejects_file_and_other_schemes():
    assert classify_preview_url("file:///etc/passwd") == "unknown"
    assert classify_preview_url("ftp://192.168.1.9/x") == "unknown"
    assert classify_preview_url("data:image/png;base64,AAAA") == "unknown"


def test_classify_rejects_bare_host_and_empty():
    assert classify_preview_url("192.168.1.9/snap.jpg") == "unknown"
    assert classify_preview_url("") == "unknown"
    assert classify_preview_url("   ") == "unknown"


def _jpeg_magic():
    return b"\xff\xd8\xff\xe0" + b"\x00" * 8


def _png_magic():
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def test_image_magic_accepts_jpeg_and_png():
    assert lanscan.looks_like_image_bytes(_jpeg_magic())
    assert lanscan.looks_like_image_bytes(_png_magic())


def test_image_magic_rejects_html_and_empty():
    assert not lanscan.looks_like_image_bytes(b"<html>not an image")
    assert not lanscan.looks_like_image_bytes(b"")
    assert not lanscan.looks_like_image_bytes(b"\x00\x00")
