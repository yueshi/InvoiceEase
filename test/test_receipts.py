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


def db_get(model, id_):
    from invoicing.db import SessionLocal

    with SessionLocal() as s:
        return s.get(model, id_)


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
    """多回单 PDF 上传 → 建批次记录（解析在后台；入库行为由 worker 测试覆盖）。"""
    from invoicing.models import ReceiptUpload
    from invoicing.storage import LocalFileStorage

    auth = _seed_login(client, db)
    monkeypatch.setattr(
        "invoicing.storage.get_storage",
        lambda: LocalFileStorage(root=str(tmp_path / "orig")),
    )
    monkeypatch.setattr("invoicing.api.receipts.enqueue_receipt_parse_sync", lambda uid: None)
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
    body = resp.json()
    up = db.get(ReceiptUpload, body["upload_id"])
    assert up is not None and up.status == "parsing"
    assert up.file_hash  # 防重哈希已落库


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


# 建行税票回单块的真实文本结构（脱敏）：字段标签是「付款人全称/收款国库名称」，
# 与通用规则的「对方户名」完全不同——这是此前 17/19 块规则失败、被迫逐块调 LLM 的根因。
CCB_TAX_CHUNK = (
    "，可通过建行网站(www.ccb.com)校验真伪。电子回单可重复打印，请勿重复记账。\n"
    "纳税人全称及 西安启智合创科技有限公司\n"
    "纳税人识别号（信用代码）： 91610113MA7MHLFY36\n"
    "付款人全称： 西安启智合创科技有限公司 咨询（投诉）电话：95533\n"
    "付款人账号： 61050174004100000779 征收机关名称（委托方）： 国家税务总局西咸新区税务局\n"
    "付款人开户银行： 建行西安蓝湖树小区支行 收款国库（银行）名称： 国家金库陕西省西咸新区支库\n"
    "小写（合计）金额：￥1,116.00 缴款书交易流水号： 20260420113137616000009584183300\n"
    "大写（合计）金额：人民币壹仟壹佰壹拾陆元整 税票号码： 461016260410560121\n"
    "  税（费）种名称 所属时期 实缴金额\n"
    "企业职工基本养老保险费                         20260401 20260430    1116.00\n"
    "凭证字号：30012026042003189400转账日期： 年 月 日2026 04 20\n"
)


def test_parse_receipt_text_ccb_tax_receipt():
    """税票回单规则直解：收款方取国库/征收机关（付款人全称是本司，不能当对方户名）。"""
    parsed = parse_receipt_text(CCB_TAX_CHUNK)
    assert parsed is not None
    assert parsed["amount"] == Decimal("1116.00")
    party = parsed["counterparty_name"] or ""
    assert "国家金库" in party or "税务局" in party
    assert "西安启智" not in party  # 本司名称不得被当作对方户名
    assert parsed["trade_date"] == date(2026, 4, 20)


def test_parse_receipt_text_ymd_placeholder_date():
    """无日期关键词的 CCB 版式：`流水号：…年 月 日2026 05 12`（占位符在前）→ 仍可取日期。"""
    text = (
        "户名：西安某公司\n"
        "小写（合计）金额：￥899.00\n"
        "流水号：6107400412Z24UDDO0S年 月 日2026 05 12\n"
    )
    parsed = parse_receipt_text(text)
    assert parsed is not None
    assert parsed["trade_date"] == date(2026, 5, 12)


