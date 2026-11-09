"""결정 엔진의 순수 계산부 — 같은 입력이 같은 결론을 내는지 고정한다."""

from __future__ import annotations

import pytest

from app.services.decision import (
    LOCATION_MULT_MAX,
    LOCATION_MULT_MIN,
    OVER_BUDGET_PENALTY,
    PRIORITY_WEIGHTS,
    decide_verdict,
    expected_monthly_sales,
    location_multiplier,
    max_acceptable_rent,
    recommend_score,
    rent_affordability_score,
)
from app.services.decision_narrative import rule_based_narrative
from app.services.decision import RankedCandidate


# --- 입지 보정계수 ---------------------------------------------------------


def test_location_multiplier_is_monotonic_and_clamped():
    top = location_multiplier(0)
    mid = location_multiplier(50)
    bottom = location_multiplier(100)
    assert top > mid > bottom
    assert LOCATION_MULT_MIN <= bottom <= top <= LOCATION_MULT_MAX
    # 상권 평균(중앙값 부근)은 보정이 거의 없어야 한다.
    assert 0.95 <= mid <= 1.10


def test_location_multiplier_top_decile_is_a_premium():
    assert 1.2 <= location_multiplier(10) <= LOCATION_MULT_MAX


def test_location_multiplier_missing_rank_is_neutral():
    assert location_multiplier(None) == 1.0


# --- 예상 매출 -------------------------------------------------------------


def test_expected_sales_anchors_on_benchmark_times_multiplier():
    sales, basis = expected_monthly_sales(
        per_store_monthly_won=40_000_000, trdar_nm="신촌역", integration_top_pct=50
    )
    assert sales == pytest.approx(4000 * location_multiplier(50), rel=1e-3)
    assert "신촌역" in basis and "4,000만원" in basis


def test_expected_sales_is_none_without_benchmark():
    """벤치마크가 없으면 숫자를 지어내지 않는다."""
    sales, basis = expected_monthly_sales(
        per_store_monthly_won=None, trdar_nm="", integration_top_pct=10
    )
    assert sales is None
    assert "없음" in basis


def test_expected_sales_labels_a_dong_fallback_as_dong_not_trdar():
    """scope="dong"은 지정 상권이 아니라 행정동 평균으로 메운 값이다.

    같은 숫자를 "상권"이라고 부르면 화면이 실제보다 더 정밀한 근거인 것처럼
    읽힌다 — 계산값(dong 평균)과 화면 문구(상권)가 어긋나면 안 된다.
    """
    trdar_sales, trdar_basis = expected_monthly_sales(
        per_store_monthly_won=40_000_000,
        trdar_nm="신촌역",
        integration_top_pct=50,
        scope="trdar",
    )
    dong_sales, dong_basis = expected_monthly_sales(
        per_store_monthly_won=40_000_000,
        trdar_nm="연남동",
        integration_top_pct=50,
        scope="dong",
    )
    # 같은 입력이면 계산값 자체는 동일해야 한다 — 달라지는 건 문구뿐.
    assert dong_sales == trdar_sales
    assert "상권" in trdar_basis and "행정동" not in trdar_basis
    assert "행정동" in dong_basis and "상권" not in dong_basis


# --- 적정 월세 -------------------------------------------------------------


def test_max_rent_uses_industry_burden_cap():
    max_rent, detail = max_acceptable_rent(
        expected_sales_man=3000, archetype="food_drink"
    )
    # 음식·음료 부담률 상한 15%
    assert detail["burden_cap_ratio"] == 0.15
    assert max_rent == pytest.approx(3000 * 0.15, rel=1e-3)


def test_max_rent_falls_back_to_break_even_when_that_binds():
    """매출이 낮아 부담률 상한이 손익을 못 맞추면 더 보수적인 값을 쓴다."""
    max_rent, detail = max_acceptable_rent(
        expected_sales_man=700, archetype="food_drink"
    )
    assert detail["binding_constraint"] == "손익분기 역산"
    assert max_rent < 700 * 0.15
    assert max_rent >= 0


