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

# ---- 餐补 / 招待合规 ---------------------------------------------------------

# 城市档映射（P1 先内置常见一线；运营可改为政策表驱动，见 P2 规划）
_CITY_TIER: dict[str, str] = {
    "北京": "tier1", "上海": "tier1", "广州": "tier1", "深圳": "tier1",
}


def city_tier_of(city: str | None) -> str:
    """城市 → 档位；未收录一律 default（查不到具体档不判无标准）。"""
    return _CITY_TIER.get((city or "").strip(), "default")


def _severity_for(overage: Decimal, tolerance: Decimal) -> str | None:
    """超标准程度 → 严重度：未超 None / 超容忍内 warning / 超容忍 error。"""
    if overage <= 0:
        return None
    return "warning" if overage <= tolerance else "error"


def check_meal(entries, *, policy_lookup) -> list[Issue]:
    """餐补与招待合规（spec §4.2「条件固化」）：

    - 差旅伙食补助：日标准（未填则 金额/天数 推断）vs policy(travel/meal_allowance)
    - 招待：人均 = 金额/人数 vs policy(entertainment/per_head)
    - 无政策配置 → NO_POLICY_CONFIGURED（warning，不阻断）
    - 招待人数缺失/非正 → HEADCOUNT_UNKNOWN（warning，不猜数）

    policy_lookup 由调用方注入（绑定 db+tenant），签名：(category, item_key, city_tier)。
    """
    issues: list[Issue] = []
    for eid, entry_type, scene, _occurred, amount in entries:
        scene = scene or {}
        subtype = str(scene.get("subtype") or "").strip()
        tier = city_tier_of(scene.get("city"))

        if entry_type == "travel" and subtype == "allowance":
            per_day = _allowance_daily(scene, amount)
            if per_day is None:
                continue
            policy = policy_lookup(category="travel", item_key="meal_allowance",
                                   city_tier=tier)
            if policy is None:
                issues.append(Issue(
                    code="NO_POLICY_CONFIGURED",
                    message="未配置差旅伙食补助标准，无法判定是否超标（建议补配置）",
                    severity="warning", entry_ids=[eid],
                ))
                continue
            over = per_day - Decimal(policy.standard)
            severity = _severity_for(over, Decimal(policy.tolerance))
            if severity:
                issues.append(Issue(
                    code="OVER_STANDARD",
                    message=f"伙食补助日标准 {per_day} 元超公司标准 "
                            f"{policy.standard} 元（超出 {over} 元）",
                    severity=severity, entry_ids=[eid],
                ))

        elif entry_type == "entertainment":
            policy = policy_lookup(category="entertainment", item_key="per_head",
                                   city_tier=tier)
            if policy is None:
                issues.append(Issue(
                    code="NO_POLICY_CONFIGURED",
                    message="未配置招待人均标准，无法判定是否超标（建议补配置）",
                    severity="warning", entry_ids=[eid],
                ))
                continue
            headcount = _headcount(scene)
            if headcount is None:
                issues.append(Issue(
                    code="HEADCOUNT_UNKNOWN",
                    message="招待人数缺失或非正数，无法计算人均（请补充人数）",
                    severity="warning", entry_ids=[eid],
                ))
                continue
            per_head = (Decimal(amount) / headcount).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            over = per_head - Decimal(policy.standard)
            severity = _severity_for(over, Decimal(policy.tolerance))
            if severity:
                issues.append(Issue(
                    code="PER_HEAD_OVER_STANDARD",
                    message=f"招待人均 {per_head} 元超公司标准 "
                            f"{policy.standard} 元（{headcount} 人，超出 {over} 元）",
                    severity=severity, entry_ids=[eid],
                ))
    return issues


def _allowance_daily(scene: dict, amount) -> Decimal | None:
    """补助日标准：优先场景字段；缺失则 金额/天数 推断；都拿不到 → None。"""
    raw = str(scene.get("daily_standard") or "").strip()
    if raw:
        try:
            return Decimal(raw)
        except Exception:  # noqa: BLE001
            return None
    days_raw = str(scene.get("days") or "").strip()
    try:
        days = Decimal(days_raw)
    except Exception:  # noqa: BLE001
        return None
    if days <= 0:
        return None
    return (Decimal(amount) / days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _headcount(scene: dict) -> Decimal | None:
    raw = str(scene.get("headcount") or "").strip()
    try:
        n = Decimal(raw)
    except Exception:  # noqa: BLE001
        return None
    return n if n > 0 else None
