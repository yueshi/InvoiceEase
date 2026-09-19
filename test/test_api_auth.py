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


@pytest.fixture(autouse=True)
def _reset_login_failures():
    """登录限流是模块级状态：用例前后清场，避免跨文件泄漏
    （隔离性用例故意把 zhangsan 锁满，曾致后续文件登录 429 → KeyError access_token）。"""
    from invoicing.api.auth import _login_failures

    _login_failures.clear()
    yield
    _login_failures.clear()


def _seed_user(db, username="zhangsan", role=Role.finance_staff.value, password="pass123"):
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def test_login_success(client, db):
    _seed_user(db)
    resp = client.post("/api/v1/auth/login", json={"username": "zhangsan", "password": "pass123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["username"] == "zhangsan"


def test_login_wrong_password(client, db):
    _seed_user(db)
    resp = client.post("/api/v1/auth/login", json={"username": "zhangsan", "password": "wrong"})
    assert resp.status_code == 401


def test_me(client, db):
    user = _seed_user(db)
    resp = client.post("/api/v1/auth/login", json={"username": "zhangsan", "password": "pass123"})
    token = resp.json()["access_token"]
    resp2 = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 200
    assert resp2.json()["id"] == user.id
    assert "password_hash" not in resp2.json()


# ---- M1：登录限流（进程内滑动窗口，(ip, username) 键） --------------------


def _fail_login(client, username, password="wrong"):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def test_login_rate_limit_locks_after_five_failures(client, db):
    import time

    from invoicing.api.auth import _login_failures

    _seed_user(db)
    for _ in range(5):
        assert _fail_login(client, "zhangsan").status_code == 401
    # 第 6 次（含正确密码）都 429
    resp = _fail_login(client, "zhangsan")
    assert resp.status_code == 429
    assert "分钟" in resp.json()["detail"]
    resp = client.post("/api/v1/auth/login", json={"username": "zhangsan", "password": "pass123"})
    assert resp.status_code == 429
    # 429 也写审计且带 rate_limited 标记（撞库检测信号不丢）
    from invoicing.models import AuditLog

    row = db.query(AuditLog).filter(AuditLog.detail["reason"].as_string() == "rate_limited").first()
    assert row is not None
    # 窗口过期恢复：把记录的失败时间戳拨回窗口外
    key = next(iter(_login_failures))
    _login_failures[key] = [time.time() - 16 * 60]
    assert client.post(
        "/api/v1/auth/login", json={"username": "zhangsan", "password": "pass123"}
    ).status_code == 200


def test_login_rate_limit_success_clears_counter(client, db):
    from invoicing.api.auth import _login_failures

    _seed_user(db)
    for _ in range(4):
        assert _fail_login(client, "zhangsan").status_code == 401
    assert client.post(
        "/api/v1/auth/login", json={"username": "zhangsan", "password": "pass123"}
    ).status_code == 200
    assert _login_failures == {}  # 成功清零：老用户改错密码后重新记满才锁


def test_login_rate_limit_isolated_per_username(client, db):
    _seed_user(db)
    _seed_user(db, username="lisi")
    for _ in range(5):
        assert _fail_login(client, "zhangsan").status_code == 401
    assert _fail_login(client, "zhangsan").status_code == 429
    assert _fail_login(client, "lisi", "pass123").status_code == 200  # 别人不受影响
    assert _fail_login(client, "nobody").status_code == 401  # 未知用户名同样只按自身计数
