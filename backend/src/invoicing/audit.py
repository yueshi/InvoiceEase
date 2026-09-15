"""审计写入与结果归一化。

结果类别（outcome）——审计记录「事件是否成功」是等保 2.0 的明确要素：
- success：正常完成（解析成功/验真通过/复核通过/登录成功）
- blocked：**正常业务处置**（重复拦截、非发票拒收、红字待核……）——不是失败
- failed：业务失败（验真失败、登录失败）
- error：系统异常（解析异常、收信错误）
- None：无成败语义（配置变更、字段更新、删除等）

写入时落库（audit_logs.outcome），供列表筛选（如「仅看异常」）与统计。
"""
from sqlalchemy.orm import Session

from invoicing.models import AuditLog

_BLOCKED_RESULTS = {
    "duplicate", "not_invoice", "duplicate_cleanup", "duplicate_email",
    "zip_no_invoice", "ignored_attachment", "blocked", "rejected",
}
_ERROR_RESULTS = {"error", "parse_error"}
_FAILED_RESULTS = {"failed"}


def classify_outcome(action: str, detail: dict | None) -> str | None:
    """按 action + detail 归一化结果类别；无成败语义返回 None。"""
    detail = detail or {}
    result = detail.get("result")

    if action == "LOGIN":
        return "success"
    if action == "LOGIN_FAILED":
        return "failed"
    if action == "LOGOUT":
        return None

    if action == "PARSE":
        if isinstance(result, str):
            if result in _BLOCKED_RESULTS:
                return "blocked"
            if result in _ERROR_RESULTS:
                return "error"
        if detail.get("errors"):
            return "failed"  # 校验/解析错误 → 待复核
        return "success"

    if action == "VERIFY":
        if result == "passed":
            return "success"
        if result in _FAILED_RESULTS:
            return "failed"
        if result in _ERROR_RESULTS:
            return "error"
        return None

    if action == "FETCH":
        if isinstance(result, dict):
            return "success"
        if isinstance(result, str):
            if result in _BLOCKED_RESULTS:
                return "blocked"
            if result in _ERROR_RESULTS:
                return "error"
        return None

    if action == "REVIEW":
        return "success" if detail.get("action") == "approve" else "failed" if detail.get("action") == "reject" else None

    if action == "REJECT_REPLY":
        return "success"

    if isinstance(result, str):
        if result in _ERROR_RESULTS:
            return "error"
        if result in _BLOCKED_RESULTS:
            return "blocked"
        if result in _FAILED_RESULTS:
            return "failed"

    # 账号安全事件（密码/状态/角色/令牌）：越权或危险操作被拦是**系统正确处置**（blocked）
    if action in {
        "MCP_TOKEN_ISSUE", "MCP_TOKEN_REVOKE",
        "PASSWORD_CHANGE", "PASSWORD_RESET",
        "USER_SUSPEND", "USER_RESUME", "USER_ROLE_CHANGE", "USER_CREATE",
    }:
        if result in _BLOCKED_RESULTS:
            return "blocked"
        if result in _ERROR_RESULTS:
            return "error"
        return "success"

    # 上传/删除/更新/归类/放行/配置变更等：完成即成功，但配置变更无成败语义
    if action == "CONFIG_CHANGE":
        return None
    if action in {"INVOICE_UPLOAD", "INVOICE_DELETE", "UNBLOCK", "INGEST", "AI_REVIEW", "AUTO_REVIEW", "REVERIFY"}:
        return "success"
    return None


def write_audit(
    db: Session,
    action: str,
    user_id: int | None = None,
    invoice_id: int | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
    channel: str = "web",
) -> AuditLog:
    log = AuditLog(
        user_id=user_id,
        action=action,
        invoice_id=invoice_id,
        detail=detail,
        ip_address=ip_address,
        channel=channel,
        outcome=classify_outcome(action, detail),
    )
    db.add(log)
    db.flush()
    return log
