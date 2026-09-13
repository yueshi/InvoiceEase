"""报销 service（P0 轻量闭环）。

合规要点（design/2026-09-13-发票易报销功能设计.md）：
- **一票一报**：active 明细的 invoice_id 唯一（DB 部分唯一索引兜底 + 应用层前置校验）；
  驳回/撤回时明细置 active=False 并释放发票 reimbursement_status
- **验真门槛**：verify_status != passed 的发票不得报销（政策 100% 验真）
- **整票报销**：P0 明细金额 = 发票全额（P1 放开拆额）
- **员工范围**：本人上传（user_id=本人）或公共池（user_id 为空）
- **无票支出**：银行/缴款书回单直接引用；人工凭证按 28 号公告校验要素与税前扣除资格
- 单级财务审批；每个动作写审计（EXPENSE_* + outcome）
"""
import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.config import settings
from invoicing.models import (
    BankReceipt,
    EntryType,
    ExpenseClaim,
    ExpenseClaimStatus,
    ExpenseEntry,
    ExpenseItem,
    Invoice,
    User,
    VoucherType,
)
from invoicing.models.fields import utcnow

logger = logging.getLogger(__name__)

EXPENSE_TYPES = ("travel", "office", "entertainment", "procurement", "welfare", "other")
_ENTRY_TYPE_LABELS = {
    "travel": "差旅", "procurement": "采购", "entertainment": "招待",
    "office": "办公", "welfare": "福利费", "other": "其他",
}
_FINANCE_ROLES = ("finance_staff", "finance_manager", "admin")

# 人工凭证类型（无票支出，需按 28 号公告校验要素）
_MANUAL_VOUCHER_TYPES = (
    VoucherType.RECEIPT_VOUCHER.value,
    VoucherType.INTERNAL.value,
    VoucherType.CONTRACT.value,
    VoucherType.OVERSEAS.value,
)


_CENT = Decimal("0.01")


