#!/usr/bin/env python3
"""Import one SBIZ quarterly CSV into store_snapshots (segment-snapped).

Usage:
  python scripts/import_sbiz_snapshot.py --csv path/to/stores.csv --quarter 20240930 \\
      --category-keywords 커피,카페

CSV must include lon/lat (or 좌표) and 업종 columns. Maps rows to nearest roads_clean
segment within 50m.
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
    p = argparse.ArgumentParser(description="Import SBIZ quarterly snapshot")
    p.add_argument("--csv", required=True, help="Path to quarterly CSV file")
    p.add_argument("--quarter", required=True, help="Quarter label e.g. 20240930")
    p.add_argument("--category-keywords", default="", help="Comma-separated match keywords")
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    return p.parse_args()


def main():
    args = parse_args()
    db_url = args.database_url or "postgresql://spacesyntax:spacesyntax@localhost:5432/spacesyntax"
    keywords = [k.strip() for k in args.category_keywords.split(",") if k.strip()]

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("Empty CSV")
        return 1

    inserted = 0
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            for row in rows:
                lon = float(row.get("lon") or row.get("경도") or row.get("LON") or 0)
                lat = float(row.get("lat") or row.get("위도") or row.get("LAT") or 0)
                if not lon or not lat:
                    continue
                mcls = str(row.get("indsMclsNm") or row.get("상권업종중분류명") or "")
                scls = str(row.get("indsSclsNm") or row.get("상권업종소분류명") or "")
                label = f"{mcls} {scls}"
                cat_match = any(k in label for k in keywords) if keywords else False
                cur.execute(
                    """
                    SELECT osm_id FROM roads_clean
                    WHERE ST_DWithin(
                      geom_5179,
                      ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179),
                      50
                    )
                    ORDER BY geom_5179 <-> ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179)
                    LIMIT 1
                    """,
                    (lon, lat, lon, lat),
                )
                seg = cur.fetchone()
                seg_id = seg[0] if seg else None
                cur.execute(
                    """
                    INSERT INTO store_snapshots
                      (quarter, bizes_id, bizes_nm, inds_scls_cd, inds_mcls_nm,
                       lon, lat, segment_osm_id, category_match, geom)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326))
                    """,
                    (
                        args.quarter,
                        row.get("bizesId") or row.get("상가업소번호"),
                        row.get("bizesNm") or row.get("상호명"),
                        row.get("indsSclsCd") or row.get("상권업종소분류코드"),
                        mcls,
                        lon,
                        lat,
                        seg_id,
                        cat_match,
                        lon,
                        lat,
                    ),
                )
                inserted += 1
        conn.commit()
    print(f"Imported {inserted} rows for quarter {args.quarter}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
