"""One-time bulk loader: local SGIS census grid bundle -> Postgres/PostGIS.

Reads the same folder layout the old file-based ``sgis_grid.py`` expected
(``1. 통계/`` long-format CSVs + ``2. 경계/grid_{tile}/grid_{tile}_{suffix}.shp``
boundaries, EPSG:5179) and bulk-loads it into ``sgis_grid_cells`` /
``sgis_grid_stats`` via COPY. Safe to re-run after a fresh SGIS download —
each table is TRUNCATEd before reloading.

Usage (from backend/):
    .venv/Scripts/python.exe -m scripts.import_sgis_grid --data-dir "C:\\dev\\space-syntax\\data\\sgis-grid"
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from pathlib import Path

import psycopg2
import shapefile
from shapely.geometry import shape
from shapely import wkb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.sgis_grid import (
    CREATE_CELLS_INDEX_SQL,
    CREATE_CELLS_TABLE_SQL,
    CREATE_STATS_TABLE_SQL,
    RES_100M,
    RES_500M,
    RES_1K,
)

_RES_SUFFIX: dict[int, str] = {
    RES_100M: "100M",
    RES_500M: "500M",
    RES_1K: "1K",
}

_STAT_DIRS: dict[str, list[Path]] = {
    "인구": [Path("1. 통계") / "1. 2024년 격자 통계(인구)"],
    "가구": [Path("1. 통계") / "2. 2024년 격자 통계(가구)"],
    "주택": [Path("1. 통계") / "3. 2024년 격자 통계(주택)"],
    "사업체": [
        Path("1. 통계") / "4. 2024년 격자 통계(사업체)",
        Path("1. 통계") / "4. 2023년 격자통계(사업체, 종사자)" / "사업체(대분류)",
    ],
    "종사자": [
        Path("1. 통계") / "4. 2024년 격자 통계(종사자)",
        Path("1. 통계") / "4. 2023년 격자통계(사업체, 종사자)" / "종사자(대분류)",
    ],
}

_STAT_YEAR: dict[str, tuple[str, ...]] = {
    "인구": ("2024",),
    "가구": ("2024",),
    "주택": ("2024",),
    "사업체": ("2024", "2023"),
    "종사자": ("2024", "2023"),
}

_CATEGORIES = ("인구", "가구", "주택", "사업체", "종사자")


def _parse_stat_value(value: str) -> int:
    if value in ("", "N/A", None):
        return 0
    return int(float(value))


def _csv_has_header(first_row: list[str]) -> bool:
    if len(first_row) < 4:
        return False
    return first_row[0] in ("기준연도", "year") or first_row[1] in ("격자코드", "grid_cd")


def _iter_stat_rows(path: Path):
    """Yield (grid_code, item, value) tuples from one census stat CSV."""
    with path.open("r", encoding="cp949", newline="") as f:
        reader = csv.reader(f)
        first = next(reader, None)
        if first is None:
            return
        if _csv_has_header(first):
            f.seek(0)
            for row in csv.DictReader(f):
                yield (
                    row["격자코드"].strip(),
                    row["통계항목"].strip(),
                    _parse_stat_value(row["통계값"]),
                )
            return
        for row in [first, *reader]:
            if len(row) < 4:
                continue
            yield (row[1].strip(), row[2].strip(), _parse_stat_value(row[3]))


def _stat_csv_path(
    root: Path, *, category: str, tile: str, resolution_m: int
) -> Path | None:
    suffix = _RES_SUFFIX[resolution_m]
    for year in _STAT_YEAR[category]:
        filename = f"{year}년_{category}_{tile}_{suffix}.csv"
        for rel_dir in _STAT_DIRS[category]:
            candidate = root / rel_dir / filename
            if candidate.is_file():
                return candidate
    return None


def _discover_tiles(bounds_root: Path) -> list[str]:
    tiles = []
    for tile_dir in sorted(bounds_root.iterdir()):
        if not tile_dir.is_dir() or not tile_dir.name.startswith("grid_"):
            continue
        tiles.append(tile_dir.name.replace("grid_", "", 1))
    return tiles


def _shapefile_path(root: Path, tile: str, resolution_m: int) -> Path:
    suffix = _RES_SUFFIX[resolution_m]
    return root / "2. 경계" / f"grid_{tile}" / f"grid_{tile}_{suffix}.shp"


def _copy_cells(cur, rows: list[tuple[int, str, str, str]]) -> None:
    """rows: (resolution_m, grid_code, tile, wkb_hex)"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    buf.seek(0)
    cur.copy_expert(
        "COPY sgis_grid_cells (resolution_m, grid_code, tile, geom_5179) "
        "FROM STDIN WITH (FORMAT csv)",
        buf,
    )


