# 发票易 WorkBuddy 集成实施计划（Plan F）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 /mcp server 上扩展 4 个 WorkBuddy 编排工具（extract_invoice / batch_extract_invoices / validate_invoice / invoice_ingest），落地 Agent 编排集成模式（Agent Mail 下载附件到本地 → 发票易识别/校验/归档），并输出 WorkBuddy 接入文档与 Skill 编排指令。

**Architecture:** 纯函数识别层（`mcp/extract.py`：本地文件分类 → 既有 parse_file 分级路由 → ExtractResult 映射）不落库；`invoice_ingest` 落库闭环（原件入存储 → 建 Invoice(parsing) → 内联 parse+verify → InvoiceOut）。工具挂既有 MCPServer（Plan B 的 `mcp/` 包），零改动 REST/收取链路。

**Tech Stack:** Python（pydantic 输出模型、既有 parse/validation 引擎）、MCP Python SDK v2（既有 server 装饰器）、WorkBuddy（平台 Agent Mail Connector + 自定义 MCP streamable-http）。

**Spec:** `design/2026-08-15-workbuddy-integration-design.md`（V0.1，§三工具表与 ExtractResult 结构、§四配置、§五职责边界）；`/Users/james/WorkBuddy/2026-08-15-19-40-33/invoice-extractor-mcp/design.html`（命名与流程参照）。

## Global Constraints

- ExtractResult/ValidationResult 字段名与设计 §三逐字一致（camelCase，checkCode/addressPhone/bankAccount/items 缺失置空）
- 图片输入一律合规拒收（FRD G-03）：`"合规拒收：仅接受 PDF/OFD/XML 原件（财会〔2025〕9 号）"`；纯版式 PDF → `"未内嵌结构化数据，OCR 引擎 Phase 2 支持"`
- 金额 Decimal 严禁 float；中文注释；测试在仓库根 `test/`；提交格式 `feat(mcp): ...`
- 后端回归基线 108 不得回退；开发模式 SQLite/本地存储/进程内队列，无需 docker
- MCP 工具错误用 raise ValueError（SDK 转 isError）；stateless 工具（extract/validate）不开 DB 会话

---

### Task 1: extract / validate 工具与数据映射

**Files:**
- Create: `backend/src/invoicing/schemas/mcp_extract.py`
- Create: `backend/src/invoicing/mcp/extract.py`
- Modify: `backend/src/invoicing/mcp/server.py`（注册 3 个工具）
- Create: `test/test_mcp_extract.py`

**Interfaces:**
- Consumes: `classify_attachment`（fetch/filters.py）、`parse_file`（parse/router.py）、`ParsedInvoice/ParseError`（parse/schemas.py）、`validate`（parse/validation.py）
- Produces:
  - `ExtractResult / ExtractInvoiceData / PartyInfo / ValidationResult / ValidationError`（schemas/mcp_extract.py）
  - `extract_invoice_file(file_path: str) -> ExtractResult`、`batch_extract_invoice_files(file_paths: list[str]) -> list[ExtractResult]`、`validate_invoice_data(invoice_data: dict) -> ValidationResult`、`IMAGE_REJECT: str`、`UNSTRUCTURED: str`（F2 复用）
  - server.py 注册 `extract_invoice / batch_extract_invoices / validate_invoice`

- [ ] **Step 1: 写失败测试 test/test_mcp_extract.py**

