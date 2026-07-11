"""Pure-logic tests for the stream budget math (app.services.resources)."""
from app.services import resources


def _cam(id_, name, sub=None, main=None, sub_url=None):
    return {
        "id": id_,
        "name": name,
        "sub_resolution": sub,
        "main_resolution": main,
        "sub_url": sub_url,
    }


def _hw(cls, cpu_count=4, ram_mb=2048, model=None):
    return {"class": cls, "cpu_count": cpu_count, "ram_mb": ram_mb, "model": model}


# --- hardware classification -------------------------------------------------

def test_classify_pi5():
    assert resources._classify("Raspberry Pi 5 Model B Rev 1.0", 8192) == "pi5"


def test_classify_pi4():
    assert resources._classify("Raspberry Pi 4 Model B Rev 1.4", 4096) == "pi4"


def test_classify_pi3():
    assert resources._classify("Raspberry Pi 3 Model B Plus Rev 1.3", 1024) == "pi3"


def test_classify_pi_zero():
    assert resources._classify("Raspberry Pi Zero 2 W Rev 1.0", 512) == "pi_zero"


def test_classify_compute_module():
    assert resources._classify("Raspberry Pi Compute Module 4 Rev 1.0", 4096) == "pi4"


def test_classify_x86_generic():
    assert resources._classify(None, 8192) == "x86"


def test_classify_unknown_low_ram():
    assert resources._classify(None, 0) == "unknown"


def test_detect_hardware_shape():
    hw = resources.detect_hardware()
    assert set(hw) == {"model", "cpu_count", "ram_mb", "class"}
    assert hw["cpu_count"] >= 1


# --- budget math: hard gating on a small Pi ----------------------------------

def test_pi3_with_hd_no_sub_cameras_gates_hard():
    cams = [_cam(f"c{i}", f"Cam {i}", main=(1920, 1080)) for i in range(4)]
    result = resources.budget(cams, hardware=_hw("pi3"))
    # 1920*1080*15 / 1e6 ~= 31.1 MP/s each; pi3 budget is 30 MP/s, so only the
    # always-on first camera fits.
    assert result["live_tile_limit"] == 1
    assert result["per_camera"]["c0"]["live"] is True
    assert result["per_camera"]["c1"]["live"] is False
    assert result["surface"] == "kiosk"


def test_at_least_one_always_live_even_when_oversized():
    cams = [_cam("only", "Solo", main=(3840, 2160))]
    result = resources.budget(cams, hardware=_hw("pi3"))
    assert result["live_tile_limit"] == 1
    assert result["per_camera"]["only"]["live"] is True


# --- pi5 fits more than pi3 ----------------------------------------------------

def test_pi5_fits_more_than_pi3():
    cams = [_cam(f"c{i}", f"Cam {i}", sub=(640, 360)) for i in range(30)]
    pi3_result = resources.budget(cams, hardware=_hw("pi3"))
    pi5_result = resources.budget(cams, hardware=_hw("pi5"))
    assert pi5_result["live_tile_limit"] > pi3_result["live_tile_limit"]


# --- unknown resolutions use defaults -----------------------------------------

def test_unknown_resolution_uses_sub_default():
    cams = [_cam("c0", "Cam 0")]
    result = resources.budget(cams, hardware=_hw("x86"))
    # No sub_resolution, no main_resolution, no sub_url -> defaults to main HD.
    cost = result["per_camera"]["c0"]["cost"]
    expected = (1920 * 1080 * 15) / 1_000_000
    assert abs(cost - expected) < 0.01
    assert result["per_camera"]["c0"]["uses"] == "main"


def test_unknown_resolution_with_sub_url_uses_sub_default():
    cams = [_cam("c0", "Cam 0", sub_url="rtsp://cam/sub")]
    result = resources.budget(cams, hardware=_hw("x86"))
    cost = result["per_camera"]["c0"]["cost"]
    expected = (640 * 360 * 15) / 1_000_000
    assert abs(cost - expected) < 0.01
    assert result["per_camera"]["c0"]["uses"] == "sub"


