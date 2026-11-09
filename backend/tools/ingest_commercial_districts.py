"""R-ONE 상권구획도(업로드용) SHP → commercial_districts PostGIS 적재.

한국부동산원 상업용부동산 임대동향조사의 368개 상권 폴리곤(EPSG:5179).
lat/lng → ST_Contains(상권) 매칭으로 상가 임대료·공실률 참고치를 찾는 데 쓴다.
재실행 안전(전체 교체).

Run: cd backend && .venv/Scripts/python -m tools.ingest_commercial_districts "C:\\...\\상권구획도(업로드용).zip"
"""

from __future__ import annotations

import argparse
import io
import time
import zipfile

import psycopg2
import psycopg2.extras
import shapefile  # pyshp
from shapely.geometry import MultiPolygon, shape

from app.config import settings

_INSERT_SQL = """
INSERT INTO commercial_districts (
  sanggwon_nm, sido_cd, region_cd, note, area_sqm,
  has_sohgyumo, has_jungdaehyeong, has_office, has_jibhap, geom_5179, ingested_at
)
VALUES %s
ON CONFLICT ON CONSTRAINT commercial_districts_key DO UPDATE SET
  region_cd = EXCLUDED.region_cd, note = EXCLUDED.note, area_sqm = EXCLUDED.area_sqm,
  has_sohgyumo = EXCLUDED.has_sohgyumo, has_jungdaehyeong = EXCLUDED.has_jungdaehyeong,
  has_office = EXCLUDED.has_office, has_jibhap = EXCLUDED.has_jibhap,
  geom_5179 = EXCLUDED.geom_5179, ingested_at = NOW()
"""
_VALUES_TEMPLATE = (
    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, "
    "ST_Multi(ST_GeomFromText(%s, 5179)), NOW())"
)

_FIELD_ALIASES = {
    "sanggwon_nm": ("상권명",),
    "sido_cd": ("시도코드",),
    "region_cd": ("지역코드",),
    "note": ("비고",),
    "area_sqm": ("구역면적",),
    "flag_small": ("소규모",),
    "flag_mid": ("중대형",),
    "flag_office": ("오피스",),
    "flag_jibhap": ("집합",),
}


def _field_index(reader: shapefile.Reader) -> dict[str, int]:
    names = [f[0] for f in reader.fields[1:]]
    idx: dict[str, int] = {}
    for key, aliases in _FIELD_ALIASES.items():
        for a in aliases:
            if a in names:
                idx[key] = names.index(a)
                break
    missing = [k for k in ("sanggwon_nm", "sido_cd") if k not in idx]
    if missing:
        raise SystemExit(f"shapefile missing fields {missing}; got {names}")
    return idx


def _flag(rec, idx: dict[str, int], key: str) -> bool:
    if key not in idx:
        return False
    v = rec[idx[key]]
    return bool(str(v).strip()) and str(v).strip() not in ("0", "N", "-")


def ingest(zip_path: str) -> None:
    t0 = time.perf_counter()
    zf = zipfile.ZipFile(zip_path)
    shp_name = next((n for n in zf.namelist() if n.lower().endswith(".shp")), None)
    if not shp_name:
        raise SystemExit("ZIP에 .shp 없음")
    base = shp_name[:-4]
    reader = shapefile.Reader(
        shp=io.BytesIO(zf.read(shp_name)),
        dbf=io.BytesIO(zf.read(base + ".dbf")),
        shx=io.BytesIO(zf.read(base + ".shx")),
        encoding="cp949",
    )
    idx = _field_index(reader)

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()

    rows: list[tuple] = []
    skipped = 0
    for sr in reader.iterShapeRecords():
        rec = sr.record
        name = str(rec[idx["sanggwon_nm"]]).strip()
        if not name:
            skipped += 1
            continue
        try:
            geom = shape(sr.shape.__geo_interface__)
            if geom.geom_type == "Polygon":
                geom = MultiPolygon([geom])
            wkt = geom.wkt
        except Exception:
            skipped += 1
            continue
        area = rec[idx["area_sqm"]] if "area_sqm" in idx else None
        rows.append((
            name,
            str(rec[idx["sido_cd"]]).strip(),
            str(rec[idx["region_cd"]]).strip() if "region_cd" in idx else "",
            str(rec[idx["note"]]).strip() if "note" in idx else "",
            float(area) if area not in (None, "") else None,
            _flag(rec, idx, "flag_small"),
            _flag(rec, idx, "flag_mid"),
            _flag(rec, idx, "flag_office"),
            _flag(rec, idx, "flag_jibhap"),
            wkt,
        ))

    psycopg2.extras.execute_values(
        cur, _INSERT_SQL, rows, template=_VALUES_TEMPLATE, page_size=200
    )
    conn.commit()
    cur.execute("SELECT count(*), count(DISTINCT sido_cd) FROM commercial_districts")
    n, n_sido = cur.fetchone()
    conn.close()
    print(
        f"DONE in {time.perf_counter() - t0:.0f}s: upserted {len(rows)} "
        f"(skipped {skipped}); DB total {n} 상권, {n_sido} 시도"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", help="상권구획도(업로드용).zip 경로")
    args = parser.parse_args()
    ingest(args.zip_path)