def _copy_stats(cur, rows: list[tuple[int, str, str, int]]) -> None:
    """rows: (resolution_m, grid_code, item, value)"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    buf.seek(0)
    cur.copy_expert(
        "COPY sgis_grid_stats (resolution_m, grid_code, item, value) "
        "FROM STDIN WITH (FORMAT csv)",
        buf,
    )


def import_cells(conn, root: Path, tiles: list[str]) -> int:
    total = 0
    with conn.cursor() as cur:
        for tile in tiles:
            for resolution_m in (RES_100M, RES_500M, RES_1K):
                shp_path = _shapefile_path(root, tile, resolution_m)
                if not shp_path.is_file():
                    continue
                reader = shapefile.Reader(str(shp_path))
                rows = []
                for shp_rec in reader.iterShapeRecords():
                    code = str(shp_rec.record[0]).strip()
                    geom = shape(shp_rec.shape.__geo_interface__)
                    hex_wkb = wkb.dumps(geom, hex=True, srid=5179)
                    rows.append((resolution_m, code, tile, hex_wkb))
                if rows:
                    _copy_cells(cur, rows)
                    total += len(rows)
                    print(
                        f"  cells  {tile} {_RES_SUFFIX[resolution_m]}: {len(rows)} rows",
                        flush=True,
                    )
    conn.commit()
    return total


def import_stats(conn, root: Path, tiles: list[str]) -> int:
    total = 0
    with conn.cursor() as cur:
        for tile in tiles:
            for category in _CATEGORIES:
                for resolution_m in (RES_100M, RES_500M, RES_1K):
                    # Only import stats for resolutions that actually have a
                    # matching boundary shapefile — stat rows for a
                    # non-existent cell resolution can never be queried.
                    if not _shapefile_path(root, tile, resolution_m).is_file():
                        continue
                    csv_path = _stat_csv_path(
                        root, category=category, tile=tile, resolution_m=resolution_m
                    )
                    if csv_path is None:
                        continue
                    rows = [
                        (resolution_m, code, item, value)
                        for code, item, value in _iter_stat_rows(csv_path)
                    ]
                    if rows:
                        _copy_stats(cur, rows)
                        total += len(rows)
                        print(
                            f"  stats  {tile} {category} {_RES_SUFFIX[resolution_m]}: "
                            f"{len(rows)} rows ({csv_path.name})",
                            flush=True,
                        )
    conn.commit()
    return total


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=str(Path(__file__).resolve().parent.parent.parent / "data" / "sgis-grid"),
        help="Merged SGIS grid bundle folder (default: ../data/sgis-grid)",
    )
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Postgres connection string (default: settings.database_url)",
    )
    args = parser.parse_args()

    root = Path(args.data_dir)
    bounds_root = root / "2. 경계"
    if not bounds_root.is_dir():
        raise SystemExit(f"No '2. 경계' folder under {root}")

    tiles = _discover_tiles(bounds_root)
    print(f"Found {len(tiles)} tile directories under {bounds_root}")

    conn = psycopg2.connect(args.database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_CELLS_TABLE_SQL)
            cur.execute(CREATE_CELLS_INDEX_SQL)
            cur.execute(CREATE_STATS_TABLE_SQL)
            print("Truncating existing sgis_grid_cells / sgis_grid_stats...")
            cur.execute("TRUNCATE sgis_grid_cells, sgis_grid_stats")
        conn.commit()

        t0 = time.monotonic()
        print("Importing grid cells (boundaries)...")
        cell_count = import_cells(conn, root, tiles)
        print(f"Imported {cell_count} cells in {time.monotonic() - t0:.1f}s")

        t1 = time.monotonic()
        print("Importing grid stats (census CSVs)...")
        stat_count = import_stats(conn, root, tiles)
        print(f"Imported {stat_count} stat rows in {time.monotonic() - t1:.1f}s")

        print("Analyzing tables...")
        with conn.cursor() as cur:
            cur.execute("ANALYZE sgis_grid_cells")
            cur.execute("ANALYZE sgis_grid_stats")
        conn.commit()
    finally:
        conn.close()

    print("Done.")


if __name__ == "__main__":
    main()
