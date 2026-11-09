import asyncio

import pytest
from fastapi import HTTPException

from app.models.schemas import AnalyzeRequest
from app.routers import analyze as analyze_router
from app.services.space_syntax import WALK
from app.services.spatial_query import RoadSegment


class FakeCache:
    def __init__(self, payload=None):
        self.payload = payload
        self.get_keys = []
        self.put_calls = []

    def get(self, key):
        self.get_keys.append(key)
        return self.payload

    def put(self, key, payload):
        self.put_calls.append((key, payload))


def cached_payload():
    return {
        "center": {"lat": 37.55513, "lng": 126.93689},
        "radius_m": 500,
        "segment_count": 1,
        "warnings": [],
        "segments": {"type": "FeatureCollection", "features": []},
        "engines": {
            "python": {"available": False, "label": "Python angular (fallback)"},
            "depthmapx": {"available": True, "label": "DepthmapX angular reference", "error": None},
            "comparison": {},
        },
    }


def test_analyze_returns_persistent_cache_hit_without_recomputing(monkeypatch):
    cache = FakeCache(cached_payload())
    monkeypatch.setattr(analyze_router, "analysis_cache", cache)
    monkeypatch.setattr(analyze_router.settings, "analysis_cache_enabled", True)
    monkeypatch.setattr(analyze_router.settings, "analysis_cache_network_version", "v1")
    monkeypatch.setattr(analyze_router.settings, "analysis_cache_version", "a1")
    monkeypatch.setattr(
        analyze_router,
        "fetch_roads_in_radius",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cache hit recomputed roads")),
    )

    response = asyncio.run(
        analyze_router.analyze(
            AnalyzeRequest(lat=37.555134, lng=126.936893, radius_m=500, mode="walk")
        )
    )

    assert response.segment_count == 1
    assert response.center.lat == 37.55513
    assert response.engines["cache"]["hit"] is True
    assert len(cache.get_keys) == 1


def test_analyze_stores_computed_cache_miss(monkeypatch):
    cache = FakeCache()
    segment = RoadSegment(
        osm_id=1,
        highway="residential",
        length_m=100,
        coordinates=[[126.9, 37.5], [126.901, 37.5]],
        walkable=True,
    )
    metrics = {
        WALK: {
            "choice": {0: 2.0},
            "integration": {0: 3.0},
            "mean_depth": {0: 1.0},
            "connectivity": {0: 1.0},
        }
    }
    monkeypatch.setattr(analyze_router, "analysis_cache", cache)
    monkeypatch.setattr(analyze_router.settings, "analysis_cache_enabled", True)
    monkeypatch.setattr(analyze_router.settings, "analysis_cache_network_version", "v1")
    monkeypatch.setattr(analyze_router.settings, "analysis_cache_version", "a1")
    # This test exercises the depthmapx-succeeds caching path specifically, so it
    # must opt in even though depthmapx is disabled by default now (python is the
    # default desired engine; see test_analyze_stores_python_cache_when_depthmapx_disabled).
    monkeypatch.setattr(analyze_router.settings, "depthmapx_enabled", True)
    monkeypatch.setattr(analyze_router, "fetch_roads_in_radius", lambda *_args: [segment])
    monkeypatch.setattr(analyze_router, "build_graph", lambda _segments: (object(), {}))
    monkeypatch.setattr(analyze_router, "analysis_point_on_primary_network", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        analyze_router,
        "compute_depthmapx_metrics",
        lambda *_args, **_kwargs: (metrics, None),
    )

    response = asyncio.run(
        analyze_router.analyze(
            AnalyzeRequest(lat=37.5, lng=126.9, radius_m=500, mode="walk")
        )
    )

    assert response.segment_count == 1
    assert len(cache.put_calls) == 1
    _, stored = cache.put_calls[0]
    assert stored["segment_count"] == 1
    assert stored["segments"]["features"][0]["properties"]["integration_walk"] == 3.0
    assert stored["_cache"]["mode"] == WALK
    assert stored["_cache"]["engine"] == "depthmapx"
    assert stored["_cache"]["baseline_metrics"]["integration"] == [3.0]


