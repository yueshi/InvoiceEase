# invoicing/mcp/tools.py
"""MCP 工具实现：薄适配器，直接调用 workflow service 层。"""
import re
from datetime import date

from fastapi import HTTPException

from invoicing.audit import write_audit
from invoicing.db import SessionLocal
from invoicing.fetch.service import poll_mailbox
from invoicing.models import AuditAction, AuditLog, CompanyInfo, CompanyKind, Mailbox, Role, User
from invoicing.schemas.company_info import CompanyInfoOut, TAX_ID_PATTERN
from invoicing.schemas.invoice import InvoiceListResponse, InvoiceOut
from invoicing.schemas.mailbox import PollResultOut
from invoicing.workflow import services


def _mcp_admin_user() -> User:
    """MCP 静态 token 即管理员通道：合成 admin 用户走 service 层（无租户过滤，MVP 单租户）。"""
    return User(id=0, username="mcp", password_hash="", role=Role.admin.value)


def fetch_invoices(mailbox_id: int | None = None) -> PollResultOut:
    total = {"received": 0, "rejected_images": 0, "ignored": 0, "duplicates": 0, "errors": 0}
    with SessionLocal() as db:
        q = db.query(Mailbox).filter(Mailbox.enabled.is_(True))
        if mailbox_id is not None:
            q = q.filter(Mailbox.id == mailbox_id)
        mailboxes = q.all()
        if mailbox_id is not None and not mailboxes:
            raise ValueError(f"邮箱不存在或已停用: {mailbox_id}")
        for mb in mailboxes:
            result = poll_mailbox(db, mb)
            for key in total:
                total[key] += getattr(result, key)
        write_audit(
            db, action="FETCH", channel="mcp",
            detail={"mailbox_ids": [mb.id for mb in mailboxes], "result": total},
        )
        db.commit()
    return PollResultOut(**total, active_mailboxes=[mb.username or mb.name for mb in mailboxes])


