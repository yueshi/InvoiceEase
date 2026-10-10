"""v1.1 §7.5 两段握手 + 幂等键：工具模块。

- idempotent_run：同 (key, tool_name) 重放返回缓存（24h 窗口）
- create_proposal：签发一次性 token（默认 15 分钟 TTL）
- consume_proposal：校验 token + human_ack，标记 consumed
- generate_token：URL-safe token（~43 字符）
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select

from invoicing.models.idempotency import IdempotencyKey
from invoicing.models.proposal import Proposal


IDEMPOTENCY_TTL_HOURS = 24
PROPOSAL_TTL_SECONDS = 900  # 15 分钟（v1.1 §7.5）


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def namespace_of(tool_name: str, actor_id: int | None, phase: str = "") -> str:
    """幂等命名空间 = 工具 + 主体（+阶段）。

    主体维度是必须的：否则两个用户用同一 idempotency_key 会互相命中缓存——
    B 的写操作会**拿到 A 的结果且不执行**（静默假成功），proposal 阶段还会
    泄漏 A 的 token 与预览（final review 实测）。
    ponytail: 命名空间编进 tool_name 列；如需按主体查询，再拆独立列。
    """
    ns = f"{tool_name}:u{actor_id if actor_id is not None else 0}"
    return f"{ns}:{phase}" if phase else ns


def idempotent_run(db, *, key: str | None, tool_name: str,
                   actor_id: int | None = None, phase: str = "",
                   fn: Callable[[], dict]) -> dict:
    """有 key 走缓存（24h 内同 (key, 命名空间) 返回首次结果）；无 key 每次都调 fn。"""
    if not key:
        return fn()
    ns = namespace_of(tool_name, actor_id, phase)
    existing = db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.key == key,
            IdempotencyKey.tool_name == ns,
        )
    ).scalar_one_or_none()
    if existing is not None and _as_utc(existing.expires_at) > _now():
        return existing.response

    result = fn()
    if existing is not None:  # 过期残留：就地刷新
        existing.response = result
        existing.expires_at = _now() + timedelta(hours=IDEMPOTENCY_TTL_HOURS)
    else:
        db.add(IdempotencyKey(
            key=key, tool_name=ns, response=result,
            expires_at=_now() + timedelta(hours=IDEMPOTENCY_TTL_HOURS),
        ))
    db.commit()
    return result


def create_proposal(db, *, tool_name: str, payload: dict, preview: dict,
                    actor_id: int, actor_type: str, channel: str,
                    ttl_seconds: int = PROPOSAL_TTL_SECONDS) -> Proposal:
    """签发 proposal（不产生业务副作用），返回含 token 的 ORM 对象。"""
    p = Proposal(
        token=generate_token(), tool_name=tool_name, payload=payload,
        preview=preview, actor_id=actor_id, actor_type=actor_type,
        channel=channel,
        expires_at=_now() + timedelta(seconds=ttl_seconds),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def consume_proposal(db, *, token: str, human_ack: bool) -> Proposal:
    """校验 token + human_ack 并标记 consumed；返回 Proposal（调用方取 payload 落库）。"""
    if not human_ack:
        raise ValueError("human_ack=true required to consume proposal")
    p = db.execute(
        select(Proposal).where(Proposal.token == token)
    ).scalar_one_or_none()
    if p is None:
        raise ValueError(f"proposal not found: {token}")
    if _as_utc(p.expires_at) < _now():
        raise ValueError(f"proposal expired: {token}")
    if p.consumed_at is not None:
        raise ValueError(f"proposal already consumed: {token}")
    p.consumed_at = _now()
    db.commit()
    db.refresh(p)
    return p


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """SQLite 取回的是 naive datetime（UTC 语义）；补 tz 便于比较。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)