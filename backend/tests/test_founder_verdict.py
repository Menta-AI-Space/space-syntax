"""창업자 결정 카드 — 소유자가 10/24에 고정한 문턱을 그대로 지키는지."""

import pytest

from app.services.founder_verdict import CONDITIONAL, GO, NO, founder_verdict

# 사람 흐름 대표값: 좋음(상위 10/15%) · 보통(30/35%) · 약함(60/60%)
FLOW = {"good": (10, 15), "ok": (30, 35), "weak": (60, 60)}
COMP = {"low": "보통", "high": "높음", "very_high": "매우 높음"}

TABLE = {
    ("good", "low"): GO,
    ("good", "high"): CONDITIONAL,
    ("good", "very_high"): CONDITIONAL,
    ("ok", "low"): GO,
    ("ok", "high"): CONDITIONAL,
    ("ok", "very_high"): NO,
    ("weak", "low"): CONDITIONAL,
    ("weak", "high"): NO,
    ("weak", "very_high"): NO,
}


def _run(flow="good", comp="low", cap=300.0, rent=None, intensity=None):
    integ, choice = FLOW[flow]
    return founder_verdict(
        integration_top_pct=integ,
        choice_top_pct=choice,
        competition_intensity=intensity if intensity is not None else COMP[comp],
        same_category_count=12,
        rent_cap_man=cap,
        monthly_rent_man=rent,
    )


@pytest.mark.parametrize(("cell", "expected"), list(TABLE.items()))
def test_table_cells(cell, expected):
    assert _run(*cell).signal == expected


def test_flow_good_needs_both_measures():
    # Integration은 상위 10%여도 Choice가 30%면 「좋음」이 아니라 「보통」이다.
    v = founder_verdict(
        integration_top_pct=10, choice_top_pct=30, competition_intensity="높음",
        same_category_count=5, rent_cap_man=300, monthly_rent_man=None,
    )
    assert v.flow == "ok"


def test_rent_within_cap_keeps_signal():
    v = _run("good", "low", cap=300, rent=300)
    assert v.signal == GO
    assert "300만원" in v.rent_line


def test_rent_over_within_20pct_drops_one_step():
    assert _run("good", "low", cap=300, rent=360).signal == CONDITIONAL
    assert _run("good", "high", cap=300, rent=330).signal == NO


def test_rent_over_more_than_20pct_is_no():
    assert _run("good", "low", cap=300, rent=361).signal == NO


def test_no_rent_given_states_cap():
    v = _run("good", "low", cap=284.4)
    assert v.signal == GO
    assert v.rent_cap_man == 284
    assert "284만원 이하면" in v.rent_line


def test_missing_sales_lowers_go_to_conditional():
    v = _run("good", "low", cap=None)
    assert v.signal == CONDITIONAL
    assert v.rent_cap_man is None
    # 이미 🟡·🔴인 것은 그대로다.
    assert _run("weak", "high", cap=None).signal == NO


def test_cap_zero_is_no():
    assert _run("good", "low", cap=0).signal == NO


def test_unknown_competition_is_flagged_not_called_low():
    v = founder_verdict(
        integration_top_pct=10, choice_top_pct=15, competition_intensity=None,
        same_category_count=None, rent_cap_man=300, monthly_rent_man=None,
    )
    assert v.competition_label == "확인 못 함"
    assert v.cautions
