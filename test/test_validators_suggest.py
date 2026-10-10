"""suggest_claims_for_invoice 补录归属建议测试（v1.1 §5.3 发票补录 Skill）。

入参：invoice(issue_date, expense_type) + claims_with_entries(claim_type, entry_dates)。
日期接近度是**门控**条件（超窗口直接排除），类型与区间命中给分。
"""
from datetime import date
from types import SimpleNamespace

from invoicing.workflow.validators import suggest_claims_for_invoice


def _claim(cid, no, ctype, dates):
    return SimpleNamespace(id=cid, claim_no=no, claim_type=ctype, entry_dates=dates)


def _inv(d, etype="travel"):
    return SimpleNamespace(issue_date=d, expense_type=etype)


def test_type_and_date_match_ranks_first():
    inv = _inv(date(2026, 6, 11), "travel")
    claims = [
        _claim(1, "FY-1", "travel", [date(2026, 6, 10), date(2026, 6, 12)]),
        _claim(2, "FY-2", "office", [date(2026, 6, 11)]),
    ]
    out = suggest_claims_for_invoice(inv, claims)
    assert out and out[0].claim_id == 1
    assert any("类型" in r for r in out[0].reasons)


def test_no_date_overlap_beyond_window_excluded():
    """类型匹配但日期离太远 → 不推荐（防误挂到无关单据）。"""
    inv = _inv(date(2026, 6, 30), "travel")
    claims = [_claim(1, "FY-1", "travel", [date(2026, 6, 1)])]
    assert suggest_claims_for_invoice(inv, claims) == []


def test_within_window_but_outside_range_still_candidate():
    inv = _inv(date(2026, 6, 15), "travel")
    claims = [_claim(1, "FY-1", "travel", [date(2026, 6, 10), date(2026, 6, 12)])]
    out = suggest_claims_for_invoice(inv, claims)
    assert len(out) == 1
    assert 0 < out[0].score < 6  # 命中窗口但不在区间内 → 分值低于"区间内"档


def test_inside_range_scores_higher_than_window_only():
    inv = _inv(date(2026, 6, 11), "travel")
    inside = _claim(1, "FY-1", "travel", [date(2026, 6, 10), date(2026, 6, 12)])
    # 距区间 6 天（窗口内但不在区间）
    window_only = _claim(2, "FY-2", "travel", [date(2026, 6, 4), date(2026, 6, 5)])
    out = suggest_claims_for_invoice(inv, [window_only, inside])
    assert [s.claim_id for s in out] == [1, 2]


def test_returns_at_most_three_sorted_by_score():
    inv = _inv(date(2026, 6, 11), "travel")
    claims = [_claim(i, f"FY-{i}", "travel", [date(2026, 6, 11)]) for i in range(1, 6)]
    assert len(suggest_claims_for_invoice(inv, claims)) == 3


def test_invoice_without_date_returns_empty():
    inv = _inv(None, "travel")
    claims = [_claim(1, "FY-1", "travel", [date(2026, 6, 10)])]
    assert suggest_claims_for_invoice(inv, claims) == []


def test_claim_without_entries_still_candidate_on_type():
    """草稿单还没建事项：类型匹配即可作为候选（+3），供补录时挂入。"""
    inv = _inv(date(2026, 6, 11), "travel")
    claims = [_claim(9, "FY-9", "travel", [])]
    out = suggest_claims_for_invoice(inv, claims)
    assert len(out) == 1 and out[0].claim_id == 9


def test_no_type_match_no_date_overlap_returns_empty():
    inv = _inv(date(2026, 6, 11), "office")
    claims = [_claim(1, "FY-1", "travel", [date(2026, 6, 1)])]
    assert suggest_claims_for_invoice(inv, claims) == []


def test_reasons_are_human_readable():
    inv = _inv(date(2026, 6, 11), "travel")
    claims = [_claim(1, "FY-1", "travel", [date(2026, 6, 10), date(2026, 6, 12)])]
    reasons = suggest_claims_for_invoice(inv, claims)[0].reasons
    assert reasons and all(isinstance(r, str) and r for r in reasons)