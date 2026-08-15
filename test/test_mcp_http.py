"""MCP HTTP 冒烟：鉴权 401/通过、挂载可达、其他路径不受影响。"""
import pytest
from fastapi.testclient import TestClient

from invoicing.config import settings
from invoicing.db import get_db
from invoicing.main import create_app


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def test_mcp_no_token_401(client):
    resp = client.post("/mcp", json={})
    assert resp.status_code == 401


def test_mcp_wrong_token_401(client):
    resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


def test_mcp_valid_token_passes_auth(client):
    resp = client.post(
        "/mcp", json={}, headers={"Authorization": f"Bearer {settings.mcp_token}"}
    )
    assert resp.status_code != 401  # 进入 MCP 协议层后的响应（如 400/406），鉴权已放行


def test_other_paths_unaffected(client):
    assert client.get("/health").status_code == 200
    # /api/v1 未登录仍是 REST 的 401（非 MCP 中间件）
    assert client.get("/api/v1/invoices").status_code == 401