```python
"""WorkBuddy 识别工具测试（设计 V0.1 §三）。"""
import json
from datetime import date
from pathlib import Path

import pytest
from mcp import Client

from invoicing.mcp.extract import (
    UNSTRUCTURED,
    batch_extract_invoice_files,
    extract_invoice_file,
    validate_invoice_data,
)
from invoicing.mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_extract_xml_success():
    result = extract_invoice_file(str(FIXTURES / "dianzi.xml"))
    assert result.success is True
    assert result.data.invoiceNumber == "24312000000012345678"
    assert result.data.amountWithoutTax == "909.09"
    assert result.data.totalWithTax == "1000.00"
    assert result.data.totalWithTaxCN == "壹仟元整"
    assert result.data.buyer.name == "测试采购有限公司"
    assert result.data.checkCode is None
    assert result.data.items == []
    assert result.validation.valid is True


def test_extract_image_rejected(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    result = extract_invoice_file(str(p))
    assert result.success is False
    assert "合规拒收" in result.error


def test_extract_plain_pdf_unstructured(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF-1.4 no data")
    result = extract_invoice_file(str(p))
    assert result.success is False
    assert result.error == UNSTRUCTURED


def test_extract_missing_file():
    with pytest.raises(ValueError, match="文件不存在"):
        extract_invoice_file("/nonexistent/x.pdf")


def test_batch_extract_mixed(tmp_path):
    ok = FIXTURES / "dianzi.xml"
    bad = tmp_path / "photo.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n")
    results = batch_extract_invoice_files([str(ok), str(bad)])
    assert len(results) == 2
    assert results[0].success is True
    assert results[1].success is False


def test_validate_ok():
    data = {
        "invoiceNumber": "24312000000012345678",
        "issueDate": "2026-08-01",
        "amountWithoutTax": "909.09",
        "taxAmount": "90.91",
        "totalWithTax": "1000.00",
        "totalWithTaxCN": "壹仟元整",
        "buyer": {"name": "A", "taxId": "T1"},
        "seller": {"name": "B", "taxId": "T2"},
    }
    assert validate_invoice_data(data).valid is True


def test_validate_total_mismatch():
    data = {
        "invoiceNumber": "N1", "issueDate": "2026-08-01",
        "amountWithoutTax": "909.10", "taxAmount": "90.91", "totalWithTax": "1000.00",
    }
    result = validate_invoice_data(data)
    assert result.valid is False
    assert any(e.code == "TOTAL_MISMATCH" for e in result.errors)


def test_validate_missing_field():
    result = validate_invoice_data({"invoiceNumber": "N1"})
    assert result.valid is False
    assert any(e.code == "MISSING_FIELD" for e in result.errors)


@pytest.mark.asyncio
async def test_in_memory_client_lists_new_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
    assert {"extract_invoice", "batch_extract_invoices", "validate_invoice"} <= names
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_mcp_extract.py -v`
Expected: FAIL（`invoicing.mcp.extract` 不存在）

- [ ] **Step 3: 实现 schemas/mcp_extract.py**

```python
"""WorkBuddy 识别工具输出模型（字段名对齐集成设计 V0.1 §三，camelCase）。"""
from pydantic import BaseModel


class PartyInfo(BaseModel):
    name: str | None = None
    taxId: str | None = None
    addressPhone: str | None = None
    bankAccount: str | None = None


class ExtractInvoiceData(BaseModel):
    invoiceType: str | None = None
    invoiceNumber: str
    invoiceCode: str | None = None
    issueDate: str
    checkCode: str | None = None
    buyer: PartyInfo
    seller: PartyInfo
    amountWithoutTax: str
    taxAmount: str
    totalWithTax: str
    totalWithTaxCN: str | None = None
    items: list[dict] = []
    sourceFile: str
    extractedAt: str


class ValidationError(BaseModel):
    code: str
    message: str


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []


class ExtractResult(BaseModel):
    success: bool
    error: str | None = None
    data: ExtractInvoiceData | None = None
    validation: ValidationResult | None = None
```

- [ ] **Step 4: 实现 mcp/extract.py**

