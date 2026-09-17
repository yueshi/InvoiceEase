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


def test_abstract_from_tax_detail_line():
    """缴款书摘要：明细行（含本张金额）的行首文本 = 险种名（该版式无摘要标签）。"""
    parsed = parse_receipt_text(CCB_TAX_CHUNK)
    assert parsed is not None
    assert parsed["abstract"] == "企业职工基本养老保险费"


def test_abstract_from_fee_header_and_interest_detail():
    """手续费：`项目名称 … 金额` 之间；利息：含金额明细行行首（活期利息）。"""
    fee = parse_receipt_text(CCB_FEE_CHUNK, self_accounts={"61050174004100000779"})
    assert fee is not None
    assert fee["abstract"] == "工本费/转账汇款手续费/手续费"

    interest = parse_receipt_text(CCB_INTEREST_CHUNK, self_accounts={"61050174004100000779"})
    assert interest is not None
    assert interest["abstract"] == "活期利息"


def test_abstract_when_amount_split_across_detail_rows():
    """医疗险等：合计金额被拆到多条明细行（449.10 + 8.00）→ 取各行行首拼接。"""
    from decimal import Decimal as _D

    from invoicing.parse.receipt import extract_abstract

    text = (
        "小写（合计）金额：￥457.10 缴款书交易流水号： 20260420113139864000009584192838\n"
        "  税（费）种名称 所属时期 实缴金额\n"
        "基本医疗保险费                                 20260401 20260430     449.10\n"
        "基本医疗保险费                                 20260401 20260430       8.00\n"
    )
    assert extract_abstract(text, _D("457.10")) == "基本医疗保险费"


def test_abstract_label_still_wins():
    """显式标签（摘要/用途/备注）优先级最高，不被明细行规则覆盖。"""
    parsed = parse_receipt_text(
        "交易日期 2026-08-05\n对方户名 北京某某科技有限公司\n交易金额 1,000.00\n摘要 货款\n"
    )
    assert parsed is not None
    assert parsed["abstract"] == "货款"


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


def test_mcp_receipt_ingest_async_batch(db, tmp_path, monkeypatch, mcp_admin_auth):
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


# 手续费/利息回单真实版式（脱敏）：唯一「户名」行是账户持有人=本司，回单本身
# 没有对方户名字段（银行内部交易）
CCB_FEE_CHUNK = (
    "，可通过建行网站(www.ccb.com)校验真伪。电子回单可重复打印，请勿重复记账。\n"
    "户名： 西安启智合创科技有限公司 账号： 61050174004100000779\n"
    "项目名称 工本费/转账汇款手续费/手续费 金额\n"
    "4000212普惠远航（B版） ￥899.00 ￥899.00\n"
    "合计金额 （大写）人民币捌佰玖拾玖元整 ￥899.00\n"
    "付款方式：转账 打印柜员：Z1999999\n"
)

CCB_INTEREST_CHUNK = (
    "，可通过建行网站(www.ccb.com)校验真伪。电子回单可重复打印，请勿重复记账。\n"
    "户名： 西安启智合创科技有限公司 账号： 61050174004100000779\n"
    "计息项目 起息日 结息日 本金/积数 利率（%） 利息\n"
    "活期利息 20260321 20260621 121454.92 0.050000 ￥0.17\n"
    "合计金额 (大写)人民币壹角柒分 ￥0.17\n"
    "流水号：年 月 日2026 06 21币别：人民币\n"
)


def test_party_capture_strips_trailing_labels():
    """P0 取值清洗：户名行捕获剥离线内后续字段标签（账号/开户行等）。"""
    parsed = parse_receipt_text("户名： 某某科技有限公司 账号： 61050174004100000779\n交易金额：100.00\n")
    assert parsed is not None
    assert parsed["counterparty_name"] == "某某科技有限公司"


def test_self_account_line_marks_no_counterparty():
    """P1 本司账户行（无银行模板）：对方留空（不臆造）+ no_counterparty 标记。"""
    parsed = parse_receipt_text(
        CCB_FEE_CHUNK, self_accounts={"61050174004100000779"}
    )
    assert parsed is not None
    assert parsed["counterparty_name"] is None  # 不把本司当对方
    assert "no_counterparty" in parsed["quality_issues"]
    assert parsed["direction"] == "付"  # 手续费=支出方


def test_self_account_line_uses_bank_as_counterparty():
    """本司账户行（识别到银行模板）：对方 = 对应银行，标 self_account_row。

    手续费/利息是与开户行发生的交易；此前该场景对方留空，列表与凭证草稿
    都看不出对方是谁。仍保留待核对（推断值 + 费用分类需人眼确认）。
    """
    from invoicing.parse.bank_templates import detect_bank

    template = detect_bank(CCB_FEE_CHUNK)
    assert template is not None and template.code == "ccb"

    parsed = parse_receipt_text(
        CCB_FEE_CHUNK, self_accounts={"61050174004100000779"}, template=template
    )
    assert parsed is not None
    assert parsed["counterparty_name"] == "中国建设银行"  # 对应银行（模板名）
    assert "self_account_row" in parsed["quality_issues"]
    assert "no_counterparty" not in parsed["quality_issues"]
    assert parsed["direction"] == "付"


