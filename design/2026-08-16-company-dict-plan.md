# 发票易 常用税号及公司信息实施计划（Plan K）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地常用税号及公司信息：company_infos 表 + REST CRUD（admin）+ MCP 3 工具 + 解析链路自动纠错（OCR 字典）与购买方归属校验（BUYER_MISMATCH 待复核）+ Web UI 系统设置新 tab。

**Architecture:** `parse/company_dict.py` 提供 `enrich_parsed(parsed, db)`（纠错 + 校验，返回追加错误）；在**文本/OCR 来源**（confidence < 1.0）的消费点调用：MCP extract（只读 SessionLocal）与 worker `_parse_invoice`（已有 db）——结构化来源（XML/XBRL）不做纠错（原件数据优先）。

**Tech Stack:** SQLAlchemy + Alembic、difflib（模糊匹配）、既有 MCP/REST/Web 框架。

**Spec:** `design/2026-08-16-company-dict-design.md`（V0.1，已确认含 BUYER_MISMATCH 从严）。

## Global Constraints

- 纠错字典只作用于 confidence < 1.0 来源（PDF_TEXT/OFD_TEXT/PDF_OCR/OFD_OCR/IMAGE_OCR）；XML/XBRL 结构化来源不动
- 纠错规则：税号精确匹配优先（名称不同则以预设为准）；否则 difflib ratio ≥ 0.75 且长度差 ≤ 4 取最高分；精确相等跳过
- BUYER_MISMATCH 从严：预设存在 kind=self 且 buyer_tax_id 不匹配任一 → 追加校验错误（走待复核）；未配置本司 → 跳过
- tax_id 18 位 `[0-9A-Z]{18}`；kind 枚举 self/supplier/other；is_default 仅 kind=self 可用，设默认时清除其他默认
- 中文注释；金额 Decimal；测试在仓库根 `test/`；提交格式 `feat(backend):/feat(mcp):/feat(web): ...`
- 后端回归基线 164 不得回退；SQLite 开发模式无需 docker

---

### Task 1: company_infos 模型/迁移/Schema/API

**Files:**
- Modify: `backend/src/invoicing/models/enums.py`（CompanyKind）
- Create: `backend/src/invoicing/models/company_info.py`
- Modify: `backend/src/invoicing/models/__init__.py`（导出）
- Create: `backend/src/invoicing/schemas/company_info.py`
- Create: `backend/src/invoicing/api/company_infos.py`
- Modify: `backend/src/invoicing/api/__init__.py`（挂载）
- Create: `backend/alembic/versions/<autogen>_company_infos.py`
- Test: `test/test_api_company_infos.py`

**Interfaces:**
- Consumes: `utcnow`（models/fields.py）、`require_role`、`get_db`
- Produces:
  - `CompanyKind(str, Enum)`：`self / supplier / other`
  - `CompanyInfo(id, name, tax_id(unique), kind, is_default, remark, created_at, updated_at)`
  - `CompanyInfoOut / CompanyInfoCreate / CompanyInfoUpdate`
  - REST：`GET/POST/PUT/DELETE /api/v1/company-infos`（admin）；create/update 校验 tax_id 18 位、name 非空、kind 枚举、is_default 联动（设默认清其他默认，kind≠self 时 is_default 强制 False）

- [ ] **Step 1: 写失败测试 test/test_api_company_infos.py**

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_api_company_infos.py -v`
Expected: FAIL（路由不存在）

- [ ] **Step 3: 实现模型与 schema**

enums.py 追加：

```python
class CompanyKind(str, Enum):
    self = "self"
    supplier = "supplier"
    other = "other"
```

models/company_info.py：

```python
"""常用税号及公司信息（OCR 纠错字典 + 购买方归属校验 + 预存管理）。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import utcnow


class CompanyInfo(Base):
    __tablename__ = "company_infos"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    tax_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="other")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    remark: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, onupdate=func.now(), nullable=False
    )
```

models/__init__.py 导出 `CompanyInfo`、`CompanyKind`。

schemas/company_info.py：

```python
"""常用税号及公司信息 schema。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

TAX_ID_PATTERN = r"^[0-9A-Z]{18}$"


class CompanyInfoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    tax_id: str
    kind: str
    is_default: bool
    remark: str | None
    created_at: datetime
    updated_at: datetime


class CompanyInfoCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    tax_id: str = Field(pattern=TAX_ID_PATTERN)
    kind: str = "other"
    is_default: bool = False
    remark: str | None = None


class CompanyInfoUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    tax_id: str | None = Field(default=None, pattern=TAX_ID_PATTERN)
    kind: str | None = None
    is_default: bool | None = None
    remark: str | None = None
```

- [ ] **Step 4: 实现 API**

api/company_infos.py：

```python
"""常用税号及公司信息 CRUD（admin 专属）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import CompanyInfo, CompanyKind, User
from invoicing.schemas.company_info import CompanyInfoCreate, CompanyInfoOut, CompanyInfoUpdate
from invoicing.security import require_role

router = APIRouter(prefix="/company-infos", tags=["company-infos"])

_KINDS = {k.value for k in CompanyKind}


def _validate_kind(kind: str) -> None:
    if kind not in _KINDS:
        raise HTTPException(422, f"非法类型: {kind}（可选 self/supplier/other）")


def _clear_defaults(db: Session) -> None:
    db.query(CompanyInfo).filter(CompanyInfo.is_default.is_(True)).update({"is_default": False})


@router.get("", response_model=list[CompanyInfoOut])
def list_company_infos(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return db.query(CompanyInfo).order_by(CompanyInfo.id).all()


@router.post("", response_model=CompanyInfoOut)
def create_company_info(body: CompanyInfoCreate, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    _validate_kind(body.kind)
    if db.query(CompanyInfo).filter(CompanyInfo.tax_id == body.tax_id).first():
        raise HTTPException(409, "该税号已存在")
    if body.is_default:
        if body.kind != CompanyKind.self.value:
            raise HTTPException(422, "is_default 仅适用于 kind=self")
        _clear_defaults(db)
    info = CompanyInfo(
        name=body.name,
        tax_id=body.tax_id,
        kind=body.kind,
        is_default=body.is_default,
        remark=body.remark,
    )
    db.add(info)
    db.commit()
    return info


@router.put("/{info_id}", response_model=CompanyInfoOut)
def update_company_info(info_id: int, body: CompanyInfoUpdate, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    info = db.get(CompanyInfo, info_id)
    if info is None:
        raise HTTPException(404, "记录不存在")
    data = body.model_dump(exclude_unset=True)
    if "kind" in data:
        _validate_kind(data["kind"])
    if data.get("is_default") and (data.get("kind") or info.kind) != CompanyKind.self.value:
        raise HTTPException(422, "is_default 仅适用于 kind=self")
    if data.get("is_default"):
        _clear_defaults(db)
        db.refresh(info)
    for field, value in data.items():
        setattr(info, field, value)
    db.commit()
    return info


@router.delete("/{info_id}")
def delete_company_info(info_id: int, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    info = db.get(CompanyInfo, info_id)
    if info is None:
        raise HTTPException(404, "记录不存在")
    db.delete(info)
    db.commit()
    return {"ok": True}
```

api/__init__.py 挂载 `company_infos.router`。

- [ ] **Step 5: 迁移**

Run: `cd backend && uv run alembic revision --autogenerate -m "company_infos 常用税号公司表"`
检查生成脚本含 company_infos 表与 tax_id 唯一约束；`uv run alembic upgrade head` 成功。

- [ ] **Step 6: 运行测试与回归**

Run: `cd backend && uv run pytest ../test/test_api_company_infos.py -v` → 4 PASS
Run: `cd backend && uv run pytest ../test -q` → 168 passed（164 + 4）

- [ ] **Step 7: 提交**

```bash
git add backend/src/invoicing/models backend/src/invoicing/schemas/company_info.py \
  backend/src/invoicing/api/company_infos.py backend/src/invoicing/api/__init__.py \
  backend/alembic/versions test/test_api_company_infos.py
git commit -m "feat(backend): 常用税号及公司信息模型与 CRUD API"
```

---

### Task 2: 纠错字典与购买方归属校验

**Files:**
- Create: `backend/src/invoicing/parse/company_dict.py`
- Modify: `backend/src/invoicing/mcp/extract.py`（成功路径 confidence<1.0 时 enrich）
- Modify: `backend/src/invoicing/workers/tasks.py`（_parse_invoice 成功分支 enrich + 错误并入）
- Create: `test/test_company_dict.py`

**Interfaces:**
- Consumes: `CompanyInfo`、`ParsedInvoice`、`ParseError`
- Produces:
  - `enrich_parsed(parsed: ParsedInvoice, db: Session) -> list[ParseError]`（纠错 + BUYER_MISMATCH；返回追加错误）

- [ ] **Step 1: 写失败测试 test/test_company_dict.py**

```python
"""纠错字典与归属校验测试。"""
from datetime import date
from decimal import Decimal

from invoicing.models import CompanyInfo
from invoicing.parse.company_dict import enrich_parsed
from invoicing.parse.schemas import ParsedInvoice


def _parsed(buyer_name="澜鸿欣（上海）数字科技有限公司", buyer_tax_id="91310101MAELA36R35") -> ParsedInvoice:
    return ParsedInvoice(
        invoice_number="N1",
        issue_date=date(2026, 7, 9),
        amount_without_tax=Decimal("65.48"),
        tax_amount=Decimal("1.96"),
        total_amount=Decimal("67.44"),
        total_amount_cn="",
        seller_name="山东及时雨汽车科技有限公司西安分公司",
        seller_tax_id="91610132MA6UY02A5U",
        buyer_name=buyer_name,
        buyer_tax_id=buyer_tax_id,
        confidence_score=0.95,
        parse_source="OFD_TEXT",
    )


def test_fuzzy_name_correction(db):
    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.flush()
    parsed = _parsed()
    errors = enrich_parsed(parsed, db)
    assert parsed.buyer_name == "澜铮鸿欣（上海）数字科技有限公司"  # 模糊匹配纠错
    assert errors == []  # 税号匹配本司，无 BUYER_MISMATCH


def test_tax_id_match_prefers_preset(db):
    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.flush()
    parsed = _parsed(buyer_name="完全不同的名字")
    errors = enrich_parsed(parsed, db)
    assert parsed.buyer_name == "澜铮鸿欣（上海）数字科技有限公司"  # 税号精确匹配优先


def test_buyer_mismatch_adds_error(db):
    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.flush()
    parsed = _parsed(buyer_tax_id="91320594MA1MFD7F31")  # 其他公司税号
    errors = enrich_parsed(parsed, db)
    assert any(e.code == "BUYER_MISMATCH" for e in errors)


def test_no_self_configured_skips(db):
    parsed = _parsed()
    assert enrich_parsed(parsed, db) == []  # 未配置本司 → 跳过校验


def test_empty_dict_noop(db):
    parsed = _parsed()
    assert enrich_parsed(parsed, db) == []
    assert parsed.buyer_name == "澜鸿欣（上海）数字科技有限公司"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_company_dict.py -v`
Expected: FAIL（company_dict 不存在）

- [ ] **Step 3: 实现 parse/company_dict.py**

```python
"""常用公司字典：OCR 纠错 + 购买方归属校验（设计 V0.1）。