def test_max_rent_never_negative():
    max_rent, _ = max_acceptable_rent(expected_sales_man=50, archetype="food_drink")
    assert max_rent == 0.0


def test_max_rent_none_without_expected_sales():
    assert max_acceptable_rent(expected_sales_man=None, archetype="food_drink") == (
        None,
        {},
    )


# --- 월세 여유도 -----------------------------------------------------------


def test_rent_affordability_at_cap_is_midpoint():
    assert rent_affordability_score(500, 500) == 50.0


def test_rent_affordability_rewards_headroom_and_punishes_excess():
    assert rent_affordability_score(400, 500) > 50
    assert rent_affordability_score(600, 500) < 50


def test_rent_affordability_none_when_rent_unknown():
    assert rent_affordability_score(None, 500) is None
    # 상한을 못 구한 경우도 '정보 없음'이다.
    assert rent_affordability_score(300, None) is None


def test_rent_affordability_zero_cap_beats_missing_rent_input():
    """상한 0은 월세를 적었는지와 무관한 '자리의 성질'이다.

    월세를 안 적었다는 이유로 None을 주면 recommend_score가 그 항목을 빼고
    재정규화해서 감점을 피한다. 실제로 연남동(상한 0·월세 미입력) 64.1점이
    망원동(상한 0·월세 0 입력) 50.4점을 이기고, 신촌(상한 221·월세 250)
    53.0점보다도 위로 올라왔다 — 입력칸 하나가 순위를 뒤집었다.
    """
    assert rent_affordability_score(None, 0) == 0.0
    assert rent_affordability_score(0, 0) == 0.0
    # 상한이 정상이고 월세만 없으면 여전히 '정보 없음'이다.
    assert rent_affordability_score(None, 500) is None


def test_rent_affordability_is_zero_when_the_site_cannot_carry_any_rent():
    # 상한 0은 '정보 없음'이 아니라 최악이다. None을 주면 recommend_score가 그
    # 항목을 빼고 재정규화해서 감점을 피하고, 월세를 한 푼도 못 내는 자리가
    # 1위로 올라온다(연남동 상한 0·월세 300 vs 신촌 상한 192·월세 250).
    assert rent_affordability_score(300, 0) == 0.0
    assert rent_affordability_score(300, -10) == 0.0


# --- 추천 점수 -------------------------------------------------------------


def test_priority_weight_tables_each_sum_to_one():
    for name, weights in PRIORITY_WEIGHTS.items():
        assert sum(weights.values()) == pytest.approx(1.0), name


def test_missing_component_renormalizes_instead_of_scoring_zero():
    with_rent, _ = recommend_score(
        site_score_total=80,
        demand_score=80,
        cannibalization_risk=20,
        rent_score=80,
        priority="balanced",
    )
    without_rent, weights = recommend_score(
        site_score_total=80,
        demand_score=80,
        cannibalization_risk=20,
        rent_score=None,
        priority="balanced",
    )
    assert "rent" not in weights
    # 보고용으로 소수 3자리 반올림되므로 그만큼의 오차는 허용.
    assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)
    # 월세 미입력이 점수를 끌어내리면 안 된다 (동일 값이면 동일 점수).
    assert without_rent == pytest.approx(with_rent, abs=0.1)


def test_priority_shifts_the_ranking_lever():
    """경쟁이 심하지만 매출이 좋은 곳은 revenue에서 유리, stability에서 불리."""
    kwargs = dict(site_score_total=80, demand_score=95, rent_score=60)
    revenue, _ = recommend_score(cannibalization_risk=90, priority="revenue", **kwargs)
    stability, _ = recommend_score(
        cannibalization_risk=90, priority="stability", **kwargs
    )
    assert revenue > stability


def test_recommend_score_is_deterministic():
    args = dict(
        site_score_total=77.3,
        demand_score=88.1,
        cannibalization_risk=41.2,
        rent_score=63.4,
        priority="balanced",
    )
    assert recommend_score(**args) == recommend_score(**args)


