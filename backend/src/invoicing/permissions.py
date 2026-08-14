from invoicing.models.enums import Role

ROLE_ACTIONS: dict[str, set[str]] = {
    Role.employee.value: {"view_invoice"},
    Role.finance_staff.value: {"view_invoice", "review_invoice", "reverify_invoice"},
    Role.finance_manager.value: {
        "view_invoice",
        "review_invoice",
        "reverify_invoice",
        "approve_invoice",
    },
    Role.admin.value: {"*"},
}


def has_action(role: str, action: str) -> bool:
    actions = ROLE_ACTIONS.get(role, set())
    return "*" in actions or action in actions
