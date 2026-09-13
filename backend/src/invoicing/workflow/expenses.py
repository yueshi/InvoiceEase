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
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.config import settings
from invoicing.models import (
    BankReceipt,
    ExpenseClaim,
    ExpenseClaimStatus,
    ExpenseItem,
    Invoice,
    User,
    VoucherType,
)
from invoicing.models.fields import utcnow

logger = logging.getLogger(__name__)

EXPENSE_TYPES = ("travel", "office", "entertainment", "procurement", "other")
_FINANCE_ROLES = ("finance_staff", "finance_manager", "admin")

# 人工凭证类型（无票支出，需按 28 号公告校验要素）
_MANUAL_VOUCHER_TYPES = (
    VoucherType.RECEIPT_VOUCHER.value,
    VoucherType.INTERNAL.value,
    VoucherType.CONTRACT.value,
    VoucherType.OVERSEAS.value,
)


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


def _recalc_total(db: Session, claim: ExpenseClaim) -> None:
    db.flush()  # SessionLocal autoflush=False：先落库再聚合，否则看不到新增明细
    total = (
        db.query(func.coalesce(func.sum(ExpenseItem.amount), 0))
        .filter(ExpenseItem.claim_id == claim.id, ExpenseItem.active.is_(True))
        .scalar()
    )
    claim.total_amount = Decimal(str(total or 0))


# ---- 建单 / 明细 ----------------------------------------------------------


def create_claim(db: Session, user: User, title: str, remark: str | None = None) -> ExpenseClaim:
    if not (title or "").strip():
        raise ValueError("请填写报销事由")
    claim = ExpenseClaim(
        claim_no=_next_claim_no(db),
        applicant_id=user.id,
        title=title.strip(),
        remark=remark,
        status=ExpenseClaimStatus.DRAFT,
        total_amount=Decimal("0"),
    )
    db.add(claim)
    db.flush()
    write_audit(
        db, action="EXPENSE_CREATE", user_id=user.id, channel="web",
        detail={"claim_no": claim.claim_no, "title": claim.title},
    )
    db.commit()
    return claim


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


def add_invoice(
    db: Session,
    user: User,
    claim_id: int,
    invoice_id: int,
    expense_type: str = "other",
    note: str | None = None,
) -> ExpenseItem:
    claim = _get_claim(db, claim_id)
    _require_owner_draft(claim, user)
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
    voucher_type: str = VoucherType.BANK_RECEIPT.value,
    expense_type: str = "other",
    note: str | None = None,
) -> ExpenseItem:
    """无票支出：银行回单/缴款书回单作为明细（金额取自回单）。"""
    claim = _get_claim(db, claim_id)
    _require_owner_draft(claim, user)
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
        claim_id=claim.id, receipt_id=r.id, voucher_type=voucher_type,
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
        claim_id=claim.id, voucher_type=voucher_type, amount=Decimal(str(amount)),
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
    items = db.query(ExpenseItem).filter(
        ExpenseItem.claim_id == claim.id, ExpenseItem.active.is_(True)
    ).count()
    if items == 0:
        raise ValueError("报销单没有明细，无法提交")
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


def _release_items(db: Session, claim: ExpenseClaim) -> None:
    """驳回/撤回：明细失效并释放发票占用（一票一报约束即时解除）。"""
    items = db.query(ExpenseItem).filter(
        ExpenseItem.claim_id == claim.id, ExpenseItem.active.is_(True)
    ).all()
    for item in items:
        item.active = False
        if item.invoice_id:
            inv = db.get(Invoice, item.invoice_id)
            if inv is not None and inv.reimbursement_status == "pending":
                inv.reimbursement_status = "none"


# ---- 查询 ----------------------------------------------------------------


def list_claims(db: Session, user: User, status: str | None = None) -> list[ExpenseClaim]:
    """员工看本人；财务/管理员看全部（FRD 权限约定）。"""
    q = db.query(ExpenseClaim)
    if not _is_finance(user):
        q = q.filter(ExpenseClaim.applicant_id == user.id)
    if status:
        q = q.filter(ExpenseClaim.status == status)
    return q.order_by(ExpenseClaim.created_at.desc(), ExpenseClaim.id.desc()).all()


def item_count(db: Session, claim_id: int) -> int:
    """有效明细数（列表展示用）。"""
    return (
        db.query(ExpenseItem)
        .filter(ExpenseItem.claim_id == claim_id, ExpenseItem.active.is_(True))
        .count()
    )


def claim_detail(db: Session, user: User, claim_id: int) -> tuple[ExpenseClaim, list[ExpenseItem]]:
    claim = _get_claim(db, claim_id)
    if not _is_finance(user) and claim.applicant_id != user.id:
        raise ValueError("无权查看他人的报销单")
    items = (
        db.query(ExpenseItem)
        .filter(ExpenseItem.claim_id == claim.id, ExpenseItem.active.is_(True))
        .order_by(ExpenseItem.id)
        .all()
    )
    return claim, items
