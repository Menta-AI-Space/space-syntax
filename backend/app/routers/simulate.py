import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.errors import no_roads_error, road_fetch_error, too_many_segments_error
from app.models.schemas import (
    CenterPoint,
    ExplanationOut,
    NewRoadMetricOut,
    SimulateRequest,
    SimulateResponse,
    SimulationContextRequest,
    SimulationContextResponse,
    SimulationMetricOut,
    SimulationImpactRoadOut,
)
from app.services.analysis_cache import (
    PostgresAnalysisCache,
    build_cache_key,
    cap_analysis_segments,
    cached_baseline,
    snap_to_analysis_grid,
)
from app.services.geocoding import GeocodingError, geocode_address
from app.services.stats import build_baseline_geojson
from app.services.simulation import (
    LOCAL_IMPACT_RADIUS_M,
    SimulationError,
    VirtualRoad,
    impact_candidate_ids,
    rule_based_simulation_explanation,
    run_simulation,
    top_improved,
    top_worsened,
)
from app.services.space_syntax import WALK
from app.services.spatial_query import (
    fetch_road_names,
    fetch_roads_in_radius,
    get_connection,
)

router = APIRouter(prefix="/api", tags=["simulation"])
logger = logging.getLogger(__name__)
analysis_cache = PostgresAnalysisCache(
    get_connection,
    max_entries=settings.analysis_cache_max_entries,
)


@router.post("/simulation/context", response_model=SimulationContextResponse)
async def simulation_context(request: SimulationContextRequest):
    """Fetch baseline road network for the simulation map (no metrics)."""
    try:
        if request.lat is not None and request.lng is not None:
            lat, lng = request.lat, request.lng
        else:
            lat, lng = await geocode_address(request.address or "")
    except GeocodingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        segments = await run_in_threadpool(
            fetch_roads_in_radius, lng, lat, request.radius_m
        )
    except Exception as e:
        raise road_fetch_error(e, lat=request.lat, lng=request.lng) from e

    if not segments:
        raise no_roads_error()
    if len(segments) > settings.max_segments:
        raise too_many_segments_error(len(segments))

    return SimulationContextResponse(
        center=CenterPoint(lat=lat, lng=lng),
        radius_m=request.radius_m,
        segment_count=len(segments),
        segments=build_baseline_geojson(segments),
    )