# --- 판정 -----------------------------------------------------------------


def test_verdict_recommend_requires_score_and_affordable_rent():
    assert (
        decide_verdict(score=80, rent_gap_man=-50, max_rent_man=500, over_budget=False)
        == "recommend"
    )


def test_verdict_downgrades_to_conditional_when_rent_is_over():
    assert (
        decide_verdict(score=80, rent_gap_man=40, max_rent_man=500, over_budget=False)
        == "conditional"
    )


def test_verdict_avoids_on_low_score_or_huge_rent_gap():
    assert (
        decide_verdict(score=30, rent_gap_man=None, max_rent_man=None, over_budget=False)
        == "avoid"
    )
    assert (
        decide_verdict(score=85, rent_gap_man=300, max_rent_man=500, over_budget=False)
        == "avoid"
    )


def test_verdict_holds_in_the_middle():
    assert (
        decide_verdict(score=55, rent_gap_man=None, max_rent_man=None, over_budget=False)
        == "hold"
    )


def test_over_budget_blocks_a_plain_recommend():
    assert (
        decide_verdict(score=90, rent_gap_man=-100, max_rent_man=500, over_budget=True)
        == "conditional"
    )


def test_over_budget_penalty_is_a_real_demotion():
    assert OVER_BUDGET_PENALTY > 0


# --- 규칙 기반 서술 (LLM 없이도 같은 결론) ---------------------------------


def _cand(cid: str, rank: int, score: float, **kw) -> RankedCandidate:
    base = dict(
        id=cid,
        label=f"{cid} 후보",
        rank=rank,
        recommend_score=score,
        verdict="recommend",
        verdict_label="추천",
        expected_monthly_sales_man=3000.0,
        expected_sales_basis="",
        max_acceptable_rent_man=450.0,
        current_rent_man=400.0,
        rent_gap_man=-50.0,
        strengths=["강점1"],
        risks=["위험1"],
        actions=["행동1"],
        evidence={},
    )
    base.update(kw)
    return RankedCandidate(**base)


def test_rule_narrative_states_the_winner_and_a_conditional_threshold():
    ranked = [_cand("A", 1, 82.0), _cand("B", 2, 70.0)]
    headline, note = rule_based_narrative(ranked, "balanced")
    assert "A 후보" in headline
    assert "82" in headline
    # 조건부 결론이 반드시 들어가야 한다.
    assert "450" in note and "B 후보" in note


def test_rule_narrative_flags_an_overpriced_winner():
    ranked = [
        _cand("A", 1, 82.0, current_rent_man=600.0, rent_gap_man=150.0),
        _cand("B", 2, 70.0),
    ]
    headline, _ = rule_based_narrative(ranked, "balanced")
    assert "비싸" in headline


def test_rule_narrative_handles_a_single_candidate():
    headline, note = rule_based_narrative([_cand("A", 1, 82.0)], "balanced")
    assert headline
    assert note == ""


def test_rule_narrative_discloses_a_dong_fallback_sales_benchmark_in_the_headline():
    """decide 엔진이 sales_benchmark.scope를 무시하던 버그 — 헤드라인이
    행정동 평균 추정치를 다른 후보와 똑같은 확정치처럼 말하면 안 된다."""
    ranked = [
        _cand(
            "A",
            1,
            82.0,
            evidence={"sales_benchmark": {"scope": "dong"}},
        ),
        _cand("B", 2, 70.0),
    ]
    headline, _ = rule_based_narrative(ranked, "balanced")
    assert "행정동" in headline

    # scope="trdar"(지정 상권 안)이면 이 캐비어트를 지어내지 않는다.
    ranked_trdar = [
        _cand("A", 1, 82.0, evidence={"sales_benchmark": {"scope": "trdar"}}),
        _cand("B", 2, 70.0),
    ]
    headline_trdar, _ = rule_based_narrative(ranked_trdar, "balanced")
    assert "행정동" not in headline_trdar


