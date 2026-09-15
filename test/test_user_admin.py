"""用户管理：自助改密 / 管理员重置 / 暂停恢复 / 角色调整 + 防自锁 + 三处状态闸门。

闸门是本次的要点：暂停若只在登录处生效，已签发的 JWT（最长 8 小时）与
MCP 令牌（Agent 通道）都还能用 —— 那样的"暂停"是假的。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.models import AuditLog, McpToken, Role, User, UserStatus
from invoicing.security import hash_password
from invoicing.workflow import users as svc


@pytest.fixture()
def client(db):
    from invoicing.main import create_app

    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def users(db):
    """本文件要走真实登录，口令哈希必须是真的（conftest 已把 bcrypt cost 降到 4）。"""
    emp = User(username="emp", password_hash=hash_password("pass123"), role=Role.employee.value)
    admin = User(username="adm", password_hash=hash_password("pass123"), role=Role.admin.value)
    admin2 = User(username="adm2", password_hash=hash_password("pass123"), role=Role.admin.value)
    db.add_all([emp, admin, admin2])
    db.commit()
    return {"emp": emp, "admin": admin, "admin2": admin2}


def _login(client, username, password="pass123"):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def _h(resp):
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _audits(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


# ---- 自助改密 --------------------------------------------------------------


def test_self_change_password(client, db, users):
    h = _h(_login(client, "emp"))  # 先登录，拿一个已签发的 JWT
    resp = client.post("/api/v1/auth/password", headers=h,
                       json={"old_password": "pass123", "new_password": "newpass456"})
    assert resp.status_code == 200

    assert _login(client, "emp", "newpass456").status_code == 200
    assert _login(client, "emp", "pass123").status_code == 401
    log = _audits(db, "PASSWORD_CHANGE")[0]
    assert log.user_id == users["emp"].id and log.outcome == "success"


def test_self_change_requires_correct_old_password(client, db, users):
    """会话被劫持时不能直接改密——必须知道原密码。"""
    h = _h(_login(client, "emp"))
    resp = client.post("/api/v1/auth/password", headers=h,
                       json={"old_password": "wrong", "new_password": "newpass456"})
    assert resp.status_code == 422 and "原密码" in resp.json()["detail"]
    assert _login(client, "emp", "pass123").status_code == 200  # 未变
    assert _audits(db, "PASSWORD_CHANGE")[0].outcome == "blocked"  # 留痕


def test_self_change_rejects_short_and_same_password(client, db, users):
    h = _h(_login(client, "emp"))
    for body in ({"old_password": "pass123", "new_password": "short"},
                 {"old_password": "pass123", "new_password": "pass123"}):
        resp = client.post("/api/v1/auth/password", headers=h, json=body)
        assert resp.status_code == 422


# ---- 管理员重置 ------------------------------------------------------------


def test_admin_reset_generated_password_shown_once(client, db, users):
    h = _h(_login(client, "adm"))
    resp = client.post(f"/api/v1/users/{users['emp'].id}/password", headers=h,
                       json={"generate": True})
    assert resp.status_code == 200
    plaintext = resp.json()["plaintext"]
    assert plaintext and len(plaintext) >= 8
    # 明文只在响应里出现一次，且真能登录
    assert _login(client, "emp", plaintext).status_code == 200

    db.refresh(users["emp"])
    assert users["emp"].must_change_password is True  # 强制下次改密
    log = _audits(db, "PASSWORD_RESET")[0]
    assert log.user_id == users["admin"].id  # 记的是**操作者**
    assert log.detail["target_username"] == "emp"
    assert log.detail["mode"] == "generated"


def test_admin_reset_specified_password_not_echoed(client, db, users):
    h = _h(_login(client, "adm"))
    resp = client.post(f"/api/v1/users/{users['emp'].id}/password", headers=h,
                       json={"new_password": "chosen-by-admin"})
    assert resp.status_code == 200
    assert resp.json()["plaintext"] is None  # 管理员自己指定的，无需回显
    assert _login(client, "emp", "chosen-by-admin").status_code == 200
    assert _audits(db, "PASSWORD_RESET")[0].detail["mode"] == "specified"


def test_reset_defaults_to_generate(client, db, users):
    """两者都不给 → 自动生成（最常见的"帮我重置一下"）。"""
    h = _h(_login(client, "adm"))
    resp = client.post(f"/api/v1/users/{users['emp'].id}/password", headers=h, json={})
    assert resp.status_code == 200 and resp.json()["plaintext"]


def test_employee_cannot_reset_others(client, db, users):
    h = _h(_login(client, "emp"))
    resp = client.post(f"/api/v1/users/{users['admin'].id}/password", headers=h, json={})
    assert resp.status_code == 403


# ---- 强制改密闸门 ----------------------------------------------------------


def test_must_change_password_blocks_everything_else(client, db, users):
    """被重置后，除改密/看自己/登出外一律拦住——**默认全拦**，避免新增接口漏网。"""
    h = _h(_login(client, "adm"))
    client.post(f"/api/v1/users/{users['emp'].id}/password", headers=h, json={})

    db.refresh(users["emp"])
    users["emp"].password_hash = hash_password("known-pass")  # 便于本用例登录
    db.commit()
    emp_h = _h(_login(client, "emp", "known-pass"))

    blocked = client.get("/api/v1/invoices", headers=emp_h)
    assert blocked.status_code == 403 and "先修改密码" in blocked.json()["detail"]

    # 白名单可用，否则用户无法自救
    assert client.get("/api/v1/auth/me", headers=emp_h).status_code == 200
    assert client.post("/api/v1/auth/password", headers=emp_h,
                       json={"old_password": "known-pass",
                             "new_password": "brand-new-pass"}).status_code == 200
    # 改完即放行
    assert client.get("/api/v1/invoices", headers=_h(_login(client, "emp", "brand-new-pass"))
                      ).status_code == 200
    db.refresh(users["emp"])
    assert users["emp"].must_change_password is False


# ---- 暂停 / 恢复：三处闸门 --------------------------------------------------


def test_suspend_blocks_login(client, db, users):
    h = _h(_login(client, "adm"))
    assert client.post(f"/api/v1/users/{users['emp'].id}/suspend", headers=h).status_code == 200

    resp = _login(client, "emp")
    assert resp.status_code == 403 and "已暂停" in resp.json()["detail"]
    # 审计里与"密码错"区分开（否则看不出有人在撞已停用的账号）
    failed = _audits(db, "LOGIN_FAILED")
    assert failed[-1].detail.get("reason") == "suspended"


def test_suspend_invalidates_issued_jwt_immediately(client, db, users):
    """**闸门 2**：暂停前签发的 JWT 立即失效，不用等 8 小时过期。"""
    emp_h = _h(_login(client, "emp"))
    assert client.get("/api/v1/invoices", headers=emp_h).status_code == 200

    admin_h = _h(_login(client, "adm"))
    client.post(f"/api/v1/users/{users['emp'].id}/suspend", headers=admin_h)

    resp = client.get("/api/v1/invoices", headers=emp_h)  # 同一个 JWT
    assert resp.status_code == 401 and "已暂停" in resp.json()["detail"]


def test_suspend_invalidates_mcp_tokens_immediately(client, db, users):
    """**闸门 3**：暂停后 Agent 令牌立即失效——不查这一处，"暂停"就是假的。"""
    from invoicing.mcp.verifier import MCPTokenVerifier

    _, plaintext = _issue(db, users["emp"], "员工令牌")

    assert asyncio.run(MCPTokenVerifier().verify_token(plaintext)) is not None
    admin_h = _h(_login(client, "adm"))
    client.post(f"/api/v1/users/{users['emp'].id}/suspend", headers=admin_h)

    assert asyncio.run(MCPTokenVerifier().verify_token(plaintext)) is None

    # 恢复后重新可用（令牌本身没被撤销，只是人被停了）
    client.post(f"/api/v1/users/{users['emp'].id}/resume", headers=admin_h)
    assert asyncio.run(MCPTokenVerifier().verify_token(plaintext)) is not None


def test_suspend_then_resume_restores_login(client, db, users):
    h = _h(_login(client, "adm"))
    client.post(f"/api/v1/users/{users['emp'].id}/suspend", headers=h)
    assert _login(client, "emp").status_code == 403

    assert client.post(f"/api/v1/users/{users['emp'].id}/resume", headers=h).status_code == 200
    assert _login(client, "emp").status_code == 200
    assert {a.action for a in _audits(db, "USER_SUSPEND") + _audits(db, "USER_RESUME")} == {
        "USER_SUSPEND", "USER_RESUME"
    }


# ---- 防自锁 ----------------------------------------------------------------


def test_cannot_suspend_self(client, db, users):
    h = _h(_login(client, "adm"))
    resp = client.post(f"/api/v1/users/{users['admin'].id}/suspend", headers=h)
    assert resp.status_code == 422 and "自己" in resp.json()["detail"]
    assert _audits(db, "USER_SUSPEND")[0].detail["reason"] == "self_suspend"


def test_one_admin_can_suspend_another(client, db, users):
    """两个可用管理员互为保险：停掉另一个可以（系统始终有人能管）。"""
    admin_h = _h(_login(client, "adm2"))
    assert client.post(f"/api/v1/users/{users['admin'].id}/suspend",
                       headers=admin_h).status_code == 200
    db.refresh(users["admin"])
    assert users["admin"].status == UserStatus.SUSPENDED.value


def test_last_admin_guard_is_defensive_only(db, users):
    """「不能停/降**最后一个**可用管理员」是**防御性**规则：经 API 当前不可达。

    原因：操作者自身必须是可用管理员（`get_current_user` 已挡住暂停用户），
    那就意味着至少还有一个可用管理员，目标不可能是"最后一个"。
    真正兜住自锁的是「不能停自己 / 不能改自己角色」这两条（见下两个用例）。

    这里直接在 service 层验证规则本身——**防止将来新增调用路径时它已经悄悄失效**。
    """
    # 构造：emp 升为管理员，其余管理员全部暂停 → emp 成为唯一可用管理员
    for u in db.query(User).filter(User.role == Role.admin.value).all():
        u.status = UserStatus.SUSPENDED.value
    users["emp"].role = Role.admin.value
    db.commit()
    assert svc._other_active_admins(db, users["emp"].id) == 0

    with pytest.raises(ValueError, match="最后一个"):
        svc.set_status(db, users["admin"], users["emp"].id, suspend=True)
    with pytest.raises(ValueError, match="最后一个"):
        svc.set_role(db, users["admin"], users["emp"].id, "employee")

    # 两次尝试都要留痕（系统正确拦截）
    assert {a.detail["reason"] for a in _audits(db, "USER_SUSPEND")
            + _audits(db, "USER_ROLE_CHANGE")} == {"last_admin"}


def test_cannot_change_own_role(client, db, users):
    """自锁的真正防线①：改自己角色（最后一个管理员自降 = 系统失管）。"""
    h = _h(_login(client, "adm"))
    resp = client.put(f"/api/v1/users/{users['admin'].id}", headers=h, json={"role": "employee"})
    assert resp.status_code == 422 and "自己" in resp.json()["detail"]
    assert _audits(db, "USER_ROLE_CHANGE")[0].detail["reason"] == "self_role_change"


def test_cannot_suspend_self_also_when_only_admin(client, db, users):
    """自锁的真正防线②：暂停自己（同上，且这是 API 可达的路径）。"""
    for u in db.query(User).filter(User.role == Role.admin.value,
                                   User.id != users["admin"].id).all():
        u.status = UserStatus.SUSPENDED.value
    db.commit()
    h = _h(_login(client, "adm"))
    resp = client.post(f"/api/v1/users/{users['admin'].id}/suspend", headers=h)
    assert resp.status_code == 422 and "自己" in resp.json()["detail"]


# ---- 角色调整 --------------------------------------------------------------


def test_role_change_audited_with_from_to(client, db, users):
    h = _h(_login(client, "adm"))
    resp = client.put(f"/api/v1/users/{users['emp'].id}", headers=h,
                      json={"role": "finance_staff"})
    assert resp.status_code == 200 and resp.json()["role"] == "finance_staff"

    log = _audits(db, "USER_ROLE_CHANGE")[0]
    assert log.detail["from"] == "employee" and log.detail["to"] == "finance_staff"
    assert log.detail["target_username"] == "emp"
    assert log.detail["active_tokens"] == 0  # 提示管理员是否需要一并撤销令牌

    # 角色即时生效：财务才能看的接口现在能进
    assert client.get("/api/v1/receipts", headers=_h(_login(client, "emp"))).status_code == 200


def test_role_change_rejects_same_and_illegal(client, db, users):
    h = _h(_login(client, "adm"))
    assert client.put(f"/api/v1/users/{users['emp'].id}", headers=h,
                      json={"role": "employee"}).status_code == 422
    assert client.put(f"/api/v1/users/{users['emp'].id}", headers=h,
                      json={"role": "superuser"}).status_code == 422


def test_put_users_rejects_password_field(client, db, users):
    """密码走独立接口（语义不同：重置还置强制改密），混用会含糊。"""
    h = _h(_login(client, "adm"))
    resp = client.put(f"/api/v1/users/{users['emp'].id}", headers=h,
                      json={"password": "whatever123"})
    assert resp.status_code == 422 and "password" in resp.json()["detail"]


# ---- 建号审计 --------------------------------------------------------------


def test_create_user_is_audited(client, db, users):
    h = _h(_login(client, "adm"))
    assert client.post("/api/v1/users", headers=h,
                       json={"username": "newbie", "password": "pass12345",
                             "role": "employee"}).status_code == 200
    log = _audits(db, "USER_CREATE")[0]
    assert log.user_id == users["admin"].id
    assert log.detail["target_username"] == "newbie" and log.outcome == "success"


def test_suspended_user_cannot_be_created_active(client, db, users):
    """新建用户默认 active（回归：新列默认值必须对）。"""
    h = _h(_login(client, "adm"))
    client.post("/api/v1/users", headers=h,
                json={"username": "newbie2", "password": "pass12345", "role": "employee"})
    assert db.query(User).filter(User.username == "newbie2").one().status == UserStatus.ACTIVE.value


def _issue(db, user, name):
    from invoicing.workflow import mcp_tokens as tokens

    return tokens.issue_token(db, user, name=name, scopes=("invoice:read",))
