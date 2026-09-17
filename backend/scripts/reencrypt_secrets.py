"""邮箱凭据重加密（Fernet key 轮换 / 从未配置 key 时期迁移）。**建议先停服**。

用法（backend 目录下）：
    uv run python scripts/reencrypt_secrets.py --from-key <旧key> --yes

行为：用旧 key 解密 mailboxes 表全部密文（password / smtp_password / agently_token），
用当前 INVOICING_FERNET_KEY 重新加密后写回。不加 --yes 只预览受影响行数。
旧 key 未知（产生于未配置 key 时期）时密文已无法解密，只能重新录入凭据。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from invoicing.config import settings  # noqa: E402

_FIELDS = ("password_encrypted", "smtp_password_encrypted", "agently_token_encrypted")


def main() -> int:
    from cryptography.fernet import Fernet, InvalidToken

    from invoicing.db import SessionLocal
    from invoicing.fetch.crypto import fernet_configured
    from invoicing.models import Mailbox

    ap = argparse.ArgumentParser()
    ap.add_argument("--from-key", required=True, help="旧 FERNET_KEY（44 字符 urlsafe base64）")
    ap.add_argument("--yes", action="store_true", help="确认执行（缺省只预览）")
    args = ap.parse_args()

    if not fernet_configured():
        print("当前 INVOICING_FERNET_KEY 未配置或非法，无法作为目标 key。先配置再执行。")
        return 1
    try:
        old = Fernet(args.from_key.encode())
    except Exception:
        print("--from-key 非法（需 44 字符 urlsafe base64 / 32 字节）")
        return 1

    with SessionLocal() as db:
        rows = (
            db.query(Mailbox)
            .filter(
                Mailbox.password_encrypted.isnot(None)
                | Mailbox.smtp_password_encrypted.isnot(None)
                | Mailbox.agently_token_encrypted.isnot(None)
            )
            .all()
        )
        if not rows:
            print("没有需要迁移的密文。")
            return 0
        print(f"待迁移邮箱 {len(rows)} 个（预览模式，加 --yes 执行）" if not args.yes else f"开始迁移 {len(rows)} 个邮箱…")
        new_fernet = Fernet(settings.fernet_key.encode())
        failed: list[str] = []
        done = 0
        for mb in rows:
            try:
                new_values = {
                    f: new_fernet.encrypt(old.decrypt(getattr(mb, f).encode())).decode()
                    for f in _FIELDS
                    if getattr(mb, f) is not None
                }
            except InvalidToken:
                failed.append(mb.name)
                continue
            if args.yes:
                for f, v in new_values.items():
                    setattr(mb, f, v)
            done += 1
        if args.yes:
            db.commit()
        print(f"完成: {done} 个邮箱" + (f"；失败（密文与旧 key 不匹配）: {', '.join(failed)}——请重新录入" if failed else ""))
        if not args.yes:
            print("（预览模式，未写库）")
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
