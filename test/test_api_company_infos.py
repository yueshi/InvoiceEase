"""常用税号及公司信息 CRUD 测试。"""
import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import Role, User
from invoicing.security import hash_password


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def _admin(client, db):
    db.add(User(username="root", password_hash=hash_password("pass123"), role=Role.admin.value))
    db.flush()
    token = client.post("/api/v1/auth/login", json={"username": "root", "password": "pass123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_crud_company_info(client, db):
    h = _admin(client, db)
    resp = client.post(
        "/api/v1/company-infos",
        json={"name": "澜铮鸿欣（上海）数字科技有限公司", "tax_id": "91310101MAELA36R35", "kind": "self", "is_default": True},
        headers=h,
    )
    assert resp.status_code == 200
    info_id = resp.json()["id"]
    assert resp.json()["kind"] == "self"
    assert resp.json()["is_default"] is True

    resp = client.get("/api/v1/company-infos", headers=h)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = client.put(
        f"/api/v1/company-infos/{info_id}",
        json={"remark": "本司主体"},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["remark"] == "本司主体"

    resp = client.delete(f"/api/v1/company-infos/{info_id}", headers=h)
    assert resp.status_code == 200
    assert client.get("/api/v1/company-infos", headers=h).json() == []


def test_invalid_tax_id_422(client, db):
    h = _admin(client, db)
    resp = client.post(
        "/api/v1/company-infos",
        json={"name": "X 公司", "tax_id": "123"},
        headers=h,
    )
    assert resp.status_code == 422


def test_default_clears_previous(client, db):
    h = _admin(client, db)
    client.post("/api/v1/company-infos", json={"name": "A 公司", "tax_id": "91310101MAELA36R35", "kind": "self", "is_default": True}, headers=h)
    resp = client.post("/api/v1/company-infos", json={"name": "B 公司", "tax_id": "91610132MA6UY02A5U", "kind": "self", "is_default": True}, headers=h)
    assert resp.status_code == 200
    infos = client.get("/api/v1/company-infos", headers=h).json()
    defaults = [i for i in infos if i["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["name"] == "B 公司"


def test_admin_only(client, db):
    db.add(User(username="caiwu", password_hash=hash_password("pass123"), role=Role.finance_staff.value))
    db.flush()
    token = client.post("/api/v1/auth/login", json={"username": "caiwu", "password": "pass123"}).json()["access_token"]
    resp = client.get("/api/v1/company-infos", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_put_kind_change_clears_default(client, db):
    h = _admin(client, db)
    resp = client.post("/api/v1/company-infos", json={"name": "A 公司", "tax_id": "91310101MAELA36R35", "kind": "self", "is_default": True}, headers=h)
    assert resp.status_code == 200
    info_id = resp.json()["id"]
    resp = client.put(
        f"/api/v1/company-infos/{info_id}",
        json={"kind": "supplier"},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["is_default"] is False
    infos = client.get("/api/v1/company-infos", headers=h).json()
    assert all(not i["is_default"] for i in infos)


def test_put_tax_id_conflict_409(client, db):
    h = _admin(client, db)
    a = client.post("/api/v1/company-infos", json={"name": "A 公司", "tax_id": "91310101MAELA36R35", "kind": "self"}, headers=h)
    assert a.status_code == 200
    client.post("/api/v1/company-infos", json={"name": "B 公司", "tax_id": "91610132MA6UY02A5U", "kind": "self"}, headers=h)
    resp = client.put(
        f"/api/v1/company-infos/{a.json()['id']}",
        json={"tax_id": "91610132MA6UY02A5U"},
        headers=h,
    )
    assert resp.status_code == 409
