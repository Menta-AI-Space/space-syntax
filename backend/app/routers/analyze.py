import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.errors import (
    no_roads_error,
    road_fetch_error,
    segment_cap_warning,
    too_many_segments_error,
)
from app.models.schemas import AnalyzeRequest, AnalyzeResponse, CenterPoint
from app.observability import timed_step
from app.services.analysis_cache import (
    PostgresAnalysisCache,
    build_cache_key,
    cap_analysis_segments,
    snap_to_analysis_grid,
)
from app.services.depthmapx_engine import compute_depthmapx_metrics
from app.services.geocoding import GeocodingError, geocode_address
from app.services.graph_builder import analysis_point_on_primary_network, build_graph
from app.services.spatial_query import fetch_roads_in_radius, get_connection
from app.services.space_syntax import DRIVE, METRICS, WALK, compute_metrics, extend_unique_warnings
from app.services.stats import build_geojson

router = APIRouter(prefix="/api", tags=["analysis"])
logger = logging.getLogger(__name__)
analysis_cache = PostgresAnalysisCache(
    get_connection,
    max_entries=settings.analysis_cache_max_entries,
)


def _analysis_cache_key(
    lng: float,
    lat: float,
    request: AnalyzeRequest,
    engine: str,
):
    # Snap ONLY the cache key to the precompute grid (EPSG:5179, 500 m), so
    # nearby/repeat clicks and precomputed grid points hit the same entry.
    # The true click coordinates (lng, lat) still drive the analysis on a miss.
    snapped_lng, snapped_lat = snap_to_analysis_grid(lng, lat)
    return build_cache_key(
        snapped_lng,
        snapped_lat,
        request.radius_m,
        request.mode,
        settings.analysis_cache_network_version,
        settings.analysis_cache_version,
        engine,
        settings.interactive_max_segments,
    )


def _with_aux_defaults(metrics: dict, segment_count: int) -> dict:
    """DepthmapX returns choice/integration; fill optional display metrics cheaply."""
    for mode in (WALK, DRIVE):
        metrics.setdefault(mode, {})
        for metric in METRICS:
            metrics[mode].setdefault(metric, {i: 0.0 for i in range(segment_count)})
    return metrics


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    try:
        if request.lat is not None and request.lng is not None:
            lat, lng = request.lat, request.lng
        else:
            with timed_step(logger, "geocode"):
                lat, lng = await geocode_address(request.address or "")
    except GeocodingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    desired_engine = "depthmapx" if settings.depthmapx_enabled else "python"
    cache_key = _analysis_cache_key(lng, lat, request, desired_engine)
    if settings.analysis_cache_enabled:
        try:
            with timed_step(logger, "cache-read"):
                cached = await run_in_threadpool(analysis_cache.get, cache_key)
            if cached is not None:
                response = AnalyzeResponse.model_validate(cached)
                response.engines["cache"] = {
                    "hit": True,
                    "network_version": settings.analysis_cache_network_version,
                    "analysis_version": settings.analysis_cache_version,
                    "engine": desired_engine,
                }
                return response
        except Exception as exc:
            logger.warning("Persistent analysis cache read failed: %s", exc)

    try:
        with timed_step(logger, "db-fetch"):
            segments = await run_in_threadpool(
                fetch_roads_in_radius, lng, lat, request.radius_m
            )
    except Exception as e:
        raise road_fetch_error(e, lat=lat, lng=lng) from e

    if not segments:
        raise no_roads_error()

    warnings: list[str] = []
    segments, original_segment_count = cap_analysis_segments(
        segments,
        lng,
        lat,
        settings.interactive_max_segments,
    )
    if len(segments) < original_segment_count:
        warnings.append(segment_cap_warning(len(segments), original_segment_count))
    if len(segments) > settings.max_segments:
        raise too_many_segments_error(len(segments))

    try:
        with timed_step(logger, "graph-build"):
            G, _ = await asyncio.wait_for(
                run_in_threadpool(build_graph, segments),
                timeout=settings.analyze_compute_timeout_seconds,
            )
    except asyncio.TimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail="분석 시간이 초과됐습니다. 반경을 줄여 다시 시도해 주세요.",
        ) from e
    on_primary = analysis_point_on_primary_network(G, lng, lat, attr="walkable")

    depthmapx_metrics = None
    depthmapx_error = None
    metrics = None
    engine_metrics = {}
    actual_engine = None

    if settings.depthmapx_enabled:
        # Subprocess call takes seconds-to-minutes; keep it off the event loop
        # so unrelated requests (upjong search, docs) stay responsive.
        with timed_step(logger, "depthmapx"):
            depthmapx_metrics, depthmapx_error = await run_in_threadpool(
                compute_depthmapx_metrics, G, segments, modes=(request.mode,)
            )
        if depthmapx_metrics is not None:
            metrics = _with_aux_defaults(depthmapx_metrics, len(segments))
            engine_metrics["depthmapx"] = depthmapx_metrics
            actual_engine = "depthmapx"
        elif settings.depthmapx_strict:
            raise HTTPException(
                status_code=503,
                detail=(
                    "DepthmapX engine failed and strict mode disables the "
                    f"Python fallback: {depthmapx_error or 'unknown error'}"
                ),
            )
        elif depthmapx_error:
            warnings.append(f"DepthmapX unavailable; using Python engine: {depthmapx_error}")

    if metrics is None:
        try:
            with timed_step(logger, "metrics"):
                metrics, metric_warnings = await asyncio.wait_for(
                    run_in_threadpool(
                        compute_metrics,
                        G,
                        segments,
                        modes=(request.mode,),
                        warn_about_fragments=not on_primary,
                    ),
                    timeout=settings.analyze_compute_timeout_seconds,
                )
        except asyncio.TimeoutError as e:
            raise HTTPException(
                status_code=504,
                detail="분석 시간이 초과됐습니다. 반경을 줄여 다시 시도해 주세요.",
            ) from e
        metrics = _with_aux_defaults(metrics, len(segments))
        extend_unique_warnings(warnings, metric_warnings)
        engine_metrics["python"] = metrics
        actual_engine = "python"

    geojson = build_geojson(segments, metrics, engine_metrics)

    response = AnalyzeResponse(
        center=CenterPoint(lat=lat, lng=lng),
        radius_m=request.radius_m,
        segment_count=len(segments),
        warnings=warnings,
        segments=geojson,
        engines={
            "python": {
                "available": "python" in engine_metrics,
                "label": "Python angular (fallback)",
            },
            "depthmapx": {
                "available": depthmapx_metrics is not None,
                "label": "DepthmapX angular reference",
                "error": depthmapx_error,
            },
            "comparison": {},
            "cache": {
                "hit": False,
                "network_version": settings.analysis_cache_network_version,
                "analysis_version": settings.analysis_cache_version,
                "engine": actual_engine,
                "stored": settings.analysis_cache_enabled and actual_engine == desired_engine,
            },
        },
    )
    if settings.analysis_cache_enabled and actual_engine == desired_engine:
        cache_payload = response.model_dump(mode="json")
        cache_payload["_cache"] = {
            "mode": request.mode,
            "engine": actual_engine,
            "analysis_version": settings.analysis_cache_version,
            "segment_limit": settings.interactive_max_segments,
            "baseline_metrics": {
                metric: [
                    float(metrics[request.mode][metric].get(index, 0.0))
                    for index in range(len(segments))
                ]
                for metric in METRICS
            },
        }
        try:
            await run_in_threadpool(
                analysis_cache.put,
                cache_key,
                cache_payload,
            )
        except Exception as exc:
            response.warnings.append(f"Persistent analysis cache write failed: {exc}")
    return response
