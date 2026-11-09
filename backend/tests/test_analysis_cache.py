from app.services.analysis_cache import (
    AnalysisCacheKey,
    PostgresAnalysisCache,
    build_cache_key,
    cached_baseline,
    cap_analysis_segments,
)
from app.services.spatial_query import RoadSegment


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.result = None

    def execute(self, query, params=None):
        normalized = " ".join(query.split()).upper()
        if normalized.startswith("SELECT RESULT"):
            self.result = self.rows.get(tuple(params))
        elif normalized.startswith("INSERT INTO ANALYSIS_CACHE"):
            payload = getattr(params[-1], "adapted", params[-1])
            self.rows[tuple(params[:-1])] = payload

    def fetchone(self):
        return (self.result,) if self.result is not None else None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_cache_key_preserves_coordinates_and_includes_analysis_identity():
    key = build_cache_key(
        lng=126.9368934,
        lat=37.5551344,
        radius_m=500,
        mode="walk",
        network_version="seoul-osm-2026-07",
        analysis_version="angular-v2",
        engine="depthmapx",
        segment_limit=1000,
    )

    assert key == AnalysisCacheKey(
        network_version="seoul-osm-2026-07",
        analysis_version="angular-v2",
        engine="depthmapx",
        segment_limit=1000,
        center_lng=126.9368934,
        center_lat=37.5551344,
        radius_m=500,
        mode="walk",
    )


def test_cache_key_changes_with_radius_mode_or_network_version():
    args = (126.9, 37.5, 500, "walk", "v1", "a1", "depthmapx", 1000)
    base = build_cache_key(*args)

    assert build_cache_key(126.9, 37.5, 1000, "walk", "v1", "a1", "depthmapx", 1000) != base
    assert build_cache_key(126.9, 37.5, 500, "drive", "v1", "a1", "depthmapx", 1000) != base
    assert build_cache_key(126.9, 37.5, 500, "walk", "v2", "a1", "depthmapx", 1000) != base
    assert build_cache_key(126.9, 37.5, 500, "walk", "v1", "a2", "depthmapx", 1000) != base
    assert build_cache_key(126.9, 37.5, 500, "walk", "v1", "a1", "python", 1000) != base
    assert build_cache_key(126.9, 37.5, 500, "walk", "v1", "a1", "depthmapx", 5000) != base


def test_postgres_cache_round_trips_json_payload():
    rows = {}
    cache = PostgresAnalysisCache(lambda: FakeConnection(rows), ensure_schema=False)
    key = build_cache_key(126.9, 37.5, 500, "walk", "v1", "a1", "python", 1000)
    payload = {"center": {"lat": 37.5, "lng": 126.9}, "segment_count": 2}

    assert cache.get(key) is None

    cache.put(key, payload)

    assert cache.get(key) == payload


def test_cached_baseline_restores_segments_and_metrics():
    payload = {
        "_cache": {
            "mode": "walk",
            "baseline_metrics": {
                "integration": [3.5000004],
                "choice": [7.0000004],
                "mean_depth": [1.2000004],
                "connectivity": [4.0000004],
            },
        },
        "segments": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "osm_id": 9,
                        "highway": "residential",
                        "length_m": 120.0,
                        "walkable": True,
                        "drivable": True,
                        "layer": 1,
                        "bridge": True,
                        "tunnel": False,
                        "integration_walk": 3.5,
                        "choice_walk": 7.0,
                        "mean_depth_walk": 1.2,
                        "connectivity_walk": 4.0,
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[126.9, 37.5], [126.901, 37.5]],
                    },
                }
            ],
        }
    }

    segments, metrics = cached_baseline(payload, "walk")

    assert segments[0].osm_id == 9
    assert segments[0].coordinates[1] == [126.901, 37.5]
    assert segments[0].layer == 1
    assert segments[0].bridge is True
    assert metrics["integration"][0] == 3.5000004
    assert metrics["choice"][0] == 7.0000004


def test_cap_analysis_segments_is_shared_and_deterministic():
    segments = [
        RoadSegment(
            osm_id=osm_id,
            highway="residential",
            length_m=10,
            coordinates=[[126.9 + offset, 37.5], [126.9 + offset, 37.5001]],
            walkable=True,
        )
        for osm_id, offset in [(1, 0.003), (2, 0.001), (3, 0.002)]
    ]

    capped, original_count = cap_analysis_segments(segments, 126.9, 37.5, 2)

    assert original_count == 3
    assert [segment.osm_id for segment in capped] == [2, 3]
