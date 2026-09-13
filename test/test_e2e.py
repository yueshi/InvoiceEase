"""核心链路验收：收取 → 解析 → 验真 → 列表可见。"""
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.fetch.protocol import MailFetcher, RawAttachment, RawMailMessage
from invoicing.fetch.service import poll_mailbox
from invoicing.main import create_app
from invoicing.models import Invoice, Mailbox, Role, User
from invoicing.security import hash_password

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


class FakeFetcher(MailFetcher):
    def __init__(self, messages):
        self.messages = messages

    def fetch_new(self, last_uid):
        return [m for m in self.messages if m.uid > last_uid]

    def mark_seen(self, uids):
        pass


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def test_full_pipeline_xml_invoice(db, client):
    # 1. 管理员与邮箱配置
    db.add(User(username="root", password_hash=hash_password("pass123"), role=Role.admin.value))
    db.flush()
    mb = Mailbox(
        name="e2e", imap_host="x", imap_port=993, use_ssl=True,
        username="inv@example.com", password_encrypted="enc:x", keywords="发票,Invoice",
    )
    db.add(mb)
    db.flush()

    # 2. 模拟邮件收取（XML 发票 + 一张图片 + 无关附件）
    xml = (FIXTURES / "dianzi.xml").read_bytes()
    fetcher = FakeFetcher(
        [
            RawMailMessage(
                uid=1,
                message_id="<e2e1@example.com>",
                subject="发票",
                sender="user@example.com",
                attachments=[
                    RawAttachment("dianzi.xml", "application/xml", xml),
                    RawAttachment("photo.jpg", "image/jpeg", b"\xff\xd8\xff"),
                    RawAttachment("note.txt", "text/plain", b"hello"),
                ],
            )
        ]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 1
    assert result.rejected_images == 1

    # 3. 本地队列模式：收取后同步内联完成解析+验真（生产 redis 模式此处由 worker 异步消费）
    inv = db.query(Invoice).filter(Invoice.email_message_id == "<e2e1@example.com>").one()
    assert inv.status == "pending_submit"
    assert inv.verify_status == "passed"
    assert inv.parse_source == "XML"
    assert inv.invoice_number == "24312000000012345678"
    assert inv.total_amount is not None
    assert inv.total_amount == Decimal("1000.00")
    assert type(inv.total_amount) is Decimal
    assert inv.xml_url is not None  # XML 原件归档（合规硬约束）

    # 4. 财务专员通过 API 看到发票
    db.add(User(username="caiwu", password_hash=hash_password("pass123"), role=Role.finance_staff.value))
    db.flush()
    token = client.post(
        "/api/v1/auth/login", json={"username": "caiwu", "password": "pass123"}
    ).json()["access_token"]
    resp = client.get("/api/v1/invoices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["invoice_number"] == "24312000000012345678"
    assert item["status"] == "pending_submit"

    # 5. 审计日志留痕
    from invoicing.models import AuditLog

    actions = {log.action for log in db.query(AuditLog).all()}
    assert {"FETCH", "PARSE", "VERIFY", "LOGIN"} <= actions  # 身份事件审计（等保要求）


def test_dedup_index_multi_tenant_coexistence(db):
    from invoicing.models import Invoice

    db.add(Invoice(tenant_id="tenant-a", invoice_number="24312000000012345678", file_url="a.xml", file_type="XML"))
    db.add(Invoice(tenant_id="tenant-b", invoice_number="24312000000012345678", file_url="b.xml", file_type="XML"))
    db.flush()  # 不同租户同号共存，不触发唯一索引


def test_email_message_id_partial_index_allows_null(db):
    from invoicing.models import Invoice

    db.add(Invoice(invoice_number="24312000000011111111", file_url="a.xml", file_type="XML"))
    db.add(Invoice(invoice_number="24312000000022222222", file_url="b.xml", file_type="XML"))
    db.flush()  # 两个 NULL email_message_id 可共存（部分唯一索引语义）


def test_reverify_endpoint_roundtrip(db, client):
    from invoicing.models import Invoice, Role, User
    from invoicing.security import hash_password

    db.add(User(username="caiwu", password_hash=hash_password("pass123"), role=Role.finance_staff.value))
    db.flush()
    inv = Invoice(
        file_url="a.xml", file_type="XML", invoice_number="24312000000033333333",
        status="pending_submit", total_amount=Decimal("1000.00"), seller_name="示例科技有限公司",
        parse_source="XML", confidence_score=1.0, verify_status="passed",
    )
    db.add(inv)
    db.flush()
    token = client.post(
        "/api/v1/auth/login", json={"username": "caiwu", "password": "pass123"}
    ).json()["access_token"]
    resp = client.post(
        f"/api/v1/invoices/{inv.id}/verify",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["verify_status"] == "passed"  # 本地模式内联重验完成
    assert resp.json()["status"] == "pending_submit"
