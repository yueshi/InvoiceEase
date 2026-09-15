"""用户与权限管理：自助改密、管理员重置、暂停/恢复、角色调整。

**防自锁规则**（不做会把系统锁死，只能改库才能救回来）：
- 不能暂停自己、不能改自己的角色 —— **这两条是真正生效的防线**
- 不能暂停或降级**最后一个可用管理员** —— **防御性规则，经 API 当前不可达**：
  操作者自身必须是可用管理员（`get_current_user` 已挡暂停用户），
  因此至少还剩一个可用管理员，目标不可能是"最后一个"。
  保留它是为了将来新增调用路径（脚本/其他角色可停管理员）时不至于失守；
  `test_last_admin_guard_is_defensive_only` 在 service 层钉住这条规则本身。

**角色变更与已有 MCP 令牌的关系**（重要，别误以为漏了）：
- 数据范围（role）**即时生效**——MCP 令牌的 claims 每次请求都重新组装
- 令牌的 scope 是**签发时固定**的：降级后旧令牌可能仍带多余 scope，
  但工具层是「role 与 scope 取交集」，角色闸门照样拦得住；
  要彻底收紧就撤销该用户的令牌（UI 会提示）。
"""
import logging
import secrets

from sqlalchemy import func
from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.models import McpToken, Role, User, UserStatus
from invoicing.security import hash_password, verify_password

logger = logging.getLogger(__name__)

PASSWORD_MIN_LENGTH = 8
_GENERATED_PASSWORD_BYTES = 12  # token_urlsafe(12) ≈ 16 字符

ACTION_PASSWORD_CHANGE = "PASSWORD_CHANGE"
ACTION_PASSWORD_RESET = "PASSWORD_RESET"
ACTION_SUSPEND = "USER_SUSPEND"
ACTION_RESUME = "USER_RESUME"
ACTION_ROLE_CHANGE = "USER_ROLE_CHANGE"
ACTION_CREATE = "USER_CREATE"


def generate_password() -> str:
    return secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)


def _validate_password(pw: str | None) -> None:
    if len(pw or "") < PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码至少 {PASSWORD_MIN_LENGTH} 位")


