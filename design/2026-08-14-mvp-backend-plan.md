# 发票易 MVP 后端核心服务实施计划（Plan A）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 MVP 后端核心：邮件收取、分级解析、验真查重、状态机、REST API 与审计日志，核心链路「收取→解析→验真→列表可见」有全自动化测试兜底。

**Architecture:** 单体 FastAPI 应用（src 布局，包名 `invoicing`）。SQLAlchemy 2 + Alembic 管理 PostgreSQL 数据模型；APScheduler 定时轮询邮箱；arq（Redis）异步消费解析/验真任务；MinIO（boto3 S3 兼容）存发票原件；service 层统一被 REST 与后续 MCP（Plan B）复用。

**Tech Stack:** Python 3.11+、FastAPI、SQLAlchemy 2.x、Alembic、PostgreSQL 16、Redis 7 + arq、MinIO + boto3、APScheduler、PyJWT、bcrypt、cryptography(Fernet)、lxml、pypdf、pytest、uv（包管理）。

**Spec:** `design/2026-08-14-mvp-design.md`（Plan A 覆盖其第 2~5、7 节）

## Global Constraints

- 需求基线以 `frd/InvoiceEase-frd-v0.1.txt` 为准；设计文档已评审，冲突时以设计文档为准
- 合规硬约束：只收 PDF/OFD/XML 原件；XML 原件必须单独保存（`xml_url`）；验真失败绝不自动放行；所有状态跃迁写审计日志
- 金额一律 `NUMERIC(14,2)`（SQLAlchemy `Numeric(14,2)` / Python `Decimal`），禁止 float
- 状态转换只能通过 `invoicing/workflow/state.py` 的转换表，非法转换必须抛异常
- 测试代码放在仓库根 `test/` 目录，样例文件放 `test/fixtures/`
- 每个任务结束必须 git commit，提交信息格式 `feat(backend): ...`
- 运行测试前需先 `docker compose -f deploy/dev-compose.yml up -d`（postgres/redis/minio 开发环境，Task 1 提供）

---

## 文件结构总览

```
backend/
  pyproject.toml                    # uv 项目定义、依赖、pytest 配置
  alembic.ini                       # Alembic 配置
  alembic/env.py                    # 接入 Settings 与 Base.metadata
  alembic/versions/                 # 迁移脚本（Task 2 autogenerate 生成）
  src/invoicing/
    __init__.py
    config.py                       # Settings（pydantic-settings）
    db.py                           # engine / SessionLocal / get_db / Base
    main.py                         # create_app()：路由注册 + lifespan（调度器/bootstrap/storage）
    bootstrap.py                    # 首次启动创建 admin 账号
    scheduler.py                    # APScheduler 轮询装配
    security.py                     # bcrypt + JWT + get_current_user / require_role
    permissions.py                  # 角色→动作权限矩阵
    audit.py                        # write_audit() 审计日志写入
    storage.py                      # ObjectStorage（MinIO 封装）
    models/
      __init__.py                   # re-export 全部模型
      enums.py                      # Role/InvoiceStatus/VerifyStatus/ParseSource/FileType/AuditAction
      user.py                       # User
      mailbox.py                    # Mailbox
      invoice.py                    # Invoice（含查重唯一索引）
      audit.py                      # AuditLog
    workflow/
      __init__.py
      state.py                      # TRANSITIONS 转换表 + transition()
      services.py                   # service 层：list/get/review/re-verify/stats
    fetch/
      __init__.py
      protocol.py                   # RawAttachment/RawMailMessage/MailFetcher 协议
      imap.py                       # ImapMailFetcher（imaplib）
      filters.py                    # 附件分类 + ZIP 解压
      reply.py                      # 拒收回复邮件（smtplib）
      service.py                    # poll_mailbox() 编排
    parse/
      __init__.py
      schemas.py                    # ParsedInvoice / ParseError / ParseOutcome
      xml_parser.py                 # 数电票 XML 解析
      xbrl.py                       # OFD/PDF 内嵌 XML 提取
      validation.py                 # 价税合计/大小写校验 + amount_to_cn
      router.py                     # 分级路由 parse_file()
    verify/
      __init__.py
      provider.py                   # VerifyProvider ABC + VerifyResult
      mock.py                       # MockVerifyProvider 规则引擎
      dedup.py                      # 查重服务
    workers/
      __init__.py
      queue.py                      # WorkerSettings + enqueue 辅助
      tasks.py                      # parse_invoice_task / verify_invoice_task（同步核心 + async 壳）
    api/
      __init__.py                   # api_router 聚合（前缀 /api/v1）
      deps.py                       # get_db / get_current_user / require_role
      auth.py                       # 登录/登出/me
      users.py                      # 用户管理
      invoices.py                   # 列表/详情/原件下载/复核/重验
      mailboxes.py                  # 邮箱配置/连通性测试/手动收取
      stats.py                      # 工作台统计
      audit.py                      # 审计日志查询
deploy/
  dev-compose.yml                   # 开发依赖：postgres/redis/minio
test/
  conftest.py                       # 测试环境变量 + 测试库 + db/app/client fixtures
  test_health.py  test_models.py  test_state.py  test_security.py
  test_permissions.py  test_audit.py  test_storage.py  test_xml_parser.py
  test_xbrl.py  test_validation.py  test_router.py  test_workers.py
  test_dedup.py  test_mock_provider.py  test_fetch.py  test_api_auth.py
  test_api_users.py  test_api_invoices.py  test_api_mailboxes.py
  test_api_stats.py  test_e2e.py
  fixtures/invoices/
    dianzi.xml  dianzi_bad_total.xml  dianzi_bad_cn.xml
```

**目录约定**：`backend/` 与 `test/` 都在仓库根下；`test/` 不放在 backend 内（遵循用户全局目录约定）。

---

### Task 1: 工程脚手架、配置与开发环境

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/src/invoicing/__init__.py`
- Create: `backend/src/invoicing/config.py`
- Create: `backend/src/invoicing/db.py`
- Create: `backend/src/invoicing/main.py`
- Create: `deploy/dev-compose.yml`
- Create: `test/conftest.py`
- Create: `test/test_health.py`
- Modify: `.gitignore`（追加 `.venv/`、`__pycache__/`、`.pytest_cache/`、`*.egg-info/`）

**Interfaces:**
- Produces:
  - `invoicing.config.Settings`：字段 `database_url`、`redis_url`、`minio_endpoint`、`minio_access_key`、`minio_secret_key`、`minio_bucket`、`minio_secure`、`jwt_secret`、`jwt_algorithm`、`jwt_expire_minutes`、`mcp_token`、`fernet_key`、`admin_username`、`admin_password`、`mock_verify_rules`、`scheduler_enabled`、`log_level`；模块级单例 `settings = Settings()`
  - `invoicing.db.engine`、`invoicing.db.SessionLocal`、`invoicing.db.get_db()`（FastAPI 依赖）、`invoicing.db.Base`
  - `invoicing.main.create_app() -> FastAPI`

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "invoicing"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy>=2.0",
    "psycopg[binary]>=3.2",
    "alembic>=1.13",
    "pydantic>=2.8",
    "pydantic-settings>=2.4",
    "redis>=5.0",
    "arq>=0.26",
    "apscheduler>=3.10",
    "boto3>=1.34",
    "pypdf>=4.2",
    "lxml>=5.2",
    "cryptography>=42.0",
    "bcrypt>=4.1",
    "PyJWT>=2.8",
]

[dependency-groups]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    "ruff>=0.5",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/invoicing"]

[tool.pytest.ini_options]
testpaths = ["../test"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: 写 dev-compose.yml**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: invoicing
      POSTGRES_PASSWORD: invoicing
      POSTGRES_DB: invoicing
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U invoicing"]
      interval: 5s
      retries: 10
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports: ["9000:9000", "9001:9001"]
    volumes: [miniodata:/data]
volumes:
  pgdata:
  miniodata:
```

- [ ] **Step 3: 写 config.py**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVOICING_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://invoicing:invoicing@localhost:5432/invoicing"
    redis_url: str = "redis://localhost:6379/0"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "invoice-originals"
    minio_secure: bool = False
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    mcp_token: str = "change-me"
    fernet_key: str = "change-me-32bytes-base64-key!!!"  # 生产环境必须覆盖
    admin_username: str = "admin"
    admin_password: str = "admin123"
    mock_verify_rules: str = '{"fail_prefixes": ["0000"], "error_prefixes": ["0001"]}'
    scheduler_enabled: bool = True
    log_level: str = "INFO"


settings = Settings()
```

- [ ] **Step 4: 写 db.py**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from invoicing.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 5: 写 main.py（最小版，后续任务扩展）**

```python
from fastapi import FastAPI

from invoicing.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="发票易 InvoiceEase")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "invoicing", "version": "0.1.0"}

    return app


app = create_app()
```

- [ ] **Step 6: 写 conftest.py（必须在导入 invoicing 前设置环境变量）**

```python
import os

os.environ.setdefault(
    "INVOICING_DATABASE_URL",
    "postgresql+psycopg://invoicing:invoicing@localhost:5432/invoicing_test",
)
os.environ.setdefault("INVOICING_SCHEDULER_ENABLED", "false")
os.environ.setdefault("INVOICING_JWT_SECRET", "test-secret")
os.environ.setdefault("INVOICING_ADMIN_PASSWORD", "admin123")

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import invoicing.models  # noqa: F401  确保模型注册进 Base.metadata
from invoicing.config import settings
from invoicing.db import Base


@pytest.fixture(scope="session")
def engine():
    admin_engine = create_engine(
        "postgresql+psycopg://invoicing:invoicing@localhost:5432/postgres", isolation_level="AUTOCOMMIT"
    )
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname='invoicing_test'")
        ).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE invoicing_test"))
    admin_engine.dispose()
    eng = create_engine(settings.database_url)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    trans.rollback()
    connection.close()
```

- [ ] **Step 7: 写失败的测试 test/test_health.py**

```python
from fastapi.testclient import TestClient

from invoicing.main import app


def test_health():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "invoicing", "version": "0.1.0"}
```

- [ ] **Step 8: 安装依赖并运行测试，确认失败**

Run: `cd backend && uv sync`
然后 `docker compose -f deploy/dev-compose.yml up -d`（仓库根目录执行）
Run: `cd backend && uv run pytest ../test/test_health.py -v`
Expected: FAIL（`invoicing` 包尚不存在，ModuleNotFoundError）

- [ ] **Step 9: 补 .gitignore**

```gitignore
.venv/
__pycache__/
.pytest_cache/
*.egg-info/
.env
```

- [ ] **Step 10: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_health.py -v`
Expected: PASS（postgres/dev-compose 需已启动）

- [ ] **Step 11: Commit**

```bash
git add backend/pyproject.toml backend/src/invoicing/__init__.py backend/src/invoicing/config.py \
  backend/src/invoicing/db.py backend/src/invoicing/main.py deploy/dev-compose.yml \
  test/conftest.py test/test_health.py .gitignore
git commit -m "feat(backend): 工程脚手架、配置与开发环境"
```

---

### Task 2: 数据模型与 Alembic 初始迁移

**Files:**
- Create: `backend/src/invoicing/models/__init__.py`
- Create: `backend/src/invoicing/models/enums.py`
- Create: `backend/src/invoicing/models/user.py`
- Create: `backend/src/invoicing/models/mailbox.py`
- Create: `backend/src/invoicing/models/invoice.py`
- Create: `backend/src/invoicing/models/audit.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`（与 env.py 配套，内容见下）
- Create: `test/test_models.py`

**Interfaces:**
- Consumes: `invoicing.db.Base`（Task 1）
- Produces（后续任务依赖的确切名称）:
  - `Role(str, Enum)`: `employee / finance_staff / finance_manager / admin`
  - `InvoiceStatus(str, Enum)`: `receiving / received / parsing / parsed / verifying / pending_submit / pending_review / blocked / rejected / submitted / archived`
  - `VerifyStatus(str, Enum)`: `pending / passed / failed`
  - `ParseSource(str, Enum)`: `XML / OFD_XBRL / PDF_XBRL / PDF_UNSTRUCTURED`
  - `FileType(str, Enum)`: `PDF / OFD / XML`
  - `AuditAction(str, Enum)`: `FETCH / PARSE / VERIFY / REVIEW / LOGIN / LOGOUT / REJECT_REPLY / CONFIG_CHANGE / REVERIFY`
  - `User(id, username, password_hash, role, created_at, updated_at)`
  - `Mailbox(id, name, imap_host, imap_port, use_ssl, username, password_encrypted, folder, keywords, poll_interval_seconds, smtp_host, smtp_port, smtp_username, smtp_password_encrypted, enabled, last_polled_at, last_uid, created_at, updated_at)`
  - `Invoice(id, tenant_id, user_id, mailbox_id, email_message_id, email_subject, invoice_code, invoice_number, issue_date, amount_without_tax, tax_amount, total_amount, total_amount_cn, seller_name, seller_tax_id, buyer_name, buyer_tax_id, invoice_type, file_url, file_type, xml_url, parse_source, confidence_score, validation_errors, verify_status, verify_detail, verified_at, duplicate_flag, duplicate_of_id, status, review_note, reviewed_by, reviewed_at, created_at, updated_at)`
  - `AuditLog(id, user_id, action, invoice_id, detail, ip_address, channel, created_at)`

- [ ] **Step 1: 写 models/enums.py**

```python
from enum import Enum


class Role(str, Enum):
    employee = "employee"
    finance_staff = "finance_staff"
    finance_manager = "finance_manager"
    admin = "admin"


class InvoiceStatus(str, Enum):
    receiving = "receiving"
    received = "received"
    parsing = "parsing"
    parsed = "parsed"
    verifying = "verifying"
    pending_submit = "pending_submit"
    pending_review = "pending_review"
    blocked = "blocked"
    rejected = "rejected"
    submitted = "submitted"
    archived = "archived"


class VerifyStatus(str, Enum):
    pending = "pending"
    passed = "passed"
    failed = "failed"


class ParseSource(str, Enum):
    XML = "XML"
    OFD_XBRL = "OFD_XBRL"
    PDF_XBRL = "PDF_XBRL"
    PDF_UNSTRUCTURED = "PDF_UNSTRUCTURED"


class FileType(str, Enum):
    PDF = "PDF"
    OFD = "OFD"
    XML = "XML"


class AuditAction(str, Enum):
    FETCH = "FETCH"
    PARSE = "PARSE"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    REJECT_REPLY = "REJECT_REPLY"
    CONFIG_CHANGE = "CONFIG_CHANGE"
    REVERIFY = "REVERIFY"
```

- [ ] **Step 2: 写 models/user.py**

```python
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.enums import Role


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=Role.employee.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=func.now(),
        nullable=False,
    )
```

（说明：`default` 提供 Python 侧默认值，保证 API 返回对象在未刷新前 `created_at` 即非空；`server_default` 保证非 ORM 写入也有值。）

