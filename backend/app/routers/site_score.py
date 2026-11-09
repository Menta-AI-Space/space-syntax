import asyncio
import logging
import math

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.errors import (
    no_roads_error,
    road_fetch_error,
    segment_cap_warning,
    too_many_segments_error,
)
from app.models.schemas import (
    AgeBandOut,
    DongMarketContextOut,
    ApartmentMarketOut,
    CompetitionOut,
    DemandOut,
    ExplanationOut,
    GenderOut,
    HousingMixOut,
    IndustryKpiOut,
    IndustryOut,
    NearestSegmentOut,
    PersonalFitRequest,
    PersonalFitOut,
    PriorityNoteOut,
    SalesBenchmarkOut,
    SeoulRankOut,
    SeoulRelativeOut,
    SiteScoreRequest,
    SiteScoreResponse,
    SubScoreOut,
    TransitOut,
)
from app.services import site_score as scoring
from app.services.analysis_cache import (
    PostgresAnalysisCache,
    build_cache_key,
    cached_baseline,
    cap_analysis_segments,
    snap_to_analysis_grid,
)
from app.services.demand_benchmark import (
    dong_market_context_for_point,
    seoul_rank_for_profile,
)
from app.services.demand_radius import scale_demand_profile_to_radius
from app.services.depthmapx_engine import compute_depthmapx_metrics
from app.services.dong_center import resolve_unique_dong
from app.services.geocoding import GeocodingError, geocode_address
from app.services.graph_builder import analysis_point_on_primary_network, build_graph
from app.services.isochrone import compute_isochrones
from app.services.housing_market import fetch_apartment_market
from app.services.industry_kpis import KpiInputs, compute_industry_kpis
from app.services.living_pop import (
    combined_with_foreign,
    dinner_average,
    fetch_living_pop_foreign_profile,
    fetch_living_pop_profile,
    late_night_average,
    lunch_average,
    morning_commute_average,
    weekend_prime_average,
)
from app.services.ev_demand import ev_demand_for_point
from app.services.llm_explain import explain_site_score, rule_based_explanation
from app.services.opinet import fuel_position_for_point
from app.services.personal_fit import compute_personal_fit
from app.services.profile_resolver import ProfileResolver
from app.services.sbiz_stores import SbizError, fetch_stores_in_radius
from app.services.sgis import SgisError, fetch_demand_profile
from app.services.space_syntax import WALK, compute_metrics, extend_unique_warnings
from app.services.spatial_query import (
    clinic_weekend_supply,
    ev_charger_supply,
    fetch_roads_in_radius,
    get_connection,
    nearest_school_distance_m,
)
from app.services.sbiz_upjong import get_upjong_tree, resolve_upjong_selection
from app.services.transit import (
    TransitError,
    count_bus_stops,
    fetch_station_scale,
    find_nearest_subway,
)
from app.services.trdar_sales_local import sales_benchmark_for_point

router = APIRouter(prefix="/api", tags=["site-score"])
logger = logging.getLogger(__name__)
analysis_cache = PostgresAnalysisCache(
    get_connection,
    max_entries=settings.analysis_cache_max_entries,
)


@router.post("/personal-fit", response_model=PersonalFitOut)
async def personal_fit(request: PersonalFitRequest):
    """Reweight an existing site score without rerunning spatial analysis."""
    fit = compute_personal_fit(
        base_score=request.base_score,
        subscores=request.subscores,
        base_weights=request.base_weights,
        priorities=request.priorities,
        extra_factor_scores=request.extra_factor_scores,
        priority_boosts=request.priority_boosts,
    )
    return PersonalFitOut(
        base_score=fit.base_score,
        personal_score=fit.personal_score,
        priorities=[
            PriorityNoteOut(key=n.key, label=n.label, applied=n.applied, note=n.note)
            for n in fit.priorities
        ],
        strengths=fit.strengths,
        cautions=fit.cautions,
        weights_used=fit.weights_used,
    )


def _relative_out(rel) -> SeoulRelativeOut | None:
    """서울 425개 동 분포에서의 위치. 값이 없으면 통째로 생략한다."""
    if rel is None:
        return None
    return SeoulRelativeOut(
        value=rel.value,
        top_pct=rel.top_pct,
        seoul_median=rel.seoul_median,
        vs_median_pct=rel.vs_median_pct,
    )


def _market_context_out(ctx) -> DongMarketContextOut | None:
    """유동인구 구성·지역 소비 → 응답 스키마.

    절대수만 주면 좋은지 나쁜지 알 수 없어서, 값마다 '서울 상위 %'와 '중앙값
    대비 %'를 함께 담는다. 성별×연령 교차(20대 여성)는 공개 데이터에 없어
    성별과 연령대를 각각 따로 낸다 — 곱해서 지어내지 않는다.
    """
    if ctx is None:
        return None
    return DongMarketContextOut(
        dong_name=ctx.dong_name,
        quarter=ctx.quarter,
        footfall_daily=_relative_out(ctx.footfall_daily),
        female_pct=_relative_out(ctx.female_pct),
        age_pcts={k: _relative_out(v) for k, v in (ctx.age_pcts or {}).items() if v},
        local_spend_monthly=_relative_out(ctx.local_spend_monthly),
    )


