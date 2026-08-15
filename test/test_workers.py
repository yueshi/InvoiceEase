from pathlib import Path

import pytest

from invoicing.models import Invoice
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
