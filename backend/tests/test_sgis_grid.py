"""Tests for SGIS grid radius aggregation (PostGIS-backed).

Real geometry/SQL execution (ST_Buffer, ST_Intersects, JSON aggregation) is
Postgres's job and isn't re-verified here — ``FakeGridDB`` recomputes the
same intersection with shapely so the *Python* orchestration in
``sgis_grid.py`` (resolution selection, single-vs-split branching, dataclass
assembly) gets real behavioral coverage without a live database.
"""

from __future__ import annotations

import re

from pyproj import Transformer
from shapely.geometry import Point, Polygon

from app.config import settings
from app.services import sgis_grid
from app.services.sgis_grid import RES_100M, RES_500M, RES_1K

_korea_to_wgs84 = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
_wgs84_to_korea = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)

_AGE_ITEM_RE = re.compile(r"^in_(age|grp)_")


class FakeGridDB:
    def __init__(self) -> None:
        self.cells: list[tuple[int, str, str, object]] = []  # (res, code, tile, geom)
        self.stats: list[tuple[int, str, str, int]] = []  # (res, code, item, value)

    def add_cell(
        self, resolution_m: int, grid_code: str, tile: str, minx: float, miny: float
    ) -> None:
        size = float(resolution_m)
        geom = Polygon(
            [
                (minx, miny),
                (minx, miny + size),
                (minx + size, miny + size),
                (minx + size, miny),
                (minx, miny),
            ]
        )
        self.cells.append((resolution_m, grid_code, tile, geom))

    def add_stat(self, resolution_m: int, grid_code: str, item: str, value: int) -> None:
        self.stats.append((resolution_m, grid_code, item, value))


class FakeCursor:
    def __init__(self, db: FakeGridDB) -> None:
        self.db = db
        self._result: object = None

    def execute(self, query: str, params=None) -> None:
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT EXISTS"):
            self._result = ("has_data", bool(self.db.cells))
        elif "FROM sgis_grid_cells" in normalized and "DISTINCT resolution_m" in normalized:
            self._result = ("resolutions", sorted({c[0] for c in self.db.cells}))
        elif "FROM sgis_grid_stats WHERE item" in normalized:
            (item,) = params
            self._result = (
                "resolutions",
                sorted({s[0] for s in self.db.stats if s[2] == item}),
            )
        elif normalized.startswith("WITH buf AS"):
            self._result = ("aggregate", self._run_aggregate(params))
        else:
            raise AssertionError(f"unrecognized query: {normalized[:80]}")

    def _run_aggregate(self, params: dict):
        lng, lat = params["lng"], params["lat"]
        x, y = _wgs84_to_korea.transform(lng, lat)
        buffer_geom = Point(x, y).buffer(params["radius_m"])
        resolution_m = params["resolution_m"]
        stat_keys = set(params["stat_keys"])
        include_age_bands = params["include_age_bands"]

        hit_codes: dict[str, str] = {}
        for res, code, tile, geom in self.db.cells:
            if res != resolution_m:
                continue
            if geom.intersects(buffer_geom):
                hit_codes[code] = tile

        totals: dict[str, int] = {}
        age_items: dict[str, int] = {}
        matched_codes: set[str] = set()
        for res, code, item, value in self.db.stats:
            if res != resolution_m or code not in hit_codes:
                continue
            is_age = bool(_AGE_ITEM_RE.match(item))
            if item not in stat_keys and not (include_age_bands and is_age):
                continue
            if value != 0:
                matched_codes.add(code)
            if item in stat_keys:
                totals[item] = totals.get(item, 0) + value
            if include_age_bands and is_age:
                age_items[item] = age_items.get(item, 0) + value

        tiles = sorted(set(hit_codes.values()))
        return (tiles, len(matched_codes), totals, age_items)

    def fetchone(self):
        kind, value = self._result
        if kind == "has_data":
            return (value,)
        return value

    def fetchall(self):
        kind, value = self._result
        return [(v,) for v in value]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self, db: FakeGridDB) -> None:
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _clear_grid_caches() -> None:
    sgis_grid._has_grid_data.cache_clear()
    sgis_grid._available_cell_resolutions.cache_clear()
    sgis_grid._workers_available_resolutions.cache_clear()


def _use_fake_db(monkeypatch, db: FakeGridDB) -> None:
    monkeypatch.setattr(sgis_grid, "get_connection", lambda: FakeConnection(db))


def _write_mini_grid_bundle(db: FakeGridDB) -> None:
    tile = "zzzz"
    code_a = f"{tile}0001"
    code_b = f"{tile}0002"
    db.add_cell(RES_1K, code_a, tile, 901_000.0, 1_901_000.0)
    db.add_cell(RES_1K, code_b, tile, 901_000.0 + RES_1K, 1_901_000.0)

    db.add_stat(RES_1K, code_a, sgis_grid.STAT_POP, 100)
    db.add_stat(RES_1K, code_a, sgis_grid.STAT_POP_MALE, 40)
    db.add_stat(RES_1K, code_a, sgis_grid.STAT_POP_FEMALE, 60)
    db.add_stat(RES_1K, code_a, "in_age_005", 30)
    db.add_stat(RES_1K, code_a, "in_age_006", 20)
    db.add_stat(RES_1K, code_a, sgis_grid.STAT_HOUSEHOLDS, 50)
    db.add_stat(RES_1K, code_a, sgis_grid.STAT_WORKERS, 300)
    db.add_stat(RES_1K, code_a, sgis_grid.STAT_COMPANIES, 10)

    db.add_stat(RES_1K, code_b, sgis_grid.STAT_POP, 200)
    db.add_stat(RES_1K, code_b, sgis_grid.STAT_POP_MALE, 90)
    db.add_stat(RES_1K, code_b, sgis_grid.STAT_POP_FEMALE, 110)
    db.add_stat(RES_1K, code_b, sgis_grid.STAT_HOUSEHOLDS, 80)
    db.add_stat(RES_1K, code_b, sgis_grid.STAT_WORKERS, 400)
    db.add_stat(RES_1K, code_b, sgis_grid.STAT_COMPANIES, 20)


