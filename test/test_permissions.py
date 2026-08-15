from invoicing.permissions import has_action


def test_action_matrix():
    assert has_action("employee", "view_invoice")
    assert not has_action("employee", "review_invoice")
    assert has_action("finance_staff", "review_invoice")
    assert not has_action("finance_staff", "approve_invoice")
    assert has_action("finance_manager", "approve_invoice")
    assert has_action("admin", "anything")