```python
"""WorkBuddy 文件级识别：读本地文件 → 分级解析 → ExtractResult（集成设计 V0.1 §三）。

图片按合规拒收（FRD G-03 仅收原件）；纯版式 PDF 无内嵌结构化数据时
success=False 并提示 OCR 引擎 Phase 2 支持。
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from invoicing.fetch.filters import classify_attachment
from invoicing.models.enums import FileType
from invoicing.parse.router import parse_file
from invoicing.parse.schemas import ParsedInvoice
from invoicing.parse.validation import validate as core_validate
from invoicing.schemas.mcp_extract import (
    ExtractInvoiceData,
    ExtractResult,
    PartyInfo,
    ValidationResult,
)

IMAGE_REJECT = "合规拒收：仅接受 PDF/OFD/XML 原件（财会〔2025〕9 号）"
UNSTRUCTURED = "未内嵌结构化数据，OCR 引擎 Phase 2 支持"

_SUPPORTED = (FileType.PDF.value, FileType.OFD.value, FileType.XML.value)


def _read_file(file_path: str) -> bytes:
    path = Path(file_path)
    if not path.is_file():
        raise ValueError(f"文件不存在: {file_path}")
    return path.read_bytes()


def _map_data(parsed: ParsedInvoice, source_file: str) -> ExtractInvoiceData:
    return ExtractInvoiceData(
        invoiceNumber=parsed.invoice_number,
        invoiceCode=parsed.invoice_code,
        issueDate=parsed.issue_date.isoformat(),
        buyer=PartyInfo(name=parsed.buyer_name, taxId=parsed.buyer_tax_id),
        seller=PartyInfo(name=parsed.seller_name, taxId=parsed.seller_tax_id),
        amountWithoutTax=str(parsed.amount_without_tax),
        taxAmount=str(parsed.tax_amount),
        totalWithTax=str(parsed.total_amount),
        totalWithTaxCN=parsed.total_amount_cn,
        sourceFile=source_file,
        extractedAt=datetime.now(timezone.utc).isoformat(),
    )


def extract_invoice_file(file_path: str) -> ExtractResult:
    data = _read_file(file_path)
    kind = classify_attachment(Path(file_path).name, "", data)
    if kind == "IMAGE":
        return ExtractResult(success=False, error=IMAGE_REJECT)
    if kind not in _SUPPORTED:
        return ExtractResult(success=False, error=f"不支持的格式: {kind or '未知'}")
    outcome = parse_file(kind, data)
    if outcome.parsed is None:
        if outcome.source == "PDF_UNSTRUCTURED":
            return ExtractResult(success=False, error=UNSTRUCTURED)
        detail = outcome.errors[0].message if outcome.errors else "解析失败"
        return ExtractResult(success=False, error=f"解析失败: {detail}")
    validation_errors = [{"code": e.code, "message": e.message} for e in outcome.errors]
    return ExtractResult(
        success=True,
        data=_map_data(outcome.parsed, file_path),
        validation=ValidationResult(valid=not validation_errors, errors=validation_errors),
    )


def batch_extract_invoice_files(file_paths: list[str]) -> list[ExtractResult]:
    return [extract_invoice_file(p) for p in file_paths]


def validate_invoice_data(invoice_data: dict) -> ValidationResult:
    required = ["invoiceNumber", "issueDate", "amountWithoutTax", "taxAmount", "totalWithTax"]
    missing = [
        {"code": "MISSING_FIELD", "message": f"缺少必填字段: {f}"}
        for f in required
        if invoice_data.get(f) in (None, "")
    ]
    if missing:
        return ValidationResult(valid=False, errors=missing)
    try:
        parsed = ParsedInvoice(
            invoice_number=str(invoice_data["invoiceNumber"]),
            issue_date=date.fromisoformat(str(invoice_data["issueDate"])),
            amount_without_tax=Decimal(str(invoice_data["amountWithoutTax"])),
            tax_amount=Decimal(str(invoice_data["taxAmount"])),
            total_amount=Decimal(str(invoice_data["totalWithTax"])),
            total_amount_cn=str(invoice_data.get("totalWithTaxCN") or ""),
            seller_name=str((invoice_data.get("seller") or {}).get("name") or ""),
            seller_tax_id=str((invoice_data.get("seller") or {}).get("taxId") or ""),
            buyer_name=str((invoice_data.get("buyer") or {}).get("name") or ""),
            buyer_tax_id=str((invoice_data.get("buyer") or {}).get("taxId") or ""),
            confidence_score=1.0,
            parse_source="workbuddy",
        )
    except (ValueError, InvalidOperation) as e:
        return ValidationResult(valid=False, errors=[{"code": "INVALID_FIELD", "message": str(e)}])
    core_errors = [{"code": e.code, "message": e.message} for e in core_validate(parsed)]
    return ValidationResult(valid=not core_errors, errors=core_errors)
```

