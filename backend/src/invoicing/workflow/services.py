# workflow/services.py
from decimal import Decimal
from enum import Enum

from sqlalchemy import or_
from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.models import (
    AuditLog, BankReceipt, ExpenseEntry, ExpenseItem, Invoice, InvoiceStatus, Role, User,
)
from invoicing.models.fields import utcnow
from invoicing.schemas.invoice import InvoiceListResponse
from invoicing.storage import get_storage
from invoicing.workflow.state import transition
from invoicing.workers.queue import enqueue_parse_sync, enqueue_verify_sync


def scoped_invoices(db: Session, current_user: User):
    """发票查询的 RBAC 范围（FRD §3.5.2）：员工仅本人，财务/主管/管理员全公司。

    所有按发票出数的读路径（列表/详情/工作台统计）都必须经此收敛，避免漏网。
    """
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
    expense_type: str | None = None,
    invoice_direction: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> InvoiceListResponse:
    q = scoped_invoices(db, current_user)
    if status:
        q = q.filter(Invoice.status == status)
    if invoice_direction:
        q = q.filter(Invoice.invoice_direction == invoice_direction)
    if expense_type:
        # unclassified = 未归类（规则未命中的留空发票）
        if expense_type == "unclassified":
            q = q.filter(Invoice.expense_type.is_(None))
        else:
            q = q.filter(Invoice.expense_type == expense_type)
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
    _attach_submitted_by_name(db, items)
    return InvoiceListResponse(items=items, total=total, page=page, page_size=page_size)


def _attach_submitted_by_name(db: Session, items: list[Invoice]) -> None:
    """批量附挂提交人用户名（瞬态属性，非数据库列）——单次查询，避免逐行 N+1。

    InvoiceOut.submitted_by_name 经 from_attributes 读取该属性；未附挂时取默认 None。
    """
    ids = {i.user_id for i in items if i.user_id}
    name_map: dict[int, str] = {}
    if ids:
        name_map = dict(db.query(User.id, User.username).filter(User.id.in_(ids)).all())
    for i in items:
        i.submitted_by_name = name_map.get(i.user_id) if i.user_id else None


