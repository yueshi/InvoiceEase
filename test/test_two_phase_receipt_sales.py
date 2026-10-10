"""P0-2 receipt_* / sales_* / red_* 两段握手测试（v1.1 §7.5）。"""
from datetime import date
from decimal import Decimal

import pytest

from invoicing.mcp import tools as mt
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY
from invoicing.models import BankReceipt, Invoice


def _seed_receipt(db, amount="50.00") -> BankReceipt:
    r = BankReceipt(file_url="tp/r.pdf", file_type="PDF", counterparty_name="某某",
                    amount=Decimal(amount), trade_date=date(2026, 6, 1))
    db.add(r)
    db.commit()
    return r


def _seed_invoice(db, number="24312000000000077001") -> Invoice:
    inv = Invoice(file_url=f"tp/{number}.xml", file_type="XML", invoice_number=number,
                  status="pending_submit", total_amount=Decimal("50.00"),
                  amount_without_tax=Decimal("50.00"), tax_amount=Decimal("0"),
                  issue_date=date(2026, 6, 1))
    db.add(inv)
    db.commit()
    return inv


def _confirm(tool, proposal):
    return mt.confirm_execute(token=proposal["proposal_token"],
                              tool_name=tool, human_ack=True)


def test_receipt_sales_tools_registered():
    expected = {"receipt_ingest", "receipt_pair",
                "sales_invoice_import", "red_invoice_link"}
    assert expected <= set(PROPOSAL_REGISTRY)


# ---- receipt_ingest --------------------------------------------------------

def test_receipt_ingest_proposal_zero_side_effect(db, tmp_path, mcp_admin_auth,
                                                   monkeypatch):
    from invoicing.models import ReceiptUpload
    p = tmp_path / "r.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    prop = mt.receipt_ingest_proposal(str(p))
    assert prop["proposal_token"]
    assert "r.pdf" in prop["preview"]["description"]
    assert db.query(ReceiptUpload).count() == 0  # 提案阶段零副作用


def test_receipt_ingest_confirm_batches(db, tmp_path, mcp_admin_auth, monkeypatch):
    from invoicing.storage import LocalFileStorage
    monkeypatch.setattr("invoicing.storage.get_storage",
                        lambda: LocalFileStorage(root=str(tmp_path / "orig")))
    monkeypatch.setattr("invoicing.mcp.tools.enqueue_receipt_parse_sync", lambda uid: None)
    p = tmp_path / "r2.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    out = _confirm("receipt_ingest", mt.receipt_ingest_proposal(str(p)))
    assert out["status"] == "parsing"
    assert out["upload_id"] > 0


# ---- receipt_pair ----------------------------------------------------------

def test_receipt_pair_proposal_and_confirm(db, mcp_admin_auth):
    r = _seed_receipt(db)
    inv = _seed_invoice(db)
    prop = mt.receipt_pair_proposal(r.id, inv.id)
    assert "配对" in prop["preview"]["description"]
    db.expire_all()
    assert db.get(BankReceipt, r.id).status != "paired"  # 未变

    out = _confirm("receipt_pair", prop)
    assert out["status"] == "paired"
    assert out["paired_invoice_id"] == inv.id


def test_receipt_pair_missing_receipt_rejected_at_confirm(db, mcp_admin_auth):
    inv = _seed_invoice(db, number="24312000000000077002")
    prop = mt.receipt_pair_proposal(999999, inv.id)
    with pytest.raises(ValueError, match="回单不存在"):
        _confirm("receipt_pair", prop)


# ---- sales_invoice_import --------------------------------------------------

def test_sales_invoice_import_proposal_and_missing_file(db, tmp_path, mcp_admin_auth):
    """提案只看文件名；确认阶段文件缺失 → 明确报错（文件须存活到确认）。"""
    prop = mt.sales_invoice_import_proposal(str(tmp_path / "nonexistent.xml"))
    assert "nonexistent.xml" in prop["preview"]["description"]
    with pytest.raises(ValueError):
        _confirm("sales_invoice_import", prop)


# ---- red_invoice_link ------------------------------------------------------

def test_red_invoice_link_proposal_and_confirm(db, mcp_admin_auth):
    red = Invoice(file_url="tp/red.xml", file_type="XML",
                  invoice_number="24312000000000077003",
                  status="pending_submit", total_amount=Decimal("-50.00"),
                  amount_without_tax=Decimal("-50.00"), tax_amount=Decimal("0"),
                  issue_date=date(2026, 6, 1), invoice_direction="output",
                  red_flag=True)
    blue = Invoice(file_url="tp/blue.xml", file_type="XML",
                   invoice_number="24312000000000077004",
                   status="pending_submit", total_amount=Decimal("50.00"),
                   amount_without_tax=Decimal("50.00"), tax_amount=Decimal("0"),
                   issue_date=date(2026, 6, 1), invoice_direction="output")
    db.add_all([red, blue])
    db.commit()

    prop = mt.red_invoice_link_proposal(red.id, blue.id)
    assert "关联" in prop["preview"]["description"]
    out = _confirm("red_invoice_link", prop)
    assert out["original_invoice_id"] == blue.id