import logging
import smtplib
from email.mime.text import MIMEText

from invoicing.fetch.crypto import decrypt_secret
from invoicing.models import Mailbox

logger = logging.getLogger(__name__)

REJECT_TEMPLATE = (
    "您好：\n\n"
    "我们检测到您发送的发票附件为非标准格式（图片）。\n"
    "为符合电子发票归档要求，请重新发送该发票的 OFD/PDF/XML 源文件。\n\n"
    "——发票易自动处理系统"
)


def send_reject_reply(mailbox: Mailbox, to_addr: str, subject: str) -> None:
    if not mailbox.smtp_host or not to_addr:
        logger.info("SMTP 未配置或收件人为空，跳过拒收回复 mailbox_id=%s", mailbox.id)
        return
    msg = MIMEText(REJECT_TEMPLATE, "plain", "utf-8")
    msg["Subject"] = f"Re: {subject}"
    msg["From"] = mailbox.smtp_username or mailbox.username
    msg["To"] = to_addr
    if mailbox.use_ssl or mailbox.smtp_port == 465:
        server = smtplib.SMTP_SSL(mailbox.smtp_host, mailbox.smtp_port or 465, timeout=30)
    else:
        server = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port or 25, timeout=30)
    try:
        password = decrypt_secret(mailbox.smtp_password_encrypted) if mailbox.smtp_password_encrypted else ""
        if password:
            server.login(mailbox.smtp_username or mailbox.username, password)
        server.sendmail(msg["From"], [to_addr], msg.as_string())
    finally:
        server.quit()
