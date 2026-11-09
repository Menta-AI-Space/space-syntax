"""Load 서울시 상권분석서비스(영역-상권) shapefile into PostGIS.

Source: data/trdar-area/trdar_area.shp (OA-15560 file download, 1,650
상권 polygons, Korea 2000 Central Belt CRS, UTF-8 dbf per .cpg).
Creates ``trdar_area`` with WGS84 polygons + GIST index — enables
point → 상권 resolution for trdar_sales / trdar_workpop.

Usage: python tools/ingest_trdar_area.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg2
import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

SHP_PATH = Path(__file__).resolve().parents[2] / "data" / "trdar-area" / "trdar_area.shp"

DDL = """
DROP TABLE IF EXISTS trdar_area;
CREATE TABLE trdar_area (
    trdar_cd text PRIMARY KEY,
    trdar_nm text NOT NULL DEFAULT '',
    trdar_se_nm text NOT NULL DEFAULT '',
    signgu_nm text NOT NULL DEFAULT '',
    adstrd_nm text NOT NULL DEFAULT '',
    area_m2 double precision,
    geom geometry(MultiPolygon, 4326) NOT NULL
);
"""


def main() -> None:
    sf = shapefile.Reader(str(SHP_PATH), encoding="utf-8")
    src_crs = CRS.from_wkt(SHP_PATH.with_suffix(".prj").read_text())
    to_wgs84 = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True).transform

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute(DDL)
    fields = [f[0] for f in sf.fields[1:]]
    n = 0
    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        geom = shp_transform(to_wgs84, shape(sr.shape.__geo_interface__))
        if geom.geom_type == "Polygon":
            from shapely.geometry import MultiPolygon

            geom = MultiPolygon([geom])
        cur.execute(
            "INSERT INTO trdar_area VALUES (%s,%s,%s,%s,%s,%s, ST_GeomFromText(%s, 4326))",
            (
                str(rec["TRDAR_CD"]),
                rec.get("TRDAR_CD_N") or "",
                rec.get("TRDAR_SE_1") or "",
                rec.get("SIGNGU_CD_") or "",
                rec.get("ADSTRD_CD_") or "",
                float(rec["RELM_AR"]) if rec.get("RELM_AR") else None,
                geom.wkt,
            ),
        )
        n += 1
    cur.execute("CREATE INDEX trdar_area_gix ON trdar_area USING GIST (geom)")
    conn.commit()
    cur.execute("SELECT count(*) FROM trdar_area")
    print(f"loaded {n} polygons, table now has {cur.fetchone()[0]} rows")
    # sanity: 홍대입구역 좌표 → 상권
    cur.execute(
        "SELECT trdar_cd, trdar_nm FROM trdar_area "
        "WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(126.9237, 37.5570), 4326))"
    )
    print("홍대입구 point →", cur.fetchall())


if __name__ == "__main__":
    main()