def test_rule_narrative_avoids_space_syntax_jargon():
    ranked = [_cand("A", 1, 82.0), _cand("B", 2, 70.0)]
    text = " ".join(rule_based_narrative(ranked, "balanced"))
    for jargon in ("통합도", "Integration", "Choice", "공간구문론"):
        assert jargon not in text


def test_risks_flag_a_site_that_cannot_carry_any_rent():
    # `and max_rent`가 0을 걸러내 가장 나쁜 경우만 위험에서 빠졌다 —
    # 연남동은 상한 0에 월세 300만원인데 월세 위험이 하나도 안 떴다.
    from app.services.decision import build_risks

    risks = build_risks(
        {"rent_gap_man": 300.0, "max_acceptable_rent_man": 0.0, "current_rent_man": 300.0}
    )
    assert any("월세" in r for r in risks), risks

    # 상한이 있는 초과는 기존대로 상한과 초과폭을 보여 준다.
    risks = build_risks(
        {"rent_gap_man": 58.0, "max_acceptable_rent_man": 192.0, "current_rent_man": 250.0}
    )
    assert any("적정 상한" in r and "비쌈" in r for r in risks), risks


def test_risks_flag_a_zero_cap_even_without_a_rent_figure():
    """`and current_rent`가 0/미입력을 falsy로 걸러 상한 0 자체가 사라졌다."""
    from app.services.decision import build_risks

    for current in (None, 0.0):
        risks = build_risks(
            {"max_acceptable_rent_man": 0.0, "current_rent_man": current}
        )
        assert any("여력" in r for r in risks), (current, risks)


def test_risks_flag_a_dong_fallback_sales_benchmark():
    """decide 엔진이 sales_benchmark.scope를 무시하고 확정치로 취급하던 버그.

    scope="dong"은 지정 상권 데이터가 없어 행정동 평균으로 메운 값이다.
    이 값에서 예상 매출·적정 월세·순위·판정이 전부 나오므로, 정밀도가
    낮다는 사실이 위험 목록에서 사라지면 안 된다.
    """
    from app.services.decision import build_risks

    risks = build_risks(
        {
            "expected_monthly_sales_man": 3500.0,
            "sales_benchmark_scope": "dong",
            "max_acceptable_rent_man": 450.0,
            "current_rent_man": 400.0,
            "rent_gap_man": -50.0,
        }
    )
    assert any("행정동" in r for r in risks), risks

    # scope="trdar"(지정 상권 안)이면 이 위험을 지어내지 않는다.
    risks = build_risks(
        {
            "expected_monthly_sales_man": 3500.0,
            "sales_benchmark_scope": "trdar",
            "max_acceptable_rent_man": 450.0,
            "current_rent_man": 400.0,
            "rent_gap_man": -50.0,
        }
    )
    assert not any("행정동" in r for r in risks), risks


def test_strengths_label_dong_fallback_benchmark_as_dong_not_trdar():
    """강점 문구도 화면-계산 불일치를 피해야 한다 — "상권 평균"이라고 쓰면
    행정동 평균으로 추정한 값이 지정 상권 벤치마크처럼 읽힌다."""
    from app.services.decision import build_strengths

    strengths = build_strengths(
        {
            "expected_monthly_sales_man": 4000.0,
            "per_store_monthly_man": 3000.0,
            "sales_benchmark_scope": "dong",
        }
    )
    assert any("행정동" in s for s in strengths), strengths
    assert not any("상권 평균" in s or "상권 점포당" in s for s in strengths), strengths


def test_actions_tell_you_the_rent_is_not_negotiable_when_the_cap_is_zero():
    """상한 0에서는 `if max_rent`가 전부 falsy라 월세 권고가 한 줄도 없었다."""
    from app.services.decision import build_actions

    actions = build_actions(
        {"max_acceptable_rent_man": 0.0, "current_rent_man": 300.0, "rent_gap_man": 300.0},
        "avoid",
    )
    assert any("고정비" in a or "깎아서" in a for a in actions), actions


