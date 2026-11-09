"""Real competition + model fit API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.routers.academy_cluster import run_academy_cluster_analysis
from app.models.schemas import (
    AcademyClusterRequest,
    ClinicSubjectOut,
    ClinicSubjectsResponse,
    CompetitionBucketOut,
    CompetitorSummaryOut,
    ModelFitItemOut,
    ModelFitResponse,
    RealCompetitionBundleResponse,
    RealCompetitionRequest,
    RealCompetitionResponse,
    SiteScoreRequest,
    SynergyClusterBucketOut,
    SynergyResponse,
    SynergyScoresOut,
    SynergyStoreOut,
)
from app.routers.dashboard import _load_context, _resolve_point, _resolve_upjong
from app.routers.site_score import site_score
from app.services.isochrone import competition_fetch_radius_m, compute_isochrones
from app.services.territory_cannibalization import (
    MODEL_FIT_REACH_MINUTES,
    MODEL_FIT_REACH_MODE,
)
from app.services.space_syntax import extend_unique_warnings
from app.services.academy_model_fit import compute_academy_model_fit
from app.services.bakery_model_fit import compute_bakery_model_fit
from app.services.cafe_model_fit import compute_cafe_model_fit
from app.services.clinic_model_fit import compute_clinic_model_fit
from app.services.hira_clinics import HiraError, list_all_subjects
from app.services.real_competition import (
    compute_real_competition,
    resolve_subject_codes,
    resolve_vertical,
)
from app.services.restaurant_model_fit import (
    SiteMetrics,
    compute_restaurant_model_fit,
)
from app.services.synergy_analysis import (
    VALID_FOOD_DRINK_TARGETS,
    compute_synergy_cluster,
)

router = APIRouter(prefix="/api", tags=["real-competition"])

# 모델 적합도가 구현된 업종만. 경쟁규칙 프로필은 있지만 적합도 모델이 없는
# 업종(beauty, convenience 등)은 경쟁 분석만 제공하고 적합도는 건너뛴다 —
# 이전에는 _compute_model_fit의 ValueError가 500으로 새어나갔다.
# (academy는 위쪽 전용 경로에서 조기 반환하므로 여기 포함하지 않는다.)
MODEL_FIT_VERTICALS = frozenset({"restaurant", "cafe", "clinic", "bakery"})


def _bucket_out(b) -> CompetitionBucketOut:
    return CompetitionBucketOut(
        key=b.key,
        label=b.label,
        count=b.count,
        weighted_score=b.weighted_score,
        intensity=b.intensity,
        nearest=[
            CompetitorSummaryOut(
                name=n.name,
                category=n.category,
                distance_m=n.distance_m,
                weight=n.weight,
                subjects=n.subjects,
                specialist_count=n.specialist_count,
            )
            for n in b.nearest
        ],
    )


def _synergy_out(result) -> SynergyResponse:
    return SynergyResponse(
        target=result.target,
        target_label=result.target_label,
        clusters=[
            SynergyClusterBucketOut(
                key=c.key,
                label=c.label,
                relation_group=c.relation_group,
                count=c.count,
                weighted_score=c.weighted_score,
                intensity=c.intensity,
                stores=[
                    SynergyStoreOut(
                        name=s.name,
                        category=s.category,
                        nearby_category=s.nearby_category,
                        relation=s.relation,
                        distance_m=s.distance_m,
                        same_building=s.same_building,
                        weight=s.weight,
                        address=s.address,
                    )
                    for s in c.stores
                ],
            )
            for c in result.clusters
        ],
        scores=SynergyScoresOut(
            complementary_synergy=result.scores.complementary_synergy,
            direct_competition_pressure=result.scores.direct_competition_pressure,
        ),
        headline=result.headline,
        interpretation=result.interpretation,
        warnings=result.warnings,
    )


def _model_fit_out(result, *, reach_label: str | None = None) -> ModelFitResponse:
    return ModelFitResponse(
        vertical=result.vertical,
        headline=result.headline,
        models=[
            ModelFitItemOut(
                model_id=m.model_id,
                label=m.label,
                score=round(m.score, 1),
                verdict=m.verdict,
                detail=m.detail,
            )
            for m in result.models
        ],
        reach_label=reach_label,
        warnings=result.warnings,
    )


def _pct_from_bands(age_bands, *labels: str) -> float | None:
    total = 0.0
    found = False
    for band in age_bands:
        if band.band in labels:
            total += band.pct
            found = True
    return total if found else None


def _site_metrics_from_score(score, request: RealCompetitionRequest) -> SiteMetrics:
    youth = None
    mid = None
    school = None
    parent = None
    senior = None
    female = None
    apartment_pct = None
    if score.demand and score.demand.age_bands:
        bands = score.demand.age_bands
        youth = _pct_from_bands(bands, "20대", "30대")
        mid = _pct_from_bands(bands, "40대", "50대")
        school = _pct_from_bands(bands, "10세 미만", "10대")
        parent = _pct_from_bands(bands, "30대", "40대", "50대")
        senior = _pct_from_bands(bands, "60대", "70세+")
    if score.demand and score.demand.gender:
        female = score.demand.gender.female_pct
    if score.demand and score.demand.housing:
        apartment_pct = score.demand.housing.apartment_pct
    return SiteMetrics(
        workers=score.demand.workers if score.demand else None,
        population=score.demand.population if score.demand else None,
        households=score.demand.households if score.demand else None,
        youth_pct=youth,
        middle_age_pct=mid,
        school_age_pct=school,
        parent_pct=parent,
        senior_pct=senior,
        female_pct=female,
        apartment_pct=apartment_pct,
        integration_top_pct=score.segment.integration_top_pct,
        choice_top_pct=score.segment.choice_top_pct,
        connectivity_top_pct=score.segment.connectivity_top_pct,
        transit_distance_m=score.transit.distance_m if score.transit else None,
        total_score=score.total_score,
        area_pyeong=request.area_pyeong,
        has_parking=request.has_parking,
        has_exhaust=request.has_exhaust,
        has_elevator=request.has_elevator,
        floor_level=request.floor_level,
        has_pharmacy_nearby=request.has_pharmacy_nearby,
    )


def _compute_model_fit(vertical: str, metrics: SiteMetrics, comp, cluster=None):
    if vertical == "restaurant":
        return compute_restaurant_model_fit(metrics, comp)
    if vertical == "cafe":
        return compute_cafe_model_fit(metrics, comp)
    if vertical == "clinic":
        return compute_clinic_model_fit(metrics, comp)
    if vertical == "academy":
        return compute_academy_model_fit(metrics, cluster)
    if vertical == "bakery":
        return compute_bakery_model_fit(metrics, comp)
    raise ValueError(f"unknown vertical: {vertical}")


@router.get("/clinic-subjects", response_model=ClinicSubjectsResponse)
async def clinic_subjects():
    """전국 HIRA 진료과목 목록 — 진료과목 선택 UI(picker)용. HIRA 데이터가 없으면
    (테이블 미적재) 빈 목록 + warning으로 강등한다 (다른 HIRA enrichment 호출부와
    동일한 폴백 방식)."""
    try:
        subjects = list_all_subjects()
    except HiraError as e:
        return ClinicSubjectsResponse(subjects=[], warnings=[str(e)])
    return ClinicSubjectsResponse(
        subjects=[
            ClinicSubjectOut(
                subject_code=s.subject_code,
                subject_name=s.subject_name,
                institution_count=s.institution_count,
            )
            for s in subjects
        ]
    )


@router.post("/real-competition", response_model=RealCompetitionBundleResponse)
async def real_competition(request: RealCompetitionRequest):
    warnings: list[str] = []
    lat, lng = await _resolve_point(request)
    upjong = await _resolve_upjong(request)
    vertical = resolve_vertical(
        archetype=upjong.archetype,
        inds_mcls_cd=upjong.inds_mcls_cd,
        inds_scls_cd=upjong.inds_scls_cd,
    )
    if not vertical:
        raise HTTPException(
            status_code=400,
            detail=(
                "음식·카페·병원·학원·베이커리 업종을 선택하면 "
                "실질 경쟁·모델 적합도 분석을 사용할 수 있습니다."
            ),
        )

    if vertical == "academy":
        if not request.target_model:
            raise HTTPException(
                status_code=400,
                detail="학원 업종은 과목(수학·영어 등)을 선택해야 클러스터 분석을 사용할 수 있습니다.",
            )
        cluster_req = AcademyClusterRequest(**request.model_dump())
        cluster_resp = await run_academy_cluster_analysis(cluster_req)
        return RealCompetitionBundleResponse(
            academy_cluster=cluster_resp,
            model_fit=cluster_resp.model_fit,
            warnings=cluster_resp.warnings,
        )

    subject_codes = (
        (request.subject_codes or None) or resolve_subject_codes(upjong.inds_scls_cd)
        if vertical == "clinic"
        else None
    )

    fetch_radius = competition_fetch_radius_m(
        request.radius_m, request.mode, request.competitor_minutes
    )
    segments, G, stores, _, demand_profile, load_warnings = await _load_context(
        lat, lng, fetch_radius, upjong
    )
    dong_pop = demand_profile.population if demand_profile else None
    warnings.extend(load_warnings)

    reachable_polygon = None
    iso_result = compute_isochrones(
        G=G,
        segments=segments,
        lat=lat,
        lng=lng,
        minutes_list=(request.competitor_minutes,),
        mode=request.mode,
        stores=stores,
        inds_mcls_cd=upjong.inds_mcls_cd,
        inds_scls_cd=upjong.inds_scls_cd,
        dong_population=dong_pop,
    )
    extend_unique_warnings(warnings, iso_result.warnings)
    if iso_result.bands:
        reachable_polygon = iso_result.bands[0].polygon

    comp = compute_real_competition(
        lat=lat,
        lng=lng,
        stores=stores,
        vertical=vertical,
        radius_m=request.radius_m,
        reachable_polygon=reachable_polygon,
        reach_mode=request.mode,
        reach_minutes=request.competitor_minutes,
        subject_codes=subject_codes,
    )
    extend_unique_warnings(warnings, comp.warnings)

    synergy_resp = None
    if vertical in VALID_FOOD_DRINK_TARGETS:
        synergy_result = compute_synergy_cluster(
            lat=lat,
            lng=lng,
            stores=stores,
            target=vertical,
            radius_m=request.radius_m,
            bld_mng_no=request.bld_mng_no,
        )
        synergy_resp = _synergy_out(synergy_result)

    model_fit = None
    if vertical not in MODEL_FIT_VERTICALS:
        warnings.append(
            "이 업종은 모델 적합도 분석을 아직 지원하지 않아 경쟁 강도 분석만 제공합니다."
        )
    else:
        # 모델 적합도 권역(도보 15분)이 경쟁 권역보다 크면 더 큰 그래프가 필요하다.
        # 경쟁·적합도가 모두 이 그래프를 쓰도록 한 번만 로드해 이중 빌드를 피한다.
        fit_fetch_radius = competition_fetch_radius_m(
            request.radius_m, MODEL_FIT_REACH_MODE, MODEL_FIT_REACH_MINUTES
        )
        if fit_fetch_radius > fetch_radius:
            segments_fit, G_fit, stores_fit, _, _, fit_load_warnings = await _load_context(
                lat, lng, fit_fetch_radius, upjong
            )
            extend_unique_warnings(warnings, fit_load_warnings)
        else:
            segments_fit, G_fit, stores_fit = segments, G, stores

        fit_iso = compute_isochrones(
            G=G_fit,
            segments=segments_fit,
            lat=lat,
            lng=lng,
            minutes_list=(MODEL_FIT_REACH_MINUTES,),
            mode=MODEL_FIT_REACH_MODE,
            stores=stores_fit,
            inds_mcls_cd=upjong.inds_mcls_cd,
            inds_scls_cd=upjong.inds_scls_cd,
            dong_population=dong_pop,
        )
        fit_polygon = fit_iso.bands[0].polygon if fit_iso.bands else None
        comp_for_fit = compute_real_competition(
            lat=lat,
            lng=lng,
            stores=stores_fit,
            vertical=vertical,
            radius_m=request.radius_m,
            reachable_polygon=fit_polygon,
            reach_mode=MODEL_FIT_REACH_MODE,
            reach_minutes=MODEL_FIT_REACH_MINUTES,
            subject_codes=subject_codes,
        )

        try:
            score = await site_score(
                SiteScoreRequest(
                    lat=lat,
                    lng=lng,
                    radius_m=request.radius_m if request.radius_m in (500, 1000) else 500,
                    inds_lcls_cd=request.inds_lcls_cd,
                    inds_mcls_cd=request.inds_mcls_cd,
                    inds_scls_cd=request.inds_scls_cd,
                    business_type=request.business_type,
                    include_ai_explanation=False,
                )
            )
            metrics = _site_metrics_from_score(score, request)
            fit = _compute_model_fit(vertical, metrics, comp_for_fit)
            # site_score 내부에서 엔진 폴백(DepthmapX 미사용)이나 수요 조회
            # 실패가 나면 score.warnings에 남는다. 이 경고를 안 옮기면 그
            # 경고에서 나온 metrics로 계산한 모델 적합도 점수가 아무 신뢰도
            # 표시 없이 확정치처럼 나간다.
            extend_unique_warnings(fit.warnings, score.warnings)
            fit.warnings.insert(
                0,
                f"모델 적합도는 도보 {MODEL_FIT_REACH_MINUTES}분 권역 경쟁·배후 수요 기준입니다.",
            )
            model_fit = _model_fit_out(
                fit,
                reach_label=f"도보 {MODEL_FIT_REACH_MINUTES}분 권역",
            )
            extend_unique_warnings(warnings, fit.warnings)
        except HTTPException as exc:
            warnings.append(str(exc.detail))

    return RealCompetitionBundleResponse(
        competition=RealCompetitionResponse(
            vertical=comp.vertical,
            total_weighted_score=comp.total_weighted_score,
            total_intensity=comp.total_intensity,
            buckets=[_bucket_out(b) for b in comp.buckets],
            summary_one_liner=comp.summary_one_liner,
            reach_mode=comp.reach_mode,
            reach_minutes=comp.reach_minutes,
            reach_label=comp.reach_label,
            warnings=comp.warnings,
        ),
        model_fit=model_fit,
        synergy=synergy_resp,
        warnings=warnings,
    )
