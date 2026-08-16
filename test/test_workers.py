from pathlib import Path

import pytest

from invoicing.models import AuditLog, Invoice
from invoicing.storage import LocalFileStorage

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


@pytest.fixture()
def storage(tmp_path):
    return LocalFileStorage(root=str(tmp_path / "originals"))


@pytest.fixture(autouse=True)
def _point_worker_at_fixture_storage(monkeypatch, storage):
    # _parse_invoice 内部经 get_storage() 读取原件，测试中将其指向隔离的 tmp_path 存储
    monkeypatch.setattr("invoicing.workers.tasks.get_storage", lambda: storage)


@pytest.fixture(autouse=True)
def _isolate_inline_verify(monkeypatch):
    # 本地模式（queue_backend=local）下 _parse_invoice 尾部会内联执行验真
    # （enqueue_verify_sync → _verify_invoice），发票会被直接从 parsed 推到
    # pending_submit，导致本文件用例无法再在 parsed 状态下直调 _verify_invoice。
    # 将内联入队置为 no-op，隔离出「解析」与「验真」两个阶段分别验证。
    monkeypatch.setattr("invoicing.workers.tasks.enqueue_verify_sync", lambda invoice_id: None)


def _make_invoice(db, storage, filename="dianzi.xml", file_type="XML"):
    data = (FIXTURES / filename).read_bytes()
    key = f"test-worker/{filename}"
    storage.put(key, data, "application/xml")
    inv = Invoice(file_url=key, file_type=file_type, status="parsing", invoice_number="24312000000012345678")
    db.add(inv)
    db.flush()
    db.commit()  # 提交事务：_parse_invoice 的独立会话只读取已提交数据
    return inv


def test_parse_invoice_task_success(db, storage):
    from invoicing.workers.tasks import _parse_invoice

    inv = _make_invoice(db, storage)
    _parse_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "parsed"
    assert inv.parse_source == "XML"
    assert inv.total_amount is not None
    assert inv.confidence_score == 1.0


def test_parse_invoice_task_unstructured_goes_to_review(db, storage):
    from invoicing.workers.tasks import _parse_invoice

    inv = _make_invoice(db, storage)
    inv.file_type = "PDF"
    inv.file_url = "test-worker/plain.pdf"
    storage.put(inv.file_url, b"%PDF-1.4 no attachments", "application/pdf")
    db.commit()  # 提交修改：_parse_invoice 的独立会话只读取已提交数据
    _parse_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_review"
    assert inv.parse_source == "PDF_UNSTRUCTURED"


def test_verify_invoice_task_pass(db, storage):
    from invoicing.workers.tasks import _parse_invoice, _verify_invoice

    inv = _make_invoice(db, storage)
    _parse_invoice(inv.id)
    db.refresh(inv)
    _verify_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_submit"
    assert inv.verify_status == "passed"
    assert inv.verify_detail["status"] == "passed"
    assert inv.verified_at is not None


def test_verify_invoice_task_fail_goes_review(db, storage):
    from invoicing.workers.tasks import _parse_invoice, _verify_invoice

    inv = _make_invoice(db, storage)
    _parse_invoice(inv.id)
    db.refresh(inv)
    # 解析会覆盖号码，因此必须在解析之后再改为 mock 规则失败前缀
    inv.invoice_number = "00001234567890123456"
    db.commit()  # worker 独立会话只读已提交数据，号码修改必须提交才能可见
    _verify_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_review"
    assert inv.verify_status == "failed"


def test_verify_invoice_task_duplicate_blocks(db, storage, monkeypatch):
    from invoicing.workers.tasks import _parse_invoice, _verify_invoice

    inv = _make_invoice(db, storage)
    _parse_invoice(inv.id)
    db.refresh(inv)
    other = Invoice(file_url="x.xml", file_type="XML", invoice_number="24312000000000000001")
    db.add(other)
    db.commit()  # 提交并释放测试会话写事务：_verify_invoice 独立会话写库前不能有未提交写锁（SQLite）
    monkeypatch.setattr("invoicing.workers.tasks.find_duplicate", lambda d, i: other)
    _verify_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "blocked"
    assert inv.duplicate_flag is True
    assert inv.duplicate_of_id == other.id


def test_parse_invoice_task_failure_falls_to_review(db, storage, monkeypatch):
    from invoicing.models import AuditLog
    from invoicing.workers.tasks import _parse_invoice

    inv = _make_invoice(db, storage)

    class BoomStorage:
        def get(self, key):
            raise IOError("storage down")

    monkeypatch.setattr("invoicing.workers.tasks.get_storage", lambda: BoomStorage())
    with pytest.raises(IOError):
        _parse_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_review"
    assert inv.validation_errors is not None and any(
        e.get("result") == "error" for e in inv.validation_errors
    )
    assert (
        db.query(AuditLog)
        .filter(AuditLog.invoice_id == inv.id, AuditLog.action == "PARSE")
        .count()
        >= 1
    )


def test_verify_invoice_task_failure_falls_to_review(db, storage, monkeypatch):
    from invoicing.models import AuditLog
    from invoicing.workers.tasks import _parse_invoice, _verify_invoice

    inv = _make_invoice(db, storage)
    _parse_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "parsed"

    class BoomProvider:
        def verify(self, inv):
            raise IOError("provider down")

    monkeypatch.setattr("invoicing.workers.tasks.get_provider", lambda: BoomProvider())
    with pytest.raises(IOError):
        _verify_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_review"
    assert (
        db.query(AuditLog)
        .filter(AuditLog.invoice_id == inv.id, AuditLog.action == "VERIFY")
        .count()
        >= 1
    )


