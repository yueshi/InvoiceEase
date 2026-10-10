"""check_meal 餐补/招待合规测试（v1.1 §4.2/§4.3：标准 + 容忍值）。

入参：[(entry_id, entry_type, scene_fields, occurred_on, amount), ...]
policy_lookup(category=..., item_key=..., city_tier=...) -> policy | None（注入以便单测）
severity：≤标准 → 无；超标准但 ≤容忍 → warning（转人工）；超容忍 → error（阻断）。
"""
from decimal import Decimal
from types import SimpleNamespace

from invoicing.workflow.validators import check_meal


def _policy(std, tol="50"):
    return SimpleNamespace(standard=Decimal(std), tolerance=Decimal(tol))


def _allowance(eid, days="1", daily="100", amount="100", city=None):
    scene = {"subtype": "allowance", "days": days, "daily_standard": daily}
    if city:
        scene["city"] = city
    return (eid, "travel", scene, "2026-06-10", Decimal(amount))


def _entertain(eid, headcount="2", amount="500", city=None):
    scene = {"guests": "客户", "headcount": headcount}
    if city:
        scene["city"] = city
    return (eid, "entertainment", scene, "2026-06-10", Decimal(amount))


# ---- 伙食补助 --------------------------------------------------------------

def test_allowance_within_standard_ok():
    assert check_meal([_allowance(1, daily="100")],
                      policy_lookup=lambda **kw: _policy("100")) == []


def test_allowance_over_standard_within_tolerance_warns():
    issues = check_meal([_allowance(1, daily="130")],
                        policy_lookup=lambda **kw: _policy("100"))
    assert any(i.code == "OVER_STANDARD" and i.severity == "warning" for i in issues)


def test_allowance_over_tolerance_fails():
    issues = check_meal([_allowance(1, daily="300")],
                        policy_lookup=lambda **kw: _policy("100"))
    assert any(i.code == "OVER_STANDARD" and i.severity == "error" for i in issues)


def test_allowance_exact_tolerance_boundary_warns_only():
    """正好等于 标准+容忍 → 仍只 warning（不阻断）。"""
    issues = check_meal([_allowance(1, daily="150")],
                        policy_lookup=lambda **kw: _policy("100", tol="50"))
    assert issues and all(i.severity == "warning" for i in issues)


def test_allowance_without_declared_daily_uses_amount_over_days():
    """票据未写日标准 → 用 金额/天数 推断。"""
    issues = check_meal([_allowance(1, days="2", daily="", amount="400")],
                        policy_lookup=lambda **kw: _policy("100"))
    assert any(i.code == "OVER_STANDARD" and i.severity == "error" for i in issues)


# ---- 招待 ------------------------------------------------------------------

def test_entertainment_per_head_over_standard():
    # 人均 250，标准 200，容忍 50 → 正好边界：warning
    issues = check_meal([_entertain(7, headcount="2", amount="500")],
                        policy_lookup=lambda **kw: _policy("200"))
    assert any(i.code == "PER_HEAD_OVER_STANDARD" and i.severity == "warning" for i in issues)


def test_entertainment_per_head_over_tolerance_fails():
    # 人均 450，标准 300，容忍 50 → 超容忍：error
    issues = check_meal([_entertain(7, headcount="2", amount="900")],
                        policy_lookup=lambda **kw: _policy("300"))
    assert any(i.code == "PER_HEAD_OVER_STANDARD" and i.severity == "error" for i in issues)


def test_entertainment_missing_headcount_warns():
    e = (7, "entertainment", {"guests": "客户"}, "2026-06-10", Decimal("900"))
    issues = check_meal([e], policy_lookup=lambda **kw: _policy("300"))
    assert any(i.code == "HEADCOUNT_UNKNOWN" and i.severity == "warning" for i in issues)


def test_entertainment_zero_headcount_warns_not_divide_by_zero():
    issues = check_meal([_entertain(7, headcount="0", amount="900")],
                        policy_lookup=lambda **kw: _policy("300"))
    assert any(i.code == "HEADCOUNT_UNKNOWN" for i in issues)


# ---- 降级 / 其它 ------------------------------------------------------------