def _write_multi_resolution_bundle(db: FakeGridDB, *, workers_1k: bool = False) -> None:
    tile = "zzzz"
    pop_code = f"{tile}p001"
    detail_code = f"{tile}d001"
    workers_code = f"{tile}w001"

    db.add_cell(RES_100M, pop_code, tile, 901_000.0, 1_901_000.0)
    db.add_stat(RES_100M, pop_code, sgis_grid.STAT_POP, 42)
    db.add_stat(RES_100M, pop_code, sgis_grid.STAT_POP_MALE, 20)
    db.add_stat(RES_100M, pop_code, sgis_grid.STAT_POP_FEMALE, 22)

    db.add_cell(RES_500M, detail_code, tile, 901_000.0, 1_901_000.0)
    db.add_stat(RES_500M, detail_code, sgis_grid.STAT_HOUSEHOLDS, 17)
    db.add_stat(RES_500M, detail_code, sgis_grid.STAT_COMPANIES, 9)

    if workers_1k:
        db.add_cell(RES_1K, workers_code, tile, 901_000.0, 1_901_000.0)
        db.add_stat(RES_1K, workers_code, sgis_grid.STAT_WORKERS, 33)


class TestSgisGridRadius:
    def setup_method(self) -> None:
        _clear_grid_caches()

    def teardown_method(self) -> None:
        _clear_grid_caches()

    def test_query_sums_intersecting_cells(self, monkeypatch):
        db = FakeGridDB()
        _write_mini_grid_bundle(db)
        _use_fake_db(monkeypatch, db)

        lng, lat = _korea_to_wgs84.transform(901_500, 1_901_500)
        result = sgis_grid.query_grid_radius(lat, lng, 500)

        assert result is not None
        assert result.grid_cells == 1
        assert result.population == 100
        assert result.households == 50
        assert result.workers == 300
        assert result.companies == 10
        assert result.age_band_counts.get("20대") == 50

    def test_query_merges_two_cells(self, monkeypatch):
        db = FakeGridDB()
        _write_mini_grid_bundle(db)
        _use_fake_db(monkeypatch, db)

        lng_mid, lat_mid = _korea_to_wgs84.transform(901_500, 1_901_500)
        result = sgis_grid.query_grid_radius(lat_mid, lng_mid, 800)

        assert result is not None
        assert result.grid_cells == 2
        assert result.population == 300
        assert result.households == 130
        assert result.workers == 700

    def test_multi_resolution_pop_100m_detail_500m(self, monkeypatch):
        db = FakeGridDB()
        _write_multi_resolution_bundle(db)
        _use_fake_db(monkeypatch, db)

        lng, lat = _korea_to_wgs84.transform(901_050, 1_901_050)
        result = sgis_grid.query_grid_radius(lat, lng, 300)

        assert result is not None
        assert result.population == 42
        assert result.households == 17
        assert result.companies == 9
        assert result.workers == 0
        assert result.pop_resolution_m == RES_100M
        assert result.detail_resolution_m == RES_500M

    def test_workers_1k_aggregated_despite_500m_detail(self, monkeypatch):
        """2023 종사자 bundle is 1km-only; must not silently drop to 0 when
        가구/사업체 aggregate at 500m (grid codes differ per resolution)."""
        db = FakeGridDB()
        _write_multi_resolution_bundle(db, workers_1k=True)
        _use_fake_db(monkeypatch, db)

        lng, lat = _korea_to_wgs84.transform(901_050, 1_901_050)
        result = sgis_grid.query_grid_radius(lat, lng, 300)

        assert result is not None
        assert result.workers == 33
        assert result.households == 17
        assert result.companies == 9
        assert result.pop_resolution_m == RES_100M
        assert result.detail_resolution_m == RES_500M
        assert result.workers_resolution_m == RES_1K

    def test_unconfigured_returns_none(self, monkeypatch):
        _use_fake_db(monkeypatch, FakeGridDB())
        assert sgis_grid.query_grid_radius(37.5665, 126.9780, 500) is None

    def test_is_grid_data_available_reflects_db_state(self, monkeypatch):
        _use_fake_db(monkeypatch, FakeGridDB())
        assert sgis_grid.is_grid_data_available() is False

        _clear_grid_caches()
        db = FakeGridDB()
        _write_mini_grid_bundle(db)
        _use_fake_db(monkeypatch, db)
        assert sgis_grid.is_grid_data_available() is True