- [ ] **Step 5: 注册工具（mcp/server.py）**

在 `mcp = build_server()` 的 build_server 函数内、既有 3 个 tool 之后追加：

```python
    from invoicing.mcp import extract as mcp_extract
    from invoicing.schemas.mcp_extract import ExtractResult, ValidationResult

    @server.tool(description="识别本地发票文件（PDF/OFD/XML 原件；图片按合规拒收），返回结构化数据与校验结果。")
    def extract_invoice(file_path: str) -> ExtractResult:
        return mcp_extract.extract_invoice_file(file_path)

    @server.tool(description="批量识别本地发票文件，逐条返回 success/error，互不影响。")
    def batch_extract_invoices(file_paths: list[str]) -> list[ExtractResult]:
        return mcp_extract.batch_extract_invoice_files(file_paths)

    @server.tool(description="校验发票数据：字段完整性与价税合计/大小写金额一致性。")
    def validate_invoice(invoice_data: dict) -> ValidationResult:
        return mcp_extract.validate_invoice_data(invoice_data)
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_mcp_extract.py -v`
Expected: 9 PASS（若 in-memory Client 断言因工具描述/装饰器差异失败，按已安装 SDK 等价写法微调并说明）

- [ ] **Step 7: 回归并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 117 passed（108 + 9）

```bash
git add backend/src/invoicing/schemas/mcp_extract.py backend/src/invoicing/mcp/extract.py \
  backend/src/invoicing/mcp/server.py test/test_mcp_extract.py
git commit -m "feat(mcp): WorkBuddy 识别工具（extract/batch_extract/validate）"
```

---

### Task 2: invoice_ingest 工具（归档闭环）

**Files:**
- Modify: `backend/src/invoicing/models/enums.py`（AuditAction 加 INGEST）
- Modify: `backend/src/invoicing/mcp/tools.py`（ingest_invoice）
- Modify: `backend/src/invoicing/mcp/server.py`（注册 invoice_ingest）
- Create: `test/test_mcp_ingest.py`

**Interfaces:**
- Consumes: `IMAGE_REJECT`（Task 1）、`get_storage`、`SessionLocal`、`Invoice/InvoiceStatus/AuditAction`、`write_audit`、`enqueue_parse_sync`、`InvoiceOut`
- Produces: `ingest_invoice(file_path: str) -> InvoiceOut`（图片/不支持格式抛 ValueError；原件存 `tenant-default/workbuddy/{uuid}-{name}`；建 Invoice(parsing, email_subject="WorkBuddy 导入")；审计 INGEST channel=mcp；本地模式内联 parse+verify 后返回终态 InvoiceOut）

- [ ] **Step 1: 写失败测试 test/test_mcp_ingest.py**

```python
"""WorkBuddy 归档闭环工具测试（集成设计 V0.1 §三 invoice_ingest）。"""
from pathlib import Path

import pytest

from invoicing.db import SessionLocal
from invoicing.mcp.tools import ingest_invoice
from invoicing.models import AuditLog, Invoice

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_ingest_xml_full_pipeline(db):
    inv = ingest_invoice(str(FIXTURES / "dianzi.xml"))
    assert inv.status == "pending_submit"  # 本地队列模式：内联 parse+verify
    assert inv.invoice_number == "24312000000012345678"
    assert inv.total_amount is not None
    assert inv.xml_url is not None  # XML 原件归档
    with SessionLocal() as s:
        log = s.query(AuditLog).filter(AuditLog.action == "INGEST", AuditLog.invoice_id == inv.id).first()
    assert log is not None
    assert log.channel == "mcp"


def test_ingest_image_rejected(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    with pytest.raises(ValueError, match="合规拒收"):
        ingest_invoice(str(p))


def test_ingest_duplicate_number_blocked(db, tmp_path):
    first = ingest_invoice(str(FIXTURES / "dianzi.xml"))
    assert first.status == "pending_submit"
    # 同发票号不同文件再次导入 → 查重拦截
    dup = tmp_path / "dianzi_copy.xml"
    dup.write_bytes((FIXTURES / "dianzi.xml").read_bytes())
    second = ingest_invoice(str(dup))
    assert second.status == "blocked"
    assert second.duplicate_of_id == first.id
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_mcp_ingest.py -v`
Expected: FAIL（`ingest_invoice` 不存在）

