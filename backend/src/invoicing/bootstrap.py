from sqlalchemy.orm import Session

from invoicing.config import settings
from invoicing.models import Role, User
from invoicing.security import hash_password


def ensure_admin_user(db: Session) -> None:
    if db.query(User).count() > 0:
        return
    db.add(
        User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role=Role.admin.value,
        )
    )
    db.commit()
