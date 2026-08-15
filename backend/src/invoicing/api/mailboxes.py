from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.fetch.crypto import encrypt_secret
from invoicing.fetch.service import PollResult, poll_mailbox
from invoicing.models import Mailbox, User
from invoicing.schemas.mailbox import MailboxCreate, MailboxOut, MailboxUpdate, PollResultOut
from invoicing.security import require_role

router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])


@router.get("", response_model=list[MailboxOut])
def list_mailboxes(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return db.query(Mailbox).order_by(Mailbox.id).all()


@router.post("", response_model=MailboxOut)
def create_mailbox(body: MailboxCreate, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    if body.mailbox_type == "imap":
        if not (body.imap_host and body.username and body.password):
            raise HTTPException(422, "IMAP 类型邮箱必须提供 imap_host/username/password")
        agently_token = None
        agently_workspace = None
    elif body.mailbox_type == "agently":
        agently_token = encrypt_secret(body.agently_token) if body.agently_token else None
        agently_workspace = body.agently_workspace
    else:
        raise HTTPException(422, f"非法邮箱类型: {body.mailbox_type}")
    mb = Mailbox(
        name=body.name,
        mailbox_type=body.mailbox_type,
        agently_workspace=agently_workspace,
        agently_token_encrypted=agently_token,
        imap_host=body.imap_host if body.mailbox_type == "imap" else None,
        imap_port=body.imap_port,
        use_ssl=body.use_ssl,
        username=body.username if body.mailbox_type == "imap" else None,
        password_encrypted=encrypt_secret(body.password) if body.mailbox_type == "imap" else None,
        folder=body.folder,
        keywords=body.keywords,
        poll_interval_seconds=body.poll_interval_seconds,
        smtp_host=body.smtp_host,
        smtp_port=body.smtp_port,
        smtp_username=body.smtp_username,
        smtp_password_encrypted=encrypt_secret(body.smtp_password) if body.smtp_password else None,
    )
    db.add(mb)
    db.commit()
    return mb


@router.put("/{mailbox_id}", response_model=MailboxOut)
def update_mailbox(
    mailbox_id: int,
    body: MailboxUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    mb = db.get(Mailbox, mailbox_id)
    if mb is None:
        raise HTTPException(404, "邮箱配置不存在")
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "password":
            # 非空才加密写入；null/空串表示「不修改」，避免写 None 触发 NOT NULL 约束
            if value:
                mb.password_encrypted = encrypt_secret(value)
        elif field == "smtp_password":
            if value:
                mb.smtp_password_encrypted = encrypt_secret(value)
        elif field == "agently_token":
            if value:
                mb.agently_token_encrypted = encrypt_secret(value)
        else:
            setattr(mb, field, value)
    db.commit()
    return mb


@router.post("/{mailbox_id}/test")
def test_connection(mailbox_id: int, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    from invoicing.fetch.imap import ImapMailFetcher

    mb = db.get(Mailbox, mailbox_id)
    if mb is None:
        raise HTTPException(404, "邮箱配置不存在")
    if mb.mailbox_type == "agently":
        from invoicing.fetch.agently import AgentlyCliError, run_cli

        try:
            payload = run_cli(mb, ["+me"])
            aliases = (payload.get("data") or {}).get("aliases") or []
            email = aliases[0].get("email", "未知") if aliases else "未知"
            return {"ok": True, "message": f"CLI 可用，邮箱 {email}"}
        except AgentlyCliError as e:
            return {"ok": False, "message": str(e)}
    try:
        fetcher = ImapMailFetcher(mb)
        conn = fetcher._connect()
        conn.logout()
        return {"ok": True, "message": "IMAP 连接成功"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/{mailbox_id}/poll", response_model=PollResultOut)
def trigger_poll(mailbox_id: int, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    mb = db.get(Mailbox, mailbox_id)
    if mb is None:
        raise HTTPException(404, "邮箱配置不存在")
    result = poll_mailbox(db, mb)
    return result