def _warning_text(e: BaseException) -> str:
    """str(e) with the type name as fallback — asyncio.TimeoutError stringifies empty."""
    text = str(e).strip()
    return text or type(e).__name__


def _demand_to_out(
    profile,
    *,
    demographic_fit: float | None,
    demographic_fit_label: str,
    seoul_rank,
    apartment_market,
    market_context=None,
    radius_m: int,
    dong_population: int | None = None,
    dong_workers: int | None = None,
    dong_households: int | None = None,
) -> DemandOut:
    return DemandOut(
        adm_cd=profile.adm_cd,
        adm_name=profile.adm_name,
        population=profile.population,
        households=profile.households,
        companies=profile.companies,
        workers=profile.workers,
        radius_m=radius_m,
        dong_population=dong_population,
        dong_workers=dong_workers,
        dong_households=dong_households,
        avg_age=profile.avg_age,
        population_density=profile.population_density,
        age_bands=[
            AgeBandOut(band=b.band, count=b.count, pct=b.pct) for b in profile.age_bands
        ],
        gender=(
            GenderOut(
                male_pct=profile.gender.male_pct,
                female_pct=profile.gender.female_pct,
                male_count=profile.gender.male_count,
                female_count=profile.gender.female_count,
            )
            if profile.gender
            else None
        ),
        housing=(
            HousingMixOut(
                apartment_pct=profile.housing.apartment_pct,
                officetel_pct=profile.housing.officetel_pct,
                row_house_pct=profile.housing.row_house_pct,
                detached_pct=profile.housing.detached_pct,
                apartment_count=profile.housing.apartment_count,
                officetel_count=profile.housing.officetel_count,
            )
            if profile.housing
            else None
        ),
        industries=[
            IndustryOut(
                code=i.code, name=i.name, companies=i.companies, workers=i.workers
            )
            for i in profile.industries
        ],
        seoul_rank=(
            SeoulRankOut(
                population_top_pct=seoul_rank.population_top_pct,
                workers_top_pct=seoul_rank.workers_top_pct,
                youth_top_pct=seoul_rank.youth_top_pct,
            )
            if seoul_rank
            else None
        ),
        apartment_market=(
            ApartmentMarketOut(
                median_price_per_sqm=apartment_market.median_price_per_sqm,
                sample_count=apartment_market.sample_count,
                scope=apartment_market.scope,
                vs_seoul_pct=apartment_market.vs_seoul_pct,
                seoul_median_per_sqm=apartment_market.seoul_median_per_sqm,
            )
            if apartment_market
            else None
        ),
        market_context=_market_context_out(market_context),
        demographic_fit=demographic_fit,
        demographic_fit_label=demographic_fit_label,
        data_scope=profile.data_scope,
    )


def _demand_facts_for_llm(demand: DemandOut | None) -> str | None:
    if demand is None:
        return None
    lines = [
        f"배후 수요 ({demand.data_scope}): {demand.adm_name}",
        f"반경 {demand.radius_m or '?'}m 추정 인구 {demand.population or '—'}명, "
        f"종사자 {demand.workers or '—'}명, 가구 {demand.households or '—'}",
    ]
    if demand.avg_age is not None:
        lines.append(f"평균 연령 {demand.avg_age}세")
    if demand.gender:
        lines.append(
            f"성별 남 {demand.gender.male_pct}% / 여 {demand.gender.female_pct}%"
        )
    if demand.age_bands:
        top = max(demand.age_bands, key=lambda b: b.pct)
        lines.append(f"최다 연령대 {top.band} ({top.pct}%)")
    mc = demand.market_context
    if mc:
        # 절대수만 던지지 않는다 — 서울 425개 동 중 어디쯤인지가 판단에 쓰인다.
        if mc.footfall_daily and mc.footfall_daily.top_pct is not None:
            lines.append(
                f"유동인구 하루 평균 {mc.footfall_daily.value:,.0f}명 "
                f"(서울 상위 {mc.footfall_daily.top_pct}%)"
            )
        if mc.female_pct and mc.female_pct.top_pct is not None:
            lines.append(
                f"유동인구 중 여성 {mc.female_pct.value:.0f}% "
                f"(서울 상위 {mc.female_pct.top_pct}%)"
            )
        # 상위 30% 안에 드는 연령대만 — 전 구간을 나열하면 서술이 표가 된다.
        strong_ages = [
            f"{band} {rel.value:.0f}%(상위 {rel.top_pct}%)"
            for band, rel in (mc.age_pcts or {}).items()
            if rel and rel.top_pct is not None and rel.top_pct <= 30
        ]
        if strong_ages:
            lines.append("유동인구 중 두드러진 연령대: " + ", ".join(strong_ages))
        sp = mc.local_spend_monthly
        if sp and sp.top_pct is not None:
            lines.append(
                f"이 지역에서 일어나는 월 카드소비 규모 서울 상위 {sp.top_pct}% "
                "(주민 구매력이 아니라 지역에서 발생한 소비다)"
            )
    if demand.seoul_rank:
        sr = demand.seoul_rank
        if sr.population_top_pct is not None:
            lines.append(f"서울 인구 상위 {sr.population_top_pct}%")
        if sr.workers_top_pct is not None:
            lines.append(f"서울 종사자 상위 {sr.workers_top_pct}%")
    if demand.apartment_market and demand.apartment_market.median_price_per_sqm:
        # 절대값만 주면 LLM도 사용자도 높낮이를 못 잡는다 — 서울 대비 상대치를 함께.
        vs = demand.apartment_market.vs_seoul_pct
        rel = ""
        if vs is not None:
            rel = f", 서울 평균 대비 {vs:+d}% ({'높음' if vs > 5 else '낮음' if vs < -5 else '비슷'})"
        lines.append(
            f"아파트 매매 중앙값 약 {demand.apartment_market.median_price_per_sqm:,.0f}만원/㎡"
            f"{rel} ({demand.apartment_market.scope})"
        )
    if demand.demographic_fit is not None:
        lines.append(
            f"업종 연령 적합도 {demand.demographic_fit}점 ({demand.demographic_fit_label})"
        )
    return "\n".join(lines)


