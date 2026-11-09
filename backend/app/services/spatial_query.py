import threading
from contextlib import contextmanager
from dataclasses import dataclass

from app.config import settings

# Fetch slightly beyond the analysis radius so segments that connect the in-radius
# network are included even when their geometry mostly lies outside the bubble.
CONNECTIVITY_FETCH_BUFFER_M = 100

# Query roads_clean using EPSG:5179 (Korea 2000 / Unified CS, meters).
# The network predicate (default: walkable OR drivable) and an optional arterial
# filter are spliced in by fetch_roads_in_radius; the {network} placeholder keeps
# the geometry/DWithin scaffold in one place.
ROAD_QUERY = """
SELECT
    osm_id,
    highway,
    length_m,
    walkable,
    drivable,
    layer,
    bridge,
    tunnel,
    ST_AsGeoJSON(geom)::json AS geom
FROM roads_clean
WHERE {network}
  AND ST_DWithin(
        geom_5179,
        ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179),
        %s
      )
"""


@dataclass
class RoadSegment:
    osm_id: int
    highway: str
    length_m: float
    coordinates: list[list[float]]
    walkable: bool = False
    drivable: bool = False
    layer: int = 0
    bridge: bool = False
    tunnel: bool = False


# A fresh psycopg2.connect to the local PostGIS costs ~80ms idle and ~200ms
# under load (TCP + auth + backend fork). One /api/decide with three candidates
# opens 42 of them, so connection setup alone accounted for ~8s of thread time
# — more than every SQL statement in the request put together. Connections are
# pooled per process instead; the semaphore is sized with the pool so an
# exhausted pool waits for a free connection rather than raising PoolError.
_POOL = None
_POOL_LOCK = threading.Lock()
_POOL_SLOTS: threading.Semaphore | None = None


def _pool():
    global _POOL, _POOL_SLOTS
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                from psycopg2.pool import ThreadedConnectionPool

                size = max(1, settings.db_pool_max_connections)
                _POOL_SLOTS = threading.Semaphore(size)
                _POOL = ThreadedConnectionPool(1, size, settings.database_url)
    return _POOL, _POOL_SLOTS


@contextmanager
def get_connection():
    """Pooled connection with the same semantics ``with psycopg2.connect(...)``
    had: commit on a clean exit, rollback on an exception. The connection goes
    back to the pool instead of being dropped."""
    pool, slots = _pool()
    slots.acquire()
    connection = None
    try:
        connection = pool.getconn()
        try:
            yield connection
            connection.commit()
        except BaseException:
            # A connection whose transaction cannot be unwound is not safe to
            # hand to the next caller — drop it and let the pool open a new one.
            try:
                connection.rollback()
            except Exception:
                pool.putconn(connection, close=True)
                connection = None
            raise
    finally:
        if connection is not None:
            pool.putconn(connection)
        slots.release()


def fetch_road_names(osm_ids: list[int]) -> dict[int, str]:
    """Best-effort name lookup for a handful of osm_ids (e.g. top-improved roads)."""
    real_ids = [i for i in osm_ids if i > 0]
    if not real_ids:
        return {}

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT osm_id, name FROM roads_clean "
                "WHERE osm_id = ANY(%s) AND name IS NOT NULL AND name <> ''",
                (real_ids,),
            )
            rows = cur.fetchall()
    return {int(osm_id): str(name) for osm_id, name in rows}


def clinic_weekend_supply(
    lat: float, lng: float, radius_m: int
) -> tuple[int, int, int] | None:
    """(전체, 토요일 진료, 일요일 진료) 의원·병원 수 within radius.

    Primary source: egen_hospitals (E-Gen 응급의료포털 — 서울 의원 94% 주말
    시간 커버리지). Fallback: hira_hospitals (전국 등기부는 완전하지만 주말
    시간 커버리지 ~7%, 서울 0%) for regions not yet E-Gen-ingested.
    Returns None when tables are missing/unreachable (enrichment only).
    """
    query = """
        SELECT count(*),
               count(*) FILTER (WHERE open_sat),
               count(*) FILTER (WHERE open_sun)
        FROM {table}
        WHERE {kind_col} IN ('의원', '병원', '종합병원')
          AND ST_DWithin(
                geom_5179,
                ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179),
                %s
              )
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query.format(table="egen_hospitals", kind_col="div_name"),
                    (lng, lat, radius_m),
                )
                row = cur.fetchone()
                if row and int(row[0]) > 0:
                    return (int(row[0]), int(row[1]), int(row[2]))
                cur.execute(
                    query.format(table="hira_hospitals", kind_col="kind"),
                    (lng, lat, radius_m),
                )
                row = cur.fetchone()
    except Exception:
        return None
    return (int(row[0]), int(row[1]), int(row[2])) if row else None


def ev_charger_supply(
    lat: float, lng: float, radius_m: int
) -> tuple[int, int] | None:
    """(전체 충전소 수, 급속 충전소 수) within radius from ev_chargers.

    Counts DISTINCT stat_id (충전소), not individual charger/plug rows —
    a single 지하주차장 station can list dozens of chger_id rows, which made
    early per-plug counts unrealistically high (Seoul 500m median 157 plugs
    vs 31 stations). 급속 = 완속 코드(02 AC완속, 07 AC3상) 외 전부 (DC 계열).
    Returns None when the table is empty/missing (환경부 API 미적재 상태) —
    a genuine 0 is only reported once data exists.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT EXISTS (SELECT 1 FROM ev_chargers LIMIT 1)")
                if not cur.fetchone()[0]:
                    return None
                cur.execute(
                    """
                    SELECT count(DISTINCT stat_id),
                           count(DISTINCT stat_id) FILTER (
                             WHERE charger_type NOT IN ('02', '07')
                           )
                    FROM ev_chargers
                    WHERE ST_DWithin(
                            geom_5179,
                            ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179),
                            %s
                          )
                    """,
                    (lng, lat, radius_m),
                )
                row = cur.fetchone()
    except Exception:
        return None
    return (int(row[0]), int(row[1])) if row else None