# --- order respected -----------------------------------------------------------

def test_order_respected_greedy():
    # Two cheap sub streams then one huge main stream; pi3 budget 30 MP/s.
    cams = [
        _cam("cheap1", "Cheap One", sub=(640, 360)),   # ~9.2 MP/s
        _cam("cheap2", "Cheap Two", sub=(640, 360)),   # ~9.2 MP/s
        _cam("huge", "Huge One", main=(3840, 2160)),   # ~124 MP/s, does not fit
    ]
    result = resources.budget(cams, hardware=_hw("pi3"))
    assert result["per_camera"]["cheap1"]["live"] is True
    assert result["per_camera"]["cheap2"]["live"] is True
    assert result["per_camera"]["huge"]["live"] is False


# --- per-camera costs -----------------------------------------------------------

def test_per_camera_cost_matches_resolution():
    cams = [_cam("c0", "Cam 0", sub=(640, 360))]
    result = resources.budget(cams, hardware=_hw("x86"))
    expected = (640 * 360 * 15) / 1_000_000
    assert abs(result["per_camera"]["c0"]["cost"] - expected) < 0.01


# --- hard tile sanity cap -------------------------------------------------------

def test_hard_tile_limit_caps_at_16():
    cams = [_cam(f"c{i}", f"Cam {i}", sub=(160, 90)) for i in range(30)]
    result = resources.budget(cams, hardware=_hw("x86"))
    assert result["live_tile_limit"] <= 16


# --- client_hint scaling (remote surface) ---------------------------------------

def test_client_hint_scales_with_cores():
    cams = [_cam(f"c{i}", f"Cam {i}", sub=(640, 360)) for i in range(20)]
    low_cores = resources.budget(cams, client_hint={"cores": 1, "width": 1920, "height": 1080})
    high_cores = resources.budget(cams, client_hint={"cores": 8, "width": 1920, "height": 1080})
    assert high_cores["live_tile_limit"] >= low_cores["live_tile_limit"]
    assert low_cores["surface"] == "remote"


def test_client_hint_min_floor_for_single_core():
    cams = [_cam("c0", "Cam 0", sub=(640, 360))]
    result = resources.budget(cams, client_hint={"cores": 1, "width": 800, "height": 600})
    assert result["live_tile_limit"] == 1


def test_client_hint_small_tile_recommendation():
    cams = [_cam(f"c{i}", f"Cam {i}", sub=(160, 90)) for i in range(12)]
    result = resources.budget(cams, client_hint={"cores": 8, "width": 1000, "height": 720})
    joined = " ".join(result["recommendations"])
    assert "small" in joined.lower()


# --- recommendation strings mention camera names ---------------------------------

def test_recommendation_mentions_camera_without_sub():
    cams = [_cam("c0", "Front Door", main=(1920, 1080))]
    result = resources.budget(cams, hardware=_hw("x86"))
    joined = " ".join(result["recommendations"])
    assert "Front Door" in joined
    assert "no sub stream" in joined


def test_recommendation_everything_fits():
    cams = [_cam(f"c{i}", f"Cam {i}", sub=(320, 180)) for i in range(3)]
    result = resources.budget(cams, hardware=_hw("x86"))
    joined = " ".join(result["recommendations"])
    assert "fit within" in joined


def test_recommendation_hardware_limited_mentions_class():
    cams = [_cam(f"c{i}", f"Cam {i}", main=(1920, 1080)) for i in range(4)]
    result = resources.budget(cams, hardware=_hw("pi4"))
    joined = " ".join(result["recommendations"])
    assert "smoothly" in joined
    # No jargon like "MP/s" in user-forward copy.
    assert "MP/s" not in joined


def test_no_cameras_empty_result():
    result = resources.budget([], hardware=_hw("x86"))
    assert result["live_tile_limit"] == 0
    assert result["per_camera"] == {}