def test_verdict_never_recommends_a_site_that_cannot_carry_any_rent():
    # 월세를 내고 있는데 상한이 0이면 초과폭이 상한 대비 무한대다.
    assert (
        decide_verdict(score=90, rent_gap_man=200.0, max_rent_man=0.0, over_budget=False)
        == "avoid"
    )
    # 월세를 아직 안 적었어도 '추천'까지 갈 수는 없다.
    assert (
        decide_verdict(score=90, rent_gap_man=None, max_rent_man=0.0, over_budget=False)
        != "recommend"
    )


def test_top_percentile_below_one_is_not_written_as_zero_percent():
    """0.2%를 '상위 0%'로 반올림하면 아무것도 가리키지 않는 말이 된다."""
    from app.services.decision import build_strengths

    strengths = build_strengths({"integration_top_pct": 0.2, "site_total_score": 80.0})
    assert any("상위 1% 이내" in s for s in strengths), strengths
    assert not any("상위 0%" in s for s in strengths), strengths


def test_josa_matches_the_preceding_sound_including_addresses():
    from app.services.decision import josa

    assert josa("신촌", "을", "를") == "을"       # 촌 — 받침 ㄴ
    assert josa("연남동", "을", "를") == "을"     # 동 — 받침 ㅇ
    assert josa("성수", "을", "를") == "를"       # 수 — 받침 없음
    assert josa("홍대", "이", "가") == "가"       # 대 — 받침 없음
    # 지번으로 끝나는 후보 이름은 숫자를 읽어야 조사가 맞는다.
    assert josa("연남동 481-16", "을", "를") == "을"   # 십육 — 받침 ㄱ
    assert josa("연남동 255-29", "을", "를") == "를"   # 이십구 — 받침 없음


def test_rank_drivers_explain_a_gap_that_the_sales_figure_cannot():
    """예상 월매출이 서로 같아도 무엇이 순위를 갈랐는지 말할 수 있어야 한다."""
    from app.services.decision import compute_rank_drivers

    rows = [
        {
            "id": "a",
            "_weights": {"site": 0.35, "demand": 0.25, "competition": 0.2, "rent": 0.2},
            "_facts": {"integration_top_pct": 0.2, "cannibalization_risk": 40.0,
                       "competitor_count": 12},
            "evidence": {"score_components": {"site": 90.0, "demand": 80.0,
                                              "competition": 60.0, "rent": None}},
        },
        {
            "id": "b",
            "_weights": {"site": 0.35, "demand": 0.25, "competition": 0.2, "rent": 0.2},
            "_facts": {"integration_top_pct": 8.0, "cannibalization_risk": 90.0,
                       "competitor_count": 80},
            "evidence": {"score_components": {"site": 60.0, "demand": 80.0,
                                              "competition": 10.0, "rent": None}},
        },
    ]
    compute_rank_drivers(rows)
    a_drivers = rows[0]["evidence"]["rank_drivers"]
    b_drivers = rows[1]["evidence"]["rank_drivers"]
    assert a_drivers and b_drivers
    # 1위는 위로, 2위는 아래로 — 같은 항목이 반대 방향으로 잡힌다.
    assert a_drivers[0]["direction"] == "up"
    assert b_drivers[0]["direction"] == "down"
    # 값이 같은 항목(demand)은 순위를 가른 요인이 아니다.
    assert all(d["key"] != "demand" for d in a_drivers)
    # 사람이 읽을 문장이 붙는다.
    assert all(d["text"] for d in a_drivers)


