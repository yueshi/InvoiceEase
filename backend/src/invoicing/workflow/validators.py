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
    """容错解析：接受 2026-06-10 / 2026/6/10 / 2026.6.10（LLM 输出常见）。

    解析失败返回 None —— 调用方须把它当作「日期未知」，不要当作「无日期即无事」。
    """
    if not raw:
        return None
    text = str(raw).strip().replace("/", "-").replace(".", "-")
    parts = text.split("-")
    if len(parts) == 3:
        try:
            text = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        except (TypeError, ValueError):
            return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


# 场景里的权威日期字段（按子类）：排序与归属判定都用它，occurred_on 只是兜底
_SCENE_DATE_KEYS = ("travel_date", "checkin", "checkout")


def _entry_date(scene: dict, occurred) -> date | None:
    """条目日期：优先场景权威字段（交通/住宿/市内交通各自必填），其次 occurred_on。

    Agent 流程通常不填 occurred_on —— 只用它排序会让行程退化为插入序（final review 实测假告警）。
    """
    for key in _SCENE_DATE_KEYS:
        d = _parse_date((scene or {}).get(key))
        if d:
            return d
    return _parse_date(occurred)


def _travel_entries(entries):
    """筛出差旅项，按**场景权威日期**排序（无日期排末尾，保持稳定）。"""
    travel = [
        (eid, scene or {}, occurred)
        for eid, scene, occurred in entries
        if str((scene or {}).get("subtype") or "").strip()
        in ("transport", "accommodation", "local_transport")
    ]
    def _key(item):
        d = _entry_date(item[1], item[2])
        return (d is None, d or date.max)
    return sorted(travel, key=_key)


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

# 城市档内置默认（settings.city_tiers 非空时**全量替换**，见 config.py）
_DEFAULT_CITY_TIERS: dict[str, str] = {
    "北京": "tier1", "上海": "tier1", "广州": "tier1", "深圳": "tier1",
}


def _city_tier_map() -> dict[str, str]:
    """生效的城市档映射：settings.city_tiers（JSON）优先，非法则回落内置。"""
    import json

    from invoicing.config import settings

    raw = (settings.city_tiers or "").strip()
    if not raw:
        return _DEFAULT_CITY_TIERS
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (ValueError, TypeError):
        pass
    return _DEFAULT_CITY_TIERS


def _normalize_city(city: str | None) -> str:
    """去掉常见行政后缀与空白（「北京市」应命中「北京」）。"""
    name = (city or "").strip()
    for suffix in ("市", "省", "自治区", "特别行政区"):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip()


