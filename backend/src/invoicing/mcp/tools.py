# invoicing/mcp/tools.py
"""MCP 工具实现：薄适配器，直接调用 workflow service 层。"""
import re
from datetime import date

from fastapi import HTTPException

from invoicing import web_links
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
from invoicing.mcp.identity import requires, requires_role
from invoicing.mcp.proposal_registry import register_proposal
from invoicing.schemas.company_info import CompanyInfoOut, TAX_ID_PATTERN
from invoicing.schemas.invoice import (
    InvoiceListResponse,
    InvoiceOut,
    ReceiptListResponse,
    ReceiptOut,
)
from invoicing.schemas.mailbox import PollResultOut
from invoicing.workflow import services
# P0-1/P0-2 工具实现在 workflow 层：导入为模块级名字，供 _impl_of 解析
# （agent tools_bridge 的调用前权限过滤依赖它）
from invoicing.workflow.budget_service import (
    check_budget_available_mcp,
    query_budget_mcp,
)
from invoicing.workflow.services import validate_expense_mcp
from invoicing.workers.queue import enqueue_receipt_parse_sync


def _current_user(db) -> User:
    """当前 MCP 调用的真实用户（service 层接口要 ORM 对象）。

    Principal 来自 SDK 认证上下文（`@requires` 已保证上下文存在）；
    这里只做 Principal → User 的落地。**不再合成 admin、不再取"最小 id 的 admin"**
    ——那正是「MCP 调用全算管理员」的根因。
    """
    from invoicing.mcp.identity import current_principal

    principal = current_principal()
    if principal.user_id is None:
        raise ValueError("当前令牌未绑定用户，无法执行需要归属的操作")
    user = db.get(User, principal.user_id)
    if user is None:
        raise ValueError(f"令牌归属用户不存在: {principal.user_id}")
    return user


# 注册名 → 实现函数名（少数不一致；与 server.py 注册一一对应）
_REGISTERED_ALIASES = {
    "invoice_fetch": "fetch_invoices",
    "invoice_list": "list_invoices_mcp",
    "invoice_detail": "get_invoice_mcp",
    "invoice_ingest": "ingest_invoice",
    "receipt_parse_status": "receipt_upload_status",
    "extract_invoice": "extract_invoice_file",
    "batch_extract_invoices": "batch_extract_invoice_files",
    "validate_invoice": "validate_invoice_data",
    # P0-1 工具实现在 workflow 层（见顶部导入）
    "validate_expense": "validate_expense_mcp",
    "query_budget": "query_budget_mcp",
    "check_budget_available": "check_budget_available_mcp",
}


def _impl_of(registered_name: str):
    """注册名 → 实现函数；未知工具返回 None（过滤时按"无要求"放行，执行层仍有 @requires 兜底）。"""
    impl_name = _REGISTERED_ALIASES.get(registered_name, registered_name)
    fn = globals().get(impl_name)
    if fn is None:
        from invoicing.mcp import extract as extract_mod

        fn = getattr(extract_mod, impl_name, None)
    return fn