def test_analyze_does_not_cache_transient_python_fallback(monkeypatch):
    cache = FakeCache()
    segment = RoadSegment(
        osm_id=1,
        highway="residential",
        length_m=100,
        coordinates=[[126.9, 37.5], [126.901, 37.5]],
        walkable=True,
    )
    python_metrics = {
        WALK: {
            "choice": {0: 2.0},
            "integration": {0: 3.0},
            "mean_depth": {0: 1.0},
            "connectivity": {0: 1.0},
        }
    }
    monkeypatch.setattr(analyze_router, "analysis_cache", cache)
    monkeypatch.setattr(analyze_router.settings, "analysis_cache_enabled", True)
    monkeypatch.setattr(analyze_router.settings, "depthmapx_enabled", True)
    # This test exercises the lenient fallback path; strict mode (the MVP
    # default via .env) turns the same situation into a 503 instead.
    monkeypatch.setattr(analyze_router.settings, "depthmapx_strict", False)
    monkeypatch.setattr(analyze_router, "fetch_roads_in_radius", lambda *_args: [segment])
    monkeypatch.setattr(analyze_router, "build_graph", lambda _segments: (object(), {}))
    monkeypatch.setattr(analyze_router, "analysis_point_on_primary_network", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        analyze_router,
        "compute_depthmapx_metrics",
        lambda *_args, **_kwargs: (None, "temporary failure"),
    )
    monkeypatch.setattr(
        analyze_router,
        "compute_metrics",
        lambda *_args, **_kwargs: (python_metrics, []),
    )

    response = asyncio.run(
        analyze_router.analyze(
            AnalyzeRequest(lat=37.5, lng=126.9, radius_m=500, mode="walk")
        )
    )

    assert response.engines["cache"]["stored"] is False
    assert cache.put_calls == []


def test_analyze_strict_mode_errors_instead_of_python_fallback(monkeypatch):
    cache = FakeCache()
    segment = RoadSegment(
        osm_id=1,
        highway="residential",
        length_m=100,
        coordinates=[[126.9, 37.5], [126.901, 37.5]],
        walkable=True,
    )
    monkeypatch.setattr(analyze_router, "analysis_cache", cache)
    monkeypatch.setattr(analyze_router.settings, "analysis_cache_enabled", True)
    monkeypatch.setattr(analyze_router.settings, "depthmapx_enabled", True)
    monkeypatch.setattr(analyze_router.settings, "depthmapx_strict", True)
    monkeypatch.setattr(analyze_router, "fetch_roads_in_radius", lambda *_args: [segment])
    monkeypatch.setattr(analyze_router, "build_graph", lambda _segments: (object(), {}))
    monkeypatch.setattr(analyze_router, "analysis_point_on_primary_network", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        analyze_router,
        "compute_depthmapx_metrics",
        lambda *_args, **_kwargs: (None, "temporary failure"),
    )
    monkeypatch.setattr(
        analyze_router,
        "compute_metrics",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("strict mode must not fall back to the Python engine")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            analyze_router.analyze(
                AnalyzeRequest(lat=37.5, lng=126.9, radius_m=500, mode="walk")
            )
        )

    assert exc_info.value.status_code == 503
    assert "temporary failure" in exc_info.value.detail
    assert cache.put_calls == []


def test_cache_key_snaps_nearby_coords_to_same_entry():
    request = AnalyzeRequest(lat=37.5551341, lng=126.9368939, radius_m=500, mode="walk")
    key_a = analyze_router._analysis_cache_key(126.9368931, 37.5551339, request, "python")
    key_b = analyze_router._analysis_cache_key(126.9368934, 37.5551342, request, "python")

    # Both clicks snap to the same EPSG:5179 500 m grid cell centre, so they
    # share a cache entry (and collide with the precomputed grid point).
    assert key_a == key_b
    assert (key_a.center_lng, key_a.center_lat) == (126.938237, 37.555146)


def test_analyze_stores_python_cache_when_depthmapx_disabled(monkeypatch):
    cache = FakeCache()
    segment = RoadSegment(
        osm_id=1,
        highway="residential",
        length_m=100,
        coordinates=[[126.9, 37.5], [126.901, 37.5]],
        walkable=True,
    )
    python_metrics = {
        WALK: {
            "choice": {0: 2.0},
            "integration": {0: 3.0},
            "mean_depth": {0: 1.0},
            "connectivity": {0: 1.0},
        }
    }
    monkeypatch.setattr(analyze_router, "analysis_cache", cache)
    monkeypatch.setattr(analyze_router.settings, "analysis_cache_enabled", True)
    monkeypatch.setattr(analyze_router.settings, "depthmapx_enabled", False)
    monkeypatch.setattr(analyze_router, "fetch_roads_in_radius", lambda *_args: [segment])
    monkeypatch.setattr(analyze_router, "build_graph", lambda _segments: (object(), {}))
    monkeypatch.setattr(analyze_router, "analysis_point_on_primary_network", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        analyze_router,
        "compute_metrics",
        lambda *_args, **_kwargs: (python_metrics, []),
    )

    response = asyncio.run(
        analyze_router.analyze(
            AnalyzeRequest(lat=37.5, lng=126.9, radius_m=500, mode="walk")
        )
    )

    assert response.engines["cache"]["engine"] == "python"
    assert response.engines["cache"]["stored"] is True
    assert len(cache.put_calls) == 1
    _, stored = cache.put_calls[0]
    assert stored["_cache"]["engine"] == "python"