def _money_q(v) -> Decimal:
    """金额量化到分：SQLite 的 SUM 会返回 float，直接 Decimal(float) 会带出
    `2991.1500000000000909…` 这类长尾（真实数据已复现）。"""
    if v is None:
        return Decimal("0")
    return Decimal(str(v)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _is_finance(user: User) -> bool:
    return (user.role or "") in _FINANCE_ROLES


def _next_claim_no(db: Session) -> str:
    """单号 FY-YYYYMM-####（按月流水）。"""
    prefix = f"FY-{date.today().strftime('%Y%m')}-"
    count = (
        db.query(func.count(ExpenseClaim.id))
        .filter(ExpenseClaim.claim_no.like(f"{prefix}%"))
        .scalar()
        or 0
    )
    return f"{prefix}{count + 1:04d}"


def _get_claim(db: Session, claim_id: int) -> ExpenseClaim:
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None:
        raise ValueError(f"报销单不存在: {claim_id}")
    return claim


def _require_owner_draft(claim: ExpenseClaim, user: User) -> None:
    if claim.applicant_id != user.id:
        raise ValueError("无权操作他人的报销单")
    if claim.status != ExpenseClaimStatus.DRAFT:
        raise ValueError(f"仅草稿可修改（当前 {claim.status}）")


ALLOWANCE_RULE = "travel_allowance"  # 差旅伙食补助的自动凭证标识（ExpenseItem.auto_rule）


def _positive_decimal(raw, label: str) -> Decimal:
    """场景字段里的人工输入 → 正数（天数/日标准都必须是正数）。"""
    text = str(raw if raw is not None else "").strip()
    try:
        value = Decimal(text)
    except (InvalidOperation, ArithmeticError):
        raise ValueError(f"{label}必须为正数（当前：{text or '空'}）") from None
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{label}必须为正数（当前：{text or '空'}）")
    return value


def _trim(v: Decimal) -> str:
    """金额/天数去尾零显示（Decimal('4.00') → '4'，Decimal('100') → '100'）。"""
    return format(v.normalize(), "f")


def _allowance_amount(entry_type: str, scene: dict) -> tuple[Decimal | None, str | None]:
    """差旅-伙食补助 → (金额, 摘要)；非补助事项返回 (None, None)。

    补助没有发票（国税函〔2009〕3 号：差旅费津贴不属于工资薪金，凭内部凭证扣除），
    所以只有把它物化成凭证，才能计入事项金额（entry.amount = Σ其下凭证）。
    日标准留空时取公司标准，避免「只填了天数却算不出钱」。
    """
    if entry_type != EntryType.TRAVEL.value or scene.get("subtype") != "allowance":
        return None, None
    days = _positive_decimal(scene.get("days"), "补助天数")
    raw_standard = str(scene.get("daily_standard") or "").strip()
    standard = (
        _positive_decimal(raw_standard, "日补助标准")
        if raw_standard
        else _money_q(settings.travel_allowance_daily_standard)
    )
    return _money_q(days * standard), f"{_trim(days)} 天 × {_trim(standard)} 元/天"


def _sync_allowance_item(db: Session, entry: ExpenseEntry) -> None:
    """差旅伙食补助的自动凭证：有则改、无则建、不再适用则撤（幂等）。

    改天数只更新同一条（不会重复累加）；事项改成非补助子类时撤销，
    否则会留下一笔与场景字段脱钩的钱。
    """
    amount, note = _allowance_amount(entry.entry_type, entry.scene_fields or {})
    db.flush()
    existing = (
        db.query(ExpenseItem)
        .filter(ExpenseItem.entry_id == entry.id, ExpenseItem.auto_rule == ALLOWANCE_RULE)
        .first()
    )
    if amount is None:
        if existing is None:
            return
        db.delete(existing)
    elif existing is None:
        db.add(
            ExpenseItem(
                claim_id=entry.claim_id, entry_id=entry.id,
                voucher_type=VoucherType.INTERNAL.value, amount=amount,
                expense_type=EntryType.TRAVEL.value, note=note,
                deductible=True,
                deductible_note="内部凭证：差旅费津贴据实扣除（凭公司标准与出差事实）",
                auto_rule=ALLOWANCE_RULE, active=True,
            )
        )
    else:
        existing.amount = amount
        existing.note = note
        existing.active = True
    _recalc_total(db, _get_claim(db, entry.claim_id))


def _recalc_entry_amount(db: Session, entry: ExpenseEntry) -> None:
    """事项金额 = 其凭证合计（自动，不接受手工修改）。"""
    db.flush()
    total = (
        db.query(func.coalesce(func.sum(ExpenseItem.amount), 0))
        .filter(ExpenseItem.entry_id == entry.id, ExpenseItem.active.is_(True))
        .scalar()
    )
    entry.amount = _money_q(total)


def _recalc_total(db: Session, claim: ExpenseClaim) -> None:
    db.flush()  # SessionLocal autoflush=False：先落库再聚合，否则看不到新增明细
    for entry in db.query(ExpenseEntry).filter(ExpenseEntry.claim_id == claim.id).all():
        _recalc_entry_amount(db, entry)
    total = (
        db.query(func.coalesce(func.sum(ExpenseItem.amount), 0))
        .filter(ExpenseItem.claim_id == claim.id, ExpenseItem.active.is_(True))
        .scalar()
    )
    claim.total_amount = _money_q(total)


# ---- 建单 / 明细 ----------------------------------------------------------


def create_claim(
    db: Session, user: User, title: str, remark: str | None = None, claim_type: str | None = None
) -> ExpenseClaim:
    """新建报销单：claim_type 选择单据类型（六类之一），作为默认事项类型与报表维度。"""
    if not (title or "").strip():
        raise ValueError("请填写报销事由")
    ctype = claim_type or EntryType.OTHER.value
    if ctype not in EXPENSE_TYPES:
        raise ValueError(f"非法单据类型: {ctype}（可选 {'/'.join(EXPENSE_TYPES)}）")
    claim = ExpenseClaim(
        claim_no=_next_claim_no(db),
        applicant_id=user.id,
        title=title.strip(),
        claim_type=ctype,
        remark=remark,
        status=ExpenseClaimStatus.DRAFT,
        total_amount=Decimal("0"),
    )
    db.add(claim)
    db.flush()
    write_audit(
        db, action="EXPENSE_CREATE", user_id=user.id, channel="web",
        detail={"claim_no": claim.claim_no, "title": claim.title, "claim_type": ctype},
    )
    db.commit()
    return claim


# 差旅事项子类（调研：行程明细表含「车船票；住宿天数」、补贴计算表）
TRAVEL_SUBTYPES: dict[str, dict] = {
    "transport": {
        "label": "交通",
        "required": ("transport_mode", "from_city", "to_city", "travel_date"),
        "labels": {"transport_mode": "交通方式（飞机/火车/高铁/长途汽车/出租车/自驾）",
                   "from_city": "出发城市", "to_city": "到达城市",
                   "vehicle_no": "车次/航班号", "travel_date": "乘车/乘机日期"},
    },
    "accommodation": {
        "label": "住宿",
        "required": ("city", "checkin", "checkout"),
        "labels": {"city": "住宿城市", "checkin": "入住日期", "checkout": "离店日期",
                   "nights": "住宿晚数", "rooms": "房间数"},
    },
    "local_transport": {
        "label": "市内交通",
        "required": ("city", "travel_date"),
        "labels": {"city": "所在城市", "travel_date": "发生日期"},
    },
    "allowance": {
        "label": "伙食补助",
        "required": ("days",),
        "labels": {"days": "补助天数", "daily_standard": "日补助标准", "city": "所在地"},
    },
    "other": {"label": "其他差旅支出", "required": (), "labels": {}},
}

# 场景必填/建议要素（调研：差旅行程、采购三单、招待对象人数）
_SCENE_REQUIRED: dict[str, dict] = {
    "travel": {  # 差旅由子类细化（见 validate_scene_fields）
        "required": (),
        "labels": {},
    },
    EntryType.ENTERTAINMENT.value: {
        "required": ("guests", "headcount"),
        "labels": {"guests": "招待对象", "headcount": "招待人数"},
    },
}
_SCENE_SUGGESTED: dict[str, dict] = {
    EntryType.PROCUREMENT.value: {
        "suggested": ("supplier", "contract_no", "order_no"),
        "labels": {"supplier": "供应商", "contract_no": "合同号", "order_no": "订单号"},
    },
}


def validate_scene_fields(entry_type: str, scene_fields: dict | None) -> list[str]:
    """场景要素校验：必填缺失 → 抛错；建议项缺失 → 返回提示（不阻断）。

    差旅（travel）按**子类**细化：交通（方式+出发到达城市+日期）/ 住宿（城市+入住离店）/
    市内交通（城市+日期）/ 伙食补助（天数）/ 其它（说明即可）——调研自实务行程明细表。
    """
    scene = scene_fields or {}
    if entry_type == EntryType.TRAVEL.value:
        subtype = str(scene.get("subtype") or "").strip()
        if subtype not in TRAVEL_SUBTYPES:
            options = "/".join(f"{k}（{v['label']}）" for k, v in TRAVEL_SUBTYPES.items())
            raise ValueError(f"差旅事项需选择子类：{options}")
        sub = TRAVEL_SUBTYPES[subtype]
        missing = [sub["labels"][k] for k in sub["required"] if not str(scene.get(k) or "").strip()]
        if missing:
            raise ValueError(f"差旅-{sub['label']}事项缺少必填要素：{'、'.join(missing)}")
        return []
    spec = _SCENE_REQUIRED.get(entry_type)
    if spec:
        missing = [spec["labels"][k] for k in spec["required"] if not str(scene.get(k) or "").strip()]
        if missing:
            raise ValueError(f"{_ENTRY_TYPE_LABELS.get(entry_type, entry_type)}事项缺少必填要素：{'、'.join(missing)}")
    hints = []
    sspec = _SCENE_SUGGESTED.get(entry_type)
    if sspec:
        missing = [sspec["labels"][k] for k in sspec["suggested"] if not str(scene.get(k) or "").strip()]
        if missing:
            hints.append(f"建议补充：{'、'.join(missing)}（采购三单匹配所需）")
    return hints


def create_entry(
    db: Session,
    user: User,
    claim_id: int,
    entry_type: str | None,
    title: str,
    occurred_on: date | None = None,
    scene_fields: dict | None = None,
    note: str | None = None,
) -> ExpenseEntry:
    """新建事项（费用明细行，凭证挂在其下）。"""
    claim = _get_claim(db, claim_id)
    _require_owner_draft(claim, user)
    entry_type = entry_type or claim.claim_type  # 未指定则跟随单据类型
    if entry_type not in _ENTRY_TYPE_LABELS:
        raise ValueError(f"非法事项类型: {entry_type}（可选 {'/'.join(_ENTRY_TYPE_LABELS)}）")
    if not (title or "").strip():
        raise ValueError("请填写事项说明")
    validate_scene_fields(entry_type, scene_fields)
    entry = ExpenseEntry(
        claim_id=claim.id, entry_type=entry_type, title=title.strip(),
        occurred_on=occurred_on, scene_fields=scene_fields or None, note=note,
        amount=Decimal("0"),
    )
    db.add(entry)
    db.flush()
    _sync_allowance_item(db, entry)  # 伙食补助：按天数×标准自动生成内部凭证
    write_audit(
        db, action="EXPENSE_ADD_ENTRY", user_id=user.id, channel="web",
        detail={"claim_no": claim.claim_no, "entry_id": entry.id, "entry_type": entry_type,
                "title": entry.title, "amount": str(entry.amount)},
    )
    db.commit()
    return entry


def update_entry(
    db: Session, user: User, entry_id: int, **fields
) -> ExpenseEntry:
    """更新事项（草稿态）；类型变更时校验新场景要素。"""
    entry = db.get(ExpenseEntry, entry_id)
    if entry is None:
        raise ValueError(f"事项不存在: {entry_id}")
    claim = _get_claim(db, entry.claim_id)
    _require_owner_draft(claim, user)
    allowed = {"entry_type", "title", "occurred_on", "scene_fields", "note"}
    data = {k: v for k, v in fields.items() if k in allowed and v is not None}
    merged_type = data.get("entry_type", entry.entry_type)
    merged_scene = data.get("scene_fields", entry.scene_fields)
    validate_scene_fields(merged_type, merged_scene)
    for k, v in data.items():
        setattr(entry, k, v)
    _sync_allowance_item(db, entry)  # 天数/标准/子类变更 → 同步补助凭证
    db.commit()
    return entry


def remove_entry(db: Session, user: User, entry_id: int) -> None:
    """删除事项：释放其下凭证占用的发票；凭证行随 FK CASCADE 一并删除。

    注意：明细**不要**再 `db.delete(item)`/改 active —— entry 删除时 DB 级联已经
    移除了它们，ORM 再发一条 DELETE/UPDATE 会「expected to delete 1 row(s);
    0 were matched」（本仓库真实复现过）。这里只释放发票占用，然后 expunge。
    """
    entry = db.get(ExpenseEntry, entry_id)
    if entry is None:
        raise ValueError(f"事项不存在: {entry_id}")
    claim = _get_claim(db, entry.claim_id)
    _require_owner_draft(claim, user)
    items = db.query(ExpenseItem).filter(ExpenseItem.entry_id == entry.id).all()
    for item in items:
        if item.invoice_id:
            inv = db.get(Invoice, item.invoice_id)
            if inv is not None and inv.reimbursement_status == "pending":
                inv.reimbursement_status = "none"  # 释放占用
        db.expunge(item)
    db.delete(entry)
    _recalc_total(db, claim)
    write_audit(
        db, action="EXPENSE_REMOVE_ENTRY", user_id=user.id, channel="web",
        detail={"claim_no": claim.claim_no, "entry_id": entry_id},
    )
    db.commit()


def eligible_invoices(db: Session, user: User) -> list[Invoice]:
    """员工可选发票：本人上传的 ∪ 公共池（无归属）；已验真、未拦截、未占用。"""
    return (
        db.query(Invoice)
        .filter(
            or_(Invoice.user_id == user.id, Invoice.user_id.is_(None)),
            Invoice.verify_status == "passed",
            Invoice.status != "blocked",
            Invoice.reimbursement_status == "none",
            Invoice.total_amount.isnot(None),
        )
        .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
        .all()
    )


def _require_entry(db: Session, claim: ExpenseClaim, entry_id: int | None) -> ExpenseEntry:
    """凭证必须挂到本单的事项下（P0.5）。"""
    if entry_id is None:
        raise ValueError("请先选择事项（凭证需挂到具体费用事项下）")
    entry = db.get(ExpenseEntry, entry_id)
    if entry is None or entry.claim_id != claim.id:
        raise ValueError(f"事项不属于该报销单: {entry_id}")
    return entry


def add_invoice(
    db: Session,
    user: User,
    claim_id: int,
    invoice_id: int,
    entry_id: int | None = None,
    expense_type: str = "other",
    note: str | None = None,
) -> ExpenseItem:
    claim = _get_claim(db, claim_id)
    _require_owner_draft(claim, user)
    entry = _require_entry(db, claim, entry_id)
    if expense_type not in EXPENSE_TYPES:
        raise ValueError(f"非法费用类型: {expense_type}（可选 {'/'.join(EXPENSE_TYPES)}）")

    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise ValueError(f"发票不存在: {invoice_id}")
    if inv.user_id is not None and inv.user_id != user.id:
        raise ValueError("该发票归属他人，无权用于报销")
    if inv.verify_status != "passed":
        raise ValueError("发票未通过验真，不可报销（政策要求先验真）")
    if inv.status == "blocked":
        raise ValueError("发票已被查重拦截，不可报销")

    existing = (
        db.query(ExpenseItem)
        .filter(ExpenseItem.invoice_id == inv.id, ExpenseItem.active.is_(True))
        .first()
    )
    if existing is not None:
        if existing.claim_id == claim.id:
            raise ValueError("该发票已在本报销单中")
        raise ValueError(f"该发票已被报销单 #{existing.claim_id} 占用（一票一报）")
    if inv.reimbursement_status != "none":
        raise ValueError("该发票已被报销或占用")

    item = ExpenseItem(
        claim_id=claim.id,
        entry_id=entry.id,
        invoice_id=inv.id,
        voucher_type=VoucherType.INVOICE.value,
        amount=inv.total_amount,  # 整票报销（P0）
        expense_type=expense_type,
        note=note,
        active=True,
    )
    db.add(item)
    inv.reimbursement_status = "pending"  # 占用
    _recalc_total(db, claim)
    write_audit(
        db, action="EXPENSE_ADD_INVOICE", user_id=user.id, invoice_id=inv.id, channel="web",
        detail={"claim_no": claim.claim_no, "amount": str(inv.total_amount)},
    )
    db.commit()
    return item


def add_receipt(
    db: Session,
    user: User,
    claim_id: int,
    receipt_id: int,
    entry_id: int | None = None,
    voucher_type: str = VoucherType.BANK_RECEIPT.value,
    expense_type: str = "other",
    note: str | None = None,
) -> ExpenseItem:
    """无票支出：银行回单/缴款书回单作为明细（金额取自回单）。"""
    claim = _get_claim(db, claim_id)
    _require_owner_draft(claim, user)
    entry = _require_entry(db, claim, entry_id)
    if voucher_type not in (VoucherType.BANK_RECEIPT.value, VoucherType.TAX_RECEIPT.value):
        raise ValueError("回单明细仅支持 bank_receipt / tax_receipt")
    r = db.get(BankReceipt, receipt_id)
    if r is None:
        raise ValueError(f"回单不存在: {receipt_id}")
    if r.amount is None:
        raise ValueError("回单金额缺失，无法报销")
    existing = (
        db.query(ExpenseItem)
        .filter(ExpenseItem.receipt_id == r.id, ExpenseItem.active.is_(True))
        .first()
    )
    if existing is not None:
        raise ValueError("该回单已被占用（一单一报）")

    item = ExpenseItem(
        claim_id=claim.id, entry_id=entry.id, receipt_id=r.id, voucher_type=voucher_type,
        amount=r.amount, expense_type=expense_type, note=note, active=True,
    )
    db.add(item)
    _recalc_total(db, claim)
    write_audit(
        db, action="EXPENSE_ADD_RECEIPT", user_id=user.id, channel="web",
        detail={"claim_no": claim.claim_no, "receipt_id": r.id, "amount": str(r.amount)},
    )
    db.commit()
    return item


def add_manual_voucher(
    db: Session,
    user: User,
    claim_id: int,
    entry_id: int | None,
    voucher_type: str,
    amount: Decimal,
    expense_type: str = "other",
    note: str | None = None,
    payee_name: str | None = None,
    payee_id_no: str | None = None,
    attachment_url: str | None = None,
) -> ExpenseItem:
    """人工凭证（无票支出）：按 28 号公告校验要素与税前扣除资格。"""
    claim = _get_claim(db, claim_id)
    _require_owner_draft(claim, user)
    entry = _require_entry(db, claim, entry_id)
    if voucher_type not in _MANUAL_VOUCHER_TYPES:
        raise ValueError(f"非法凭证类型: {voucher_type}")
    if expense_type not in EXPENSE_TYPES:
        raise ValueError(f"非法费用类型: {expense_type}")
    if amount is None or Decimal(str(amount)) <= 0:
        raise ValueError("金额必须大于 0")

    deductible, deductible_note = _evaluate_deductibility(
        voucher_type, Decimal(str(amount)), payee_name, payee_id_no
    )
    item = ExpenseItem(
        claim_id=claim.id, entry_id=entry.id, voucher_type=voucher_type, amount=Decimal(str(amount)),
        expense_type=expense_type, note=note, payee_name=payee_name, payee_id_no=payee_id_no,
        attachment_url=attachment_url, deductible=deductible, deductible_note=deductible_note,
        active=True,
    )
    db.add(item)
    _recalc_total(db, claim)
    write_audit(
        db, action="EXPENSE_ADD_VOUCHER", user_id=user.id, channel="web",
        detail={
            "claim_no": claim.claim_no, "voucher_type": voucher_type,
            "amount": str(amount), "deductible": deductible,
        },
    )
    db.commit()
    return item


def _evaluate_deductibility(
    voucher_type: str, amount: Decimal, payee_name: str | None, payee_id_no: str | None
) -> tuple[bool, str | None]:
    """税前扣除资格判定（28 号公告 + 起征点阈值）。

    - 收款凭证：要素（姓名+身份证号）齐全才可扣除；超小额零星阈值需取得发票
    - 内部凭证/境外票据：属非应税项目 → 可扣除
    - 合同类：仅特殊情形（对方注销等）可扣除，需附非现金付款凭证 → 标记待财务确认
    """
    threshold = Decimal(str(settings.expense_petty_cash_threshold))
    if voucher_type == VoucherType.RECEIPT_VOUCHER.value:
        missing = []
        if not (payee_name or "").strip():
            missing.append("收款人姓名")
        if not (payee_id_no or "").strip():
            missing.append("身份证号")
        if missing:
            return False, f"收款凭证要素不全（缺 {'、'.join(missing)}），不可税前扣除"
        if amount > threshold:
            return False, (
                f"单次 {amount} 元已超小额零星标准（{threshold} 元），需取得发票方可税前扣除"
            )
        return True, None
    if voucher_type == VoucherType.CONTRACT.value:
        return False, "合同类凭证仅特殊情形（对方注销/非正常户等）可扣除，需附非现金付款凭证并经财务确认"
    return True, None


def remove_item(db: Session, user: User, item_id: int) -> None:
    item = db.get(ExpenseItem, item_id)
    if item is None:
        raise ValueError(f"明细不存在: {item_id}")
    if item.auto_rule:
        raise ValueError("该凭证由系统自动计算，不能单独删除；请修改补助天数或日标准")
    claim = _get_claim(db, item.claim_id)
    _require_owner_draft(claim, user)

    if item.invoice_id:
        inv = db.get(Invoice, item.invoice_id)
        if inv is not None and inv.reimbursement_status == "pending":
            inv.reimbursement_status = "none"  # 释放占用
    item.active = False
    _recalc_total(db, claim)
    write_audit(
        db, action="EXPENSE_REMOVE_ITEM", user_id=user.id, channel="web",
        detail={"claim_no": claim.claim_no, "item_id": item_id},
    )
    db.commit()


# ---- 状态流转 ------------------------------------------------------------


def submit_claim(db: Session, user: User, claim_id: int) -> ExpenseClaim:
    claim = _get_claim(db, claim_id)
    _require_owner_draft(claim, user)
    entries = db.query(ExpenseEntry).filter(ExpenseEntry.claim_id == claim.id).all()
    if not entries:
        raise ValueError("报销单没有事项，无法提交")
    empty = [e.title for e in entries if item_count_of_entry(db, e.id) == 0]
    if empty:
        raise ValueError(f"以下事项还没有关联凭证：{'、'.join(empty)}")
    items = db.query(ExpenseItem).filter(
        ExpenseItem.claim_id == claim.id, ExpenseItem.active.is_(True)
    ).count()
    _recalc_total(db, claim)
    claim.status = ExpenseClaimStatus.PENDING
    claim.submitted_at = utcnow()
    write_audit(
        db, action="EXPENSE_SUBMIT", user_id=user.id, channel="web",
        detail={"claim_no": claim.claim_no, "items": items, "total": str(claim.total_amount)},
    )
    db.commit()
    return claim


def approve_claim(db: Session, user: User, claim_id: int) -> ExpenseClaim:
    claim = _get_claim(db, claim_id)
    if claim.status != ExpenseClaimStatus.PENDING:
        raise ValueError(f"仅待审批的报销单可审批（当前 {claim.status}）")
    if not _is_finance(user):
        raise ValueError("仅财务角色可审批报销单")

    items = db.query(ExpenseItem).filter(
        ExpenseItem.claim_id == claim.id, ExpenseItem.active.is_(True)
    ).all()
    for item in items:
        if item.invoice_id:
            inv = db.get(Invoice, item.invoice_id)
            if inv is not None:
                inv.reimbursement_status = "claimed"  # 已报销
    claim.status = ExpenseClaimStatus.APPROVED
    claim.approver_id = user.id
    claim.decided_at = utcnow()
    write_audit(
        db, action="EXPENSE_APPROVE", user_id=user.id, invoice_id=None, channel="web",
        detail={"claim_no": claim.claim_no, "total": str(claim.total_amount), "result": "approved"},
    )
    db.commit()
    return claim


def reject_claim(db: Session, user: User, claim_id: int, reason: str) -> ExpenseClaim:
    claim = _get_claim(db, claim_id)
    if claim.status != ExpenseClaimStatus.PENDING:
        raise ValueError(f"仅待审批的报销单可驳回（当前 {claim.status}）")
    if not _is_finance(user):
        raise ValueError("仅财务角色可审批报销单")
    if not (reason or "").strip():
        raise ValueError("驳回必须填写理由")

    _release_items(db, claim)
    claim.status = ExpenseClaimStatus.REJECTED
    claim.approver_id = user.id
    claim.decided_at = utcnow()
    claim.rejected_reason = reason.strip()
    write_audit(
        db, action="EXPENSE_REJECT", user_id=user.id, channel="web",
        detail={"claim_no": claim.claim_no, "reason": claim.rejected_reason, "result": "rejected"},
    )
    db.commit()
    return claim


def withdraw_claim(db: Session, user: User, claim_id: int) -> ExpenseClaim:
    claim = _get_claim(db, claim_id)
    if claim.applicant_id != user.id:
        raise ValueError("只能撤回本人提交的报销单")
    if claim.status not in (ExpenseClaimStatus.DRAFT, ExpenseClaimStatus.PENDING):
        raise ValueError(f"当前状态不可撤回（{claim.status}）")
    _release_items(db, claim)
    claim.status = ExpenseClaimStatus.WITHDRAWN
    write_audit(
        db, action="EXPENSE_WITHDRAW", user_id=user.id, channel="web",
        detail={"claim_no": claim.claim_no},
    )
    db.commit()
    return claim


_CLAIM_SNAPSHOT_COLS = (
    "id", "claim_no", "applicant_id", "title", "total_amount", "status",
    "approver_id", "submitted_at", "decided_at", "remark", "created_at",
)


def delete_claim(db: Session, user: User, claim_id: int) -> dict:
    """删除报销单（审计全字段快照 + 释放发票占用）。

    已通过的单据被删除时**必须释放发票**（reimbursement_status 回到 none），
    否则那些发票会永久卡在「已报销」而无法重新报销。
    权限：本人可删自己的单；财务/管理员可删任意单。
    """
    claim = _get_claim(db, claim_id)
    if claim.applicant_id != user.id and not _is_finance(user):
        raise ValueError("无权删除他人的报销单")

    snapshot = {col: str(getattr(claim, col)) for col in _CLAIM_SNAPSHOT_COLS}
    items = db.query(ExpenseItem).filter(ExpenseItem.claim_id == claim.id).all()
    snapshot["item_count"] = str(len(items))
    # 只释放发票占用，不置 active：事项与明细行随 claim 删除由 FK CASCADE 清掉
    # （再显式 db.delete 会与级联重复，触发「0 rows matched」）
    _release_items(db, claim, mark_inactive=False)
    write_audit(
        db, action="EXPENSE_DELETE", user_id=user.id, channel="web",
        detail={"claim_no": claim.claim_no, "snapshot": snapshot},
    )
    db.delete(claim)
    db.commit()
    return {"ok": True}


def _release_items(db: Session, claim: ExpenseClaim, mark_inactive: bool = True) -> None:
    """驳回/撤回/删除：明细失效并释放发票占用（一票一报约束即时解除）。

    须同时释放 pending（审批中占用）与 **claimed（已通过）**——删除已通过的单据
    时若不释放 claimed，那些发票会永久卡在「已报销」无法重新报销。

    mark_inactive=False 用于**整单删除**：明细行随 FK CASCADE 消失，再置 active
    会多出一条 UPDATE（行已没了）。
    """
    items = db.query(ExpenseItem).filter(
        ExpenseItem.claim_id == claim.id, ExpenseItem.active.is_(True)
    ).all()
    for item in items:
        if mark_inactive:
            item.active = False
        if item.invoice_id:
            inv = db.get(Invoice, item.invoice_id)
            if inv is not None and inv.reimbursement_status in ("pending", "claimed"):
                inv.reimbursement_status = "none"


# ---- 查询 ----------------------------------------------------------------


def list_claims(
    db: Session, user: User, status: str | None = None, claim_type: str | None = None
) -> list[ExpenseClaim]:
    """员工看本人；财务/管理员看全部（FRD 权限约定）；可按状态与单据类型筛选。"""
    q = db.query(ExpenseClaim)
    if not _is_finance(user):
        q = q.filter(ExpenseClaim.applicant_id == user.id)
    if status:
        q = q.filter(ExpenseClaim.status == status)
    if claim_type:
        q = q.filter(ExpenseClaim.claim_type == claim_type)
    return q.order_by(ExpenseClaim.created_at.desc(), ExpenseClaim.id.desc()).all()


def item_count_of_entry(db: Session, entry_id: int) -> int:
    return (
        db.query(ExpenseItem)
        .filter(ExpenseItem.entry_id == entry_id, ExpenseItem.active.is_(True))
        .count()
    )


def list_entries(db: Session, claim_id: int) -> list[ExpenseEntry]:
    return (
        db.query(ExpenseEntry)
        .filter(ExpenseEntry.claim_id == claim_id)
        .order_by(ExpenseEntry.id)
        .all()
    )


def item_count(db: Session, claim_id: int) -> int:
    """有效明细数（列表展示用）。"""
    return (
        db.query(ExpenseItem)
        .filter(ExpenseItem.claim_id == claim_id, ExpenseItem.active.is_(True))
        .count()
    )


def claim_detail(
    db: Session, user: User, claim_id: int
) -> tuple[ExpenseClaim, list[ExpenseEntry], list[ExpenseItem]]:
    """单据详情：返回 单据 + 事项 + 有效凭证（凭证带 entry_id 便于前端分组）。"""
    claim = _get_claim(db, claim_id)
    if not _is_finance(user) and claim.applicant_id != user.id:
        raise ValueError("无权查看他人的报销单")
    entries = list_entries(db, claim.id)
    items = (
        db.query(ExpenseItem)
        .filter(ExpenseItem.claim_id == claim.id, ExpenseItem.active.is_(True))
        .order_by(ExpenseItem.id)
        .all()
    )
    return claim, entries, items