def permissions_of(registered_name: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """按注册名取工具的权限要求（scope 集 + 角色白名单）。

    数据源 = 装饰器元数据（@requires/@requires_role 写入），无第二份手抄表；
    供 Web 助手在「调用前」过滤工具清单（tools_bridge），
    覆盖性由 test_mcp_permissions 的交叉校验用例防漂移。
    """
    fn = _impl_of(registered_name)
    if fn is None:
        return frozenset(), ()
    return (
        frozenset(getattr(fn, "__requires_scopes__", ())),
        tuple(getattr(fn, "__requires_roles__", ()) or ()),
    )


def _registered_tool_names() -> list[str]:
    """全部已声明权限的工具（注册名）：模块内省 + 别名反查。

    与真实注册的一致性由 test_mcp_permissions 核对（_impl_of 逐名比对）。
    """
    import sys

    from invoicing.mcp import extract as extract_mod
    # P0-1：扫描新工具所在模块（v1.1 §5.2 验证/预算服务 MCP）
    # 工具定义在 services.py / budget_service.py，但要被 my_permissions 列出
    from invoicing.workflow import services as services_mod
    from invoicing.workflow import budget_service as budget_service_mod

    reverse = {v: k for k, v in _REGISTERED_ALIASES.items()}
    names: list[str] = []
    for mod in (sys.modules[__name__], extract_mod, services_mod, budget_service_mod):
        for obj in vars(mod).values():
            if callable(obj) and hasattr(obj, "__requires_scopes__"):
                names.append(reverse.get(obj.__name__, obj.__name__))
    return sorted(names)


@requires()
def my_permissions() -> dict:
    """当前令牌的身份与操作范围（只读、只返回自己）。

    @requires() 空声明 = 认证即可调（自身信息无权限门槛）。
    Web 助手的工具清单在调用前按权限过滤；MCP 侧（WorkBuddy）没有这一步，
    Agent 用本工具即可在调用前知道边界，排障时也能回答「这个令牌能干嘛」。
    """
    from invoicing.mcp.identity import current_principal, role_label

    p = current_principal()
    allowed: list[str] = []
    for name in _registered_tool_names():
        need_scopes, need_roles = permissions_of(name)
        if need_scopes <= p.scopes and (not need_roles or p.role in need_roles):
            allowed.append(name)
    return {
        "username": p.username,
        "role": p.role,
        "role_label": role_label(p.role),
        "tenant_id": p.tenant_id,
        "token_source": p.source,
        "scopes": sorted(p.scopes),
        # 数据范围与 scoped_invoices/报销服务同口径：员工仅本人，财务+全公司
        "data_scope": "本人" if p.role == "employee" else "全公司",
        "tools": allowed,
    }


def _audit(db, action: str, *, invoice_id: int | None = None, detail: dict | None = None):
    """MCP 审计：**带上主体**（谁、用哪个令牌、哪个租户）。

    此前只记 `channel="mcp"`，审计能回答「有 Agent 干过这件事」，
    回答不了「谁干的」——等保要求的「主体」要素在 MCP 通道是空的。
    主体取自认证上下文（`@requires` 已保证存在）。
    """
    from invoicing.mcp.identity import current_principal

    p = current_principal()
    merged = {"token_source": p.source, "token_id": p.token_id, "tenant_id": p.tenant_id}
    merged.update(detail or {})
    return write_audit(
        db, action=action, user_id=p.user_id, invoice_id=invoice_id,
        channel="mcp", detail=merged,
    )


def _make_proposal(*, tool_name: str, payload: dict, preview: dict,
                   idempotency_key: str | None = None) -> dict:
    """两段握手第一步公共实现（v1.1 §7.5）：签发 proposal，不落业务库。

    幂等：同 idempotency_key 重放返回同一 token（Agent 重试/网络抖动不产生
    第二张提案）。scope/role 由调用方工具上的 @requires/@requires_role 承担——
    本 helper 自身不做权限判断。
    """
    from invoicing.idempotency import create_proposal, idempotent_run
    from invoicing.mcp.identity import current_principal

    principal = current_principal()

    with SessionLocal() as db:
        def _build() -> dict:
            p = create_proposal(
                db, tool_name=tool_name, payload=payload, preview=preview,
                actor_id=principal.user_id or 0, actor_type="user", channel="mcp",
            )
            return {
                "proposal_token": p.token,
                "tool_name": tool_name,
                "preview": p.preview,
                "expires_at": p.expires_at.isoformat(),
                "next_step": "用户确认后调 confirm_execute(token=..., tool_name=..., human_ack=true)",
            }

        # 幂等命名空间 = 工具 + 主体 + 阶段：
        # - 阶段隔离：否则 confirm 会命中 proposal 的缓存、直接返回提案而不执行
        # - 主体隔离：否则跨用户同 key 会互相命中（泄漏 token / 静默假成功）
        return idempotent_run(db, key=idempotency_key, tool_name=tool_name,
                              actor_id=principal.user_id, phase="proposal",
                              fn=_build)


@requires_role("admin")
@requires("invoice:write")
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
        _audit(
            db, action="FETCH",
            detail={"mailbox_ids": [mb.id for mb in mailboxes], "result": total},
        )
        db.commit()
    return PollResultOut(**total, active_mailboxes=[mb.username or mb.name for mb in mailboxes])


@requires("invoice:read")
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
        user = _current_user(db)
        # 关键字参数：避免签名扩展（如新增 expense_type）导致位置参数错位
        result = services.list_invoices(
            db, user, status=status, date_from=date_from, date_to=date_to,
            keyword=keyword, expense_type=expense_type, page=page, page_size=page_size,
        )
    # MCP 返回需 pydantic 模型（REST 由 response_model 转换，MCP 无此层）
    result.items = [InvoiceOut.model_validate(item, from_attributes=True) for item in result.items]
    # 深链：列表级给一条带同组筛选的链接（items 不逐条出票——防 N 张票与响应膨胀）
    result.web_url = web_links.web_link(
        "/invoices", user_id=user.id, status=status, keyword=keyword,
        expense_type=expense_type, period=web_links.period_of(date_from, date_to),
    )
    return result


@requires("invoice:read")
def get_invoice_mcp(invoice_id: int) -> InvoiceOut:
    with SessionLocal() as db:
        user = _current_user(db)
        try:
            # get_invoice 走 scoped_invoices：员工查他人发票得 404（数据范围隔离生效点）
            inv = services.get_invoice(db, user, invoice_id)
        except HTTPException as e:
            # service 层 404 泄漏到 MCP 层，映射为协议友好的错误信息
            raise ValueError(f"发票不存在或无权访问: {invoice_id}") from e
        out = InvoiceOut.model_validate(inv, from_attributes=True)
        out.web_url = web_links.web_link("/invoices", user_id=user.id, invoice_id=invoice_id)
        return out


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


@requires("invoice:write")
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
        _audit(
            db, action=AuditAction.INGEST.value, invoice_id=invoice_id,
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
            _audit(
                db, action="PARSE", invoice_id=inv.id,
                detail={"result": "not_invoice", "rejected_by": "mcp_ingest", "file_type": kind},
            )
            _cleanup_dependents_of(db, inv.id, unlink_receipts=True)  # 本记录即将物理删除
            db.delete(inv)
            db.commit()
            raise ValueError(reason)
        return InvoiceOut.model_validate(inv, from_attributes=True)


@requires("masterdata:read")
def company_info_list(kind: str | None = None) -> list[CompanyInfoOut]:
    """常用公司列表（kind 可选：self/supplier/other）。"""
    with SessionLocal() as db:
        q = db.query(CompanyInfo)
        if kind:
            q = q.filter(CompanyInfo.kind == kind)
        return [CompanyInfoOut.model_validate(i, from_attributes=True) for i in q.order_by(CompanyInfo.id).all()]


def _validate_company_info(name: str, tax_id: str, kind: str, is_default: bool) -> None:
    """保存前校验（与 REST 对齐）：tax_id 18 位 / kind 枚举 / name 非空 / 默认联动。

    提案阶段调用——非法输入不产生待确认提案（浪费一次人工确认）。
    """
    if not name or not name.strip():
        raise ValueError("公司名称不能为空")
    if not re.fullmatch(TAX_ID_PATTERN, tax_id):
        raise ValueError("税号必须为 18 位字母数字（[0-9A-Z]）")
    if kind not in {k.value for k in CompanyKind}:
        raise ValueError(f"非法类型: {kind}（可选 self/supplier/other）")
    if is_default and kind != CompanyKind.self.value:
        raise ValueError("is_default 仅适用于 kind=self")


@requires("masterdata:write")
def company_info_save_proposal(
    name: str,
    tax_id: str,
    kind: str = CompanyKind.other.value,
    is_default: bool = False,
    remark: str | None = None,
    idempotency_key: str | None = None,
    *,
    bank_account: str | None = None,  # 未对 MCP 暴露；放末尾防包装层位置转发错位
) -> dict:
    """【两段握手第一步】保存常用公司——返回待确认提案，不落库（同税号更新）。"""
    _validate_company_info(name, tax_id, kind, is_default)
    return _make_proposal(
        tool_name="company_info_save",
        payload={"name": name, "tax_id": tax_id, "kind": kind,
                 "is_default": is_default, "remark": remark,
                 "bank_account": bank_account},
        preview={"description": f"保存公司：{name}（税号 {tax_id}）",
                 "kind": kind, "is_default": is_default},
        idempotency_key=idempotency_key,
    )


@register_proposal("company_info_save")
def company_info_save(db, *, name: str, tax_id: str,
                      kind: str = CompanyKind.other.value,
                      is_default: bool = False, remark: str | None = None,
                      bank_account: str | None = None) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
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
    _audit(
        db, action=AuditAction.CONFIG_CHANGE.value,
        detail={"entity": "company_info", "tax_id": tax_id},
    )
    db.commit()
    return CompanyInfoOut.model_validate(info, from_attributes=True).model_dump(mode="json")


# ---- 报销（Agent 对话式报销，P0）------------------------------------------


def _money(v) -> str | None:
    """金额统一 2 位小数（Decimal 直接 str 会丢尾零，如 200.00 → 200）。"""
    return f"{v:.2f}" if v is not None else None


@requires("expense:write")
def expense_create_proposal(title: str, remark: str | None = None,
                            claim_type: str | None = None,
                            idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】创建报销单（草稿）——返回待确认提案，不落库。

    claim_type 选择单据类型（travel 差旅/procurement 采购/entertainment 招待/
    office 办公/welfare 福利/other 其他），事项默认继承该类型。
    用户确认后调 confirm_execute(token, "expense_create", human_ack=true) 才真正建单。
    """
    return _make_proposal(
        tool_name="expense_create",
        payload={"title": title, "remark": remark, "claim_type": claim_type},
        preview={"description": f"创建报销单：{title}", "claim_type": claim_type},
        idempotency_key=idempotency_key,
    )


@register_proposal("expense_create")
def expense_create(db, *, title: str, remark: str | None = None,
                   claim_type: str | None = None) -> dict:
    """两段握手第二步执行体（confirm_execute 专用，不注册为 MCP 工具）。"""
    from invoicing.workflow import expenses as svc

    claim = svc.create_claim(db, _current_user(db), title, remark, claim_type)
    return {
        "id": claim.id, "claim_no": claim.claim_no, "status": claim.status,
        "title": claim.title, "claim_type": claim.claim_type,
    }


@requires("expense:write")
def expense_add_entry_proposal(claim_id: int, entry_type: str, title: str,
                               occurred_on: str | None = None,
                               scene_fields: dict | None = None,
                               note: str | None = None,
                               idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】新建报销事项（费用明细行）——返回待确认提案，不落库。

    entry_type: travel（差旅，需城市+起止日期）/ procurement（采购，建议合同号订单号）/
    entertainment（招待，需对象+人数）/ office / other。

    差旅子类 scene_fields={"subtype": ...}：transport（交通：方式+出发到达城市+日期）/
    accommodation（住宿：城市+入住离店）/ local_transport（市内交通：城市+日期）/
    **allowance（伙食补助：days 天数 + daily_standard 日标准；无需发票，
    系统按 天数×标准 自动生成内部凭证并计入金额，daily_standard 缺省用公司标准）**。
    """
    return _make_proposal(
        tool_name="expense_add_entry",
        payload={"claim_id": claim_id, "entry_type": entry_type, "title": title,
                 "occurred_on": occurred_on, "scene_fields": scene_fields, "note": note},
        preview={"description": f"报销单 {claim_id} 新建事项：{title}",
                 "entry_type": entry_type},
        idempotency_key=idempotency_key,
    )


@register_proposal("expense_add_entry")
def expense_add_entry(db, *, claim_id: int, entry_type: str, title: str,
                      occurred_on: str | None = None,
                      scene_fields: dict | None = None,
                      note: str | None = None) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from datetime import date as _date

    from invoicing.workflow import expenses as svc

    occurred = _date.fromisoformat(occurred_on) if occurred_on else None
    entry = svc.create_entry(
        db, _current_user(db), claim_id, entry_type, title, occurred, scene_fields, note
    )
    return {
        "entry_id": entry.id, "claim_id": entry.claim_id, "entry_type": entry.entry_type,
        "title": entry.title, "amount": _money(entry.amount),
    }


@requires("expense:write")
def expense_add_invoices_proposal(claim_id: int, entry_id: int,
                                  invoice_numbers: list[str],
                                  expense_type: str = "other",
                                  note: str | None = None,
                                  idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】按发票号码批量加入报销单——返回待确认提案，不落库。

    实际执行时自动校验（一票一报/已验真/未拦截/归属范围），返回逐条结果。
    """
    return _make_proposal(
        tool_name="expense_add_invoices",
        payload={"claim_id": claim_id, "entry_id": entry_id,
                 "invoice_numbers": invoice_numbers, "expense_type": expense_type,
                 "note": note},
        preview={"description": f"向报销单 {claim_id} 加入 {len(invoice_numbers)} 张发票",
                 "invoice_numbers": invoice_numbers},
        idempotency_key=idempotency_key,
    )


@register_proposal("expense_add_invoices")
def expense_add_invoices(db, *, claim_id: int, entry_id: int,
                         invoice_numbers: list[str], expense_type: str = "other",
                         note: str | None = None) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.models import Invoice
    from invoicing.workflow import expenses as svc

    results = []
    user = _current_user(db)
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


@requires("expense:write")
def expense_add_receipt_proposal(claim_id: int, entry_id: int, receipt_id: int,
                                 voucher_type: str | None = None,
                                 expense_type: str = "other",
                                 note: str | None = None,
                                 idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】把银行回单/缴款书回单挂为报销凭证——返回待确认提案。

    receipt_id 用 receipt_list 查询；voucher_type 留空时按回单交易性质自动建议
    （税费/社保 → 缴款书回单 tax_receipt，其余 → 银行回单 bank_receipt）。
    """
    return _make_proposal(
        tool_name="expense_add_receipt",
        payload={"claim_id": claim_id, "entry_id": entry_id, "receipt_id": receipt_id,
                 "voucher_type": voucher_type, "expense_type": expense_type,
                 "note": note},
        preview={"description": f"向报销单 {claim_id} 挂回单 {receipt_id}"},
        idempotency_key=idempotency_key,
    )


@register_proposal("expense_add_receipt")
def expense_add_receipt(db, *, claim_id: int, entry_id: int, receipt_id: int,
                        voucher_type: str | None = None,
                        expense_type: str = "other",
                        note: str | None = None) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.workflow import expenses as svc

    item = svc.add_receipt(db, _current_user(db), claim_id, receipt_id, entry_id,
                           voucher_type, expense_type, note)
    claim = svc._get_claim(db, claim_id)
    return {
        "claim_id": claim.id, "claim_no": claim.claim_no, "entry_id": entry_id,
        "total_amount": _money(claim.total_amount),
        "item": {"id": item.id, "voucher_type": item.voucher_type,
                 "amount": _money(item.amount), "receipt_id": item.receipt_id},
    }


@requires("expense:write")
def expense_add_voucher_proposal(claim_id: int, entry_id: int, voucher_type: str,
                                 amount: str, expense_type: str = "other",
                                 note: str | None = None,
                                 payee_name: str | None = None,
                                 payee_id_no: str | None = None,
                                 idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】无票支出人工凭证——返回待确认提案，不落库。

    voucher_type：receipt_voucher（收款凭证，小额零星 ≤500 元，需收款人姓名+身份证号）/
    contract（合同类，仅特殊情形可扣，需财务确认）/ overseas（境外票据）。
    """
    return _make_proposal(
        tool_name="expense_add_voucher",
        payload={"claim_id": claim_id, "entry_id": entry_id,
                 "voucher_type": voucher_type, "amount": amount,
                 "expense_type": expense_type, "note": note,
                 "payee_name": payee_name, "payee_id_no": payee_id_no},
        preview={"description": f"向报销单 {claim_id} 挂人工凭证 {voucher_type} {amount} 元"},
        idempotency_key=idempotency_key,
    )


@register_proposal("expense_add_voucher")
def expense_add_voucher(db, *, claim_id: int, entry_id: int, voucher_type: str,
                        amount: str, expense_type: str = "other",
                        note: str | None = None, payee_name: str | None = None,
                        payee_id_no: str | None = None) -> dict:
    """两段握手第二步执行体（confirm_execute 专用；按 28 号公告校验扣除资格）。"""
    from decimal import Decimal

    from invoicing.workflow import expenses as svc

    item = svc.add_manual_voucher(
        db, _current_user(db), claim_id, entry_id,
        voucher_type=voucher_type, amount=Decimal(str(amount)),
        expense_type=expense_type, note=note,
        payee_name=payee_name, payee_id_no=payee_id_no,
    )
    claim = svc._get_claim(db, claim_id)
    return {
        "claim_id": claim.id, "claim_no": claim.claim_no, "entry_id": entry_id,
        "total_amount": _money(claim.total_amount),
        "item": {"id": item.id, "voucher_type": item.voucher_type,
                 "amount": _money(item.amount), "deductible": item.deductible,
                 "deductible_note": item.deductible_note},
    }


@requires("expense:write")
def expense_submit_proposal(claim_id: int,
                            idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】提交报销单进入审批——返回待确认提案，不落库。

    确认执行时自动跑 validate_expense（P0-1）：FAIL 抛错 + SUBMIT_BLOCKED 审计。
    """
    return _make_proposal(
        tool_name="expense_submit",
        payload={"claim_id": claim_id},
        preview={"description": f"提交报销单 {claim_id} 进入审批"},
        idempotency_key=idempotency_key,
    )


@register_proposal("expense_submit")
def expense_submit(db, *, claim_id: int) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.workflow import expenses as svc

    claim = svc.submit_claim(db, _current_user(db), claim_id)
    return {"id": claim.id, "claim_no": claim.claim_no, "status": claim.status,
            "total_amount": _money(claim.total_amount)}


@requires("expense:read")
def expense_list(status: str | None = None, claim_type: str | None = None) -> list[dict]:
    """报销单列表（可按状态与单据类型过滤）。"""
    from invoicing.workflow import expenses as svc

    with SessionLocal() as db:
        user = _current_user(db)
        rows = svc.list_claims(db, user, status, claim_type)
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
                # 深链：报销单基数小，逐单附链接（讨论某张单时可直达）
                "web_url": web_links.web_link("/expenses", user_id=user.id, claim_id=c.id),
            }
            for c in rows
        ]


@requires_role("finance_staff", "finance_manager", "admin")
@requires("expense:approve")
def expense_approve_proposal(claim_id: int, action: str = "approve",
                             reason: str | None = None,
                             idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】审批报销单——返回待确认提案，不落库。

    action=approve/reject（reject 必填 reason）。财务通道：MCP 层 role 门 +
    scope 门双闸（service 层 `_is_finance` 仍为第三道）。
    """
    if action not in ("approve", "reject"):
        raise ValueError(f"非法审批动作: {action}（可选 approve/reject）")
    if action == "reject" and not reason:
        raise ValueError("驳回必须提供 reason")
    return _make_proposal(
        tool_name="expense_approve",
        payload={"claim_id": claim_id, "action": action, "reason": reason},
        preview={
            "description": f"{'通过' if action == 'approve' else '驳回'}"
                           f"报销单 {claim_id}",
            "reason": reason,
        },
        idempotency_key=idempotency_key,
    )


@register_proposal("expense_approve")
def expense_approve(db, *, claim_id: int, action: str = "approve",
                    reason: str | None = None) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.workflow import expenses as svc

    user = _current_user(db)
    if action == "approve":
        claim = svc.approve_claim(db, user, claim_id)
    elif action == "reject":
        claim = svc.reject_claim(db, user, claim_id, reason or "")
    else:
        raise ValueError(f"非法审批动作: {action}（可选 approve/reject）")
    return {"id": claim.id, "claim_no": claim.claim_no, "status": claim.status,
            "rejected_reason": claim.rejected_reason}


@requires("expense:read")
def expense_eligible_invoices(limit: int = 50) -> list[dict]:
    """可报销发票池（已验真、未拦截、未占用），供 Agent 建单选票。"""
    from invoicing.workflow import expenses as svc

    with SessionLocal() as db:
        rows = svc.eligible_invoices(db, _current_user(db))[:limit]
        return [
            {
                "id": i.id, "invoice_number": i.invoice_number,
                "issue_date": str(i.issue_date) if i.issue_date else None,
                "seller_name": i.seller_name, "total_amount": _money(i.total_amount),
            }
            for i in rows
        ]


@requires("masterdata:read")
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


@requires("masterdata:write")
def bank_account_save_proposal(
    account_no: str,
    account_name: str | None = None,
    bank_name: str | None = None,
    remark: str | None = None,
    is_default: bool = False,
    enabled: bool = True,
    idempotency_key: str | None = None,
    *,
    bank_code: str | None = None,  # 未对 MCP 暴露；放末尾防包装层位置转发错位
) -> dict:
    """【两段握手第一步】新增/更新本司银行账号——返回待确认提案，不落库。

    账号规范化去空格连字符，须 6-32 位数字（提案阶段校验）。
    """
    normalized = re.sub(r"[\s\-]", "", account_no or "")
    if not re.fullmatch(r"[0-9]{6,32}", normalized):
        raise ValueError("账号须为 6-32 位数字")
    return _make_proposal(
        tool_name="bank_account_save",
        payload={"account_no": normalized, "account_name": account_name,
                 "bank_name": bank_name, "bank_code": bank_code,
                 "remark": remark, "is_default": is_default, "enabled": enabled},
        preview={"description": f"保存银行账号：{normalized[-4:].rjust(len(normalized), '*')}"
                                f"（{account_name or '未命名'}）",
                 "bank_name": bank_name, "is_default": is_default},
        idempotency_key=idempotency_key,
    )


@register_proposal("bank_account_save")
def bank_account_save(db, *, account_no: str, account_name: str | None = None,
                      bank_name: str | None = None, bank_code: str | None = None,
                      remark: str | None = None, is_default: bool = False,
                      enabled: bool = True) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.models import BankAccount

    normalized = account_no
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
    _audit(
        db, action=AuditAction.CONFIG_CHANGE.value,
        detail={"entity": "bank_account", "account_no": normalized, "created": created},
    )
    db.commit()
    return {
        "id": acc.id, "account_no": acc.account_no, "account_name": acc.account_name,
        "bank_name": acc.bank_name, "remark": acc.remark,
        "is_default": acc.is_default, "enabled": acc.enabled,
    }


@requires("masterdata:write")
def bank_account_delete_proposal(id: int,
                                 idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】删除本司银行账号——返回待确认提案，不落库。"""
    return _make_proposal(
        tool_name="bank_account_delete",
        payload={"id": id},
        preview={"description": f"删除银行账号 id={id}（不可撤销）"},
        idempotency_key=idempotency_key,
    )


@register_proposal("bank_account_delete")
def bank_account_delete(db, *, id: int) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.models import BankAccount

    acc = db.get(BankAccount, id)
    if acc is None:
        raise ValueError(f"账号不存在: {id}")
    db.delete(acc)
    _audit(
        db, action=AuditAction.CONFIG_CHANGE.value,
        detail={"entity": "bank_account", "id": id, "deleted": True},
    )
    db.commit()
    return {"ok": True}


@requires("masterdata:write")
def company_info_delete_proposal(id: int,
                                 idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】删除常用公司——返回待确认提案，不落库。"""
    return _make_proposal(
        tool_name="company_info_delete",
        payload={"id": id},
        preview={"description": f"删除常用公司 id={id}（不可撤销）"},
        idempotency_key=idempotency_key,
    )


@register_proposal("company_info_delete")
def company_info_delete(db, *, id: int) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    info = db.get(CompanyInfo, id)
    if info is None:
        raise ValueError(f"记录不存在: {id}")
    db.delete(info)
    _audit(
        db, action=AuditAction.CONFIG_CHANGE.value,
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


@requires_role("finance_staff", "finance_manager", "admin")
@requires("invoice:write")
def invoice_update_proposal(
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
    idempotency_key: str | None = None,
) -> dict:
    """【两段握手第一步】更新发票业务字段（人工复核纠正）——返回待确认提案。

    仅传入非 None 字段生效；状态变更走 review/verify。确认后调
    confirm_execute(token, 'invoice_update', human_ack=true)。
    """
    fields = {
        "invoice_number": invoice_number, "issue_date": issue_date,
        "amount_without_tax": amount_without_tax, "tax_amount": tax_amount,
        "total_amount": total_amount, "total_amount_cn": total_amount_cn,
        "seller_name": seller_name, "seller_tax_id": seller_tax_id,
        "buyer_name": buyer_name, "buyer_tax_id": buyer_tax_id,
        "invoice_type": invoice_type, "review_note": review_note,
    }
    changed = {k: v for k, v in fields.items() if v is not None}
    return _make_proposal(
        tool_name="invoice_update",
        payload={"invoice_id": invoice_id, **fields},
        preview={"description": f"修改发票 {invoice_id}：{'、'.join(changed) or '无字段变化'}",
                 "fields": changed},
        idempotency_key=idempotency_key,
    )


@register_proposal("invoice_update")
def invoice_update(db, *, invoice_id: int, invoice_number: str | None = None,
                   issue_date: str | None = None, amount_without_tax: str | None = None,
                   tax_amount: str | None = None, total_amount: str | None = None,
                   total_amount_cn: str | None = None, seller_name: str | None = None,
                   seller_tax_id: str | None = None, buyer_name: str | None = None,
                   buyer_tax_id: str | None = None, invoice_type: str | None = None,
                   review_note: str | None = None) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.schemas.invoice import InvoiceUpdate

    body = InvoiceUpdate(
        invoice_number=invoice_number, issue_date=issue_date,
        amount_without_tax=amount_without_tax, tax_amount=tax_amount,
        total_amount=total_amount, total_amount_cn=total_amount_cn,
        seller_name=seller_name, seller_tax_id=seller_tax_id,
        buyer_name=buyer_name, buyer_tax_id=buyer_tax_id,
        invoice_type=invoice_type, review_note=review_note,
    )
    # 传真实用户（此前传 None 是 MCP 无身份时代的残留）：审计能回答「谁改的」。
    # channel 必须显式给——服务层推断是「有 user → web」，不传会把 MCP 记成 web
    inv = _http_to_value_error(
        services.update_invoice, db, _current_user(db), invoice_id,
        body.model_dump(exclude_none=True), channel="mcp",
    )
    return InvoiceOut.model_validate(inv, from_attributes=True).model_dump(mode="json")


@requires("invoice:admin")
def invoice_delete_proposal(invoice_id: int,
                            idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】删除发票——返回待确认提案，不落库（高破坏性，务必人工确认）。"""
    return _make_proposal(
        tool_name="invoice_delete",
        payload={"invoice_id": invoice_id},
        preview={"description": f"删除发票 {invoice_id}（审计快照 + 原件清理，不可撤销）"},
        idempotency_key=idempotency_key,
    )


@register_proposal("invoice_delete")
def invoice_delete(db, *, invoice_id: int) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    return _http_to_value_error(
        services.delete_invoice, db, _current_user(db), invoice_id,
        channel="mcp"
    )


@requires("invoice:admin")
def invoice_unblock_proposal(invoice_id: int,
                             idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】人工放行被拦截发票——返回待确认提案，不落库。"""
    return _make_proposal(
        tool_name="invoice_unblock",
        payload={"invoice_id": invoice_id},
        preview={"description": f"放行被拦截发票 {invoice_id}（blocked → 待复核）"},
        idempotency_key=idempotency_key,
    )


@register_proposal("invoice_unblock")
def invoice_unblock(db, *, invoice_id: int) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    inv = _http_to_value_error(
        services.unblock_invoice, db, _current_user(db), invoice_id,
        channel="mcp"
    )
    return InvoiceOut.model_validate(inv, from_attributes=True).model_dump(mode="json")


@requires_role("finance_staff", "finance_manager", "admin")
@requires("invoice:write")
def invoice_classify_proposal(
    invoice_id: int,
    expense_type: str | None = None,
    cost_center: str | None = None,
    description: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """【两段握手第一步】费用归类——返回待确认提案，不落库。

    expense_type 不传时在确认执行阶段自动建议（travel/office/entertainment/
    procurement/other）。
    """
    return _make_proposal(
        tool_name="invoice_classify",
        payload={"invoice_id": invoice_id, "expense_type": expense_type,
                 "cost_center": cost_center, "description": description},
        preview={"description": f"归类发票 {invoice_id}"
                                + (f" → {expense_type}" if expense_type else "（自动建议）"),
                 "cost_center": cost_center, "note": description},
        idempotency_key=idempotency_key,
    )


@register_proposal("invoice_classify")
def invoice_classify(db, *, invoice_id: int, expense_type: str | None = None,
                     cost_center: str | None = None,
                     description: str | None = None) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.models import Invoice
    from invoicing.parse.classify import EXPENSE_TYPES, suggest_expense_type

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
    _audit(
        db, action="INVOICE_CLASSIFY", invoice_id=invoice_id,
        detail={"expense_type": expense_type, "cost_center": cost_center, "description": description},
    )
    db.commit()
    return InvoiceOut.model_validate(inv, from_attributes=True).model_dump(mode="json")


@requires_role("finance_staff", "finance_manager", "admin")
@requires("invoice:write")
def invoice_ai_review_proposal(invoice_id: int,
                                idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】生成/重算发票复核预判——返回待确认提案，不落库。"""
    return _make_proposal(
        tool_name="invoice_ai_review",
        payload={"invoice_id": invoice_id},
        preview={"description": f"重算发票 {invoice_id} 的 AI 复核预判（覆盖前次结论）"},
        idempotency_key=idempotency_key,
    )


@register_proposal("invoice_ai_review")
def invoice_ai_review(db, *, invoice_id: int) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.models import Invoice
    from invoicing.models.fields import utcnow
    from invoicing.parse.ai_review import predict_review

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
    _audit(
        db, action="AI_REVIEW", invoice_id=invoice_id,
        detail={"verdict": verdict.verdict, "reason": verdict.reason, "confidence": verdict.confidence},
    )
    db.commit()
    return InvoiceOut.model_validate(inv, from_attributes=True).model_dump(mode="json")


@requires("sales:write")
def sales_invoice_import_proposal(file_path: str,
                                  idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】导入已开票（销项，文件解析）——返回待确认提案，不落库。

    确认执行时才读取文件（XML/OFD/PDF → 入库为销项票，红票自动关联原蓝票）；
    提案与确认间隔内文件须保持可读。
    """
    from pathlib import Path

    name = Path(file_path).name
    return _make_proposal(
        tool_name="sales_invoice_import",
        payload={"file_path": file_path},
        preview={"description": f"导入销项发票文件：{name}"},
        idempotency_key=idempotency_key,
    )


@register_proposal("sales_invoice_import")
def sales_invoice_import(db, *, file_path: str) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from pathlib import Path

    from invoicing.mcp.extract import _read_file
    from invoicing.workflow import sales as sales_svc

    path = Path(file_path)
    data = _read_file(file_path)
    results = sales_svc.import_sales_files(db, _current_user(db), [(path.name, data)])
    return results[0]


@requires("sales:write")
def sales_invoice_import_list(file_path: str) -> dict:
    """导入已开票（清单批量）：开票系统导出的 CSV/Excel；含"原发票号码"列则自动关联红票。"""
    from pathlib import Path

    from invoicing.mcp.extract import _read_file
    from invoicing.workflow import sales as sales_svc

    path = Path(file_path)
    data = _read_file(file_path)
    with SessionLocal() as db:
        return sales_svc.import_sales_list(db, _current_user(db), data, path.name)


@requires("sales:read")
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


@requires("sales:write")
def red_invoice_link_proposal(red_invoice_id: int, original_invoice_id: int,
                              idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】人工补关联红字票到原蓝票——返回待确认提案，不落库。"""
    return _make_proposal(
        tool_name="red_invoice_link",
        payload={"red_invoice_id": red_invoice_id,
                 "original_invoice_id": original_invoice_id},
        preview={"description": f"把红字票 {red_invoice_id} 关联到原蓝票 {original_invoice_id}"},
        idempotency_key=idempotency_key,
    )


@register_proposal("red_invoice_link")
def red_invoice_link(db, *, red_invoice_id: int, original_invoice_id: int) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.workflow import sales as sales_svc

    inv = sales_svc.link_red_invoice(db, _current_user(db), red_invoice_id, original_invoice_id)
    return {"ok": True, "invoice_id": inv.id, "original_invoice_id": inv.original_invoice_id}


@requires("report:read")
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


@requires("report:read")
def invoice_stats(month: str) -> dict:
    """月度成本结构化统计（供图表与统计回答；单张明细用 invoice_list）。"""
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise ValueError(f"month 格式应为 YYYY-MM，收到: {month!r}")
    from invoicing.reports import monthly_cost

    with SessionLocal() as db:
        data = monthly_cost(db, month)
    return {
        "month": data["month"],
        "total_count": data["total_count"],
        "total_amount": str(data["total_amount"]),
        "total_without_tax": str(data["total_without_tax"]),
        "total_tax": str(data["total_tax"]),
        "by_type": {k: str(v) for k, v in data["by_type"].items()},
        "by_center": {k: str(v) for k, v in data["by_center"].items()},
    }


@requires("report:read")
def invoice_health_report(month: str) -> str:
    """月度健康报告（P3/R3）：老板视角收口文本，供数字员工直接引用推送。"""
    from invoicing.reports import monthly_health

    with SessionLocal() as db:
        return monthly_health(db, month)


@requires("receipt:write")
def receipt_ingest_proposal(file_path: str,
                            idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】银行回单入库——返回待确认提案，不落库。

    确认执行时才读取文件（存档 → 建批次 → 后台解析）；提案与确认间隔内文件须保持可读。
    同一文件重复提交被 file_hash 唯一约束拒绝（业务级幂等，与两段握手独立）。
    """
    from pathlib import Path

    name = Path(file_path).name
    return _make_proposal(
        tool_name="receipt_ingest",
        payload={"file_path": file_path},
        preview={"description": f"回单入库：{name}（存档 + 后台解析，解析完成后 receipt_list 可见）"},
        idempotency_key=idempotency_key,
    )


@register_proposal("receipt_ingest")
def receipt_ingest(db, *, file_path: str) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。

    R1.1 批次异步模式：存档 → 建批次 → 后台解析 → 立即返回批次号。
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


@requires_role("finance_staff", "finance_manager", "admin")
@requires("receipt:read")
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


@requires_role("finance_staff", "finance_manager", "admin")
@requires("receipt:read")
def receipt_list(month: str) -> ReceiptListResponse:
    """回单清单（P3/R1）：month=YYYY-MM。

    带 `category` / `invoice_requirement`（终审 7，与 API `_receipt_out` 同源派生）：
    Agent 侧不能只靠 status 推「无需发票 / 待开票」——status 是配对面孔，
    发票要求由交易性质派生。

    返回新增 `web_url`：列表级免登深链，落前端 /receipts 并带同月周期筛选。
    """
    from invoicing.reports import receipts_in_month
    from invoicing.workflow.receipts import requirement_of

    with SessionLocal() as db:
        user = _current_user(db)
        rows = receipts_in_month(db, month)
        items = [
            ReceiptOut(
                id=r.id,
                trade_date=str(r.trade_date) if r.trade_date else None,
                counterparty_name=r.counterparty_name,
                amount=_money(r.amount),
                abstract=r.abstract,
                direction=r.direction,
                needs_review=r.needs_review,
                quality_issues=r.quality_issues,
                bank_code=r.bank_code,
                page_no=r.page_no,
                category=r.category,
                invoice_requirement=requirement_of(r.category),
                paired_invoice_id=r.paired_invoice_id,
                status=r.status,
            )
            for r in rows
        ]
    # 深链：列表级给一条带同月周期的链接（与 invoice_list 对称）
    web_url = web_links.web_link("/receipts", user_id=user.id, period=month)
    return ReceiptListResponse(items=items, month=month, web_url=web_url)


@requires("receipt:write")
def receipt_pair_proposal(receipt_id: int, invoice_id: int,
                          idempotency_key: str | None = None) -> dict:
    """【两段握手第一步】手动配对回单与发票——返回待确认提案，不落库。"""
    return _make_proposal(
        tool_name="receipt_pair",
        payload={"receipt_id": receipt_id, "invoice_id": invoice_id},
        preview={"description": f"配对回单 {receipt_id} ↔ 发票 {invoice_id}（覆盖自动建议）"},
        idempotency_key=idempotency_key,
    )


@register_proposal("receipt_pair")
def receipt_pair(db, *, receipt_id: int, invoice_id: int) -> dict:
    """两段握手第二步执行体（confirm_execute 专用）。"""
    from invoicing.models import BankReceipt, Invoice

    r = db.get(BankReceipt, receipt_id)
    if r is None:
        raise ValueError(f"回单不存在: {receipt_id}")
    if db.get(Invoice, invoice_id) is None:
        raise ValueError(f"发票不存在: {invoice_id}")
    r.paired_invoice_id = invoice_id
    r.status = "paired"
    db.commit()
    return {"receipt_id": receipt_id, "paired_invoice_id": invoice_id, "status": "paired"}


@requires("report:read")
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
        from invoicing.workflow.receipts import is_unmatched_expense

        unmatched = [r for r in rows if is_unmatched_expense(r)]
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


# ===== P0-2 两段握手：confirm_execute 单一入口（v1.1 §7.5） =====

@requires()
def confirm_execute(token: str, tool_name: str, human_ack: bool,
                    idempotency_key: str | None = None) -> dict:
    """两段握手的第二步：消费 proposal_token 并执行注册的落库函数。

    - 未注册的 tool_name → KeyError（软白名单，P0-3 升硬白名单）
    - human_ack 必须为 true（v1.1 §7.5；缺省视为未确认）
    - token 一次性、15 分钟 TTL、绑定发起主体（跨主体拒绝）
    - 同 idempotency_key 重放返回首次结果（24h 窗口），不重复执行
    """
    from invoicing.idempotency import consume_proposal, idempotent_run
    from invoicing.mcp.identity import current_principal
    from invoicing.mcp.proposal_registry import get_proposal

    proposal_fn = get_proposal(tool_name)  # KeyError：非两段工具
    principal = current_principal()
    if principal.user_id is None:
        # fail-closed（与 _current_user 同口径）：无主体不得确认任何提案
        raise ValueError("当前令牌未绑定主体，无法确认写操作")

    with SessionLocal() as db:
        def _execute():
            try:
                proposal = consume_proposal(db, token=token, human_ack=human_ack)
            except ValueError as e:
                # 执行体上次失败时 token 已烧；重试只会撞 already consumed，
                # 真实错误（如 validate FAIL）已被吞。给出可行动指引而非干巴巴报错。
                if "already consumed" in str(e):
                    raise ValueError(
                        f"{e}（若上次确认在执行中途失败，该 token 已失效且原错误见上次返回——"
                        "请重新调用 *_proposal 发起新提案后再确认）"
                    ) from None
                raise
            if proposal.tool_name != tool_name:
                # 防张冠李戴：执行哪个函数必须与用户预览过的提案一致
                raise ValueError(
                    f"proposal tool_name 不符：提案为 {proposal.tool_name}，"
                    f"请求为 {tool_name}"
                )
            if proposal.actor_id != principal.user_id:
                raise ValueError(
                    f"proposal 归属主体不符：发起 {proposal.actor_id} / 确认 {principal.user_id}"
                )
            result = proposal_fn(db, **dict(proposal.payload or {}))
            _audit(db, action=f"{tool_name.upper()}_CONFIRMED",
                   detail={"proposal_token": token, "tool_name": tool_name})
            db.commit()
            return result

        return idempotent_run(db, key=idempotency_key, tool_name=tool_name,
                              actor_id=principal.user_id, fn=_execute)


# ===== P1 业务校验工具（只读；v1.1 §5.3 Skill 的编排依赖） =====
# 只读契约：不写业务库、不写审计、不签发提案（spec §7.1 ❌1）

@requires("expense:read")
def validate_trip_consistency(claim_id: int) -> dict:
    """行程一致性校验（只读）：返程缺失/行程不接续/住宿晚数矛盾/日期矛盾。

    返回 {"ok": bool, "issues": [{"code","message","severity","entry_ids"}]}。
    severity=warning 表示需人工判断（spec §4.2 转人工分支），error 为逻辑矛盾。
    """
    from invoicing.models import ExpenseEntry
    from invoicing.workflow.validators import check_trip

    with SessionLocal() as db:
        claim = _claim_or_raise(db, claim_id)
        rows = (db.query(ExpenseEntry)
                .filter(ExpenseEntry.claim_id == claim.id).all())
        entries = [(e.id, e.scene_fields or {},
                    str(e.occurred_on) if e.occurred_on else None)
                   for e in rows]
        issues = check_trip(entries)
    return _issues_out(issues)


@requires("expense:read")
def validate_meal_compliance(claim_id: int) -> dict:
    """餐补/招待合规校验（只读）：日标准与人均标准 vs 政策表（含容忍值）。

    severity：超标准但在容忍值内 = warning；超容忍值 = error；
    未配置政策 = warning（不把"没配标准"当违规）。
    """
    from invoicing.models import ExpenseEntry
    from invoicing.workflow.policy_service import get_policy
    from invoicing.workflow.validators import check_meal

    with SessionLocal() as db:
        claim = _claim_or_raise(db, claim_id)
        rows = (db.query(ExpenseEntry)
                .filter(ExpenseEntry.claim_id == claim.id).all())

        def _lookup(*, category: str, item_key: str, city_tier: str):
            return get_policy(db, tenant_id=claim.tenant_id, category=category,
                              item_key=item_key, city_tier=city_tier)

        entries = [(e.id, e.entry_type, e.scene_fields or {},
                    str(e.occurred_on) if e.occurred_on else None,
                    e.amount or 0)
                   for e in rows]
        issues = check_meal(entries, policy_lookup=_lookup)
    return _issues_out(issues)


@requires("expense:read")
def suggest_claim_for_invoice(invoice_id: int) -> dict:
    """补录归属建议（只读）：该票最可能挂到哪张草稿报销单。

    返回 {"candidates": [{"claim_id","claim_no","score","reasons"}]}（≤3 条，按分降序）。
    仅推荐草稿单；日期门控 ±7 天，防误挂。
    """
    from invoicing.models import (
        ExpenseClaim,
        ExpenseClaimStatus,
        ExpenseEntry,
        Invoice,
    )
    from invoicing.workflow.validators import suggest_claims_for_invoice as _suggest

    with SessionLocal() as db:
        inv = db.get(Invoice, invoice_id)
        if inv is None:
            raise ValueError(f"发票不存在: {invoice_id}")
        drafts = (db.query(ExpenseClaim)
                  .filter(ExpenseClaim.status == ExpenseClaimStatus.DRAFT.value)
                  .all())
        # 事项日期（用于日期门控与打分）
        claim_ids = [c.id for c in drafts]
        dates_by_claim: dict[int, list] = {cid: [] for cid in claim_ids}
        if claim_ids:
            for eid, cid, occurred in (
                db.query(ExpenseEntry.id, ExpenseEntry.claim_id, ExpenseEntry.occurred_on)
                .filter(ExpenseEntry.claim_id.in_(claim_ids)).all()
            ):
                if occurred is not None:
                    dates_by_claim[cid].append(occurred)

        class _View:  # 轻量视图，避免把 ORM 对象漏进纯函数
            def __init__(self, c):
                self.id, self.claim_no = c.id, c.claim_no
                self.claim_type = c.claim_type
                self.entry_dates = dates_by_claim.get(c.id, [])

        suggestions = _suggest(inv, [_View(c) for c in drafts])

    return {"candidates": [
        {"claim_id": s.claim_id, "claim_no": s.claim_no,
         "score": s.score, "reasons": s.reasons}
        for s in suggestions
    ]}


def _claim_or_raise(db, claim_id: int):
    from invoicing.models import ExpenseClaim

    claim = db.get(ExpenseClaim, claim_id)
    if claim is None:
        raise ValueError(f"报销单不存在: {claim_id}")
    return claim


def _issues_out(issues) -> dict:
    """统一的校验出参。

    - `ok`      = 无 error（不阻断；warning 仍需人工确认）
    - `outcome` = PASS / NEEDS_REVIEW / FAIL（与 validate_expense 同词表，便于 Skill 统一处理）
    """
    has_error = any(i.severity == "error" for i in issues)
    has_warning = any(i.severity == "warning" for i in issues)
    outcome = "FAIL" if has_error else ("NEEDS_REVIEW" if has_warning else "PASS")
    return {
        "ok": not has_error,
        "outcome": outcome,
        "issues": [
            {"code": i.code, "message": i.message, "severity": i.severity,
             "entry_ids": i.entry_ids}
            for i in issues
        ],
    }