def test_self_account_line_without_config_marks_account_residue():
    """未配置本司账号时兜底：清洗后仍命中账号模式 → 记账号残留问题（不静默）。"""
    parsed = parse_receipt_text(CCB_INTEREST_CHUNK)
    assert parsed is not None
    assert parsed["counterparty_name"] == "西安启智合创科技有限公司"
    assert "account_like_party" in parsed["quality_issues"]
    assert parsed["direction"] == "收"  # 利息=收入


def test_interest_receipt_direction_and_date():
    parsed = parse_receipt_text(CCB_INTEREST_CHUNK, self_accounts={"61050174004100000779"})
    assert parsed["amount"] == Decimal("0.17")
    assert parsed["trade_date"] == date(2026, 6, 21)  # 结息日（年 月 日占位乱序）


def test_list_receipts_exposes_quality_fields(client, db):
    """API 带出质量/方向字段（前端待核对高亮与筛选依赖）。"""
    auth = _seed_login(client, db)
    db.add(BankReceipt(
        file_url="r.pdf", file_type="PDF", trade_date=date(2026, 6, 21),
        counterparty_name=None, amount=Decimal("0.17"), abstract="活期利息",
        direction="收", needs_review=True, quality_issues=["no_counterparty"],
    ))
    db.commit()
    rows = client.get("/api/v1/receipts?quarter=2026-Q2", headers=auth).json()
    assert rows[0]["needs_review"] is True
    assert rows[0]["quality_issues"] == ["no_counterparty"]
    assert rows[0]["direction"] == "收"