- [ ] **Step 3: 写 models/mailbox.py**

```python
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base


class Mailbox(Base):
    __tablename__ = "mailboxes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    imap_host: Mapped[str] = mapped_column(String(256), nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    username: Mapped[str] = mapped_column(String(256), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(String(512), nullable=False)
    folder: Mapped[str] = mapped_column(String(128), nullable=False, default="INBOX")
    keywords: Mapped[str] = mapped_column(String(256), nullable=False, default="发票,Invoice")
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    smtp_host: Mapped[str | None] = mapped_column(String(256), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_username: Mapped[str | None] = mapped_column(String(256), nullable=True)
    smtp_password_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_polled_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_uid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=func.now(),
        nullable=False,
    )
```

- [ ] **Step 4: 写 models/invoice.py（含查重唯一索引与 email_message_id 部分唯一索引）**

```python
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.enums import InvoiceStatus, VerifyStatus


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        Index(
            "uq_invoices_dedup_key",
            "tenant_id",
            func.coalesce("invoice_code", ""),
            "invoice_number",
            unique=True,
        ),
        Index(
            "uq_invoices_email_message_id",
            "email_message_id",
            unique=True,
            postgresql_where=("email_message_id IS NOT NULL"),
        ),
        Index("ix_invoices_status", "status"),
        Index("ix_invoices_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id"), nullable=True)
    email_message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    email_subject: Mapped[str | None] = mapped_column(String(512), nullable=True)

    invoice_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_without_tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_amount_cn: Mapped[str | None] = mapped_column(String(128), nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    seller_tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    buyer_tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    file_url: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(8), nullable=False)
    xml_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    parse_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_errors: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    verify_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VerifyStatus.pending.value
    )
    verify_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duplicate_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=InvoiceStatus.received.value, index=False
    )
    review_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=func.now(),
        nullable=False,
    )
```

- [ ] **Step 5: 写 models/audit.py 与 models/__init__.py**

```python
# models/audit.py
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = ()

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
```

```python
# models/__init__.py
from invoicing.models.audit import AuditLog
from invoicing.models.enums import (
    AuditAction,
    FileType,
    InvoiceStatus,
    ParseSource,
    Role,
    VerifyStatus,
)
from invoicing.models.invoice import Invoice
from invoicing.models.mailbox import Mailbox
from invoicing.models.user import User

__all__ = [
    "AuditLog",
    "AuditAction",
    "FileType",
    "Invoice",
    "InvoiceStatus",
    "Mailbox",
    "ParseSource",
    "Role",
    "User",
    "VerifyStatus",
]
```

- [ ] **Step 6: 写失败测试 test/test_models.py**

```python
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from invoicing.models import Invoice, User


def test_insert_and_query_invoice(db):
    user = User(username="u1", password_hash="x", role="finance_staff")
    db.add(user)
    db.flush()
    inv = Invoice(
        invoice_number="24312000000012345678",
        issue_date=date(2026, 8, 1),
        total_amount=Decimal("1000.00"),
        file_url="u1/file.pdf",
        file_type="PDF",
        mailbox_id=None,
        email_message_id="<msg1@example.com>",
        email_subject="发票",
    )
    db.add(inv)
    db.flush()
    assert inv.id is not None
    assert inv.status == "received"


def test_duplicate_dedup_key_raises_integrity_error(db):
    db.add(Invoice(invoice_number="24312000000011112222", file_url="a.xml", file_type="XML"))
    db.flush()
    dup = Invoice(invoice_number="24312000000011112222", file_url="b.xml", file_type="XML")
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.flush()


def test_duplicate_email_message_id_raises_integrity_error(db):
    db.add(Invoice(file_url="a.xml", file_type="XML", email_message_id="<m1@x.com>"))
    db.flush()
    db.add(Invoice(file_url="b.xml", file_type="XML", email_message_id="<m1@x.com>"))
    with pytest.raises(IntegrityError):
        db.flush()
```

- [ ] **Step 7: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_models.py -v`
Expected: FAIL（models 模块不存在）

- [ ] **Step 8: 实现 models 包（Steps 1-5 内容落盘）**

- [ ] **Step 9: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_models.py -v`
Expected: 3 PASS（测试库 invoicing_test 由 conftest 自动创建；dev-compose 的 postgres 必须已启动）

- [ ] **Step 10: 配置 Alembic 并生成初始迁移**

Run: `cd backend && uv run alembic init alembic`
然后改写 `backend/alembic.ini` 中 `sqlalchemy.url` 一行留空（由 env.py 注入），并写 `backend/alembic/env.py`：

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from invoicing.config import settings
from invoicing.db import Base
import invoicing.models  # noqa: F401  确保模型注册进 Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`script.py.mako` 使用 `alembic init` 生成的默认模板即可，无需改动。

Run: `cd backend && uv run alembic revision --autogenerate -m "init tables"`
然后检查 `backend/alembic/versions/` 生成的迁移脚本：必须包含 users/mailboxes/invoices/audit_logs 4 张表、`uq_invoices_dedup_key` 唯一索引（表达式 `coalesce(invoice_code, '')`）、`uq_invoices_email_message_id` 部分唯一索引。缺失则手工补进迁移脚本。

Run: `cd backend && uv run alembic upgrade head`
Expected: 开发库 invoicing 中出现 users/mailboxes/invoices/audit_logs 4 张表。

- [ ] **Step 11: Commit**

```bash
git add backend/src/invoicing/models backend/alembic.ini backend/alembic test/test_models.py
git commit -m "feat(backend): 数据模型与 Alembic 初始迁移"
```

---

### Task 3: 状态机转换表

**Files:**
- Create: `backend/src/invoicing/workflow/__init__.py`
- Create: `backend/src/invoicing/workflow/state.py`
- Create: `test/test_state.py`

**Interfaces:**
- Consumes: `invoicing.models.Invoice`、`InvoiceStatus`（Task 2）
- Produces:
  - `TRANSITIONS: dict[str, set[str]]`（状态 → 允许的下一状态集合）
  - `can_transition(from_status: str, to_status: str) -> bool`
  - `transition(invoice: Invoice, to_status: str) -> None`（非法转换抛 `ValueError`）

- [ ] **Step 1: 写失败测试 test/test_state.py**

```python
import pytest

from invoicing.models import Invoice, InvoiceStatus
from invoicing.workflow.state import TRANSITIONS, can_transition, transition


def test_legal_transitions():
    assert can_transition("received", "parsing")
    assert can_transition("parsing", "pending_review")
    assert can_transition("verifying", "pending_submit")
    assert can_transition("verifying", "blocked")
    assert can_transition("pending_review", "rejected")
    assert can_transition("pending_review", "pending_submit")
    assert can_transition("pending_submit", "verifying")


def test_illegal_transitions():
    assert not can_transition("received", "parsed")  # 跳级非法
    assert not can_transition("blocked", "pending_submit")  # 拦截后不可放行
    assert not can_transition("archived", "parsing")
    assert not can_transition("pending_submit", "received")


def test_transition_applies_status():
    inv = Invoice(file_url="a.xml", file_type="XML", status="received")
    transition(inv, "parsing")
    assert inv.status == "parsing"


def test_transition_raises_on_illegal():
    inv = Invoice(file_url="a.xml", file_type="XML", status="blocked")
    with pytest.raises(ValueError, match="非法状态转换"):
        transition(inv, "pending_submit")


def test_terminal_states_defined():
    for status in InvoiceStatus:
        assert status.value in TRANSITIONS  # 所有状态都有转换表条目
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_state.py -v`
Expected: FAIL（workflow.state 不存在）

- [ ] **Step 3: 实现 state.py**

```python
from invoicing.models import Invoice


TRANSITIONS: dict[str, set[str]] = {
    "receiving": {"received"},
    "received": {"parsing"},
    "parsing": {"parsed", "pending_review"},
    "parsed": {"verifying"},
    "verifying": {"pending_submit", "pending_review", "blocked"},
    "pending_review": {"pending_submit", "rejected", "verifying"},
    "pending_submit": {"verifying", "submitted"},
    "blocked": set(),
    "rejected": set(),
    "submitted": {"archived"},
    "archived": set(),
}


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in TRANSITIONS.get(from_status, set())


def transition(invoice: Invoice, to_status: str) -> None:
    if not can_transition(invoice.status, to_status):
        raise ValueError(
            f"非法状态转换: {invoice.status} -> {to_status} (invoice_id={invoice.id})"
        )
    invoice.status = to_status
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_state.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/invoicing/workflow test/test_state.py
git commit -m "feat(backend): 状态机转换表"
```

---

### Task 4: 安全模块（bcrypt + JWT）与权限矩阵

**Files:**
- Create: `backend/src/invoicing/security.py`
- Create: `backend/src/invoicing/permissions.py`
- Create: `test/test_security.py`
- Create: `test/test_permissions.py`

**Interfaces:**
- Consumes: `User`、`Role`（Task 2）、`get_db`（Task 1）
- Produces:
  - `hash_password(password: str) -> str`
  - `verify_password(password: str, password_hash: str) -> bool`
  - `create_access_token(user: User) -> str`
  - `decode_token(token: str) -> dict`（无效/过期抛 `jwt.PyJWTError`）
  - FastAPI 依赖：`get_current_user(db=Depends(get_db), credentials=Depends(HTTPBearer(auto_error=False))) -> User`（未登录抛 401；用户不存在抛 401）
  - FastAPI 依赖工厂：`require_role(*roles: str)`（角色不符抛 403）
  - `permissions.ROLE_ACTIONS: dict[str, set[str]]` 与 `has_action(role: str, action: str) -> bool`

- [ ] **Step 1: 写失败测试 test/test_security.py**

```python
import pytest

from invoicing.security import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)
```

（Step 1 只有这一条测试，因 `invoicing.security` 不存在而失败；Step 4 在文件末尾追加其余测试。）


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

    app.dependency_overrides[get_db_import()] = override_db
    return app


