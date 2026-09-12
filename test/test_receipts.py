"""银行回单测试（R1：解析 + 配对建议 + 凭证草稿）。"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import BankReceipt, Invoice, Role, User
from invoicing.parse.receipt import parse_receipt_text, parse_receipts_text, suggest_pair
from invoicing.reports import receipts_to_csv
from invoicing.security import hash_password


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def _seed_login(client, db, username="caiwu_r", role=Role.finance_staff.value):
    db.add(User(username=username, password_hash=hash_password("pass123"), role=role))
    db.commit()
    token = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "pass123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_parse_receipt_text():
    """规则通道：金额/对方户名/日期/摘要从回单文本提取。"""
    text = (
        "交易日期 2026-08-05\n"
        "对方户名 北京某某科技有限公司\n"
        "交易金额 1,000.00\n"
        "摘要 货款\n"
    )
    parsed = parse_receipt_text(text)
    assert parsed["amount"] == Decimal("1000.00")
    assert "北京某某科技有限公司" in parsed["counterparty_name"]
    assert parsed["trade_date"] == date(2026, 8, 5)
    assert parsed["abstract"] == "货款"


def test_parse_receipt_text_ccb_messy_transfer_date():
    """建行回单 PDF 文本层乱序：`转账日期： 年 月 日2026 04 20`（占位符在值前、空格分隔）
    → 日期仍可提取。"""
    text = (
        "凭证字号：30012026042003189400转账日期： 年 月 日2026 04 20\n"
        "户名：西安启智合创科技有限公司 账号： 61050174004100000779\n"
        "小写（合计）金额：￥1,116.00\n"
    )
    parsed = parse_receipt_text(text)
    assert parsed is not None
    assert parsed["trade_date"] == date(2026, 4, 20)
    assert parsed["amount"] == Decimal("1116.00")


def test_list_receipts_includes_null_trade_date_in_created_month(client, db):
    """缺 trade_date 的回单按 created_at 归月，不得在任何月份隐身。"""
    auth = _seed_login(client, db)
    r = BankReceipt(  # trade_date 缺失（规则/LLM 均未提取出日期）
        file_url="r.pdf", file_type="PDF", counterparty_name="某某公司",
        amount=Decimal("1116.00"), status="unmatched",
    )
    db.add(r)
    db.commit()
    # created_at 为当前月（utcnow），当月查询必须可见
    resp = client.get("/api/v1/receipts?month=2026-09", headers=auth)
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert r.id in ids


def test_receipts_csv_includes_null_trade_date_row(db):
    """凭证草稿 CSV：缺日期回单按 created_at 归月，不得静默丢失。"""
    r = BankReceipt(
        file_url="r.pdf", file_type="PDF", counterparty_name="某某公司",
        amount=Decimal("1116.00"), status="unmatched",
    )
    db.add(r)
    db.commit()
    csv_data = receipts_to_csv(db, "2026-09").decode("utf-8-sig")
    assert "1116.00" in csv_data


_BLOCK_MARKER = "此回单以客户真实交易为依据，可通过建行网站校验真伪。"


def test_parse_receipts_text_multi_block():
    """一份 PDF 多张回单（块尾免责声明分隔）→ 逐块提取，各得一条。"""
    text = (
        "户名：西安某公司\n交易金额：3,500.00\n" + _BLOCK_MARKER + "\n"
        "户名：北京某公司\n交易金额：46.50\n" + _BLOCK_MARKER + "\n"
    )
    results = parse_receipts_text(text)
    assert [r["amount"] for r in results] == [Decimal("3500.00"), Decimal("46.50")]
    assert [r["counterparty_name"] for r in results] == ["西安某公司", "北京某公司"]


def test_parse_receipts_text_single_block_compat():
    """无块标记的单文档文本 → 整体一块，返回单条（兼容单张回单）。"""
    text = "交易日期 2026-08-05\n对方户名 北京某某科技有限公司\n交易金额 1,000.00\n"
    results = parse_receipts_text(text)
    assert len(results) == 1
    assert results[0]["amount"] == Decimal("1000.00")
    assert results[0]["trade_date"] == date(2026, 8, 5)


def test_upload_receipt_multi_block_creates_rows(client, db, monkeypatch, tmp_path):
    """上传多回单 PDF → 一次入库 N 条（共享原件 URL），API 返回数组。"""
    from invoicing.storage import LocalFileStorage

    auth = _seed_login(client, db)
    monkeypatch.setattr(
        "invoicing.storage.get_storage",
        lambda: LocalFileStorage(root=str(tmp_path / "orig")),
    )
    text = (
        "户名：西安某公司\n交易金额：3,500.00\n" + _BLOCK_MARKER + "\n"
        "户名：北京某公司\n交易金额：46.50\n" + _BLOCK_MARKER + "\n"
    )
    monkeypatch.setattr(
        "invoicing.parse.pdf_text_parser.extract_pdf_text", lambda data: text
    )
    p = tmp_path / "multi.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    with open(p, "rb") as fh:
        resp = client.post(
            "/api/v1/receipts/upload",
            headers=auth,
            files={"file": ("multi.pdf", fh, "application/pdf")},
        )
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert {r["amount"] for r in rows} == {"3500.00", "46.50"}
    assert db.query(BankReceipt).count() == 2
    assert len({r.file_url for r in db.query(BankReceipt).all()}) == 1  # 共享原件


def test_parse_receipt_fee_table_header_not_amount():
    """手续费明细表：『…手续费 金额』表头跨行是产品编号，真金额在 ￥ 锚点——
    不得把表头后的编号当成金额。"""
    text = (
        "户名：西安启智合创科技有限公司\n"
        "项目名称 工本费/转账汇款手续费/手续费 金额\n"
        "4000212普惠远航（B版） ￥899.00 ￥899.00\n"
        "合计金额 （大写）人民币捌佰玖拾玖元整 ￥899.00\n"
    )
    parsed = parse_receipt_text(text)
    assert parsed is not None
    assert parsed["amount"] == Decimal("899.00")


def test_parse_receipt_missing_fields_returns_none():
    """关键字段缺失 → None（不产半成品，LLM 兜底由调用方处理）。"""
    assert parse_receipt_text("没有金额和户名的文本") is None


def test_pair_by_amount_and_normalized_name(db):
    """配对：金额相等 + 户名规范化后包含。"""
    inv = Invoice(
        file_url="a.xml", file_type="XML", invoice_number="24312000000012345678",
        status="pending_submit", total_amount=Decimal("1000.00"),
        seller_name="北京某某科技有限公司", issue_date=date(2026, 8, 5),
    )
    db.add(inv)
    db.flush()
    r = BankReceipt(
        file_url="r.pdf", file_type="PDF", trade_date=date(2026, 8, 5),
        counterparty_name="北京某某科技", amount=Decimal("1000.00"),
    )
    db.add(r)
    db.flush()
    assert suggest_pair(db, r.id) == inv.id


def test_pair_amount_mismatch_no_pair(db):
    """金额不等 → 不配对。"""
    inv = Invoice(
        file_url="a.xml", file_type="XML", invoice_number="24312000000012345679",
        status="pending_submit", total_amount=Decimal("1000.00"),
        seller_name="北京某某科技有限公司", issue_date=date(2026, 8, 5),
    )
    db.add(inv)
    db.flush()
    r = BankReceipt(
        file_url="r.pdf", file_type="PDF", trade_date=date(2026, 8, 5),
        counterparty_name="北京某某科技", amount=Decimal("999.99"),
    )
    db.add(r)
    db.flush()
    assert suggest_pair(db, r.id) is None


def test_receipts_csv_has_voucher_columns(db):
    """凭证草稿 CSV：金蝶/用友通用列（日期/摘要/对方户名/金额/借贷方）。"""
    r = BankReceipt(
        file_url="r.pdf", file_type="PDF", trade_date=date(2026, 8, 5),
        counterparty_name="某某公司", amount=Decimal("1000.00"), abstract="货款",
    )
    db.add(r)
    db.flush()
    csv_data = receipts_to_csv(db, "2026-08").decode("utf-8-sig")
    header = csv_data.splitlines()[0]
    assert "日期" in header
    assert "借方" in header
    assert "1000.00" in csv_data