def _get(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError(f"用户不存在: {user_id}")
    return user


def _is_admin(u: User) -> bool:
    return (u.role or "") == Role.admin.value


def _other_active_admins(db: Session, exclude_id: int) -> int:
    """除某用户外，还有几个**可用的**管理员（暂停的不算）。"""
    return (
        db.query(func.count(User.id))
        .filter(
            User.role == Role.admin.value,
            User.status == UserStatus.ACTIVE.value,
            User.id != exclude_id,
        )
        .scalar()
        or 0
    )


def _denied(db: Session, actor: User, action: str, reason: str, **extra) -> None:
    """越权/危险操作的尝试要留痕（系统正确拦截，非失败）。"""
    write_audit(
        db, action=action, user_id=actor.id, channel="web",
        detail={"result": "rejected", "reason": reason, **extra},
    )
    db.commit()


def active_token_count(db: Session, user_id: int) -> int:
    """该用户未撤销、未过期的令牌数（UI 用于提示"降级后旧令牌仍带旧 scope"）。"""
    from invoicing.models.fields import utcnow

    now = utcnow()
    return (
        db.query(func.count(McpToken.id))
        .filter(
            McpToken.user_id == user_id,
            McpToken.revoked_at.is_(None),
            (McpToken.expires_at.is_(None)) | (McpToken.expires_at > now),
        )
        .scalar()
        or 0
    )


# ---- 密码 ------------------------------------------------------------------


def change_own_password(db: Session, user: User, old_password: str, new_password: str) -> None:
    """自助改密：须验旧密码（防会话被劫持后直接改密）。"""
    if not verify_password(old_password, user.password_hash):
        _denied(db, user, ACTION_PASSWORD_CHANGE, "wrong_old_password")
        raise ValueError("原密码不正确")
    _validate_password(new_password)
    if verify_password(new_password, user.password_hash):
        raise ValueError("新密码不能与原密码相同")

    user.password_hash = hash_password(new_password)
    user.must_change_password = False  # 改完即解除强制改密
    write_audit(
        db, action=ACTION_PASSWORD_CHANGE, user_id=user.id, channel="web",
        detail={"method": "self"},
    )
    db.commit()


def reset_password(
    db: Session, actor: User, target_id: int,
    new_password: str | None = None, generate: bool = False,
) -> tuple[User, str | None]:
    """管理员重置他人密码。返回 (用户, 一次性明文)。

    明文仅在**自动生成**时返回（管理员自己指定的密码他已知晓，无需回显）。
    重置后置 `must_change_password`——否则管理员会长期知道对方的密码。
    """
    if generate and new_password:
        raise ValueError("「自动生成」与「手动指定」只能选一种")
    if not generate:
        _validate_password(new_password)

    target = _get(db, target_id)
    plaintext = generate_password() if generate else new_password
    target.password_hash = hash_password(plaintext)  # type: ignore[arg-type]
    target.must_change_password = True
    write_audit(
        db, action=ACTION_PASSWORD_RESET, user_id=actor.id, channel="web",
        detail={"target_user_id": target.id, "target_username": target.username,
                "mode": "generated" if generate else "specified"},
    )
    db.commit()
    logger.info("管理员 %s 重置了用户 %s 的密码", actor.username, target.username)
    return target, (plaintext if generate else None)


# ---- 暂停 / 恢复 ------------------------------------------------------------


def set_status(db: Session, actor: User, target_id: int, *, suspend: bool) -> User:
    """暂停/恢复账号。暂停**立即生效**：登录、已签发 JWT、MCP 令牌三处同时失效。"""
    target = _get(db, target_id)
    action = ACTION_SUSPEND if suspend else ACTION_RESUME

    if suspend:
        if target.id == actor.id:
            _denied(db, actor, action, "self_suspend")
            raise ValueError("不能暂停自己的账号")
        if _is_admin(target) and _other_active_admins(db, target.id) == 0:
            _denied(db, actor, action, "last_admin")
            raise ValueError("不能暂停最后一个可用的管理员账号")
        if target.status == UserStatus.SUSPENDED.value:
            raise ValueError("该账号已处于暂停状态")

    target.status = (
        UserStatus.SUSPENDED.value if suspend else UserStatus.ACTIVE.value
    )
    write_audit(
        db, action=action, user_id=actor.id, channel="web",
        detail={"target_user_id": target.id, "target_username": target.username},
    )
    db.commit()
    return target


# ---- 角色 ------------------------------------------------------------------


def set_role(db: Session, actor: User, target_id: int, role: str) -> User:
    """调整角色（4 档）。数据范围即时生效；令牌 scope 见模块 docstring 的说明。"""
    if role not in Role.__members__:
        raise ValueError(f"非法角色: {role}（可选 {'/'.join(Role.__members__)}）")

    target = _get(db, target_id)
    if target.role == role:
        raise ValueError("角色未发生变化")
    if target.id == actor.id:
        _denied(db, actor, ACTION_ROLE_CHANGE, "self_role_change", to=role)
        raise ValueError("不能修改自己的角色（请联系其他管理员）")
    if _is_admin(target) and role != Role.admin.value and _other_active_admins(db, target.id) == 0:
        _denied(db, actor, ACTION_ROLE_CHANGE, "last_admin", to=role)
        raise ValueError("不能降级最后一个可用的管理员")

    old_role = target.role
    target.role = role
    write_audit(
        db, action=ACTION_ROLE_CHANGE, user_id=actor.id, channel="web",
        detail={"target_user_id": target.id, "target_username": target.username,
                "from": old_role, "to": role,
                # 令牌 scope 是签发时固定的：降级后旧令牌可能仍带多余 scope
                # （角色闸门仍会拦），提示管理员是否需要一并撤销
                "active_tokens": active_token_count(db, target.id)},
    )
    db.commit()
    return target


# ---- 新建 ------------------------------------------------------------------


def create_user(db: Session, actor: User, username: str, password: str, role: str) -> User:
    """新建用户（补审计：此前这个动作没有留痕）。"""
    if role not in Role.__members__:
        raise ValueError(f"非法角色: {role}（可选 {'/'.join(Role.__members__)}）")
    _validate_password(password)
    if db.query(User).filter(User.username == username).first():
        raise ValueError("用户名已存在")

    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    write_audit(
        db, action=ACTION_CREATE, user_id=actor.id, channel="web",
        detail={"target_user_id": user.id, "target_username": username, "role": role},
    )
    db.commit()
    return user