def test_company_info_bank_account_roundtrip(client, db):
    """本司银行账号可保存并回读（P1 判定依据）。"""
    from invoicing.models import Role, User
    from invoicing.security import hash_password

    db.add(User(username="admin_ba", password_hash=hash_password("pass123"), role=Role.admin.value))
    db.commit()
    token = client.post(
        "/api/v1/auth/login", json={"username": "admin_ba", "password": "pass123"}
    ).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    resp = client.post("/api/v1/company-infos", headers=auth, json={
        "name": "西安启智合创科技有限公司", "tax_id": "91610113MA7MHLFY36",
        "kind": "self", "is_default": True, "bank_account": "61050174004100000779",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["bank_account"] == "61050174004100000779"


def test_direction_semantics_beat_page_furniture():
    """方向优先级：业务语义 > 版式标签——税票块混入页面装饰「收款人回单」仍应判付；
    转账块（无语义关键词）凭「贷方回单」标签判收。"""
    from invoicing.parse.receipt import infer_direction

    tax_with_furniture = "税票号码： 461016260410560121 收款人回单 缴款书交易流水号"
    assert infer_direction(tax_with_furniture) == "付"
    transfer = "凭证种类 电汇凭证 结算方式 转账 ︵贷方回单︶"
    assert infer_direction(transfer) == "收"
    assert infer_direction("中国建设银行单位客户专用回单 借\n方\n回\n单") == "付"


def test_llm_path_fills_direction(monkeypatch):
    """LLM 兜底路径（竖排版式规则失败）也须带出收付方向（关键词归一化匹配）。"""
    from invoicing.parse import receipt as R

    class FakeEngine:
        def chat_json(self, prompt, text):
            return '{"trade_date": "2026-04-20", "counterparty_name": "贾琨", "amount": "1600.00", "abstract": "转账"}'

    monkeypatch.setattr("invoicing.parse.llm.get_llm_engine", lambda: FakeEngine())
    # 竖排：贷\n方\n回\n单（收）；规则因换行无法匹配 → 走 LLM
    chunk = (
        "付\n款\n人\n全\xa0称 贾琨 收\n款\n人\n全\xa0称 西安启智合创科技有限公司\n"
        "︵\n贷\n方\n回\n单\n︶\n金额 （大写）人民币壹仟陆佰元整 （小写）￥1,600.00\n"
    )
    fields = R._llm_fill_fields(chunk, {})
    assert fields["counterparty_name"] == "贾琨"
    assert fields["direction"] == "收"


def test_anchor_token_prefers_longest_digit_run():
    """锚点串取块内最长数字串（流水号/税票号码），用于精确定位。"""
    from invoicing.parse.receipt import anchor_token

    chunk = (
        "凭证字号：30012026042003189400转账日期： 年 月 日2026 04 20\n"
        "小写（合计）金额：￥1,116.00 缴款书交易流水号： 20260420113137616000009584183300\n"
        "税票号码： 461016260410560121\n"
    )
    assert anchor_token(chunk) == "20260420113137616000009584183300"


def test_assign_anchor_bands_partitions_page():
    """同页多张回单：按锚点 y 中点切带，各覆盖自己区域，无重叠。"""
    from invoicing.parse.receipt import assign_anchor_bands

    rows = [
        {"page": 1, "anchor_text": "1111111111111111"},  # 页面底部（y 小）
        {"page": 1, "anchor_text": "2222222222222222"},
        {"page": 1, "anchor_text": "3333333333333333"},  # 页面顶部（y 大）
    ]
    pages = [{
        "w": 600.0, "h": 800.0,
        "spans": [
            ("税票号码：1111111111111111", 25.0, 100.0),  # y=100 最下
            ("税票号码：2222222222222222", 25.0, 400.0),
            ("税票号码：3333333333333333", 25.0, 700.0),  # y=700 最上
        ],
    }]
    assign_anchor_bands(rows, pages)
    bands = [r["anchor"]["bbox"] for r in rows]
    assert all(b is not None for b in bands)
    # 归一化且无重叠（y 带按页内位置切分）
    ys = sorted((b[1], b[3]) for b in bands)
    assert ys[0][1] <= ys[1][0] and ys[1][1] <= ys[2][0]
    assert ys[0][0] >= 0.0 and ys[-1][1] <= 1.0
    # 最下方（y=100）的行 y 带应在页面下部
    assert rows[0]["anchor"]["bbox"][3] < 0.5
    assert rows[2]["anchor"]["bbox"][1] > 0.5
    assert rows[0]["anchor"]["text"] == "1111111111111111"


def test_assign_anchor_bands_prefers_locator():
    """定位优先级：PDFium 定位器命中优先（全字符坐标），金额等候选串按序尝试。"""
    from invoicing.parse.receipt import assign_anchor_bands

    class FakeLocator:
        available = True
        def locate(self, page, token):
            # 流水号无坐标，金额命中 → 用金额定位
            return (600.0, 620.0) if token == "1,116.00" else None
        def page_height(self, page):
            return 800.0

    rows = [
        {"page": 1, "anchor_text": "9999999999999999", "amount": Decimal("1116.00")},
        {"page": 2, "anchor_text": "8888888888888888", "amount": Decimal("46.50")},
    ]
    assign_anchor_bands(rows, [None, None], locate=FakeLocator())
    assert rows[0]["anchor"]["bbox"] is not None   # 金额兜底命中
    assert rows[1]["anchor"]["bbox"] is None       # 两串都未命中 → 降级


def test_assign_anchor_bands_uses_structural_boundaries():
    """结构边界优先：同页回单按「回单头 → 免责声明行」切带（锚点所在行位置不固定，
    中点切带会偏移——真实数据复核发现的缺陷）。"""
    from invoicing.parse.receipt import assign_anchor_bands

    class StructuralLocator:
        available = True
        # 页 1 三张回单：头 804/523/242，块尾免责声明 584/303/22（自上而下）
        def find_all(self, page, token):
            if token == "此回单以客户真实交易为依据":
                return [583.7, 303.1, 22.4]
            if token == "单位客户专用回单":
                return [804.1, 523.5, 242.9]
            return []
        def locate(self, page, token):
            return None
        def page_height(self, page):
            return 842.0

    # 三张回单，锚点均在块内不同位置（仅用于 text，不参与切带）
    rows = [
        {"page": 1, "anchor_text": "A1", "amount": Decimal("1116.00")},
        {"page": 1, "anchor_text": "A2", "amount": Decimal("46.50")},
        {"page": 1, "anchor_text": "A3", "amount": Decimal("457.10")},
    ]
    assign_anchor_bands(rows, [None], locate=StructuralLocator())
    b0, b1, b2 = (r["anchor"]["bbox"] for r in rows)
    # 归一化：第一张 [584/842≈0.693, 812/842≈0.964]；第二张 [303, 527]；第三张 [22, 243]
    assert 0.68 < b0[1] < 0.71 and b0[3] > 0.94          # #1 ≈ [0.69, 0.96]
    assert 0.35 < b1[1] < 0.37 and 0.61 < b1[3] < 0.64   # #2 ≈ [0.36, 0.63]
    assert b2[1] < 0.03 and 0.28 < b2[3] < 0.30          # #3 ≈ [0.03, 0.29]
    # 无重叠且各覆盖自己区间
    assert b0[1] > b1[3] > b1[1] > b2[3] > b2[1]


def test_assign_anchor_bands_falls_back_when_structure_incomplete():
    """结构锚点数量与回单数不一致（无法对齐）→ 回退锚点中点法。"""
    from invoicing.parse.receipt import assign_anchor_bands

    class PartialLocator:
        available = True
        def find_all(self, page, token):
            return [500.0]  # 只有 1 个免责声明，但本页有 2 张回单
        def locate(self, page, token):
            return (600.0, 620.0) if token == "S1" else (200.0, 220.0)
        def page_height(self, page):
            return 800.0

    rows = [
        {"page": 1, "anchor_text": "S1", "amount": Decimal("1.00")},
        {"page": 1, "anchor_text": "S2", "amount": Decimal("2.00")},
    ]
    assign_anchor_bands(rows, [None], locate=PartialLocator())
    # 回退到中点法：以两张锚点 y(610/210) 的中点 410 为界
    assert rows[0]["anchor"]["bbox"][1] > 0.5   # 上张
    assert rows[1]["anchor"]["bbox"][3] < 0.55  # 下张


def test_assign_anchor_bands_without_coordinates_degrades():
    """锚点串在坐标层找不到（本 PDF 48% 片段无坐标）→ bbox 为空但页码保留。"""
    from invoicing.parse.receipt import assign_anchor_bands

    rows = [{"page": 2, "anchor_text": "9999999999999999"}]
    pages = [None, {"w": 600.0, "h": 800.0, "spans": [("别的文字", 10.0, 10.0)]}]
    assign_anchor_bands(rows, pages)
    assert rows[0]["anchor"] == {"bbox": None, "text": "9999999999999999", "v": 1}


def test_parse_receipts_bytes_per_page(monkeypatch):
    """逐页分块：多页 PDF 的每条回单携带页码。"""
    from invoicing.parse import receipt as R

    pages = [
        "户名：甲公司\n交易金额：100.00\n",
        "户名：乙公司\n交易金额：200.00\n",
    ]
    monkeypatch.setattr(R, "_receipt_pages_from_bytes", lambda data, kind: pages)
    monkeypatch.setattr(R, "_llm_fill_fields", lambda chunk, fields, self_names=None: fields)
    rows = R.parse_receipts_bytes(b"%PDF", "PDF")
    assert [(r["page"], str(r["amount"])) for r in rows] == [(1, "100.00"), (2, "200.00")]


def test_receipt_page_image_endpoint_renders_and_caches(client, db, monkeypatch, tmp_path):
    """原件定位渲染端点：返回该页 PNG，并缓存（同页同 DPI 只渲染一次）。"""
    from invoicing.storage import LocalFileStorage

    auth = _seed_login(client, db)
    storage = LocalFileStorage(root=str(tmp_path / "orig"))
    monkeypatch.setattr("invoicing.storage.get_storage", lambda: storage)
    r = BankReceipt(
        file_url="tenant-default/receipts/multi.pdf", file_type="PDF", file_hash="hash-1",
        counterparty_name="某某公司", amount=Decimal("899.00"), status="unmatched", page_no=3,
    )
    db.add(r)
    db.commit()
    storage.put(r.file_url, b"%PDF-1.4 fake", "application/pdf")

    calls = []
    monkeypatch.setattr(
        "invoicing.parse.pdf_text_parser.render_pdf_page_png",
        lambda data, page_no, dpi=150: (calls.append(page_no), b"\x89PNG\r\n\x1a\nFAKE")[1],
    )
    resp = client.get(f"/api/v1/receipts/{r.id}/page.png", headers=auth)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG")
    assert calls == [3]  # 渲染了第 3 页

    resp2 = client.get(f"/api/v1/receipts/{r.id}/page.png", headers=auth)
    assert resp2.status_code == 200
    assert calls == [3]  # 命中缓存，未重复渲染


def test_receipt_page_image_endpoint_501_without_renderer(client, db, monkeypatch, tmp_path):
    """渲染库缺失 → 501，前端据此降级为「打开原 PDF 第 N 页」。"""
    from invoicing.parse.pdf_text_parser import RenderUnavailable
    from invoicing.storage import LocalFileStorage

    auth = _seed_login(client, db)
    storage = LocalFileStorage(root=str(tmp_path / "orig"))
    monkeypatch.setattr("invoicing.storage.get_storage", lambda: storage)
    r = BankReceipt(file_url="r.pdf", file_type="PDF", file_hash="h2", amount=Decimal("1.00"),
                    status="unmatched", page_no=1)
    db.add(r)
    db.commit()
    storage.put(r.file_url, b"%PDF-1.4 fake", "application/pdf")
    monkeypatch.setattr(
        "invoicing.parse.pdf_text_parser.render_pdf_page_png",
        lambda *a, **k: (_ for _ in ()).throw(RenderUnavailable("no pypdfium2")),
    )
    resp = client.get(f"/api/v1/receipts/{r.id}/page.png", headers=auth)
    assert resp.status_code == 501


def test_pdfium_locator_rejects_embedded_match(tmp_path):
    """定位词边界校验：短数字串嵌在长数字内部时不得误配（回归：日期串误命中流水号）。"""
    from invoicing.parse.pdf_text_parser import PdfiumLocator

    loc = PdfiumLocator(b"not-a-pdf")  # 不读真实 PDF，只测匹配逻辑
    text = "流水号：30012026042003189400 交易日期：20260321"
    assert loc._find_bounded(text, "20260321") > 0            # 独立出现 → 命中
    assert loc._find_bounded("流水号：9912026032199", "20260321") == -1  # 嵌在长数字内 → 拒绝


def test_list_receipts_year_filter(client, db):
    """按年过滤：year=2026 覆盖全年；与 month/quarter 三选一互斥。"""
    from datetime import date as _date

    auth = _seed_login(client, db)
    rows = [
        BankReceipt(file_url="a.pdf", file_type="PDF", trade_date=_date(2026, 1, 5),
                    counterparty_name="A", amount=Decimal("10.00"), status="unmatched"),
        BankReceipt(file_url="b.pdf", file_type="PDF", trade_date=_date(2026, 12, 20),
                    counterparty_name="B", amount=Decimal("20.00"), status="unmatched"),
        BankReceipt(file_url="c.pdf", file_type="PDF", trade_date=_date(2025, 6, 1),
                    counterparty_name="C", amount=Decimal("30.00"), status="unmatched"),
    ]
    for r in rows:
        db.add(r)
    db.commit()

    y2026 = client.get("/api/v1/receipts?year=2026", headers=auth)
    assert y2026.status_code == 200
    assert {r["counterparty_name"] for r in y2026.json()} == {"A", "B"}

    y2025 = client.get("/api/v1/receipts?year=2025", headers=auth)
    assert {r["counterparty_name"] for r in y2025.json()} == {"C"}

    # 三个周期参数互斥：同给两个 → 422
    both = client.get("/api/v1/receipts?year=2026&quarter=2026-Q1", headers=auth)
    assert both.status_code == 422
    assert client.get("/api/v1/receipts?year=26", headers=auth).status_code == 422


def test_list_receipts_all_periods(client, db):
    """「全部」周期：不带 month/quarter/year → 返回全部回单（按入库时间倒序）。"""
    from datetime import date as _date

    auth = _seed_login(client, db)
    for d, name in ((_date(2025, 3, 1), "旧"), (_date(2026, 6, 30), "新")):
        db.add(BankReceipt(file_url="r.pdf", file_type="PDF", trade_date=d,
                           counterparty_name=name, amount=Decimal("1.00"), status="unmatched"))
    db.commit()

    resp = client.get("/api/v1/receipts", headers=auth)
    assert resp.status_code == 200
    rows = resp.json()
    assert {r["counterparty_name"] for r in rows} == {"旧", "新"}

    # 仍然互斥：多给周期 → 422
    both = client.get("/api/v1/receipts?year=2026&month=2026-06", headers=auth)
    assert both.status_code == 422


def test_receipts_csv_year(client, db, monkeypatch, tmp_path):
    """凭证草稿 CSV 支持按年导出（文件名带到年）。"""
    from datetime import date as _date

    auth = _seed_login(client, db)
    db.add(BankReceipt(file_url="r.pdf", file_type="PDF", trade_date=_date(2026, 3, 3),
                       counterparty_name="某某公司", amount=Decimal("66.00"), status="unmatched"))
    db.commit()
    resp = client.get("/api/v1/receipts/export?year=2026", headers=auth)
    assert resp.status_code == 200
    assert "66.00" in resp.content.decode("utf-8-sig")
    assert "2026" in resp.headers["content-disposition"]


def test_bank_account_crud_and_self_account_wiring(client, db):
    """常用企业银行账号：CRUD + 解析接线（self_account_set 取启用账号）。"""
    from invoicing.models import Role, User
    from invoicing.parse.receipt import self_account_set, self_name_set
    from invoicing.security import hash_password

    db.add(User(username="admin_ba2", password_hash=hash_password("pass123"), role=Role.admin.value))
    db.commit()
    auth = client.post(
        "/api/v1/auth/login", json={"username": "admin_ba2", "password": "pass123"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {auth}"}

    # 创建
    resp = client.post("/api/v1/bank-accounts", headers=headers, json={
        "account_no": "61050174004100000779",
        "account_name": "西安启智合创科技有限公司",
        "bank_name": "建行西安蓝湖树小区支行",
        "remark": "基本户",
    })
    assert resp.status_code == 200, resp.text
    acc = resp.json()
    assert acc["account_no"] == "61050174004100000779"

    # 唯一性
    dup = client.post("/api/v1/bank-accounts", headers=headers, json={
        "account_no": "61050174004100000779", "account_name": "重复",
    })
    assert dup.status_code == 409

    # 解析接线：启用的账号进入本司账号集合；户名进入本司名称集合
    assert "61050174004100000779" in self_account_set(db)
    assert any("西安启智" in n for n in self_name_set(db))

    # 停用后不参与判定
    client.put(f"/api/v1/bank-accounts/{acc['id']}", headers=headers, json={"enabled": False})
    assert "61050174004100000779" not in self_account_set(db)

    # 更新与删除
    upd = client.put(f"/api/v1/bank-accounts/{acc['id']}", headers=headers,
                     json={"enabled": True, "remark": "改为一般户"})
    assert upd.json()["remark"] == "改为一般户"
    assert client.delete(f"/api/v1/bank-accounts/{acc['id']}", headers=headers).status_code == 200
    assert client.get("/api/v1/bank-accounts", headers=headers).json() == []


def test_bank_account_bank_code_autodetect(client, db):
    """银行代码：开户行文本自动识别（建行…→ccb）；显式指定时不被覆盖；识别不出为 None。"""
    from invoicing.models import Role, User
    from invoicing.security import hash_password

    db.add(User(username="admin_bc", password_hash=hash_password("pass123"), role=Role.admin.value))
    db.commit()
    auth = client.post(
        "/api/v1/auth/login", json={"username": "admin_bc", "password": "pass123"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {auth}"}

    auto = client.post("/api/v1/bank-accounts", headers=headers, json={
        "account_no": "61050174004100000779", "bank_name": "建行西安蓝湖树小区支行",
    }).json()
    assert auto["bank_code"] == "ccb"  # 自动识别

    explicit = client.post("/api/v1/bank-accounts", headers=headers, json={
        "account_no": "6225881293783592", "bank_name": "招商银行深圳分行", "bank_code": "cmb",
    }).json()
    assert explicit["bank_code"] == "cmb"

    unknown = client.post("/api/v1/bank-accounts", headers=headers, json={
        "account_no": "1234567890", "bank_name": "某村镇银行",
    }).json()
    assert unknown["bank_code"] is None

    # 更新开户行时若未显式给 bank_code，重新识别
    upd = client.put(f"/api/v1/bank-accounts/{unknown['id']}", headers=headers,
                     json={"bank_name": "中国工商银行北京中关村支行"}).json()
    assert upd["bank_code"] == "icbc"


def test_bank_account_legacy_company_info_still_works(client, db):
    """兼容：company_infos.bank_account 旧数据仍参与本司账户判定（迁移前数据不失效）。"""
    from invoicing.models import CompanyInfo
    from invoicing.parse.receipt import self_account_set

    db.add(CompanyInfo(name="旧公司", tax_id="91310101MAELA36R99", kind="self",
                       bank_account="6225881293783592"))
    db.commit()
    assert "6225881293783592" in self_account_set(db)


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


def test_suggest_pair_excludes_self(db):
    """P2：销方是本司的发票不参与配对（防本司自开票误配）。"""
    from invoicing.models import CompanyInfo

    db.add(CompanyInfo(name="西安启智合创科技有限公司", tax_id="91610113MA7MHLFY36", kind="self"))
    inv = Invoice(
        file_url="a.xml", file_type="XML", invoice_number="24312000000012345690",
        status="pending_submit", total_amount=Decimal("899.00"),
        seller_name="西安启智合创科技有限公司", issue_date=date(2026, 5, 12),
    )
    db.add(inv)
    db.flush()
    r = BankReceipt(
        file_url="r.pdf", file_type="PDF", trade_date=date(2026, 5, 12),
        counterparty_name="西安启智合创科技有限公司", amount=Decimal("899.00"),
    )
    db.add(r)
    db.flush()
    assert suggest_pair(db, r.id) is None  # 户名命中本司 → 不配对


def test_csv_direction_columns(db):
    """P2：凭证借贷方向随收付方向；收=借银行/贷费用，付=借费用/贷银行。"""
    db.add(BankReceipt(
        file_url="r1.pdf", file_type="PDF", trade_date=date(2026, 4, 20),
        counterparty_name="税务局", amount=Decimal("1116.00"), abstract="社保",
        direction="付",
    ))
    db.add(BankReceipt(
        file_url="r2.pdf", file_type="PDF", trade_date=date(2026, 6, 21),
        counterparty_name=None, amount=Decimal("0.17"), abstract="活期利息",
        direction="收", needs_review=True, quality_issues=["no_counterparty"],
    ))
    db.commit()
    lines = receipts_to_csv(db, month="2026-04").decode("utf-8-sig").splitlines()
    assert "社保" in lines[1] and lines[1].split(",")[4] == "社保"      # 借方=费用
    assert lines[1].split(",")[5] == "银行存款"                        # 贷方=银行
    lines6 = receipts_to_csv(db, month="2026-06").decode("utf-8-sig").splitlines()
    assert lines6[1].split(",")[4] == "银行存款"                       # 收：借方=银行
    assert lines6[1].split(",")[5] == "活期利息"                       # 贷方=摘要(利息收入)


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


def test_period_probe_reports_other_periods(client, db):
    """P1-4 周期外提示：跨月补录的回单在当前月看不到时，要能告诉前端"别处还有几条"。

    历史痛点：回单页默认「本月」，6 月的回单在 9 月页面空白且无提示 ——
    用户以为上传失败（发票页早前已修过同类问题，回单页当时漏了）。
    """
    auth = _seed_login(client, db)
    db.add_all([
        BankReceipt(file_url="a.pdf", file_type="PDF", trade_date=date(2026, 6, 3),
                    counterparty_name="A", amount=Decimal("100.00"), status="unmatched"),
        BankReceipt(file_url="b.pdf", file_type="PDF", trade_date=date(2026, 6, 4),
                    counterparty_name="B", amount=Decimal("200.00"), status="unmatched",
                    paired_invoice_id=None),
    ])
    db.commit()

    # 当前月（2026-09）为空，但全部时间有 2 条 → 前端据此提示
    assert client.get("/api/v1/receipts?month=2026-09", headers=auth).json() == []
    probe = client.get("/api/v1/receipts/period-probe", headers=auth)
    assert probe.status_code == 200 and probe.json()["total"] == 2

    # 未配对筛选下同样成立（与列表页筛选口径一致）
    db.query(BankReceipt).filter(BankReceipt.counterparty_name == "B").one()
    inv = Invoice(file_url="i.xml", file_type="XML", invoice_number="24312000000000009001",
                  status="parsed", verify_status="passed")
    db.add(inv)
    db.commit()
    paired = db.query(BankReceipt).filter(BankReceipt.counterparty_name == "A").one()
    paired.paired_invoice_id = inv.id
    db.commit()
    assert client.get(
        "/api/v1/receipts/period-probe?unmatched=true", headers=auth
    ).json()["total"] == 1
    assert client.get("/api/v1/receipts/period-probe", headers=auth).json()["total"] == 2


def test_unmatched_expense_excludes_incoming(client, db):
    """「只看无票支出」不得混入收款（收款未配对 ≠ 无票支出）。

    回归：/receipts/unmatched 与 period-probe?unmatched=true 此前只判「未配对」，
    收款方向（direction="收"）的未配对回单也会被当成无票支出返回/计数。
    """
    auth = _seed_login(client, db)
    db.add_all([
        BankReceipt(file_url="pay.pdf", file_type="PDF", trade_date=date(2026, 8, 3),
                    counterparty_name="供应商甲", amount=Decimal("800.00"),
                    status="unmatched", direction="付"),
        BankReceipt(file_url="in.pdf", file_type="PDF", trade_date=date(2026, 8, 4),
                    counterparty_name="客户乙", amount=Decimal("900.00"),
                    status="unmatched", direction="收"),
        BankReceipt(file_url="unknown.pdf", file_type="PDF", trade_date=date(2026, 8, 5),
                    counterparty_name="方向未识别", amount=Decimal("100.00"),
                    status="unmatched", direction=None),  # 按费用处理（与凭证导出口径一致）
    ])
    db.commit()

    rows = client.get("/api/v1/receipts/unmatched?month=2026-08", headers=auth).json()
    names = sorted(r["counterparty_name"] for r in rows)
    assert names == ["供应商甲", "方向未识别"]

    probe = client.get("/api/v1/receipts/period-probe?unmatched=true", headers=auth).json()
    assert probe["total"] == 2  # 与列表页口径一致（收款不计入）


def test_confirm_review_clears_flag_and_audits(client, db):
    """核对无误：清待核对标记 + 审计留痕（RECEIPT_REVIEW）；重复点击幂等。"""
    from invoicing.models import AuditLog

    auth = _seed_login(client, db)
    r = BankReceipt(
        file_url="fee.pdf", file_type="PDF", trade_date=date(2026, 8, 6),
        counterparty_name="中国建设银行", amount=Decimal("15.00"), abstract="手续费",
        direction="付", status="unmatched", needs_review=True, quality_issues=["self_account_row"],
    )
    db.add(r)
    db.commit()

    resp = client.post(f"/api/v1/receipts/{r.id}/confirm-review", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["needs_review"] is False
    # 解析期标记保留（作为历史记录），仅出人工队列
    assert resp.json()["quality_issues"] == ["self_account_row"]

    logs = db.query(AuditLog).filter(AuditLog.action == "RECEIPT_REVIEW").all()
    assert len(logs) == 1 and logs[0].detail["receipt_id"] == r.id

    # 幂等：已核对的重复点击不再写审计/不报错
    assert client.post(f"/api/v1/receipts/{r.id}/confirm-review", headers=auth).status_code == 200
    assert db.query(AuditLog).filter(AuditLog.action == "RECEIPT_REVIEW").count() == 1


def test_confirm_review_requires_finance(client, db):
    """核对操作仅财务角色可用（员工 403）。"""
    from invoicing.models import Role as R

    auth_emp = _seed_login(client, db, username="emp_r", role=R.employee.value)
    r = BankReceipt(file_url="x.pdf", file_type="PDF", needs_review=True, status="unmatched")
    db.add(r)
    db.commit()
    assert client.post(f"/api/v1/receipts/{r.id}/confirm-review", headers=auth_emp).status_code == 403


def test_classify_receipt_table():
    """判定表：每类语料 → (category, requirement)；含误判边界（税务师事务所=采购）。"""
    from invoicing.models import BankReceipt
    from invoicing.workflow.receipts import classify_receipt, requirement_of

    SELF = {"西安启智合创科技有限公司"}
    cases = [
        (dict(counterparty_name="国家金库陕西省西咸新区支库", direction="付"), "tax", "none"),
        (dict(counterparty_name="国家税务总局西咸新区税务局", direction="付"), "tax", "none"),
        (dict(counterparty_name="中国人民银行国库", direction="付"), "tax", "none"),
        (dict(counterparty_name="国家税务总局西咸新区税务局", direction="付", abstract="企业职工基本养老保险费"), "social", "none"),
        (dict(counterparty_name="国家税务总局西咸新区税务局", direction="付", abstract="失业保险费"), "social", "none"),
        (dict(counterparty_name="中国工商银行", direction="付", abstract="工伤保险费"), "social", "none"),
        (dict(counterparty_name="西安住房公积金管理中心", direction="付"), "social", "none"),
        (dict(counterparty_name="中国建设银行", direction="付", abstract="手续费",
              quality_issues=["self_account_row"]), "bank_fee", "none"),
        (dict(counterparty_name="某某公司", direction="付", abstract="代发工资"), "salary", "none"),
        (dict(counterparty_name="中国建设银行", direction="付",
              quality_issues=["self_account_row"]), "internal_transfer", "none"),
        (dict(counterparty_name="西安启智合创科技有限公司", direction="付"), "internal_transfer", "none"),
        (dict(counterparty_name="客户甲公司", direction="收"), "sales_collection", "issue"),
        (dict(counterparty_name="西安市财政局", direction="收"), "treasury_in", "none"),
        (dict(counterparty_name="供应商甲", direction="付"), "purchase", "fetch"),
        (dict(counterparty_name=None, direction=None), "unknown", "fetch"),
        # 误判边界：含「税务」二字的服务商是正常采购，必须继续催票
        (dict(counterparty_name="陕西税务师事务所有限公司", direction="付"), "purchase", "fetch"),
    ]
    for kwargs, want_cat, want_req in cases:
        r = BankReceipt(file_url="t.pdf", file_type="PDF", status="unmatched", **kwargs)
        cat, src = classify_receipt(r, SELF)
        assert cat == want_cat, f"{kwargs} → {cat}，期望 {want_cat}"
        assert src == "rule"
        assert requirement_of(cat) == want_req, f"{want_cat} 的发票要求应为 {want_req}"


def test_category_column_fits_all_values():
    """列宽必须装得下最长类别取值（SQLite 不校验长度，PG 会炸——见 T1 审查 Important-1）。"""
    from invoicing.models import BankReceipt
    from invoicing.workflow.receipts import CATEGORY_META

    assert BankReceipt.__table__.c.category.type.length >= max(len(c) for c in CATEGORY_META)


def test_unmatched_expense_excludes_invoice_exempt(client, db):
    """无需发票类（税费/社保/银行费用）不入无票支出队列；采购类仍在。"""
    auth = _seed_login(client, db)
    db.add_all([
        BankReceipt(file_url="tax.pdf", file_type="PDF", trade_date=date(2026, 8, 7),
                    counterparty_name="国家金库陕西省西咸新区支库", amount=Decimal("1116.00"),
                    status="unmatched", direction="付", category="tax"),
        BankReceipt(file_url="social.pdf", file_type="PDF", trade_date=date(2026, 8, 8),
                    counterparty_name="西安住房公积金管理中心", amount=Decimal("600.00"),
                    status="unmatched", direction="付", category="social"),
        BankReceipt(file_url="fee.pdf", file_type="PDF", trade_date=date(2026, 8, 9),
                    counterparty_name="中国建设银行", amount=Decimal("15.00"), abstract="手续费",
                    status="unmatched", direction="付", category="bank_fee"),
        BankReceipt(file_url="buy.pdf", file_type="PDF", trade_date=date(2026, 8, 10),
                    counterparty_name="供应商甲", amount=Decimal("800.00"),
                    status="unmatched", direction="付", category="purchase"),
    ])
    db.commit()

    rows = client.get("/api/v1/receipts/unmatched?month=2026-08", headers=auth).json()
    assert [r["counterparty_name"] for r in rows] == ["供应商甲"]
    probe = client.get("/api/v1/receipts/period-probe?unmatched=true", headers=auth).json()
    assert probe["total"] == 1


def test_suggest_pair_skips_invoice_exempt(db):
    """无需发票的行不参与发票配对（税务行与发票金额撞车也不得错配）。"""
    from invoicing.parse.receipt import suggest_pair

    inv = Invoice(file_url="i.xml", file_type="XML", invoice_number="24990000000000000001",
                  status="parsed", verify_status="passed", seller_name="供应商甲",
                  total_amount=Decimal("800.00"))
    db.add(inv)
    r = BankReceipt(file_url="buy.pdf", file_type="PDF", counterparty_name="供应商甲",
                    amount=Decimal("800.00"), status="unmatched", direction="付", category="purchase")
    db.add(r)
    db.commit()
    assert suggest_pair(db, r.id) == inv.id  # 采购类照常配对

    r.category = "tax"  # 同类行若被判为税费 → 不参与配对
    db.commit()
    assert suggest_pair(db, r.id) is None