def test_rank_drivers_explain_a_component_the_field_average_cannot():
    """월세를 적은 후보와 안 적은 후보가 섞이면 평균 차이로는 설명이 안 된다.

    라이브 연남동 사례: 1·2위는 월세 미입력이라 월세 항목이 빠진 채 재정규화되고,
    3~5위는 상한 0이라 월세 0점을 받는다. 월세 점수를 가진 후보끼리는 값이 전부
    0으로 같아서 '후보 평균과의 차이'가 0이 되고, 정작 12점을 가른 요인이
    "차이 없음"으로 나왔다. 이 항목이 내 점수를 얼마나 끌어내렸는지는 말할 수 있다.
    """
    from app.services.decision import compute_rank_drivers

    weights = {"site": 0.35, "demand": 0.25, "competition": 0.2, "rent": 0.2}
    renormalized = {"site": 0.437, "demand": 0.312, "competition": 0.25}
    rows = [
        {   # 월세 미입력 — 월세 항목이 빠진 채 재정규화된다.
            "id": "a",
            "_weights": renormalized,
            "_facts": {},
            "evidence": {"score_components": {"site": 81.0, "demand": 99.0,
                                              "competition": 0.0, "rent": None}},
        },
        {   # 상한 0 — 월세 0점을 받아 점수가 크게 깎인다.
            "id": "b",
            "_weights": weights,
            "_facts": {"max_acceptable_rent_man": 0.0},
            "evidence": {"score_components": {"site": 83.0, "demand": 99.0,
                                              "competition": 0.0, "rent": 0.0}},
        },
        {
            "id": "c",
            "_weights": weights,
            "_facts": {"max_acceptable_rent_man": 0.0},
            "evidence": {"score_components": {"site": 82.0, "demand": 99.0,
                                              "competition": 0.0, "rent": 0.0}},
        },
    ]
    compute_rank_drivers(rows)
    rent_drivers = [
        d for d in rows[1]["evidence"]["rank_drivers"] if d["key"] == "rent"
    ]
    assert rent_drivers, rows[1]["evidence"]["rank_drivers"]
    driver = rent_drivers[0]
    assert driver["direction"] == "down"
    assert "월세 낼 여력 없음" in (driver["detail"] or "")
    # 월세를 안 적은 후보에게는 월세 요인이 잡히지 않는다(채점되지 않았으므로).
    assert all(d["key"] != "rent" for d in rows[0]["evidence"]["rank_drivers"])


def test_rank_drivers_do_not_invent_a_reason_for_a_dead_heat():
    from app.services.decision import compute_rank_drivers

    rows = [
        {
            "id": cid,
            "_weights": {"site": 1.0},
            "_facts": {},
            "evidence": {"score_components": {"site": score}},
        }
        for cid, score in (("a", 70.0), ("b", 69.9))
    ]
    compute_rank_drivers(rows)
    assert rows[0]["evidence"]["rank_drivers"][0]["key"] == "tie"


def test_conditional_conclusion_uses_the_winners_threshold_not_the_runner_ups():
    """LLM에 맡겼더니 2위 상한을 골라 앞뒤가 바뀐 문장이 나왔다."""
    from app.services.decision_narrative import conditional_conclusion

    top = _cand("A", 1, 82.0, label="신촌", max_acceptable_rent_man=394.0)
    second = _cand("B", 2, 70.0, label="연남동", max_acceptable_rent_man=220.0)
    text = conditional_conclusion(top, second)
    assert "394" in text and "220" not in text
    # 조사도 앞말에 맞아야 한다 — "'연남동'를"이 아니라 "'연남동'을".
    assert "'연남동'을" in text


def test_conditional_conclusion_does_not_say_rent_below_zero():
    """상한 0에서 "월세 0만원 이하면 망원동"이라는 문장이 실제로 나왔다."""
    from app.services.decision_narrative import conditional_conclusion

    top = _cand("A", 1, 50.0, label="망원동", max_acceptable_rent_man=0.0)
    second = _cand("B", 2, 40.0, label="강남역", max_acceptable_rent_man=470.0)
    text = conditional_conclusion(top, second)
    assert "0만원 이하" not in text
    assert "여력이 없" in text


