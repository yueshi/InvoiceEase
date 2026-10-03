"""Agent 深链与免登票据（design/2026-10-03-Agent跳转Web后台-深链与免登票据设计.md §9）。"""
from datetime import date, datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from invoicing import web_links
from invoicing.config import settings
from invoicing.db import SessionLocal, get_db
from invoicing.main import create_app
from invoicing.models import AuditLog, Role, User, UserStatus
from invoicing.security import hash_password

TICKET_URL = "/api/v1/auth/ticket-login"


@pytest.fixture()
def client(db):
    """每请求一个新 session —— 与生产一致。

    复用共享 session 会让「二次兑换」走身份映射冲突（FlushError→500）而不是
    生产路径的 INSERT 主键冲突（IntegrityError→401），撤回保护就测不出来了。
    """
    app = create_app()

    def override_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def web_cfg(monkeypatch):
    """启用深链的配置基线；用例内可再改单项。"""
    monkeypatch.setattr(settings, "web_base_url", "https://inv.example.com")
    monkeypatch.setattr(settings, "web_ticket_ttl_minutes", 60)
    yield


def _seed_user(db, username="zhangsan", role=Role.finance_staff.value, status="active"):
    user = User(username=username, password_hash=hash_password("pass123"), role=role,
                status=status)
    db.add(user)
    db.commit()  # 请求侧是独立 session，必须落库可见
    return user


def _ticket_of(url: str) -> str:
    assert url is not None
    return url.split("ticket=")[1]


# ---- URL 构造 --------------------------------------------------------------


def test_link_none_without_base(monkeypatch):
    monkeypatch.setattr(settings, "web_base_url", "")
    assert web_links.web_link("/invoices", user_id=1, status="pending_review") is None


def test_link_builds_url_and_encodes_params(web_cfg):
    url = web_links.web_link(
        "/invoices", user_id=None, status="pending_review", keyword="差旅", page=None
    )
    assert url == "https://inv.example.com/invoices?status=pending_review&keyword=%E5%B7%AE%E6%97%85"


def test_link_rejects_path_outside_whitelist(web_cfg):
    with pytest.raises(ValueError):
        web_links.web_link("/admin/secret", user_id=None)
    with pytest.raises(ValueError):
        web_links.web_link("https://evil.example.com/x", user_id=None)


def test_link_carries_ticket_bound_to_user(web_cfg, db):
    user = _seed_user(db)
    url = web_links.web_link("/invoices", user_id=user.id, status="pending_review")
    claims = web_links.decode_ticket(_ticket_of(url))
    assert claims["sub"] == str(user.id)
    assert claims["purpose"] == "web_link"
    assert claims["jti"]


def test_ticket_disabled_when_ttl_zero(monkeypatch, db):
    monkeypatch.setattr(settings, "web_base_url", "https://inv.example.com")
    monkeypatch.setattr(settings, "web_ticket_ttl_minutes", 0)
    user = _seed_user(db)
    assert web_links.web_link("/invoices", user_id=user.id) == "https://inv.example.com/invoices"


# ---- period 压缩 -----------------------------------------------------------


def test_period_of_compresses_exact_periods():
    assert web_links.period_of(date(2026, 10, 1), date(2026, 10, 31)) == "2026-10"
    assert web_links.period_of(date(2026, 10, 1), date(2026, 12, 31)) == "2026-Q4"
    assert web_links.period_of(date(2026, 1, 1), date(2026, 12, 31)) == "2026"
    assert web_links.period_of(date(2024, 2, 1), date(2024, 2, 29)) == "2024-02"  # 闰年整月


def test_period_of_returns_none_for_partial_ranges():
    assert web_links.period_of(date(2026, 10, 1), date(2026, 10, 20)) is None  # 本周类
    assert web_links.period_of(date(2026, 10, 1), date(2026, 11, 30)) is None  # 跨月非整季
    assert web_links.period_of(None, date(2026, 10, 31)) is None
    assert web_links.period_of(date(2026, 10, 1), None) is None


