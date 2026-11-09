"""Ingest Seoul trade area (상권) zones from shapefile into PostGIS.

Source: 서울시 상권분석서비스 - 영역(상권) (OA-15560)
Shapefile in Korea 2000 / Central Belt (EPSG:5181); reprojected to 4326
at ingest time. Fields: TRDAR_SE_C/TRDAR_SE_1 (구분 code/name),
TRDAR_CD/TRDAR_CD_N (상권 code/name), SIGNGU_CD/SIGNGU_CD_ (자치구
code/name), ADSTRD_CD/ADSTRD_CD_ (행정동 code/name), RELM_AR (면적 m2).

Usage:
    python tools/ingest_trdar_zone.py <shapefile.shp>

Re-ingesting upserts on trdar_id (= 상권 코드).
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg2
import shapefile
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shp_transform
from shapely.wkt import dumps as wkt_dumps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC_EPSG = 5181  # Korea 2000 / Central Belt (per .prj)

DDL = """
CREATE TABLE IF NOT EXISTS trdar_zone (
    trdar_id text PRIMARY KEY,
    trdar_name text NOT NULL,
    trdar_code text NOT NULL,
    gu_code text,
    gu_name text,
    geom geometry(MultiPolygon, 4326) NOT NULL
);
ALTER TABLE trdar_zone ADD COLUMN IF NOT EXISTS trdar_se_cd text;
ALTER TABLE trdar_zone ADD COLUMN IF NOT EXISTS trdar_se_name text;
ALTER TABLE trdar_zone ADD COLUMN IF NOT EXISTS dong_code text;
ALTER TABLE trdar_zone ADD COLUMN IF NOT EXISTS dong_name text;
ALTER TABLE trdar_zone ADD COLUMN IF NOT EXISTS area_m2 double precision;
CREATE INDEX IF NOT EXISTS trdar_zone_geom_idx
    ON trdar_zone USING GIST (geom);
CREATE INDEX IF NOT EXISTS trdar_zone_gu_idx
    ON trdar_zone (gu_code);
"""

UPSERT = """
INSERT INTO trdar_zone (trdar_id, trdar_name, trdar_code, gu_code, gu_name,
                        trdar_se_cd, trdar_se_name, dong_code, dong_name,
                        area_m2, geom)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        ST_Multi(ST_GeomFromText(%s, 4326)))
ON CONFLICT (trdar_id) DO UPDATE SET
    trdar_name = EXCLUDED.trdar_name,
    trdar_code = EXCLUDED.trdar_code,
    gu_code = EXCLUDED.gu_code,
    gu_name = EXCLUDED.gu_name,
    trdar_se_cd = EXCLUDED.trdar_se_cd,
    trdar_se_name = EXCLUDED.trdar_se_name,
    dong_code = EXCLUDED.dong_code,
    dong_name = EXCLUDED.dong_name,
    area_m2 = EXCLUDED.area_m2,
    geom = EXCLUDED.geom
"""


def norm(val) -> str | None:
    if val is None or val == "":
        return None
    return str(val).strip()


def main(shp_path: str) -> None:
    if not Path(shp_path).exists():
        raise FileNotFoundError(f"Shapefile not found: {shp_path}")

    sf = shapefile.Reader(shp_path, encoding="utf-8")
    field_names = [f[0] for f in sf.fields[1:]]
    print(f"Fields: {field_names}, records: {len(sf)}")

    project = Transformer.from_crs(SRC_EPSG, 4326, always_xy=True).transform

    conn = psycopg2.connect(settings.database_url)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(DDL)

    inserted = skipped = 0
    for shp, rec in zip(sf.shapes(), sf.records()):
        attrs = dict(zip(field_names, rec))
        code = norm(attrs.get("TRDAR_CD"))
        if not code:
            skipped += 1
            continue
        try:
            geom = shp_transform(project, shape(shp.__geo_interface__))
        except Exception as e:
            print(f"WARN geometry {code}: {e}")
            skipped += 1
            continue
        cur.execute(
            UPSERT,
            (
                code,
                norm(attrs.get("TRDAR_CD_N")) or f"Zone {code}",
                code,
                norm(attrs.get("SIGNGU_CD")),
                norm(attrs.get("SIGNGU_CD_")),
                norm(attrs.get("TRDAR_SE_C")),
                norm(attrs.get("TRDAR_SE_1")),
                norm(attrs.get("ADSTRD_CD")),
                norm(attrs.get("ADSTRD_CD_")),
                float(attrs.get("RELM_AR") or 0) or None,
                wkt_dumps(geom),
            ),
        )
        inserted += 1

    conn.commit()
    cur.execute(
        "SELECT count(*), ST_AsText(ST_Centroid(ST_Collect(geom))) FROM trdar_zone"
    )
    total, center = cur.fetchone()
    print(f"DONE upserted {inserted} zones (skipped={skipped}) · total={total}")
    print(f"Seoul-wide centroid (should be ~127.0, 37.55): {center}")
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    main(sys.argv[1])
