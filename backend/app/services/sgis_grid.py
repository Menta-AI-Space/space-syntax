"""SGIS grid statistics — spatial radius aggregation against PostGIS.

Grid cell polygons (EPSG:5179) and long-format census stats (격자코드 x
통계항목) are loaded once via ``scripts/import_sgis_grid.py`` into two tables:

- ``sgis_grid_cells(resolution_m, grid_code, tile, geom_5179)`` — GiST-indexed
- ``sgis_grid_stats(resolution_m, grid_code, item, value)``

Preferred resolutions (auto-detected from whichever the DB has loaded):

- Population (+ gender): 100m, fallback 1km
- Households, housing, companies: 500m, fallback 1km
- Workers: 500m or 1km (2023 종사자 bundle); not in census 사업체 export
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

import psycopg2

from app.config import settings

RES_100M = 100
RES_500M = 500
RES_1K = 1000

STAT_POP = "to_in_001"
STAT_POP_MALE = "to_in_007"
STAT_POP_FEMALE = "to_in_008"
STAT_HOUSEHOLDS = "to_ga_001"
STAT_HOUSING = "to_ho_001"
STAT_COMPANIES = "to_fa_010"
STAT_WORKERS = "to_em_020"

_WORKERS_RES_PREFERENCE: tuple[int, ...] = (RES_500M, RES_1K)

# 5-year grid codes → dashboard age-band labels (sgis.py / site_score.py).
_AGE_5Y_TO_BAND: dict[str, str] = {}
for _code, _band in (
    ("001", "10세 미만"),
    ("002", "10세 미만"),
    ("003", "10대"),
    ("004", "10대"),
    ("005", "20대"),
    ("006", "20대"),
    ("007", "30대"),
    ("008", "30대"),
    ("009", "40대"),
    ("010", "40대"),
    ("011", "50대"),
    ("012", "50대"),
    ("013", "60대"),
    ("014", "60대"),
):
    for _prefix in ("in_age_", "in_grp_"):
        _AGE_5Y_TO_BAND[f"{_prefix}{_code}"] = _band
for _suffix, _band in (
    ("015", "70세+"),
    ("016", "70세+"),
    ("017", "70세+"),
    ("018", "70세+"),
    ("019", "70세+"),
):
    for _prefix in ("in_age_", "in_grp_"):
        _AGE_5Y_TO_BAND[f"{_prefix}{_suffix}"] = _band
for _n in range(20, 80):
    _AGE_5Y_TO_BAND[f"in_age_{_n:03d}"] = "70세+"
    _AGE_5Y_TO_BAND[f"in_grp_{_n:03d}"] = "70세+"

_BAND_ORDER = [
    "10세 미만",
    "10대",
    "20대",
    "30대",
    "40대",
    "50대",
    "60대",
    "70세+",
]


@dataclass
class GridRadiusStats:
    population: int
    households: int
    workers: int
    companies: int
    male: int
    female: int
    age_band_counts: dict[str, int] = field(default_factory=dict)
    grid_cells: int = 0
    tiles: list[str] = field(default_factory=list)
    stats_year_pop: str = "2024"
    stats_year_workers: str = "2023"
    pop_resolution_m: int = RES_1K
    detail_resolution_m: int = RES_1K
    workers_resolution_m: int = RES_1K


# --- schema -----------------------------------------------------------

CREATE_CELLS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sgis_grid_cells (
    resolution_m INTEGER NOT NULL,
    grid_code TEXT NOT NULL,
    tile TEXT NOT NULL,
    geom_5179 GEOMETRY(POLYGON, 5179) NOT NULL,
    PRIMARY KEY (resolution_m, grid_code)
)
"""

CREATE_CELLS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS sgis_grid_cells_geom_idx
ON sgis_grid_cells USING GIST (geom_5179)
"""

CREATE_STATS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sgis_grid_stats (
    resolution_m INTEGER NOT NULL,
    grid_code TEXT NOT NULL,
    item TEXT NOT NULL,
    value BIGINT NOT NULL,
    PRIMARY KEY (resolution_m, grid_code, item)
)
"""

# --- queries ------------------------------------------------------------

_HAS_DATA_SQL = "SELECT EXISTS (SELECT 1 FROM sgis_grid_cells LIMIT 1)"

_AVAILABLE_CELL_RESOLUTIONS_SQL = "SELECT DISTINCT resolution_m FROM sgis_grid_cells"

_STAT_RESOLUTIONS_FOR_ITEM_SQL = (
    "SELECT DISTINCT resolution_m FROM sgis_grid_stats WHERE item = %s"
)