def test_parse_invoice_buyer_mismatch_goes_review(db, storage, monkeypatch):
    """预存本司 + 解析购买方税号不匹配 → BUYER_MISMATCH 走待复核且不入验真队列。"""
    import io
    import zipfile

    from invoicing.models import CompanyInfo
    from invoicing.workers.tasks import _parse_invoice

    # 预存本司（kind=self）；worker 独立会话只读已提交数据
    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.commit()

    # 记录入队调用：归属校验未通过时不应产生验真任务
    calls = []
    monkeypatch.setattr("invoicing.workers.tasks.enqueue_verify_sync", lambda invoice_id: calls.append(invoice_id))

    content = """<?xml version="1.0" encoding="UTF-8"?>
<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">
  <ofd:Content>
    <ofd:Layer>
      <ofd:TextObject>
        <ofd:TextCode X="10" Y="100">电子发票（普通发票） 发票号码：26617000000309516967</ofd:TextCode>
        <ofd:TextCode X="10" Y="120">开票日期：2026年07月09日</ofd:TextCode>
        <ofd:TextCode X="10" Y="140">名称：测试采购有限公司</ofd:TextCode>
        <ofd:TextCode X="10" Y="160">统一社会信用代码/纳税人识别号：91310000MA1FL0B000</ofd:TextCode>
        <ofd:TextCode X="10" Y="180">名称：示例出行科技有限公司</ofd:TextCode>
        <ofd:TextCode X="10" Y="200">统一社会信用代码/纳税人识别号：91310000MA1FL0A000</ofd:TextCode>
        <ofd:TextCode X="10" Y="240">合    计 ¥65.48 ¥1.96</ofd:TextCode>
      </ofd:TextObject>
    </ofd:Layer>
  </ofd:Content>
</ofd:Page>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org/2016'/>")
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", content)
    key = "test-worker/mismatch.ofd"
    storage.put(key, buf.getvalue(), "application/ofd")
    inv = Invoice(file_url=key, file_type="OFD", status="parsing", invoice_number="26617000000309516967")
    db.add(inv)
    db.commit()

    _parse_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_review"  # BUYER_MISMATCH 从严：走待复核
    assert inv.validation_errors is not None and any(
        e.get("code") == "BUYER_MISMATCH" for e in inv.validation_errors
    )
    assert calls == []  # 不进入验真队列


def test_parse_invoice_task_duplicate_number_blocks(db, storage):
    from invoicing.workers.tasks import _parse_invoice

    first = _make_invoice(db, storage)
    _parse_invoice(first.id)
    db.refresh(first)
    assert first.status == "parsed"  # autouse fixture 隔离内联验真

    second = Invoice(
        file_url="test-worker/dup.xml",
        file_type="XML",
        status="parsing",
        invoice_number=None,
    )
    db.add(second)
    db.commit()
    storage.put(second.file_url, (FIXTURES / "dianzi.xml").read_bytes(), "application/xml")
    _parse_invoice(second.id)
    # 重复拦截：审计留痕 + 物理删除（不留全字段为空的 blocked 空壳）
    db.expunge(second)  # 另一会话已删除，需先移出本会话 identity map
    assert db.get(Invoice, second.id) is None
    logs = db.query(AuditLog).filter(AuditLog.action == "PARSE", AuditLog.invoice_id == second.id).all()
    assert any(l.detail.get("result") == "duplicate" and l.detail.get("duplicate_of_id") == first.id for l in logs)


def test_parse_invoice_validation_error_keeps_fields(db, storage):
    """回归：校验矛盾票（字段齐全但金额矛盾）→ 待复核且已提取字段入库，复核人只改单字段。"""
    import io
    import zipfile

    from invoicing.workers.tasks import _parse_invoice

    content = """<?xml version="1.0" encoding="UTF-8"?>
<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">
  <ofd:Content>
    <ofd:Layer>
      <ofd:TextObject>
        <ofd:TextCode X="10" Y="100">电子发票（普通发票） 发票号码：26617000000309516967</ofd:TextCode>
        <ofd:TextCode X="10" Y="120">开票日期：2026年07月09日</ofd:TextCode>
        <ofd:TextCode X="10" Y="140">名称：测试采购有限公司</ofd:TextCode>
        <ofd:TextCode X="10" Y="160">统一社会信用代码/纳税人识别号：91310000MA1FL0B000</ofd:TextCode>
        <ofd:TextCode X="10" Y="180">名称：示例出行科技有限公司</ofd:TextCode>
        <ofd:TextCode X="10" Y="200">统一社会信用代码/纳税人识别号：91310000MA1FL0A000</ofd:TextCode>
        <ofd:TextCode X="10" Y="220">价税合计（大写）壹佰圆整</ofd:TextCode>
        <ofd:TextCode X="10" Y="240">合    计 ¥65.48 ¥1.96</ofd:TextCode>
      </ofd:TextObject>
    </ofd:Layer>
  </ofd:Content>
</ofd:Page>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org/2016'/>")
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", content)
    key = "test-worker/conflict.ofd"
    storage.put(key, buf.getvalue(), "application/ofd")
    inv = Invoice(file_url=key, file_type="OFD", status="parsing", invoice_number="26617000000309516967")
    db.add(inv)
    db.commit()

    _parse_invoice(inv.id)
    db.refresh(inv)
    assert inv.status == "pending_review"  # 金额矛盾 → 待复核
    # 已提取字段已入库：复核人只需改金额，不必从原件重录
    assert inv.invoice_number == "26617000000309516967"
    assert inv.total_amount is not None
    assert inv.seller_name == "示例出行科技有限公司"
    assert inv.buyer_name == "测试采购有限公司"