def get_invoice(db: Session, current_user: User, invoice_id: int) -> Invoice:
    inv = scoped_invoices(db, current_user).filter(Invoice.id == invoice_id).first()
    if inv is None:
        from fastapi import HTTPException

        raise HTTPException(404, "发票不存在")
    _attach_submitted_by_name(db, [inv])
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
    inv.reviewed_at = utcnow()
    # B1：审计记录复核时的 AI 预判对比（观察期改判率数据源，P3 信任仪表盘用）
    write_audit(
        db, action="REVIEW", user_id=current_user.id, invoice_id=inv.id, channel="web",
        detail={
            "action": action,
            "note": note,
            "ai_verdict": inv.ai_review_verdict,
            "ai_confidence": inv.ai_review_confidence,
        },
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
    # 本地队列模式内联完成验真：刷新会话后再返回，响应反映验真后状态（redis 模式为尽力读取当前值）
    db.refresh(inv)
    return inv


_SNAPSHOT_COLS = (
    "invoice_code", "invoice_number", "issue_date", "amount_without_tax", "tax_amount",
    "total_amount", "total_amount_cn", "seller_name", "seller_tax_id", "buyer_name",
    "buyer_tax_id", "invoice_type", "file_url", "file_type", "xml_url", "parse_source",
    "confidence_score", "verify_status", "status", "email_message_id",
)


_AMOUNT_FIELDS = ("amount_without_tax", "tax_amount", "total_amount", "total_amount_cn")

# C3 统一门控集合：金额字段 + 关键业务字段（影响校验重算 + AI 预判作废）。
# 单一真相来源，避免 update_invoice 内"金额/敏感"两套集合不一致导致陈旧错误。
_REVIEW_SENSITIVE_FIELDS: frozenset[str] = frozenset(_AMOUNT_FIELDS) | frozenset({
    "buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id",
    "invoice_type", "issue_date",
})


class RevalidateState(str, Enum):
    """_revalidate_invoice 三态结果（C2/C7 修复）。

    - VALID: 字段齐全且校验通过 → 应清空 inv.validation_errors + 可触发自动 transition
    - INVALID: 字段齐全但校验出错误 → 应写入新 validation_errors
    - INCOMPLETE: 关键字段缺失无法校验 → 保留旧 validation_errors（不变）
    """

    VALID = "valid"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"


def _revalidate_invoice(inv: Invoice) -> tuple[RevalidateState, list[dict] | None]:
    """重算 validation_errors（C2/C7 修复：返回三态，调用方据此决定清空/写入/保留）。

    Returns:
        (RevalidateState.VALID, []) 字段齐全且校验通过
        (RevalidateState.INVALID, errors) 字段齐全但校验出错误
        (RevalidateState.INCOMPLETE, None) 关键字段缺失无法校验

    调用方根据状态决定是否清空 inv.validation_errors / 触发自动状态恢复。
    """
    from invoicing.parse.schemas import ParsedInvoice
    from invoicing.parse.validation import validate

    if (
        inv.invoice_number is None
        or inv.issue_date is None
        or inv.amount_without_tax is None
        or inv.tax_amount is None
        or inv.total_amount is None
    ):
        return RevalidateState.INCOMPLETE, None  # 字段不全，调用方应保留旧错
    parsed = ParsedInvoice(
        invoice_number=inv.invoice_number,
        issue_date=inv.issue_date,
        amount_without_tax=inv.amount_without_tax,
        tax_amount=inv.tax_amount,
        total_amount=inv.total_amount,
        total_amount_cn=inv.total_amount_cn or "",
        seller_name=inv.seller_name or "",
        seller_tax_id=inv.seller_tax_id or "",
        buyer_name=inv.buyer_name or "",
        buyer_tax_id=inv.buyer_tax_id or "",
        confidence_score=inv.confidence_score or 0.0,
        parse_source=inv.parse_source or "",
    )
    errs = validate(parsed)
    if not errs:
        return RevalidateState.VALID, []
    return RevalidateState.INVALID, [e.model_dump() for e in errs]


def upload_invoice(db: Session, current_user: User, filename: str, data: bytes) -> Invoice:
    """员工交票上传（M3）：仅 PDF/OFD/XML 原件；图片 422 合规引导；user_id 归属上传者。

    复用 ingest 管线（存档 → 解析 → 验真；enqueue_parse_sync 本地模式内联）。
    重复票按 ingest 语义返回已有记录（新记录已被物理删除，审计留痕）。
    """
    from uuid import uuid4

    from fastapi import HTTPException

    from invoicing.fetch.filters import classify_attachment

    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(422, "文件过大（上限 20MB），请发送至公司收票邮箱")
    kind = classify_attachment(filename, "", data)
    if kind == "IMAGE":
        raise HTTPException(
            422,
            "合规要求仅接收发票原件（PDF/OFD/XML）。请上传发票原件文件，或将原件发送至公司收票邮箱由系统自动收取",
        )
    if kind not in ("PDF", "OFD", "XML"):
        raise HTTPException(422, f"不支持的格式: {kind or '未知'}（仅接收 PDF/OFD/XML 原件）")

    key = f"tenant-default/upload/{uuid4().hex}-{filename}"
    get_storage().put(key, data, "application/octet-stream")

    inv = Invoice(
        file_url=key,
        file_type=kind,
        status=InvoiceStatus.parsing.value,
        email_subject="员工上传",
        user_id=current_user.id,
    )
    db.add(inv)
    db.flush()
    write_audit(
        db, action="INVOICE_UPLOAD", user_id=current_user.id, invoice_id=inv.id, channel="web",
        detail={"filename": filename, "file_type": kind, "size": len(data)},
    )
    db.commit()
    invoice_id = inv.id
    enqueue_parse_sync(invoice_id)
    # 重复拦截：新记录可能已被物理删除（审计留痕），返回已有记录
    db.expire_all()
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        dup_log = (
            db.query(AuditLog)
            .filter(AuditLog.invoice_id == invoice_id, AuditLog.action == "PARSE")
            .order_by(AuditLog.id.desc())
            .first()
        )
        existing_id = (dup_log.detail or {}).get("duplicate_of_id") if dup_log else None
        existing = db.get(Invoice, existing_id) if existing_id else None
        if existing is not None:
            return existing
        raise HTTPException(409, "发票重复且原记录不可用")
    return inv


def update_invoice(db: Session, current_user: User | None, invoice_id: int, data: dict, *, channel: str | None = None) -> Invoice:
    """更新发票业务字段（人工复核纠正）；状态变更走 review/verify 专用端点。

    current_user 可为 None（MCP 通道无用户上下文，审计 user_id 留空）。
    channel: 显式传入审计通道；为 None 时按 current_user 推断（向后兼容）。

    C2/C3/C4 修复：
    - 校验门控从 _AMOUNT_FIELDS 扩展到 _REVIEW_SENSITIVE_FIELDS（统一门控）
    - _revalidate_invoice 三态返回；VALID 触发自动 blocked/pending_review → parsed + enqueue_verify
    """
    from fastapi import HTTPException

    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, "发票不存在")
    changed: dict = {}
    for field, value in data.items():
        if value is None:
            continue  # 显式 null 不落库（str 清空用空串表达，防 NOT NULL 字段崩）
        old = getattr(inv, field)
        if old != value:
            setattr(inv, field, value)
            changed[field] = str(value)

    # C2/C7/C4：关键字段变更 → 重算校验并按三态分支
    revalidate_state: RevalidateState | None = None
    revalidate_errors: list[dict] | None = None
    prior_had_errors = bool(inv.validation_errors)
    if any(f in changed for f in _REVIEW_SENSITIVE_FIELDS):
        revalidate_state, revalidate_errors = _revalidate_invoice(inv)
        if revalidate_state is RevalidateState.VALID:
            inv.validation_errors = None
        elif revalidate_state is RevalidateState.INVALID:
            inv.validation_errors = revalidate_errors
        # INCOMPLETE: 保留旧错（不写入）

    # C6：invoice_type 变更时重判红字（C6 修复入口）
    if "invoice_type" in changed:
        from invoicing.parse.red_flag import detect_red_invoice

        new_red = detect_red_invoice(None, inv.invoice_type)
        inv.red_flag = new_red

    # B4：关键字段变更 → 既有 AI 预判基于旧数据作废（理由不得基于旧数据），班表下轮重算
    if changed and any(f in _REVIEW_SENSITIVE_FIELDS for f in changed):
        inv.ai_review_verdict = None
        inv.ai_review_reason = None
        inv.ai_review_confidence = None
        inv.ai_reviewed_at = None

    # C4：校验由非空变空 → 自动状态恢复（blocked/pending_review → parsed）
    auto_recovered = False
    if (
        revalidate_state is RevalidateState.VALID
        and prior_had_errors
        and inv.status in (InvoiceStatus.pending_review.value, InvoiceStatus.blocked.value)
    ):
        try:
            transition(inv, InvoiceStatus.parsed.value)
            auto_recovered = True
        except ValueError:
            pass  # 状态机拒绝时忽略（理论上 state.py 已允许 blocked → parsed）

    if changed:
        effective_channel = channel or ("web" if current_user else "mcp")
        user_id = current_user.id if current_user else None
        detail: dict = {"changed": changed}
        if revalidate_state is not None:
            detail["validation"] = revalidate_state.value
            if revalidate_state is RevalidateState.VALID:
                detail["validation_errors_cleared"] = True
        if auto_recovered:
            detail["auto_recovered_to"] = inv.status
        write_audit(
            db, action="INVOICE_UPDATE", user_id=user_id,
            invoice_id=inv.id, channel=effective_channel,
            detail=detail,
        )

    db.commit()

    # C4：状态自动恢复后，若进 parsed 且无红字 → enqueue_verify（与 worker 解析后行为一致）
    if auto_recovered and not inv.red_flag:
        enqueue_verify_sync(inv.id)

    return inv