- [ ] **Step 3: 实现 enums 与 tools.py**

enums.py 的 AuditAction 追加：`INGEST = "INGEST"`。

tools.py 追加（文件尾部）：

```python
def ingest_invoice(file_path: str) -> InvoiceOut:
    """WorkBuddy 归档闭环：原件入存储 → 解析 → 验真（本地模式内联）→ 返回发票记录。"""
    from pathlib import Path
    from uuid import uuid4

    from invoicing.fetch.filters import classify_attachment
    from invoicing.mcp.extract import IMAGE_REJECT
    from invoicing.models import AuditAction, Invoice, InvoiceStatus
    from invoicing.storage import get_storage
    from invoicing.workers.queue import enqueue_parse_sync

    path = Path(file_path)
    if not path.is_file():
        raise ValueError(f"文件不存在: {file_path}")
    data = path.read_bytes()
    kind = classify_attachment(path.name, "", data)
    if kind == "IMAGE":
        raise ValueError(IMAGE_REJECT)
    if kind not in ("PDF", "OFD", "XML"):
        raise ValueError(f"不支持的格式: {kind or '未知'}")

    key = f"tenant-default/workbuddy/{uuid4().hex}-{path.name}"
    get_storage().put(key, data, "application/octet-stream")

    with SessionLocal() as db:
        inv = Invoice(
            file_url=key,
            file_type=kind,
            status=InvoiceStatus.parsing.value,
            email_subject="WorkBuddy 导入",
        )
        db.add(inv)
        db.commit()
        invoice_id = inv.id
        write_audit(
            db, action=AuditAction.INGEST.value, invoice_id=invoice_id, channel="mcp",
            detail={"source_file": file_path, "file_type": kind},
        )
        db.commit()

    enqueue_parse_sync(invoice_id)  # 本地模式内联执行 parse+verify；redis 模式入队

    with SessionLocal() as db:
        inv = db.get(Invoice, invoice_id)
        return InvoiceOut.model_validate(inv, from_attributes=True)
```

（`InvoiceOut` 已在 tools.py 导入；`SessionLocal` 已导入；`write_audit` 已导入——仅按需补缺失 import。）

- [ ] **Step 4: 注册 invoice_ingest（mcp/server.py，Task 1 三个工具之后）**

```python
    @server.tool(description="导入本地发票原件入库：原件归档 → 解析 → 验真，返回发票记录（重复发票返回已拦截状态）。")
    def invoice_ingest(file_path: str) -> InvoiceOut:
        return mcp_tools.ingest_invoice(file_path)
```

（`InvoiceOut` 已在 server.py 导入；`mcp_tools` 已在 server.py 引用。）

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_mcp_ingest.py -v`
Expected: 3 PASS

- [ ] **Step 6: 回归并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 120 passed（117 + 3）

```bash
git add backend/src/invoicing/models/enums.py backend/src/invoicing/mcp/tools.py \
  backend/src/invoicing/mcp/server.py test/test_mcp_ingest.py
git commit -m "feat(mcp): invoice_ingest 归档闭环工具"
```

---

### Task 3: WorkBuddy 接入文档与 Skill 编排

**Files:**
- Modify: `docs/开发环境指南.md`（WorkBuddy 集成章节）
- Create: `docs/workbuddy-skill/SKILL.md`
- Modify: `CLAUDE.md`（构建与测试节追加一行说明）

**Interfaces:**
- Consumes: Task 1/2 的工具面
- Produces: WorkBuddy 接入文档 + Skill 编排指令

- [ ] **Step 1: docs/开发环境指南.md 追加章节**

```markdown
## WorkBuddy 集成（Agent 编排模式）

