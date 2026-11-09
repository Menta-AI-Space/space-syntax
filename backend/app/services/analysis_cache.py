"""Persistent cache primitives for stable road-network analyses."""

from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Callable

from app.services.space_syntax import METRICS
from app.services.spatial_query import RoadSegment

# Analysis precompute grid: EPSG:5179 (Korea TM), fixed spacing anchored at the
# Seoul bbox SW corner. This is the SAME grid that
# tools/precompute_analysis_cache.iter_grid_points walks, so a live cache key
# snapped here collides with precomputed entries and with nearby/repeat clicks.
ANALYSIS_GRID_BBOX = (126.764, 37.428, 127.184, 37.702)
ANALYSIS_GRID_STEP_M = 500


@lru_cache(maxsize=1)
def _grid_origin():
    from pyproj import Transformer

    to_meters = Transformer.from_crs(4326, 5179, always_xy=True)
    to_wgs84 = Transformer.from_crs(5179, 4326, always_xy=True)
    origin_x, origin_y = to_meters.transform(
        ANALYSIS_GRID_BBOX[0], ANALYSIS_GRID_BBOX[1]
    )
    return to_meters, to_wgs84, origin_x, origin_y


def snap_to_analysis_grid(lng: float, lat: float) -> tuple[float, float]:
    """Snap a WGS84 point to the nearest analysis-grid cell centre.

    Returns the grid point's ``(lng, lat)`` rounded to 6 decimals — identical to
    what tools/precompute_analysis_cache produces — so cache keys built from
    arbitrary clicks land on precomputed / previously-computed entries. Only the
    cache KEY uses this; the caller still runs the analysis on the true click
    coordinates on a miss, so a snap can only turn a miss into a hit, never
    change a computed result.
    """
    try:
        to_meters, to_wgs84, origin_x, origin_y = _grid_origin()
        x, y = to_meters.transform(lng, lat)
        step = ANALYSIS_GRID_STEP_M
        snapped_x = origin_x + round((x - origin_x) / step) * step
        snapped_y = origin_y + round((y - origin_y) / step) * step
        snapped_lng, snapped_lat = to_wgs84.transform(snapped_x, snapped_y)
        return round(snapped_lng, 6), round(snapped_lat, 6)
    except Exception:
        # pyproj missing or transform failure — degrade to the old behaviour
        # (fine-grained key) rather than breaking analysis.
        return round(lng, 6), round(lat, 6)


CREATE_CACHE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS analysis_cache_entries (
    network_version TEXT NOT NULL,
    analysis_version TEXT NOT NULL,
    engine TEXT NOT NULL CHECK (engine IN ('depthmapx', 'python')),
    segment_limit INTEGER NOT NULL CHECK (segment_limit > 0),
    center_lng DOUBLE PRECISION NOT NULL,
    center_lat DOUBLE PRECISION NOT NULL,
    radius_m INTEGER NOT NULL CHECK (radius_m IN (500, 1000)),
    mode TEXT NOT NULL CHECK (mode IN ('walk', 'drive')),
    result JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        network_version, analysis_version, engine, segment_limit,
        center_lng, center_lat, radius_m, mode
    )
)
"""

CREATE_CACHE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS analysis_cache_entries_time_idx
ON analysis_cache_entries (computed_at DESC)
"""

SELECT_CACHE_SQL = """
SELECT result
FROM analysis_cache_entries
WHERE network_version = %s
  AND analysis_version = %s
  AND engine = %s
  AND segment_limit = %s
  AND center_lng = %s
  AND center_lat = %s
  AND radius_m = %s
  AND mode = %s
"""

UPSERT_CACHE_SQL = """
INSERT INTO analysis_cache_entries (
    network_version, analysis_version, engine, segment_limit,
    center_lng, center_lat, radius_m, mode, result
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (
    network_version, analysis_version, engine, segment_limit,
    center_lng, center_lat, radius_m, mode
)
DO UPDATE SET result = EXCLUDED.result, computed_at = NOW()
"""

PRUNE_CACHE_SQL = """
DELETE FROM analysis_cache_entries
WHERE ctid IN (
    SELECT ctid
    FROM analysis_cache_entries
    ORDER BY computed_at DESC
    OFFSET %s
)
"""