def test_upload_receipt_async_returns_parsing_immediately(client, db, monkeypatch, tmp_path):
    """上传立即返回 parsing（解析在后台线程），不再同步等待 LLM。"""
    from invoicing.models import ReceiptUpload
    from invoicing.storage import LocalFileStorage

    auth = _seed_login(client, db)
    monkeypatch.setattr(
        "invoicing.storage.get_storage",
        lambda: LocalFileStorage(root=str(tmp_path / "orig")),
    )
    called_in_request = []
    # 解析必须发生在后台（enqueue），请求路径不得同步调用解析
    monkeypatch.setattr(
        "invoicing.api.receipts.enqueue_receipt_parse_sync",
        lambda upload_id: called_in_request.append(upload_id),
    )
    p = tmp_path / "receipt.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    with open(p, "rb") as fh:
        resp = client.post(
            "/api/v1/receipts/upload",
            headers=auth,
            files={"file": ("receipt.pdf", fh, "application/pdf")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "parsing" and body["upload_id"] > 0
    assert called_in_request == [body["upload_id"]]  # 已入队
    up = db.get(ReceiptUpload, body["upload_id"])
    assert up is not None and up.status == "parsing"
    assert db.query(BankReceipt).count() == 0  # 请求时未入库


def test_upload_receipt_duplicate_hash_409(client, db, monkeypatch, tmp_path):
    """同一文件重复上传 → 409（file_hash 唯一），附已有批次信息。"""
    from invoicing.storage import LocalFileStorage

    auth = _seed_login(client, db)
    monkeypatch.setattr(
        "invoicing.storage.get_storage",
        lambda: LocalFileStorage(root=str(tmp_path / "orig")),
    )
    monkeypatch.setattr("invoicing.api.receipts.enqueue_receipt_parse_sync", lambda uid: None)
    p = tmp_path / "receipt.pdf"
    p.write_bytes(b"%PDF-1.4 same-bytes")
    with open(p, "rb") as fh:
        first = client.post(
            "/api/v1/receipts/upload",
            headers=auth,
            files={"file": ("receipt.pdf", fh, "application/pdf")},
        )
    assert first.status_code == 200
    with open(p, "rb") as fh:
        second = client.post(
            "/api/v1/receipts/upload",
            headers=auth,
            files={"file": ("receipt-rename.pdf", fh, "application/pdf")},
        )
    assert second.status_code == 409
    assert "已上传" in second.json()["detail"]


def test_list_receipt_uploads_endpoint(client, db, monkeypatch, tmp_path):
    """GET /receipts/uploads：最近批次状态（前端轮询用）。"""
    from invoicing.models import ReceiptUpload
    from invoicing.storage import LocalFileStorage

    auth = _seed_login(client, db)
    monkeypatch.setattr(
        "invoicing.storage.get_storage",
        lambda: LocalFileStorage(root=str(tmp_path / "orig")),
    )
    monkeypatch.setattr("invoicing.api.receipts.enqueue_receipt_parse_sync", lambda uid: None)
    p = tmp_path / "receipt.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    with open(p, "rb") as fh:
        client.post(
            "/api/v1/receipts/upload",
            headers=auth,
            files={"file": ("receipt.pdf", fh, "application/pdf")},
        )
    resp = client.get("/api/v1/receipts/uploads", headers=auth)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "parsing"
    assert rows[0]["receipt_count"] == 0


def test_mcp_receipt_ingest_async_batch(db, tmp_path, monkeypatch):
    """MCP receipt_ingest 切批次模式：立即返回批次号（解析入队），重复提交 ValueError。"""
    from invoicing.models import ReceiptUpload
    from invoicing.mcp.tools import receipt_ingest, receipt_upload_status
    from invoicing.storage import LocalFileStorage

    monkeypatch.setattr(
        "invoicing.storage.get_storage",
        lambda: LocalFileStorage(root=str(tmp_path / "orig")),
    )
    monkeypatch.setattr(
        "invoicing.mcp.tools.enqueue_receipt_parse_sync", lambda uid: None
    )
    p = tmp_path / "receipt.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    result = receipt_ingest(str(p))
    assert result["status"] == "parsing"
    assert result["upload_id"] > 0
    up = db_get(ReceiptUpload, result["upload_id"])
    assert up is not None and up.status == "parsing"

    # WorkBuddy 重复提交同一文件 → ValueError 附已有批次信息
    import pytest

    with pytest.raises(ValueError, match="已上传过"):
        receipt_ingest(str(p))

    # 状态查询工具
    st = receipt_upload_status(result["upload_id"])
    assert st["id"] == result["upload_id"]
    assert st["status"] == "parsing"


def test_receipt_file_endpoint_serves_original(client, db, monkeypatch, tmp_path):
    """回单原件查看端点：财务角色可下载原始文件（人工核对/补录用）。"""
    from invoicing.storage import LocalFileStorage

    auth = _seed_login(client, db)
    storage = LocalFileStorage(root=str(tmp_path / "orig"))
    monkeypatch.setattr("invoicing.storage.get_storage", lambda: storage)
    r = BankReceipt(
        file_url="tenant-default/receipts/x.pdf", file_type="PDF",
        counterparty_name="某某公司", amount=Decimal("1116.00"), status="unmatched",
    )
    db.add(r)
    db.commit()
    storage.put(r.file_url, b"%PDF-1.4 original-bytes", "application/pdf")
    resp = client.get(f"/api/v1/receipts/{r.id}/file", headers=auth)
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 original-bytes"
    assert "application/pdf" in resp.headers["content-type"]
    assert "inline" in resp.headers.get("content-disposition", "")


def test_receipt_file_endpoint_404(client, db):
    auth = _seed_login(client, db)
    resp = client.get("/api/v1/receipts/99999/file", headers=auth)
    assert resp.status_code == 404


def test_list_receipts_quarter_filter(client, db):
    """季度过滤：Q2 = 4/5/6 月；q1 不含 4 月；month/quarter 互斥。"""
    from datetime import date as _date

    auth = _seed_login(client, db)
    rows = [
        BankReceipt(file_url="r1.pdf", file_type="PDF", trade_date=_date(2026, 4, 20),
                    counterparty_name="A", amount=Decimal("100.00"), status="unmatched"),
        BankReceipt(file_url="r2.pdf", file_type="PDF", trade_date=_date(2026, 6, 25),
                    counterparty_name="B", amount=Decimal("200.00"), status="unmatched"),
        BankReceipt(file_url="r3.pdf", file_type="PDF", trade_date=_date(2026, 7, 1),
                    counterparty_name="C", amount=Decimal("300.00"), status="unmatched"),
    ]
    for r in rows:
        db.add(r)
    db.commit()

    q2 = client.get("/api/v1/receipts?quarter=2026-Q2", headers=auth)
    assert q2.status_code == 200
    assert {r["counterparty_name"] for r in q2.json()} == {"A", "B"}

    q3 = client.get("/api/v1/receipts?quarter=2026-Q3", headers=auth)
    assert {r["counterparty_name"] for r in q3.json()} == {"C"}

    # month 与 quarter 互斥：同时给 → 422
    both = client.get("/api/v1/receipts?month=2026-04&quarter=2026-Q2", headers=auth)
    assert both.status_code == 422
    # 非法季度格式 → 422
    bad = client.get("/api/v1/receipts?quarter=2026-Q5", headers=auth)
    assert bad.status_code == 422


def test_receipts_csv_quarter(client, db, monkeypatch, tmp_path):
    """凭证草稿 CSV 支持按季度导出。"""
    from datetime import date as _date

    auth = _seed_login(client, db)
    db.add(BankReceipt(file_url="r.pdf", file_type="PDF", trade_date=_date(2026, 5, 12),
                       counterparty_name="某某公司", amount=Decimal("899.00"), status="unmatched"))
    db.commit()
    resp = client.get("/api/v1/receipts/export?quarter=2026-Q2", headers=auth)
    assert resp.status_code == 200
    assert "899.00" in resp.content.decode("utf-8-sig")


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
