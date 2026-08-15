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