def nearest_school_distance_m(lat: float, lng: float, max_radius_m: int = 3000) -> float | None:
    """Distance in meters to the nearest active school, or None if none within max_radius_m."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ST_Distance(
                    geom_5179,
                    ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179)
                ) AS dist_m
                FROM schools
                WHERE ST_DWithin(
                    geom_5179,
                    ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179),
                    %s
                )
                ORDER BY geom_5179 <-> ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179)
                LIMIT 1
                """,
                (lng, lat, lng, lat, max_radius_m, lng, lat),
            )
            row = cur.fetchone()
    return float(row[0]) if row else None


def fetch_roads_in_radius(
    lng: float,
    lat: float,
    radius_m: int,
    *,
    mode: str | None = None,
    arterial_only: bool = False,
) -> list[RoadSegment]:
    """Query road segments within radius from roads_clean (EPSG:5179).

    With the defaults (mode=None, arterial_only=False) this returns every
    walkable-or-drivable segment in radius — the historical behaviour that
    analyze/simulate/site-score rely on, so their small-radius fetches are
    unchanged.

    Callers that already know the travel mode (e.g. the dashboard's wide
    competition fetch) can narrow the network:
      * mode drive/moto -> drivable-only, walk/bike -> walkable-only, reusing
        isochrone.network_attr_for_mode so the split stays in one place.
      * arterial_only  -> keep only through-traffic classes (config-driven).
        For 4~8km drive reaches this is what actually decides reachability, and
        dropping alleys cuts dense-Seoul fetches ~80% — enough to clear the
        max_segments 422 that motivated this filter.
    """
    import psycopg2.extras

    # None -> preserve the original walkable-OR-drivable predicate exactly.
    if mode is None:
        network_clause = "(walkable OR drivable)"
    else:
        from app.services.isochrone import network_attr_for_mode

        network_clause = network_attr_for_mode(mode)

    query = ROAD_QUERY.format(network=network_clause)
    params: list = [lng, lat, radius_m + CONNECTIVITY_FETCH_BUFFER_M]
    if arterial_only:
        query += "  AND highway = ANY(%s)\n"
        params.append(settings.arterial_highway_list)

    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

    segments: list[RoadSegment] = []
    for row in rows:
        geom = row["geom"]
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        segments.append(
            RoadSegment(
                osm_id=int(row["osm_id"]),
                highway=str(row["highway"]),
                length_m=float(row["length_m"] or 0),
                coordinates=coords,
                walkable=bool(row["walkable"]),
                drivable=bool(row["drivable"]),
                layer=int(row["layer"] or 0),
                bridge=bool(row["bridge"]),
                tunnel=bool(row["tunnel"]),
            )
        )
    return segments


def fetch_apartment_points(lng: float, lat: float, radius_m: int) -> list[tuple[float, float]]:
    """반경 안 OSM 아파트 건물 중심(경도, 위도). 계획도면 모델의 「주변 아파트 블록」을 도면 밖까지 세려고 쓴다
    — 학습(신도시 10곳)도 OSM building=apartments 중심점으로 셌다(2026-10-27)."""
    query = """
        SELECT ST_X(c), ST_Y(c) FROM (
          SELECT ST_Transform(ST_Centroid(way), 4326) AS c FROM planet_osm_polygon
          WHERE building = 'apartments'
            AND ST_DWithin(ST_Transform(way, 5179),
                           ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179), %s)
        ) t
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (lng, lat, radius_m))
            return [(float(x), float(y)) for x, y in cur.fetchall()]
