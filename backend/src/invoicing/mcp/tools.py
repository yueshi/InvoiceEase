# invoicing/mcp/tools.py
"""MCP 工具实现：薄适配器，直接调用 workflow service 层。"""
import re
from datetime import date

from fastapi import HTTPException

from invoicing.audit import write_audit
from invoicing.db import SessionLocal
from invoicing.fetch.service import poll_mailbox
from invoicing.models import (
    AuditAction,
    AuditLog,
    CompanyInfo,
    CompanyKind,
    Mailbox,
    ReceiptUpload,
    Role,
    User,
)
from invoicing.schemas.company_info import CompanyInfoOut, TAX_ID_PATTERN
from invoicing.schemas.invoice import InvoiceListResponse, InvoiceOut
from invoicing.schemas.mailbox import PollResultOut
from invoicing.workflow import services
from invoicing.workers.queue import enqueue_receipt_parse_sync


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
    expense_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> InvoiceListResponse:
    with SessionLocal() as db:
        # 关键字参数：避免签名扩展（如新增 expense_type）导致位置参数错位
        result = services.list_invoices(
            db, _mcp_admin_user(), status=status, date_from=date_from, date_to=date_to,
            keyword=keyword, expense_type=expense_type, page=page, page_size=page_size,
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
    bank_account: str | None = None,
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
        if bank_account is not None:
            info.bank_account = bank_account.strip() or None
        write_audit(
            db, action=AuditAction.CONFIG_CHANGE.value, channel="mcp",
            detail={"entity": "company_info", "tax_id": tax_id},
        )
        db.commit()
        return CompanyInfoOut.model_validate(info, from_attributes=True)


# ---- 报销（Agent 对话式报销，P0）------------------------------------------


def _money(v) -> str | None:
    """金额统一 2 位小数（Decimal 直接 str 会丢尾零，如 200.00 → 200）。"""
    return f"{v:.2f}" if v is not None else None


def _mcp_real_user(db) -> User:
    """MCP 静态 token = 管理员通道，但**报销需要真实归属人**：

    合成用户 id=0 会违反外键（expense_claims.applicant_id → users.id，PRAGMA
    foreign_keys=ON）。解析库内真实管理员（按 id 最小），审计与归属都更可追溯。
    """
    from invoicing.models import Role

    user = (
        db.query(User)
        .filter(User.role == Role.admin.value)
        .order_by(User.id)
        .first()
    )
    return user or _mcp_admin_user()


def expense_create(title: str, remark: str | None = None, claim_type: str | None = None) -> dict:
    """创建报销单（草稿）：claim_type 选择单据类型（travel 差旅/procurement 采购/
    entertainment 招待/office 办公/welfare 福利/other 其他），事项默认继承该类型。"""
    from invoicing.workflow import expenses as svc

    with SessionLocal() as db:
        claim = svc.create_claim(db, _mcp_real_user(db), title, remark, claim_type)
        return {
            "id": claim.id, "claim_no": claim.claim_no, "status": claim.status,
            "title": claim.title, "claim_type": claim.claim_type,
        }


def expense_add_entry(claim_id: int, entry_type: str, title: str,
                      occurred_on: str | None = None, scene_fields: dict | None = None,
                      note: str | None = None) -> dict:
    """新建报销事项（费用明细行）：凭证挂到事项下；按类型校验场景要素。

    entry_type: travel（差旅，需城市+起止日期）/ procurement（采购，建议合同号订单号）/
    entertainment（招待，需对象+人数）/ office / other。
    """
    from datetime import date as _date

    from invoicing.workflow import expenses as svc

    occurred = _date.fromisoformat(occurred_on) if occurred_on else None
    with SessionLocal() as db:
        entry = svc.create_entry(
            db, _mcp_real_user(db), claim_id, entry_type, title, occurred, scene_fields, note
        )
        return {
            "entry_id": entry.id, "claim_id": entry.claim_id, "entry_type": entry.entry_type,
            "title": entry.title, "amount": _money(entry.amount),
        }


def expense_add_invoices(claim_id: int, entry_id: int, invoice_numbers: list[str],
                         expense_type: str = "other", note: str | None = None) -> dict:
    """按发票号码批量加入报销单（自动校验：一票一报/已验真/未拦截/归属范围）。

    返回逐条结果（success/error），互不影响——便于 Agent 一次性处理多张票。
    """
    from invoicing.models import Invoice
    from invoicing.workflow import expenses as svc

    results = []
    with SessionLocal() as db:
        user = _mcp_real_user(db)
        for number in invoice_numbers:
            inv = db.query(Invoice).filter(Invoice.invoice_number == number).first()
            if inv is None:
                results.append({"invoice_number": number, "success": False, "error": "发票不存在"})
                continue
            try:
                item = svc.add_invoice(db, user, claim_id, inv.id, entry_id, expense_type, note)
                results.append({
                    "invoice_number": number, "success": True,
                    "amount": _money(item.amount), "item_id": item.id,
                })
            except ValueError as e:
                results.append({"invoice_number": number, "success": False, "error": str(e)})
        claim = svc._get_claim(db, claim_id)
        return {
            "claim_id": claim.id, "claim_no": claim.claim_no, "entry_id": entry_id,
            "total_amount": _money(claim.total_amount), "results": results,
        }


def expense_submit(claim_id: int) -> dict:
    """提交报销单进入审批（需已有明细）。"""
    from invoicing.workflow import expenses as svc

    with SessionLocal() as db:
        claim = svc.submit_claim(db, _mcp_real_user(db), claim_id)
        return {"id": claim.id, "claim_no": claim.claim_no, "status": claim.status,
                "total_amount": _money(claim.total_amount)}


def expense_list(status: str | None = None, claim_type: str | None = None) -> list[dict]:
    """报销单列表（可按状态与单据类型过滤）。"""
    from invoicing.workflow import expenses as svc

    with SessionLocal() as db:
        rows = svc.list_claims(db, _mcp_real_user(db), status, claim_type)
        return [
            {
                "id": c.id, "claim_no": c.claim_no, "title": c.title,
                "claim_type": c.claim_type, "status": c.status,
                "applicant_id": c.applicant_id, "total_amount": _money(c.total_amount),
                "item_count": svc.item_count(db, c.id),
                "entries": [
                    {"entry_id": e.id, "entry_type": e.entry_type, "title": e.title,
                     "amount": _money(e.amount), "item_count": svc.item_count_of_entry(db, e.id)}
                    for e in svc.list_entries(db, c.id)
                ],
                "submitted_at": str(c.submitted_at) if c.submitted_at else None,
                "rejected_reason": c.rejected_reason,
            }
            for c in rows
        ]


def expense_approve(claim_id: int, action: str = "approve", reason: str | None = None) -> dict:
    """审批报销单：action=approve/reject（reject 必填 reason）。财务通道。"""
    from invoicing.workflow import expenses as svc

    with SessionLocal() as db:
        user = _mcp_real_user(db)
        if action == "approve":
            claim = svc.approve_claim(db, user, claim_id)
        elif action == "reject":
            claim = svc.reject_claim(db, user, claim_id, reason or "")
        else:
            raise ValueError(f"非法审批动作: {action}（可选 approve/reject）")
        return {"id": claim.id, "claim_no": claim.claim_no, "status": claim.status,
                "rejected_reason": claim.rejected_reason}


def expense_eligible_invoices(limit: int = 50) -> list[dict]:
    """可报销发票池（已验真、未拦截、未占用），供 Agent 建单选票。"""
    from invoicing.workflow import expenses as svc

    with SessionLocal() as db:
        rows = svc.eligible_invoices(db, _mcp_real_user(db))[:limit]
        return [
            {
                "id": i.id, "invoice_number": i.invoice_number,
                "issue_date": str(i.issue_date) if i.issue_date else None,
                "seller_name": i.seller_name, "total_amount": _money(i.total_amount),
            }
            for i in rows
        ]


def bank_account_list() -> list[dict]:
    """常用企业银行账号列表（本司账户，回单「本司账户行」判定用）。"""
    from invoicing.models import BankAccount

    with SessionLocal() as db:
        return [
            {
                "id": a.id,
                "account_no": a.account_no,
                "account_name": a.account_name,
                "bank_name": a.bank_name,
                "remark": a.remark,
                "is_default": a.is_default,
                "enabled": a.enabled,
            }
            for a in db.query(BankAccount).order_by(BankAccount.id).all()
        ]


def bank_account_save(
    account_no: str,
    account_name: str | None = None,
    bank_name: str | None = None,
    bank_code: str | None = None,
    remark: str | None = None,
    is_default: bool = False,
    enabled: bool = True,
) -> dict:
    """新增/更新本司银行账号（同账号更新）；账号规范化去空格连字符，须 6-32 位数字。"""
    from invoicing.models import BankAccount

    normalized = re.sub(r"[\s\-]", "", account_no or "")
    if not re.fullmatch(r"[0-9]{6,32}", normalized):
        raise ValueError("账号须为 6-32 位数字")
    with SessionLocal() as db:
        acc = db.query(BankAccount).filter(BankAccount.account_no == normalized).first()
        created = acc is None
        if acc is None:
            acc = BankAccount(account_no=normalized)
            db.add(acc)
        if is_default:
            db.query(BankAccount).filter(BankAccount.is_default.is_(True)).update({"is_default": False})
        acc.account_name = (account_name or "").strip() or None
        acc.bank_name = (bank_name or "").strip() or None
        if bank_code:
            acc.bank_code = bank_code
        elif acc.bank_code is None and acc.bank_name:
            from invoicing.parse.bank_templates import detect_bank_code

            acc.bank_code = detect_bank_code(acc.bank_name)
        acc.remark = remark
        acc.is_default = is_default
        acc.enabled = enabled
        write_audit(
            db, action=AuditAction.CONFIG_CHANGE.value, channel="mcp",
            detail={"entity": "bank_account", "account_no": normalized, "created": created},
        )
        db.commit()
        return {
            "id": acc.id, "account_no": acc.account_no, "account_name": acc.account_name,
            "bank_name": acc.bank_name, "remark": acc.remark,
            "is_default": acc.is_default, "enabled": acc.enabled,
        }


def bank_account_delete(id: int) -> dict:
    """删除本司银行账号；不存在抛 ValueError。"""
    from invoicing.models import BankAccount

    with SessionLocal() as db:
        acc = db.get(BankAccount, id)
        if acc is None:
            raise ValueError(f"账号不存在: {id}")
        db.delete(acc)
        write_audit(
            db, action=AuditAction.CONFIG_CHANGE.value, channel="mcp",
            detail={"entity": "bank_account", "id": id, "deleted": True},
        )
        db.commit()
        return {"ok": True}


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


def sales_invoice_import(file_path: str) -> dict:
    """导入已开票（销项，文件解析）：XML/OFD/PDF → 入库为销项票，红票自动关联原蓝票。"""
    from pathlib import Path

    from invoicing.mcp.extract import _read_file
    from invoicing.workflow import sales as sales_svc

    path = Path(file_path)
    data = _read_file(file_path)
    with SessionLocal() as db:
        results = sales_svc.import_sales_files(db, _mcp_real_user(db), [(path.name, data)])
        return results[0]


def sales_invoice_import_list(file_path: str) -> dict:
    """导入已开票（清单批量）：开票系统导出的 CSV/Excel；含"原发票号码"列则自动关联红票。"""
    from pathlib import Path

    from invoicing.mcp.extract import _read_file
    from invoicing.workflow import sales as sales_svc

    path = Path(file_path)
    data = _read_file(file_path)
    with SessionLocal() as db:
        return sales_svc.import_sales_list(db, _mcp_real_user(db), data, path.name)


def red_invoice_list() -> list[dict]:
    """未关联原蓝票的红字票（销项退款场景待人工补关联）。"""
    from invoicing.workflow import sales as sales_svc

    with SessionLocal() as db:
        return [
            {"id": i.id, "invoice_number": i.invoice_number, "buyer_name": i.buyer_name,
             "total_amount": _money(i.total_amount),
             "issue_date": str(i.issue_date) if i.issue_date else None}
            for i in sales_svc.unlinked_red_invoices(db)
        ]


def red_invoice_link(red_invoice_id: int, original_invoice_id: int) -> dict:
    """人工补关联：把红字票挂到原蓝票。"""
    from invoicing.workflow import sales as sales_svc

    with SessionLocal() as db:
        inv = sales_svc.link_red_invoice(db, _mcp_real_user(db), red_invoice_id, original_invoice_id)
        return {"ok": True, "invoice_id": inv.id, "original_invoice_id": inv.original_invoice_id}


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


def receipt_ingest(file_path: str) -> dict:
    """银行回单入库（R1.1 批次异步模式）：存档 → 建批次 → 后台解析 → 立即返回批次号。

    一份 PDF 可含多张回单，解析（分块规则+LLM 兜底）可达分钟级，同步执行会拖垮
    Agent 客户端并诱发重复提交——WorkBuddy 应立即拿到批次号，稍后用
    receipt_upload_status 轮询；解析完成后回单出现在 receipt_list。同一文件
    重复提交被 file_hash 唯一约束拒绝。
    """
    import hashlib
    from pathlib import Path
    from uuid import uuid4

    from invoicing.fetch.filters import classify_attachment
    from invoicing.mcp.extract import _read_file
    from invoicing.storage import get_storage

    path = Path(file_path)
    data = _read_file(file_path)
    kind = classify_attachment(path.name, "", data)
    if kind not in ("PDF", "IMAGE"):
        raise ValueError(f"不支持的格式: {kind or '未知'}（仅 PDF/图片回单）")

    file_hash = hashlib.sha256(data).hexdigest()
    with SessionLocal() as db:
        existing = db.query(ReceiptUpload).filter(ReceiptUpload.file_hash == file_hash).first()
        if existing is not None:
            state = "解析中" if existing.status == "parsing" else (
                f"已入库 {existing.receipt_count} 张" if existing.status == "parsed" else "解析失败"
            )
            raise ValueError(
                f"该回单文件已上传过（批次 #{existing.id}，{state}），请勿重复提交；"
                f"用 receipt_upload_status({existing.id}) 查询进度"
            )
        key = f"tenant-default/receipts/{uuid4().hex}-{path.name}"
        get_storage().put(key, data, "application/octet-stream")
        up = ReceiptUpload(
            file_hash=file_hash, file_url=key, file_type=kind, status="parsing"
        )
        db.add(up)
        db.commit()
        upload_id = up.id
    enqueue_receipt_parse_sync(upload_id)
    return {
        "upload_id": upload_id,
        "status": "parsing",
        "message": "已接收，后台解析中；用 receipt_upload_status 轮询，完成后 receipt_list 查看",
    }


def receipt_upload_status(upload_id: int) -> dict:
    """回单上传批次解析状态（receipt_ingest 异步模式的配套轮询工具）。"""
    with SessionLocal() as db:
        up = db.get(ReceiptUpload, upload_id)
        if up is None:
            raise ValueError(f"批次不存在: {upload_id}")
        return {
            "id": up.id,
            "status": up.status,
            "receipt_count": up.receipt_count,
            "error": up.error,
            "created_at": str(up.created_at),
            "parsed_at": str(up.parsed_at) if up.parsed_at else None,
        }


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
                "amount": _money(r.amount),
                "abstract": r.abstract,
                "direction": r.direction,
                "needs_review": r.needs_review,
                "quality_issues": r.quality_issues,
                "bank_code": r.bank_code,
                "page_no": r.page_no,
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
