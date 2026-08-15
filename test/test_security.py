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