# Sums grid-cell stats whose polygons intersect a metric circle around
# (lat, lng). ``hit_cells`` is the spatial-index-backed candidate set;
# ``relevant_stats`` restricts to the requested stat_keys (plus optional
# age-band items) so both the totals and the "has actual data" cell count
# are computed in one round trip.
_AGGREGATE_SQL = """
WITH buf AS (
    SELECT ST_Buffer(
        ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), 5179),
        %(radius_m)s
    ) AS geom
),
hit_cells AS (
    SELECT c.grid_code, c.tile
    FROM sgis_grid_cells c, buf
    WHERE c.resolution_m = %(resolution_m)s
      AND ST_Intersects(c.geom_5179, buf.geom)
),
relevant_stats AS (
    SELECT s.grid_code, s.item, s.value
    FROM sgis_grid_stats s
    JOIN hit_cells h ON h.grid_code = s.grid_code
    WHERE s.resolution_m = %(resolution_m)s
      AND (
        s.item = ANY(%(stat_keys)s)
        OR (%(include_age_bands)s AND s.item ~ '^in_(age|grp)_')
      )
)
SELECT
    (SELECT COALESCE(array_agg(DISTINCT tile), ARRAY[]::text[]) FROM hit_cells) AS tiles,
    (SELECT COUNT(DISTINCT grid_code) FROM relevant_stats WHERE value <> 0) AS grid_cells,
    COALESCE((
        SELECT json_object_agg(item, total)
        FROM (
            SELECT item, SUM(value) AS total
            FROM relevant_stats
            WHERE item = ANY(%(stat_keys)s)
            GROUP BY item
        ) t
    ), '{}'::json) AS totals,
    COALESCE((
        SELECT json_object_agg(item, total)
        FROM (
            SELECT item, SUM(value) AS total
            FROM relevant_stats
            WHERE %(include_age_bands)s AND item ~ '^in_(age|grp)_'
            GROUP BY item
        ) t
    ), '{}'::json) AS age_items
"""


def get_connection():
    return psycopg2.connect(settings.database_url)


