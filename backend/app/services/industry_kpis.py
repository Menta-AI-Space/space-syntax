"""Industry-profile KPI computation (Phase 1 pilot: cafe).

Bridges the gap between the adaptive-UI industry profiles
(backend/industry-profiles/**/*.json, ui.summaryKpis) and the analysis
engine: each KPI key maps to a compute function that derives a 0–100
score from data the site-score pipeline already gathers (radius-scaled
demand, SBIZ competition, space-syntax ranks, transit, corner geometry).

Keys without an implementation yield score=None so the UI keeps its
honest "데이터 없음" state instead of substituting zeros.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from app.services.site_score import (
    competition_density_per_km2,
    competition_intensity_label,
    score_qualitative_label,
    transit_score,
)


@dataclass
class KpiInputs:
    """Snapshot of the per-request analysis data KPI functions may use."""

    # Demand (radius-scaled, from SGIS)
    population: int | None = None
    workers: int | None = None
    households: int | None = None
    female_pct: float | None = None
    age_band_pcts: dict[str, float] = field(default_factory=dict)
    # Space-syntax ranks within the analysis radius (top % — lower is better)
    integration_top_pct: float | None = None
    choice_top_pct: float | None = None
    connectivity_top_pct: float | None = None
    junction_degree: int = 0
    is_corner: bool = False
    # Transit
    nearest_station_m: float | None = None
    # Nearest active elementary/middle/high school (schools table)
    nearest_school_m: float | None = None
    # Living-population time slots (Seoul 250m grid, local PostGIS)
    late_night_pop: float | None = None
    weekend_prime_pop: float | None = None
    morning_pop: float | None = None
    lunch_pop: float | None = None
    dinner_pop: float | None = None
    living_weekday_days: int = 0
    living_weekend_days: int = 0
    # HIRA 의원·병원 supply (weekend counts = 신고분 lower bound, ~7% coverage)
    clinic_count_hira: int | None = None
    clinic_sat_open: int | None = None
    clinic_sun_open: int | None = None
    # 환경부 전기차 충전기 (반경 내 전체/급속 — 테이블 미적재 시 None)
    ev_charger_count: int | None = None
    ev_fast_charger_count: int | None = None
    # 시군구 EV 충전 수요 (환경부 합성데이터 2024 집계 — 연간 세션 + 전국 백분위)
    ev_sgg_sessions: int | None = None
    ev_sgg_demand_pct: float | None = None
    # 오피넷 유가 (gas_station 요청 시에만 조회)
    fuel_local_avg_won: float | None = None
    fuel_sido_avg_won: float | None = None
    fuel_station_count: int | None = None
    # 상권 추정매출 — 이 상권 업종 점포당 월매출 vs 서울 전체 중앙값 (원)
    trdar_per_store_won: int | None = None
    citywide_per_store_won: int | None = None
    # 단기체류 외국인 비중 (체류 총량 대비 %) — 관광상권 주석용
    foreign_share_pct: float | None = None
    # Franchise brand context (request.brand + SBIZ 상호명 + 상권 점포 데이터)
    brand_label: str = ""
    own_brand_count_in_radius: int | None = None
    nearest_own_brand_m: float | None = None
    own_brand_seoul_count: int | None = None
    trdar_store_count: int | None = None
    trdar_franchise_count: int | None = None
    # Same-category competition (walk-5min isochrone when available)
    # total_store_count == 0 means the SBIZ store fetch failed or returned
    # nothing — competition KPIs must go None instead of claiming "no rivals"
    total_store_count: int = 0
    same_category_count: int = 0
    competition_area_m2: float | None = None
    min_same_category_distance_m: float | None = None
    radius_m: int = 500
    match_method: str = "disabled"


@dataclass
class IndustryKpi:
    key: str
    label: str
    score: float | None
    display: str
    detail: str


def _saturating(value: int, scale: float) -> float:
    """0–100 with diminishing returns; ~63 at value==scale (same family as demand_score)."""
    return 100.0 * (1 - math.exp(-value / scale))


def _blend(components: list[tuple[float | None, float]]) -> float | None:
    """Weighted mean over available components, renormalizing weights."""
    total = 0.0
    weight = 0.0
    for value, w in components:
        if value is None:
            continue
        total += value * w
        weight += w
    if weight <= 0:
        return None
    return round(total / weight, 1)


def _kpi_takeout_demand(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """출근시간 실측 체류 + 직장인 수요 + 통행축 + 역세권 → 출근길/점심 테이크아웃 수요."""
    morning_comp = (
        _saturating(int(inputs.morning_pop), 20_000)
        if inputs.morning_pop is not None
        else None
    )
    workers_comp = (
        _saturating(inputs.workers, 10_000) if inputs.workers is not None else None
    )
    choice_comp = (
        100.0 - inputs.choice_top_pct if inputs.choice_top_pct is not None else None
    )
    transit_comp = transit_score(inputs.nearest_station_m)
    score = _blend(
        [(morning_comp, 0.3), (workers_comp, 0.35), (choice_comp, 0.2), (transit_comp, 0.15)]
    )
    if score is None:
        return None, "—", "종사자·통행 데이터 없음"
    parts = []
    if inputs.morning_pop is not None:
        parts.append(f"출근시간(7–9시) 체류 {inputs.morning_pop:,.0f}명")
    if inputs.workers is not None:
        parts.append(f"반경 {inputs.radius_m}m 종사자 {inputs.workers:,}명")
    if inputs.choice_top_pct is not None:
        parts.append(f"통행축 상위 {inputs.choice_top_pct}%")
    if inputs.nearest_station_m is not None:
        parts.append(f"역까지 {inputs.nearest_station_m:.0f}m")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_stay_demand(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """거주 인구 + 20–30대 비중 + 접근성 → 체류(카공·모임) 수요."""
    pop_comp = (
        _saturating(inputs.population, 20_000)
        if inputs.population is not None
        else None
    )
    young_pct = inputs.age_band_pcts.get("20대", 0.0) + inputs.age_band_pcts.get(
        "30대", 0.0
    )
    young_comp = min(100.0, young_pct * 2.5) if inputs.age_band_pcts else None
    integration_comp = (
        100.0 - inputs.integration_top_pct
        if inputs.integration_top_pct is not None
        else None
    )
    score = _blend([(pop_comp, 0.5), (young_comp, 0.3), (integration_comp, 0.2)])
    if score is None:
        return None, "—", "거주 인구 데이터 없음"
    parts = []
    if inputs.population is not None:
        parts.append(f"반경 {inputs.radius_m}m 거주 {inputs.population:,}명")
    if inputs.age_band_pcts:
        parts.append(f"20–30대 {young_pct:.0f}%")
    if inputs.integration_top_pct is not None:
        parts.append(f"접근성 상위 {inputs.integration_top_pct}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_cafe_saturation(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """도보 5분 도달권 내 동일업종 밀도 → 공급 포화도 (높을수록 포화)."""
    if inputs.match_method == "disabled":
        return None, "—", "업종 미선택 — 동일업종 집계 안 함"
    if inputs.total_store_count == 0:
        return None, "—", "상가(상권)정보 조회 실패 — 경쟁 지표 계산 불가"
    density = competition_density_per_km2(
        inputs.same_category_count,
        radius_m=inputs.radius_m if inputs.competition_area_m2 is None else None,
        area_m2=inputs.competition_area_m2,
    )
    score = round(min(100.0, density * 1.25), 1)
    detail = (
        f"도보 5분 내 동일업종 {inputs.same_category_count}곳 · km²당 {density:.1f}곳"
    )
    return score, competition_intensity_label(density), detail


def _kpi_visibility(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """연결 갈래 순위 + 코너 여부 → 가시성."""
    conn_comp = (
        100.0 - inputs.connectivity_top_pct
        if inputs.connectivity_top_pct is not None
        else None
    )
    corner_comp = 100.0 if inputs.is_corner else 40.0
    score = _blend([(conn_comp, 0.7), (corner_comp, 0.3)])
    if score is None:
        return None, "—", "도로 연결 데이터 없음"
    corner_text = f"코너 입지 (교차 {inputs.junction_degree}갈래)" if inputs.is_corner else "일반 입지"
    detail_parts = [corner_text]
    if inputs.connectivity_top_pct is not None:
        detail_parts.insert(0, f"연결성 상위 {inputs.connectivity_top_pct}%")
    return score, score_qualitative_label(score), " · ".join(detail_parts)


def _kpi_saturation(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """Alias of cafe_saturation semantics for other verticals' supply/pressure keys."""
    return _kpi_cafe_saturation(inputs)


