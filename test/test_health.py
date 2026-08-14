from fastapi.testclient import TestClient

from invoicing.main import app


def test_health():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "invoicing", "version": "0.1.0"}
