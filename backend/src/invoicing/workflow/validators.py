"""业务合理性校验（v1.1 §4.2「条件固化」）：行程 / 餐补 / 补录归属。

与 P0-1 `workflow.validation.validate_expense` 的分工：
- `validate_expense`  管报销单**能不能提交**（金额/价税合计/重复/凭证/预算）→ PASS/FAIL/NEEDS_REVIEW
- `validators`        管**业务上合不合理**（行程闭环/标准超标/该挂哪张单）→ Issue 列表

severity 语义（对齐 spec §4.2）：
- `error`   逻辑矛盾，阻断
- `warning` 需人工判断（转人工分支），不阻断

本模块**纯函数优先**（入参为普通数据结构），便于单测；DB 装配在 mcp 层。
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


@dataclass
class Issue:
    code: str
    message: str
    severity: str                    # error / warning
    entry_ids: list[int] = field(default_factory=list)


# ---- 行程一致性 --------------------------------------------------------------


def _parse_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return None


def _travel_entries(entries):
    """筛出差旅项，按发生日期排序（无日期排末尾，保持稳定）。"""
    travel = [
        (eid, scene or {}, occurred)
        for eid, scene, occurred in entries
        if str((scene or {}).get("subtype") or "").strip()
        in ("transport", "accommodation", "local_transport")
    ]
    return sorted(
        travel,
        key=lambda item: (_parse_date(item[2]) is None,
                          _parse_date(item[2]) or date.max),
    )


def check_trip(entries: list[tuple[int, dict, str | None]]) -> list[Issue]:
    """行程重构校验：
    - 有城际去程但无返程（首段出发城市 ≠ 末段到达城市）→ NO_RETURN_TRIP（warning）
    - 相邻城际段城市不接续 → ROUTE_DISCONTINUOUS（warning）
    - 住宿 nights 与入住/离店日期差不符 → NIGHTS_MISMATCH（error）
    - 离店早于入住 → STAY_DATE_INVALID（error）
    """
    issues: list[Issue] = []
    travel = _travel_entries(entries)

    segments = [(eid, s) for eid, s, _ in travel if s.get("subtype") == "transport"]
    stays = [(eid, s) for eid, s, _ in travel if s.get("subtype") == "accommodation"]

    # 返程闭环
    if segments:
        first_from = str(segments[0][1].get("from_city") or "").strip()
        last_to = str(segments[-1][1].get("to_city") or "").strip()
        if first_from and last_to and first_from != last_to:
            issues.append(Issue(
                code="NO_RETURN_TRIP",
                message=f"行程未见返程：首段自 {first_from} 出发，末段到达 {last_to}"
                        f"（请补充返程票据或说明）",
                severity="warning",
                entry_ids=[eid for eid, _ in segments],
            ))

    # 段间接续
    for (prev_id, prev), (cur_id, cur) in zip(segments, segments[1:]):
        prev_to = str(prev.get("to_city") or "").strip()
        cur_from = str(cur.get("from_city") or "").strip()
        if prev_to and cur_from and prev_to != cur_from:
            issues.append(Issue(
                code="ROUTE_DISCONTINUOUS",
                message=f"行程不接续：上一段到达 {prev_to}，下一段自 {cur_from} 出发",
                severity="warning",
                entry_ids=[prev_id, cur_id],
            ))

    # 住宿
    for eid, s in stays:
        checkin, checkout = _parse_date(s.get("checkin")), _parse_date(s.get("checkout"))
        if checkin and checkout:
            if checkout < checkin:
                issues.append(Issue(
                    code="STAY_DATE_INVALID",
                    message=f"住宿日期矛盾：入住 {checkin} 晚于离店 {checkout}",
                    severity="error", entry_ids=[eid],
                ))
            else:
                nights = str(s.get("nights") or "").strip()
                if nights:
                    try:
                        declared = Decimal(nights)
                    except Exception:  # noqa: BLE001 —— 非数字不参与矛盾判定
                        declared = None
                    actual = Decimal((checkout - checkin).days)
                    if declared is not None and declared != actual:
                        issues.append(Issue(
                            code="NIGHTS_MISMATCH",
                            message=f"住宿晚数矛盾：票据 {declared} 晚，"
                                    f"入住 {checkin} 至离店 {checkout} 为 {actual} 晚",
                            severity="error", entry_ids=[eid],
                        ))
    return issues