def test_rule_narrative_reports_a_zero_cap_instead_of_dropping_it():
    """`if top.max_acceptable_rent_man`이 0을 falsy로 걸러 통째로 빠졌다."""
    ranked = [
        _cand("A", 1, 60.0, max_acceptable_rent_man=0.0, current_rent_man=None,
              rent_gap_man=None),
        _cand("B", 2, 55.0),
    ]
    headline, _ = rule_based_narrative(ranked, "balanced")
    assert "여력" in headline, headline


def test_narrative_guard_catches_a_headline_that_renames_the_verdict():
    """판정이 '비추천'인데 "추천보다 보류 쪽"이라고 쓴 서술이 실제로 나왔다."""
    from app.services.decision_narrative import _contradicts_verdict

    assert _contradicts_verdict("추천보다 보류 쪽에 가깝습니다.", "avoid")
    assert not _contradicts_verdict("현 조건으로는 비추천입니다.", "avoid")
    assert not _contradicts_verdict("조건부 추천입니다.", "conditional")
    assert _contradicts_verdict("보류입니다.", "recommend")
    # '비추천'은 '추천'을 부분문자열로 가진다 — 추천 판정에서 걸러야 한다.
    assert _contradicts_verdict("이 자리는 비추천입니다.", "recommend")


def test_narrative_guard_allows_recommend_as_an_ordinary_verb():
    """맨 '추천'을 판정 주장으로 세면 정상 서술까지 전부 규칙 문장으로 떨어진다.

    판정이 '보류'인데 "월세를 낮춰야 추천할 수 있습니다"는 어긋난 말이 아니다.
    이걸 모순으로 잡는 바람에 라이브 응답이 통째로 rule로 내려갔다.
    """
    from app.services.decision_narrative import _contradicts_verdict

    assert not _contradicts_verdict(
        "월세를 낮춰야 추천할 수 있습니다.", "hold"
    )
    assert not _contradicts_verdict("조건이 맞으면 추천할 만합니다.", "conditional")
    # 그래도 다른 판정어를 대놓고 쓰면 잡는다.
    assert _contradicts_verdict("사실상 비추천에 가깝습니다.", "hold")


def test_rank_driver_particle_matches_the_component_name():
    """"월세 여유이(가) …"처럼 병기가 문장에 새어 나오면 안 된다."""
    from app.services.decision import compute_rank_drivers

    rows = [
        {
            "id": cid,
            "_weights": {"rent": 1.0},
            "_facts": {"max_acceptable_rent_man": 220.0},
            "evidence": {"score_components": {"rent": score}},
        }
        for cid, score in (("a", 90.0), ("b", 10.0))
    ]
    compute_rank_drivers(rows)
    text = rows[0]["evidence"]["rank_drivers"][0]["text"]
    assert "이(가)" not in text, text
    assert text.startswith("월세 여유가"), text


def test_expected_sales_band_needs_two_quarters(monkeypatch):
    """분기가 하나뿐이면 밴드를 만들지 않는다 — 점 하나로 범위를 지어내지 않는다."""
    from app.services import trdar_sales_local as t

    monkeypatch.setattr(t, "_per_store_quarterly_points",
                        lambda cd, pid: [("20254", 5_000_000)])
    assert t.per_store_recent_band("x", "cafe") is None

    monkeypatch.setattr(t, "_per_store_quarterly_points",
                        lambda cd, pid: [("20251", 4_300_000), ("20252", 6_000_000),
                                          ("20253", 5_100_000), ("20254", 5_600_000)])
    band = t.per_store_recent_band("x", "cafe")
    assert (band.low_won, band.high_won, band.quarters) == (4_300_000, 6_000_000, 4)


def test_expected_range_must_contain_the_point_estimate():
    """「예상 월매출 405만원 (113만원~289만원)」 같은 모순 범위는 내보내지 않는다(§9-2).

    decision.py는 범위가 점추정을 담을 때만 expected_range를 채운다 — 그 조건문을 고정한다.
    """
    import inspect

    from app.services import decision

    src = inspect.getsource(decision)
    assert "candidate_range[0] <= expected <= candidate_range[1]" in src