def city_tier_of(city: str | None) -> str:
    """城市 → 档位；未收录一律 default（查不到具体档不判无标准）。"""
    return _city_tier_map().get(_normalize_city(city), "default")


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

        elif entry_type == "travel" and subtype == "accommodation":
            nights = _nights(scene)
            if nights is None:
                continue
            policy = policy_lookup(category="travel", item_key="accommodation",
                                   city_tier=tier)
            if policy is None:
                issues.append(Issue(
                    code="NO_POLICY_CONFIGURED",
                    message="未配置住宿标准，无法判定是否超标（建议补配置）",
                    severity="warning", entry_ids=[eid],
                ))
                continue
            per_night = (Decimal(amount) / nights).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            over = per_night - Decimal(policy.standard)
            severity = _severity_for(over, Decimal(policy.tolerance))
            if severity:
                issues.append(Issue(
                    code="PER_NIGHT_OVER_STANDARD",
                    message=f"住宿 {per_night} 元/晚超公司标准 {policy.standard} 元"
                            f"（{_trim_dec(nights)} 晚，超出 {over} 元）",
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
    issues.extend(_check_local_transport_daily(entries, policy_lookup=policy_lookup))
    return issues


def _check_local_transport_daily(entries, *, policy_lookup) -> list[Issue]:
    """市内交通按 (城市, 日期) 合计与日标准比对（逐笔判定会放过"一天打十次车"）。"""
    buckets: dict[tuple, dict] = {}
    for eid, entry_type, scene, occurred, amount in entries:
        scene = scene or {}
        if entry_type != "travel" or str(scene.get("subtype") or "").strip() != "local_transport":
            continue
        d = _entry_date(scene, occurred)
        key = (str(scene.get("city") or "").strip(), d)
        b = buckets.setdefault(key, {"ids": [], "total": Decimal("0")})
        b["ids"].append(eid)
        b["total"] += Decimal(amount)

    issues: list[Issue] = []
    for (city, d), b in buckets.items():
        tier = city_tier_of(city)
        policy = policy_lookup(category="travel", item_key="local_transport_day",
                               city_tier=tier)
        if policy is None:
            issues.append(Issue(
                code="NO_POLICY_CONFIGURED",
                message="未配置市内交通日标准，无法判定是否超标（建议补配置）",
                severity="warning", entry_ids=list(b["ids"]),
            ))
            continue
        over = b["total"] - Decimal(policy.standard)
        severity = _severity_for(over, Decimal(policy.tolerance))
        if severity:
            when = str(d) if d else "（日期未知）"
            issues.append(Issue(
                code="DAILY_TRANSPORT_OVER_STANDARD",
                message=f"{when} {city or ''} 市内交通合计 {b['total']} 元"
                        f"超日标准 {policy.standard} 元（超出 {over} 元）",
                severity=severity, entry_ids=list(b["ids"]),
            ))
    return issues


def _nights(scene: dict) -> Decimal | None:
    """住宿晚数：优先票据字段，缺失则用 入住/离店 日期差。"""
    raw = str(scene.get("nights") or "").strip()
    if raw:
        try:
            n = Decimal(raw)
            return n if n > 0 else None
        except Exception:  # noqa: BLE001
            return None
    checkin, checkout = _parse_date(scene.get("checkin")), _parse_date(scene.get("checkout"))
    if checkin and checkout and checkout > checkin:
        return Decimal((checkout - checkin).days)
    return None


def _trim_dec(v: Decimal) -> str:
    return format(v.normalize(), "f")


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


# ---- 补录归属建议 ------------------------------------------------------------

@dataclass
class Suggestion:
    claim_id: int
    claim_no: str
    score: int
    reasons: list[str] = field(default_factory=list)


_WINDOW_DAYS = 7


def suggest_claims_for_invoice(invoice, claims_with_entries, *,
                               window_days: int = _WINDOW_DAYS) -> list[Suggestion]:
    """发票补录：建议该票最可能归属的草稿单（spec §5.3 发票补录 Skill）。

    门控：单据有事项日期时，发票开票日必须落在 [最早, 最晚] ± window_days 内，
    否则排除（防误挂到无关单据）。
    打分：类型匹配 +3 / 开票日落在事项区间内 +3 / 仅落在窗口内 +1。
    返回：按分降序的前 3 条（同分保持输入顺序）。

    claims_with_entries 元素需有：id / claim_no / claim_type / entry_dates(list[date])。
    """
    issue_date = getattr(invoice, "issue_date", None)
    if issue_date is None:
        return []
    inv_type = getattr(invoice, "expense_type", None)

    out: list[Suggestion] = []
    for c in claims_with_entries:
        dates = [d for d in (getattr(c, "entry_dates", None) or []) if d]
        score = 0
        reasons: list[str] = []

        if dates:
            lo, hi = min(dates), max(dates)
            if lo <= issue_date <= hi:
                score += 3
                reasons.append("开票日在单据事项日期区间内")
            else:
                gap = (lo - issue_date).days if issue_date < lo else (issue_date - hi).days
                if gap > window_days:
                    continue  # 门控：离太远，不推荐
                score += 1
                reasons.append(f"开票日距单据行程 {gap} 天（窗口 {window_days} 天内）")

        ctype = getattr(c, "claim_type", None)
        if inv_type and ctype and inv_type == ctype:
            score += 3
            reasons.append(f"类型匹配（{inv_type}）")

        if score > 0:
            out.append(Suggestion(claim_id=c.id, claim_no=c.claim_no,
                                  score=score, reasons=reasons))

    out.sort(key=lambda s: -s.score)  # 稳定排序：同分保持输入顺序
    return out[:3]
