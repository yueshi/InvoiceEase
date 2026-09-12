from invoicing.models import Invoice


TRANSITIONS: dict[str, set[str]] = {
    "receiving": {"received"},
    "received": {"parsing"},
    "parsing": {"parsed", "pending_review", "blocked"},
    "parsed": {"verifying"},
    "verifying": {"pending_submit", "pending_review", "blocked"},
    "pending_review": {"pending_submit", "rejected", "verifying"},
    "pending_submit": {"verifying", "submitted"},
    # blocked 双出口（C4 修复）：
    # - "pending_review"：人工 unblock 路径
    # - "parsed"：仅由 update_invoice + _revalidate_invoice 三态 VALID 触发，
    #   REST/MCP 端点不暴露新边，仅自动恢复路径使用（audit 留痕）
    "blocked": {"pending_review", "parsed"},
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