只作用于 confidence < 1.0 的来源（文本规则/OCR）——结构化来源（XML/XBRL）
以原件数据为准，不做纠错。调用方需自行保证该前提。
"""
import difflib

from sqlalchemy.orm import Session

from invoicing.models import CompanyInfo, CompanyKind
from invoicing.parse.schemas import ParseError, ParsedInvoice

_RATIO_THRESHOLD = 0.75
_LEN_DELTA = 4


def _best_match(name: str, infos: list[CompanyInfo]) -> CompanyInfo | None:
    best, best_ratio = None, 0.0
    for info in infos:
        if not info.name:
            continue
        ratio = difflib.SequenceMatcher(None, name, info.name).ratio()
        if ratio >= _RATIO_THRESHOLD and abs(len(name) - len(info.name)) <= _LEN_DELTA and ratio > best_ratio:
            best, best_ratio = info, ratio
    return best


def enrich_parsed(parsed: ParsedInvoice, db: Session) -> list[ParseError]:
    """纠错字典 + 购买方归属校验；返回追加的校验错误。"""
    errors: list[ParseError] = []
    infos = db.query(CompanyInfo).all()
    if not infos:
        return errors
    by_tax = {i.tax_id: i for i in infos}

    for attr in ("buyer", "seller"):
        name = getattr(parsed, f"{attr}_name") or ""
        tax_id = getattr(parsed, f"{attr}_tax_id") or ""
        if tax_id in by_tax:
            info = by_tax[tax_id]
            if info.name and name != info.name:
                setattr(parsed, f"{attr}_name", info.name)
        elif name:
            best = _best_match(name, infos)
            if best is not None:
                setattr(parsed, f"{attr}_name", best.name)

    self_infos = [i for i in infos if i.kind == CompanyKind.self.value]
    if self_infos and parsed.buyer_tax_id and all(
        i.tax_id != parsed.buyer_tax_id for i in self_infos
    ):
        errors.append(
            ParseError(
                code="BUYER_MISMATCH",
                message=f"购买方税号 {parsed.buyer_tax_id} 与预设本司不匹配",
            )
        )
    return errors
```

- [ ] **Step 4: 集成 mcp/extract.py**

`extract_invoice_file` 与 `batch_extract_invoice_files` 的成功路径（`result.success` 组装前）：对 `outcome.parsed` / IMAGE 分支的 `parsed`，当 `confidence_score < 1.0` 时：

```python
        from invoicing.db import SessionLocal
        from invoicing.parse.company_dict import enrich_parsed

        with SessionLocal() as s:
            extra = enrich_parsed(parsed, s)
        if extra:
            errors = errors + [{"code": e.code, "message": e.message} for e in extra]
```

（在 XML/PDF/OFD 结构化路径的 `_map_data(outcome.parsed, ...)` 之前插入；`errors` 变量为校验错误 dict 列表——按各分支现有形态合并。）

- [ ] **Step 5: 集成 workers/tasks.py**

`_parse_invoice` 成功分支（`_apply_parsed_fields(inv, outcome.parsed)` 之后、`transition` 之前）：

```python
        if outcome.parsed.confidence_score is not None and outcome.parsed.confidence_score < 1.0:
            from invoicing.parse.company_dict import enrich_parsed

            extra = enrich_parsed(outcome.parsed, db)
            if extra:
                inv.validation_errors = [
                    {"code": e.code, "message": e.message} for e in extra
                ]
```

（注意：`_apply_parsed_fields` 不拷贝 validation_errors，本分支单独设置；若 extra 非空，发票将因 errors 走待复核分支——把 `outcome.errors` 判定改为 `if outcome.parsed is not None and not outcome.errors and not extra:` 或直接沿用现有分支结构，将 extra 并入 outcome.errors 再走原逻辑——实现者按最小改动选择并把 `errors` 合成进 `outcome.errors`。）

- [ ] **Step 6: 运行测试与回归**

Run: `cd backend && uv run pytest ../test/test_company_dict.py -v` → 5 PASS
Run: `cd backend && uv run pytest ../test -q` → 173 passed（168 + 5）

- [ ] **Step 7: 提交**

```bash
git add backend/src/invoicing/parse/company_dict.py backend/src/invoicing/mcp/extract.py \
  backend/src/invoicing/workers/tasks.py test/test_company_dict.py
git commit -m "feat(parse): 常用公司纠错字典与购买方归属校验"
```

---

### Task 3: MCP 工具（company_info_list/save/delete）

**Files:**
- Modify: `backend/src/invoicing/mcp/tools.py`（3 函数）
- Modify: `backend/src/invoicing/mcp/server.py`（注册）
- Test: `test/test_mcp_company.py`

**Interfaces:**
- Consumes: `CompanyInfo/CompanyKind`、`SessionLocal`、`write_audit`、`CompanyInfoOut`
- Produces:
  - `company_info_list(kind: str | None) -> list[CompanyInfoOut]`
  - `company_info_save(name, tax_id, kind="other", is_default=False, remark=None) -> CompanyInfoOut`（tax_id 存在则更新，否则新建；默认联动同 REST）
  - `company_info_delete(id: int) -> dict`（不存在抛 ValueError）

- [ ] **Step 1: 写失败测试 test/test_mcp_company.py**

```python
"""MCP 常用公司工具测试（直调函数 + in-memory 协议）。"""
import pytest
from mcp import Client

from invoicing.db import SessionLocal
from invoicing.mcp.server import mcp
from invoicing.mcp.tools import company_info_list, company_info_save
from invoicing.models import CompanyInfo


def test_save_and_list(db):
    saved = company_info_save(
        name="澜铮鸿欣（上海）数字科技有限公司",
        tax_id="91310101MAELA36R35",
        kind="self",
        is_default=True,
    )
    assert saved.name == "澜铮鸿欣（上海）数字科技有限公司"
    items = company_info_list(kind="self")
    assert len(items) == 1
    assert items[0].tax_id == "91310101MAELA36R35"


def test_save_upsert_by_tax_id(db):
    first = company_info_save(name="A", tax_id="91610132MA6UY02A5U")
    second = company_info_save(name="山东及时雨汽车科技有限公司西安分公司", tax_id="91610132MA6UY02A5U")
    assert first.id == second.id  # 同税号更新
    assert second.name == "山东及时雨汽车科技有限公司西安分公司"


@pytest.mark.asyncio
async def test_in_memory_client_lists_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
    assert {"company_info_list", "company_info_save", "company_info_delete"} <= names
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_mcp_company.py -v`
Expected: FAIL（工具不存在）

- [ ] **Step 3: 实现 mcp/tools.py 追加**

```python
def company_info_list(kind: str | None = None) -> list[CompanyInfoOut]:
    """常用公司列表（kind 可选：self/supplier/other）。"""
    with SessionLocal() as db:
        q = db.query(CompanyInfo)
        if kind:
            q = q.filter(CompanyInfo.kind == kind)
        return [CompanyInfoOut.model_validate(i, from_attributes=True) for i in q.order_by(CompanyInfo.id).all()]


def company_info_save(
    name: str,
    tax_id: str,
    kind: str = CompanyKind.other.value,
    is_default: bool = False,
    remark: str | None = None,
) -> CompanyInfoOut:
    """保存常用公司（同税号更新；is_default 仅 kind=self，设默认清其他默认）。"""
    from invoicing.models import CompanyInfo, CompanyKind

    if kind not in {k.value for k in CompanyKind}:
        raise ValueError(f"非法类型: {kind}（可选 self/supplier/other）")
    if is_default and kind != CompanyKind.self.value:
        raise ValueError("is_default 仅适用于 kind=self")
    with SessionLocal() as db:
        info = db.query(CompanyInfo).filter(CompanyInfo.tax_id == tax_id).first()
        if info is None:
            info = CompanyInfo(tax_id=tax_id)
            db.add(info)
        if is_default:
            db.query(CompanyInfo).filter(CompanyInfo.is_default.is_(True)).update({"is_default": False})
        info.name = name
        info.kind = kind
        info.is_default = is_default
        info.remark = remark
        db.commit()
        write_audit(
            db, action=AuditAction.CONFIG_CHANGE.value, channel="mcp",
            detail={"entity": "company_info", "tax_id": tax_id},
        )
        db.commit()
        return CompanyInfoOut.model_validate(info, from_attributes=True)


def company_info_delete(id: int) -> dict:
    """删除常用公司；不存在抛 ValueError。"""
    with SessionLocal() as db:
        info = db.get(CompanyInfo, id)
        if info is None:
            raise ValueError(f"记录不存在: {id}")
        db.delete(info)
        write_audit(
            db, action=AuditAction.CONFIG_CHANGE.value, channel="mcp",
            detail={"entity": "company_info", "id": id, "deleted": True},
        )
        db.commit()
        return {"ok": True}
```

（`CompanyInfo`/`CompanyKind`/`CompanyInfoOut`/`AuditAction` 按需补导入。）

- [ ] **Step 4: 注册（mcp/server.py 末尾追加）**

```python
    @server.tool(description="查询常用税号及公司信息（kind 可选 self/supplier/other）。")
    def company_info_list(kind: str | None = None) -> list[CompanyInfoOut]:
        return mcp_tools.company_info_list(kind)

    @server.tool(description="保存常用税号及公司信息（同税号更新；kind=self 可设默认）。")
    def company_info_save(
        name: str, tax_id: str, kind: str = "other", is_default: bool = False, remark: str | None = None
    ) -> CompanyInfoOut:
        return mcp_tools.company_info_save(name, tax_id, kind, is_default, remark)

    @server.tool(description="删除常用税号及公司信息。")
    def company_info_delete(id: int) -> dict:
        return mcp_tools.company_info_delete(id)
```

- [ ] **Step 5: 运行测试与回归**

Run: `cd backend && uv run pytest ../test/test_mcp_company.py -v` → 3 PASS
Run: `cd backend && uv run pytest ../test -q` → 176 passed

- [ ] **Step 6: 提交**

```bash
git add backend/src/invoicing/mcp/tools.py backend/src/invoicing/mcp/server.py test/test_mcp_company.py
git commit -m "feat(mcp): 常用税号及公司信息工具（list/save/delete）"
```

---

### Task 4: Web UI 系统设置 tab 与文档

**Files:**
- Modify: `web/src/types.ts`（CompanyInfoOut/Create/Update）
- Create: `web/src/api/companyInfos.ts`
- Modify: `web/src/views/SettingsView.vue`（新 tab）
- Modify: `web/src/views/__tests__/settings.spec.ts`（mock 追加）
- Modify: `docs/开发环境指南.md`（常用公司说明）

**Interfaces:**
- Consumes: Task 1 REST
- Produces: 前端类型/api/新 tab

- [ ] **Step 1: types.ts 追加**

```ts
export interface CompanyInfoOut {
  id: number;
  name: string;
  tax_id: string;
  kind: string;
  is_default: boolean;
  remark: string | null;
  created_at: string;
  updated_at: string;
}
export interface CompanyInfoCreate {
  name: string;
  tax_id: string;
  kind?: string;
  is_default?: boolean;
  remark?: string | null;
}
export type CompanyInfoUpdate = Partial<CompanyInfoCreate>;
export const COMPANY_KIND_LABELS: Record<string, string> = {
  self: "本司", supplier: "供应商", other: "其他",
};
```

- [ ] **Step 2: api/companyInfos.ts**

```ts
import { api } from "./client";
import type { CompanyInfoCreate, CompanyInfoOut, CompanyInfoUpdate } from "../types";

export async function listCompanyInfos(): Promise<CompanyInfoOut[]> {
  const { data } = await api.get<CompanyInfoOut[]>("/company-infos");
  return data;
}
export async function createCompanyInfo(body: CompanyInfoCreate): Promise<CompanyInfoOut> {
  const { data } = await api.post<CompanyInfoOut>("/company-infos", body);
  return data;
}
export async function updateCompanyInfo(id: number, body: CompanyInfoUpdate): Promise<CompanyInfoOut> {
  const { data } = await api.put<CompanyInfoOut>(`/company-infos/${id}`, body);
  return data;
}
export async function deleteCompanyInfo(id: number): Promise<void> {
  await api.delete(`/company-infos/${id}`);
}
```

- [ ] **Step 3: SettingsView 新 tab**

- `<a-tabs>` 追加 `<a-tab-pane key="company" tab="常用税号/公司">`
- 状态：`companyInfos = ref<CompanyInfoOut[]>([])`、`companyModalOpen`、`companyForm = reactive({ name: "", tax_id: "", kind: "other", is_default: false, remark: "" })`、`editingCompanyId = ref<number | null>(null)`
- loadAll 追加 `companyInfos.value = await listCompanyInfos()`
- 表格列：名称/税号/类型（COMPANY_KIND_LABELS）/默认/操作（编辑、删除）；新建/编辑 modal（税号输入时前端校验 18 位提示）；删除用 `Modal.confirm` 或 window.confirm
- 保存/删除调用 Task 2 api，成功 `message.success` + 刷新

- [ ] **Step 4: settings.spec.ts mock 追加并跑测试**

mock `../../api/companyInfos` 的 listCompanyInfos 返回 `[]`，断言渲染不崩溃；`npm run test` 全绿 + `npm run build` 成功。

- [ ] **Step 5: docs 与回归**

docs/开发环境指南.md 追加：

```markdown
## 常用税号及公司信息

- 系统设置 →「常用税号/公司」：预存公司（本司/供应商/其他），支持默认本司标记
- 作用：① 文本/OCR 解析出的公司名自动模糊纠错（如 OCR 漏字）② 购买方税号与预设本司不匹配 → 待复核（BUYER_MISMATCH）③ MCP 工具 `company_info_list/save/delete` 可查可设
- 结构化来源（XML/XBRL）不做纠错（原件数据优先）
```

Run: `cd backend && uv run pytest ../test -q`（176 不变）+ `cd web && npm run test && npm run build` 全绿

- [ ] **Step 6: 提交**

```bash
git add web/src/types.ts web/src/api/companyInfos.ts web/src/views/SettingsView.vue \
  web/src/views/__tests__/settings.spec.ts docs/开发环境指南.md
git commit -m "feat(web): 系统设置新增常用税号/公司 tab 与文档"
```

---

## Plan K 验收清单（全部完成后核对）

- [ ] 后端 176 测试全绿（164 + API 4 + dict 5 + MCP 3）
- [ ] REST CRUD（admin）+ 校验（税号 18 位/kind 枚举/默认联动）
- [ ] 纠错字典：模糊匹配（0.75/长度差 4）+ 税号优先；只作用 confidence<1.0
- [ ] BUYER_MISMATCH 从严（进待复核），未配置本司跳过
- [ ] MCP 3 工具注册 + 审计 CONFIG_CHANGE channel=mcp
- [ ] Web UI tab + 前端 13+ 测试 + build
- [ ] 真机验证：预存「澜铮鸿欣（上海）数字科技有限公司」后，及时 OFD 的 OCR 名称自动纠正