def test_no_policy_configured_warns_not_fails():
    issues = check_meal([_allowance(1, daily="100")],
                        policy_lookup=lambda **kw: None)
    assert any(i.code == "NO_POLICY_CONFIGURED" and i.severity == "warning"
               for i in issues)


def test_non_meal_entries_ignored():
    transport = (1, "travel", {"subtype": "transport", "from_city": "上海"},
                 "2026-06-10", Decimal("500"))
    assert check_meal([transport], policy_lookup=lambda **kw: _policy("100")) == []


def test_lookup_receives_correct_dimensions():
    """注入的 lookup 必须收到 (category, item_key, city_tier) 三维。"""
    seen = []

    def lookup(**kw):
        seen.append(kw)
        return _policy("100")

    check_meal([_allowance(1)], policy_lookup=lookup)
    assert seen == [{"category": "travel", "item_key": "meal_allowance",
                     "city_tier": "default"}]

    seen.clear()
    check_meal([_entertain(2)], policy_lookup=lookup)
    assert seen == [{"category": "entertainment", "item_key": "per_head",
                     "city_tier": "default"}]

# ---- final review：住宿与市内交通标准（种子已配但原先无人读取） -------------

def _stay(eid, checkin="2026-06-10", checkout="2026-06-12", nights="2",
          amount="1200", city="北京"):
    return (eid, "travel", {"subtype": "accommodation", "city": city,
                            "checkin": checkin, "checkout": checkout,
                            "nights": nights}, checkin, Decimal(amount))


def _local(eid, date="2026-06-10", amount="100", city="北京"):
    return (eid, "travel", {"subtype": "local_transport", "city": city,
                            "travel_date": date}, date, Decimal(amount))


def _by_item(mapping):
    """按 item_key 返回不同标准的 lookup。"""
    def lookup(**kw):
        std = mapping.get(kw["item_key"])
        return _policy(std) if std else None
    return lookup


def test_accommodation_per_night_within_standard_ok():
    # 1200 / 2 晚 = 600/晚，标准 500，容忍 50 → 超 100 > 50 → error
    issues = check_meal([_stay(1)], policy_lookup=_by_item({"accommodation": "700"}))
    assert issues == []


def test_accommodation_per_night_over_tolerance_fails():
    issues = check_meal([_stay(1)], policy_lookup=_by_item({"accommodation": "500"}))
    assert any(i.code == "PER_NIGHT_OVER_STANDARD" and i.severity == "error"
               for i in issues)


def test_accommodation_within_tolerance_warns():
    issues = check_meal([_stay(1, amount="1080")],  # 540/晚，标准 500，容忍 50
                        policy_lookup=_by_item({"accommodation": "500"}))
    assert any(i.code == "PER_NIGHT_OVER_STANDARD" and i.severity == "warning"
               for i in issues)


def test_accommodation_nights_inferred_from_dates_when_missing():
    """未填 nights → 用 入住/离店 推断。"""
    e = (1, "travel", {"subtype": "accommodation", "city": "北京",
                       "checkin": "2026-06-10", "checkout": "2026-06-12"},
         "2026-06-10", Decimal("1200"))
    issues = check_meal([e], policy_lookup=_by_item({"accommodation": "400"}))
    assert any(i.code == "PER_NIGHT_OVER_STANDARD" for i in issues)


def test_local_transport_daily_total_over_standard():
    """同一日多笔市内交通按日合计与日标准比对（不是逐笔）。"""
    entries = [_local(1, amount="120"), _local(2, amount="150")]
    issues = check_meal(entries, policy_lookup=_by_item({"local_transport_day": "200"}))
    assert any(i.code == "DAILY_TRANSPORT_OVER_STANDARD" and i.severity == "error"
               for i in issues)
    assert issues[0].entry_ids == [1, 2]  # 命中同一天的两笔


def test_local_transport_different_days_not_summed():
    entries = [_local(1, date="2026-06-10", amount="120"),
               _local(2, date="2026-06-11", amount="150")]
    assert check_meal(entries,
                      policy_lookup=_by_item({"local_transport_day": "200"})) == []