def unblock_invoice(db: Session, current_user: User | None, invoice_id: int, *, channel: str | None = None) -> Invoice:
    """人工放行：blocked → 待复核（清除重复标记与悬空引用）。

    current_user 可为 None（MCP 通道无用户上下文）。
    channel: 显式传入审计通道；为 None 时按 current_user 推断（向后兼容）。

    C10 修复：放行后若从未验真过（verify_status=pending），强制 enqueue_verify_sync，
    满足「验真覆盖率 100%」合规硬约束。否则被误判重复的票放行后可绕过验真提交。
    """
    from fastapi import HTTPException

    from invoicing.models import VerifyStatus

    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, "发票不存在")
    if inv.status != InvoiceStatus.blocked.value:
        raise HTTPException(409, "仅已拦截（blocked）状态的发票可放行")
    inv.duplicate_flag = False
    inv.duplicate_of_id = None
    transition(inv, InvoiceStatus.pending_review.value)
    # C10：放行后强制验真（除非已经验真过：passed/failed）
    verify_dispatched = inv.verify_status == VerifyStatus.pending.value
    effective_channel = channel or ("web" if current_user else "mcp")
    user_id = current_user.id if current_user else None
    write_audit(
        db, action="UNBLOCK", user_id=user_id,
        invoice_id=inv.id, channel=effective_channel,
        detail={
            "from": "blocked",
            "to": "pending_review",
            "verify_dispatched": verify_dispatched,
        },
    )
    db.commit()
    if verify_dispatched:
        enqueue_verify_sync(inv.id)
    return inv

