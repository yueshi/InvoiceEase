from fastapi.testclient import TestClient

from invoicing.main import create_app


def test_health(db):
    with TestClient(create_app()) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "invoicing", "version": "0.1.0"}
