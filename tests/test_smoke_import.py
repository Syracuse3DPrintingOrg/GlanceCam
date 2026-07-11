"""Import smoke test: the app builds and exposes /health.

conftest.py already put the service dir on sys.path.
"""


def test_app_imports():
    from app.main import app
    assert app is not None


def test_health_route_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/health" in paths


def test_health_responds_ok():
    # Only run the live check if a test client is available (httpx installed).
    try:
        from fastapi.testclient import TestClient
    except Exception:
        import pytest
        pytest.skip("TestClient/httpx not available")
    from app.main import app
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["app"] == "glancecam"
        assert body["status"] == "ok"
