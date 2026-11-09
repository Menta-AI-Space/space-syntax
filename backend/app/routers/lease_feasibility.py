"""Lease feasibility API — user rent inputs + site score + Seoul sales benchmark."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    DashboardPointRequest,
    ExplanationOut,
    LeaseFeasibilityRequest,
    LeaseFeasibilityResponse,
    LeaseSiteContextOut,
    SiteScoreRequest,
)
from app.routers.dashboard import _resolve_point, _resolve_upjong, cannibalization, isochrone
from app.routers.site_score import site_score
from app.services.lease_feasibility import LeaseInputs, SiteContext, analyze_lease_feasibility
from app.services.seoul_sales import fetch_sales_benchmark
from app.services.space_syntax import extend_unique_warnings
from app.services.cafe_model_fit import compute_cafe_model_fit, top_model_labels as cafe_top
from app.services.real_competition import (
    compute_real_competition,
    resolve_subject_codes,
    resolve_vertical,
)
from app.services.restaurant_model_fit import (
    SiteMetrics,
    compute_restaurant_model_fit,
    top_model_labels as restaurant_top,
)
from app.services.sbiz_stores import SbizError, fetch_stores_in_radius
from app.services.sgis import SgisError, fetch_demand_profile

router = APIRouter(prefix="/api", tags=["lease-feasibility"])


def _appropriate_rent_band(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return ""
    return f"{low:.0f}~{high:.0f}만 원"


@router.post("/lease-feasibility", response_model=LeaseFeasibilityResponse)
async def lease_feasibility(request: LeaseFeasibilityRequest):
    warnings: list[str] = []
    try:
        lat, lng = await _resolve_point(request)
    except HTTPException:
        raise

    upjong = await _resolve_upjong(request)
    score_req = SiteScoreRequest(
        lat=lat,
        lng=lng,
        radius_m=request.radius_m if request.radius_m in (500, 1000) else 500,
        inds_lcls_cd=request.inds_lcls_cd,
        inds_mcls_cd=request.inds_mcls_cd,
        inds_scls_cd=request.inds_scls_cd,
        business_type=request.business_type,
    )
    dash_req = DashboardPointRequest(
        lat=lat,
        lng=lng,
        radius_m=request.radius_m,
        inds_lcls_cd=request.inds_lcls_cd,
        inds_mcls_cd=request.inds_mcls_cd,
        inds_scls_cd=request.inds_scls_cd,
        business_type=request.business_type,
    )

    try:
        score = await site_score(score_req)
    except HTTPException as exc:
        raise exc
    # site_score 내부에서 엔진 폴백(DepthmapX 미사용)·수요 조회 실패·도달권
    # 계산 실패 등이 나면 score.warnings에 남는다. total_score·grade·경쟁
    # 카운트·모델 적합도가 전부 이 site_score 결과에서 나오므로, 이 경고를
    # 옮기지 않으면 화면에는 신뢰도 표시 없이 확정치처럼 나간다.
    extend_unique_warnings(warnings, getattr(score, "warnings", None) or [])

    try:
        cann = await cannibalization(dash_req)
    except HTTPException as exc:
        warnings.append(str(exc.detail))
        cann = None

    adm_name = None
    try:
        profile = await fetch_demand_profile(lat, lng)
        adm_name = profile.adm_name
    except SgisError as exc:
        warnings.append(str(exc))

    same_count = score.competition.same_category_count if score.competition else 0
    bench = await fetch_sales_benchmark(
        lat,
        lng,
        adm_name=adm_name,
        inds_scls_cd=upjong.inds_scls_cd,
        inds_mcls_cd=upjong.inds_mcls_cd,
        business_label=upjong.label,
        fallback_store_count=same_count or None,
    )
    warnings.extend(bench.warnings)

    site = SiteContext(
        total_score=score.total_score,
        grade=score.grade,
        competition_label=score.competition.intensity_label if score.competition else "",
        same_category_count=same_count,
        cannibalization_risk=cann.risk_score if cann else None,
        cannibalization_label=cann.risk_label if cann else "",
        business_label=upjong.label,
    )

    vertical = resolve_vertical(
        archetype=upjong.archetype,
        inds_mcls_cd=upjong.inds_mcls_cd,
        inds_scls_cd=upjong.inds_scls_cd,
    )
    if vertical:
        stores = []
        try:
            stores = await fetch_stores_in_radius(lat, lng, request.radius_m)
        except SbizError as exc:
            warnings.append(str(exc))
        if stores:
            subject_codes = (
                (request.subject_codes or None) or resolve_subject_codes(upjong.inds_scls_cd)
                if vertical == "clinic"
                else None
            )
            comp = compute_real_competition(
                lat=lat,
                lng=lng,
                stores=stores,
                vertical=vertical,
                radius_m=request.radius_m,
                subject_codes=subject_codes,
            )
            youth = mid = None
            if score.demand and score.demand.age_bands:
                for band in score.demand.age_bands:
                    if "20" in band.band and "29" in band.band:
                        youth = band.pct
                    if "40" in band.band or "50" in band.band:
                        mid = (mid or 0) + band.pct
            metrics_in = SiteMetrics(
                workers=score.demand.workers if score.demand else None,
                population=score.demand.population if score.demand else None,
                households=score.demand.households if score.demand else None,
                youth_pct=youth,
                middle_age_pct=mid,
                integration_top_pct=score.segment.integration_top_pct,
                choice_top_pct=score.segment.choice_top_pct,
                connectivity_top_pct=score.segment.connectivity_top_pct,
                transit_distance_m=score.transit.distance_m if score.transit else None,
                total_score=score.total_score,
                area_pyeong=request.area_pyeong,
            )
            if vertical == "restaurant":
                fit = compute_restaurant_model_fit(metrics_in, comp)
                site.best_restaurant_models = restaurant_top(fit, 2)
            else:
                fit = compute_cafe_model_fit(metrics_in, comp)
                site.best_cafe_models = cafe_top(fit, 2)

    inputs = LeaseInputs(
        monthly_rent_man=request.monthly_rent_man,
        maintenance_man=request.maintenance_man,
        deposit_man=request.deposit_man,
        key_money_man=request.key_money_man,
        area_pyeong=request.area_pyeong,
        staff_count=request.staff_count,
        ticket_price=request.ticket_price,
        cogs_ratio=request.cogs_ratio,
        delivery_share=request.delivery_share,
        interior_cost_man=request.interior_cost_man,
        hours_per_day=request.hours_per_day,
        days_per_week=request.days_per_week,
        labor_cost_man=request.labor_cost_man,
        misc_fixed_man=request.misc_fixed_man,
    )

    benchmark_man = bench.monthly_revenue_man
    result = analyze_lease_feasibility(
        inputs,
        upjong.archetype,
        site,
        benchmark_monthly_man=benchmark_man,
        benchmark_scope=bench.scope,
    )
    m = result.metrics

    return LeaseFeasibilityResponse(
        verdict=result.verdict,
        monthly_fixed_cost_man=m.monthly_fixed_cost_man,
        labor_cost_man=m.labor_cost_man,
        misc_fixed_man=m.misc_fixed_man,
        variable_cost_ratio=m.variable_cost_ratio,
        break_even_monthly_man=m.break_even_monthly_man,
        break_even_daily_man=m.break_even_daily_man,
        orders_per_day=m.orders_per_day,
        rent_burden_pct=m.rent_burden_pct,
        appropriate_rent_low_man=m.appropriate_rent_low_man,
        appropriate_rent_high_man=m.appropriate_rent_high_man,
        appropriate_rent_band=_appropriate_rent_band(
            m.appropriate_rent_low_man, m.appropriate_rent_high_man
        ),
        rent_excess_man=m.rent_excess_man,
        key_money_payback_months=m.key_money_payback_months,
        recommended_rent_max_man=m.recommended_rent_max_man,
        rent_per_pyeong_man=m.rent_per_pyeong_man,
        benchmark_monthly_revenue_man=benchmark_man,
        benchmark_per_store_monthly_man=bench.per_store_monthly_man,
        benchmark_scope=bench.scope,
        benchmark_data_source=bench.data_source,
        site_context=LeaseSiteContextOut(
            total_score=site.total_score,
            grade=site.grade,
            competition_label=site.competition_label,
            same_category_count=site.same_category_count,
            cannibalization_risk=site.cannibalization_risk,
            cannibalization_label=site.cannibalization_label,
            business_label=site.business_label,
            best_restaurant_models=site.best_restaurant_models,
            best_cafe_models=site.best_cafe_models,
        ),
        explanation=ExplanationOut(text=result.explanation, source=result.explanation_source),
        warnings=warnings,
    )
