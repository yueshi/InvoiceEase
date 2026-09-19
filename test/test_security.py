import pytest

from invoicing.security import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.models import User
from invoicing.security import (
    create_access_token,
    decode_token,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)


def test_hash_and_verify_roundtrip():
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    user = User(id=1, username="u1", password_hash="x", role="finance_staff")
    token = create_access_token(user)
    payload = decode_token(token)
    assert payload["sub"] == "1"
    assert payload["role"] == "finance_staff"


def test_decode_invalid_token_raises():
    import jwt

    with pytest.raises(jwt.PyJWTError):
        decode_token("not-a-token")


def _build_app(db_factory, role="finance_staff"):
    app = FastAPI()

    @app.get("/protected")
    def protected(user=Depends(get_current_user)):
        return {"user_id": user.id, "role": user.role}

    @app.get("/admin-only")
    def admin_only(user=Depends(require_role("admin"))):
        return {"ok": True}

    def override_db():
        yield db_factory()

    app.dependency_overrides[get_db] = override_db
    return app


def test_current_user_and_role_guard(db):
    user = User(username="u1", password_hash="x", role="finance_staff")
    db.add(user)
    db.flush()
    token = create_access_token(user)
    client = TestClient(_build_app(lambda: db))
    resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "finance_staff"
    resp2 = client.get("/admin-only", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 403


def test_missing_token_401(db):
    client = TestClient(_build_app(lambda: db))
    assert client.get("/protected").status_code == 401


# ---- M1：CORS / TrustedHost 中间件（空配置=关闭，同源部署零行为变化） --------------------


def _fresh_app():
    from invoicing.main import create_app

    return TestClient(create_app())


def test_cors_middleware_configurable(monkeypatch):
    """cors_origins 空=无 CORS 头；配置后仅列出的来源拿到 allow-origin。"""
    from invoicing.config import settings

    monkeypatch.setattr(settings, "cors_origins", "")
    client = _fresh_app()
    r = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers

    monkeypatch.setattr(settings, "cors_origins", "https://web.example.com")
    client = _fresh_app()
    r = client.get("/health", headers={"Origin": "https://web.example.com"})
    assert r.headers.get("access-control-allow-origin") == "https://web.example.com"
    r2 = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r2.headers


def test_trusted_host_middleware(monkeypatch):
    """allowed_hosts 空=不校验 Host；配置后非白名单 Host 400。"""
    from invoicing.config import settings

    monkeypatch.setattr(settings, "allowed_hosts", "")
    client = _fresh_app()
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 200

    monkeypatch.setattr(settings, "allowed_hosts", "invoice.example.com")
    client = _fresh_app()
    assert client.get("/health", headers={"Host": "invoice.example.com"}).status_code == 200
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