def _segments_geojson(result) -> dict:
    """GeoJSON for the delta view: existing segments + drawn virtual roads."""
    features = []
    for d in result.segments:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": d.coordinates},
                "properties": {
                    "virtual": False,
                    "osm_id": d.osm_id,
                    "highway": d.highway,
                    "integration_before": round(d.integration_before, 6),
                    "integration_after": round(d.integration_after, 6),
                    "choice_before": round(d.choice_before, 6),
                    "choice_after": round(d.choice_after, 6),
                    "integration_delta_pct": d.integration_delta_pct,
                    "choice_delta_pct": d.choice_delta_pct,
                    "changed": d.changed,
                    "closed": d.closed,
                    "no_data": d.no_data,
                },
            }
        )
    new_road_by_id = {road.osm_id: road for road in result.new_roads}
    for seg in result.virtual_segments:
        new_road = new_road_by_id.get(seg.osm_id)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": seg.coordinates},
                "properties": {
                    "virtual": True,
                    "osm_id": seg.osm_id,
                    "highway": seg.highway,
                    "length_m": round(seg.length_m, 1),
                    "integration_after": (
                        round(new_road.integration, 6) if new_road else None
                    ),
                    "choice_after": round(new_road.choice, 6) if new_road else None,
                    "integration_top_pct": (
                        new_road.integration_top_pct if new_road else None
                    ),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@router.post("/simulate", response_model=SimulateResponse)
async def simulate(request: SimulateRequest):
    segments = []
    before_metrics = None
    before_engine = None
    cache_warning = None
    desired_engine = "depthmapx" if settings.depthmapx_enabled else "python"
    if settings.analysis_cache_enabled:
        # Snap to the analyze/precompute grid so the baseline network is reused
        # instead of recomputed for the simulation's "before" state.
        snapped_lng, snapped_lat = snap_to_analysis_grid(request.lng, request.lat)
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
                metadata = cached.get("_cache") or {}
                if metadata.get("engine") == desired_engine:
                    segments, before_metrics = cached_baseline(cached, WALK)
                    before_engine = desired_engine
                    known_ids = {segment.osm_id for segment in segments}
                    if not set(request.closed_osm_ids).issubset(known_ids):
                        segments = []
                        before_metrics = None
                        before_engine = None
        except Exception as exc:
            cache_warning = f"Persistent analysis cache unavailable: {exc}"

    try:
        if not segments:
            segments = await run_in_threadpool(
                fetch_roads_in_radius, request.lng, request.lat, request.radius_m
            )
            segments, original_segment_count = cap_analysis_segments(
                segments,
                request.lng,
                request.lat,
                settings.interactive_max_segments,
            )
            if len(segments) < original_segment_count:
                # 같은 안내가 입지 분석에서는 한국어인데 여기만 영어라, 신설도로
                # 시뮬레이션 화면에만 영어 경고가 떴다. 문구를 맞춘다.
                cache_warning = (
                    f"빠른 분석을 위해 가까운 도로 {len(segments):,}개만 사용했습니다 "
                    f"(전체 {original_segment_count:,}개)."
                )
    except Exception as e:
        raise road_fetch_error(e, lat=request.lat, lng=request.lng) from e

    if not segments:
        raise no_roads_error()
    if len(segments) > settings.max_segments:
        raise too_many_segments_error(len(segments))

    roads = [
        VirtualRoad(coordinates=r.coordinates, road_type=r.road_type)
        for r in request.roads
    ]

    try:
        result = await run_in_threadpool(
            run_simulation,
            segments,
            roads,
            request.closed_osm_ids,
            before_metrics,
            before_engine,
        )
    except SimulationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if cache_warning:
        result.warnings.append(cache_warning)

    names: dict[int, str] = {}
    try:
        candidate_ids = impact_candidate_ids(result)
        if candidate_ids:
            names = await run_in_threadpool(fetch_road_names, candidate_ids)
    except Exception as exc:
        logger.warning("Failed to resolve simulation road names: %s", exc)
        result.warnings.append("도로명 조회에 실패해 OSM ID로 표시합니다.")

    top = top_improved(result, names=names)
    worsened = top_worsened(result, names=names)
    explanation = rule_based_simulation_explanation(result, top)

    return SimulateResponse(
        lat=request.lat,
        lng=request.lng,
        radius_m=request.radius_m,
        integration=SimulationMetricOut(
            display_before=result.integration.display_before,
            display_after=result.integration.display_after,
            change_pct=result.integration.change_pct,
        ),
        choice=SimulationMetricOut(
            display_before=result.choice.display_before,
            display_after=result.choice.display_after,
            change_pct=result.choice.change_pct,
        ),
        local_integration=(
            SimulationMetricOut(
                display_before=result.local_integration.display_before,
                display_after=result.local_integration.display_after,
                change_pct=result.local_integration.change_pct,
            )
            if result.local_integration
            else None
        ),
        local_choice=(
            SimulationMetricOut(
                display_before=result.local_choice.display_before,
                display_after=result.local_choice.display_after,
                change_pct=result.local_choice.change_pct,
            )
            if result.local_choice
            else None
        ),
        local_radius_m=int(LOCAL_IMPACT_RADIUS_M),
        local_segment_count=result.local_segment_count,
        new_roads=[
            NewRoadMetricOut(
                osm_id=road.osm_id,
                road_type=road.road_type,
                length_m=round(road.length_m, 1),
                integration=round(road.integration, 6),
                choice=round(road.choice, 6),
                integration_top_pct=road.integration_top_pct,
            )
            for road in result.new_roads
        ],
        improved_count=result.improved_count,
        harmed_count=result.harmed_count,
        segment_count=len(result.segments),
        top_improved=[SimulationImpactRoadOut(**t) for t in top],
        top_worsened=[SimulationImpactRoadOut(**t) for t in worsened],
        segments=_segments_geojson(result),
        explanation=ExplanationOut(text=explanation, source="rule"),
        warnings=result.warnings,
    )
