"""ONVIF: pure SOAP builders and XML parsers against fixture payloads."""
import base64
import hashlib

from app.services.discovery import onvif


_PROBE_MATCH = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
 <s:Body>
  <d:ProbeMatches>
   <d:ProbeMatch>
    <a:EndpointReference><a:Address>urn:uuid:abcd-1234</a:Address></a:EndpointReference>
    <d:Types>dn:NetworkVideoTransmitter</d:Types>
    <d:Scopes>onvif://www.onvif.org/name/Front%20Door onvif://www.onvif.org/hardware/RLC-810A</d:Scopes>
    <d:XAddrs>http://192.168.1.50/onvif/device_service http://[fe80::1]/onvif/device_service</d:XAddrs>
   </d:ProbeMatch>
  </d:ProbeMatches>
 </s:Body>
</s:Envelope>"""

_PROFILES = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
 xmlns:tt="http://www.onvif.org/ver10/schema">
 <s:Body>
  <trt:GetProfilesResponse>
   <trt:Profiles token="Profile_1">
    <tt:Name>MainStream</tt:Name>
    <tt:VideoEncoderConfiguration>
     <tt:Encoding>H264</tt:Encoding>
     <tt:Resolution><tt:Width>2560</tt:Width><tt:Height>1440</tt:Height></tt:Resolution>
    </tt:VideoEncoderConfiguration>
   </trt:Profiles>
   <trt:Profiles token="Profile_2">
    <tt:Name>SubStream</tt:Name>
    <tt:VideoEncoderConfiguration>
     <tt:Encoding>H264</tt:Encoding>
     <tt:Resolution><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:Resolution>
    </tt:VideoEncoderConfiguration>
   </trt:Profiles>
  </trt:GetProfilesResponse>
 </s:Body>
</s:Envelope>"""

_STREAM_URI = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
 xmlns:tt="http://www.onvif.org/ver10/schema">
 <s:Body>
  <trt:GetStreamUriResponse>
   <trt:MediaUri>
    <tt:Uri>rtsp://192.168.1.50:554/Preview_01_main</tt:Uri>
   </trt:MediaUri>
  </trt:GetStreamUriResponse>
 </s:Body>
</s:Envelope>"""


def test_build_probe_targets_network_video_transmitter():
    xml = onvif.build_probe("uuid:fixed")
    assert "NetworkVideoTransmitter" in xml
    assert "uuid:fixed" in xml
    assert "Probe" in xml


def test_password_digest_matches_formula():
    nonce = b"0123456789abcdef"
    created = "2026-07-11T00:00:00Z"
    expected = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + b"secret").digest()).decode()
    assert onvif.password_digest(nonce, created, "secret") == expected


def test_security_header_present_only_with_username():
    assert onvif._security_header("", "x") == ""
    hdr = onvif._security_header("admin", "pw", nonce=b"n" * 16,
                                 created="2026-07-11T00:00:00Z")
    assert "UsernameToken" in hdr
    assert "admin" in hdr
    assert "PasswordDigest" in hdr


def test_parse_probe_matches():
    devs = onvif.parse_probe_matches(_PROBE_MATCH)
    assert len(devs) == 1
    d = devs[0]
    assert d["urn"] == "urn:uuid:abcd-1234"
    assert d["name"] == "Front Door"  # %20 decoded
    assert d["hardware"] == "RLC-810A"
    assert "http://192.168.1.50/onvif/device_service" in d["xaddrs"]
    assert len(d["xaddrs"]) == 2


def test_parse_probe_matches_bad_xml():
    assert onvif.parse_probe_matches("<not-closed") == []


def test_parse_profiles_reads_resolution():
    profs = onvif.parse_profiles(_PROFILES)
    assert len(profs) == 2
    assert profs[0]["token"] == "Profile_1"
    assert (profs[0]["width"], profs[0]["height"]) == (2560, 1440)
    assert profs[0]["encoding"] == "H264"


def test_classify_profiles_main_is_highest_res():
    profs = onvif.parse_profiles(_PROFILES)
    picks = onvif.classify_profiles(profs)
    assert picks["main"] == "Profile_1"
    assert picks["sub"] == "Profile_2"


def test_classify_single_profile_has_no_sub():
    picks = onvif.classify_profiles([{"token": "only", "width": 1920,
                                      "height": 1080}])
    assert picks["main"] == "only"
    assert picks["sub"] is None


def test_classify_empty():
    assert onvif.classify_profiles([]) == {"main": None, "sub": None}


def test_parse_stream_uri():
    assert onvif.parse_stream_uri(_STREAM_URI) == \
        "rtsp://192.168.1.50:554/Preview_01_main"


def test_parse_stream_uri_bad_xml():
    assert onvif.parse_stream_uri("<x") == ""


def test_get_streams_no_xaddr():
    out = onvif.get_streams("")
    assert out["ok"] is False
