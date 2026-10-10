"""check_trip 行程一致性测试（v1.1 §4.2 行程重构）。

入参：[(entry_id, scene_fields, occurred_on), ...]（只处理差旅项）。
severity 语义：error=阻断 / warning=转人工（spec §4.2「条件固化」转人工分支）。
"""
from invoicing.workflow.validators import check_trip


def _t(eid, **scene):
    return (eid, {"subtype": "transport", **scene}, scene.get("travel_date"))


def _acc(eid, **scene):
    return (eid, {"subtype": "accommodation", **scene}, scene.get("checkin"))


# ---- 返程 ----------------------------------------------------------------

def test_balanced_round_trip_has_no_issues():
    entries = [
        _t(1, transport_mode="高铁", from_city="上海", to_city="北京", travel_date="2026-06-10"),
        _t(2, transport_mode="高铁", from_city="北京", to_city="上海", travel_date="2026-06-12"),
    ]
    assert check_trip(entries) == []


def test_one_way_trip_flags_missing_return():
    entries = [_t(1, transport_mode="飞机", from_city="上海", to_city="深圳",
                  travel_date="2026-06-10")]
    issues = check_trip(entries)
    assert any(i.code == "NO_RETURN_TRIP" and i.severity == "warning" for i in issues)


def test_single_local_transport_does_not_flag_missing_return():
    """只有市内交通（无城际段）不判返程缺失。"""
    entries = [(1, {"subtype": "local_transport", "city": "北京",
                    "travel_date": "2026-06-10"}, "2026-06-10")]
    assert check_trip(entries) == []


# ---- 接续 ----------------------------------------------------------------

def test_route_discontinuity_flagged():
    entries = [
        _t(1, transport_mode="高铁", from_city="上海", to_city="北京", travel_date="2026-06-10"),
        _t(2, transport_mode="高铁", from_city="广州", to_city="上海", travel_date="2026-06-12"),
    ]
    issues = check_trip(entries)
    assert any(i.code == "ROUTE_DISCONTINUOUS" and i.severity == "warning" for i in issues)


def test_route_sorted_by_date_not_input_order():
    """接续判定按行程日期，不看出参顺序。"""
    entries = [
        _t(2, transport_mode="高铁", from_city="北京", to_city="上海", travel_date="2026-06-12"),
        _t(1, transport_mode="高铁", from_city="上海", to_city="北京", travel_date="2026-06-10"),
    ]
    assert check_trip(entries) == []


# ---- 住宿 ----------------------------------------------------------------

def test_accommodation_nights_mismatch_flagged():
    entries = [_acc(5, city="北京", checkin="2026-06-10", checkout="2026-06-12", nights="5")]
    issues = check_trip(entries)
    assert any(i.code == "NIGHTS_MISMATCH" and i.severity == "error" for i in issues)


def test_accommodation_nights_consistent_ok():
    entries = [_acc(5, city="北京", checkin="2026-06-10", checkout="2026-06-12", nights="2")]
    assert check_trip(entries) == []


def test_checkout_before_checkin_flagged():
    entries = [_acc(5, city="北京", checkin="2026-06-12", checkout="2026-06-10")]
    assert any(i.code == "STAY_DATE_INVALID" and i.severity == "error"
               for i in check_trip(entries))


# ---- 边界 ----------------------------------------------------------------

def test_no_travel_entries_no_issues():
    assert check_trip([]) == []
    assert check_trip([(1, {"subtype": "other"}, None)]) == []


def test_unparsable_date_does_not_crash():
    """日期缺失/格式错不炸（数据质量由场景必填校验兜底）。"""
    entries = [
        _t(1, transport_mode="高铁", from_city="上海", to_city="北京", travel_date="不是日期"),
        _acc(2, city="北京", checkin="2026-06-10", checkout="坏日期", nights="1"),
    ]
    check_trip(entries)  # 不抛异常即通过


def test_issue_carries_entry_ids():
    entries = [_t(1, transport_mode="飞机", from_city="上海", to_city="深圳",
                  travel_date="2026-06-10")]
    issues = check_trip(entries)
    assert issues[0].entry_ids == [1]