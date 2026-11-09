"""Ingest Seoul 250m-grid SHORT-TERM FOREIGNER living population (OA-22786).

Same file format as the domestic dataset (일자, 시간, 행정동코드, 250m격자,
생활인구합계, then per-nationality masked cells), so the parsing helpers are
imported from ingest_living_pop. Written to SEPARATE tables
(living_pop_foreign_hourly / living_pop_foreign_slot) — running the domestic
tool on foreigner files would delete-and-replace domestic dates with
foreigner totals, corrupting both. Grid geometry is shared via
living_pop_grid.

Usage:
    python tools/ingest_living_pop_foreign.py <file.csv|file.zip|dir> [...]
"""

from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import settings  # noqa: E402
from ingest_living_pop import (  # noqa: E402
    CELL_M,
    grid_sw_corner,
    iter_source_files,
    read_csv_rows,
)

DDL = """
CREATE TABLE IF NOT EXISTS living_pop_foreign_hourly (
    stat_date date NOT NULL,
    hour smallint NOT NULL,
    grid_code text NOT NULL,
    total real NOT NULL,
    PRIMARY KEY (stat_date, hour, grid_code)
);
CREATE TABLE IF NOT EXISTS living_pop_foreign_slot (
    grid_code text NOT NULL,
    day_type text NOT NULL,
    hour smallint NOT NULL,
    avg_total real NOT NULL,
    sample_days integer NOT NULL,
    PRIMARY KEY (grid_code, day_type, hour)
);
"""

REBUILD_SLOTS = """
TRUNCATE living_pop_foreign_slot;
INSERT INTO living_pop_foreign_slot (grid_code, day_type, hour, avg_total, sample_days)
SELECT grid_code,
       CASE WHEN EXTRACT(ISODOW FROM stat_date) >= 6 THEN 'weekend' ELSE 'weekday' END,
       hour,
       AVG(total),
       COUNT(DISTINCT stat_date)
FROM living_pop_foreign_hourly
GROUP BY 1, 2, 3;
"""


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
        except Exception as e:
            print(f"SKIP {name}: {e}")
            continue
        if not sums:
            print(f"SKIP {name}: no data rows")
            continue
        file_dates = {k[0] for k in sums}
        new_dates = file_dates - dates_loaded
        if new_dates:
            cur.execute(
                "DELETE FROM living_pop_foreign_hourly WHERE stat_date = ANY(%s)",
                (sorted(new_dates),),
            )
            dates_loaded |= new_dates
        buf = io.StringIO()
        for (d, h, g), v in sums.items():
            buf.write(f"{d.isoformat()}\t{h}\t{g}\t{v:.2f}\n")
        buf.seek(0)
        cur.copy_expert(
            "COPY living_pop_foreign_hourly (stat_date, hour, grid_code, total) FROM STDIN",
            buf,
        )
        total_rows += len(sums)
        print(f"OK   {name}: {len(sums):,} rows")

    # register any grid cells not yet in the shared geometry table
    cur.execute(
        "SELECT DISTINCT h.grid_code FROM living_pop_foreign_hourly h "
        "LEFT JOIN living_pop_grid g USING (grid_code) WHERE g.grid_code IS NULL"
    )
    grid_rows = []
    for (code,) in cur.fetchall():
        sw = grid_sw_corner(code)
        if sw is not None:
            grid_rows.append((code, sw[0], sw[1], sw[0] + CELL_M, sw[1] + CELL_M))
    if grid_rows:
        args = b",".join(
            cur.mogrify("(%s, ST_MakeEnvelope(%s,%s,%s,%s,5179))", r)
            for r in grid_rows
        )
        cur.execute(b"INSERT INTO living_pop_grid (grid_code, geom) VALUES " + args)

    cur.execute(REBUILD_SLOTS)
    cur.execute("SELECT count(*) FROM living_pop_foreign_hourly")
    hourly = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM living_pop_foreign_slot")
    slots = cur.fetchone()[0]
    conn.commit()
    print(
        f"DONE ingested {total_rows:,} rows this run · foreign hourly={hourly:,} "
        f"slots={slots:,} · new grid cells={len(grid_rows):,}"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    main(sys.argv[1:])