def list_invoices_mcp(
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> InvoiceListResponse:
    with SessionLocal() as db:
        result = services.list_invoices(
            db, _mcp_admin_user(), status, date_from, date_to, keyword, page, page_size
        )
    # MCP 返回需 pydantic 模型（REST 由 response_model 转换，MCP 无此层）
    result.items = [InvoiceOut.model_validate(item, from_attributes=True) for item in result.items]
    return result


def get_invoice_mcp(invoice_id: int) -> InvoiceOut:
    with SessionLocal() as db:
        try:
            inv = services.get_invoice(db, _mcp_admin_user(), invoice_id)
        except HTTPException as e:
            # service 层 404 泄漏到 MCP 层，映射为协议友好的错误信息
            raise ValueError(f"发票不存在或无权访问: {invoice_id}") from e
        return InvoiceOut.model_validate(inv, from_attributes=True)


def _not_invoice_reason(data: bytes, kind: str) -> str:
    """非发票拒收的可行动提示：命中回单特征 → 引导 receipt_ingest；
    识别能力缺失 → 说明受限；其余 → 明确「不是电子发票原件」。"""
    from invoicing.parse.receipt import parse_receipt_bytes
    from invoicing.workers.tasks import _parse_chain_armed

    receipt = parse_receipt_bytes(data, kind)
    if receipt.get("amount") is not None and receipt.get("counterparty_name"):
        return (
            "该文件疑似银行回单，不是电子发票原件。"
            "请改用 receipt_ingest 工具处理银行回单。"
        )
    if not _parse_chain_armed():
        return (
            "该 PDF 未提取到发票字段：文件无文本层，且本环境未配置 OCR/LLM 引擎，"
            "无法识别图片型原件。请部署 OCR 引擎或通过 Web 端上传处理。"
        )
    return "该文件不是电子发票原件（未识别出发票字段），已拒收。"


def ingest_invoice(file_path: str) -> InvoiceOut:
    """WorkBuddy 归档闭环：原件入存储 → 解析 → 验真（本地模式内联）→ 返回发票记录。
    注意：本地模式返回终态 InvoiceOut；redis 模式返回 parsing 中间态（异步 worker 处理），状态以发票详情查询为准。"""
    from pathlib import Path
    from uuid import uuid4

    from invoicing.fetch.filters import classify_attachment
    from invoicing.mcp.extract import OCR_UNAVAILABLE, _read_file
    from invoicing.models import AuditAction, Invoice, InvoiceStatus
    from invoicing.models.enums import FileType
    from invoicing.storage import get_storage
    from invoicing.workers.queue import enqueue_parse_sync

    path = Path(file_path)
    data = _read_file(file_path)
    kind = classify_attachment(path.name, "", data)
    if kind == "IMAGE":
        from invoicing.parse.ocr import get_ocr_provider

        if get_ocr_provider() is None:
            raise ValueError(OCR_UNAVAILABLE)
        # 图片原件合规归档（本地工具语义；邮箱收取仍拒收图片）
        kind = FileType.IMAGE.value
    if kind not in ("PDF", "OFD", "XML", "IMAGE"):
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
        if inv is None:
            # 新记录在解析阶段已被物理删除（审计留痕），按审计明细区分两种情形：
            # not_invoice（非发票硬拒绝）/ duplicate（重复拦截，返回指向的已有记录）。
            from invoicing.models import Invoice as InvoiceModel

            parse_logs = db.query(AuditLog).filter(
                AuditLog.action == "PARSE",
            ).order_by(AuditLog.id.desc()).all()
            not_inv_log = None
            dup_log = None
            for log in parse_logs:
                detail = log.detail or {}
                if detail.get("discarded_invoice_id") != invoice_id:
                    continue
                if detail.get("result") == "not_invoice":
                    not_inv_log = log
                    break
                if detail.get("result") == "duplicate":
                    dup_log = log
                    break
            if not_inv_log is not None:
                # worker 已拒收并清理原件，这里把可行动的原因透传给调用方
                raise ValueError(_not_invoice_reason(data, kind))
            existing_id = (dup_log.detail or {}).get("duplicate_of_id") if dup_log else None
            existing = db.get(InvoiceModel, existing_id) if existing_id else None
            if existing is not None:
                return InvoiceOut.model_validate(existing, from_attributes=True)
            raise ValueError("发票重复且原记录不可用")

        # 非发票硬拒绝（WorkBuddy 边界）：空解析产生的全空壳记录（无号码/无金额/零置信）
        # 不返回给调用方——就地删除并给出可行动的错误提示，库内无残留。
        if inv.invoice_number is None and inv.total_amount is None and not inv.confidence_score:
            from invoicing.workflow.services import _cleanup_dependents_of

            get_storage().delete(key)
            reason = _not_invoice_reason(data, kind)
            write_audit(
                db, action="PARSE", invoice_id=inv.id, channel="mcp",
                detail={"result": "not_invoice", "rejected_by": "mcp_ingest", "file_type": kind},
            )
            _cleanup_dependents_of(db, inv.id)
            db.delete(inv)
            db.commit()
            raise ValueError(reason)
        return InvoiceOut.model_validate(inv, from_attributes=True)


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
    """保存常用公司（同税号更新；is_default 仅 kind=self，设默认清其他默认）。

    校验与 REST 对齐（tax_id 18 位 / kind 枚举 / name 非空 / 默认联动），非法输入抛 ValueError。
    """
    if not name or not name.strip():
        raise ValueError("公司名称不能为空")
    if not re.fullmatch(TAX_ID_PATTERN, tax_id):
        raise ValueError("税号必须为 18 位字母数字（[0-9A-Z]）")
    if kind not in {k.value for k in CompanyKind}:
        raise ValueError(f"非法类型: {kind}（可选 self/supplier/other）")
    if is_default and kind != CompanyKind.self.value:
        raise ValueError("is_default 仅适用于 kind=self")
    with SessionLocal() as db:
        info = db.query(CompanyInfo).filter(CompanyInfo.tax_id == tax_id).first()
        if info is None:
            info = CompanyInfo(tax_id=tax_id)
            db.add(info)
        # kind 改为非 self 时 is_default 强制 False，防脏数据（K1 修复轮教训）
        if kind != CompanyKind.self.value:
            is_default = False
        if is_default:
            db.query(CompanyInfo).filter(CompanyInfo.is_default.is_(True)).update({"is_default": False})
        info.name = name.strip()
        info.kind = kind
        info.is_default = is_default
        info.remark = remark
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


def _http_to_value_error(fn, *args, **kwargs):
    """REST service 层抛 HTTPException；MCP 工具语义转 ValueError（与既有工具约定一致）。"""
    from fastapi import HTTPException

    try:
        return fn(*args, **kwargs)
    except HTTPException as e:
        raise ValueError(str(e.detail)) from e


def invoice_update(
    invoice_id: int,
    invoice_number: str | None = None,
    issue_date: str | None = None,
    amount_without_tax: str | None = None,
    tax_amount: str | None = None,
    total_amount: str | None = None,
    total_amount_cn: str | None = None,
    seller_name: str | None = None,
    seller_tax_id: str | None = None,
    buyer_name: str | None = None,
    buyer_tax_id: str | None = None,
    invoice_type: str | None = None,
    review_note: str | None = None,
) -> InvoiceOut:
    """更新发票业务字段（人工复核纠正）；仅传入非 None 字段生效，状态变更走 review/verify。"""
    from invoicing.schemas.invoice import InvoiceUpdate

    body = InvoiceUpdate(
        invoice_number=invoice_number,
        issue_date=issue_date,
        amount_without_tax=amount_without_tax,
        tax_amount=tax_amount,
        total_amount=total_amount,
        total_amount_cn=total_amount_cn,
        seller_name=seller_name,
        seller_tax_id=seller_tax_id,
        buyer_name=buyer_name,
        buyer_tax_id=buyer_tax_id,
        invoice_type=invoice_type,
        review_note=review_note,
    )
    with SessionLocal() as db:
        inv = _http_to_value_error(
            services.update_invoice, db, None, invoice_id,
            body.model_dump(exclude_none=True), channel="mcp",
        )
        return InvoiceOut.model_validate(inv, from_attributes=True)


def invoice_delete(invoice_id: int) -> dict:
    """删除发票（审计全字段快照 + 原件清理）；不存在抛 ValueError。"""
    with SessionLocal() as db:
        return _http_to_value_error(
            services.delete_invoice, db, None, invoice_id, channel="mcp"
        )


def invoice_unblock(invoice_id: int) -> InvoiceOut:
    """人工放行被拦截发票：blocked → 待复核（清除重复标记）；非 blocked 抛 ValueError。"""
    with SessionLocal() as db:
        inv = _http_to_value_error(
            services.unblock_invoice, db, None, invoice_id, channel="mcp"
        )
        return InvoiceOut.model_validate(inv, from_attributes=True)


def invoice_classify(
    invoice_id: int,
    expense_type: str | None = None,
    cost_center: str | None = None,
    description: str | None = None,
) -> InvoiceOut:
    """费用归类：expense_type 不传时自动建议并落库（travel/office/entertainment/procurement/other）。"""
    from invoicing.models import Invoice
    from invoicing.parse.classify import EXPENSE_TYPES, suggest_expense_type

    with SessionLocal() as db:
        inv = db.get(Invoice, invoice_id)
        if inv is None:
            raise ValueError(f"发票不存在: {invoice_id}")
        if expense_type is not None and expense_type not in EXPENSE_TYPES:
            raise ValueError(f"非法费用类型: {expense_type}（可选 {', '.join(EXPENSE_TYPES)}）")
        if expense_type is None:
            expense_type = suggest_expense_type(inv.seller_name or "", inv.invoice_type)
        inv.expense_type = expense_type
        if cost_center is not None:
            inv.cost_center = cost_center
        if description is not None:
            inv.description = description
        write_audit(
            db, action="INVOICE_CLASSIFY", invoice_id=invoice_id, channel="mcp",
            detail={"expense_type": expense_type, "cost_center": cost_center, "description": description},
        )
        db.commit()
        return InvoiceOut.model_validate(inv, from_attributes=True)


def invoice_ai_review(invoice_id: int) -> InvoiceOut:
    """生成/重算发票复核预判（approve/reject/uncertain + 理由 + 置信度）。"""
    from invoicing.models import Invoice
    from invoicing.models.fields import utcnow
    from invoicing.parse.ai_review import predict_review

    with SessionLocal() as db:
        inv = db.get(Invoice, invoice_id)
        if inv is None:
            raise ValueError(f"发票不存在: {invoice_id}")
        verdict = predict_review(inv, db)
        if verdict is None:
            raise ValueError("预判不可用（LLM 未启用或调用失败），请人工复核")
        inv.ai_review_verdict = verdict.verdict
        inv.ai_review_reason = verdict.reason
        inv.ai_review_confidence = verdict.confidence
        inv.ai_reviewed_at = utcnow()
        write_audit(
            db, action="AI_REVIEW", invoice_id=invoice_id, channel="mcp",
            detail={"verdict": verdict.verdict, "reason": verdict.reason, "confidence": verdict.confidence},
        )
        db.commit()
        return InvoiceOut.model_validate(inv, from_attributes=True)


def invoice_report(month: str) -> str:
    """月度成本报表摘要（供数字员工汇报）：总额/张数/类型分布/部门分布。"""
    from invoicing.reports import monthly_cost

    with SessionLocal() as db:
        data = monthly_cost(db, month)
    lines = [
        f"{month} 月成本报表：共 {data['total_count']} 张，合计 {data['total_amount']} 元"
        f"（不含税 {data['total_without_tax']} 元 + 税额 {data['total_tax']} 元）",
        "按费用类型：" + "；".join(f"{k} {v}元" for k, v in data["by_type"].items()) if data["by_type"] else "（无）",
        "按部门/项目：" + "；".join(f"{k} {v}元" for k, v in data["by_center"].items()) if data["by_center"] else "（无）",
    ]
    return "\n".join(lines)


def invoice_health_report(month: str) -> str:
    """月度健康报告（P3/R3）：老板视角收口文本，供数字员工直接引用推送。"""
    from invoicing.reports import monthly_health

    with SessionLocal() as db:
        return monthly_health(db, month)


def receipt_ingest(file_path: str) -> list[dict]:
    """银行回单入库（P3/R1）：PDF/图片 → 存档 → 多张解析（分块规则+LLM 兜底）→ 逐条自动配对。

    一份 PDF 可含多张回单：每张入库一条（共享同一原件 URL），返回数组。
    """
    from pathlib import Path
    from uuid import uuid4

    from invoicing.fetch.filters import classify_attachment
    from invoicing.mcp.extract import _read_file
    from invoicing.models import BankReceipt
    from invoicing.parse.receipt import parse_receipts_bytes, suggest_pair
    from invoicing.storage import get_storage

    path = Path(file_path)
    data = _read_file(file_path)
    kind = classify_attachment(path.name, "", data)
    if kind not in ("PDF", "IMAGE"):
        raise ValueError(f"不支持的格式: {kind or '未知'}（仅 PDF/图片回单）")
    rows = parse_receipts_bytes(data, kind)
    if not rows:
        raise ValueError("未识别出银行回单信息（规则+LLM 均未提取出金额与户名）")
    key = f"tenant-default/receipts/{uuid4().hex}-{path.name}"
    get_storage().put(key, data, "application/octet-stream")
    out = []
    with SessionLocal() as db:
        for fields in rows:
            r = BankReceipt(
                file_url=key,
                file_type=kind,
                trade_date=fields.get("trade_date"),
                counterparty_name=fields.get("counterparty_name"),
                amount=fields.get("amount"),
                abstract=fields.get("abstract"),
                status="pending",
            )
            db.add(r)
            db.flush()
            suggested = suggest_pair(db, r.id)
            if suggested is not None:
                r.paired_invoice_id = suggested
                r.status = "paired"
            else:
                r.status = "unmatched"
            db.commit()
            out.append({
                "id": r.id,
                "trade_date": str(r.trade_date) if r.trade_date else None,
                "counterparty_name": r.counterparty_name,
                "amount": str(r.amount) if r.amount else None,
                "abstract": r.abstract,
                "paired_invoice_id": r.paired_invoice_id,
                "status": r.status,
            })
    return out


def receipt_list(month: str) -> list[dict]:
    """回单清单（P3/R1）：month=YYYY-MM。"""
    from invoicing.reports import receipts_in_month

    with SessionLocal() as db:
        rows = receipts_in_month(db, month)
        return [
            {
                "id": r.id,
                "trade_date": str(r.trade_date) if r.trade_date else None,
                "counterparty_name": r.counterparty_name,
                "amount": str(r.amount) if r.amount else None,
                "abstract": r.abstract,
                "paired_invoice_id": r.paired_invoice_id,
                "status": r.status,
            }
            for r in rows
        ]


def receipt_pair(receipt_id: int, invoice_id: int) -> dict:
    """手动配对回单与发票（覆盖自动建议）。"""
    from invoicing.models import BankReceipt, Invoice

    with SessionLocal() as db:
        r = db.get(BankReceipt, receipt_id)
        if r is None:
            raise ValueError(f"回单不存在: {receipt_id}")
        if db.get(Invoice, invoice_id) is None:
            raise ValueError(f"发票不存在: {invoice_id}")
        r.paired_invoice_id = invoice_id
        r.status = "paired"
        db.commit()
        return {"receipt_id": receipt_id, "paired_invoice_id": invoice_id, "status": "paired"}


def receipt_report(month: str) -> str:
    """回单/无票费用汇报（P3/R1+R2）：总额/张数 + 无票支出清单（催票数据源）。"""
    from invoicing.models import BankReceipt
    from invoicing.reports import _month_bounds

    start, end = _month_bounds(month)
    with SessionLocal() as db:
        rows = (
            db.query(BankReceipt)
            .filter(BankReceipt.trade_date >= start, BankReceipt.trade_date < end)
            .order_by(BankReceipt.trade_date, BankReceipt.id)
            .all()
        )
        total = sum((r.amount for r in rows if r.amount), 0)
        unmatched = [r for r in rows if r.paired_invoice_id is None]
    lines = [f"{month} 月回单：共 {len(rows)} 笔，合计 {total} 元"]
    if unmatched:
        lines.append(f"⚠️ 无票支出 {len(unmatched)} 笔（建议催交发票）：")
        for r in unmatched:
            lines.append(
                f"  - {r.trade_date} {r.counterparty_name or '未知对方'} {r.amount}元 {r.abstract or ''}"
            )
    else:
        lines.append("无票支出：无（回单均已配对发票）")
    return "\n".join(lines)
