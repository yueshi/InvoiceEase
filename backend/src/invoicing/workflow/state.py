from invoicing.models import Invoice


TRANSITIONS: dict[str, set[str]] = {
    "receiving": {"received"},
    "received": {"parsing"},
    "parsing": {"parsed", "pending_review", "blocked"},
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