def get_db_import():
    from invoicing.db import get_db

    return get_db
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_security.py -v`
Expected: FAIL（`invoicing.security` 模块不存在）

- [ ] **Step 3: 实现 security.py 与 permissions.py**

```python
# security.py
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from invoicing.config import settings
from invoicing.db import get_db
from invoicing.models import User

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def get_current_user(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未提供认证凭证")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "认证凭证无效或已过期")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user


def require_role(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限执行此操作")
        return user

    return checker
```

```python
# permissions.py
from invoicing.models.enums import Role

ROLE_ACTIONS: dict[str, set[str]] = {
    Role.employee.value: {"view_invoice"},
    Role.finance_staff.value: {"view_invoice", "review_invoice", "reverify_invoice"},
    Role.finance_manager.value: {
        "view_invoice",
        "review_invoice",
        "reverify_invoice",
        "approve_invoice",
    },
    Role.admin.value: {"*"},
}


def has_action(role: str, action: str) -> bool:
    actions = ROLE_ACTIONS.get(role, set())
    return "*" in actions or action in actions
```

- [ ] **Step 4: 在 test/test_security.py 末尾追加以下测试**

```python
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
```

- [ ] **Step 5: 写失败测试 test/test_permissions.py（先写，后实现）**

```python
from invoicing.permissions import has_action


def test_action_matrix():
    assert has_action("employee", "view_invoice")
    assert not has_action("employee", "review_invoice")
    assert has_action("finance_staff", "review_invoice")
    assert not has_action("finance_staff", "approve_invoice")
    assert has_action("finance_manager", "approve_invoice")
    assert has_action("admin", "anything")
```

- [ ] **Step 6: 运行全部新测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_security.py ../test/test_permissions.py -v`
Expected: 6 PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/invoicing/security.py backend/src/invoicing/permissions.py \
  test/test_security.py test/test_permissions.py
git commit -m "feat(backend): 安全模块（bcrypt+JWT）与权限矩阵"
```

---

### Task 5: 审计日志写入

**Files:**
- Create: `backend/src/invoicing/audit.py`
- Create: `test/test_audit.py`

**Interfaces:**
- Consumes: `AuditLog`（Task 2）
- Produces:
  - `write_audit(db, action: str, user_id: int | None = None, invoice_id: int | None = None, detail: dict | None = None, ip_address: str | None = None, channel: str = "web") -> AuditLog`（写入后 `db.flush()` 返回对象，由调用方决定何时 commit）

- [ ] **Step 1: 写失败测试 test/test_audit.py**

```python
from invoicing.audit import write_audit
from invoicing.models import AuditLog, Invoice, User


def test_write_audit(db):
    user = User(username="u1", password_hash="x", role="admin")
    db.add(user)
    db.flush()
    inv = Invoice(file_url="a.xml", file_type="XML")
    db.add(inv)
    db.flush()

    log = write_audit(
        db,
        action="REVIEW",
        user_id=user.id,
        invoice_id=inv.id,
        detail={"note": "通过"},
        ip_address="127.0.0.1",
        channel="web",
    )
    assert log.id is not None
    assert log.action == "REVIEW"
    assert log.detail == {"note": "通过"}
    assert log.channel == "web"


def test_write_audit_system_channel(db):
    log = write_audit(db, action="FETCH", channel="system", detail={"mailbox_id": 1})
    assert log.user_id is None
    assert log.channel == "system"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_audit.py -v`
Expected: FAIL（invoicing.audit 不存在）

- [ ] **Step 3: 实现 audit.py**

```python
from sqlalchemy.orm import Session

from invoicing.models import AuditLog


def write_audit(
    db: Session,
    action: str,
    user_id: int | None = None,
    invoice_id: int | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
    channel: str = "web",
) -> AuditLog:
    log = AuditLog(
        user_id=user_id,
        action=action,
        invoice_id=invoice_id,
        detail=detail,
        ip_address=ip_address,
        channel=channel,
    )
    db.add(log)
    db.flush()
    return log
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_audit.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/invoicing/audit.py test/test_audit.py
git commit -m "feat(backend): 审计日志写入服务"
```

---

### Task 6: 对象存储（MinIO 封装）

**Files:**
- Create: `backend/src/invoicing/storage.py`
- Create: `test/test_storage.py`

**Interfaces:**
- Consumes: `settings`（Task 1）
- Produces:
  - `class ObjectStorage`：`__init__(endpoint, access_key, secret_key, bucket, secure)`；方法 `ensure_bucket() -> None`、`put(key: str, data: bytes, content_type: str) -> str`（返回 key）、`get(key: str) -> bytes`、`presigned_url(key: str, expires: int = 3600) -> str`
  - `get_storage() -> ObjectStorage`（基于 settings 构建的单例）

- [ ] **Step 1: 写失败测试 test/test_storage.py（依赖 dev-compose 的 minio 运行）**

```python
import pytest

from invoicing.config import settings
from invoicing.storage import ObjectStorage


@pytest.fixture(scope="module")
def storage():
    st = ObjectStorage(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket=settings.minio_bucket,
        secure=settings.minio_secure,
    )
    st.ensure_bucket()
    return st


def test_put_and_get_roundtrip(storage):
    key = storage.put("test/hello.xml", b"<eInvoice/>", "application/xml")
    assert key == "test/hello.xml"
    assert storage.get(key) == b"<eInvoice/>"


def test_presigned_url(storage):
    key = storage.put("test/url.pdf", b"%PDF-1.4", "application/pdf")
    url = storage.presigned_url(key)
    assert url.startswith("http")
    assert key in url
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_storage.py -v`
Expected: FAIL（invoicing.storage 不存在）

- [ ] **Step 3: 实现 storage.py**

```python
import boto3
from botocore.client import Config

from invoicing.config import settings


class ObjectStorage:
    def __init__(
        self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool
    ):
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=f"{'https' if secure else 'http'}://{endpoint}",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def presigned_url(self, key: str, expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires,
        )


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        _storage = ObjectStorage(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
            secure=settings.minio_secure,
        )
    return _storage
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_storage.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/invoicing/storage.py test/test_storage.py
git commit -m "feat(backend): MinIO 对象存储封装"
```

---

### Task 7: 解析引擎 —— 数电票 XML 解析

**Files:**
- Create: `backend/src/invoicing/parse/__init__.py`
- Create: `backend/src/invoicing/parse/schemas.py`
- Create: `backend/src/invoicing/parse/xml_parser.py`
- Create: `test/fixtures/invoices/dianzi.xml`
- Create: `test/test_xml_parser.py`

**Interfaces:**
- Consumes: `ParseSource`、`FileType`（Task 2）
- Produces:
  - `ParsedInvoice`（pydantic v2）：`invoice_code: str | None`、`invoice_number: str`、`issue_date: date`、`amount_without_tax: Decimal`、`tax_amount: Decimal`、`total_amount: Decimal`、`total_amount_cn: str`、`seller_name: str`、`seller_tax_id: str`、`buyer_name: str`、`buyer_tax_id: str`、`invoice_type: str | None`、`confidence_score: float`、`parse_source: str`
  - `ParseError`：`code: str`、`message: str`
  - `parse_invoice_xml(data: bytes) -> ParsedInvoice`（解析失败抛 `ValueError`，消息形如 `XML_PARSE_ERROR: ...`）

- [ ] **Step 1: 写 fixture test/fixtures/invoices/dianzi.xml（脱敏样例，数字自洽：909.09 + 90.91 = 1000.00）**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<eInvoice xmlns="http://www.chinatax.gov.cn/eip">
  <EInvoiceHeader>
    <EInvoiceNumber>24312000000012345678</EInvoiceNumber>
    <EInvoiceType>81</EInvoiceType>
    <IssueDate>2026-08-01</IssueDate>
    <TotalAmountExcludingTax>909.09</TotalAmountExcludingTax>
    <TotalTaxAmount>90.91</TotalTaxAmount>
    <AmountInFigures>1000.00</AmountInFigures>
    <AmountInWords>壹仟元整</AmountInWords>
  </EInvoiceHeader>
  <EInvoiceSellerInformation>
    <SellerName>示例科技有限公司</SellerName>
    <SellerTaxpayerIdentificationNumber>91310000MA1FL0A000</SellerTaxpayerIdentificationNumber>
  </EInvoiceSellerInformation>
  <EInvoiceBuyerInformation>
    <BuyerName>测试采购有限公司</BuyerName>
    <BuyerTaxpayerIdentificationNumber>91310000MA1FL0B000</BuyerTaxpayerIdentificationNumber>
  </EInvoiceBuyerInformation>
</eInvoice>
```

- [ ] **Step 2: 写失败测试 test/test_xml_parser.py**

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoicing.parse.xml_parser import parse_invoice_xml

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_parse_dianzi_xml():
    data = (FIXTURES / "dianzi.xml").read_bytes()
    parsed = parse_invoice_xml(data)
    assert parsed.invoice_number == "24312000000012345678"
    assert parsed.invoice_code is None
    assert parsed.issue_date == date(2026, 8, 1)
    assert parsed.amount_without_tax == Decimal("909.09")
    assert parsed.tax_amount == Decimal("90.91")
    assert parsed.total_amount == Decimal("1000.00")
    assert parsed.total_amount_cn == "壹仟元整"
    assert parsed.seller_name == "示例科技有限公司"
    assert parsed.seller_tax_id == "91310000MA1FL0A000"
    assert parsed.buyer_name == "测试采购有限公司"
    assert parsed.buyer_tax_id == "91310000MA1FL0B000"
    assert parsed.invoice_type == "81"
    assert parsed.confidence_score == 1.0
    assert parsed.parse_source == "XML"


def test_parse_invalid_xml_raises():
    with pytest.raises(ValueError, match="XML_PARSE_ERROR"):
        parse_invoice_xml(b"<not-an-invoice/>")
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_xml_parser.py -v`
Expected: FAIL（invoicing.parse 不存在）

- [ ] **Step 4: 实现 schemas.py 与 xml_parser.py**

```python
# parse/schemas.py
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ParsedInvoice(BaseModel):
    invoice_code: str | None = None
    invoice_number: str
    issue_date: date
    amount_without_tax: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    total_amount_cn: str
    seller_name: str
    seller_tax_id: str
    buyer_name: str
    buyer_tax_id: str
    invoice_type: str | None = None
    confidence_score: float
    parse_source: str


class ParseError(BaseModel):
    code: str
    message: str
```

```python
# parse/xml_parser.py
from datetime import date
from decimal import Decimal

from lxml import etree

from invoicing.models.enums import ParseSource
from invoicing.parse.schemas import ParsedInvoice


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(root, *names: str) -> str | None:
    wanted = set(names)
    for el in root.iter():
        if _local_name(el.tag) in wanted and el.text is not None:
            return el.text.strip()
    return None


def parse_invoice_xml(data: bytes) -> ParsedInvoice:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError as e:
        raise ValueError(f"XML_PARSE_ERROR: {e}")

    number = _first_text(root, "EInvoiceNumber", "InvoiceNumber")
    issue_date = _first_text(root, "IssueDate")
    if not number or not issue_date:
        raise ValueError(
            "XML_PARSE_ERROR: 缺少 EInvoiceNumber/IssueDate，不是数电票 XML"
        )
    try:
        parsed_date = date.fromisoformat(issue_date)
    except ValueError as e:
        raise ValueError(f"XML_PARSE_ERROR: 开票日期格式非法 {issue_date}")

    return ParsedInvoice(
        invoice_number=number,
        issue_date=parsed_date,
        amount_without_tax=Decimal(_first_text(root, "TotalAmountExcludingTax") or "0"),
        tax_amount=Decimal(_first_text(root, "TotalTaxAmount") or "0"),
        total_amount=Decimal(_first_text(root, "AmountInFigures") or "0"),
        total_amount_cn=_first_text(root, "AmountInWords") or "",
        seller_name=_first_text(root, "SellerName") or "",
        seller_tax_id=_first_text(root, "SellerTaxpayerIdentificationNumber") or "",
        buyer_name=_first_text(root, "BuyerName") or "",
        buyer_tax_id=_first_text(root, "BuyerTaxpayerIdentificationNumber") or "",
        invoice_type=_first_text(root, "EInvoiceType"),
        confidence_score=1.0,
        parse_source=ParseSource.XML.value,
    )
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_xml_parser.py -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/invoicing/parse test/test_xml_parser.py test/fixtures/invoices/dianzi.xml
git commit -m "feat(backend): 数电票 XML 解析引擎"
```

---

### Task 8: OFD/PDF 内嵌 XML（XBRL）提取

**Files:**
- Create: `backend/src/invoicing/parse/xbrl.py`
- Create: `test/test_xbrl.py`

**Interfaces:**
- Consumes: `parse_invoice_xml`（Task 7）
- Produces:
  - `extract_xml_from_ofd(data: bytes) -> bytes | None`（zipfile 中寻找可解析为数电票的 XML；找不到返回 None）
  - `extract_xml_from_pdf(data: bytes) -> bytes | None`（pypdf 附件中寻找可解析为数电票的 XML；找不到返回 None）

- [ ] **Step 1: 写失败测试 test/test_xbrl.py（fixture 在测试内用代码构造）**

```python
import io
import zipfile
from pathlib import Path

from pypdf import PdfWriter

from invoicing.parse.xbrl import extract_xml_from_ofd, extract_xml_from_pdf

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"
INVOICE_XML = (FIXTURES / "dianzi.xml").read_bytes()


def _build_ofd_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org'/>")
        zf.writestr("Doc_0/Attachs/original_invoice.xml", INVOICE_XML)
    return buf.getvalue()


def _build_pdf_with_attachment() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_attachment("original_invoice.xml", INVOICE_XML)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _build_plain_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_xml_from_ofd():
    assert extract_xml_from_ofd(_build_ofd_bytes()) is not None


def test_extract_xml_from_ofd_returns_none_for_plain_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    assert extract_xml_from_ofd(buf.getvalue()) is None


def test_extract_xml_from_pdf_attachment():
    xml = extract_xml_from_pdf(_build_pdf_with_attachment())
    assert xml is not None


def test_extract_xml_from_pdf_plain_returns_none():
    assert extract_xml_from_pdf(_build_plain_pdf()) is None


def test_extracted_xml_is_parseable():
    from invoicing.parse.xml_parser import parse_invoice_xml

    parsed = parse_invoice_xml(extract_xml_from_ofd(_build_ofd_bytes()))
    assert parsed.invoice_number == "24312000000012345678"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_xbrl.py -v`
Expected: FAIL（invoicing.parse.xbrl 不存在）

- [ ] **Step 3: 实现 xbrl.py**

```python
import io
import zipfile

from lxml import etree
from pypdf import PdfReader

from invoicing.parse.xml_parser import parse_invoice_xml


def _is_invoice_xml(data: bytes) -> bool:
    try:
        parse_invoice_xml(data)
        return True
    except ValueError:
        return False


def _find_invoice_xml(candidates: list[bytes]) -> bytes | None:
    for data in candidates:
        if _is_invoice_xml(data):
            return data
    return None


def extract_xml_from_ofd(data: bytes) -> bytes | None:
    """OFD 是 zip 容器，遍历其中 XML 文件，返回第一个可解析为数电票的 XML 原文。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    candidates = [
        zf.read(name)
        for name in zf.namelist()
        if name.lower().endswith(".xml") and not name.endswith("/")
    ]
    return _find_invoice_xml(candidates)


def extract_xml_from_pdf(data: bytes) -> bytes | None:
    """提取 PDF 内嵌附件中的数电票 XML。"""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return None
    candidates: list[bytes] = []
    attachments = getattr(reader, "attachments", None) or {}
    for payload in attachments.values():
        if isinstance(payload, (list, tuple)):
            candidates.extend(p for p in payload if isinstance(p, bytes))
        elif isinstance(payload, bytes):
            candidates.append(payload)
    return _find_invoice_xml(candidates)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_xbrl.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/invoicing/parse/xbrl.py test/test_xbrl.py
git commit -m "feat(backend): OFD/PDF 内嵌 XBRL 提取"
```

---

### Task 9: 校验逻辑（价税合计 + 大小写金额）

**Files:**
- Create: `backend/src/invoicing/parse/validation.py`
- Create: `test/fixtures/invoices/dianzi_bad_total.xml`
- Create: `test/fixtures/invoices/dianzi_bad_cn.xml`
- Create: `test/test_validation.py`

**Interfaces:**
- Consumes: `ParsedInvoice`（Task 7）
- Produces:
  - `amount_to_cn(amount: Decimal) -> str`（小写金额 → 中文大写，负数抛 ValueError）
  - `validate(parsed: ParsedInvoice) -> list[ParseError]`（错误码 `TOTAL_MISMATCH` / `CN_MISMATCH`）

- [ ] **Step 1: 写 fixtures**

dianzi_bad_total.xml：复制 dianzi.xml，将 `<TotalAmountExcludingTax>909.09</TotalAmountExcludingTax>` 改为 `909.10`（合计 1000.01 ≠ 1000.00）。
dianzi_bad_cn.xml：复制 dianzi.xml，将 `<AmountInWords>壹仟元整</AmountInWords>` 改为 `<AmountInWords>壹佰元整</AmountInWords>`。

- [ ] **Step 2: 写失败测试 test/test_validation.py**

```python
from decimal import Decimal
from pathlib import Path

from invoicing.parse.schemas import ParseError
from invoicing.parse.validation import amount_to_cn, validate
from invoicing.parse.xml_parser import parse_invoice_xml

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_amount_to_cn():
    assert amount_to_cn(Decimal("1000.00")) == "壹仟元整"
    assert amount_to_cn(Decimal("100.05")) == "壹佰元零伍分"
    assert amount_to_cn(Decimal("105.30")) == "壹佰零伍元叁角"
    assert amount_to_cn(Decimal("0")) == "零元整"
    assert amount_to_cn(Decimal("100000000.00")) == "壹亿元整"


def test_validate_clean_invoice():
    parsed = parse_invoice_xml((FIXTURES / "dianzi.xml").read_bytes())
    assert validate(parsed) == []


def test_validate_total_mismatch():
    parsed = parse_invoice_xml((FIXTURES / "dianzi_bad_total.xml").read_bytes())
    errors = validate(parsed)
    assert any(e.code == "TOTAL_MISMATCH" for e in errors)


def test_validate_cn_mismatch():
    parsed = parse_invoice_xml((FIXTURES / "dianzi_bad_cn.xml").read_bytes())
    errors = validate(parsed)
    assert any(e.code == "CN_MISMATCH" for e in errors)
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_validation.py -v`
Expected: FAIL（invoicing.parse.validation 不存在）

- [ ] **Step 4: 实现 validation.py**

```python
from decimal import ROUND_HALF_UP, Decimal

from invoicing.parse.schemas import ParseError, ParsedInvoice

CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
CN_UNITS = ["", "拾", "佰", "仟"]
CN_SECTIONS = ["", "万", "亿", "万亿"]


def _section_to_cn(n: int) -> str:
    """0 <= n <= 9999 → 中文（无单位尾部补零）"""
    out, zero = "", False
    for pos in (3, 2, 1, 0):
        unit = 10**pos
        d, n = divmod(n, unit)
        if d == 0:
            if out and n > 0:
                zero = True
            continue
        if zero:
            out += "零"
            zero = False
        out += CN_DIGITS[d] + CN_UNITS[pos]
    return out


def amount_to_cn(amount: Decimal) -> str:
    if amount < 0:
        raise ValueError("金额不能为负")
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_cents = int(amount * 100)
    yuan, cents = divmod(total_cents, 100)
    jiao, fen = divmod(cents, 10)
    if yuan == 0 and jiao == 0 and fen == 0:
        return "零元整"
    result = ""
    if yuan > 0:
        secs = []
        n = yuan
        while n > 0:
            secs.append(n % 10000)
            n //= 10000
        for i in range(len(secs) - 1, -1, -1):
            seg = _section_to_cn(secs[i])
            if not seg:
                if result and i > 0 and not result.endswith("零"):
                    result += "零"
                continue
            if result and secs[i] < 1000 and not result.endswith("零"):
                result += "零"
            result += seg + CN_SECTIONS[i]
        result += "元"
    if jiao == 0 and fen == 0:
        result += "整"
    else:
        if jiao:
            result += CN_DIGITS[jiao] + "角"
        elif result and fen:
            result += "零"
        if fen:
            result += CN_DIGITS[fen] + "分"
    return result


def validate(parsed: ParsedInvoice) -> list[ParseError]:
    errors: list[ParseError] = []
    expected_total = parsed.amount_without_tax + parsed.tax_amount
    if expected_total != parsed.total_amount:
        errors.append(
            ParseError(
                code="TOTAL_MISMATCH",
                message=(
                    f"价税合计不一致: 不含税{parsed.amount_without_tax} + "
                    f"税额{parsed.tax_amount} != 合计{parsed.total_amount}"
                ),
            )
        )
    if parsed.total_amount_cn:
        cn = parsed.total_amount_cn.replace(" ", "").replace("　", "")
        if amount_to_cn(parsed.total_amount) != cn:
            errors.append(
                ParseError(
                    code="CN_MISMATCH",
                    message=f"大小写金额不一致: {parsed.total_amount} 对应 '{amount_to_cn(parsed.total_amount)}'，实际 '{cn}'",
                )
            )
    return errors
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_validation.py -v`
Expected: 5 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/invoicing/parse/validation.py test/test_validation.py \
  test/fixtures/invoices/dianzi_bad_total.xml test/fixtures/invoices/dianzi_bad_cn.xml
git commit -m "feat(backend): 解析校验（价税合计/大小写金额）"
```

---

### Task 10: 分级路由与 parse worker 任务

**Files:**
- Create: `backend/src/invoicing/parse/router.py`
- Create: `backend/src/invoicing/workers/__init__.py`
- Create: `backend/src/invoicing/workers/queue.py`
- Create: `backend/src/invoicing/workers/tasks.py`
- Create: `test/test_router.py`
- Create: `test/test_workers.py`

**Interfaces:**
- Consumes: `parse_invoice_xml`、`validate`、`extract_xml_from_ofd/pdf`、`FileType`/`ParseSource`、`Invoice`、`AuditLog.write_audit`、`transition`、`storage.get_storage`、`enqueue_verify`（本任务产出）
- Produces:
  - `ParseOutcome(source: str | None, parsed: ParsedInvoice | None, errors: list[ParseError])`
  - `parse_file(file_type: str, data: bytes) -> ParseOutcome`
  - `enqueue_parse(invoice_id: int) -> None`（异步，arq `_job_id=f"parse:{invoice_id}"` 幂等）
  - `enqueue_verify(invoice_id: int) -> None`（异步，`_job_id=f"verify:{invoice_id}"`）
  - `enqueue_parse_sync(invoice_id: int) -> None`（`asyncio.run` 包装，供同步上下文调用；失败仅记录日志不抛出）
  - `WorkerSettings`（arq，`functions=[parse_invoice_task, verify_invoice_task]`、`max_tries=3`、`max_jobs=10`）
  - `async parse_invoice_task(ctx, invoice_id)`（`asyncio.to_thread` 调 `_parse_invoice`）
  - `_parse_invoice(invoice_id: int) -> None`（同步核心：可被测试直接调用）
  - `async verify_invoice_task(ctx, invoice_id)` 与 `_verify_invoice(invoice_id)`（verify 核心在 Task 11 实现，本任务先给 tasks.py 留 parse 部分并注册）

- [ ] **Step 1: 写失败测试 test/test_router.py**

```python
from pathlib import Path

from invoicing.parse.router import parse_file

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_route_xml():
    outcome = parse_file("XML", (FIXTURES / "dianzi.xml").read_bytes())
    assert outcome.source == "XML"
    assert outcome.parsed.invoice_number == "24312000000012345678"
    assert outcome.errors == []


def test_route_pdf_unstructured():
    outcome = parse_file("PDF", b"%PDF-1.4 no attachments")
    assert outcome.source == "PDF_UNSTRUCTURED"
    assert outcome.parsed is None
    assert outcome.xml_data is None


def test_route_xml_keeps_raw_xml():
    data = (FIXTURES / "dianzi.xml").read_bytes()
    outcome = parse_file("XML", data)
    assert outcome.xml_data == data
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_router.py -v`
Expected: FAIL（invoicing.parse.router 不存在）

- [ ] **Step 3: 实现 router.py**

```python
from invoicing.models.enums import FileType, ParseSource
from invoicing.parse.schemas import ParseError, ParseOutcome
from invoicing.parse.validation import validate
from invoicing.parse.xbrl import extract_xml_from_ofd, extract_xml_from_pdf
from invoicing.parse.xml_parser import parse_invoice_xml


def _parse_structured(data: bytes, source: ParseSource) -> ParseOutcome:
    try:
        parsed = parse_invoice_xml(data)
    except ValueError as e:
        return ParseOutcome(source=None, parsed=None, xml_data=data, errors=[ParseError(code="XML_PARSE_ERROR", message=str(e))])
    errors = validate(parsed)
    return ParseOutcome(source=source.value, parsed=parsed, errors=errors, xml_data=data)


def parse_file(file_type: str, data: bytes) -> ParseOutcome:
    if file_type == FileType.XML.value:
        return _parse_structured(data, ParseSource.XML)
    if file_type == FileType.OFD.value:
        xml = extract_xml_from_ofd(data)
        if xml is not None:
            return _parse_structured(xml, ParseSource.OFD_XBRL)
        return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
    if file_type == FileType.PDF.value:
        xml = extract_xml_from_pdf(data)
        if xml is not None:
            return _parse_structured(xml, ParseSource.PDF_XBRL)
        return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
    raise ValueError(f"不支持的文件类型: {file_type}")
```

并在 `parse/schemas.py` 追加：

```python
class ParseOutcome(BaseModel):
    source: str | None
    parsed: ParsedInvoice | None
    errors: list[ParseError]
    xml_data: bytes | None = None  # 提取出的 XML 原文（合规归档用）
```

- [ ] **Step 4: 写失败测试 test/test_workers.py**

```python
from pathlib import Path

import pytest

from invoicing.models import Invoice
from invoicing.storage import ObjectStorage

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


@pytest.fixture(scope="module")
def storage():
    from invoicing.config import settings

    st = ObjectStorage(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket=settings.minio_bucket,
        secure=settings.minio_secure,
    )
    st.ensure_bucket()
    return st


def _make_invoice(db, storage, filename="dianzi.xml", file_type="XML"):
    data = (FIXTURES / filename).read_bytes()
    key = f"test-worker/{filename}"
    storage.put(key, data, "application/xml")
    inv = Invoice(file_url=key, file_type=file_type, status="parsing", invoice_number="24312000000012345678")
    db.add(inv)
    db.flush()
    return inv


def test_parse_invoice_task_success(db, storage):
    from invoicing.workers.tasks import _parse_invoice

    inv = _make_invoice(db, storage)
    _parse_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "parsed"
    assert inv.parse_source == "XML"
    assert inv.total_amount is not None
    assert inv.confidence_score == 1.0


def test_parse_invoice_task_unstructured_goes_to_review(db, storage):
    from invoicing.workers.tasks import _parse_invoice

    inv = _make_invoice(db, storage)
    inv.file_type = "PDF"
    inv.file_url = "test-worker/plain.pdf"
    storage.put(inv.file_url, b"%PDF-1.4 no attachments", "application/pdf")
    _parse_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_review"
    assert inv.parse_source == "PDF_UNSTRUCTURED"
```

- [ ] **Step 5: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_workers.py -v`
Expected: FAIL（invoicing.workers.tasks 不存在）

- [ ] **Step 6: 实现 workers/queue.py 与 workers/tasks.py**

```python
# workers/queue.py
import asyncio
import logging

from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import WorkerSettings

from invoicing.config import settings

logger = logging.getLogger(__name__)

redis_settings = RedisSettings.from_dsn(settings.redis_url)


async def enqueue_parse(invoice_id: int) -> None:
    pool = await create_pool(redis_settings)
    try:
        await pool.enqueue_job("parse_invoice_task", invoice_id, _job_id=f"parse:{invoice_id}")
    finally:
        await pool.aclose()


async def enqueue_verify(invoice_id: int) -> None:
    pool = await create_pool(redis_settings)
    try:
        await pool.enqueue_job("verify_invoice_task", invoice_id, _job_id=f"verify:{invoice_id}")
    finally:
        await pool.aclose()


def enqueue_parse_sync(invoice_id: int) -> None:
    try:
        asyncio.run(enqueue_parse(invoice_id))
    except Exception:
        logger.exception("入队解析任务失败 invoice_id=%s", invoice_id)


def enqueue_verify_sync(invoice_id: int) -> None:
    try:
        asyncio.run(enqueue_verify(invoice_id))
    except Exception:
        logger.exception("入队验真任务失败 invoice_id=%s", invoice_id)


class WorkerSettings(WorkerSettings):
    """arq 配置类：`uv run arq invoicing.workers.queue.WorkerSettings` 启动 worker。

    `functions` 用全限定字符串路径，arq 启动时自动导入任务函数；
    `max_tries=3` 覆盖全局默认重试，失败任务重试后仍失败则标记为失败任务。
    """

    functions = [
        "invoicing.workers.tasks.parse_invoice_task",
        "invoicing.workers.tasks.verify_invoice_task",
    ]
    max_tries = 3
    max_jobs = 10
```

```python
# workers/tasks.py
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from invoicing.audit import write_audit
from invoicing.db import SessionLocal
from invoicing.models import Invoice, InvoiceStatus
from invoicing.parse.router import parse_file
from invoicing.storage import get_storage
from invoicing.workflow.state import transition
from invoicing.workers.queue import enqueue_verify_sync

logger = logging.getLogger(__name__)


def _apply_parsed_fields(inv: Invoice, parsed) -> None:
    inv.invoice_code = parsed.invoice_code
    inv.invoice_number = parsed.invoice_number
    inv.issue_date = parsed.issue_date
    inv.amount_without_tax = parsed.amount_without_tax
    inv.tax_amount = parsed.tax_amount
    inv.total_amount = parsed.total_amount
    inv.total_amount_cn = parsed.total_amount_cn
    inv.seller_name = parsed.seller_name
    inv.seller_tax_id = parsed.seller_tax_id
    inv.buyer_name = parsed.buyer_name
    inv.buyer_tax_id = parsed.buyer_tax_id
    inv.invoice_type = parsed.invoice_type
    inv.confidence_score = parsed.confidence_score
    inv.parse_source = parsed.parse_source


def _save_xml_original(storage, inv: Invoice, xml_data: bytes) -> None:
    """合规硬约束：无论原收到格式，XML 原件必须单独存档。"""
    if inv.file_type == "XML":
        inv.xml_url = inv.file_url  # 原件即 XML
    elif xml_data:
        xml_key = f"tenant-default/invoice-{inv.id}/original_invoice.xml"
        storage.put(xml_key, xml_data, "application/xml")
        inv.xml_url = xml_key


def _parse_invoice(invoice_id: int) -> None:
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        if inv is None or inv.status != InvoiceStatus.parsing.value:
            logger.info("跳过解析任务 invoice_id=%s status=%s", invoice_id, inv.status if inv else None)
            return
        storage = get_storage()
        data = storage.get(inv.file_url)
        outcome = parse_file(inv.file_type, data)

        if outcome.parsed is not None and not outcome.errors:
            _apply_parsed_fields(inv, outcome.parsed)
            _save_xml_original(storage, inv, outcome.xml_data)
            transition(inv, InvoiceStatus.parsed.value)
            write_audit(
                db, action="PARSE", invoice_id=inv.id, channel="system",
                detail={"source": outcome.source, "confidence": outcome.parsed.confidence_score},
            )
        else:
            # 结构化数据不可得 → 待复核（Phase 2 OCR 接入后此路径升级）
            inv.parse_source = outcome.source
            inv.confidence_score = 0.0
            inv.validation_errors = [e.model_dump() for e in outcome.errors]
            transition(inv, InvoiceStatus.pending_review.value)
            write_audit(
                db, action="PARSE", invoice_id=inv.id, channel="system",
                detail={"source": outcome.source, "errors": inv.validation_errors},
            )
        db.commit()
        if outcome.parsed is not None and not outcome.errors:
            enqueue_verify_sync(inv.id)
    except IntegrityError:
        # 解析出的「发票代码+号码」与库中已有发票冲突（唯一索引兜底并发）→ 查重拦截
        db.rollback()
        inv = db.get(Invoice, invoice_id)
        if inv is None:
            raise
        from invoicing.verify.dedup import find_duplicate

        existing = find_duplicate(
            db,
            Invoice(
                tenant_id=inv.tenant_id,
                invoice_code=inv.invoice_code,
                invoice_number=inv.invoice_number,
            ),
        )
        if existing is not None:
            inv.duplicate_flag = True
            inv.duplicate_of_id = existing.id
            transition(inv, InvoiceStatus.blocked.value)
            write_audit(
                db, action="PARSE", invoice_id=inv.id, channel="system",
                detail={"result": "duplicate", "duplicate_of_id": existing.id},
            )
            db.commit()
        else:
            raise
    except Exception:
        db.rollback()
        logger.exception("解析任务异常 invoice_id=%s", invoice_id)
        raise
    finally:
        db.close()


async def parse_invoice_task(ctx, invoice_id: int) -> None:
    await asyncio.to_thread(_parse_invoice, invoice_id)


async def verify_invoice_task(ctx, invoice_id: int) -> None:
    # _verify_invoice 在 Task 11 中与本函数同文件定义，运行时解析
    await asyncio.to_thread(_verify_invoice, invoice_id)
```

- [ ] **Step 7: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_router.py ../test/test_workers.py -v`
Expected: 4 PASS（_parse_invoice 直调不依赖 redis/arq；enqueue_verify_sync 失败仅记日志，不影响断言）

- [ ] **Step 8: Commit**

```bash
git add backend/src/invoicing/parse/router.py backend/src/invoicing/parse/schemas.py \
  backend/src/invoicing/workers test/test_router.py test/test_workers.py
git commit -m "feat(backend): 解析分级路由与 parse worker 任务"
```

---

### Task 11: 验真查重与 verify worker 任务

**Files:**
- Create: `backend/src/invoicing/verify/__init__.py`
- Create: `backend/src/invoicing/verify/provider.py`
- Create: `backend/src/invoicing/verify/mock.py`
- Create: `backend/src/invoicing/verify/dedup.py`
- Modify: `backend/src/invoicing/workers/tasks.py`（补 `_verify_invoice`）
- Create: `test/test_mock_provider.py`
- Create: `test/test_dedup.py`
- Modify: `test/test_workers.py`（追加 verify 用例）

**Interfaces:**
- Consumes: `settings`、`Invoice`/`VerifyStatus`、`transition`、`write_audit`（Task 2/3/5）、`_parse_invoice` 落库的字段（Task 10）
- Produces:
  - `VerifyResult(status: str, detail: dict, raw: dict)`（status ∈ `passed / failed / error`）
  - `class VerifyProvider(ABC)`：`verify(invoice: Invoice) -> VerifyResult`；`get_provider() -> VerifyProvider`（当前返回 MockVerifyProvider）
  - `MockVerifyProvider(VerifyProvider)`：按 `settings.mock_verify_rules` JSON 规则（`fail_prefixes` / `error_prefixes` 对 invoice_number 前缀匹配）
  - `find_duplicate(db, invoice: Invoice) -> Invoice | None`（同 tenant + coalesce(code)+number 且 id 不同）
  - `_verify_invoice(invoice_id: int) -> None`（同步核心，测试直调）

- [ ] **Step 1: 写失败测试 test/test_mock_provider.py**

```python
from decimal import Decimal

from invoicing.models import Invoice
from invoicing.verify.mock import MockVerifyProvider
from invoicing.verify.provider import get_provider, VerifyResult


def test_mock_pass_by_default():
    inv = Invoice(invoice_number="24312000000012345678", file_url="a.xml", file_type="XML")
    result = MockVerifyProvider().verify(inv)
    assert isinstance(result, VerifyResult)
    assert result.status == "passed"


def test_mock_fail_prefix():
    inv = Invoice(invoice_number="00001234567890123456", file_url="a.xml", file_type="XML")
    result = MockVerifyProvider().verify(inv)
    assert result.status == "failed"
    assert "reason" in result.detail


def test_mock_error_prefix():
    inv = Invoice(invoice_number="00011234567890123456", file_url="a.xml", file_type="XML")
    result = MockVerifyProvider().verify(inv)
    assert result.status == "error"


def test_get_provider_returns_mock():
    assert isinstance(get_provider(), MockVerifyProvider)
```

- [ ] **Step 2: 写失败测试 test/test_dedup.py**

（注意：查重唯一索引在数据库层面生效，「两行同号并存」不可能出现——因此 find_duplicate 的测试场景是「库中已有一行 + 用未入库的探针对象预检」，以及「唯一索引兜底」。）

```python
import pytest
from sqlalchemy.exc import IntegrityError

from invoicing.models import Invoice
from invoicing.verify.dedup import find_duplicate


def test_find_duplicate_with_detached_probe(db):
    first = Invoice(invoice_number="24312000000012345678", file_url="a.xml", file_type="XML")
    db.add(first)
    db.flush()
    probe = Invoice(invoice_number="24312000000012345678", file_url="b.xml", file_type="XML")
    found = find_duplicate(db, probe)
    assert found is not None
    assert found.id == first.id


def test_find_duplicate_self_excluded(db):
    inv = Invoice(invoice_number="24312000000099998888", file_url="a.xml", file_type="XML")
    db.add(inv)
    db.flush()
    assert find_duplicate(db, inv) is None


def test_dedup_unique_index_backstop(db):
    db.add(Invoice(invoice_number="24312000000077776666", file_url="a.xml", file_type="XML"))
    db.flush()
    db.add(Invoice(invoice_number="24312000000077776666", file_url="b.xml", file_type="XML"))
    with pytest.raises(IntegrityError):
        db.flush()  # 数据库唯一索引兜底并发重复
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_mock_provider.py ../test/test_dedup.py -v`
Expected: FAIL（invoicing.verify 不存在）

- [ ] **Step 4: 实现 verify 包**

```python
# verify/provider.py
from abc import ABC, abstractmethod

from pydantic import BaseModel

from invoicing.models import Invoice


class VerifyResult(BaseModel):
    status: str  # passed / failed / error
    detail: dict
    raw: dict


class VerifyProvider(ABC):
    @abstractmethod
    def verify(self, invoice: Invoice) -> VerifyResult: ...


def get_provider() -> VerifyProvider:
    from invoicing.verify.mock import MockVerifyProvider

    return MockVerifyProvider()
```

```python
# verify/mock.py
import json

from invoicing.config import settings
from invoicing.models import Invoice
from invoicing.verify.provider import VerifyProvider, VerifyResult


class MockVerifyProvider(VerifyProvider):
    """规则引擎模拟验真：发票号码前缀命中 fail_prefixes 判失败、error_prefixes 判异常，否则通过。"""

    def __init__(self) -> None:
        rules = json.loads(settings.mock_verify_rules)
        self.fail_prefixes = rules.get("fail_prefixes", [])
        self.error_prefixes = rules.get("error_prefixes", [])

    def verify(self, invoice: Invoice) -> VerifyResult:
        number = invoice.invoice_number or ""
        for prefix in self.error_prefixes:
            if number.startswith(prefix):
                return VerifyResult(
                    status="error", detail={"reason": "mock_rule_error_prefix"}, raw={}
                )
        for prefix in self.fail_prefixes:
            if number.startswith(prefix):
                return VerifyResult(
                    status="failed", detail={"reason": "mock_rule_fail_prefix"}, raw={}
                )
        return VerifyResult(status="passed", detail={"reason": "mock_rule_default_pass"}, raw={})
```

```python
# verify/dedup.py
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from invoicing.models import Invoice


def find_duplicate(db: Session, invoice: Invoice) -> Invoice | None:
    """按 (tenant_id, coalesce(invoice_code,''), invoice_number) 查重复。

    `invoice` 可以是库中对象（按 id 排除自身），也可以是未入库的探针对象
    （id 为 None 时不能加 id 过滤，否则 `id != NULL` 恒为假查不到任何行）。
    """
    conds = [
        Invoice.tenant_id == invoice.tenant_id,
        func.coalesce(Invoice.invoice_code, "") == func.coalesce(invoice.invoice_code, ""),
        Invoice.invoice_number == invoice.invoice_number,
    ]
    if invoice.id is not None:
        conds.append(Invoice.id != invoice.id)
    stmt = select(Invoice).where(*conds)
    return db.scalars(stmt).first()
```

- [ ] **Step 5: 在 workers/tasks.py 追加 `_verify_invoice`**

```python
# 追加到 workers/tasks.py
from invoicing.models import VerifyStatus
from invoicing.verify.dedup import find_duplicate
from invoicing.verify.provider import get_provider


def _verify_invoice(invoice_id: int) -> None:
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        if inv is None or inv.status != InvoiceStatus.verifying.value:
            logger.info("跳过验真任务 invoice_id=%s status=%s", invoice_id, inv.status if inv else None)
            return

        duplicate = find_duplicate(db, inv)
        if duplicate is not None:
            inv.duplicate_flag = True
            inv.duplicate_of_id = duplicate.id
            transition(inv, InvoiceStatus.blocked.value)
            write_audit(
                db, action="VERIFY", invoice_id=inv.id, channel="system",
                detail={"result": "duplicate", "duplicate_of_id": duplicate.id},
            )
            db.commit()
            return

        result = get_provider().verify(inv)
        inv.verify_detail = {"status": result.status, **result.detail}
        inv.verified_at = datetime.now(timezone.utc)
        if result.status == "passed":
            inv.verify_status = VerifyStatus.passed.value
            transition(inv, InvoiceStatus.pending_submit.value)
        else:
            # failed 与 error（服务异常）都不放行，进人工复核
            inv.verify_status = VerifyStatus.failed.value
            transition(inv, InvoiceStatus.pending_review.value)
        write_audit(
            db, action="VERIFY", invoice_id=inv.id, channel="system",
            detail={"result": result.status, "provider": "mock"},
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("验真任务异常 invoice_id=%s", invoice_id)
        raise
    finally:
        db.close()
```

- [ ] **Step 6: 在 test/test_workers.py 追加 verify 用例**

```python
def test_verify_invoice_task_pass(db, storage):
    from invoicing.workers.tasks import _parse_invoice, _verify_invoice

    inv = _make_invoice(db, storage)
    _parse_invoice(inv.id)
    db.refresh(inv)
    _verify_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_submit"
    assert inv.verify_status == "passed"


def test_verify_invoice_task_fail_goes_review(db, storage):
    from invoicing.workers.tasks import _parse_invoice, _verify_invoice

    inv = _make_invoice(db, storage)
    _parse_invoice(inv.id)
    db.refresh(inv)
    # 解析会覆盖号码，因此必须在解析之后再改为 mock 规则失败前缀
    inv.invoice_number = "00001234567890123456"
    db.flush()
    _verify_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_review"
    assert inv.verify_status == "failed"


def test_verify_invoice_task_duplicate_blocks(db, storage, monkeypatch):
    from invoicing.workers.tasks import _parse_invoice, _verify_invoice

    inv = _make_invoice(db, storage)
    _parse_invoice(inv.id)
    db.refresh(inv)
    other = Invoice(file_url="x.xml", file_type="XML", invoice_number="24312000000000000001")
    db.add(other)
    db.flush()
    monkeypatch.setattr("invoicing.workers.tasks.find_duplicate", lambda d, i: other)
    _verify_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "blocked"
    assert inv.duplicate_flag is True
    assert inv.duplicate_of_id == other.id
```

（说明：真实库中两行同号不可能并存，verify 阶段的查重分支用 monkeypatch 模拟命中；查重真实拦截发生在 Task 10 的 parse 冲突处理与 Task 12 的重复邮件处理，均有独立测试。）

- [ ] **Step 7: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_mock_provider.py ../test/test_dedup.py ../test/test_workers.py -v`
Expected: 全部 PASS（mock 4 + dedup 3 + workers 5：Task 10 的 2 个 parse 用例 + 本任务 3 个 verify 用例）

- [ ] **Step 8: Commit**

```bash
git add backend/src/invoicing/verify backend/src/invoicing/workers/tasks.py \
  test/test_mock_provider.py test/test_dedup.py test/test_workers.py
git commit -m "feat(backend): 验真提供方抽象（Mock）与查重、verify worker 任务"
```

---

### Task 12: 邮件收取模块（协议 + IMAP + 过滤 + 拒收回复 + 编排）

**Files:**
- Create: `backend/src/invoicing/fetch/__init__.py`
- Create: `backend/src/invoicing/fetch/protocol.py`
- Create: `backend/src/invoicing/fetch/filters.py`
- Create: `backend/src/invoicing/fetch/reply.py`
- Create: `backend/src/invoicing/fetch/imap.py`
- Create: `backend/src/invoicing/fetch/service.py`
- Create: `test/test_fetch.py`

**Interfaces:**
- Consumes: `Mailbox`/`Invoice`/`FileType`、`write_audit`、`transition`、`storage`、`enqueue_parse_sync`、Fernet（settings.fernet_key）
- Produces:
  - `RawAttachment(filename: str, content_type: str, data: bytes)`
  - `RawMailMessage(uid: int, message_id: str | None, subject: str, sender: str, attachments: list[RawAttachment])`
  - `class MailFetcher(ABC)`：`fetch_new(last_uid: int) -> list[RawMailMessage]`、`mark_seen(uids: list[int]) -> None`
  - `ImapMailFetcher(MailFetcher)`：imaplib 实现；构造参数 `mailbox: Mailbox`
  - `classify_attachment(filename: str, content_type: str, data: bytes) -> str`（返回 `PDF/OFD/XML/ZIP/IMAGE/OTHER`）
  - `unpack_zip(data: bytes) -> list[tuple[str, bytes]]`
  - `send_reject_reply(mailbox: Mailbox, to_addr: str, subject: str) -> None`（SMTP 未配置时直接返回）
  - `encrypt_secret(plain: str) -> str` / `decrypt_secret(cipher: str) -> str`（Fernet，放 fetch/service.py 或独立 crypto 模块；放 `invoicing/fetch/crypto.py`）
  - `PollResult(received: int, rejected_images: int, ignored: int, duplicates: int, errors: int)`
  - `poll_mailbox(db, mailbox: Mailbox, fetcher: MailFetcher | None = None) -> PollResult`

- [ ] **Step 1: 写失败测试 test/test_fetch.py（用 FakeFetcher 驱动编排，不依赖真实 IMAP）**

```python
from pathlib import Path

from invoicing.fetch.protocol import MailFetcher, RawAttachment, RawMailMessage
from invoicing.fetch.service import poll_mailbox
from invoicing.models import Invoice, Mailbox

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"
INVOICE_XML = (FIXTURES / "dianzi.xml").read_bytes()


class FakeFetcher(MailFetcher):
    def __init__(self, messages):
        self.messages = messages
        self.seen: list[int] = []

    def fetch_new(self, last_uid: int) -> list[RawMailMessage]:
        return [m for m in self.messages if m.uid > last_uid]

    def mark_seen(self, uids: list[int]) -> None:
        self.seen.extend(uids)


def _mailbox(db) -> Mailbox:
    mb = Mailbox(
        name="测试邮箱",
        imap_host="imap.example.com",
        imap_port=993,
        use_ssl=True,
        username="inv@example.com",
        password_encrypted="enc:whatever",
        keywords="发票,Invoice",
    )
    db.add(mb)
    db.flush()
    return mb


def _msg(uid, subject, attachments, sender="user@example.com", message_id=None):
    return RawMailMessage(
        uid=uid,
        message_id=message_id or f"<msg{uid}@example.com>",
        subject=subject,
        sender=sender,
        attachments=attachments,
    )


def test_poll_receives_xml_invoice(db):
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [_msg(1, "发票", [RawAttachment("dianzi.xml", "application/xml", INVOICE_XML)])]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 1
    assert result.rejected_images == 0
    inv = db.query(Invoice).filter(Invoice.email_message_id == "<msg1@example.com>").one()
    assert inv.status == "received"
    assert inv.file_type == "XML"
    assert mb.last_uid == 1
    assert fetcher.seen == [1]


def test_poll_rejects_image_and_replies(db):
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [
            _msg(
                1,
                "发票照片",
                [RawAttachment("photo.jpg", "image/jpeg", b"\xff\xd8\xff")],
            )
        ]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 0
    assert result.rejected_images == 1
    assert db.query(Invoice).count() == 0
    assert mb.last_uid == 1


def test_poll_unpacks_zip(db):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dianzi.xml", INVOICE_XML)
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [_msg(2, "Invoice", [RawAttachment("invoices.zip", "application/zip", buf.getvalue())])]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 1


def test_poll_ignores_irrelevant_subject(db):
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [_msg(3, "周报", [RawAttachment("report.pdf", "application/pdf", b"%PDF-1.4")])]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 0
    assert db.query(Invoice).count() == 0


def test_poll_skips_already_seen_uids(db):
    mb = _mailbox(db)
    mb.last_uid = 5
    fetcher = FakeFetcher([_msg(4, "发票", [RawAttachment("d.xml", "application/xml", INVOICE_XML)])])
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 0


def test_poll_duplicate_email_ignored(db):
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [
            _msg(1, "发票", [RawAttachment("dianzi.xml", "application/xml", INVOICE_XML)]),
            # 同一 message_id 重复到达（异常场景），email_message_id 唯一索引兜底
            _msg(2, "发票", [RawAttachment("dianzi.xml", "application/xml", INVOICE_XML)], message_id="<msg1@example.com>"),
        ]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 1
    assert result.duplicates == 1
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_fetch.py -v`
Expected: FAIL（invoicing.fetch 不存在）

- [ ] **Step 3: 实现 fetch 包**

```python
# fetch/protocol.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RawAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class RawMailMessage:
    uid: int
    message_id: str | None
    subject: str
    sender: str
    attachments: list[RawAttachment] = field(default_factory=list)


class MailFetcher(ABC):
    @abstractmethod
    def fetch_new(self, last_uid: int) -> list[RawMailMessage]: ...

    @abstractmethod
    def mark_seen(self, uids: list[int]) -> None: ...
```

```python
# fetch/crypto.py
from cryptography.fernet import Fernet

from invoicing.config import settings

_fernet = Fernet(settings.fernet_key.encode() if len(settings.fernet_key) == 44 else Fernet.generate_key())


def encrypt_secret(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt_secret(cipher: str) -> str:
    return _fernet.decrypt(cipher.encode()).decode()
```

（注意：默认配置里 `fernet_key` 是占位串，长度非 44 时生成临时密钥——仅开发环境回退；生产部署必须设置合法 Fernet key，`Fernet.generate_key().decode()` 可生成。）

```python
# fetch/filters.py
import io
import zipfile

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic"}


def _ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def classify_attachment(filename: str, content_type: str, data: bytes) -> str:
    ext = _ext(filename)
    if content_type.startswith("image/") or ext in IMAGE_EXTS:
        return "IMAGE"
    if ext == ".ofd" or content_type in ("application/ofd", "application/vnd.ofd"):
        return "OFD"
    if ext == ".zip" or content_type in ("application/zip", "application/x-zip-compressed"):
        return "ZIP"
    if ext == ".xml" or content_type in ("application/xml", "text/xml") or data.lstrip().startswith(b"<?xml"):
        return "XML"
    if ext == ".pdf" or content_type == "application/pdf" or data.startswith(b"%PDF"):
        return "PDF"
    return "OTHER"


def unpack_zip(data: bytes) -> list[tuple[str, bytes]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return []
    out = []
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        try:
            out.append((name.split("/")[-1], zf.read(name)))
        except Exception:
            continue
    return out
```

```python
# fetch/reply.py
import logging
import smtplib
from email.mime.text import MIMEText

from invoicing.fetch.crypto import decrypt_secret
from invoicing.models import Mailbox

logger = logging.getLogger(__name__)

REJECT_TEMPLATE = (
    "您好：\n\n"
    "我们检测到您发送的发票附件为非标准格式（图片）。\n"
    "为符合电子发票归档要求，请重新发送该发票的 OFD/PDF/XML 源文件。\n\n"
    "——发票易自动处理系统"
)


def send_reject_reply(mailbox: Mailbox, to_addr: str, subject: str) -> None:
    if not mailbox.smtp_host or not to_addr:
        logger.info("SMTP 未配置或收件人为空，跳过拒收回复 mailbox_id=%s", mailbox.id)
        return
    msg = MIMEText(REJECT_TEMPLATE, "plain", "utf-8")
    msg["Subject"] = f"Re: {subject}"
    msg["From"] = mailbox.smtp_username or mailbox.username
    msg["To"] = to_addr
    if mailbox.use_ssl or mailbox.smtp_port == 465:
        server = smtplib.SMTP_SSL(mailbox.smtp_host, mailbox.smtp_port or 465, timeout=30)
    else:
        server = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port or 25, timeout=30)
    try:
        password = decrypt_secret(mailbox.smtp_password_encrypted) if mailbox.smtp_password_encrypted else ""
        if password:
            server.login(mailbox.smtp_username or mailbox.username, password)
        server.sendmail(msg["From"], [to_addr], msg.as_string())
    finally:
        server.quit()
```

```python
# fetch/imap.py
import email as email_lib
import imaplib

from invoicing.fetch.crypto import decrypt_secret
from invoicing.fetch.protocol import MailFetcher, RawAttachment, RawMailMessage
from invoicing.models import Mailbox


class ImapMailFetcher(MailFetcher):
    def __init__(self, mailbox: Mailbox):
        self.mailbox = mailbox

    def _connect(self):
        if self.mailbox.use_ssl:
            conn = imaplib.IMAP4_SSL(self.mailbox.imap_host, self.mailbox.imap_port)
        else:
            conn = imaplib.IMAP4(self.mailbox.imap_host, self.mailbox.imap_port)
        conn.login(self.mailbox.username, decrypt_secret(self.mailbox.password_encrypted))
        conn.select(self.mailbox.folder)
        return conn

    def fetch_new(self, last_uid: int) -> list[RawMailMessage]:
        conn = self._connect()
        try:
            typ, data = conn.uid("search", None, f"UID {last_uid + 1}:*")
            if typ != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()
            messages = []
            for uid in uids:
                typ, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[] RFC822.SIZE)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                messages.append(self._parse_message(int(uid), raw))
            return messages
        finally:
            conn.logout()

    def _parse_message(self, uid: int, raw: bytes) -> RawMailMessage:
        msg = email_lib.message_from_bytes(raw)
        attachments = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            filename = part.get_filename() or ""
            content_type = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload:
                attachments.append(RawAttachment(filename, content_type, payload))
        return RawMailMessage(
            uid=uid,
            message_id=msg.get("Message-ID"),
            subject=msg.get("Subject") or "",
            sender=msg.get("From") or "",
            attachments=attachments,
        )

    def mark_seen(self, uids: list[int]) -> None:
        if not uids:
            return
        conn = self._connect()
        try:
            for uid in uids:
                conn.uid("store", str(uid), "+FLAGS", "\\Seen")
        finally:
            conn.logout()
```

```python
# fetch/service.py
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.fetch.filters import classify_attachment, unpack_zip
from invoicing.fetch.imap import ImapMailFetcher
from invoicing.fetch.protocol import MailFetcher, RawAttachment, RawMailMessage
from invoicing.fetch.reply import send_reject_reply
from invoicing.models import Invoice, Mailbox
from invoicing.storage import get_storage
from invoicing.workers.queue import enqueue_parse_sync

logger = logging.getLogger(__name__)


@dataclass
class PollResult:
    received: int = 0
    rejected_images: int = 0
    ignored: int = 0
    duplicates: int = 0
    errors: int = 0


def _subject_matches(mailbox: Mailbox, subject: str) -> bool:
    keywords = [k.strip() for k in mailbox.keywords.split(",") if k.strip()]
    if not keywords:
        return True
    return any(k in subject for k in keywords)


def _store_original(db: Session, storage, mb: Mailbox, msg: RawMailMessage, att: RawAttachment) -> int | None:
    # 说明：收取阶段 invoice_number 尚未解析，查重唯一索引不触发；此处 IntegrityError
    # 只可能是 email_message_id 唯一索引——即同一邮件被重复收取，忽略并留痕。
    key = f"tenant-default/mailbox-{mb.id}/{msg.uid}-{att.filename}"
    storage.put(key, att.data, att.content_type or "application/octet-stream")
    inv = Invoice(
        mailbox_id=mb.id,
        email_message_id=msg.message_id,
        email_subject=msg.subject,
        file_url=key,
        file_type=classify_attachment(att.filename, att.content_type, att.data),
        status="received",
    )
    db.add(inv)
    try:
        with db.begin_nested():
            db.flush()
        return inv.id
    except IntegrityError:
        write_audit(
            db, action="FETCH", channel="system",
            detail={"result": "duplicate_email", "message_id": msg.message_id},
        )
        return None


def _process_attachment(db, storage, mb, msg, att, result: PollResult, to_enqueue: list[int]) -> None:
    kind = classify_attachment(att.filename, att.content_type, att.data)
    if kind in ("PDF", "OFD", "XML"):
        inv_id = _store_original(db, storage, mb, msg, att)
        if inv_id is not None:
            result.received += 1
            to_enqueue.append(inv_id)
        else:
            result.duplicates += 1
    elif kind == "ZIP":
        inner = unpack_zip(att.data)
        has_invoice = False
        for filename, data in inner:
            inner_att = RawAttachment(filename, "", data)
            if classify_attachment(filename, "", data) in ("PDF", "OFD", "XML"):
                has_invoice = True
                inv_id = _store_original(db, storage, mb, msg, inner_att)
                if inv_id is not None:
                    result.received += 1
                    to_enqueue.append(inv_id)
                else:
                    result.duplicates += 1
        if not has_invoice:
            result.ignored += 1
            write_audit(db, action="FETCH", channel="system",
                        detail={"result": "zip_no_invoice", "message_id": msg.message_id})
    elif kind == "IMAGE":
        result.rejected_images += 1
        write_audit(db, action="REJECT_REPLY", channel="system",
                    detail={"message_id": msg.message_id, "filename": att.filename, "reason": "image_not_accepted"})
        try:
            send_reject_reply(mb, msg.sender, msg.subject)
        except Exception:
            logger.exception("拒收回复发送失败 message_id=%s", msg.message_id)
            result.errors += 1
    else:
        result.ignored += 1
        write_audit(db, action="FETCH", channel="system",
                    detail={"result": "ignored_attachment", "message_id": msg.message_id,
                            "filename": att.filename})


def poll_mailbox(db: Session, mailbox: Mailbox, fetcher: MailFetcher | None = None) -> PollResult:
    fetcher = fetcher or ImapMailFetcher(mailbox)
    result = PollResult()
    to_enqueue: list[int] = []
    try:
        messages = fetcher.fetch_new(mailbox.last_uid)
        for msg in messages:
            if not _subject_matches(mailbox, msg.subject):
                result.ignored += 1
                continue
            for att in msg.attachments:
                _process_attachment(db, get_storage(), mailbox, msg, att, result, to_enqueue)
        mailbox.last_polled_at = datetime.now(timezone.utc)
        if messages:
            mailbox.last_uid = max(m.uid for m in messages)
        db.commit()
        try:
            fetcher.mark_seen([m.uid for m in messages])
        except Exception:
            logger.exception("IMAP 标记已读失败 mailbox_id=%s", mailbox.id)
        write_audit(db, action="FETCH", channel="system",
                    detail={"mailbox_id": mailbox.id, "result": result.__dict__})
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("收取失败 mailbox_id=%s", mailbox.id)
        result.errors += 1
        write_audit(db, action="FETCH", channel="system",
                    detail={"mailbox_id": mailbox.id, "result": "error"})
        db.commit()
    for inv_id in to_enqueue:
        enqueue_parse_sync(inv_id)
    return result
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_fetch.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/invoicing/fetch test/test_fetch.py
git commit -m "feat(backend): 邮件收取模块（IMAP/过滤/ZIP/拒收回复/编排）"
```

---

### Task 13: REST API —— 认证与用户管理

**Files:**
- Create: `backend/src/invoicing/api/__init__.py`
- Create: `backend/src/invoicing/api/deps.py`
- Create: `backend/src/invoicing/api/auth.py`
- Create: `backend/src/invoicing/api/users.py`
- Create: `backend/src/invoicing/schemas/__init__.py`（空文件）
- Create: `backend/src/invoicing/schemas/auth.py`
- Create: `backend/src/invoicing/schemas/user.py`
- Create: `backend/src/invoicing/bootstrap.py`
- Modify: `backend/src/invoicing/main.py`（注册路由 + lifespan 启动 bootstrap）
- Create: `test/test_api_auth.py`
- Create: `test/test_api_users.py`

**Interfaces:**
- Consumes: `security`、`permissions`、`User`/`Role`、`write_audit`、`get_db`（Task 1/2/4/5）
- Produces:
  - `api_router`（`APIRouter(prefix="/api/v1")`，聚合 auth/users/invoices/mailboxes/stats/audit 子路由）
  - Pydantic schema：`LoginRequest(username, password)`、`TokenResponse(access_token, token_type, user: UserOut)`、`UserOut(id, username, role, created_at)`、`UserCreate(username, password, role)`、`UserUpdate(password: str | None, role: str | None)`
  - `ensure_admin_user(db)`（users 为空时按 settings 创建 admin）
  - `create_app()` 的 lifespan：`with SessionLocal() as db: ensure_admin_user(db)`

- [ ] **Step 1: 写失败测试 test/test_api_auth.py**

```python
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
```

- [ ] **Step 2: 写失败测试 test/test_api_users.py**

```python
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


def _login(client, username, password="pass123"):
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return resp.json()["access_token"]


def _seed(db, username, role, password="pass123"):
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_create_user_admin_only(client, db):
    admin = _seed(db, "root", Role.admin.value)
    token = _login(client, "root")
    resp = client.post(
        "/api/v1/users",
        json={"username": "caiwu1", "password": "pass123", "role": "finance_staff"},
        headers=_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "finance_staff"


def test_create_user_forbidden_for_finance(client, db):
    staff = _seed(db, "caiwu0", Role.finance_staff.value)
    token = _login(client, "caiwu0")
    resp = client.post(
        "/api/v1/users",
        json={"username": "x", "password": "pass123", "role": "finance_staff"},
        headers=_headers(token),
    )
    assert resp.status_code == 403


def test_list_users(client, db):
    _seed(db, "root", Role.admin.value)
    token = _login(client, "root")
    resp = client.get("/api/v1/users", headers=_headers(token))
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_api_auth.py ../test/test_api_users.py -v`
Expected: FAIL（invoicing.api 不存在）

- [ ] **Step 4: 实现 schemas、bootstrap、api 包**

```python
# schemas/auth.py
from pydantic import BaseModel

from invoicing.schemas.user import UserOut


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
```

```python
# schemas/user.py
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    created_at: datetime


class UserCreate(BaseModel):
    username: str
    password: str
    role: str


class UserUpdate(BaseModel):
    password: str | None = None
    role: str | None = None
```

```python
# bootstrap.py
from sqlalchemy.orm import Session

from invoicing.config import settings
from invoicing.models import Role, User
from invoicing.security import hash_password


def ensure_admin_user(db: Session) -> None:
    if db.query(User).count() > 0:
        return
    db.add(
        User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role=Role.admin.value,
        )
    )
    db.commit()
```

```python
# api/deps.py
from invoicing.security import get_current_user, require_role  # noqa: F401  统一出口
```

```python
# api/auth.py
from fastapi import APIRouter, Depends, HTTPException, Request, status

from invoicing.audit import write_audit
from invoicing.db import get_db
from invoicing.models import User
from invoicing.schemas.auth import LoginRequest, TokenResponse
from invoicing.security import create_access_token, get_current_user, verify_password
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    write_audit(
        db, action="LOGIN", user_id=user.id, channel="web",
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return TokenResponse(access_token=create_access_token(user), user=user)


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    write_audit(db, action="LOGOUT", user_id=user.id, channel="web")
    db.commit()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user
```

```python
# api/users.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import User
from invoicing.schemas.user import UserCreate, UserOut, UserUpdate
from invoicing.security import hash_password, require_role

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return db.query(User).order_by(User.id).all()


@router.post("", response_model=UserOut)
def create_user(body: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(409, "用户名已存在")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    db.commit()
    return user


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.role:
        user.role = body.role
    db.commit()
    return user
```

- [ ] **Step 5: 修改 main.py：注册路由 + lifespan**

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from invoicing.api import api_router
from invoicing.bootstrap import ensure_admin_user
from invoicing.db import SessionLocal


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as db:
        ensure_admin_user(db)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="发票易 InvoiceEase", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "invoicing", "version": "0.1.0"}

    app.include_router(api_router)
    return app


app = create_app()
```

```python
# api/__init__.py
from fastapi import APIRouter

from invoicing.api import auth, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_api_auth.py ../test/test_api_users.py -v`
Expected: 6 PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/invoicing/api backend/src/invoicing/schemas \
  backend/src/invoicing/bootstrap.py backend/src/invoicing/main.py \
  test/test_api_auth.py test/test_api_users.py
git commit -m "feat(backend): REST API 认证与用户管理"
```

---

### Task 14: REST API —— 发票列表/详情/复核/重验与 service 层

**Files:**
- Create: `backend/src/invoicing/workflow/services.py`
- Create: `backend/src/invoicing/schemas/invoice.py`
- Create: `backend/src/invoicing/api/invoices.py`
- Modify: `backend/src/invoicing/api/__init__.py`（挂 invoices 路由）
- Create: `test/test_api_invoices.py`

**Interfaces:**
- Consumes: `Invoice`/`Role`/`InvoiceStatus`、`transition`、`write_audit`、`get_current_user`/`require_role`、`storage.get_storage`、`enqueue_verify_sync`、`settings.mcp_token`（Plan B 用，暂不消费）
- Produces:
  - `InvoiceOut`（pydantic，含全部展示字段）、`InvoiceListResponse(items, total, page, page_size)`、`ReviewRequest(action: Literal["approve","reject"], note: str | None)`
  - `list_invoices(db, current_user, status=None, date_from=None, date_to=None, keyword=None, page=1, page_size=20) -> InvoiceListResponse`
  - `get_invoice(db, current_user, invoice_id) -> Invoice`（不存在或无权限抛 404）
  - `review_invoice(db, current_user, invoice_id, action, note) -> Invoice`
  - `re_verify_invoice(db, current_user, invoice_id) -> Invoice`

- [ ] **Step 1: 写失败测试 test/test_api_invoices.py**

```python
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import Invoice, Role, User
from invoicing.security import hash_password


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def _seed(db, username, role, password="pass123"):
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def _login(client, username):
    return client.post("/api/v1/auth/login", json={"username": username, "password": "pass123"}).json()[
        "access_token"
    ]


def _invoice(db, **kw):
    inv = Invoice(
        file_url="a.xml",
        file_type="XML",
        invoice_number=kw.get("invoice_number", "24312000000012345678"),
        status=kw.get("status", "pending_submit"),
        total_amount=kw.get("total_amount", Decimal("1000.00")),
        seller_name=kw.get("seller_name", "示例科技有限公司"),
        issue_date=kw.get("issue_date", date(2026, 8, 1)),
        parse_source="XML",
        confidence_score=1.0,
        verify_status="passed",
        email_message_id=kw.get("email_message_id"),
    )
    db.add(inv)
    db.flush()
    return inv


def test_list_invoices_finance_sees_all(client, db):
    _seed(db, "caiwu1", Role.finance_staff.value)
    _invoice(db)
    token = _login(client, "caiwu1")
    resp = client.get("/api/v1/invoices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_list_invoices_employee_sees_only_own(client, db):
    emp = _seed(db, "zhangsan", Role.employee.value)
    _invoice(db)  # user_id 为空，员工不可见
    own = _invoice(db, email_message_id="<own@x.com>")
    own.user_id = emp.id
    db.flush()
    token = _login(client, "zhangsan")
    resp = client.get("/api/v1/invoices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    items = resp.json()["items"]
    assert items[0]["id"] == own.id


def test_get_invoice_detail(client, db):
    _seed(db, "caiwu1", Role.finance_staff.value)
    inv = _invoice(db)
    token = _login(client, "caiwu1")
    resp = client.get(f"/api/v1/invoices/{inv.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["invoice_number"] == "24312000000012345678"


def test_review_approve(client, db):
    _seed(db, "caiwu1", Role.finance_staff.value)
    inv = _invoice(db, status="pending_review")
    token = _login(client, "caiwu1")
    resp = client.post(
        f"/api/v1/invoices/{inv.id}/review",
        json={"action": "approve", "note": "核对无误"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_submit"
    assert resp.json()["review_note"] == "核对无误"


def test_review_reject(client, db):
    _seed(db, "caiwu1", Role.finance_staff.value)
    inv = _invoice(db, status="pending_review")
    token = _login(client, "caiwu1")
    resp = client.post(
        f"/api/v1/invoices/{inv.id}/review",
        json={"action": "reject", "note": "信息不符"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_review_forbidden_for_employee(client, db):
    _seed(db, "zhangsan", Role.employee.value)
    inv = _invoice(db, status="pending_review")
    token = _login(client, "zhangsan")
    resp = client.post(
        f"/api/v1/invoices/{inv.id}/review",
        json={"action": "approve", "note": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_api_invoices.py -v`
Expected: FAIL（invoices 路由不存在）

- [ ] **Step 3: 实现 schemas/invoice.py、workflow/services.py、api/invoices.py**

```python
# schemas/invoice.py
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    user_id: int | None
    mailbox_id: int | None
    email_subject: str | None
    invoice_code: str | None
    invoice_number: str | None
    issue_date: date | None
    amount_without_tax: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    total_amount_cn: str | None
    seller_name: str | None
    seller_tax_id: str | None
    buyer_name: str | None
    buyer_tax_id: str | None
    invoice_type: str | None
    file_type: str
    parse_source: str | None
    confidence_score: float | None
    validation_errors: dict | None
    verify_status: str
    verify_detail: dict | None
    verified_at: datetime | None
    duplicate_flag: bool
    duplicate_of_id: int | None
    status: str
    review_note: str | None
    reviewed_by: int | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InvoiceListResponse(BaseModel):
    items: list[InvoiceOut]
    total: int
    page: int
    page_size: int


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None
```

```python
# workflow/services.py
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.models import Invoice, InvoiceStatus, Role, User
from invoicing.schemas.invoice import InvoiceListResponse
from invoicing.workflow.state import transition
from invoicing.workers.queue import enqueue_verify_sync


def _scope_query(db: Session, current_user: User):
    q = db.query(Invoice)
    if current_user.role == Role.employee.value:
        q = q.filter(Invoice.user_id == current_user.id)
    return q


def list_invoices(
    db: Session,
    current_user: User,
    status: str | None = None,
    date_from=None,
    date_to=None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> InvoiceListResponse:
    q = _scope_query(db, current_user)
    if status:
        q = q.filter(Invoice.status == status)
    if date_from:
        q = q.filter(Invoice.issue_date >= date_from)
    if date_to:
        q = q.filter(Invoice.issue_date <= date_to)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(
            or_(
                Invoice.invoice_number.ilike(like),
                Invoice.seller_name.ilike(like),
                Invoice.buyer_name.ilike(like),
            )
        )
    total = q.count()
    items = q.order_by(Invoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return InvoiceListResponse(items=items, total=total, page=page, page_size=page_size)


def get_invoice(db: Session, current_user: User, invoice_id: int) -> Invoice:
    inv = _scope_query(db, current_user).filter(Invoice.id == invoice_id).first()
    if inv is None:
        from fastapi import HTTPException

        raise HTTPException(404, "发票不存在")
    return inv


def review_invoice(db: Session, current_user: User, invoice_id: int, action: str, note: str | None) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        from fastapi import HTTPException

        raise HTTPException(404, "发票不存在")
    if inv.status != InvoiceStatus.pending_review.value:
        from fastapi import HTTPException

        raise HTTPException(409, "仅待复核状态的发票可复核")
    to_status = InvoiceStatus.pending_submit.value if action == "approve" else InvoiceStatus.rejected.value
    transition(inv, to_status)
    inv.review_note = note
    inv.reviewed_by = current_user.id
    inv.reviewed_at = datetime.now(timezone.utc)
    write_audit(
        db, action="REVIEW", user_id=current_user.id, invoice_id=inv.id, channel="web",
        detail={"action": action, "note": note},
    )
    db.commit()
    return inv


def re_verify_invoice(db: Session, current_user: User, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        from fastapi import HTTPException

        raise HTTPException(404, "发票不存在")
    if inv.status not in (
        InvoiceStatus.parsed.value,
        InvoiceStatus.pending_review.value,
        InvoiceStatus.pending_submit.value,
    ):
        from fastapi import HTTPException

        raise HTTPException(409, "当前状态不可重新验真")
    transition(inv, InvoiceStatus.verifying.value)
    inv.verify_status = "pending"
    write_audit(
        db, action="REVERIFY", user_id=current_user.id, invoice_id=inv.id, channel="web"
    )
    db.commit()
    enqueue_verify_sync(inv.id)
    return inv
```

```python
# api/invoices.py
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import Role, User
from invoicing.schemas.invoice import InvoiceListResponse, InvoiceOut, ReviewRequest
from invoicing.security import get_current_user, require_role
from invoicing.storage import get_storage
from invoicing.workflow import services

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("", response_model=InvoiceListResponse)
def list_invoices(
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return services.list_invoices(db, user, status, date_from, date_to, keyword, page, page_size)


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice_detail(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return services.get_invoice(db, user, invoice_id)


@router.get("/{invoice_id}/file")
def download_file(
    invoice_id: int,
    kind: str = Query("file", pattern="^(file|xml)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    inv = services.get_invoice(db, user, invoice_id)
    key = inv.xml_url if kind == "xml" else inv.file_url
    if kind == "xml" and not key:
        from fastapi import HTTPException

        raise HTTPException(404, "该发票无 XML 原件")
    return RedirectResponse(get_storage().presigned_url(key))


@router.post("/{invoice_id}/review", response_model=InvoiceOut)
def review(
    invoice_id: int,
    body: ReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_staff.value, Role.finance_manager.value, Role.admin.value)),
):
    return services.review_invoice(db, user, invoice_id, body.action, body.note)


@router.post("/{invoice_id}/verify", response_model=InvoiceOut)
def re_verify(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_staff.value, Role.finance_manager.value, Role.admin.value)),
):
    return services.re_verify_invoice(db, user, invoice_id)
```

- [ ] **Step 4: api/__init__.py 挂载 invoices 路由**

```python
from fastapi import APIRouter

from invoicing.api import auth, invoices, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(invoices.router)
api_router.include_router(users.router)
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_api_invoices.py -v`
Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/invoicing/workflow/services.py backend/src/invoicing/schemas/invoice.py \
  backend/src/invoicing/api/invoices.py backend/src/invoicing/api/__init__.py \
  test/test_api_invoices.py
git commit -m "feat(backend): REST API 发票列表/详情/复核/重验"
```

---

### Task 15: REST API —— 邮箱配置/统计/审计 + 定时调度器

**Files:**
- Create: `backend/src/invoicing/schemas/mailbox.py`
- Create: `backend/src/invoicing/schemas/stats.py`
- Create: `backend/src/invoicing/schemas/audit.py`
- Create: `backend/src/invoicing/api/mailboxes.py`
- Create: `backend/src/invoicing/api/stats.py`
- Create: `backend/src/invoicing/api/audit.py`
- Create: `backend/src/invoicing/scheduler.py`
- Modify: `backend/src/invoicing/api/__init__.py`
- Modify: `backend/src/invoicing/main.py`（lifespan 启动调度器）
- Create: `test/test_api_mailboxes.py`
- Create: `test/test_api_stats.py`

**Interfaces:**
- Consumes: `Mailbox`/`AuditLog`/`Invoice`、`poll_mailbox`、`encrypt_secret`、`ImapMailFetcher`、`require_role`（Task 2/4/12）
- Produces:
  - `MailboxOut / MailboxCreate / MailboxUpdate`（schema，密码字段不回显）
  - `POST /api/v1/mailboxes/{id}/test` → `{ok, message}`
  - `POST /api/v1/mailboxes/{id}/poll` → `PollResultOut`
  - `GET /api/v1/stats/overview` → `{pending_review, pending_submit, today_new, month_total}`
  - `GET /api/v1/audit-logs` → `AuditListResponse(items, total, page, page_size)`
  - `scheduler.setup_scheduler(app)`：AsyncIOScheduler 每分钟检查到期邮箱并 `to_thread(poll_mailbox)`

- [ ] **Step 1: 写失败测试 test/test_api_mailboxes.py**

```python
import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import Mailbox, Role, User
from invoicing.security import hash_password


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def _admin_token(client, db):
    db.add(User(username="root", password_hash=hash_password("pass123"), role=Role.admin.value))
    db.flush()
    resp = client.post("/api/v1/auth/login", json={"username": "root", "password": "pass123"})
    return resp.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_create_mailbox_hides_password(client, db):
    token = _admin_token(client, db)
    resp = client.post(
        "/api/v1/mailboxes",
        json={
            "name": "企业邮箱",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "use_ssl": True,
            "username": "inv@example.com",
            "password": "secret123",
            "keywords": "发票,Invoice",
        },
        headers=_h(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "password" not in body


def test_mailbox_crud_admin_only(client, db):
    token = _admin_token(client, db)
    assert client.get("/api/v1/mailboxes", headers=_h(token)).status_code == 200
    db.add(User(username="caiwu", password_hash=hash_password("pass123"), role=Role.finance_staff.value))
    db.flush()
    resp = client.post("/api/v1/auth/login", json={"username": "caiwu", "password": "pass123"})
    staff_token = resp.json()["access_token"]
    assert client.get("/api/v1/mailboxes", headers=_h(staff_token)).status_code == 403
```

- [ ] **Step 2: 写失败测试 test/test_api_stats.py**

```python
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import Invoice, Role, User
from invoicing.security import hash_password


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def _login(client, db, username="caiwu", role=Role.finance_staff.value):
    db.add(User(username=username, password_hash=hash_password("pass123"), role=role))
    db.flush()
    return client.post("/api/v1/auth/login", json={"username": username, "password": "pass123"}).json()[
        "access_token"
    ]


def test_stats_overview(client, db):
    token = _login(client, db)
    db.add(Invoice(file_url="a.xml", file_type="XML", status="pending_review"))
    db.add(Invoice(file_url="b.xml", file_type="XML", status="pending_submit"))
    db.add(Invoice(file_url="c.xml", file_type="XML", status="blocked"))
    db.flush()
    resp = client.get("/api/v1/stats/overview", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_review"] == 1
    assert body["pending_submit"] == 1
    assert body["today_new"] == 3
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_api_mailboxes.py ../test/test_api_stats.py -v`
Expected: FAIL（路由不存在）

- [ ] **Step 4: 实现 schemas 与 api**

```python
# schemas/mailbox.py
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MailboxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    imap_host: str
    imap_port: int
    use_ssl: bool
    username: str
    folder: str
    keywords: str
    poll_interval_seconds: int
    smtp_host: str | None
    smtp_port: int | None
    smtp_username: str | None
    enabled: bool
    last_polled_at: datetime | None
    last_uid: int
    created_at: datetime
    updated_at: datetime


class MailboxCreate(BaseModel):
    name: str
    imap_host: str
    imap_port: int = 993
    use_ssl: bool = True
    username: str
    password: str
    folder: str = "INBOX"
    keywords: str = "发票,Invoice"
    poll_interval_seconds: int = 300
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None


class MailboxUpdate(BaseModel):
    name: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    use_ssl: bool | None = None
    username: str | None = None
    password: str | None = None
    folder: str | None = None
    keywords: str | None = None
    poll_interval_seconds: int | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    enabled: bool | None = None


class PollResultOut(BaseModel):
    received: int
    rejected_images: int
    ignored: int
    duplicates: int
    errors: int
```

```python
# schemas/stats.py
from pydantic import BaseModel


class StatsOverviewOut(BaseModel):
    pending_review: int
    pending_submit: int
    today_new: int
    month_total: int
```

```python
# schemas/audit.py
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    action: str
    invoice_id: int | None
    detail: dict | None
    ip_address: str | None
    channel: str
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditOut]
    total: int
    page: int
    page_size: int
```

```python
# api/mailboxes.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.fetch.crypto import encrypt_secret
from invoicing.fetch.service import PollResult, poll_mailbox
from invoicing.models import Mailbox, User
from invoicing.schemas.mailbox import MailboxCreate, MailboxOut, MailboxUpdate, PollResultOut
from invoicing.security import require_role

router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])


@router.get("", response_model=list[MailboxOut])
def list_mailboxes(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return db.query(Mailbox).order_by(Mailbox.id).all()


@router.post("", response_model=MailboxOut)
def create_mailbox(body: MailboxCreate, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    mb = Mailbox(
        name=body.name,
        imap_host=body.imap_host,
        imap_port=body.imap_port,
        use_ssl=body.use_ssl,
        username=body.username,
        password_encrypted=encrypt_secret(body.password),
        folder=body.folder,
        keywords=body.keywords,
        poll_interval_seconds=body.poll_interval_seconds,
        smtp_host=body.smtp_host,
        smtp_port=body.smtp_port,
        smtp_username=body.smtp_username,
        smtp_password_encrypted=encrypt_secret(body.smtp_password) if body.smtp_password else None,
    )
    db.add(mb)
    db.commit()
    return mb


@router.put("/{mailbox_id}", response_model=MailboxOut)
def update_mailbox(
    mailbox_id: int,
    body: MailboxUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    mb = db.get(Mailbox, mailbox_id)
    if mb is None:
        raise HTTPException(404, "邮箱配置不存在")
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "password" and value:
            mb.password_encrypted = encrypt_secret(value)
        elif field == "smtp_password" and value:
            mb.smtp_password_encrypted = encrypt_secret(value)
        else:
            setattr(mb, field, value)
    db.commit()
    return mb


@router.post("/{mailbox_id}/test")
def test_connection(mailbox_id: int, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    from invoicing.fetch.imap import ImapMailFetcher

    mb = db.get(Mailbox, mailbox_id)
    if mb is None:
        raise HTTPException(404, "邮箱配置不存在")
    try:
        fetcher = ImapMailFetcher(mb)
        conn = fetcher._connect()
        conn.logout()
        return {"ok": True, "message": "IMAP 连接成功"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/{mailbox_id}/poll", response_model=PollResultOut)
def trigger_poll(mailbox_id: int, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    mb = db.get(Mailbox, mailbox_id)
    if mb is None:
        raise HTTPException(404, "邮箱配置不存在")
    result = poll_mailbox(db, mb)
    return result
```

```python
# api/stats.py
from datetime import date, datetime, time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import Invoice, User
from invoicing.schemas.stats import StatsOverviewOut
from invoicing.security import get_current_user

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview", response_model=StatsOverviewOut)
def overview(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    today_start = datetime.combine(date.today(), time.min)
    month_start = datetime.combine(date.today().replace(day=1), time.min)
    return StatsOverviewOut(
        pending_review=db.query(Invoice).filter(Invoice.status == "pending_review").count(),
        pending_submit=db.query(Invoice).filter(Invoice.status == "pending_submit").count(),
        today_new=db.query(Invoice).filter(Invoice.created_at >= today_start).count(),
        month_total=db.query(Invoice).filter(Invoice.created_at >= month_start).count(),
    )
```

```python
# api/audit.py
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import AuditLog, User
from invoicing.schemas.audit import AuditListResponse
from invoicing.security import require_role

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=AuditListResponse)
def list_audit_logs(
    user_id: int | None = None,
    action: str | None = None,
    invoice_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    q = db.query(AuditLog)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if invoice_id:
        q = q.filter(AuditLog.invoice_id == invoice_id)
    if date_from:
        q = q.filter(AuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(AuditLog.created_at <= date_to)
    total = q.count()
    items = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return AuditListResponse(items=items, total=total, page=page, page_size=page_size)
```

- [ ] **Step 5: 实现 scheduler.py 并接入 lifespan**

```python
# scheduler.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from invoicing.config import settings
from invoicing.db import SessionLocal
from invoicing.fetch.service import poll_mailbox
from invoicing.models import Mailbox

logger = logging.getLogger(__name__)


def _due_mailboxes() -> list[int]:
    with SessionLocal() as db:
        mailboxes = db.query(Mailbox).filter(Mailbox.enabled.is_(True)).all()
        now = datetime.now(timezone.utc)
        due = [
            mb.id
            for mb in mailboxes
            if mb.last_polled_at is None
            or now - mb.last_polled_at >= timedelta(seconds=mb.poll_interval_seconds)
        ]
        return due


def _poll_due_mailboxes() -> None:
    for mailbox_id in _due_mailboxes():
        with SessionLocal() as db:
            mb = db.get(Mailbox, mailbox_id)
            if mb is None:
                continue
            try:
                poll_mailbox(db, mb)
            except Exception:
                logger.exception("定时收取失败 mailbox_id=%s", mailbox_id)


async def _scheduled_poll() -> None:
    await asyncio.to_thread(_poll_due_mailboxes)


def setup_scheduler(app: FastAPI) -> None:
    if not settings.scheduler_enabled:
        return
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_scheduled_poll, "interval", seconds=60, id="mailbox_poll")
    scheduler.start()
    app.state.scheduler = scheduler
```

main.py 的 lifespan 追加（在 `ensure_admin_user` 之后）：

```python
from invoicing import scheduler as scheduler_mod

@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as db:
        ensure_admin_user(db)
    scheduler_mod.setup_scheduler(app)
    yield
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        sched.shutdown(wait=False)
```

（`test/` 环境 `INVOICING_SCHEDULER_ENABLED=false`，测试不受调度器影响。）

api/__init__.py 更新：

```python
from fastapi import APIRouter

from invoicing.api import audit, auth, invoices, mailboxes, stats, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(invoices.router)
api_router.include_router(mailboxes.router)
api_router.include_router(stats.router)
api_router.include_router(audit.router)
api_router.include_router(users.router)
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_api_mailboxes.py ../test/test_api_stats.py -v`
Expected: 4 PASS

- [ ] **Step 7: 回归全部测试**

Run: `cd backend && uv run pytest ../test -v`
Expected: 全部 PASS（约 45 个用例）

- [ ] **Step 8: Commit**

```bash
git add backend/src/invoicing/api backend/src/invoicing/schemas \
  backend/src/invoicing/scheduler.py backend/src/invoicing/main.py \
  test/test_api_mailboxes.py test/test_api_stats.py
git commit -m "feat(backend): 邮箱配置/统计/审计 API 与定时调度器"
```

---

### Task 16: 核心链路端到端测试与开发文档

**Files:**
- Create: `test/test_e2e.py`
- Create: `docs/开发环境指南.md`
- Modify: `CLAUDE.md`（把实际可用的命令补充到「构建与测试」一节）

**Interfaces:**
- Consumes: 全部既有模块
- Produces: 端到端验收测试 + 开发指南

- [ ] **Step 1: 写失败测试 test/test_e2e.py**

```python
"""核心链路验收：收取 → 解析 → 验真 → 列表可见。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.fetch.protocol import MailFetcher, RawAttachment, RawMailMessage
from invoicing.fetch.service import poll_mailbox
from invoicing.main import create_app
from invoicing.models import Invoice, Mailbox, Role, User
from invoicing.security import hash_password
from invoicing.workers.tasks import _parse_invoice, _verify_invoice

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


class FakeFetcher(MailFetcher):
    def __init__(self, messages):
        self.messages = messages

    def fetch_new(self, last_uid):
        return [m for m in self.messages if m.uid > last_uid]

    def mark_seen(self, uids):
        pass


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def test_full_pipeline_xml_invoice(db, client):
    # 1. 管理员与邮箱配置
    db.add(User(username="root", password_hash=hash_password("pass123"), role=Role.admin.value))
    db.flush()
    mb = Mailbox(
        name="e2e", imap_host="x", imap_port=993, use_ssl=True,
        username="inv@example.com", password_encrypted="enc:x", keywords="发票,Invoice",
    )
    db.add(mb)
    db.flush()

    # 2. 模拟邮件收取（XML 发票 + 一张图片 + 无关附件）
    xml = (FIXTURES / "dianzi.xml").read_bytes()
    fetcher = FakeFetcher(
        [
            RawMailMessage(
                uid=1,
                message_id="<e2e1@example.com>",
                subject="发票",
                sender="user@example.com",
                attachments=[
                    RawAttachment("dianzi.xml", "application/xml", xml),
                    RawAttachment("photo.jpg", "image/jpeg", b"\xff\xd8\xff"),
                    RawAttachment("note.txt", "text/plain", b"hello"),
                ],
            )
        ]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 1
    assert result.rejected_images == 1

    # 3. 消费解析与验真任务（直调同步核心，等价于 arq worker 执行）
    inv = db.query(Invoice).filter(Invoice.email_message_id == "<e2e1@example.com>").one()
    _parse_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "parsed"
    assert inv.total_amount is not None

    _verify_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_submit"
    assert inv.verify_status == "passed"

    # 4. 财务专员通过 API 看到发票
    db.add(User(username="caiwu", password_hash=hash_password("pass123"), role=Role.finance_staff.value))
    db.flush()
    token = client.post(
        "/api/v1/auth/login", json={"username": "caiwu", "password": "pass123"}
    ).json()["access_token"]
    resp = client.get("/api/v1/invoices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["invoice_number"] == "24312000000012345678"
    assert item["status"] == "pending_submit"

    # 5. 审计日志留痕
    from invoicing.models import AuditLog

    actions = {log.action for log in db.query(AuditLog).all()}
    assert {"FETCH", "PARSE", "VERIFY"} <= actions
```

- [ ] **Step 2: 运行测试，确认失败或通过**

Run: `cd backend && uv run pytest ../test/test_e2e.py -v`
Expected: 通过（所有依赖已就绪；若失败按报错修复，常见问题：`_store_original` 的 object key 重复——每次测试 DB 回滚但 MinIO 对象持久，key 含 uid+filename 因此不受影响）

- [ ] **Step 3: 写 docs/开发环境指南.md**

```markdown
# 发票易开发环境指南

## 前置要求

- Python 3.11+、uv（`pip install uv`）
- Docker（用于 postgres/redis/minio）

## 启动开发依赖

```bash
docker compose -f deploy/dev-compose.yml up -d
```

## 安装与初始化

```bash
cd backend
uv sync
uv run alembic upgrade head
```

## 启动服务

```bash
# REST API（含 MCP 端点 /mcp，Plan B 后启用）
uv run uvicorn invoicing.main:app --host 0.0.0.0 --port 8000 --reload

# 任务 worker（另开终端）
uv run arq invoicing.workers.queue.WorkerSettings
```

首次启动自动创建管理员账号（`INVOICING_ADMIN_USERNAME`/`INVOICING_ADMIN_PASSWORD`，默认 admin/admin123，生产必须改）。

## 运行测试

```bash
cd backend
uv run pytest ../test -v
```

## 常用配置（环境变量，前缀 INVOICING_）

| 变量 | 说明 |
|------|------|
| `INVOICING_DATABASE_URL` | PostgreSQL 连接串 |
| `INVOICING_REDIS_URL` | Redis 连接串 |
| `INVOICING_JWT_SECRET` | JWT 签名密钥（生产必改） |
| `INVOICING_FERNET_KEY` | 邮箱密码加密密钥，用 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 生成 |
| `INVOICING_MOCK_VERIFY_RULES` | Mock 验真规则 JSON |
| `INVOICING_ADMIN_USERNAME/PASSWORD` | 初始管理员 |
```

- [ ] **Step 4: 更新 CLAUDE.md「构建与测试」一节**

把 CLAUDE.md 中「## 构建与测试」一节替换为：

```markdown
## 构建与测试

```bash
docker compose -f deploy/dev-compose.yml up -d   # 开发依赖（postgres/redis/minio）
cd backend && uv sync                             # 安装依赖
cd backend && uv run alembic upgrade head         # 初始化数据库
cd backend && uv run pytest ../test -v            # 运行全部测试
cd backend && uv run uvicorn invoicing.main:app --reload   # 启动 API
cd backend && uv run arq invoicing.workers.queue.WorkerSettings  # 启动任务 worker
```
```

- [ ] **Step 5: 全量回归**

Run: `cd backend && uv run pytest ../test -v`
Expected: 全部 PASS（46 个用例左右）

- [ ] **Step 6: Commit**

```bash
git add test/test_e2e.py docs/开发环境指南.md CLAUDE.md
git commit -m "test(backend): 核心链路端到端测试与开发文档"
```

---

## Plan A 验收清单（全部完成后核对）

- [ ] `uv run pytest ../test -v` 全绿
- [ ] 核心链路「收取→解析→验真→列表可见」自动化测试存在且通过（test/test_e2e.py）
- [ ] 合规项落地：XML 原件 `xml_url` 单独存储（Task 10/12 流程含）、图片拒收+回复（Task 12）、验真失败不自动放行（Task 11）、状态跃迁全部写审计（各任务）
- [ ] 4 角色建齐，员工权限隔离（service 层过滤）有测试
- [ ] `docs/开发环境指南.md` 可用（按文档从零可跑通）