def ensure_schema() -> None:
    """Create the grid tables/index if they don't exist yet (idempotent)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_CELLS_TABLE_SQL)
            cur.execute(CREATE_CELLS_INDEX_SQL)
            cur.execute(CREATE_STATS_TABLE_SQL)
        conn.commit()


@lru_cache(maxsize=1)
def _has_grid_data() -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_HAS_DATA_SQL)
                row = cur.fetchone()
        return bool(row and row[0])
    except psycopg2.Error:
        return False


def is_grid_data_available() -> bool:
    return _has_grid_data()


@lru_cache(maxsize=1)
def _available_cell_resolutions() -> frozenset[int]:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_AVAILABLE_CELL_RESOLUTIONS_SQL)
                rows = cur.fetchall()
        return frozenset(r[0] for r in rows)
    except psycopg2.Error:
        return frozenset()


@lru_cache(maxsize=1)
def _workers_available_resolutions() -> frozenset[int]:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_STAT_RESOLUTIONS_FOR_ITEM_SQL, (STAT_WORKERS,))
                rows = cur.fetchall()
        return frozenset(r[0] for r in rows) & _available_cell_resolutions()
    except psycopg2.Error:
        return frozenset()


def _best_available_resolution(preferred: tuple[int, ...]) -> int | None:
    available = _available_cell_resolutions()
    for resolution_m in preferred:
        if resolution_m in available:
            return resolution_m
    return None


def _workers_resolution() -> int | None:
    """Finest resolution where 종사자 stats AND matching boundaries both exist.

    The 2023 종사자 bundle ships 1km-only while census 가구/사업체 exports are
    500m, so worker stats can live at a coarser resolution than the detail
    aggregation — cell codes differ per resolution, so they must be summed
    against boundaries of their own resolution or they silently drop to 0.
    """
    available = _workers_available_resolutions()
    for resolution_m in _WORKERS_RES_PREFERENCE:
        if resolution_m in available:
            return resolution_m
    return None


def resolution_label(resolution_m: int) -> str:
    if resolution_m >= 1000:
        return "1km"
    return f"{resolution_m}m"


@dataclass
class _ResolutionAggregate:
    totals: dict[str, int]
    age_band_counts: dict[str, int]
    grid_cells: int
    tiles: list[str]


def _aggregate_at_resolution(
    conn,
    *,
    lat: float,
    lng: float,
    radius_m: int,
    resolution_m: int,
    stat_keys: tuple[str, ...],
    include_age_bands: bool = False,
) -> _ResolutionAggregate:
    with conn.cursor() as cur:
        cur.execute(
            _AGGREGATE_SQL,
            {
                "lat": lat,
                "lng": lng,
                "radius_m": radius_m,
                "resolution_m": resolution_m,
                "stat_keys": list(stat_keys),
                "include_age_bands": include_age_bands,
            },
        )
        row = cur.fetchone()

    tiles, grid_cells, totals_raw, age_items_raw = row
    totals = {key: int((totals_raw or {}).get(key, 0)) for key in stat_keys}

    age_band_counts: dict[str, int] = {label: 0 for label in _BAND_ORDER}
    for item, value in (age_items_raw or {}).items():
        band = _AGE_5Y_TO_BAND.get(item)
        if band:
            age_band_counts[band] += int(value)

    return _ResolutionAggregate(
        totals=totals,
        age_band_counts=age_band_counts,
        grid_cells=int(grid_cells or 0),
        tiles=sorted(tiles or []),
    )


def query_grid_radius(lat: float, lng: float, radius_m: int) -> GridRadiusStats | None:
    """Sum grid cell stats whose polygons intersect a metric circle."""
    if radius_m <= 0 or not is_grid_data_available():
        return None

    pop_res = _best_available_resolution((RES_100M, RES_1K)) or RES_1K
    detail_res = _best_available_resolution((RES_500M, RES_1K)) or RES_1K
    workers_res = _workers_resolution() or detail_res

    with get_connection() as conn:
        if pop_res == detail_res == workers_res:
            agg = _aggregate_at_resolution(
                conn,
                lat=lat,
                lng=lng,
                radius_m=radius_m,
                resolution_m=pop_res,
                stat_keys=(
                    STAT_POP,
                    STAT_POP_MALE,
                    STAT_POP_FEMALE,
                    STAT_HOUSEHOLDS,
                    STAT_COMPANIES,
                    STAT_WORKERS,
                ),
                include_age_bands=True,
            )
            if agg.grid_cells == 0:
                return None
            return GridRadiusStats(
                population=agg.totals.get(STAT_POP, 0),
                households=agg.totals.get(STAT_HOUSEHOLDS, 0),
                workers=agg.totals.get(STAT_WORKERS, 0),
                companies=agg.totals.get(STAT_COMPANIES, 0),
                male=agg.totals.get(STAT_POP_MALE, 0),
                female=agg.totals.get(STAT_POP_FEMALE, 0),
                age_band_counts=agg.age_band_counts,
                grid_cells=agg.grid_cells,
                tiles=agg.tiles,
                pop_resolution_m=pop_res,
                detail_resolution_m=detail_res,
                workers_resolution_m=workers_res,
            )

        pop_agg = _aggregate_at_resolution(
            conn,
            lat=lat,
            lng=lng,
            radius_m=radius_m,
            resolution_m=pop_res,
            stat_keys=(STAT_POP, STAT_POP_MALE, STAT_POP_FEMALE),
            include_age_bands=True,
        )
        detail_keys: tuple[str, ...] = (STAT_HOUSEHOLDS, STAT_COMPANIES)
        if workers_res == detail_res:
            detail_keys = (STAT_HOUSEHOLDS, STAT_COMPANIES, STAT_WORKERS)
        detail_agg = _aggregate_at_resolution(
            conn,
            lat=lat,
            lng=lng,
            radius_m=radius_m,
            resolution_m=detail_res,
            stat_keys=detail_keys,
        )
        if workers_res == detail_res:
            workers = detail_agg.totals.get(STAT_WORKERS, 0)
            workers_tiles: list[str] = []
        else:
            workers_agg = _aggregate_at_resolution(
                conn,
                lat=lat,
                lng=lng,
                radius_m=radius_m,
                resolution_m=workers_res,
                stat_keys=(STAT_WORKERS,),
            )
            workers = workers_agg.totals.get(STAT_WORKERS, 0)
            workers_tiles = workers_agg.tiles

    population = pop_agg.totals.get(STAT_POP, 0)
    if population <= 0 and detail_agg.totals.get(STAT_HOUSEHOLDS, 0) <= 0:
        if pop_agg.grid_cells == 0 and detail_agg.grid_cells == 0:
            return None

    tiles = sorted(set(pop_agg.tiles) | set(detail_agg.tiles) | set(workers_tiles))
    grid_cells = pop_agg.grid_cells + detail_agg.grid_cells

    return GridRadiusStats(
        population=population,
        households=detail_agg.totals.get(STAT_HOUSEHOLDS, 0),
        workers=workers,
        companies=detail_agg.totals.get(STAT_COMPANIES, 0),
        male=pop_agg.totals.get(STAT_POP_MALE, 0),
        female=pop_agg.totals.get(STAT_POP_FEMALE, 0),
        age_band_counts=pop_agg.age_band_counts,
        grid_cells=grid_cells,
        tiles=tiles,
        pop_resolution_m=pop_res,
        detail_resolution_m=detail_res,
        workers_resolution_m=workers_res,
    )


def grid_population_density(population: int, radius_m: int) -> float | None:
    if radius_m <= 0 or population <= 0:
        return None
    area_km2 = math.pi * (radius_m / 1000) ** 2
    return population / area_km2
