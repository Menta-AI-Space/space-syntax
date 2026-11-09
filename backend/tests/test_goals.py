"""목표별 엔드포인트의 결정론 계산부 — 같은 입력이 같은 결론을 내는지 고정한다.

I/O(도로망·상권·실거래)는 여기서 건드리지 않는다. 라우터가 모아 온 값을
어떻게 판정으로 바꾸는지, 그 규칙만 검증한다.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.area_search import (
    COMPASS_8,
    DISTRICT_SEARCH_RADIUS_M,
    MAX_CANDIDATES,
    MIN_SPACING_M,
    NEIGHBORHOOD_SEARCH_RADIUS_M,
    bearing_label,
    can_front_a_store,
    disambiguate_labels,
    sample_candidate_spots,
    search_radius_for,
    segment_midpoint,
)
from app.services.diagnosis import (
    ACCESS_HIGH_TOP_PCT,
    CAUSE_PRIORITY,
    COMPETITION_HIGH_RISK,
    DIAGNOSE_BAND_PCT,
    SEVERITY_ORDER,
    TIME_FIT_HIGH_INDEX,
    VERDICT_ON_PAR,
    VERDICT_OVER,
    VERDICT_UNDER,
    DiagnosisSignals,
    build_causes,
    build_diagnosis_strengths,
    core_hour_fit_index,
    core_hours_for,
    diagnose,
    diagnose_verdict_label,
    sales_gap,
)
from app.routers import goals as goals_router
from app.services.lease_feasibility import LeaseMetrics
from app.services.living_pop import LivingPopProfile
from app.services.spatial_query import RoadSegment
from app.services.trdar_sales_local import TrdarMatch, TrdarSalesTrend
from app.services import verdicts
from app.services.verdicts import (
    LEASE_KEY_MONEY_DOWNGRADE_MONTHS,
    adjust_lease_verdict,
    build_lease_actions,
    build_lease_risks,
    build_purchase_risks,
    negotiation_terms,
)


# ===========================================================================
# 1. 계약 판정 조정 (lease-verdict)
# ===========================================================================


def test_lease_verdict_is_unchanged_when_nothing_is_wrong():
    verdict, reasons = adjust_lease_verdict(
        base_verdict="recommend",
        monthly_operating_profit_man=300.0,
        key_money_payback_months=12.0,
    )
    assert verdict == "recommend"
    assert reasons == []


def test_loss_making_lease_is_always_avoid():
    """예상 매출로도 적자면 점수와 무관하게 비추천 — 유일한 하드 게이트."""
    verdict, reasons = adjust_lease_verdict(
        base_verdict="recommend",
        monthly_operating_profit_man=-50.0,
        key_money_payback_months=None,
    )
    assert verdict == "avoid"
    assert reasons and "적자" in reasons[0]


def test_slow_key_money_payback_downgrades_one_step():
    verdict, reasons = adjust_lease_verdict(
        base_verdict="recommend",
        monthly_operating_profit_man=100.0,
        key_money_payback_months=LEASE_KEY_MONEY_DOWNGRADE_MONTHS + 1,
    )
    assert verdict == "conditional"
    assert reasons


def test_key_money_payback_exactly_at_the_limit_is_not_a_downgrade():
    verdict, _ = adjust_lease_verdict(
        base_verdict="recommend",
        monthly_operating_profit_man=100.0,
        key_money_payback_months=float(LEASE_KEY_MONEY_DOWNGRADE_MONTHS),
    )
    assert verdict == "recommend"


def test_lease_adjustments_never_upgrade_a_verdict():
    for base in ("recommend", "conditional", "hold", "avoid"):
        verdict, _ = adjust_lease_verdict(
            base_verdict=base,
            monthly_operating_profit_man=9_999.0,
            key_money_payback_months=1.0,
        )
        assert verdict == base


def test_lease_adjustment_is_deterministic():
    args = dict(
        base_verdict="conditional",
        monthly_operating_profit_man=-1.0,
        key_money_payback_months=99.0,
    )
    assert adjust_lease_verdict(**args) == adjust_lease_verdict(**args)


# --- 협상 조건 -------------------------------------------------------------


def test_negotiation_targets_the_cap_minus_maintenance():
    terms = negotiation_terms(
        monthly_rent_man=520, maintenance_man=30, max_lease_cost_man=400
    )
    assert terms.target_rent_man == 370.0  # 400 상한 − 관리비 30
    assert terms.rent_free_months and 1 <= terms.rent_free_months <= 6
    assert terms.max_key_money_man == 0.0


def test_negotiation_leaves_an_affordable_rent_alone():
    terms = negotiation_terms(
        monthly_rent_man=200, maintenance_man=20, max_lease_cost_man=400
    )
    assert terms.target_rent_man == 200.0
    assert terms.rent_free_months == 0
    # 여유 180만원 × 24개월 회수 기간
    assert terms.max_key_money_man == pytest.approx(180 * 24)


def test_negotiation_without_a_cap_returns_nothing_invented():
    terms = negotiation_terms(
        monthly_rent_man=520, maintenance_man=0, max_lease_cost_man=None
    )
    assert terms.target_rent_man is None
    assert terms.rent_free_months is None
    assert terms.max_key_money_man is None


def _metrics(**kw) -> LeaseMetrics:
    base = dict(
        monthly_fixed_cost_man=900.0,
        labor_cost_man=322.0,
        misc_fixed_man=100.0,
        variable_cost_ratio=0.35,
        break_even_monthly_man=1400.0,
        break_even_daily_man=46.7,
        orders_per_day=103.0,
        rent_burden_pct=30.0,
        appropriate_rent_low_man=150.0,
        appropriate_rent_high_man=260.0,
        rent_excess_man=260.0,
        key_money_payback_months=None,
        recommended_rent_max_man=260.0,
        monthly_operating_profit_man=200.0,
        rent_per_pyeong_man=34.7,
    )
    base.update(kw)
    return LeaseMetrics(**base)


def test_lease_risks_lead_with_the_contract_specific_findings():
    risks = build_lease_risks(
        metrics=_metrics(),
        adjustments=["권리금 회수가 너무 깁니다."],
        base_risks=["입지 위험 A", "입지 위험 B"],
        expected_sales_man=1000.0,
    )
    assert risks[0] == "권리금 회수가 너무 깁니다."
    assert any("손익분기" in r for r in risks)  # 1400 > 1000
    # 계약 고유 항목(조정·손익분기·임차비 초과)이 3개나 앞서 채워져도,
    # 입지 위험(base_risks)이 고정 길이 절단으로 사라지면 안 된다.
    assert "입지 위험 A" in risks
    assert "입지 위험 B" in risks


def test_lease_risks_keep_the_benchmark_scope_caveat_even_when_slots_fill_up():
    """decision.build_risks()가 붙이는 '행정동 평균으로 추정' 캐비어트가,
    계약 고유 위험(적자·권리금 회수 초과·손익분기·임차비 초과)만으로 슬롯이
    꽉 차도 화면에서 사라지면 안 된다."""
    dong_caveat = (
        "예상 매출·적정 월세는 지정 상권이 아니라 행정동 평균으로 추정한 값 "
        "— 실제 상권 매출과 정밀도가 다를 수 있음"
    )
    risks = build_lease_risks(
        metrics=_metrics(),  # break_even=1400>1000, rent_excess_man=260 → 위험 2개 더
        adjustments=["적자 판정 사유", "권리금 회수 초과 사유"],
        base_risks=["경쟁 위험", dong_caveat],
        expected_sales_man=1000.0,
    )
    assert dong_caveat in risks


def test_purchase_risks_keep_data_quality_caveats_alongside_return_risks():
    """수익률·프리미엄·DSCR 위험이 먼저 채워져도 insufficient_sample·
    rent_estimated·낮은 입지점수 캐비어트가 잘려 나가면 안 된다."""
    ev = {
        "cap_rate_pct": 1.0,  # < PURCHASE_CAP_RATE_AVOID_PCT(2.0)
        "price_premium_pct": 30.0,  # >= PURCHASE_PRICE_PREMIUM_OK_PCT(5.0)
        "comparable_count": 4,
        "dscr": 0.8,  # < 1.0
        "loan_rate_pct": 5.0,  # > cap_rate_pct
        "insufficient_sample": True,
        "rent_estimated": True,
        "site_total_score": 40.0,  # < PURCHASE_SITE_SCORE_MIN(55.0)
    }
    risks = build_purchase_risks(ev)
    assert any("보유비용" in r for r in risks)
    assert any("비쌈" in r and "4건" in r for r in risks)
    assert any("DSCR 0.80" in r for r in risks)
    assert any("역레버리지" in r for r in risks)
    assert any("4건뿐" in r for r in risks)
    assert any("추정치" in r for r in risks)
    assert any("입지 종합 40" in r for r in risks)
    assert len(risks) == 7


def test_lease_actions_quote_the_negotiated_target():
    terms = negotiation_terms(
        monthly_rent_man=520, maintenance_man=0, max_lease_cost_man=260
    )
    actions = build_lease_actions(
        terms=terms,
        metrics=_metrics(),
        current_rent_man=520,
        key_money_man=3000,
        base_actions=["입지 권고 A"],
        verdict="conditional",
    )
    assert any("260" in a and "협상" in a for a in actions)
    assert any("권리금" in a for a in actions)
    assert len(actions) <= 5


# ===========================================================================
# 2. 진단 (diagnose)
# ===========================================================================


def test_sales_gap_is_none_without_a_benchmark():
    assert sales_gap(expected_man=None, current_man=2000) == (None, None)


def test_sales_gap_is_signed_and_relative():
    gap_man, gap_pct = sales_gap(expected_man=4000, current_man=2000)
    assert gap_man == -2000.0
    assert gap_pct == -50.0


@pytest.mark.parametrize(
    "gap_pct,expected",
    [
        (-50.0, VERDICT_UNDER),
        (-DIAGNOSE_BAND_PCT, VERDICT_UNDER),
        (-DIAGNOSE_BAND_PCT + 0.1, VERDICT_ON_PAR),
        (0.0, VERDICT_ON_PAR),
        (DIAGNOSE_BAND_PCT - 0.1, VERDICT_ON_PAR),
        (DIAGNOSE_BAND_PCT, VERDICT_OVER),
        (80.0, VERDICT_OVER),
    ],
)
def test_diagnose_verdict_bands_are_symmetric(gap_pct, expected):
    assert diagnose_verdict_label(gap_pct) == expected


def test_diagnose_verdict_without_a_benchmark_refuses_to_compare():
    assert "비교 불가" in diagnose_verdict_label(None)


def test_core_hour_index_is_relative_to_the_daily_average():
    # 12시에만 사람이 몰리고 나머지는 절반인 하루
    hourly = {h: (200.0 if h == 12 else 100.0) for h in range(24)}
    profile = LivingPopProfile(weekday_hourly=hourly)
    peak = core_hour_fit_index(profile, (12,))
    off = core_hour_fit_index(profile, (3,))
    assert peak > 100.0 > off
    # 균등 시간대는 정확히 100
    assert core_hour_fit_index(
        LivingPopProfile(weekday_hourly={h: 50.0 for h in range(24)}), (9, 10)
    ) == 100.0


def test_core_hour_index_is_none_without_data():
    assert core_hour_fit_index(None, (12,)) is None
    assert core_hour_fit_index(LivingPopProfile(), (12,)) is None


def test_core_hours_prefer_the_industry_profile_over_the_archetype():
    assert core_hours_for("cafe", "food_drink") != core_hours_for(
        "restaurant", "food_drink"
    )
    # 모르는 프로파일은 archetype으로 떨어진다
    assert core_hours_for("unknown_profile", "food_drink") == core_hours_for(
        None, "food_drink"
    )


def _bad_signals(**kw) -> DiagnosisSignals:
    base = dict(
        expected_monthly_sales_man=4000.0,
        current_monthly_sales_man=2000.0,
        integration_top_pct=ACCESS_HIGH_TOP_PCT + 10,
        choice_top_pct=80.0,
        cannibalization_risk=COMPETITION_HIGH_RISK + 10,
        competitor_count=12,
        core_hour_index=TIME_FIT_HIGH_INDEX - 10,
        core_hours=(11, 12, 13),
        demand_score=20.0,
        trend_change_pct=-25.0,
        trend_quarters=4,
        monthly_rent_man=600.0,
        rent_burden_cap_pct=15.0,
    )
    base.update(kw)
    return DiagnosisSignals(**base)


def test_every_measured_problem_becomes_a_cause():
    causes = build_causes(_bad_signals())
    assert {c.key for c in causes} == set(CAUSE_PRIORITY)


def test_causes_are_sorted_by_severity_then_fixed_priority():
    causes = build_causes(_bad_signals())
    ranks = [
        (SEVERITY_ORDER[c.severity], CAUSE_PRIORITY.index(c.key)) for c in causes
    ]
    assert ranks == sorted(ranks)


def test_a_clean_location_produces_no_causes():
    signals = DiagnosisSignals(
        expected_monthly_sales_man=4000.0,
        current_monthly_sales_man=4000.0,
        integration_top_pct=5.0,
        choice_top_pct=5.0,
        cannibalization_risk=10.0,
        competitor_count=1,
        core_hour_index=120.0,
        core_hours=(11, 12),
        demand_score=95.0,
        trend_change_pct=8.0,
        trend_quarters=4,
    )
    result = diagnose(signals)
    assert result.causes == []
    assert result.verdict_label == VERDICT_ON_PAR
    assert result.strengths


def test_missing_signals_never_invent_a_cause():
    """None인 신호는 원인이 되지 않는다 — 모르는 것을 문제로 만들지 않는다."""
    causes = build_causes(
        DiagnosisSignals(
            expected_monthly_sales_man=None, current_monthly_sales_man=2000.0
        )
    )
    assert causes == []


def test_cause_findings_quote_the_measured_numbers():
    causes = {c.key: c for c in build_causes(_bad_signals())}
    assert "70" in causes["accessibility"].finding
    assert "12" in causes["competition"].finding
    assert "80" in causes["time_mismatch"].finding
    assert "-25.0" in causes["trend"].finding
    # 600 / 2000 = 30.0%, 상한 15%
    assert "30.0%" in causes["rent_burden"].finding


def test_trend_cause_hedges_when_trdar_match_is_a_nearest_fallback():
    """trdar_for_point가 폴리곤 안이 아니라 300m 이내 최근접 상권으로 대체한
    값(contains=False)이면, "이 상권의 매출이 줄었다"처럼 단정하지 말고
    추정임을 문구에 남겨야 한다.
    """
    in_polygon = build_causes(
        _bad_signals(trdar_nm="정자동", trdar_contains=True)
    )
    fallback = build_causes(
        _bad_signals(
            trdar_nm="정자동", trdar_contains=False, trdar_distance_m=180.0
        )
    )
    in_polygon_finding = {c.key: c for c in in_polygon}["trend"].finding
    fallback_finding = {c.key: c for c in fallback}["trend"].finding

    assert in_polygon_finding != fallback_finding
    assert "180" in fallback_finding
    assert "추정" in fallback_finding
    assert "추정" not in in_polygon_finding


def test_trend_strength_also_hedges_when_trdar_match_is_a_nearest_fallback():
    signals = _bad_signals(
        trend_change_pct=8.0,
        trdar_nm="정자동",
        trdar_contains=False,
        trdar_distance_m=250.0,
    )
    trend_strength = next(
        s for s in build_diagnosis_strengths(signals) if "정자동" in s
    )
    assert "250" in trend_strength
    assert "추정" in trend_strength


def test_diagnosis_facts_expose_whether_trdar_match_was_a_fallback():
    result = diagnose(
        _bad_signals(trdar_nm="정자동", trdar_contains=False, trdar_distance_m=90.0)
    )
    assert result.facts["trdar_contains"] is False
    assert result.facts["trdar_distance_m"] == 90.0


def test_rent_burden_below_the_cap_is_not_a_cause():
    causes = build_causes(
        _bad_signals(monthly_rent_man=100.0, current_monthly_sales_man=2000.0)
    )
    assert "rent_burden" not in {c.key for c in causes}


def test_diagnosis_is_deterministic():
    a = diagnose(_bad_signals())
    b = diagnose(_bad_signals())
    assert [(c.key, c.severity, c.finding) for c in a.causes] == [
        (c.key, c.severity, c.finding) for c in b.causes
    ]
    assert a.verdict_label == b.verdict_label
    assert a.strengths == b.strengths


def test_sales_trend_propagates_the_nearest_fallback_flag(monkeypatch):
    """_sales_trend가 trdar_nm만 뽑아내면 contains/distance_m(최근접 폴백
    여부)가 지워진다 — match 전체를 돌려줘 호출부가 그 신호를 쓸 수 있어야 한다.
    """
    fallback_match = TrdarMatch(
        trdar_cd="TEST01",
        trdar_nm="테스트상권",
        trdar_se_nm="골목상권",
        contains=False,
        distance_m=210.0,
    )
    trend = TrdarSalesTrend(
        trdar_cd="TEST01",
        base_quarter="20241",
        latest_quarter="20242",
        base_per_store_won=1_000_000,
        latest_per_store_won=900_000,
        change_pct=-10.0,
        quarters=2,
    )
    monkeypatch.setattr(
        goals_router, "trdar_for_point", lambda lat, lng: fallback_match
    )
    monkeypatch.setattr(
        goals_router, "sales_trend_for_trdar", lambda trdar_cd, profile_id: trend
    )

    match, got_trend = goals_router._sales_trend(37.5, 127.0, "cafe")

    assert match is fallback_match
    assert match.contains is False
    assert match.distance_m == 210.0
    assert got_trend is trend


def test_sales_trend_is_none_without_a_profile():
    assert goals_router._sales_trend(37.5, 127.0, None) == (None, None)


def test_sales_trend_is_none_when_point_has_no_trdar_match(monkeypatch):
    monkeypatch.setattr(goals_router, "trdar_for_point", lambda lat, lng: None)
    assert goals_router._sales_trend(37.5, 127.0, "cafe") == (None, None)


# ===========================================================================
# 3. 지역 후보 추출 (area-search)
# ===========================================================================


def _seg(osm_id: int, coords, highway="residential") -> RoadSegment:
    return RoadSegment(
        osm_id=osm_id,
        highway=highway,
        length_m=100.0,
        coordinates=coords,
        walkable=True,
    )


def test_search_radius_widens_for_a_district():
    assert search_radius_for("마포구") == DISTRICT_SEARCH_RADIUS_M
    assert search_radius_for("신촌") == NEIGHBORHOOD_SEARCH_RADIUS_M
    assert search_radius_for("") == NEIGHBORHOOD_SEARCH_RADIUS_M


def test_segment_midpoint_is_the_half_length_point():
    lat, lng = segment_midpoint(_seg(1, [[126.90, 37.50], [126.92, 37.50]]))
    assert lat == pytest.approx(37.50)
    assert lng == pytest.approx(126.91, abs=1e-4)


def test_segment_midpoint_handles_degenerate_geometry():
    assert segment_midpoint(_seg(1, [[126.9, 37.5]])) == (37.5, 126.9)
    assert segment_midpoint(_seg(1, [])) == (0.0, 0.0)


def test_footways_and_steps_cannot_host_a_storefront():
    assert not can_front_a_store("footway")
    assert not can_front_a_store("steps")
    assert not can_front_a_store("primary_link")
    # 보행전용 상업가로·이면 골목은 실제 상권이므로 남는다
    assert can_front_a_store("pedestrian")
    assert can_front_a_store("service")
    assert can_front_a_store("residential")
    # 처음 보는 등급은 조용히 버리지 않는다
    assert can_front_a_store("brand_new_class")


def test_spots_are_ranked_by_accessibility_and_spaced_apart():
    # 0.002도 ≈ 220m > 150m 간격 조건
    segments = [
        _seg(10, [[126.900, 37.500], [126.900, 37.5002]]),
        _seg(20, [[126.902, 37.500], [126.902, 37.5002]]),
        _seg(30, [[126.904, 37.500], [126.904, 37.5002]]),
    ]
    integration = {0: 1.0, 1: 5.0, 2: 3.0}
    spots = sample_candidate_spots(segments, integration)
    assert [s.osm_id for s in spots] == [20, 30, 10]
    assert spots[0].integration_top_pct < spots[-1].integration_top_pct


def test_spots_closer_than_the_minimum_spacing_are_deduplicated():
    # 두 세그먼트가 ~11m 떨어져 있음 → 뒤쪽은 버려진다
    segments = [
        _seg(10, [[126.9000, 37.500], [126.9000, 37.5002]]),
        _seg(20, [[126.9001, 37.500], [126.9001, 37.5002]]),
        _seg(30, [[126.9040, 37.500], [126.9040, 37.5002]]),
    ]
    spots = sample_candidate_spots(segments, {0: 9.0, 1: 8.0, 2: 7.0})
    assert [s.osm_id for s in spots] == [10, 30]


def test_non_frontage_segments_are_never_chosen():
    segments = [
        _seg(10, [[126.900, 37.500], [126.900, 37.5002]], highway="footway"),
        _seg(20, [[126.904, 37.500], [126.904, 37.5002]], highway="steps"),
        _seg(30, [[126.908, 37.500], [126.908, 37.5002]], highway="secondary"),
    ]
    spots = sample_candidate_spots(segments, {0: 9.0, 1: 8.0, 2: 1.0})
    assert [s.osm_id for s in spots] == [30]
    # 백분위는 전체 도로망 기준이라 3개 중 꼴찌 = 100%
    assert spots[0].integration_top_pct == 100.0


def test_spot_count_is_capped():
    segments = [
        _seg(i, [[126.900 + i * 0.004, 37.500], [126.900 + i * 0.004, 37.5002]])
        for i in range(12)
    ]
    spots = sample_candidate_spots(segments, {i: float(12 - i) for i in range(12)})
    assert len(spots) == MAX_CANDIDATES


def test_spot_sampling_is_deterministic_even_on_ties():
    segments = [
        _seg(30, [[126.900, 37.500], [126.900, 37.5002]]),
        _seg(10, [[126.904, 37.500], [126.904, 37.5002]]),
        _seg(20, [[126.908, 37.500], [126.908, 37.5002]]),
    ]
    tie = {0: 5.0, 1: 5.0, 2: 5.0}
    first = sample_candidate_spots(segments, tie)
    second = sample_candidate_spots(segments, tie)
    assert [s.osm_id for s in first] == [s.osm_id for s in second] == [10, 20, 30]


def test_no_segments_means_no_spots():
    assert sample_candidate_spots([], {}) == []
    assert sample_candidate_spots([_seg(1, [[126.9, 37.5], [126.9, 37.51]])], {}) == []


def _spot(lat, lng):
    from app.services.area_search import CandidateSpot

    return CandidateSpot(
        lat=lat, lng=lng, osm_id=1, integration_top_pct=1.0, highway="residential"
    )


def test_bearing_label_reads_from_the_search_centre():
    assert bearing_label(37.50, 126.90, 37.51, 126.90) == "북"
    assert bearing_label(37.50, 126.90, 37.49, 126.90) == "남"
    assert bearing_label(37.50, 126.90, 37.50, 126.91) == "동"
    assert bearing_label(37.50, 126.90, 37.50, 126.89) == "서"
    assert bearing_label(37.50, 126.90, 37.51, 126.91) == "북동"
    # 중심과 같은 점은 예외 없이 처리된다
    assert bearing_label(37.50, 126.90, 37.50, 126.90) in COMPASS_8


# ===========================================================================
# 5. POST /api/area-search 응답에 실린 region_label
# ===========================================================================


def test_area_search_response_region_label_matches_the_requested_region(monkeypatch):
    """AreaSearchReport(프론트)가 읽는 result.region_label은 실제로 검색한
    지역명과 같아야 한다 — center.label에만 있고 최상위엔 없으면 화면 칩이
    항상 빈값으로 사라진다 (docs/agent/HANDOFF.md T-115/119/123/125/129/133).
    """
    from app.routers import goals as goals_router
    from app.services.decision import DecisionResult, RankedCandidate

    segments = [_seg(1, [[126.900, 37.500], [126.900, 37.5002]])]
    spots = [_spot(37.500, 126.900), _spot(37.5005, 126.9005)]

    monkeypatch.setattr(goals_router, "fetch_roads_in_radius", lambda *a, **k: segments)
    monkeypatch.setattr(goals_router, "build_graph", lambda segs: (object(), {}))
    monkeypatch.setattr(
        goals_router,
        "compute_depthmapx_metrics",
        lambda graph, segs, modes=(): ({goals_router.WALK: {"integration": {}}}, None),
    )
    monkeypatch.setattr(goals_router, "sample_candidate_spots", lambda *a, **k: spots)

    async def fake_reverse_geocode(lat, lng):
        return f"가짜주소 {lat:.3f},{lng:.3f}"

    monkeypatch.setattr(goals_router, "reverse_geocode", fake_reverse_geocode)

    async def fake_compare_candidates(candidates, **kwargs):
        ranked = [
            RankedCandidate(
                id=c.id,
                label=c.label,
                rank=i + 1,
                recommend_score=90.0 - i,
                verdict="recommend",
                verdict_label="추천",
                expected_monthly_sales_man=1000.0,
                expected_sales_basis="테스트 벤치마크",
                max_acceptable_rent_man=300.0,
                current_rent_man=None,
                rent_gap_man=None,
                strengths=[],
                risks=[],
                actions=[],
                evidence={},
            )
            for i, c in enumerate(candidates)
        ]
        return DecisionResult(ranked=ranked, warnings=[])

    monkeypatch.setattr(goals_router, "compare_candidates", fake_compare_candidates)

    request = goals_router.AreaSearchRequest(
        region_query="망원동", lat=37.500, lng=126.900, radius_m=500
    )
    response = asyncio.run(goals_router.area_search(request))

    # 프론트(AreaSearchReport.tsx)가 읽는 필드와 실제로 검색에 쓰인 지역명이
    # 같은 값이어야 한다 — center.label에만 들어있으면 화면 칩은 항상 undefined.
    assert response.region_label == "망원동"
    assert response.region_label == response.center.label


def test_unique_labels_are_left_untouched():
    labels = ["연세로 50", "명물길 12"]
    spots = [_spot(37.51, 126.90), _spot(37.49, 126.90)]
    assert disambiguate_labels(labels, spots, 37.50, 126.90) == labels


def test_duplicate_labels_get_a_bearing_and_distance():
    """캠퍼스처럼 한 지번이 넓으면 후보가 전부 같은 주소로 나온다."""
    labels = ["연세대학교", "연세대학교"]
    spots = [_spot(37.51, 126.90), _spot(37.49, 126.90)]
    out = disambiguate_labels(labels, spots, 37.50, 126.90)
    assert len(set(out)) == 2
    assert "북측" in out[0] and "남측" in out[1]
    assert all(o.startswith("연세대학교 (") for o in out)


def test_label_disambiguation_is_deterministic():
    labels = ["A", "A", "A"]
    spots = [_spot(37.51, 126.90), _spot(37.49, 126.90), _spot(37.50, 126.91)]
    assert disambiguate_labels(labels, spots, 37.50, 126.90) == disambiguate_labels(
        labels, spots, 37.50, 126.90
    )


def test_min_spacing_is_walkable_distance():
    """간격 기준이 도보 2분 남짓이어야 후보 비교가 의미 있다."""
    assert 100.0 <= MIN_SPACING_M <= 300.0


# ===========================================================================
# 3-1. AreaSearchResponse.region_label — 화면 지역 칩이 실제 검색 지역과 맞아야 한다
# ===========================================================================


def _mock_area_search_pipeline(monkeypatch, *, num_spots: int = 2):
    """도로망·매출 계산은 다른 테스트가 이미 커버한다. 여기서는 그 결과를
    AreaSearchResponse로 조립하는 배관(특히 region_label)만 본다."""
    from app.routers import goals as goals_router
    from app.services.area_search import CandidateSpot
    from app.services.decision import DecisionResult, RankedCandidate

    spots = [
        CandidateSpot(
            lat=37.550 + i * 0.001,
            lng=126.920 + i * 0.001,
            osm_id=i + 1,
            integration_top_pct=10.0 * (i + 1),
            highway="residential",
        )
        for i in range(num_spots)
    ]

    def _fake_extract_spots(lat, lng, search_radius_m, max_candidates):
        return spots, []

    async def _fake_reverse_geocode(lat, lng):
        return f"가짜주소 {lat:.4f},{lng:.4f}"

    async def _fake_compare_candidates(candidates, **_kwargs):
        ranked = [
            RankedCandidate(
                id=c.id,
                label=c.label,
                rank=i + 1,
                recommend_score=90.0 - i * 10.0,
                verdict="good",
                verdict_label="추천",
                expected_monthly_sales_man=1000.0,
                expected_sales_basis="test",
                max_acceptable_rent_man=300.0,
                current_rent_man=None,
                rent_gap_man=None,
                strengths=[],
                risks=[],
                actions=[],
                evidence={},
            )
            for i, c in enumerate(candidates)
        ]
        return DecisionResult(ranked=ranked, warnings=[])

    async def _fake_narrate_or_rule(payload, fallback):
        return fallback, "rule", []

    monkeypatch.setattr(goals_router, "_extract_spots", _fake_extract_spots)
    monkeypatch.setattr(goals_router, "reverse_geocode", _fake_reverse_geocode)
    monkeypatch.setattr(goals_router, "compare_candidates", _fake_compare_candidates)
    monkeypatch.setattr(goals_router, "_narrate_or_rule", _fake_narrate_or_rule)
    return goals_router


def test_area_search_response_region_label_matches_the_requested_region(monkeypatch):
    goals_router = _mock_area_search_pipeline(monkeypatch)
    request = goals_router.AreaSearchRequest(
        region_query="합정동", lat=37.550, lng=126.920
    )

    response = asyncio.run(goals_router.area_search(request))

    # 칩에 쓰는 값이 실제로 검색에 쓰인 지역과 같아야 한다 — 비어 있거나
    # 검색과 무관한 값이면 화면과 실제 계산이 어긋난다.
    assert response.region_label == "합정동"
    assert response.region_label == response.center.label
    assert len(response.candidates) == 2


def test_area_search_response_region_label_falls_back_when_no_region_query(
    monkeypatch,
):
    goals_router = _mock_area_search_pipeline(monkeypatch)
    request = goals_router.AreaSearchRequest(region_query="", lat=37.550, lng=126.920)

    response = asyncio.run(goals_router.area_search(request))

    # 지역명 없이 좌표만 준 경우에도 칩이 비어 있으면 안 되고, center.label과
    # 같은 값(같은 소스)이어야 한다 — 지어낸 값이 아니라 실제로 쓰인 값이다.
    assert response.region_label
    assert response.region_label == response.center.label


# ===========================================================================
# 4. 서술 레이어 — LLM은 결론을 바꾸지 못한다
# ===========================================================================


def test_narration_falls_back_to_the_rule_sentence_when_the_llm_is_off(
    monkeypatch,
):
    async def _off(**_kwargs):
        return None

    monkeypatch.setattr(verdicts, "complete_json", _off)
    headline, source = asyncio.run(verdicts.narrate({"판정": "추천"}, "규칙 문장"))
    assert headline == "규칙 문장"
    assert source == "rule"


def test_narration_uses_the_llm_sentence_when_available(monkeypatch):
    async def _on(**_kwargs):
        return {"headline": "LLM이 쓴 문장"}

    monkeypatch.setattr(verdicts, "complete_json", _on)
    headline, source = asyncio.run(verdicts.narrate({"판정": "추천"}, "규칙 문장"))
    assert headline == "LLM이 쓴 문장"
    assert source == "llm"


def test_an_empty_llm_answer_is_treated_as_a_failure(monkeypatch):
    async def _blank(**_kwargs):
        return {"headline": "   "}

    monkeypatch.setattr(verdicts, "complete_json", _blank)
    headline, source = asyncio.run(verdicts.narrate({}, "규칙 문장"))
    assert (headline, source) == ("규칙 문장", "rule")


# ===========================================================================
# 5. 지역 후보 검색 응답 — region_label이 실제 조회 지역과 일치해야 한다
# ===========================================================================


def test_area_search_response_region_label_matches_the_query(monkeypatch):
    """AreaSearchReport의 지역 칩은 response.region_label을 읽는다 — 필드가 없으면
    항상 빈칸으로 보인다. 값이 요청에 쓴 지역과 실제로 같은지까지 확인한다."""
    from app.routers import goals as goals_router
    from app.services.area_search import CandidateSpot
    from app.services.decision import DecisionResult, RankedCandidate

    segments = [_seg(1, [[126.900, 37.500], [126.900, 37.5002]])]
    monkeypatch.setattr(goals_router, "fetch_roads_in_radius", lambda *a, **k: segments)
    monkeypatch.setattr(
        goals_router, "cap_analysis_segments", lambda segs, *a, **k: (segs, len(segs))
    )
    monkeypatch.setattr(goals_router, "build_graph", lambda segs: (object(), {}))
    # 이 스텁은 "엔진이 꺼져 있어 Python 폴백으로 간다"를 흉내낸다. 실행 환경의
    # .env가 DEPTHMAPX_ENABLED=true면 strict 503이 먼저 터져 폴백에 못 가므로,
    # 설정도 같이 꺼야 어느 기계에서 돌려도 같은 경로를 탄다.
    monkeypatch.setattr(goals_router.settings, "depthmapx_enabled", False)
    monkeypatch.setattr(
        goals_router, "compute_depthmapx_metrics", lambda *a, **k: (None, "disabled")
    )
    monkeypatch.setattr(
        goals_router,
        "compute_metrics",
        lambda *a, **k: ({goals_router.WALK: {"integration": {0: 1.0}}}, []),
    )
    spot = CandidateSpot(
        lat=37.5001, lng=126.900, osm_id=1, integration_top_pct=1.0, highway="residential"
    )
    monkeypatch.setattr(goals_router, "sample_candidate_spots", lambda *a, **k: [spot])

    async def fake_geocode_address(_query):
        return 37.5, 126.9

    monkeypatch.setattr(goals_router, "geocode_address", fake_geocode_address)

    async def fake_reverse_geocode(_lat, _lng):
        return "연희로 12"

    monkeypatch.setattr(goals_router, "reverse_geocode", fake_reverse_geocode)

    async def fake_compare_candidates(candidates, **_kwargs):
        c = candidates[0]
        return DecisionResult(
            ranked=[
                RankedCandidate(
                    id=c.id,
                    label=c.label,
                    rank=1,
                    recommend_score=80.0,
                    verdict="recommend",
                    verdict_label="추천",
                    expected_monthly_sales_man=1000.0,
                    expected_sales_basis="상권 실거래 기반",
                    max_acceptable_rent_man=300.0,
                    current_rent_man=None,
                    rent_gap_man=None,
                    strengths=[],
                    risks=[],
                    actions=[],
                    evidence={},
                )
            ]
        )

    monkeypatch.setattr(goals_router, "compare_candidates", fake_compare_candidates)

    async def fake_narrate_or_rule(_payload, fallback):
        return fallback, "rule", []

    monkeypatch.setattr(goals_router, "_narrate_or_rule", fake_narrate_or_rule)

    request = goals_router.AreaSearchRequest(region_query="연남동", radius_m=500)
    response = asyncio.run(goals_router.area_search(request))

    assert response.region_label == "연남동"
    # center.label과도 같은 값이어야 한다 — 두 필드가 서로 다른 지역을 가리키면 안 된다.
    assert response.center.label == response.region_label
    assert "연남동" in response.headline


def test_area_search_region_label_falls_back_to_a_placeholder_for_point_lookups(
    monkeypatch,
):
    """region_query 없이 좌표로만 검색하면 region_label을 지어내지 않고
    고정 문구를 쓴다 — 없는 지명을 만들어내지 않는다는 원칙."""
    from app.routers import goals as goals_router
    from app.services.area_search import CandidateSpot
    from app.services.decision import DecisionResult, RankedCandidate

    segments = [_seg(1, [[126.900, 37.500], [126.900, 37.5002]])]
    monkeypatch.setattr(goals_router, "fetch_roads_in_radius", lambda *a, **k: segments)
    monkeypatch.setattr(
        goals_router, "cap_analysis_segments", lambda segs, *a, **k: (segs, len(segs))
    )
    monkeypatch.setattr(goals_router, "build_graph", lambda segs: (object(), {}))
    # 이 스텁은 "엔진이 꺼져 있어 Python 폴백으로 간다"를 흉내낸다. 실행 환경의
    # .env가 DEPTHMAPX_ENABLED=true면 strict 503이 먼저 터져 폴백에 못 가므로,
    # 설정도 같이 꺼야 어느 기계에서 돌려도 같은 경로를 탄다.
    monkeypatch.setattr(goals_router.settings, "depthmapx_enabled", False)
    monkeypatch.setattr(
        goals_router, "compute_depthmapx_metrics", lambda *a, **k: (None, "disabled")
    )
    monkeypatch.setattr(
        goals_router,
        "compute_metrics",
        lambda *a, **k: ({goals_router.WALK: {"integration": {0: 1.0}}}, []),
    )
    spot = CandidateSpot(
        lat=37.5001, lng=126.900, osm_id=1, integration_top_pct=1.0, highway="residential"
    )
    monkeypatch.setattr(goals_router, "sample_candidate_spots", lambda *a, **k: [spot])

    async def fake_reverse_geocode(_lat, _lng):
        return "연희로 12"

    monkeypatch.setattr(goals_router, "reverse_geocode", fake_reverse_geocode)

    async def fake_compare_candidates(candidates, **_kwargs):
        c = candidates[0]
        return DecisionResult(
            ranked=[
                RankedCandidate(
                    id=c.id,
                    label=c.label,
                    rank=1,
                    recommend_score=80.0,
                    verdict="recommend",
                    verdict_label="추천",
                    expected_monthly_sales_man=1000.0,
                    expected_sales_basis="상권 실거래 기반",
                    max_acceptable_rent_man=300.0,
                    current_rent_man=None,
                    rent_gap_man=None,
                    strengths=[],
                    risks=[],
                    actions=[],
                    evidence={},
                )
            ]
        )

    monkeypatch.setattr(goals_router, "compare_candidates", fake_compare_candidates)

    async def fake_narrate_or_rule(_payload, fallback):
        return fallback, "rule", []

    monkeypatch.setattr(goals_router, "_narrate_or_rule", fake_narrate_or_rule)

    request = goals_router.AreaSearchRequest(lat=37.5, lng=126.9, radius_m=500)
    response = asyncio.run(goals_router.area_search(request))

    assert response.region_label == "선택 지점"
    assert response.center.label == response.region_label


def test_the_narration_payload_carries_no_authority_over_the_verdict():
    """서술에 넘기는 값은 이미 결정된 결과여야 한다 — 판정 함수는 LLM을 안 본다."""
    import inspect

    from app.services import decision, diagnosis

    for fn in (
        decision.decide_verdict,
        decision.recommend_score,
        decision.max_acceptable_rent,
        verdicts.decide_purchase_verdict,
        verdicts.adjust_lease_verdict,
        verdicts.negotiation_terms,
        diagnosis.build_causes,
        diagnosis.diagnose_verdict_label,
    ):
        source = inspect.getsource(fn)
        assert "llm" not in source.lower()
        assert "complete_json" not in source


# ===========================================================================
# 6. POST /api/area-search — region_label은 실제로 검색한 지역을 반영해야 한다
# ===========================================================================


def test_area_search_response_carries_the_actual_region_label(monkeypatch):
    """AreaSearchResponse.region_label이 응답에 실려야 한다 — 전에는 필드
    자체가 스키마에 없어 프론트(AreaSearchReport)의 지역 칩이 항상 빈값이었다.
    값이 그냥 있는 게 아니라 요청에 쓴 region_query와 같아야 한다."""
    from app.routers import goals as goals_router
    from app.services.area_search import CandidateSpot
    from app.services.decision import DecisionResult, RankedCandidate

    spot = CandidateSpot(
        lat=37.556, lng=126.924, osm_id=1, integration_top_pct=5.0, highway="residential"
    )
    monkeypatch.setattr(goals_router, "_extract_spots", lambda *a, **k: ([spot], []))

    async def _fake_reverse_geocode(_lat, _lng):
        return "서울 마포구 합정동 123-4"

    monkeypatch.setattr(goals_router, "reverse_geocode", _fake_reverse_geocode)

    ranked = RankedCandidate(
        id="spot1",
        label="서울 마포구 합정동 123-4",
        rank=1,
        recommend_score=82.0,
        verdict="recommend",
        verdict_label="추천",
        expected_monthly_sales_man=1200.0,
        expected_sales_basis="benchmark",
        max_acceptable_rent_man=300.0,
        current_rent_man=None,
        rent_gap_man=None,
        strengths=[],
        risks=[],
        actions=[],
        evidence={},
    )

    async def _fake_compare_candidates(*_args, **_kwargs):
        return DecisionResult(ranked=[ranked])

    monkeypatch.setattr(goals_router, "compare_candidates", _fake_compare_candidates)

    request = goals_router.AreaSearchRequest(
        region_query="합정동", lat=37.556, lng=126.924, radius_m=500
    )
    response = asyncio.run(goals_router.area_search(request))

    assert response.region_label == "합정동"
    # center.label과도 같아야 한다 — 화면 두 곳(칩, 미니맵 중심)이 다른 지역명을
    # 보여주면 안 된다.
    assert response.region_label == response.center.label


def test_area_search_falls_back_to_a_generic_label_without_a_region_query(monkeypatch):
    """lat/lng만 주고 region_query가 없는 검색도 빈 칩이 아니라 값을 낸다."""
    from app.routers import goals as goals_router
    from app.services.area_search import CandidateSpot
    from app.services.decision import DecisionResult, RankedCandidate

    spot = CandidateSpot(
        lat=37.556, lng=126.924, osm_id=1, integration_top_pct=5.0, highway="residential"
    )
    monkeypatch.setattr(goals_router, "_extract_spots", lambda *a, **k: ([spot], []))

    async def _fake_reverse_geocode(_lat, _lng):
        return "서울 마포구 합정동 123-4"

    monkeypatch.setattr(goals_router, "reverse_geocode", _fake_reverse_geocode)

    ranked = RankedCandidate(
        id="spot1",
        label="서울 마포구 합정동 123-4",
        rank=1,
        recommend_score=82.0,
        verdict="recommend",
        verdict_label="추천",
        expected_monthly_sales_man=1200.0,
        expected_sales_basis="benchmark",
        max_acceptable_rent_man=300.0,
        current_rent_man=None,
        rent_gap_man=None,
        strengths=[],
        risks=[],
        actions=[],
        evidence={},
    )

    async def _fake_compare_candidates(*_args, **_kwargs):
        return DecisionResult(ranked=[ranked])

    monkeypatch.setattr(goals_router, "compare_candidates", _fake_compare_candidates)

    request = goals_router.AreaSearchRequest(lat=37.556, lng=126.924, radius_m=500)
    response = asyncio.run(goals_router.area_search(request))

    assert response.region_label == "선택 지점"
    assert response.region_label == response.center.label