# ---- 票据兑换端点 ----------------------------------------------------------


def test_ticket_login_success_then_replay_rejected(client, db, web_cfg):
    user = _seed_user(db)
    ticket = _ticket_of(web_links.web_link("/invoices", user_id=user.id))

    resp = client.post(TICKET_URL, json={"ticket": ticket})
    assert resp.status_code == 200
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"}
    )
    assert me.status_code == 200 and me.json()["username"] == "zhangsan"

    # 一次性：二次兑换必失败
    assert client.post(TICKET_URL, json={"ticket": ticket}).status_code == 401
    # 审计：成功兑换 channel=web_link（票据原文不入审计）
    row = (
        db.query(AuditLog)
        .filter(AuditLog.action == "LOGIN", AuditLog.channel == "web_link")
        .first()
    )
    assert row is not None and row.user_id == user.id and "ticket" not in str(row.detail)


def test_ticket_expired_rejected(client, db, web_cfg):
    user = _seed_user(db)
    now = datetime.now(timezone.utc)
    expired = pyjwt.encode(
        {"sub": str(user.id), "purpose": "web_link", "jti": "e" * 32,
         "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1)},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )
    assert client.post(TICKET_URL, json={"ticket": expired}).status_code == 401


def test_ticket_invalid_inputs_rejected(client, db, web_cfg):
    user = _seed_user(db)
    now = datetime.now(timezone.utc)

    def _encode(claims: dict) -> str:
        return pyjwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    wrong_purpose = _encode({"sub": str(user.id), "purpose": "other", "jti": "f" * 32,
                             "exp": now + timedelta(minutes=5)})
    no_exp = _encode({"sub": str(user.id), "purpose": "web_link", "jti": "a" * 32})
    unknown_user = _encode({"sub": "999999", "purpose": "web_link", "jti": "b" * 32,
                            "exp": now + timedelta(minutes=5)})
    for bad in (wrong_purpose, no_exp, unknown_user, "garbage"):
        assert client.post(TICKET_URL, json={"ticket": bad}).status_code == 401


def test_ticket_suspended_user_rejected(client, db, web_cfg):
    user = _seed_user(db, status=UserStatus.SUSPENDED.value)
    ticket = _ticket_of(web_links.web_link("/invoices", user_id=user.id))
    assert client.post(TICKET_URL, json={"ticket": ticket}).status_code == 403


# ---- MCP 出参附挂 ----------------------------------------------------------


def test_mcp_no_web_url_without_base(db, mcp_admin_auth, monkeypatch):
    """未配置 web_base_url → web_url 恒 None（全链路零行为变化基线）。"""
    monkeypatch.setattr(settings, "web_base_url", "")
    from invoicing.mcp import tools as mcp_tools

    assert mcp_tools.list_invoices_mcp().web_url is None


def test_mcp_invoice_list_carries_filtered_web_url(db, mcp_admin_auth, web_cfg):
    from invoicing.mcp import tools as mcp_tools

    result = mcp_tools.list_invoices_mcp(
        status="pending_review", date_from=date(2026, 10, 1), date_to=date(2026, 10, 31)
    )
    assert result.web_url is not None
    assert result.web_url.startswith("https://inv.example.com/invoices?")
    assert "status=pending_review" in result.web_url
    assert "period=2026-10" in result.web_url
    assert "ticket=" in result.web_url


def test_mcp_expense_list_items_carry_web_url(db, mcp_admin_auth, web_cfg):
    from invoicing.mcp import tools as mcp_tools
    from invoicing.workflow import expenses as svc

    claim = svc.create_claim(db, mcp_admin_auth, "测试报销", None, "other")
    db.commit()
    rows = mcp_tools.expense_list()
    assert rows
    assert rows[0]["web_url"].startswith("https://inv.example.com/expenses?")
    assert f"claim_id={claim.id}" in rows[0]["web_url"]