def _cleanup_dependents_of(db: Session, invoice_id: int, *, unlink_receipts: bool = False) -> int:
    """清理指向 `invoice_id` 的悬空引用（C1 根因修复；终审 1a 补回单）。

    删除/重复拦截前必须先调本函数——否则删掉原票后，依赖票的 duplicate_of_id
    仍指向不存在的 id，前端显示「重复：是」但跳转 404。

    回单同理（`unlink_receipts=True` 时）：调用方即将物理删除该发票，而 FK
    `ON DELETE SET NULL` 只清 paired_invoice_id，status 会停在 "paired"
    → 回单在催票队列（未配对）里却顶着「已配对」标签，前后自相矛盾。
    **发票保留的调用方（重复拦截里清 surviving 原票）不要传 True**——
    那会把仍然有效的配对静默拆掉。

    Returns:
        清理的依赖票行数（affected dependents，不含回单）。
    """
    dependents = db.query(Invoice).filter(Invoice.duplicate_of_id == invoice_id).all()
    for dep in dependents:
        dep.duplicate_flag = False
        dep.duplicate_of_id = None
        if dep.status == InvoiceStatus.blocked.value:
            transition(dep, InvoiceStatus.pending_review.value)
    if unlink_receipts:
        receipts = db.query(BankReceipt).filter(BankReceipt.paired_invoice_id == invoice_id).all()
        for r in receipts:
            r.paired_invoice_id = None
            r.status = "unmatched"
    return len(dependents)


def delete_invoice(db: Session, current_user: User | None, invoice_id: int, *, channel: str | None = None) -> dict:
    """删除发票：先写全字段快照审计（合规留痕），再删原件与记录。

    channel: 显式传入审计通道；为 None 时按 current_user 推断（向后兼容）。
    """
    import logging

    from fastapi import HTTPException

    logger = logging.getLogger(__name__)
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, "发票不存在")
    snapshot = {col: str(getattr(inv, col)) for col in _SNAPSHOT_COLS}
    effective_channel = channel or ("web" if current_user else "mcp")
    user_id = current_user.id if current_user else None
    write_audit(
        db, action="INVOICE_DELETE", user_id=user_id,
        invoice_id=inv.id, channel=effective_channel,
        detail={"snapshot": snapshot},
    )
    # 悬空引用清理：先清 dependents/回单再删本记录（FK CASCADE 兜底，本函数保证应用层一致）
    _cleanup_dependents_of(db, invoice_id, unlink_receipts=True)
    storage = get_storage()
    for key in (inv.file_url, inv.xml_url):
        if not key:
            continue
        try:
            storage.delete(key)
        except Exception:  # 原件删除失败不阻塞记录删除（审计已留痕，文件残留可清理）
            logger.warning("原件删除失败 invoice_id=%s key=%s", invoice_id, key, exc_info=True)
    db.delete(inv)
    db.commit()
    return {"ok": True}


# ===== P0-1 validate_expense 子函数（v1.1 §4.1-4.4 检查项） =====
# ponytail: 此节是 P0-1 阶段逐步累加的 6 个 _check_* 函数 + validate_expense 聚合。
# 加新 check 时遵循"小写 _check_ 开头、返回 list[ValidationError]、error/warning 二级"约定。