@dataclass(frozen=True)
class AnalysisCacheKey:
    network_version: str
    analysis_version: str
    engine: str
    segment_limit: int
    center_lng: float
    center_lat: float
    radius_m: int
    mode: str

    def params(self) -> tuple:
        return (
            self.network_version,
            self.analysis_version,
            self.engine,
            self.segment_limit,
            self.center_lng,
            self.center_lat,
            self.radius_m,
            self.mode,
        )


class PostgresAnalysisCache:
    def __init__(
        self,
        connection_factory: Callable,
        *,
        ensure_schema: bool = True,
        max_entries: int = 0,
    ) -> None:
        self._connection_factory = connection_factory
        self._schema_ready = not ensure_schema
        self._schema_lock = Lock()
        self._max_entries = max_entries

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(CREATE_CACHE_TABLE_SQL)
                    cursor.execute(CREATE_CACHE_INDEX_SQL)
            self._schema_ready = True

    def get(self, key: AnalysisCacheKey) -> dict | None:
        self._ensure_schema()
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(SELECT_CACHE_SQL, key.params())
                row = cursor.fetchone()
        return row[0] if row else None

    def put(self, key: AnalysisCacheKey, payload: dict) -> None:
        self._ensure_schema()
        try:
            from psycopg2.extras import Json

            stored_payload = Json(payload)
        except ImportError:
            stored_payload = payload
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(UPSERT_CACHE_SQL, (*key.params(), stored_payload))
                if self._max_entries > 0:
                    cursor.execute(PRUNE_CACHE_SQL, (self._max_entries,))


def _approx_dist2(segment: RoadSegment, lng: float, lat: float) -> float:
    if not segment.coordinates:
        return 0.0
    center_lng = sum(point[0] for point in segment.coordinates) / len(segment.coordinates)
    center_lat = sum(point[1] for point in segment.coordinates) / len(segment.coordinates)
    return (center_lng - lng) ** 2 + (center_lat - lat) ** 2


def cap_analysis_segments(
    segments: list[RoadSegment],
    lng: float,
    lat: float,
    limit: int,
) -> tuple[list[RoadSegment], int]:
    """Apply the same deterministic network cap to analysis and simulation."""
    original_count = len(segments)
    if limit <= 0 or original_count <= limit:
        return segments, original_count
    capped = sorted(
        segments,
        key=lambda segment: (
            _approx_dist2(segment, lng, lat),
            segment.osm_id,
            repr(segment.coordinates),
        ),
    )[:limit]
    return capped, original_count


def build_cache_key(
    lng: float,
    lat: float,
    radius_m: int,
    mode: str,
    network_version: str,
    analysis_version: str,
    engine: str,
    segment_limit: int,
) -> AnalysisCacheKey:
    return AnalysisCacheKey(
        network_version=network_version,
        analysis_version=analysis_version,
        engine=engine,
        segment_limit=segment_limit,
        center_lng=lng,
        center_lat=lat,
        radius_m=radius_m,
        mode=mode,
    )


def cached_baseline(
    payload: dict,
    mode: str,
) -> tuple[list[RoadSegment], dict[str, dict[int, float]]]:
    """Restore ordered segments and one mode's metrics from a cached response."""
    features = payload.get("segments", {}).get("features", [])
    segments: list[RoadSegment] = []
    metrics = {metric: {} for metric in METRICS}
    for feature in features:
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if geometry.get("type") != "LineString" or len(coordinates) < 2:
            continue
        segments.append(
            RoadSegment(
                osm_id=int(properties["osm_id"]),
                highway=str(properties.get("highway") or "unclassified"),
                length_m=float(properties.get("length_m") or 0.0),
                coordinates=coordinates,
                walkable=bool(properties.get("walkable")),
                drivable=bool(properties.get("drivable")),
                layer=int(properties.get("layer") or 0),
                bridge=bool(properties.get("bridge")),
                tunnel=bool(properties.get("tunnel")),
            )
        )
        restored_index = len(segments) - 1
        for metric in METRICS:
            metrics[metric][restored_index] = float(
                properties.get(f"{metric}_{mode}") or 0.0
            )

    cache_metadata = payload.get("_cache") or {}
    raw_metrics = cache_metadata.get("baseline_metrics") or {}
    if cache_metadata.get("mode") == mode:
        for metric in METRICS:
            values = raw_metrics.get(metric)
            if isinstance(values, list) and len(values) == len(segments):
                metrics[metric] = {
                    index: float(value) for index, value in enumerate(values)
                }
    return segments, metrics
