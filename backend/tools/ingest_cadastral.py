"""연속지적도(LSMD_CONT_LDREG) SHP → parcel_centroids 테이블 적재.

국가공간정보포털/VWorld에서 받은 연속지적도 zip(시군구/시도 단위, 내부에
.shp/.dbf/.shx)을 읽어 필지 대표점(representative point)을 저장한다.
PNU 필드만 사용하므로 속성 스키마 차이에 둔감하다. 재실행 안전(upsert).

Run: cd backend && .venv/Scripts/python -m tools.ingest_cadastral "D:\\data\\LSMD_CONT_LDREG_11*.zip"
     (기본 좌표계 EPSG:5186; 다르면 --srid 지정)
"""

from __future__ import annotations

import argparse
import glob
import io
import time
import zipfile

import psycopg2
import psycopg2.extras
import shapefile  # pyshp
from shapely.geometry import shape

from app.config import settings

_UPSERT_SQL = """
INSERT INTO parcel_centroids (pnu, geom_5179, src, ingested_at)
VALUES %s
ON CONFLICT (pnu) DO UPDATE SET
  geom_5179 = EXCLUDED.geom_5179,
  ingested_at = NOW()
"""
# lon/lat 자리에 원좌표(x, y)와 소스 SRID가 들어간다.
_VALUES_TEMPLATE = (
    "(%s, ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), %s), 5179), %s, NOW())"
)

BATCH_SIZE = 5000


def _detect_encoding(zf: zipfile.ZipFile, base: str) -> str:
    """.cpg 사이드카가 있으면 그 인코딩, 없으면 cp949(한국 지적 SHP 기본)."""
    try:
        cpg = zf.read(base + ".cpg").decode("ascii", "ignore").strip().lower()
    except KeyError:
        return "cp949"
    if not cpg:
        return "cp949"
    if "utf" in cpg or cpg in ("65001",):
        return "utf-8"
    if "949" in cpg or "ksc" in cpg or "euc" in cpg:
        return "cp949"
    return "cp949"


def _shp_readers_from_zip(zip_path: str):
    """Yield (name, shapefile.Reader) for each .shp inside the zip."""
    zf = zipfile.ZipFile(zip_path)
    shp_names = [n for n in zf.namelist() if n.lower().endswith(".shp")]
    for shp_name in shp_names:
        base = shp_name[:-4]
        try:
            shp = io.BytesIO(zf.read(shp_name))
            dbf = io.BytesIO(zf.read(base + ".dbf"))
            shx_raw = None
            try:
                shx_raw = io.BytesIO(zf.read(base + ".shx"))
            except KeyError:
                pass
        except KeyError as e:
            print(f"  WARN {zip_path}::{shp_name}: missing sidecar {e}")
            continue
        # PNU(A1)만 읽으므로 다른 필드(법정동명 등)의 인코딩 불일치는 무해.
        # VWorld 배포본의 cp949/utf-8/CPG 표기 차이로 죽지 않도록 replace.
        encoding = _detect_encoding(zf, base)
        reader = shapefile.Reader(
            shp=shp, dbf=dbf, shx=shx_raw,
            encoding=encoding, encodingErrors="replace",
        )
        yield shp_name, reader


# VWorld 연속지적도형정보 SHP는 컬럼명이 A0..An (A1=고유번호/PNU 19자리).
# 다른 출처(필드명 'PNU'/'고유번호')도 지원.
_PNU_FIELD_CANDIDATES = ("A1", "PNU", "고유번호")


def _pnu_field_index(reader: shapefile.Reader) -> int:
    # fields[0]은 DeletionFlag 메타 필드라 제외하고 센다.
    names = [f[0] for f in reader.fields[1:]]
    for cand in _PNU_FIELD_CANDIDATES:
        for i, name in enumerate(names):
            if name.upper() == cand.upper():
                return i
    raise SystemExit(f"PNU field not found; fields={names}")


def ingest(patterns: list[str], srid: int, src: str) -> None:
    t0 = time.perf_counter()
    paths: list[str] = []
    for p in patterns:
        matched = sorted(glob.glob(p))
        if not matched:
            print(f"WARN: no files match {p}")
        paths.extend(matched)
    if not paths:
        raise SystemExit("no input zip files")

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()

    total = skipped = 0
    for zip_path in paths:
        file_rows = 0
        for shp_name, reader in _shp_readers_from_zip(zip_path):
            pnu_idx = _pnu_field_index(reader)
            batch: list[tuple] = []
            for sr in reader.iterShapeRecords():
                pnu = str(sr.record[pnu_idx]).strip()
                if len(pnu) != 19 or not pnu.isdigit():
                    skipped += 1
                    continue
                try:
                    pt = shape(sr.shape.__geo_interface__).representative_point()
                except Exception:
                    skipped += 1
                    continue
                batch.append((pnu, pt.x, pt.y, srid, src))
                if len(batch) >= BATCH_SIZE:
                    psycopg2.extras.execute_values(
                        cur, _UPSERT_SQL, batch,
                        template=_VALUES_TEMPLATE, page_size=1000,
                    )
                    conn.commit()
                    file_rows += len(batch)
                    batch = []
            if batch:
                psycopg2.extras.execute_values(
                    cur, _UPSERT_SQL, batch, template=_VALUES_TEMPLATE, page_size=1000
                )
                conn.commit()
                file_rows += len(batch)
        total += file_rows
        print(f"  {zip_path}: {file_rows:,} parcels")

    cur.execute("SELECT count(*) FROM parcel_centroids")
    n = cur.fetchone()[0]
    conn.close()
    print(
        f"DONE in {time.perf_counter() - t0:.0f}s: upserted {total:,} "
        f"(skipped {skipped:,}); DB total {n:,} parcels"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zips", nargs="+", help="연속지적도 zip 경로/글롭 패턴")
    parser.add_argument(
        "--srid", type=int, default=5186,
        help="원본 좌표계 EPSG (연속지적도 기본 5186)",
    )
    parser.add_argument("--src", default="cadastral")
    args = parser.parse_args()
    ingest(args.zips, args.srid, args.src)