def _spatial_facts_for_llm(
    integration_top: float,
    choice_top: float,
    depth_top: float,
    connectivity_top: float,
    connectivity_degree: int,
) -> str:
    return (
        "도로 구조 (분석 반경 내 순위 — 상위%가 낮을수록 좋음):\n"
        f"- 찾아오기 쉬움: 상위 {integration_top}%\n"
        f"- 지나가는 길: 상위 {choice_top}%\n"
        f"- 도로망 중심성: 상위 {depth_top}%\n"
        f"- 눈에 띄는 위치: 상위 {connectivity_top}% (연결 {connectivity_degree}갈래)"
    )


@router.post("/site-score", response_model=SiteScoreResponse)
async def site_score(request: SiteScoreRequest):
    warnings: list[str] = []

    if request.lat is not None and request.lng is not None:
        lat, lng = request.lat, request.lng
    elif request.address:
        # '신촌'처럼 동네 이름만 오면 행정동 상권 중심을 먼저 쓴다. Kakao 주소검색은
        # 행정동 기하 중심을 돌려주는데, 신촌동은 그 지점이 연세대 캠퍼스 안이라
        # 반경 500m 점포가 189곳뿐이고 "동일업종 0곳 · 경쟁 매우 낮음"이라는 정반대
        # 분석이 나왔다(상권 중심은 2,408곳 · 78곳 · 매우 높음). 지번이 붙은 주소나
        # 여러 구에 같은 이름이 있는 경우는 건드리지 않고 기존 지오코딩에 맡긴다.
        dong = await run_in_threadpool(resolve_unique_dong, request.address)
        if dong is not None:
            lat, lng = dong.lat, dong.lng
        else:
            try:
                lat, lng = await geocode_address(request.address)
            except GeocodingError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
    else:
        raise HTTPException(status_code=400, detail="lat/lng or address is required")

    tree = await get_upjong_tree()
    upjong = resolve_upjong_selection(
        inds_lcls_cd=request.inds_lcls_cd,
        inds_mcls_cd=request.inds_mcls_cd,
        inds_scls_cd=request.inds_scls_cd,
        business_type=request.business_type,
        tree=tree,
    )

    # The main UI calls `/api/analyze` immediately before this endpoint. Reuse
    # its persisted angular metrics instead of calculating the same road
    # network twice for one click.
    segments = None
    metrics = None
    G = None
    edge_map = None
    desired_engine = "depthmapx" if settings.depthmapx_enabled else "python"
    if settings.analysis_cache_enabled:
        # Snap to the same grid as /api/analyze so this reuses the road-network
        # entry it just stored instead of recomputing the whole network.
        snapped_lng, snapped_lat = snap_to_analysis_grid(lng, lat)
        cache_key = build_cache_key(
            snapped_lng,
            snapped_lat,
            request.radius_m,
            WALK,
            settings.analysis_cache_network_version,
            settings.analysis_cache_version,
            desired_engine,
            settings.interactive_max_segments,
        )
        try:
            cached = await run_in_threadpool(analysis_cache.get, cache_key)
            if cached is not None:
                cached_segments, cached_walk = cached_baseline(cached, WALK)
                if cached_segments:
                    segments = cached_segments
                    metrics = {WALK: cached_walk}
                    G, edge_map = await run_in_threadpool(build_graph, segments)
                    extend_unique_warnings(warnings, cached.get("warnings") or [])
        except Exception as exc:
            logger.warning("Site-score analysis cache read failed: %s", exc)

    if segments is None:
        try:
            segments = await run_in_threadpool(
                fetch_roads_in_radius, lng, lat, request.radius_m
            )
        except Exception as e:
            raise road_fetch_error(e, lat=lat, lng=lng) from e

        if not segments:
            raise no_roads_error()
        segments, original_segment_count = cap_analysis_segments(
            segments, lng, lat, settings.interactive_max_segments
        )
        if len(segments) < original_segment_count:
            warnings.append(
                segment_cap_warning(len(segments), original_segment_count)
            )
        if len(segments) > settings.max_segments:
            raise too_many_segments_error(len(segments))
        G, edge_map = await run_in_threadpool(build_graph, segments)

    if G is None or edge_map is None:
        G, edge_map = await run_in_threadpool(build_graph, segments)

    if metrics is None:
        on_primary = analysis_point_on_primary_network(G, lng, lat, attr="walkable")
        depthmapx_metrics, depthmapx_error = await run_in_threadpool(
            compute_depthmapx_metrics, G, segments, modes=(WALK,)
        )
        if depthmapx_metrics is not None:
            metrics = depthmapx_metrics
        elif settings.depthmapx_enabled and settings.depthmapx_strict:
            raise HTTPException(
                status_code=503,
                detail=(
                    "DepthmapX engine failed and strict mode disables the "
                    f"Python fallback: {depthmapx_error or 'unknown error'}"
                ),
            )
        else:
            metrics, metric_warnings = await run_in_threadpool(
                compute_metrics,
                G, segments, modes=(WALK,), warn_about_fragments=not on_primary,
            )
            extend_unique_warnings(warnings, metric_warnings)
            if depthmapx_error:
                warnings.append(f"DepthmapX 미사용 (Python 엔진으로 대체, 느릴 수 있음): {depthmapx_error}")

    nearest = scoring.nearest_segment(segments, lat, lng)
    if nearest is None:
        raise HTTPException(status_code=404, detail="누른 지점 근처에서 도로를 찾지 못했습니다.")

    walk = metrics[WALK]
    seg_idx = nearest.index
    integration_values = list(walk["integration"].values())
    choice_values = list(walk["choice"].values())
    depth_values = list(walk["mean_depth"].values())
    connectivity_values = list(walk["connectivity"].values())
    integration_top = scoring.top_percent(
        integration_values, walk["integration"].get(seg_idx, 0.0)
    )
    choice_top = scoring.top_percent(
        choice_values, walk["choice"].get(seg_idx, 0.0)
    )
    depth_top = scoring.bottom_percent(
        depth_values, walk["mean_depth"].get(seg_idx, 0.0)
    )
    connectivity_top = scoring.top_percent(
        connectivity_values, walk["connectivity"].get(seg_idx, 0.0)
    )
    connectivity_degree = int(round(walk["connectivity"].get(seg_idx, 0.0)))

    # 독립적인 I/O(지하철·상가정보·수요 프로파일)를 동시에 시작한다. 이렇게 하면
    # 전체 지연이 각 호출의 합이 아니라 최댓값이 된다(예전엔 순차 await였음).
    # 상가정보→isochrone, 수요→순위/주거시장 의존관계는 아래에서 순서대로 await한다.
    subway_task = asyncio.ensure_future(
        asyncio.wait_for(find_nearest_subway(lat, lng), timeout=2.5)
    )
    stores_task = asyncio.ensure_future(
        asyncio.wait_for(
            fetch_stores_in_radius(lat, lng, request.radius_m), timeout=12.0
        )
    )
    demand_task = asyncio.ensure_future(
        asyncio.wait_for(fetch_demand_profile(lat, lng), timeout=15.0)
    )

    station = None
    try:
        station = await subway_task
    except Exception as e:
        warnings.append(f"교통 접근: {_warning_text(e)}")

    # 역 규모(일평균 승하차·노선 수)와 버스정류장 수. 둘 다 로컬 PostGIS 조회라
    # 빠르고, 실패하면 None/0으로 떨어져 거리만으로 점수가 계산된다.
    station_scale = None
    if station is not None:
        station_scale = await run_in_threadpool(fetch_station_scale, station.name)
    bus_stops_300m = await run_in_threadpool(count_bus_stops, lat, lng, 300)

    stores = []
    try:
        stores = await stores_task
    except (SbizError, TimeoutError) as e:
        warnings.append(f"상가(상권)정보 API: {_warning_text(e)}")
    cat_match = scoring.match_same_category(
        stores,
        inds_mcls_cd=upjong.inds_mcls_cd,
        inds_scls_cd=upjong.inds_scls_cd,
        match_method=upjong.match_method,
    )
    same_category = 0
    competition_reach_unreliable = False
    competition_area_m2: float | None = None
    # Off the event loop (as /api/dashboard already does): this is pure CPU
    # graph work, and blocking here stalls every other in-flight request —
    # which is what serialized concurrent multi-candidate analysis.
    iso_walk5 = await run_in_threadpool(
        compute_isochrones,
        G=G,
        segments=segments,
        lat=lat,
        lng=lng,
        minutes_list=(5,),
        mode="walk",
        stores=stores,
        inds_mcls_cd=upjong.inds_mcls_cd,
        inds_scls_cd=upjong.inds_scls_cd,
    )
    if iso_walk5.warnings:
        extend_unique_warnings(warnings, iso_walk5.warnings)
    if iso_walk5.bands:
        same_category = iso_walk5.bands[0].same_category_count
        competition_area_m2 = iso_walk5.bands[0].area_m2
        # 도달권이 계산됐는데 동일업종이 0곳인데 반경 안에는 여럿 있다면, 도보
        # 도달권이 동네를 못 덮은 것이다(그 지점에서 걷기 도로망이 끊긴 경우).
        # 강남 한 지점이 반경 500m 카페 75곳인데 0곳으로 나왔다 — 그대로 두면
        # "경쟁 없어 진입 유리"라는 정반대 조언이 된다. 지금까지 이 경우는
        # 도달권이 아예 비었을 때만 경고했고, 좁게라도 잡히면 조용히 0이었다.
        if same_category == 0 and cat_match.count > 0:
            # 라벨만 비우고 0곳을 그대로 뒀더니, 경쟁 점수는 만점(경쟁 없음)으로
            # 총점에 들어가고 설명에는 "도보 5분 내 동일업종 0곳"이 사실처럼
            # 적혔다 — 바로 아래 경고가 그 0곳을 못 믿는다고 말하는데도.
            # 못 믿는 값으로 점수를 매기는 대신, 실제로 센 값(반경 안 동일업종)
            # 으로 갈아 끼운다. 면적도 같이 반경 기준으로 바꿔야 밀도가 맞는다.
            competition_reach_unreliable = True
            same_category = cat_match.count
            competition_area_m2 = None
            warnings.append(
                f"이 지점은 보행 도로망이 끊겨 도보 5분 도달권을 좁게밖에 잡지 "
                f"못했습니다 — 동일업종 경쟁은 반경 {request.radius_m}m 기준 "
                f"{cat_match.count}곳으로 집계했습니다."
            )
    elif cat_match.match_method != "disabled":
        warnings.append("도보 5분 도달권을 계산하지 못해 동일업종 경쟁을 0곳으로 처리했습니다.")

    demand_profile = None
    seoul_rank = None
    apartment_market = None
    demographic_fit: float | None = None
    demographic_fit_label = ""

    dong_population: int | None = None
    dong_workers: int | None = None
    dong_households: int | None = None
    try:
        raw_profile = await demand_task
        dong_population = raw_profile.population
        dong_workers = raw_profile.workers
        dong_households = raw_profile.households
        demand_profile = scale_demand_profile_to_radius(
            raw_profile,
            request.radius_m,
            lat=lat,
            lng=lng,
        )
        age_map = {b.band: b.pct for b in demand_profile.age_bands}
        demographic_fit, demographic_fit_label = scoring.demographic_fit_score(
            upjong.archetype, age_map
        )
        rank_result, apartment_result = await asyncio.gather(
            asyncio.wait_for(
                seoul_rank_for_profile(
                    demand_profile.adm_cd,
                    demand_profile.population,
                    demand_profile.workers,
                    demand_profile.age_bands,
                ),
                timeout=8.0,
            ),
            asyncio.wait_for(
                fetch_apartment_market(demand_profile.adm_cd, demand_profile.adm_name),
                timeout=8.0,
            ),
            return_exceptions=True,
        )
        if isinstance(rank_result, BaseException):
            warnings.append(f"서울 수요 순위: {_warning_text(rank_result)}")
        else:
            seoul_rank = rank_result
        if isinstance(apartment_result, BaseException):
            warnings.append(f"주거시장 데이터: {_warning_text(apartment_result)}")
        else:
            apartment_market = apartment_result
    except (SgisError, TimeoutError) as e:
        warnings.append(f"수요 데이터: {_warning_text(e)}")

    result = scoring.compute_site_score(
        archetype=upjong.archetype,
        business_label=upjong.label,
        business_type=(
            request.business_type
            if request.business_type not in ("general", "custom")
            else "custom"
        ),
        inds_lcls_cd=upjong.inds_lcls_cd,
        inds_mcls_cd=upjong.inds_mcls_cd,
        inds_scls_cd=upjong.inds_scls_cd,
        upjong_breadcrumb=upjong.breadcrumb,
        match_method=upjong.match_method,
        integration_top_pct=integration_top,
        choice_top_pct=choice_top,
        mean_depth_top_pct=depth_top,
        connectivity_top_pct=connectivity_top,
        connectivity_degree=connectivity_degree,
        nearest_station_m=station.distance_m if station else None,
        station_name=station.name if station else None,
        station_daily_riders=station_scale.daily_total if station_scale else None,
        station_line_count=station_scale.line_count if station_scale else None,
        bus_stops_300m=bus_stops_300m,
        same_category_count=same_category,
        total_store_count=len(stores),
        population=demand_profile.population if demand_profile else None,
        workers=demand_profile.workers if demand_profile else None,
        adm_name=demand_profile.adm_name if demand_profile else None,
        radius_m=request.radius_m,
        demographic_fit=demographic_fit,
        demographic_fit_label=demographic_fit_label,
        competition_area_m2=competition_area_m2,
        competition_scope="radius" if competition_reach_unreliable else "walk5",
    )

    # 유동인구 구성·지역 소비를 서울 425개 동 분포에서의 위치로 함께 낸다.
    # 절대수만 보여 주면 좋은지 나쁜지 알 수 없다는 지적을 반영한 것으로,
    # 조회가 실패해도 분석은 그대로 나가야 하므로 None으로 강등한다.
    market_context = None
    if demand_profile:
        try:
            market_context = await run_in_threadpool(
                dong_market_context_for_point, lat, lng
            )
        except Exception as exc:
            logger.warning("market context lookup failed at (%s, %s): %s", lat, lng, exc)

    demand_out = (
        _demand_to_out(
            demand_profile,
            demographic_fit=demographic_fit,
            demographic_fit_label=demographic_fit_label,
            seoul_rank=seoul_rank,
            apartment_market=apartment_market,
            market_context=market_context,
            radius_m=request.radius_m,
            dong_population=dong_population,
            dong_workers=dong_workers,
            dong_households=dong_households,
        )
        if demand_profile
        else None
    )

    corner = scoring.corner_info(G, edge_map, seg_idx)
    resolver = ProfileResolver.load()
    industry_profile_id = resolver.resolve(
        inds_lcls_cd=upjong.inds_lcls_cd,
        inds_mcls_cd=upjong.inds_mcls_cd,
        inds_scls_cd=upjong.inds_scls_cd,
        business_type=request.business_type,
        archetype=upjong.archetype,
    )
    # 상권 매출 벤치마크를 KPI보다 먼저 계산 — 상권 점포수가 brand_whitespace 입력이 됨
    sales_benchmark_out: SalesBenchmarkOut | None = None
    if industry_profile_id:
        try:
            chain = sales_benchmark_for_point(lat, lng, industry_profile_id)
            if chain is not None:
                trdar_match, trdar_row, citywide = chain
                sales_benchmark_out = SalesBenchmarkOut(
                    trdar_cd=trdar_match.trdar_cd,
                    trdar_nm=trdar_match.trdar_nm,
                    trdar_se_nm=trdar_match.trdar_se_nm,
                    inside_area=trdar_match.contains,
                    distance_m=trdar_match.distance_m,
                    quarter=trdar_row.quarter,
                    monthly_sales_won=trdar_row.monthly_sales_won,
                    store_count=trdar_row.store_count,
                    franchise_count=trdar_row.franchise_count,
                    per_store_monthly_won=trdar_row.per_store_monthly_won,
                    weekend_share_pct=trdar_row.weekend_share_pct,
                    citywide_median_per_store_won=citywide.median_per_store_monthly_won,
                    citywide_trdar_count=citywide.trdar_count,
                    scope=getattr(trdar_row, "scope", "trdar"),
                )
        except Exception as e:
            warnings.append(f"상권 매출 벤치마크: {_warning_text(e)}")

    # 프랜차이즈 브랜드 분석 (request.brand 입력 시)
    brand = (request.brand or "").strip()
    own_brand_count: int | None = None
    nearest_own_brand_m: float | None = None
    own_brand_seoul_count: int | None = None
    if brand:
        brand_lower = brand.lower()
        own_stores = [
            s for s in stores
            if s.lat is not None and s.lon is not None
            and brand_lower in s.bizes_nm.lower()
            and (not upjong.inds_mcls_cd or s.inds_mcls_cd.strip() == upjong.inds_mcls_cd)
        ]
        own_brand_count = len(own_stores)
        if own_stores:
            nearest_own_brand_m = round(
                min(
                    math.hypot(
                        (s.lon - lng) * 111_320 * math.cos(math.radians(lat)),
                        (s.lat - lat) * 111_320,
                    )
                    for s in own_stores
                ),
                1,
            )
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM sbiz_stores "
                        "WHERE bizes_nm ILIKE %s AND (%s = '' OR inds_mcls_cd = %s)",
                        (
                            f"%{brand}%",
                            upjong.inds_mcls_cd or "",
                            upjong.inds_mcls_cd or "",
                        ),
                    )
                    own_brand_seoul_count = int(cur.fetchone()[0])
        except Exception as e:
            warnings.append(f"자사 점포망 조회: {_warning_text(e)}")

    industry_kpis: list[IndustryKpiOut] = []
    foreign_share_pct: float | None = None
    if industry_profile_id:
        # These are all independent synchronous PostGIS lookups. Running them
        # together in the threadpool keeps the event loop free and collapses
        # six sequential round-trips into one wait.
        (
            living_domestic,
            living_foreign,
            clinic_supply,
            ev_supply,
            school_distance_m,
        ) = await asyncio.gather(
            run_in_threadpool(fetch_living_pop_profile, lat, lng, request.radius_m),
            run_in_threadpool(
                fetch_living_pop_foreign_profile, lat, lng, request.radius_m
            ),
            run_in_threadpool(clinic_weekend_supply, lat, lng, request.radius_m),
            run_in_threadpool(ev_charger_supply, lat, lng, request.radius_m),
            run_in_threadpool(nearest_school_distance_m, lat, lng),
        )
        living, foreign_share_pct = combined_with_foreign(living_domestic, living_foreign)
        ev_dem = (
            await run_in_threadpool(ev_demand_for_point, lat, lng)
            if industry_profile_id == "ev_charging"
            else None
        )
        fuel = (
            await run_in_threadpool(fuel_position_for_point, lat, lng)
            if industry_profile_id == "gas_station"
            else None
        )
        kpi_inputs = KpiInputs(
            population=demand_profile.population if demand_profile else None,
            workers=demand_profile.workers if demand_profile else None,
            households=demand_profile.households if demand_profile else None,
            female_pct=(
                demand_profile.gender.female_pct
                if demand_profile and demand_profile.gender
                else None
            ),
            age_band_pcts=(
                {b.band: b.pct for b in demand_profile.age_bands}
                if demand_profile
                else {}
            ),
            integration_top_pct=integration_top,
            choice_top_pct=choice_top,
            connectivity_top_pct=connectivity_top,
            junction_degree=corner.junction_degree,
            is_corner=corner.is_corner,
            nearest_station_m=station.distance_m if station else None,
            nearest_school_m=school_distance_m,
            late_night_pop=late_night_average(living) if living else None,
            weekend_prime_pop=weekend_prime_average(living) if living else None,
            morning_pop=morning_commute_average(living) if living else None,
            lunch_pop=lunch_average(living) if living else None,
            dinner_pop=dinner_average(living) if living else None,
            living_weekday_days=living.weekday_sample_days if living else 0,
            living_weekend_days=living.weekend_sample_days if living else 0,
            clinic_count_hira=clinic_supply[0] if clinic_supply else None,
            clinic_sat_open=clinic_supply[1] if clinic_supply else None,
            clinic_sun_open=clinic_supply[2] if clinic_supply else None,
            ev_charger_count=ev_supply[0] if ev_supply else None,
            ev_fast_charger_count=ev_supply[1] if ev_supply else None,
            ev_sgg_sessions=ev_dem.sessions if ev_dem else None,
            ev_sgg_demand_pct=ev_dem.demand_pct if ev_dem else None,
            fuel_local_avg_won=fuel.local_avg_won if fuel else None,
            fuel_sido_avg_won=fuel.sido_avg_won if fuel else None,
            fuel_station_count=fuel.station_count if fuel else None,
            trdar_per_store_won=(
                sales_benchmark_out.per_store_monthly_won if sales_benchmark_out else None
            ),
            citywide_per_store_won=(
                sales_benchmark_out.citywide_median_per_store_won
                if sales_benchmark_out
                else None
            ),
            total_store_count=len(stores),
            same_category_count=same_category,
            competition_area_m2=competition_area_m2,
            min_same_category_distance_m=scoring.nearest_same_category_distance_m(
                stores,
                lat=lat,
                lng=lng,
                inds_mcls_cd=upjong.inds_mcls_cd,
                inds_scls_cd=upjong.inds_scls_cd,
                match_method=cat_match.match_method,
            ),
            radius_m=request.radius_m,
            match_method=cat_match.match_method,
            foreign_share_pct=foreign_share_pct,
            brand_label=brand,
            own_brand_count_in_radius=own_brand_count,
            nearest_own_brand_m=nearest_own_brand_m,
            own_brand_seoul_count=own_brand_seoul_count,
            trdar_store_count=(
                sales_benchmark_out.store_count if sales_benchmark_out else None
            ),
            trdar_franchise_count=(
                sales_benchmark_out.franchise_count if sales_benchmark_out else None
            ),
        )
        industry_kpis = [
            IndustryKpiOut(
                key=k.key,
                label=k.label,
                score=k.score,
                display=k.display,
                detail=k.detail,
            )
            for k in compute_industry_kpis(
                resolver.profiles[industry_profile_id], kpi_inputs
            )
        ]

    demand_facts = _demand_facts_for_llm(demand_out)
    spatial_facts = _spatial_facts_for_llm(
        integration_top, choice_top, depth_top, connectivity_top, connectivity_degree
    )
    if not request.include_ai_explanation:
        explanation_text = rule_based_explanation(
            result, demand_facts=demand_facts, spatial_facts=spatial_facts
        )
        explanation_source = "rule"
    else:
        try:
            explanation_text, explanation_source = await asyncio.wait_for(
                explain_site_score(
                    result,
                    demand_facts=demand_facts,
                    spatial_facts=spatial_facts,
                ),
                timeout=10.0,
            )
        except TimeoutError:
            explanation_text = rule_based_explanation(
                result, demand_facts=demand_facts, spatial_facts=spatial_facts
            )
            explanation_source = "rule"
            warnings.append("AI 해석 응답이 지연되어 규칙 기반 설명으로 대체했습니다.")

    # 사용자 우선순위 맞춤 점수 (기본 점수는 불변 — 신뢰 기준선)
    base_personalization_weights = scoring._site_feature_weights(upjong.archetype)
    personalization_factors: dict[str, float] = {}
    if demand_profile:
        youth_pct = sum(
            b.pct for b in demand_profile.age_bands if b.band == "20대"
        )
        if seoul_rank and seoul_rank.youth_top_pct is not None:
            personalization_factors["youth"] = round(
                100.0 - seoul_rank.youth_top_pct, 1
            )
        elif youth_pct > 0:
            personalization_factors["youth"] = round(min(100.0, youth_pct * 3.0), 1)
        # 10대는 서울 랭크 지표가 없어 비율만 사용 — 서울 평균(~8%)이 50점
        # 부근이 되도록 20대(×3)보다 높은 배율을 쓴다.
        teen_pct = sum(
            b.pct for b in demand_profile.age_bands if b.band == "10대"
        )
        if teen_pct > 0:
            personalization_factors["teen"] = round(min(100.0, teen_pct * 6.0), 1)

        # 30·40·50대 이상 — 20대와 같은 ×3 배율을 쓴다. 서울의 성인 연령대는
        # 대체로 한 구간이 15~17%라 16.7%가 50점 부근에 놓인다. '50대 이상'은
        # 세 구간(50·60·70+)을 합쳐 서울 평균이 ~33%라 ×1.5로 맞춘다.
        def _band_pct(*names: str) -> float:
            return sum(b.pct for b in demand_profile.age_bands if b.band in names)

        for key, pct, mult in (
            ("age_30s", _band_pct("30대"), 3.0),
            ("age_40s", _band_pct("40대"), 3.0),
            ("age_50plus", _band_pct("50대", "60대", "70세+"), 1.5),
        ):
            if pct > 0:
                personalization_factors[key] = round(min(100.0, pct * mult), 1)

        # 성비는 이미 0~100 스케일이라 그대로 쓴다 — 서울 평균이 50% 부근이라
        # 배율 없이도 "50점 = 평균"으로 읽힌다.
        gender = demand_profile.gender
        if gender is not None:
            if gender.female_pct is not None:
                personalization_factors["female"] = round(float(gender.female_pct), 1)
            if gender.male_pct is not None:
                personalization_factors["male"] = round(float(gender.male_pct), 1)
    if foreign_share_pct is not None:
        personalization_factors["foreign"] = round(
            min(100.0, foreign_share_pct * 5.0), 1
        )
    if (
        sales_benchmark_out
        and sales_benchmark_out.per_store_monthly_won
        and sales_benchmark_out.citywide_median_per_store_won
    ):
        sales_ratio = (
            sales_benchmark_out.per_store_monthly_won
            / sales_benchmark_out.citywide_median_per_store_won
        )
        personalization_factors["sales"] = round(
            min(100.0, 50.0 * sales_ratio), 1
        )

    personal_fit_out: PersonalFitOut | None = None
    if request.priorities:
        fit = compute_personal_fit(
            base_score=result.total_score,
            subscores=result.subscores,
            base_weights=base_personalization_weights,
            priorities=request.priorities,
            extra_factor_scores=personalization_factors,
            priority_boosts=request.priority_boosts,
        )
        personal_fit_out = PersonalFitOut(
            base_score=fit.base_score,
            personal_score=fit.personal_score,
            priorities=[
                PriorityNoteOut(key=n.key, label=n.label, applied=n.applied, note=n.note)
                for n in fit.priorities
            ],
            strengths=fit.strengths,
            cautions=fit.cautions,
            weights_used=fit.weights_used,
        )

    return SiteScoreResponse(
        lat=lat,
        lng=lng,
        radius_m=request.radius_m,
        business_type=result.business_type,
        business_label=result.business_label,
        inds_lcls_cd=result.inds_lcls_cd,
        inds_mcls_cd=result.inds_mcls_cd,
        inds_scls_cd=result.inds_scls_cd,
        upjong_breadcrumb=result.upjong_breadcrumb,
        total_score=result.total_score,
        grade=result.grade,
        verdict_one_liner=result.verdict_one_liner,
        segment=NearestSegmentOut(
            osm_id=nearest.osm_id,
            highway=nearest.highway,
            distance_m=nearest.distance_m,
            integration_top_pct=integration_top,
            choice_top_pct=choice_top,
            mean_depth_top_pct=depth_top,
            connectivity_top_pct=connectivity_top,
            connectivity_degree=connectivity_degree,
        ),
        subscores=[
            SubScoreOut(
                key=s.key,
                label=s.label,
                score=s.score,
                detail=s.detail,
                tooltip=s.tooltip,
                summary_label=s.summary_label,
                display=s.display,
                rank_top_pct=s.rank_top_pct,
            )
            for s in result.subscores
        ],
        transit=(
            TransitOut(station_name=station.name, distance_m=station.distance_m)
            if station
            else None
        ),
        demand=demand_out,
        competition=CompetitionOut(
            same_category_count=same_category,
            total_store_count=len(stores),
            density_per_km2=round(
                scoring.competition_density_per_km2(
                    same_category,
                    radius_m=request.radius_m if competition_area_m2 is None else None,
                    area_m2=competition_area_m2,
                ),
                2,
            ),
            match_method=cat_match.match_method,
            intensity_label=(
                scoring.competition_intensity_label(
                    scoring.competition_density_per_km2(
                        same_category,
                        radius_m=request.radius_m if competition_area_m2 is None else None,
                        area_m2=competition_area_m2,
                    )
                )
                if cat_match.match_method != "disabled"
                else ""
            ),
        ),
        explanation=ExplanationOut(text=explanation_text, source=explanation_source),
        industry_profile_id=industry_profile_id,
        industry_kpis=industry_kpis,
        sales_benchmark=sales_benchmark_out,
        personal_fit=personal_fit_out,
        personalization_weights=base_personalization_weights,
        personalization_factors=personalization_factors,
        foreign_share_pct=(round(foreign_share_pct, 1) if foreign_share_pct is not None else None),
        warnings=warnings,
    )