def _check_amount(amount: Decimal, threshold: Decimal | None = None) -> list:
    """金额合规：> 0、≤ 阈值；阈值边界给 warning（v1.1 §4.2 边界 → 转人工）。

    入参 amount / threshold 都用 Decimal；threshold 不传时取 settings.large_amount_threshold。
    """
    from invoicing.workflow.validation import ValidationError
    from invoicing.config import settings as _settings

    threshold = threshold if threshold is not None else _settings.large_amount_threshold
    errs = []
    if amount <= _settings.amount_floor:
        errs.append(ValidationError(
            code="AMOUNT_NOT_POSITIVE",
            message=f"金额 {amount} 必须大于 {_settings.amount_floor}",
            severity="error",
        ))
        return errs
    if amount > threshold:
        errs.append(ValidationError(
            code="AMOUNT_TOO_LARGE",
            message=f"金额 {amount} 超过大额阈值 {threshold}",
            severity="error",
        ))
    elif amount == threshold:
        errs.append(ValidationError(
            code="AMOUNT_AT_THRESHOLD",
            message=f"金额正好等于大额阈值 {threshold}，需人工复核",
            severity="warning",
        ))
    return errs


def _check_tax_sum(*, total: Decimal, without_tax: Decimal, tax: Decimal) -> list:
    """价税合计勾稽：total = without_tax + tax；容差 settings.tax_sum_tolerance（v1.1 §4.1）。

    全部入参用 Decimal；零值算 OK（无税或空单据）。
    """
    from invoicing.workflow.validation import ValidationError
    from invoicing.config import settings as _settings

    diff = abs(total - (without_tax + tax))
    if diff > _settings.tax_sum_tolerance:
        return [ValidationError(
            code="TAX_SUM_MISMATCH",
            message=f"价税合计 {total} ≠ 不含税 {without_tax} + 税额 {tax}，差 {diff}",
            severity="error",
        )]
    return []


def _check_cross_duplicate(db: Session, claim) -> list:
    """同报销单内发票号查重（v1.1 §4.1 跨票号重复）。

    跳过 invoice_number 为空字符串的（无票支出/人工凭证不参与去重）。
    ponytail: 当前模型无 backref，逐 entry 显式查询；量大后改一次性 JOIN。
    """
    from collections import Counter
    from invoicing.workflow.validation import ValidationError

    nums: list[str] = []
    items = (
        db.query(ExpenseItem.invoice_id)
        .filter(ExpenseItem.claim_id == claim.id, ExpenseItem.invoice_id.isnot(None))
        .all()
    )
    invoice_ids = {row[0] for row in items if row[0] is not None}
    if not invoice_ids:
        return []
    inv_numbers = (
        db.query(Invoice.invoice_number)
        .filter(Invoice.id.in_(invoice_ids), Invoice.invoice_number.isnot(None))
        .all()
    )
    nums = [n for (n,) in inv_numbers if n]
    counts = Counter(nums)
    dups = sorted(n for n, c in counts.items() if c > 1)
    if not dups:
        return []
    return [ValidationError(
        code="DUPLICATE_INVOICE_NUMBER",
        message=f"同报销单内发票号重复：{dups}",
        severity="error",
    )]


def _check_vouchers(db: Session, claim) -> list:
    """v1.1 §4.4 凭证齐全：claim 至少 1 个 entry，每个 entry 至少 1 张 item。

    ponytail: 当前实现为单次 N+1 查询（先 entries 再 items）；量大后改一次性 JOIN。
    """
    from invoicing.workflow.validation import ValidationError

    entries = db.query(ExpenseEntry).filter(ExpenseEntry.claim_id == claim.id).all()
    errs: list = []
    if not entries:
        errs.append(ValidationError(
            code="MISSING_VOUCHER",
            message=f"报销单 {claim.claim_no} 没有事项（entries）",
            severity="error",
        ))
        return errs
    for entry in entries:
        n_items = (
            db.query(ExpenseItem)
            .filter(ExpenseItem.claim_id == claim.id,
                    ExpenseItem.entry_id == entry.id)
            .count()
        )
        if n_items == 0:
            errs.append(ValidationError(
                code="MISSING_VOUCHER",
                message=f"事项「{entry.title or entry.id}」缺少凭证（item）",
                severity="error",
            ))
    return errs


