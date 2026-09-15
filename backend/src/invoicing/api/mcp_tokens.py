"""MCP 令牌管理 API：自助签发（所有登录用户）+ 管理员代发与全局查看。

设计见 design/2026-09-13-MCP身份与权限设计.md §10.1/§10.4。

明文只在 `POST` 响应里出现一次（`plaintext` 字段）；此后任何接口都不再返回。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import McpToken, Role, User
from invoicing.mcp.identity import ROLE_DEFAULT_SCOPES, SCOPES
from invoicing.security import get_current_user, require_role
from invoicing.workflow import mcp_tokens as svc

router = APIRouter(prefix="/mcp-tokens", tags=["mcp-tokens"])


class TokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    token_prefix: str
    user_id: int
    scopes: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    state: str  # active | revoked | expired
    owner_username: str | None = None


class IssueIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    user_id: int | None = None  # 缺省 = 自己；仅管理员可指定他人
    scopes: list[str] | None = None  # 缺省 = 目标角色预设
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    never_expires: bool = False


class IssueOut(BaseModel):
    token: TokenOut
    plaintext: str  # **仅此一次**：明文不落库，关闭后不可再取


def _out(row: McpToken, owner: str | None = None) -> TokenOut:
    return TokenOut(
        id=row.id, name=row.name, token_prefix=row.token_prefix, user_id=row.user_id,
        scopes=[s for s in row.scopes.split(",") if s],
        expires_at=row.expires_at, revoked_at=row.revoked_at,
        last_used_at=row.last_used_at, created_at=row.created_at,
        state=svc.token_state(row), owner_username=owner,
    )


@router.get("/scopes")
def list_scopes(_: User = Depends(get_current_user)):
    """可用 scope 与各角色预设（前端渲染选择器用）。"""
    return {"scopes": list(SCOPES), "role_defaults": ROLE_DEFAULT_SCOPES}


@router.get("", response_model=list[TokenOut])
def list_tokens(
    all_users: bool = Query(False, description="管理员查看全部用户（含代管场景）"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = svc.list_tokens(db, user, all_users=all_users)
    owners = {u.id: u.username for u in db.query(User).all()} if all_users else {}
    return [_out(r, owners.get(r.user_id)) for r in rows]


@router.post("", response_model=IssueOut, status_code=201)
def issue_token(
    body: IssueIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        row, plaintext = svc.issue_token(
            db, user, name=body.name, user_id=body.user_id,
            scopes=body.scopes, expires_in_days=body.expires_in_days,
            never_expires=body.never_expires,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return IssueOut(token=_out(row), plaintext=plaintext)


@router.post("/{token_id}/revoke", response_model=TokenOut)
def revoke_token(
    token_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    try:
        row = svc.revoke_token(db, user, token_id)
    except ValueError as e:
        raise HTTPException(404 if "不存在" in str(e) else 403, str(e)) from None
    return _out(row)
