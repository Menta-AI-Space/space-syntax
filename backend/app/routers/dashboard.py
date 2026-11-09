"""Dashboard APIs: isochrone, cannibalization, trends, chat."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from app.config import settings
from app.errors import analysis_failed_error, no_roads_error, too_many_segments_error
from app.models.schemas import (
    ApartmentPricesOut,
    AptRecentTradeOut,
    AreaInsightsRequest,
    AreaInsightsResponse,
    CannibalizationResponse,
    FootfallBreakdownOut,
    FootfallProfileOut,
    FootfallSliceOut,
    ChatRequest,
    ChatResponse,
    CompetitorOut,
    DashboardPointRequest,
    DashboardResponse,
    IsochroneBandOut,
    IsochroneResponse,
    StreetSignalOut,
    TerritoryAnalysisResponse,
    TerritoryDemandOut,
    TerritoryPatchOut,
    TerritoryZoneOut,
    TrendSeriesOut,
    TrendsResponse,
)
from app.services.area_insights import fetch_apartment_prices, fetch_footfall_breakdown
from app.services.cannibalization import compute_cannibalization
from app.services.chat_consultation import chat_consultation
from app.services.geocoding import GeocodingError, geocode_address
from app.services.graph_builder import build_graph
from app.services.isochrone import (
    competition_fetch_radius_m,
    compute_isochrones,
    uses_reach_approximation,
)
from app.services.kakao_place_enrich import (
    CompetitorEnrichment,
    enrich_competitors,
    fetch_kakao_photo,
    proxied_photo_url,
)
from app.services.sbiz_stores import SbizError, fetch_stores_in_radius
from app.services.sbiz_upjong import UpjongSelection, get_upjong_tree, resolve_upjong_selection
from app.services.sgis import SgisError, fetch_demand_profile
from app.services.spatial_query import fetch_roads_in_radius
from app.services.store_trends import compute_trends
from app.services.territory_cannibalization import (
    compute_territory_analysis,
    competition_fetch_radius_for_territory,
)

router = APIRouter(prefix="/api", tags=["dashboard"])


async def _cannibalization_response(
    *,
    lat: float,
    lng: float,
    radius_m: int,
    upjong: UpjongSelection,
    segments,
    G,
    stores,
    mode: str = "walk",
    minutes: int = 5,
    brand: str | None = None,
) -> CannibalizationResponse:
    result = await run_in_threadpool(
        compute_cannibalization,
        G=G,
        segments=segments,
        lat=lat,
        lng=lng,
        inds_mcls_cd=upjong.inds_mcls_cd,
        inds_scls_cd=upjong.inds_scls_cd,
        stores=stores,
        radius_m=radius_m,
        mode=mode,
        minutes=minutes,
        brand=brand,
    )
    # Photos/reviews are optional decoration, not a reason to block or fail the
    # whole dashboard when Kakao is slow or temporarily unreachable.
    try:
        enrichments = await asyncio.wait_for(
            enrich_competitors(result.competitors), timeout=2.5
        )
    except Exception:
        enrichments = [CompetitorEnrichment() for _ in result.competitors]
    competitors: list[CompetitorOut] = []
    for comp, extra in zip(result.competitors, enrichments, strict=False):
        competitors.append(
            CompetitorOut(
                name=comp.name,
                category=comp.category,
                distance_m=comp.distance_m,
                address=comp.address,
                lat=comp.lat,
                lng=comp.lng,
                kakao_place_id=extra.kakao_place_id,
                place_url=extra.place_url,
                photo_url=proxied_photo_url(extra.photo_url),
                rating=extra.rating,
                review_count=extra.review_count,
                tagline=extra.tagline,
                phone=extra.phone,
                kakao_category=extra.kakao_category,
            )
        )
    return CannibalizationResponse(
        risk_score=result.risk_score,
        risk_label=result.risk_label,
        competitors=competitors,
        overlap_pct=result.overlap_pct,
        overlap_with=result.overlap_with,
        overlap_recommended_max_pct=result.overlap_recommended_max_pct,
        detail=result.detail,
        mode=result.mode,
        minutes=result.minutes,
        total_count=result.total_count,
        reach_polygon=result.reach_polygon,
        self_total_count=result.self_total_count,
        self_competitors=[
            CompetitorOut(
                name=c.name,
                category=c.category,
                distance_m=c.distance_m,
                address=c.address,
                lat=c.lat,
                lng=c.lng,
            )
            for c in result.self_competitors
        ],
        self_overlap_pct=result.self_overlap_pct,
        demand_factor=result.demand_factor,
    )


@router.get("/place-photo")
async def place_photo(url: str = Query(..., min_length=8)):
    try:
        content, content_type = await fetch_kakao_photo(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="가게 사진을 불러오지 못했습니다.") from exc
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


async def _resolve_point(request: DashboardPointRequest) -> tuple[float, float]:
    if request.lat is not None and request.lng is not None:
        return request.lat, request.lng
    if request.address:
        try:
            return await geocode_address(request.address)
        except GeocodingError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    raise HTTPException(status_code=400, detail="lat/lng or address is required")


async def _resolve_upjong(request: DashboardPointRequest) -> UpjongSelection:
    tree = await get_upjong_tree()
    return resolve_upjong_selection(
        inds_lcls_cd=request.inds_lcls_cd,
        inds_mcls_cd=request.inds_mcls_cd,
        inds_scls_cd=request.inds_scls_cd,
        business_type=request.business_type,
        tree=tree,
    )


def _dashboard_core_fetch_radius(request: DashboardPointRequest) -> int:
    """Road fetch for isochrone bands and selected competitor reach — not delivery territory."""
    iso_minutes = max(request.minutes) if request.minutes else 15
    return max(
        request.radius_m,
        competition_fetch_radius_m(request.radius_m, request.mode, iso_minutes),
        competition_fetch_radius_m(
            request.radius_m, request.mode, request.competitor_minutes
        ),
    )


def _isochrone_fetch_radius(request: DashboardPointRequest) -> int:
    """Road fetch wide enough for every requested isochrone band."""
    iso_minutes = max(request.minutes) if request.minutes else 15
    return max(
        request.radius_m,
        competition_fetch_radius_m(request.radius_m, request.mode, iso_minutes),
    )


async def _load_context(
    lat: float, lng: float, radius_m: int, upjong: UpjongSelection,
    *, mode: str | None = None,
):
    warnings: list[str] = []
    # Wide competition/territory fetches (4~8km) in dense Seoul blow past
    # max_segments if we pull every alley. When the caller knows the mode, drop
    # to that mode's network and — beyond the threshold — to arterials only,
    # which is what actually determines a multi-km drive reach.
    arterial_only = mode is not None and radius_m > settings.arterial_only_radius_threshold_m
    segments = await run_in_threadpool(
        fetch_roads_in_radius, lng, lat, radius_m, mode=mode, arterial_only=arterial_only
    )
    if not segments:
        raise no_roads_error()
    if len(segments) > settings.max_segments:
        raise too_many_segments_error(len(segments))

    # Graph construction, store lookup, and demand lookup are independent.
    # Running them together removes two full network/DB waits from every
    # dashboard request and keeps sync graph work off the event loop.
    graph_result, stores_result, demand_result = await asyncio.gather(
        run_in_threadpool(build_graph, segments),
        fetch_stores_in_radius(lat, lng, radius_m),
        fetch_demand_profile(lat, lng),
        return_exceptions=True,
    )
    if isinstance(graph_result, BaseException):
        raise analysis_failed_error(graph_result, what="도로망 분석")
    G, _ = graph_result

    stores = []
    if isinstance(stores_result, BaseException):
        warnings.append(str(stores_result) or type(stores_result).__name__)
    else:
        stores = stores_result

    demand_profile = None
    if isinstance(demand_result, BaseException):
        warnings.append(str(demand_result) or type(demand_result).__name__)
    else:
        demand_profile = demand_result
    return segments, G, stores, upjong, demand_profile, warnings


async def _load_context_light(
    lat: float, lng: float, radius_m: int, upjong: UpjongSelection,
):
    """Store + demand context with no road fetch and no graph build.

    Vehicular (drive/moto) reach is a Euclidean disc approximation, so nothing
    downstream needs segments or a graph — the multi-km road fetch plus
    build_graph was exactly what blew the 5s budget on these modes. The radius
    only sizes the store lookup (ST_DWithin, fast even at 8km).
    """
    warnings: list[str] = []
    stores_result, demand_result = await asyncio.gather(
        fetch_stores_in_radius(lat, lng, radius_m),
        fetch_demand_profile(lat, lng),
        return_exceptions=True,
    )
    stores = []
    if isinstance(stores_result, BaseException):
        warnings.append(str(stores_result) or type(stores_result).__name__)
    else:
        stores = stores_result

    demand_profile = None
    if isinstance(demand_result, BaseException):
        warnings.append(str(demand_result) or type(demand_result).__name__)
    else:
        demand_profile = demand_result
    return [], None, stores, upjong, demand_profile, warnings


async def _load_reach_context(
    lat: float, lng: float, radius_m: int, upjong: UpjongSelection, *, mode: str,
):
    """Full road/graph context for exact walk/bike reach, light context otherwise."""
    if uses_reach_approximation(mode):
        return await _load_context_light(lat, lng, radius_m, upjong)
    return await _load_context(lat, lng, radius_m, upjong, mode=mode)


@router.post("/isochrone", response_model=IsochroneResponse)
async def isochrone(request: DashboardPointRequest):
    lat, lng = await _resolve_point(request)
    upjong = await _resolve_upjong(request)
    fetch_radius = _isochrone_fetch_radius(request)
    segments, G, stores, _, demand_profile, warnings = await _load_reach_context(
        lat, lng, fetch_radius, upjong, mode=request.mode
    )
    result = await run_in_threadpool(
        compute_isochrones,
        G=G,
        segments=segments,
        lat=lat,
        lng=lng,
        minutes_list=tuple(request.minutes),
        mode=request.mode,
        stores=stores,
        inds_mcls_cd=upjong.inds_mcls_cd,
        inds_scls_cd=upjong.inds_scls_cd,
        dong_population=demand_profile.population if demand_profile else None,
    )
    warnings.extend(result.warnings)
    return IsochroneResponse(
        lat=result.lat,
        lng=result.lng,
        mode=result.mode,
        start_connected=result.start_connected,
        warnings=warnings,
        bands=[
            IsochroneBandOut(
                minutes=b.minutes,
                max_distance_m=b.max_distance_m,
                polygon=b.polygon,
                area_m2=b.area_m2,
                store_count=b.store_count,
                same_category_count=b.same_category_count,
                population_estimate=b.population_estimate,
            )
            for b in result.bands
        ],
    )


def _territory_response(result) -> TerritoryAnalysisResponse:
    return TerritoryAnalysisResponse(
        delivery_mode=result.delivery_mode,
        delivery_minutes=result.delivery_minutes,
        model_fit_mode=result.model_fit_mode,
        model_fit_minutes=result.model_fit_minutes,
        before_territories=[
            TerritoryPatchOut(
                brand=t.brand,
                store_name=t.store_name,
                lat=t.lat,
                lng=t.lng,
                mode=t.mode,
                minutes=t.minutes,
                polygon=t.polygon,
                area_m2=t.area_m2,
                is_new_entrant=t.is_new_entrant,
            )
            for t in result.before_territories
        ],
        after_territories=[
            TerritoryPatchOut(
                brand=t.brand,
                store_name=t.store_name,
                lat=t.lat,
                lng=t.lng,
                mode=t.mode,
                minutes=t.minutes,
                polygon=t.polygon,
                area_m2=t.area_m2,
                is_new_entrant=t.is_new_entrant,
            )
            for t in result.after_territories
        ],
        zones=[
            TerritoryZoneOut(
                zone_type=z.zone_type,
                label=z.label,
                polygon=z.polygon,
                area_m2=z.area_m2,
                brands=z.brands,
                note=z.note,
            )
            for z in result.zones
        ],
        demand=TerritoryDemandOut(
            population=result.demand.population,
            workers=result.demand.workers,
            households=result.demand.households,
            youth_pct=result.demand.youth_pct,
            middle_age_pct=result.demand.middle_age_pct,
            senior_pct=result.demand.senior_pct,
            apartment_pct=result.demand.apartment_pct,
            demand_notes=result.demand.demand_notes,
        ),
        brand_fit_headline=result.brand_fit_headline,
        brand_fit_bullets=result.brand_fit_bullets,
        narrative=result.narrative,
        warnings=result.warnings,
    )


async def _territory_analysis_response(
    *,
    lat: float,
    lng: float,
    radius_m: int,
    upjong: UpjongSelection,
    segments,
    G,
    stores,
    demand_profile,
) -> TerritoryAnalysisResponse | None:
    if upjong.archetype == "education" or (
        upjong.inds_mcls_cd and upjong.inds_mcls_cd.startswith("P10")
    ):
        return None
    result = await run_in_threadpool(
        compute_territory_analysis,
        G=G,
        segments=segments,
        lat=lat,
        lng=lng,
        stores=stores,
        inds_mcls_cd=upjong.inds_mcls_cd,
        inds_scls_cd=upjong.inds_scls_cd,
        archetype=upjong.archetype,
        brand_label=upjong.label or upjong.breadcrumb,
        demand_profile=demand_profile,
    )
    return _territory_response(result)


@router.post("/cannibalization", response_model=CannibalizationResponse)
async def cannibalization(request: DashboardPointRequest):
    lat, lng = await _resolve_point(request)
    upjong = await _resolve_upjong(request)
    fetch_radius = competition_fetch_radius_m(
        request.radius_m, request.mode, request.competitor_minutes
    )
    segments, G, stores, _, _, _ = await _load_reach_context(
        lat, lng, fetch_radius, upjong, mode=request.mode
    )
    return await _cannibalization_response(
        lat=lat,
        lng=lng,
        radius_m=fetch_radius,
        upjong=upjong,
        segments=segments,
        G=G,
        stores=stores,
        mode=request.mode,
        minutes=request.competitor_minutes,
        brand=request.brand,
    )


def _trends_response(result, warnings: list[str]) -> TrendsResponse:
    warnings.extend(result.warnings)
    series_out = None
    if result.series:
        s = result.series
        series_out = TrendSeriesOut(
            category_label=s.category_label,
            quarters=s.quarters,
            category_counts=s.category_counts,
            total_counts=s.total_counts,
            category_growth_pct=s.category_growth_pct,
            total_growth_pct=s.total_growth_pct,
        )
    return TrendsResponse(
        category_label=result.category_label,
        series=series_out,
        street_signals=[
            StreetSignalOut(
                name=x.name,
                label=x.label,
                category_growth_pct=x.category_growth_pct,
                store_growth_pct=x.store_growth_pct,
                detail=x.detail,
                data_scope=x.data_scope,
            )
            for x in result.street_signals
        ],
        data_scope=result.data_scope,
        warnings=warnings,
    )


@router.post("/trends", response_model=TrendsResponse)
async def trends(request: DashboardPointRequest):
    lat, lng = await _resolve_point(request)
    upjong = await _resolve_upjong(request)
    warnings: list[str] = []
    demand_profile = None
    try:
        demand_profile = await asyncio.wait_for(
            fetch_demand_profile(lat, lng), timeout=3.0
        )
    except (SgisError, TimeoutError) as exc:
        warnings.append(str(exc) or type(exc).__name__)
    result = await compute_trends(
        lat,
        lng,
        category_label=upjong.label,
        business_type=request.business_type,
        inds_scls_cd=upjong.inds_scls_cd,
        inds_mcls_cd=upjong.inds_mcls_cd,
        adm_name=demand_profile.adm_name if demand_profile else None,
    )
    return _trends_response(result, warnings)


@router.post("/territory", response_model=TerritoryAnalysisResponse | None)
async def territory(request: DashboardPointRequest):
    """Load the expensive delivery-territory section independently.

    The frontend can render the core dashboard first and fill this section in
    later instead of holding every card behind the widest road-network query.
    """
    lat, lng = await _resolve_point(request)
    upjong = await _resolve_upjong(request)
    territory_radius = competition_fetch_radius_for_territory(request.radius_m)
    # Delivery territory reach is moto — disc-approximated — so only stores
    # and demand are needed; the former 4.5km road/graph load is gone.
    segments, G, stores, _, demand_profile, warnings = await _load_context_light(
        lat, lng, territory_radius, upjong
    )
    response = await _territory_analysis_response(
        lat=lat,
        lng=lng,
        radius_m=territory_radius,
        upjong=upjong,
        segments=segments,
        G=G,
        stores=stores,
        demand_profile=demand_profile,
    )
    if response is not None:
        response.warnings.extend(w for w in warnings if w not in response.warnings)
    return response


@router.post("/dashboard", response_model=DashboardResponse)
async def dashboard_bundle(request: DashboardPointRequest):
    lat, lng = await _resolve_point(request)
    upjong = await _resolve_upjong(request)
    core_radius = _dashboard_core_fetch_radius(request)
    segments, G, stores, _, demand_profile, warnings = await _load_reach_context(
        lat, lng, core_radius, upjong, mode=request.mode
    )

    # The three core dashboard sections are independent once the shared road,
    # store, and demand context is loaded. Compute them concurrently and reuse
    # that context instead of calling the heavy `/trends` route recursively.
    iso_result, cann, trend_result = await asyncio.gather(
        run_in_threadpool(
            compute_isochrones,
            G=G,
            segments=segments,
            lat=lat,
            lng=lng,
            minutes_list=tuple(request.minutes),
            mode=request.mode,
            stores=stores,
            inds_mcls_cd=upjong.inds_mcls_cd,
            inds_scls_cd=upjong.inds_scls_cd,
            dong_population=demand_profile.population if demand_profile else None,
        ),
        _cannibalization_response(
            lat=lat,
            lng=lng,
            radius_m=core_radius,
            upjong=upjong,
            segments=segments,
            G=G,
            stores=stores,
            mode=request.mode,
            minutes=request.competitor_minutes,
            brand=request.brand,
        ),
        compute_trends(
            lat,
            lng,
            category_label=upjong.label,
            business_type=request.business_type,
            inds_scls_cd=upjong.inds_scls_cd,
            inds_mcls_cd=upjong.inds_mcls_cd,
            adm_name=demand_profile.adm_name if demand_profile else None,
        ),
    )
    iso = IsochroneResponse(
        lat=iso_result.lat,
        lng=iso_result.lng,
        mode=iso_result.mode,
        start_connected=iso_result.start_connected,
        warnings=warnings + iso_result.warnings,
        bands=[
            IsochroneBandOut(
                minutes=b.minutes,
                max_distance_m=b.max_distance_m,
                polygon=b.polygon,
                area_m2=b.area_m2,
                store_count=b.store_count,
                same_category_count=b.same_category_count,
                population_estimate=b.population_estimate,
            )
            for b in iso_result.bands
        ],
    )
    trend = _trends_response(trend_result, list(warnings))

    territory: TerritoryAnalysisResponse | None = None
    if request.include_territory:
        territory_radius = competition_fetch_radius_for_territory(request.radius_m)
        if territory_radius <= core_radius:
            territory = await _territory_analysis_response(
                lat=lat,
                lng=lng,
                radius_m=core_radius,
                upjong=upjong,
                segments=segments,
                G=G,
                stores=stores,
                demand_profile=demand_profile,
            )
        else:
            # Moto territory reach is disc-approximated: only a wider store
            # lookup is needed, never the 4.5km road network that used to
            # either take seconds or exceed the segment cap.
            t_segments, t_G, t_stores, _, t_demand, t_warnings = (
                await _load_context_light(lat, lng, territory_radius, upjong)
            )
            territory = await _territory_analysis_response(
                lat=lat,
                lng=lng,
                radius_m=territory_radius,
                upjong=upjong,
                segments=t_segments,
                G=t_G,
                stores=t_stores,
                demand_profile=t_demand or demand_profile,
            )
            if territory is not None:
                territory.warnings = t_warnings + territory.warnings

    return DashboardResponse(
        isochrone=iso, cannibalization=cann, trends=trend, territory=territory
    )


@router.post("/area-insights", response_model=AreaInsightsResponse)
async def area_insights(request: AreaInsightsRequest):
    """주변 아파트 실거래 시세 + 생활인구(유동인구) 시간대 프로필.

    둘 다 로컬 PostGIS 조회(RTMS real_estate_trades, 서울 생활인구
    living_pop_slot)라 가볍고, 데이터가 없으면 해당 필드만 None이다.
    """
    from app.services.living_pop import fetch_living_pop_profile

    radius = max(150, min(int(request.radius_m), 2000))

    apt_stats, living, breakdown = await asyncio.gather(
        run_in_threadpool(fetch_apartment_prices, request.lat, request.lng, radius),
        run_in_threadpool(fetch_living_pop_profile, request.lat, request.lng, radius),
        run_in_threadpool(fetch_footfall_breakdown, request.lat, request.lng),
    )

    apartment = None
    if apt_stats is not None:
        apartment = ApartmentPricesOut(
            trade_count=apt_stats.trade_count,
            median_per_sqm_manwon=apt_stats.median_per_sqm_manwon,
            median_per_pyeong_manwon=apt_stats.median_per_pyeong_manwon,
            scope=apt_stats.scope,
            scope_kind=apt_stats.scope_kind,
            recent=[
                AptRecentTradeOut(
                    bldg_nm=t.bldg_nm,
                    umd_nm=t.umd_nm,
                    area_sqm=t.area_sqm,
                    floor=t.floor,
                    deal_amount_manwon=t.deal_amount_manwon,
                    deal_date=t.deal_date,
                )
                for t in apt_stats.recent
            ],
        )

    footfall = None
    if living is not None and (living.weekday_hourly or living.weekend_hourly):
        weekday = [round(living.weekday_hourly.get(h, 0.0), 1) for h in range(24)]
        weekend = [round(living.weekend_hourly.get(h, 0.0), 1) for h in range(24)]
        peak_hour = max(range(24), key=lambda h: weekday[h]) if any(weekday) else None
        footfall = FootfallProfileOut(
            weekday_hourly=weekday,
            weekend_hourly=weekend,
            weekday_peak_hour=peak_hour,
        )

    breakdown_out = None
    if breakdown is not None and breakdown.total > 0:
        gender_total = breakdown.male + breakdown.female
        age_total = sum(v for _, v in breakdown.ages) or None
        breakdown_out = FootfallBreakdownOut(
            adm_nm=breakdown.adm_nm,
            quarter=breakdown.quarter,
            total=breakdown.total,
            male_pct=round(breakdown.male / gender_total * 100, 1) if gender_total else None,
            female_pct=round(breakdown.female / gender_total * 100, 1) if gender_total else None,
            ages=[
                FootfallSliceOut(label=label, value=value, pct=round(value / age_total * 100, 1) if age_total else None)
                for label, value in breakdown.ages
            ],
            days=[FootfallSliceOut(label=label, value=value) for label, value in breakdown.days],
        )

    return AreaInsightsResponse(apartment=apartment, footfall=footfall, footfall_breakdown=breakdown_out)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    context_parts = []
    if request.lat is not None and request.lng is not None:
        dash_req = DashboardPointRequest(
            lat=request.lat,
            lng=request.lng,
            business_type=request.business_type,
            inds_mcls_cd=getattr(request, "inds_mcls_cd", None),
            inds_scls_cd=getattr(request, "inds_scls_cd", None),
            radius_m=500,
        )
        try:
            bundle = await dashboard_bundle(dash_req)
            iso = bundle.isochrone
            cann = bundle.cannibalization
            context_parts.append(
                f"업종: {bundle.trends.category_label}\n"
                f"5분 도보권 점포 {iso.bands[0].store_count if iso.bands else 0}곳\n"
                f"잠식 위험 {cann.risk_score}/100 ({cann.risk_label})\n"
                f"동일업종 경쟁 {len(cann.competitors)}곳"
            )
            if cann.overlap_pct is not None:
                context_parts.append(
                    f"최근접 {cann.overlap_with} 도달권 겹침 {cann.overlap_pct}%"
                )
        except HTTPException:
            pass
    context = "\n".join(context_parts) if context_parts else None
    reply, source = await chat_consultation(request, context=context)
    return ChatResponse(reply=reply, source=source)
