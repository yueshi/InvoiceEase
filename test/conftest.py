import os

os.environ.setdefault("INVOICING_DATABASE_URL", "sqlite:///./invoicing_test.db")
os.environ.setdefault("INVOICING_STORAGE_BACKEND", "local")
os.environ.setdefault("INVOICING_STORAGE_ROOT", "./data/test-originals")
os.environ.setdefault("INVOICING_QUEUE_BACKEND", "local")
os.environ.setdefault("INVOICING_SCHEDULER_ENABLED", "false")
os.environ.setdefault("INVOICING_OCR_PRELOAD", "false")  # 测试不预热引擎（装了 ocr extra 时避免每次 create_app 触发推理）
os.environ.setdefault("INVOICING_PASSWORD_HASH_ROUNDS", "4")  # bcrypt 降 cost：本机 cost=12 一次 4.5 秒，测试建用户量大会拖垮套件
os.environ.setdefault("INVOICING_LLM_ENABLED", "false")  # 测试环境禁用 LLM（防 .env 真实配置污染）
os.environ.setdefault("INVOICING_JWT_SECRET", "test-secret-0123456789-0123456789-0123456789")
os.environ.setdefault("INVOICING_ADMIN_PASSWORD", "admin123")

import pytest
from sqlalchemy import create_engine

from invoicing.config import settings
from invoicing.db import Base, SessionLocal


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(settings.database_url)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def mcp_admin_auth(db, mcp_auth):
    """以管理员身份调用工具 —— 供「测工具行为、不测身份」的用例使用。

    等价于升级前的单令牌通道（管理员、全 scope）。**身份相关用例必须显式用
    `mcp_auth` 覆盖**，否则身份回归会失去意义。
    """
    from invoicing.models import Role, User

    admin = User(username="__mcp_admin__", password_hash="x", role=Role.admin.value)
    db.add(admin)
    db.commit()
    mcp_auth(admin)
    return admin


@pytest.fixture()
def mcp_auth():
    """注入 MCP 认证上下文 —— 测试里替代生产的 AuthContextMiddleware。

    直接调 `mcp_tools.*` 的测试需要它：工具层 `@requires(...)` 会读上下文，
    没有上下文会抛 NoPrincipalError（**故意如此**，静默降级为管理员正是要修的缺陷）。

    用法：`mcp_auth(users["admin"])` 或 `mcp_auth(user, scopes=("invoice:read",))`
    """
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    from invoicing.mcp.identity import ROLE_DEFAULT_SCOPES

    tokens = []

    def _set(user, scopes=None, source="token", token_id=None):
        effective = tuple(scopes) if scopes is not None else ROLE_DEFAULT_SCOPES.get(user.role, ())
        tok = AccessToken(
            token="test-token", client_id="test-client", scopes=list(effective),
            subject=str(user.id),
            claims={"username": user.username, "role": user.role, "tenant_id": "default",
                    "source": source, "token_id": token_id, "iss": "test"},
        )
        tokens.append(auth_context_var.set(AuthenticatedUser(tok)))
        return user

    yield _set
    for t in reversed(tokens):
        auth_context_var.reset(t)