架构：MCP 服务器之间不能互调——Agent Mail（WorkBuddy 平台 Connector）负责邮件，
发票易 MCP 负责识别/校验/归档，Agent 编排、本地文件系统传递。

1. 后端启动后，在 `~/.workbuddy/mcp.json` 注册发票易 MCP（与既有 mcpServers 合并）：

```json
{
  "mcpServers": {
    "invoice-ease": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8001/mcp",
      "headers": { "Authorization": "Bearer <INVOICING_MCP_TOKEN>" }
    }
  }
}
```

（streamable-http 支持与否以 WorkBuddy 平台实测为准；若仅支持 stdio，需 stdio 适配进程，Phase 2 备选。）

2. 可用工具：`extract_invoice`（识别）/ `batch_extract_invoices`（批量）/ `validate_invoice`（校验）/ `invoice_ingest`（识别+入库+验真归档）/ 既有 `invoice_fetch`（服务端收取）/ `invoice_list` / `invoice_detail`。
3. 图片输入一律合规拒收（财会〔2025〕9 号，仅收 PDF/OFD/XML 原件）；纯版式 PDF 提示 OCR Phase 2。
4. Skill：将 `docs/workbuddy-skill/SKILL.md` 复制到 `~/.workbuddy/skills/invoice-auto-collect/`，对话中说「帮我收集邮箱里的发票」即可。
```

- [ ] **Step 2: 写 docs/workbuddy-skill/SKILL.md**

```markdown
# invoice-auto-collect

自动收集 WorkBuddy Agent Email 中的发票邮件，识别并归档到发票易。

## 工作流

1. `mcp__agent-mail__SearchMessages(q="发票", has_attachments=true)` → 候选邮件列表
2. 逐封 `mcp__agent-mail__ListAttachments(message_id)` → 附件清单（只看 PDF/OFD/XML 附件）
3. `mcp__agent-mail__download_attachment(...)` 下载到 `/Users/james/WorkBuddy/invoices/`
4. `mcp__invoice-ease__batch_extract_invoices(file_paths)` → 批量识别（图片附件跳过并提示用户走原件邮箱）
5. 识别成功的原件执行 `mcp__invoice-ease__invoice_ingest(file_path)` → 入库归档（返回待提交/已拦截状态）
6. 汇总输出：表格列出 发票号码/销售方/价税合计/状态；拦截的注明原因（重复发票）

## 约束

- 仅处理 PDF/OFD/XML 附件；JPG/PNG 等图片按合规要求拒绝识别
- 同一发票不要重复 ingest（系统按发票代码+号码自动拦截）
- 邮件与附件由 Agent Mail Connector 管理，本 Skill 不实现任何邮件协议
```

- [ ] **Step 3: CLAUDE.md 构建与测试节追加一行**

```bash
cd backend && uv run pytest ../test/test_mcp_extract.py ../test/test_mcp_ingest.py -v  # WorkBuddy 识别/归档工具测试
```

- [ ] **Step 4: 全量回归并提交**

Run: `cd backend && uv run pytest ../test -q` + `cd web && npm run test`
Expected: 后端 120 passed、前端 13 passed

```bash
git add docs/开发环境指南.md docs/workbuddy-skill/SKILL.md CLAUDE.md
git commit -m "docs: WorkBuddy 集成文档与 Skill 编排指令"
```

---

## Plan F 验收清单（全部完成后核对）

- [ ] 后端 120 测试全绿（extract 9 + ingest 3 + 基线 108）
- [ ] 4 个工具名与设计 §三一致；ExtractResult 结构与设计逐字一致（camelCase）
- [ ] 图片合规拒收、纯版式 PDF 明确提示、批量互不影响、validate 三路径（OK/金额不一致/缺字段）
- [ ] ingest 闭环：原件归档 + 审计 INGEST channel=mcp + 重复拦截 blocked
- [ ] in-memory Client 断言 7 个工具注册
- [ ] 文档可照做（mcp.json 示例、SKILL.md 工作流）
