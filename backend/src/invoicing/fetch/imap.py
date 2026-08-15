import email as email_lib
import imaplib

from invoicing.fetch.crypto import decrypt_secret
from invoicing.fetch.protocol import MailFetcher, RawAttachment, RawMailMessage
from invoicing.models import Mailbox


class ImapMailFetcher(MailFetcher):
    def __init__(self, mailbox: Mailbox):
        self.mailbox = mailbox

    def _connect(self):
        if self.mailbox.use_ssl:
            conn = imaplib.IMAP4_SSL(self.mailbox.imap_host, self.mailbox.imap_port)
        else:
            conn = imaplib.IMAP4(self.mailbox.imap_host, self.mailbox.imap_port)
        conn.login(self.mailbox.username, decrypt_secret(self.mailbox.password_encrypted))
        conn.select(self.mailbox.folder)
        return conn

    def fetch_new(self, last_uid: int) -> list[RawMailMessage]:
        conn = self._connect()
        try:
            typ, data = conn.uid("search", None, f"UID {last_uid + 1}:*")
            if typ != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()
            messages = []
            for uid in uids:
                typ, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[] RFC822.SIZE)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                messages.append(self._parse_message(int(uid), raw))
            return messages
        finally:
            conn.logout()

    def _parse_message(self, uid: int, raw: bytes) -> RawMailMessage:
        msg = email_lib.message_from_bytes(raw)
        attachments = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            filename = part.get_filename() or ""
            content_type = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload:
                attachments.append(RawAttachment(filename, content_type, payload))
        return RawMailMessage(
            uid=uid,
            message_id=msg.get("Message-ID"),
            subject=msg.get("Subject") or "",
            sender=msg.get("From") or "",
            attachments=attachments,
        )

    def mark_seen(self, uids: list[int]) -> None:
        if not uids:
            return
        conn = self._connect()
        try:
            for uid in uids:
                conn.uid("store", str(uid), "+FLAGS", "\\Seen")
        finally:
            conn.logout()