def _kpi_lunch_demand(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """점심시간 실측 체류 + 직장인 밀도 + 통행축 + 역세권 → 평일 점심 수요."""
    lunch_comp = (
        _saturating(int(inputs.lunch_pop), 30_000)
        if inputs.lunch_pop is not None
        else None
    )
    workers_comp = (
        _saturating(inputs.workers, 10_000) if inputs.workers is not None else None
    )
    choice_comp = (
        100.0 - inputs.choice_top_pct if inputs.choice_top_pct is not None else None
    )
    transit_comp = transit_score(inputs.nearest_station_m)
    score = _blend(
        [(lunch_comp, 0.3), (workers_comp, 0.4), (choice_comp, 0.15), (transit_comp, 0.15)]
    )
    if score is None:
        return None, "—", "종사자·통행 데이터 없음"
    parts = []
    if inputs.lunch_pop is not None:
        parts.append(f"점심(11–14시) 체류 {inputs.lunch_pop:,.0f}명")
    if inputs.workers is not None:
        parts.append(f"반경 {inputs.radius_m}m 종사자 {inputs.workers:,}명")
    if inputs.choice_top_pct is not None:
        parts.append(f"통행축 상위 {inputs.choice_top_pct}%")
    if inputs.nearest_station_m is not None:
        parts.append(f"역까지 {inputs.nearest_station_m:.0f}m")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_dinner_demand(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """저녁시간 실측 체류 + 거주 인구 + 20–40대 비중 + 접근성 → 저녁 외식 수요."""
    pop_comp = (
        _saturating(inputs.population, 20_000)
        if inputs.population is not None
        else None
    )
    dinner_comp = (
        _saturating(int(inputs.dinner_pop), 30_000)
        if inputs.dinner_pop is not None
        else None
    )
    adult_pct = sum(inputs.age_band_pcts.get(b, 0.0) for b in ("20대", "30대", "40대"))
    adult_comp = min(100.0, adult_pct * 2.0) if inputs.age_band_pcts else None
    integration_comp = (
        100.0 - inputs.integration_top_pct
        if inputs.integration_top_pct is not None
        else None
    )
    score = _blend(
        [(dinner_comp, 0.3), (pop_comp, 0.3), (adult_comp, 0.2), (integration_comp, 0.2)]
    )
    if score is None:
        return None, "—", "거주 인구 데이터 없음"
    parts = []
    if inputs.dinner_pop is not None:
        parts.append(f"저녁(17–21시) 체류 {inputs.dinner_pop:,.0f}명")
    if inputs.population is not None:
        parts.append(f"반경 {inputs.radius_m}m 거주 {inputs.population:,}명")
    if inputs.age_band_pcts:
        parts.append(f"20–40대 {adult_pct:.0f}%")
    if inputs.integration_top_pct is not None:
        parts.append(f"접근성 상위 {inputs.integration_top_pct}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_delivery_fit(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """배후 가구·인구 밀도 → 배달 상권 적합도 (가시성 무관)."""
    households_comp = (
        _saturating(inputs.households, 8_000)
        if inputs.households is not None
        else None
    )
    pop_comp = (
        _saturating(inputs.population, 20_000)
        if inputs.population is not None
        else None
    )
    score = _blend([(households_comp, 0.6), (pop_comp, 0.4)])
    if score is None:
        return None, "—", "가구·인구 데이터 없음"
    parts = []
    if inputs.households is not None:
        parts.append(f"반경 {inputs.radius_m}m 가구 {inputs.households:,}세대")
    if inputs.population is not None:
        parts.append(f"거주 {inputs.population:,}명")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_care_population(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """거주 인구 + 40–60대 비중 → 진료 수요 인구."""
    pop_comp = (
        _saturating(inputs.population, 20_000)
        if inputs.population is not None
        else None
    )
    care_pct = sum(
        inputs.age_band_pcts.get(b, 0.0) for b in ("40대", "50대", "60대")
    )
    care_comp = min(100.0, care_pct * 2.5) if inputs.age_band_pcts else None
    score = _blend([(pop_comp, 0.5), (care_comp, 0.5)])
    if score is None:
        return None, "—", "거주 인구 데이터 없음"
    parts = []
    if inputs.population is not None:
        parts.append(f"반경 {inputs.radius_m}m 거주 {inputs.population:,}명")
    if inputs.age_band_pcts:
        parts.append(f"40–60대 {care_pct:.0f}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_medical_gap(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """수요 대비 동일 진료과 공급 부족 → 공백 기회 (높을수록 기회)."""
    if inputs.match_method == "disabled":
        return None, "—", "업종 미선택 — 동일 진료과 집계 안 함"
    pop_comp = (
        _saturating(inputs.population, 20_000)
        if inputs.population is not None
        else None
    )
    density = competition_density_per_km2(
        inputs.same_category_count,
        radius_m=inputs.radius_m if inputs.competition_area_m2 is None else None,
        area_m2=inputs.competition_area_m2,
    )
    supply_room = max(0.0, 100.0 - min(100.0, density * 1.25))
    score = _blend([(pop_comp, 0.5), (supply_room, 0.5)])
    if score is None:
        return None, "—", "인구 데이터 없음"
    detail = (
        f"도보 5분 내 동일 진료과 {inputs.same_category_count}곳 (km²당 {density:.1f}곳)"
    )
    if inputs.population is not None:
        detail += f" · 반경 {inputs.radius_m}m 거주 {inputs.population:,}명"
    return score, score_qualitative_label(score), detail


def _kpi_school_age_population(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """거주 인구 + 학령(0–19세) 비중 → 학령 수요."""
    pop_comp = (
        _saturating(inputs.population, 20_000)
        if inputs.population is not None
        else None
    )
    school_pct = sum(
        inputs.age_band_pcts.get(b, 0.0) for b in ("10세 미만", "10대")
    )
    school_comp = min(100.0, school_pct * 4.0) if inputs.age_band_pcts else None
    score = _blend([(pop_comp, 0.4), (school_comp, 0.6)])
    if score is None:
        return None, "—", "거주 인구 데이터 없음"
    parts = []
    if inputs.population is not None:
        parts.append(f"반경 {inputs.radius_m}m 거주 {inputs.population:,}명")
    if inputs.age_band_pcts:
        parts.append(f"학령(0–19세) {school_pct:.0f}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_school_access(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """최근접 초중고 거리 → 하교 동선 도보 접근성 (300m 이내 만점, 1200m 이상 0점)."""
    if inputs.nearest_school_m is None:
        return None, "—", "반경 내 학교 없음"
    dist = inputs.nearest_school_m
    if dist <= 300:
        score = 100.0
    elif dist >= 1200:
        score = 0.0
    else:
        score = round(100.0 * (1200 - dist) / 900, 1)
    return score, score_qualitative_label(score), f"최근접 학교까지 {dist:.0f}m"


def _kpi_commute_access(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """대중교통 + 통행축 → 통학 접근성."""
    transit_comp = transit_score(inputs.nearest_station_m)
    choice_comp = (
        100.0 - inputs.choice_top_pct if inputs.choice_top_pct is not None else None
    )
    score = _blend([(transit_comp, 0.6), (choice_comp, 0.4)])
    if score is None:
        return None, "—", "교통·통행 데이터 없음"
    parts = []
    if inputs.nearest_station_m is not None:
        parts.append(f"역까지 {inputs.nearest_station_m:.0f}m")
    if inputs.choice_top_pct is not None:
        parts.append(f"통행축 상위 {inputs.choice_top_pct}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_female_population(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """거주 인구 + 여성 비율 + 20–40대 비중 → 핵심 고객층 규모."""
    pop_comp = (
        _saturating(inputs.population, 20_000)
        if inputs.population is not None
        else None
    )
    female_comp = (
        min(100.0, inputs.female_pct * 1.7) if inputs.female_pct is not None else None
    )
    adult_pct = sum(inputs.age_band_pcts.get(b, 0.0) for b in ("20대", "30대", "40대"))
    adult_comp = min(100.0, adult_pct * 2.0) if inputs.age_band_pcts else None
    score = _blend([(pop_comp, 0.4), (female_comp, 0.3), (adult_comp, 0.3)])
    if score is None:
        return None, "—", "거주 인구 데이터 없음"
    parts = []
    if inputs.population is not None:
        parts.append(f"반경 {inputs.radius_m}m 거주 {inputs.population:,}명")
    if inputs.female_pct is not None:
        parts.append(f"여성 {inputs.female_pct:.0f}%")
    if inputs.age_band_pcts:
        parts.append(f"20–40대 {adult_pct:.0f}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_repeat_demand(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """배후 가구·거주 인구 → 정기 재방문(단골) 수요 기반."""
    households_comp = (
        _saturating(inputs.households, 8_000)
        if inputs.households is not None
        else None
    )
    pop_comp = (
        _saturating(inputs.population, 20_000)
        if inputs.population is not None
        else None
    )
    score = _blend([(households_comp, 0.5), (pop_comp, 0.5)])
    if score is None:
        return None, "—", "가구·인구 데이터 없음"
    parts = []
    if inputs.households is not None:
        parts.append(f"반경 {inputs.radius_m}m 가구 {inputs.households:,}세대")
    if inputs.population is not None:
        parts.append(f"거주 {inputs.population:,}명")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_walk_population(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """거주 + 직장 + 통행축 → 도보 생활인구."""
    pop_comp = (
        _saturating(inputs.population, 20_000)
        if inputs.population is not None
        else None
    )
    workers_comp = (
        _saturating(inputs.workers, 10_000) if inputs.workers is not None else None
    )
    choice_comp = (
        100.0 - inputs.choice_top_pct if inputs.choice_top_pct is not None else None
    )
    score = _blend([(pop_comp, 0.4), (workers_comp, 0.3), (choice_comp, 0.3)])
    if score is None:
        return None, "—", "인구·통행 데이터 없음"
    parts = []
    if inputs.population is not None:
        parts.append(f"반경 {inputs.radius_m}m 거주 {inputs.population:,}명")
    if inputs.workers is not None:
        parts.append(f"종사자 {inputs.workers:,}명")
    if inputs.choice_top_pct is not None:
        parts.append(f"통행축 상위 {inputs.choice_top_pct}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_competition_distance(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """최근접 동일업종까지 거리 → 멀수록 유리 (500m 이상 만점)."""
    if inputs.match_method == "disabled":
        return None, "—", "업종 미선택 — 동일업종 집계 안 함"
    if inputs.total_store_count == 0:
        return None, "—", "상가(상권)정보 조회 실패 — 경쟁 지표 계산 불가"
    if inputs.min_same_category_distance_m is None:
        return 100.0, "경쟁 없음", f"반경 {inputs.radius_m}m 내 동일업종 없음"
    dist = inputs.min_same_category_distance_m
    score = round(min(100.0, dist / 500.0 * 100.0), 1)
    return score, f"최근접 {dist:.0f}m", (
        f"가장 가까운 동일업종까지 {dist:.0f}m · 도보 5분 내 {inputs.same_category_count}곳"
    )


def _foreign_note(inputs: KpiInputs) -> str:
    """관광상권 주석: 단기체류 외국인이 체류 총량의 5% 이상일 때만 표기."""
    if inputs.foreign_share_pct is not None and inputs.foreign_share_pct >= 5.0:
        return f" · 외국인 {inputs.foreign_share_pct:.0f}%"
    return ""


def _kpi_late_night_demand(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """심야(22–06시) 평균 체류 인구 → 심야 수요 (서울 생활인구 250m 격자)."""
    if inputs.late_night_pop is None:
        return None, "—", "시간대별 생활인구 데이터 없음 (생활인구 적재 필요)"
    score = round(_saturating(int(inputs.late_night_pop), 20_000), 1)
    days = inputs.living_weekday_days + inputs.living_weekend_days
    detail = (
        f"심야(22–06시) 평균 체류 {inputs.late_night_pop:,.0f}명 · 표본 {days}일"
        + _foreign_note(inputs)
    )
    return score, score_qualitative_label(score), detail


def _kpi_weekend_gap(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """주말 수요(생활인구) 대비 주말진료 확인 공급 → 주말 진료 공백 기회.

    공급은 심평원 세부정보 신고 기관(전체의 ~7%)만 집계된 하한값이라
    비율이 아닌 '확인된 곳 수'로만 감점한다 — 확인 공급이 많을수록 기회 축소.
    """
    demand_comp = (
        _saturating(int(inputs.weekend_prime_pop), 30_000)
        if inputs.weekend_prime_pop is not None
        else None
    )
    # 신고 커버리지가 지역 편중(서울 0%)이라 0곳은 "데이터 없음"과 구분 불가 —
    # 양성 신호(1곳 이상 확인)일 때만 공급 감점, 아니면 수요 단독 산정.
    supply_room = None
    sat, sun = inputs.clinic_sat_open, inputs.clinic_sun_open
    if sat is not None and sun is not None and (sat + sun) > 0:
        # 일요일 진료는 더 희소한 공급이라 2배 가중 감점
        supply_room = max(0.0, 100.0 - (sat * 10.0 + sun * 20.0))
    score = _blend([(demand_comp, 0.6), (supply_room, 0.4)])
    if score is None:
        return None, "—", "주말 생활인구·심평원 병원 진료시간 데이터 없음"
    parts = []
    if inputs.weekend_prime_pop is not None:
        parts.append(f"주말 낮(11–19시) 체류 {inputs.weekend_prime_pop:,.0f}명")
    if inputs.clinic_count_hira is not None:
        base = f"반경 {inputs.radius_m}m 의원·병원 {inputs.clinic_count_hira}곳"
        if supply_room is not None:
            base += f" · 주말진료 확인 토 {sat}/일 {sun}곳 (E-Gen·심평원 신고 기준)"
        else:
            base += " · 주말진료 신고정보 없음 (해당 지역 진료시간 데이터 커버리지 한계)"
        parts.append(base)
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_price_positioning(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """이 상권 업종 점포당 월매출의 서울 대비 위치 → 객단가/프리미엄 여력.

    가격표 공공데이터가 없어 점포당 실측 매출을 가격 수준 프록시로 쓴다
    (점포당 매출 높음 = 고객단가·프리미엄 시장, 낮음 = 저가 경쟁 시장).
    """
    if inputs.trdar_per_store_won is None or not inputs.citywide_per_store_won:
        return None, "—", "상권 추정매출 데이터 없음 (상권영역 밖이거나 업종 표본 부족)"
    ratio = inputs.trdar_per_store_won / inputs.citywide_per_store_won
    score = round(min(100.0, 50.0 * ratio), 1)
    per_store_man = inputs.trdar_per_store_won / 10_000
    citywide_man = inputs.citywide_per_store_won / 10_000
    return score, f"서울 중앙값의 {ratio:.1f}배", (
        f"이 상권 점포당 월매출 {per_store_man:,.0f}만원"
        f" · 서울 중앙값 {citywide_man:,.0f}만원"
        f" · {'프리미엄 여력' if ratio >= 1.2 else '저가 경쟁 시장' if ratio <= 0.8 else '평균 수준'}"
    )


def _kpi_weekend_demand(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """주말 낮(11–19시) 평균 체류 인구 → 주말 수요 (서울 생활인구 250m 격자)."""
    if inputs.weekend_prime_pop is None:
        return None, "—", "주말 생활인구 표본 없음 (주말 포함 데이터 적재 필요)"
    score = round(_saturating(int(inputs.weekend_prime_pop), 30_000), 1)
    detail = (
        f"주말 낮(11–19시) 평균 체류 {inputs.weekend_prime_pop:,.0f}명"
        f" · 표본 {inputs.living_weekend_days}일" + _foreign_note(inputs)
    )
    return score, score_qualitative_label(score), detail


def _kpi_brand_whitespace(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """상권 동종 점포 중 프랜차이즈 비중 → 낮을수록 브랜드 공백 기회."""
    if not inputs.trdar_store_count:
        return None, "—", "상권 점포 데이터 없음 (상권 밖이거나 점포 데이터 미적재)"
    franchise = inputs.trdar_franchise_count or 0
    ratio = franchise / inputs.trdar_store_count
    score = round(100.0 * (1.0 - ratio), 1)
    return score, f"프랜차이즈 {ratio * 100:.0f}%", (
        f"상권 동종 {inputs.trdar_store_count}곳 중 프랜차이즈 {franchise}곳"
        " — 비중이 낮을수록 브랜드 공백"
    )


def _cannibalization_display(score: float) -> str:
    if score >= 70:
        return "위험 높음"
    if score >= 40:
        return "위험 보통"
    return "위험 낮음"


def _seoul_note(inputs: KpiInputs) -> str:
    if inputs.own_brand_seoul_count is not None:
        return f" · 서울 전체 {inputs.own_brand_seoul_count}곳"
    return ""


def _kpi_cannibalization_risk(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """자사 지점 근접도 + 반경 내 지점 수 → 기존점 잠식 위험."""
    if not inputs.brand_label:
        return None, "—", "브랜드명 입력 필요 (프랜차이즈 모드에서 브랜드를 지정하세요)"
    if inputs.own_brand_count_in_radius is None:
        return None, "—", "자사 지점 조회 실패"
    if inputs.own_brand_count_in_radius == 0 or inputs.nearest_own_brand_m is None:
        return 0.0, "위험 없음", (
            f"반경 {inputs.radius_m}m 내 '{inputs.brand_label}' 지점 없음"
            + _seoul_note(inputs)
        )
    dist = inputs.nearest_own_brand_m
    dist_comp = max(0.0, 100.0 * (1.0 - min(dist, 1000.0) / 1000.0))
    count_comp = _saturating(inputs.own_brand_count_in_radius, 2)
    score = round(dist_comp * 0.6 + count_comp * 0.4, 1)
    return score, _cannibalization_display(score), (
        f"최근접 자사 지점 {dist:.0f}m · 반경 {inputs.radius_m}m 내 "
        f"{inputs.own_brand_count_in_radius}곳" + _seoul_note(inputs)
    )


def _kpi_new_coverage(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """자사 점포망이 닿지 않는 신규 커버리지 (멀수록·없을수록 유리)."""
    if not inputs.brand_label:
        return None, "—", "브랜드명 입력 필요 (프랜차이즈 모드에서 브랜드를 지정하세요)"
    if inputs.own_brand_count_in_radius is None:
        return None, "—", "자사 지점 조회 실패"
    if inputs.own_brand_count_in_radius == 0 or inputs.nearest_own_brand_m is None:
        return 100.0, "미개척 상권", (
            f"반경 {inputs.radius_m}m 내 '{inputs.brand_label}' 지점 없음 — 신규 커버리지"
            + _seoul_note(inputs)
        )
    dist = inputs.nearest_own_brand_m
    score = round(min(100.0, dist / 1000.0 * 100.0), 1)
    return score, f"최근접 {dist:.0f}m", (
        f"자사 지점과 {dist:.0f}m — 1km 이상이면 커버리지 중복 없음" + _seoul_note(inputs)
    )


def _kpi_network_efficiency(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """점포망 규모(물류·인지도) + 기존점과의 간격 → 점포망 효율."""
    if not inputs.brand_label:
        return None, "—", "브랜드명 입력 필요 (프랜차이즈 모드에서 브랜드를 지정하세요)"
    if inputs.own_brand_seoul_count is None:
        return None, "—", "자사 점포망 조회 실패"
    if inputs.own_brand_seoul_count == 0:
        return None, "—", f"서울 내 '{inputs.brand_label}' 지점 없음 — 첫 출점은 점포망 효율 해당 없음"
    scale_comp = _saturating(inputs.own_brand_seoul_count, 30)
    if inputs.nearest_own_brand_m is None:
        spacing_comp = 100.0  # 반경 내 자사 지점 없음 = 간격 충분
        spacing_text = f"반경 {inputs.radius_m}m 내 자사 지점 없음"
    else:
        spacing_comp = min(100.0, inputs.nearest_own_brand_m / 1000.0 * 100.0)
        spacing_text = f"최근접 자사 지점 {inputs.nearest_own_brand_m:.0f}m"
    score = _blend([(spacing_comp, 0.6), (scale_comp, 0.4)])
    return score, score_qualitative_label(score), (
        f"서울 자사 {inputs.own_brand_seoul_count}곳 · {spacing_text}"
    )


def _kpi_through_traffic(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """차량 통과 통행축 (choice) + 연결성 → 차량업종 핵심 입지."""
    choice_comp = (
        100.0 - inputs.choice_top_pct if inputs.choice_top_pct is not None else None
    )
    conn_comp = (
        100.0 - inputs.connectivity_top_pct
        if inputs.connectivity_top_pct is not None
        else None
    )
    score = _blend([(choice_comp, 0.7), (conn_comp, 0.3)])
    if score is None:
        return None, "—", "도로망 통행 데이터 없음"
    parts = []
    if inputs.choice_top_pct is not None:
        parts.append(f"통과 통행축 상위 {inputs.choice_top_pct}%")
    if inputs.connectivity_top_pct is not None:
        parts.append(f"연결성 상위 {inputs.connectivity_top_pct}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_vehicle_demand(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """가구(자가용 보유 단위) + 종사자(업무차량) → 배후 차량 수요."""
    hh_comp = (
        _saturating(inputs.households, 8_000) if inputs.households is not None else None
    )
    workers_comp = (
        _saturating(inputs.workers, 10_000) if inputs.workers is not None else None
    )
    pop_comp = (
        _saturating(inputs.population, 20_000) if inputs.population is not None else None
    )
    score = _blend([(hh_comp, 0.5), (workers_comp, 0.3), (pop_comp, 0.2)])
    if score is None:
        return None, "—", "가구·종사자 데이터 없음"
    parts = []
    if inputs.households is not None:
        parts.append(f"반경 {inputs.radius_m}m 가구 {inputs.households:,}세대")
    if inputs.workers is not None:
        parts.append(f"종사자 {inputs.workers:,}명")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_home_charging_gap(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """가구 밀집(집밥 공백) + 시군구 실측 충전량 분포 → 공용충전 수요."""
    hh_comp = (
        _saturating(inputs.households, 8_000) if inputs.households is not None else None
    )
    pop_comp = (
        _saturating(inputs.population, 20_000) if inputs.population is not None else None
    )
    demand_comp = inputs.ev_sgg_demand_pct  # 전국 시군구 백분위 그대로 0–100
    score = _blend([(hh_comp, 0.4), (pop_comp, 0.2), (demand_comp, 0.4)])
    if score is None:
        return None, "—", "가구·충전수요 데이터 없음"
    parts = []
    if inputs.households is not None:
        parts.append(f"반경 {inputs.radius_m}m 가구 {inputs.households:,}세대")
    if inputs.ev_sgg_sessions is not None and inputs.ev_sgg_demand_pct is not None:
        parts.append(
            f"시군구 연간 충전 {inputs.ev_sgg_sessions:,}건"
            f" (전국 상위 {100 - inputs.ev_sgg_demand_pct:.0f}%, 2024 합성데이터)"
        )
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_fuel_price_position(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """반경 주유소 평균가 vs 시도 평균 → 가격 포지셔닝 여지 (오피넷 실시간).

    주변가가 시도 평균보다 높으면 신규 진입자가 가격 경쟁력을 가질 여지 ↑.
    """
    if inputs.fuel_local_avg_won is None or not inputs.fuel_sido_avg_won:
        return None, "—", "오피넷 유가 조회 실패 또는 API 키 미설정"
    diff = inputs.fuel_local_avg_won - inputs.fuel_sido_avg_won
    diff_pct = 100.0 * diff / inputs.fuel_sido_avg_won
    score = round(min(100.0, max(0.0, 50.0 + diff_pct * 10.0)), 1)
    sign = "+" if diff >= 0 else ""
    return score, f"주변가 {sign}{diff:.0f}원", (
        f"반경 3km 주유소 {inputs.fuel_station_count}곳 휘발유 평균"
        f" {inputs.fuel_local_avg_won:,.0f}원 · 시도 평균 {inputs.fuel_sido_avg_won:,.0f}원"
        f" ({'가격 경쟁력 확보 여지' if diff_pct >= 1 else '저가 경쟁 지역' if diff_pct <= -1 else '평균 수준'})"
    )


def _kpi_charger_whitespace(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """반경 내 기존 충전소 밀도 → 공용충전 공백 기회.

    스케일은 서울 500m 반경 충전소(station) 수 실측 분포로 보정
    (중앙값 31곳, p90 57곳, 최대 83곳 — 2026-07 환경부 API 표본).
    급속 충전소는 완속보다 입지 매력이 커 신규 진입 매력을 더 깎는다.
    """
    if inputs.ev_charger_count is None:
        return None, "—", "충전소 위치 데이터 미적재 (환경부 API 승인 대기)"
    total = inputs.ev_charger_count
    fast = inputs.ev_fast_charger_count or 0
    slow = max(0, total - fast)
    if total == 0:
        return 100.0, "충전 공백", f"반경 {inputs.radius_m}m 내 등록 충전소 없음"
    effective = fast * 1.8 + slow  # 급속 가중
    score = round(100.0 - _saturating(effective, 35), 1)
    return score, score_qualitative_label(score), (
        f"반경 {inputs.radius_m}m 충전소 {total}곳 (급속 {fast}곳)"
    )


def _kpi_dwell_amenity(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """충전 대기 30분을 보낼 주변 상업시설 밀도 → 체류 여건."""
    if inputs.total_store_count == 0:
        return None, "—", "상가(상권)정보 조회 실패 — 체류 여건 계산 불가"
    score = round(_saturating(inputs.total_store_count, 150), 1)
    return score, score_qualitative_label(score), (
        f"도보권 전체 점포 {inputs.total_store_count}곳 — 충전 대기 중 이용 가능"
    )


def _kpi_tourist_stay(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """단기체류 외국인 비중 + 주말·심야 체류 → 관광 숙박 수요."""
    foreign_comp = (
        min(100.0, inputs.foreign_share_pct * 10.0)
        if inputs.foreign_share_pct is not None
        else None
    )
    weekend_comp = (
        _saturating(int(inputs.weekend_prime_pop), 30_000)
        if inputs.weekend_prime_pop is not None
        else None
    )
    night_comp = (
        _saturating(int(inputs.late_night_pop), 20_000)
        if inputs.late_night_pop is not None
        else None
    )
    score = _blend([(foreign_comp, 0.4), (weekend_comp, 0.35), (night_comp, 0.25)])
    if score is None:
        return None, "—", "생활인구·외국인 체류 데이터 없음"
    parts = []
    if inputs.foreign_share_pct is not None:
        parts.append(f"단기체류 외국인 비중 {inputs.foreign_share_pct:.1f}%")
    if inputs.weekend_prime_pop is not None:
        parts.append(f"주말 낮 체류 {inputs.weekend_prime_pop:,.0f}명")
    if inputs.late_night_pop is not None:
        parts.append(f"심야 체류 {inputs.late_night_pop:,.0f}명")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_business_density(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """종사자 밀도 + 주간 활동 체류 → 사업체 고객 기반."""
    workers_comp = (
        _saturating(inputs.workers, 10_000) if inputs.workers is not None else None
    )
    lunch_comp = (
        _saturating(int(inputs.lunch_pop), 30_000)
        if inputs.lunch_pop is not None
        else None
    )
    score = _blend([(workers_comp, 0.7), (lunch_comp, 0.3)])
    if score is None:
        return None, "—", "종사자 데이터 없음"
    parts = []
    if inputs.workers is not None:
        parts.append(f"반경 {inputs.radius_m}m 종사자 {inputs.workers:,}명")
    if inputs.lunch_pop is not None:
        parts.append(f"주간(점심) 체류 {inputs.lunch_pop:,.0f}명")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_active_age_population(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """거주 인구 + 20–40대 비중 → 운동 수요 핵심 연령층."""
    pop_comp = (
        _saturating(inputs.population, 20_000) if inputs.population is not None else None
    )
    active_pct = sum(inputs.age_band_pcts.get(b, 0.0) for b in ("20대", "30대", "40대"))
    active_comp = min(100.0, active_pct * 2.0) if inputs.age_band_pcts else None
    score = _blend([(pop_comp, 0.5), (active_comp, 0.5)])
    if score is None:
        return None, "—", "거주 인구 데이터 없음"
    parts = []
    if inputs.population is not None:
        parts.append(f"반경 {inputs.radius_m}m 거주 {inputs.population:,}명")
    if inputs.age_band_pcts:
        parts.append(f"20–40대 {active_pct:.0f}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_worker_resident_mix(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """직장인(출근 전)·주거(퇴근 후) 양쪽 수요와 그 균형 → 전일 가동률."""
    if inputs.workers is None and inputs.population is None:
        return None, "—", "종사자·거주 인구 데이터 없음"
    w = _saturating(inputs.workers, 10_000) if inputs.workers is not None else None
    p = _saturating(inputs.population, 20_000) if inputs.population is not None else None
    balance = (
        100.0 * min(w, p) / max(w, p) if w is not None and p is not None and max(w, p) > 0
        else None
    )
    score = _blend([(p, 0.4), (w, 0.4), (balance, 0.2)])
    if score is None:
        return None, "—", "종사자·거주 인구 데이터 없음"
    parts = []
    if inputs.population is not None:
        parts.append(f"거주 {inputs.population:,}명")
    if inputs.workers is not None:
        parts.append(f"종사자 {inputs.workers:,}명")
    if balance is not None:
        parts.append(f"주거·직장 균형 {balance:.0f}점")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_evening_access(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """저녁 체류 + 역세권 + 접근성 → 퇴근 후 방문 여건."""
    dinner_comp = (
        _saturating(int(inputs.dinner_pop), 30_000)
        if inputs.dinner_pop is not None
        else None
    )
    transit_comp = transit_score(inputs.nearest_station_m)
    integration_comp = (
        100.0 - inputs.integration_top_pct
        if inputs.integration_top_pct is not None
        else None
    )
    score = _blend([(dinner_comp, 0.5), (transit_comp, 0.3), (integration_comp, 0.2)])
    if score is None:
        return None, "—", "저녁 체류·교통 데이터 없음"
    parts = []
    if inputs.dinner_pop is not None:
        parts.append(f"저녁(17–21시) 체류 {inputs.dinner_pop:,.0f}명")
    if inputs.nearest_station_m is not None:
        parts.append(f"역까지 {inputs.nearest_station_m:.0f}m")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_youth_population(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """거주 인구 + 10–30대 비중 → 여가 소비 핵심층."""
    pop_comp = (
        _saturating(inputs.population, 20_000) if inputs.population is not None else None
    )
    youth_pct = sum(inputs.age_band_pcts.get(b, 0.0) for b in ("10대", "20대", "30대"))
    youth_comp = min(100.0, youth_pct * 2.0) if inputs.age_band_pcts else None
    score = _blend([(pop_comp, 0.4), (youth_comp, 0.6)])
    if score is None:
        return None, "—", "거주 인구 데이터 없음"
    parts = []
    if inputs.population is not None:
        parts.append(f"반경 {inputs.radius_m}m 거주 {inputs.population:,}명")
    if inputs.age_band_pcts:
        parts.append(f"10–30대 {youth_pct:.0f}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_target_fit(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """여성 비율 + 20–40대 비중 → 전문 소매 핵심 고객 적합도."""
    female_comp = (
        min(100.0, inputs.female_pct * 1.7) if inputs.female_pct is not None else None
    )
    adult_pct = sum(inputs.age_band_pcts.get(b, 0.0) for b in ("20대", "30대", "40대"))
    adult_comp = min(100.0, adult_pct * 2.0) if inputs.age_band_pcts else None
    score = _blend([(female_comp, 0.4), (adult_comp, 0.6)])
    if score is None:
        return None, "—", "성별·연령 데이터 없음"
    parts = []
    if inputs.female_pct is not None:
        parts.append(f"여성 {inputs.female_pct:.0f}%")
    if inputs.age_band_pcts:
        parts.append(f"20–40대 {adult_pct:.0f}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_household_base(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """배후 가구 규모 → 생활 서비스 기초 수요."""
    if inputs.households is None:
        return None, "—", "가구 데이터 없음"
    score = round(_saturating(inputs.households, 8_000), 1)
    return score, score_qualitative_label(score), (
        f"반경 {inputs.radius_m}m 가구 {inputs.households:,}세대"
    )


def _kpi_repeat_traffic(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """생활동선 접근성 + 가구·거주 기반 → 반복 방문 수요."""
    integration_comp = (
        100.0 - inputs.integration_top_pct
        if inputs.integration_top_pct is not None
        else None
    )
    hh_comp = (
        _saturating(inputs.households, 8_000) if inputs.households is not None else None
    )
    pop_comp = (
        _saturating(inputs.population, 20_000) if inputs.population is not None else None
    )
    score = _blend([(integration_comp, 0.4), (hh_comp, 0.35), (pop_comp, 0.25)])
    if score is None:
        return None, "—", "가구·접근성 데이터 없음"
    parts = []
    if inputs.integration_top_pct is not None:
        parts.append(f"접근성 상위 {inputs.integration_top_pct}%")
    if inputs.households is not None:
        parts.append(f"가구 {inputs.households:,}세대")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_daily_route(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """연결성 + 접근성 순위 → 일상 생활동선 위 입지 여부."""
    conn_comp = (
        100.0 - inputs.connectivity_top_pct
        if inputs.connectivity_top_pct is not None
        else None
    )
    integration_comp = (
        100.0 - inputs.integration_top_pct
        if inputs.integration_top_pct is not None
        else None
    )
    score = _blend([(conn_comp, 0.5), (integration_comp, 0.5)])
    if score is None:
        return None, "—", "도로망 데이터 없음"
    parts = []
    if inputs.connectivity_top_pct is not None:
        parts.append(f"연결성 상위 {inputs.connectivity_top_pct}%")
    if inputs.integration_top_pct is not None:
        parts.append(f"접근성 상위 {inputs.integration_top_pct}%")
    return score, score_qualitative_label(score), " · ".join(parts)


def _kpi_clinic_adjacency(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """반경 내 의원·병원 수 (심평원) → 처방전 수요의 원천."""
    if inputs.clinic_count_hira is None:
        return None, "—", "심평원 병의원 데이터 없음"
    score = round(_saturating(inputs.clinic_count_hira, 5), 1)
    return score, score_qualitative_label(score), (
        f"반경 {inputs.radius_m}m 의원·병원 {inputs.clinic_count_hira}곳 — 처방 수요 원천"
    )


def _kpi_walkin_access(inputs: KpiInputs) -> tuple[float | None, str, str]:
    """역세권 + 도로망 접근성 → 도보 내방 여건."""
    transit_comp = transit_score(inputs.nearest_station_m)
    integration_comp = (
        100.0 - inputs.integration_top_pct
        if inputs.integration_top_pct is not None
        else None
    )
    score = _blend([(transit_comp, 0.5), (integration_comp, 0.5)])
    if score is None:
        return None, "—", "교통·도로망 데이터 없음"
    parts = []
    if inputs.nearest_station_m is not None:
        parts.append(f"역까지 {inputs.nearest_station_m:.0f}m")
    if inputs.integration_top_pct is not None:
        parts.append(f"접근성 상위 {inputs.integration_top_pct}%")
    return score, score_qualitative_label(score), " · ".join(parts)


_KPI_REGISTRY: dict[str, Callable[[KpiInputs], tuple[float | None, str, str]]] = {
    # cafe
    "takeout_demand": _kpi_takeout_demand,
    "stay_demand": _kpi_stay_demand,
    "cafe_saturation": _kpi_cafe_saturation,
    "visibility": _kpi_visibility,
    # restaurant
    "lunch_demand": _kpi_lunch_demand,
    "dinner_demand": _kpi_dinner_demand,
    "delivery_fit": _kpi_delivery_fit,
    "competition_pressure": _kpi_saturation,
    # clinic
    "care_population": _kpi_care_population,
    "same_specialty_supply": _kpi_saturation,
    "medical_gap": _kpi_medical_gap,
    "weekend_gap": _kpi_weekend_gap,
    # academy
    "school_age_population": _kpi_school_age_population,
    "school_access": _kpi_school_access,
    "same_subject_competition": _kpi_saturation,
    "commute_access": _kpi_commute_access,
    # beauty
    "female_population": _kpi_female_population,
    "repeat_demand": _kpi_repeat_demand,
    "price_gap": _kpi_price_positioning,
    "weekend_demand": _kpi_weekend_demand,
    # convenience
    "walk_population": _kpi_walk_population,
    "late_night_demand": _kpi_late_night_demand,
    "corner_visibility": _kpi_visibility,
    "competition_distance": _kpi_competition_distance,
    # gas_station (auto)
    "through_traffic": _kpi_through_traffic,
    "vehicle_demand": _kpi_vehicle_demand,
    "fuel_price_position": _kpi_fuel_price_position,
    # ev_charging (auto) — vehicle_traffic은 through_traffic과 동일 로직 공유
    "vehicle_traffic": _kpi_through_traffic,
    "home_charging_gap": _kpi_home_charging_gap,
    "charger_whitespace": _kpi_charger_whitespace,
    "dwell_amenity": _kpi_dwell_amenity,
    # lodging
    "tourist_stay": _kpi_tourist_stay,
    "transit_gateway": _kpi_commute_access,
    "lodging_competition": _kpi_saturation,
    "night_activity": _kpi_late_night_demand,
    # professional_office
    "business_density": _kpi_business_density,
    "client_access": _kpi_commute_access,
    "professional_competition": _kpi_saturation,
    "daytime_traffic": _kpi_lunch_demand,
    # fitness
    "active_age_population": _kpi_active_age_population,
    "worker_resident_mix": _kpi_worker_resident_mix,
    "fitness_competition": _kpi_saturation,
    "evening_access": _kpi_evening_access,
    # leisure
    "youth_population": _kpi_youth_population,
    "late_night_stay": _kpi_late_night_demand,
    "weekend_family": _kpi_weekend_demand,
    "leisure_competition": _kpi_saturation,
    # retail_specialty
    "walk_traffic": _kpi_walk_population,
    "target_fit": _kpi_target_fit,
    "retail_competition": _kpi_saturation,
    "storefront_visibility": _kpi_visibility,
    # life_service
    "household_base": _kpi_household_base,
    "repeat_traffic": _kpi_repeat_traffic,
    "service_competition": _kpi_competition_distance,
    "daily_route": _kpi_daily_route,
    # pharmacy
    "clinic_adjacency": _kpi_clinic_adjacency,
    "prescription_demand": _kpi_care_population,
    "pharmacy_competition": _kpi_saturation,
    "walkin_access": _kpi_walkin_access,
    # franchise (userOverrides) — brand 입력 시 계산, 없으면 정직한 None
    "cannibalization_risk": _kpi_cannibalization_risk,
    "new_coverage": _kpi_new_coverage,
    "brand_whitespace": _kpi_brand_whitespace,
    "network_efficiency": _kpi_network_efficiency,
    "market_overlap": _kpi_cannibalization_risk,
    "designer_supply": lambda i: (None, "—", "미용사 공급 데이터 미제공 (공공데이터 한계)"),
}


def _profile_kpi_configs(profile: dict) -> list[dict[str, str]]:
    """Base summaryKpis plus every userOverrides variant, deduped by key.

    The site-score request carries no user type, so the response includes
    every KPI the profile's UI may show; the frontend picks by key.
    """
    seen: set[str] = set()
    configs: list[dict[str, str]] = []
    sources = [profile.get("ui", {}).get("summaryKpis") or []]
    for override in (profile.get("userOverrides") or {}).values():
        sources.append(override.get("summaryKpis") or [])
    for source in sources:
        for kpi in source:
            key = kpi.get("key")
            if not key or key in seen:
                continue
            seen.add(key)
            configs.append(kpi)
    return configs


def compute_industry_kpis(profile: dict, inputs: KpiInputs) -> list[IndustryKpi]:
    results: list[IndustryKpi] = []
    for config in _profile_kpi_configs(profile):
        key = config["key"]
        label = config.get("label", key)
        fn = _KPI_REGISTRY.get(key)
        if fn is None:
            results.append(
                IndustryKpi(
                    key=key,
                    label=label,
                    score=None,
                    display="—",
                    detail="지표 계산 미지원 (준비 중)",
                )
            )
            continue
        score, display, detail = fn(inputs)
        results.append(
            IndustryKpi(key=key, label=label, score=score, display=display, detail=detail)
        )
    return results