def _check_budget(db: Session, *, tenant_id: str, dept: str | None,
                  category: str, period: str, amount: Decimal) -> list:
    """v1.1 §4.3 金额阈值节点 + §9.6 预算：超额 FAIL；无预算配置 warning；缺 dept warning。

    ponytail: dept=NULL（历史数据）→ NO_DEPT warning（不阻塞，等运维补 dept）。
    """
    from invoicing.workflow.validation import ValidationError
    from invoicing.workflow.budget_service import query_budget, check_available

    errs: list = []
    if not dept:
        errs.append(ValidationError(
            code="NO_DEPT",
            message="报销单缺部门字段，无法做预算检查（spec §4.3）",
            severity="warning",
        ))
        # 缺 dept 时跳过 budget check（不阻塞）
        return errs

    view = query_budget(db, tenant_id=tenant_id, dept=dept,
                         category=category, period=period)
    if view.allocated == 0:
        errs.append(ValidationError(
            code="NO_BUDGET_CONFIGURED",
            message=f"未配置 (dept={dept}, category={category}, period={period}) 预算，建议复核",
            severity="warning",
        ))
        return errs

    check = check_available(db, tenant_id=tenant_id, dept=dept,
                            category=category, period=period, amount=amount)
    if not check.available:
        errs.append(ValidationError(
            code="OVER_BUDGET",
            message=f"金额 {amount} 超预算剩余 {check.remaining}，超出 {check.overage}",
            severity="error",
        ))
    return errs


def validate_expense(db: Session, claim) -> "ValidationResult":
    """v1.1 §5.2 验证服务 MCP 出参：6 项检查聚合（spec §4.4 自动化决策清单）。

    规则（v1.1 §2.x）：
    - 任一 error → outcome=FAIL
    - 仅 warning → outcome=NEEDS_REVIEW
    - 全通过 → outcome=PASS
    """
    from invoicing.workflow.validation import ValidationResult, ValidationError, ValidationOutcome

    errors: list = []
    warnings: list = []

    def _add(errs):
        for e in errs:
            (errors if e.severity == "error" else warnings).append(e)

    # 1. 金额合规（claim.total_amount）
    _add(_check_amount(claim.total_amount))

    # 2. 价税合计：聚合 claim 下 items 关联的 invoice 的 (amount_without_tax, tax_amount)
    inv_ids = (
        db.query(ExpenseItem.invoice_id)
        .filter(ExpenseItem.claim_id == claim.id, ExpenseItem.invoice_id.isnot(None))
        .distinct()
        .all()
    )
    inv_id_set = {row[0] for row in inv_ids if row[0] is not None}
    if inv_id_set:
        agg_rows = (
            db.query(Invoice.amount_without_tax, Invoice.tax_amount)
            .filter(Invoice.id.in_(inv_id_set))
            .all()
        )
        agg_no_tax = sum((r[0] or Decimal("0")) for r in agg_rows)
        agg_tax = sum((r[1] or Decimal("0")) for r in agg_rows)
    else:
        agg_no_tax = Decimal("0")
        agg_tax = Decimal("0")
    _add(_check_tax_sum(total=claim.total_amount,
                         without_tax=agg_no_tax, tax=agg_tax))

    # 3. 跨票号重复
    errors.extend(_check_cross_duplicate(db, claim))

    # 4. 凭证齐全
    errors.extend(_check_vouchers(db, claim))

    # 5. 预算余额（按 claim.dept × claim.claim_type × 当期 period）
    if claim.dept and claim.claim_type:
        # ponytail: period 取 claim.created_at 当月；缺 created_at 时用今天
        from datetime import date
        ref = claim.created_at.date() if claim.created_at else date.today()
        period = f"{ref.year:04d}-{ref.month:02d}"
        _add(_check_budget(db, tenant_id=claim.tenant_id, dept=claim.dept,
                            category=claim.claim_type, period=period,
                            amount=claim.total_amount))
    else:
        warnings.append(ValidationError(
            code="NO_DEPT_OR_TYPE",
            message="报销单缺 dept 或 claim_type，跳过 budget 检查",
            severity="warning",
        ))

    if errors:
        outcome = ValidationOutcome.FAIL
    elif warnings:
        outcome = ValidationOutcome.NEEDS_REVIEW
    else:
        outcome = ValidationOutcome.PASS
    return ValidationResult(outcome=outcome, errors=errors, warnings=warnings)
