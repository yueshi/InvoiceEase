from pathlib import Path

from invoicing.fetch.protocol import MailFetcher, RawAttachment, RawMailMessage
from invoicing.fetch.service import poll_mailbox
from invoicing.models import Invoice, Mailbox

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"
INVOICE_XML = (FIXTURES / "dianzi.xml").read_bytes()


class FakeFetcher(MailFetcher):
    def __init__(self, messages):
        self.messages = messages
        self.seen: list[int] = []

    def fetch_new(self, last_uid: int) -> list[RawMailMessage]:
        return [m for m in self.messages if m.uid > last_uid]

    def mark_seen(self, uids: list[int]) -> None:
        self.seen.extend(uids)


def _mailbox(db) -> Mailbox:
    mb = Mailbox(
        name="测试邮箱",
        imap_host="imap.example.com",
        imap_port=993,
        use_ssl=True,
        username="inv@example.com",
        password_encrypted="enc:whatever",
        keywords="发票,Invoice",
    )
    db.add(mb)
    db.flush()
    return mb


def _msg(uid, subject, attachments, sender="user@example.com", message_id=None):
    return RawMailMessage(
        uid=uid,
        message_id=message_id or f"<msg{uid}@example.com>",
        subject=subject,
        sender=sender,
        attachments=attachments,
    )


def test_poll_receives_xml_invoice(db):
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [_msg(1, "发票", [RawAttachment("dianzi.xml", "application/xml", INVOICE_XML)])]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 1
    assert result.rejected_images == 0
    inv = db.query(Invoice).filter(Invoice.email_message_id == "<msg1@example.com>").one()
    # 本地队列模式：收取后同步内联完成解析+验真（生产 redis 模式此处为 received）
    assert inv.status == "pending_submit"
    assert inv.parse_source == "XML"
    assert inv.file_type == "XML"
    assert mb.last_uid == 1
    assert fetcher.seen == [1]


def test_poll_rejects_image_and_replies(db):
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [
            _msg(
                1,
                "发票照片",
                [RawAttachment("photo.jpg", "image/jpeg", b"\xff\xd8\xff")],
            )
        ]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 0
    assert result.rejected_images == 1
    assert db.query(Invoice).count() == 0
    assert mb.last_uid == 1


def test_poll_unpacks_zip(db):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dianzi.xml", INVOICE_XML)
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [_msg(2, "Invoice", [RawAttachment("invoices.zip", "application/zip", buf.getvalue())])]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 1


def test_poll_ignores_irrelevant_subject(db):
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [_msg(3, "周报", [RawAttachment("report.pdf", "application/pdf", b"%PDF-1.4")])]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 0
    assert db.query(Invoice).count() == 0


def test_poll_skips_already_seen_uids(db):
    mb = _mailbox(db)
    mb.last_uid = 5
    fetcher = FakeFetcher([_msg(4, "发票", [RawAttachment("d.xml", "application/xml", INVOICE_XML)])])
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 0


def test_poll_same_email_two_invoices_both_stored(db):
    # 一信多票（正常场景）：同一 message_id 含两张发票附件，均须入库
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [
            _msg(1, "发票", [RawAttachment("dianzi.xml", "application/xml", INVOICE_XML)]),
            _msg(2, "发票", [RawAttachment("dianzi2.xml", "application/xml", INVOICE_XML)], message_id="<msg1@example.com>"),
        ]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 2
    assert result.duplicates == 0
    # 同票号第二票在解析阶段查重拦截（物理删除，不留空壳）：库中仅存一张
    assert db.query(Invoice).filter(Invoice.email_message_id == "<msg1@example.com>").count() == 1


def test_poll_duplicate_email_same_file_blocked(db):
    # 同一 message_id + 同 uid + 同文件名 → 同一 file_url key，复合唯一索引兜底拦截
    mb = _mailbox(db)
    fetcher = FakeFetcher(
        [
            _msg(1, "发票", [RawAttachment("dianzi.xml", "application/xml", INVOICE_XML)]),
            _msg(1, "发票", [RawAttachment("dianzi.xml", "application/xml", INVOICE_XML)], message_id="<msg1@example.com>"),
        ]
    )
    result = poll_mailbox(db, mb, fetcher)
    assert result.received == 1
    assert result.duplicates == 1


def test_poll_agently_mailbox_uses_agently_fetcher(db, monkeypatch):
    from invoicing.fetch.agently import AgentlyFetcher
    from invoicing.fetch.service import poll_mailbox

    mb = Mailbox(name="Agently", mailbox_type="agently", imap_host=None, username=None, password_encrypted=None)
    db.add(mb)
    db.flush()
    captured = {}

    class FakeAgentlyFetcher(AgentlyFetcher):
        def fetch_new(self, last_uid):
            captured["called"] = True
            return []

        def mark_seen(self, uids):
            pass

    monkeypatch.setattr("invoicing.fetch.agently.AgentlyFetcher", FakeAgentlyFetcher)
    poll_mailbox(db, mb)
    assert captured.get("called") is True


def test_reject_reply_agently_two_step(db, monkeypatch):
    from invoicing.fetch.reply import send_reject_reply

    mb = Mailbox(name="Agently", mailbox_type="agently", imap_host=None, username=None, password_encrypted=None)
    db.add(mb)
    db.flush()
    calls = []

    def fake_run_cli(mailbox, args, timeout=30):
        calls.append(args)
        if "--confirmation-token" in args:
            return {"ok": True, "data": {"queued": True}}
        return {"ok": True, "data": {"confirmation_required": True, "confirmation_token": "ctk_x"}}

    monkeypatch.setattr("invoicing.fetch.agently.run_cli", fake_run_cli)
    monkeypatch.setattr("invoicing.fetch.agently.REQUEST_INTERVAL", 0.0)  # 测试不等待（+reply 前置限流 sleep）
    send_reject_reply(mb, "user@agent.qq.com", "发票照片", provider_message_id="msg_1")
    assert len(calls) == 2  # 首跑 + 确认重跑
    assert calls[0][:3] == ["message", "+reply", "--id"]
    assert "--confirmation-token" in calls[1]
