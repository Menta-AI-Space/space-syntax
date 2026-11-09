"""Academy cluster analysis API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    AcademyClusterBucketOut,
    AcademyClusterRequest,
    AcademyClusterResponse,
    AcademyClusterStoreOut,
    AcademyScoresOut,
    ModelFitItemOut,
    ModelFitResponse,
    ParentSafetyDetailOut,
    SiteScoreRequest,
)
from app.routers.dashboard import _load_context, _resolve_point, _resolve_upjong
from app.routers.site_score import site_score
from app.services.academy_cluster_analysis import (
    VALID_TARGET_MODELS,
    compute_academy_cluster,
    is_academy_upjong,
)
from app.services.academy_model_fit import compute_academy_model_fit
from app.services.isochrone import _point_in_polygon, compute_isochrones
from app.services.restaurant_model_fit import SiteMetrics
from app.services.space_syntax import extend_unique_warnings

router = APIRouter(prefix="/api", tags=["academy-cluster"])


def _pct_from_bands(age_bands, *labels: str) -> float | None:
    total = 0.0
    found = False
    for band in age_bands:
        if band.band in labels:
            total += band.pct
            found = True
    return total if found else None


def _site_metrics_from_score(score, request) -> SiteMetrics:
    youth = mid = school = parent = senior = female = apartment_pct = None
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


def _cluster_out(result) -> AcademyClusterResponse:
    return AcademyClusterResponse(
        target_model=result.target_model,
        target_label=result.target_label,
        clusters=[
            AcademyClusterBucketOut(
                key=c.key,
                label=c.label,
                relation_group=c.relation_group,
                count=c.count,
                weighted_score=c.weighted_score,
                intensity=c.intensity,
                stores=[
                    AcademyClusterStoreOut(
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
        scores=AcademyScoresOut(
            direct_competition_pressure=result.scores.direct_competition_pressure,
            cluster_synergy=result.scores.cluster_synergy,
            parent_safety=result.scores.parent_safety,
        ),
        parent_safety_detail=ParentSafetyDetailOut(
            level=result.parent_safety_detail.level,
            same_building_risks=result.parent_safety_detail.same_building_risks,
            nearby_risks=result.parent_safety_detail.nearby_risks,
            sensitivity_note=result.parent_safety_detail.sensitivity_note,
        ),
        headline=result.headline,
        interpretation=result.interpretation,
        warnings=result.warnings,
    )


def _model_fit_out(result) -> ModelFitResponse:
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
        warnings=result.warnings,
    )


async def _filter_stores_by_isochrone(
    request: AcademyClusterRequest,
    lat: float,
    lng: float,
    stores,
    segments,
    G,
):
    iso = compute_isochrones(
        G=G,
        segments=segments,
        lat=lat,
        lng=lng,
        minutes_list=(request.competitor_minutes,),
        mode=request.mode,
        stores=stores,
    )
    if not iso.bands:
        return stores
    poly = iso.bands[0].polygon
    filtered = []
    for store in stores:
        if store.lat is None or store.lon is None:
            continue
        if _point_in_polygon(store.lon, store.lat, poly):
            filtered.append(store)
    return filtered


async def run_academy_cluster_analysis(
    request: AcademyClusterRequest,
) -> AcademyClusterResponse:
    if request.target_model not in VALID_TARGET_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"target_model must be one of: {', '.join(sorted(VALID_TARGET_MODELS))}",
        )

    lat, lng = await _resolve_point(request)
    upjong = await _resolve_upjong(request)
    if not is_academy_upjong(
        archetype=upjong.archetype,
        inds_mcls_cd=upjong.inds_mcls_cd,
    ):
        raise HTTPException(
            status_code=400,
            detail="학원·교육 업종을 선택하면 과목별 클러스터 분석을 사용할 수 있습니다.",
        )

    segments, G, stores, _, _, load_warnings = await _load_context(
        lat, lng, request.radius_m, upjong
    )
    stores = await _filter_stores_by_isochrone(
        request, lat, lng, stores, segments, G
    )

    cluster = compute_academy_cluster(
        lat=lat,
        lng=lng,
        stores=stores,
        target_model=request.target_model,
        radius_m=request.radius_m,
        bld_mng_no=request.bld_mng_no,
    )
    cluster.warnings.extend(load_warnings)

    response = _cluster_out(cluster)
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
            )
        )
        metrics = _site_metrics_from_score(score, request)
        fit = compute_academy_model_fit(metrics, cluster)
        # site_score 내부에서 엔진 폴백(DepthmapX 미사용)이나 수요 조회
        # 실패가 나면 score.warnings에 남는다. 이 경고를 안 옮기면 그
        # 경고에서 나온 metrics로 계산한 모델 적합도 점수가 아무 신뢰도
        # 표시 없이 확정치처럼 나간다.
        extend_unique_warnings(fit.warnings, score.warnings)
        response.model_fit = _model_fit_out(fit)
        response.warnings.extend(fit.warnings)
    except HTTPException as exc:
        response.warnings.append(str(exc.detail))

    return response


@router.post("/academy-cluster", response_model=AcademyClusterResponse)
async def academy_cluster(request: AcademyClusterRequest):
    return await run_academy_cluster_analysis(request)
