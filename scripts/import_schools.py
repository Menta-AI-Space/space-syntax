#!/usr/bin/env python3
"""Import nationwide school locations (한국지방교육행정연구재단 CSV) into `schools`.

Usage:
  python scripts/import_schools.py --csv path/to/schools.csv

CSV columns expected (UTF-8 with BOM): 학교ID, 학교명, 학교급구분, 운영상태,
소재지도로명주소, 위도, 경도. Only 운영상태 == 운영 rows are imported.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import psycopg2  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description="Import nationwide school locations")
    p.add_argument("--csv", required=True, help="Path to school location CSV")
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    return p.parse_args()


def main():
    args = parse_args()
    db_url = args.database_url or "postgresql://spacesyntax:spacesyntax@localhost:5433/spacesyntax"

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("Empty CSV")
        return 1

    inserted = 0
    skipped = 0
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            for row in rows:
                if (row.get("운영상태") or "").strip() != "운영":
                    skipped += 1
                    continue
                try:
                    lat = float(row.get("위도") or 0)
                    lon = float(row.get("경도") or 0)
                except ValueError:
                    skipped += 1
                    continue
                if not lat or not lon:
                    skipped += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO schools
                      (school_id, name, school_level, status, address, lon, lat, geom, geom_5179)
                    VALUES (
                      %(school_id)s, %(name)s, %(level)s, %(status)s, %(address)s, %(lon)s, %(lat)s,
                      ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326),
                      ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 5179)
                    )
                    ON CONFLICT (school_id) DO UPDATE SET
                      name = EXCLUDED.name,
                      school_level = EXCLUDED.school_level,
                      status = EXCLUDED.status,
                      address = EXCLUDED.address,
                      lon = EXCLUDED.lon,
                      lat = EXCLUDED.lat,
                      geom = EXCLUDED.geom,
                      geom_5179 = EXCLUDED.geom_5179
                    """,
                    {
                        "school_id": row.get("학교ID"),
                        "name": row.get("학교명"),
                        "level": row.get("학교급구분"),
                        "status": row.get("운영상태"),
                        "address": row.get("소재지도로명주소") or row.get("소재지지번주소"),
                        "lon": lon,
                        "lat": lat,
                    },
                )
                inserted += 1
        conn.commit()
    print(f"Imported {inserted} schools ({skipped} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
