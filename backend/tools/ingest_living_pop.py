"""Ingest Seoul 250m-grid living-population files into PostGIS.

Sources: 서울열린데이터광장 OA-22784 (내국인) / OA-22786 (단기외국인) —
daily CSVs (`250_LOCAL_RESD_YYYYMMDD.csv`), monthly ZIPs of those CSVs,
or the SHEET-tab CSV export. Columns: 일자, 시간, 행정동코드, 250m격자,
생활인구합계, then masked age/gender cells we don't ingest.

Design notes (from the dataset's comment thread + observed data):
- A grid straddling 행정동 boundaries appears once per 행정동 — totals
  must be summed per (date, hour, grid).
- "*" masks values ≤ 3 persons (including some grid totals) → parsed as 0.
- Some published days are empty/corrupt (e.g. 20260626) → skipped with a
  warning, never fatal.
- Grid code 다사XXXXYYYY: EPSG:5179 SW corner = tile origin + digits×10m,
  cell 250m. Seoul fits entirely in tile 다사 (origin 900000, 1900000).

Usage:
    python tools/ingest_living_pop.py <file.csv|file.zip|dir> [more paths...]

Re-ingesting the same dates overwrites them (delete + insert). After raw
ingest, the weekday/weekend hourly profile table (living_pop_slot) is
rebuilt from scratch.
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

TILE_ORIGINS_5179: dict[str, tuple[int, int]] = {
    "다사": (900_000, 1_900_000),
}
CELL_M = 250

DDL = """
CREATE TABLE IF NOT EXISTS living_pop_hourly (
    stat_date date NOT NULL,
    hour smallint NOT NULL,
    grid_code text NOT NULL,
    total real NOT NULL,
    PRIMARY KEY (stat_date, hour, grid_code)
);
CREATE TABLE IF NOT EXISTS living_pop_grid (
    grid_code text PRIMARY KEY,
    geom geometry(Polygon, 5179) NOT NULL
);
CREATE INDEX IF NOT EXISTS living_pop_grid_geom_idx
    ON living_pop_grid USING GIST (geom);
CREATE TABLE IF NOT EXISTS living_pop_slot (
    grid_code text NOT NULL,
    day_type text NOT NULL,
    hour smallint NOT NULL,
    avg_total real NOT NULL,
    sample_days integer NOT NULL,
    PRIMARY KEY (grid_code, day_type, hour)
);
"""

REBUILD_SLOTS = """
TRUNCATE living_pop_slot;
INSERT INTO living_pop_slot (grid_code, day_type, hour, avg_total, sample_days)
SELECT grid_code,
       CASE WHEN EXTRACT(ISODOW FROM stat_date) >= 6 THEN 'weekend' ELSE 'weekday' END,
       hour,
       AVG(total),
       COUNT(DISTINCT stat_date)
FROM living_pop_hourly
GROUP BY 1, 2, 3;
"""


def grid_sw_corner(grid_code: str) -> tuple[int, int] | None:
    tile, digits = grid_code[:2], grid_code[2:]
    origin = TILE_ORIGINS_5179.get(tile)
    if origin is None or len(digits) != 8 or not digits.isdigit():
        return None
    return origin[0] + int(digits[:4]) * 10, origin[1] + int(digits[4:]) * 10


def parse_total(value: str) -> float:
    value = value.strip()
    if not value or value == "*":
        return 0.0  # masked: ≤3 persons
    return float(value)


def read_csv_rows(raw: bytes, name: str) -> dict[tuple[date, int, str], float]:
    """Sum totals per (date, hour, grid) — grids split by 행정동 are merged."""
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"{name}: unknown encoding")
    sums: dict[tuple[date, int, str], float] = {}
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 5:
        raise ValueError(f"{name}: missing header")
    for row in reader:
        if len(row) < 5:
            continue
        stat_date = datetime.strptime(row[0].strip(), "%Y%m%d").date()
        key = (stat_date, int(row[1]), row[3].strip())
        sums[key] = sums.get(key, 0.0) + parse_total(row[4])
    return sums


def iter_source_files(paths: list[str]):
    for arg in paths:
        p = Path(arg)
        if p.is_dir():
            yield from ((f.name, f.read_bytes()) for f in sorted(p.glob("*.csv")))
            for z in sorted(p.glob("*.zip")):
                yield from iter_zip(z)
        elif p.suffix.lower() == ".zip":
            yield from iter_zip(p)
        else:
            yield p.name, p.read_bytes()


def iter_zip(path: Path):
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.filename.lower().endswith(".csv"):
                yield f"{path.name}:{info.filename}", zf.read(info)


def main(paths: list[str]) -> None:
    conn = psycopg2.connect(settings.database_url)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(DDL)

    total_rows = 0
    dates_loaded: set[date] = set()
    for name, raw in iter_source_files(paths):
        try:
            sums = read_csv_rows(raw, name)
        except Exception as e:  # empty/corrupt published day — skip, don't die
            print(f"SKIP {name}: {e}")
            continue
        if not sums:
            print(f"SKIP {name}: no data rows")
            continue

        file_dates = {k[0] for k in sums}
        new_dates = file_dates - dates_loaded
        if new_dates:
            cur.execute(
                "DELETE FROM living_pop_hourly WHERE stat_date = ANY(%s)",
                (sorted(new_dates),),
            )
            dates_loaded |= new_dates

        buf = io.StringIO()
        for (d, h, g), v in sums.items():
            buf.write(f"{d.isoformat()}\t{h}\t{g}\t{v:.2f}\n")
        buf.seek(0)
        cur.copy_expert(
            "COPY living_pop_hourly (stat_date, hour, grid_code, total) FROM STDIN",
            buf,
        )
        total_rows += len(sums)
        print(f"OK   {name}: {len(sums):,} rows, dates {sorted(d.isoformat() for d in file_dates)}")

    # register any new grid cells with derived 250m geometry
    cur.execute(
        "SELECT DISTINCT h.grid_code FROM living_pop_hourly h "
        "LEFT JOIN living_pop_grid g USING (grid_code) WHERE g.grid_code IS NULL"
    )
    missing = [r[0] for r in cur.fetchall()]
    skipped_codes = 0
    grid_rows = []
    for code in missing:
        sw = grid_sw_corner(code)
        if sw is None:
            skipped_codes += 1
            continue
        grid_rows.append((code, sw[0], sw[1], sw[0] + CELL_M, sw[1] + CELL_M))
    if grid_rows:
        args = b",".join(
            cur.mogrify("(%s, ST_MakeEnvelope(%s,%s,%s,%s,5179))", r)
            for r in grid_rows
        )
        cur.execute(
            b"INSERT INTO living_pop_grid (grid_code, geom) VALUES " + args
        )
    if skipped_codes:
        print(f"WARN: {skipped_codes} grid codes from unknown tiles skipped")

    cur.execute(REBUILD_SLOTS)
    cur.execute("SELECT count(*) FROM living_pop_hourly")
    hourly = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM living_pop_grid")
    grids = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM living_pop_slot")
    slots = cur.fetchone()[0]
    conn.commit()
    print(
        f"DONE ingested {total_rows:,} rows this run · table totals: "
        f"hourly={hourly:,} grids={grids:,} slots={slots:,} · new grid cells={len(grid_rows):,}"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    main(sys.argv[1:])
