"""연속지적도 SHP → ``parcel_polygons`` (폴리곤을 **버리지 않고** 저장한다).

``ingest_cadastral.py``와 같은 파일을 읽지만 목적이 다르다. 그쪽은 조인용 대표점
하나만 남기고(``representative_point()``), 여기서는 **경계 자체**를 저장한다.
지도에서 땅 한 조각을 클릭하려면 클릭할 면이 있어야 하기 때문이다.

**동 하나씩 넣는다.** ``--ldong 1156011000``(여의도동)처럼 법정동 10자리로 걸러
넣고 눈으로 확인한 뒤 넓힌다. 서울 89만 필지를 한 번에 넣어 놓고 "됐겠지" 하면
정작 화면에서 뭐가 틀렸는지 알 수 없다 — 그 방식으로 여러 번 시간을 버렸다.

⚠️ 넓힐 때의 함정: 89만 폴리곤을 GeoJSON으로 그냥 내리면 5초 룰을 못 지킨다.
전역으로 가려면 벡터 타일(``ST_AsMVT``)이나 줌별 ``ST_Simplify``가 선행이다.

Run:
  # 여의도동만 (283필지)
  cd backend && .venv/Scripts/python -m tools.ingest_parcel_polygons \\
      "../data/land/AL_D002_11_*.zip" --ldong 1156011000
  # 구 하나
  cd backend && .venv/Scripts/python -m tools.ingest_parcel_polygons \\
      "../data/land/AL_D002_11_*.zip" --ldong 11560
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time

# Windows 콘솔 기본 코드페이지(cp1252/cp949)에서 한글 진행 로그가 UnicodeEncodeError로
# 죽는다. 적재는 다 해 놓고 마지막 print에서 터지면 성공/실패를 구분할 수 없다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg2
import psycopg2.extras
from shapely.geometry import shape
from shapely.geometry.multipolygon import MultiPolygon

from app.config import settings
from tools.ingest_cadastral import _pnu_field_index, _shp_readers_from_zip

_UPSERT_SQL = """
INSERT INTO parcel_polygons (pnu, geom_5179, area_sqm, src, ingested_at)
VALUES %s
ON CONFLICT (pnu) DO UPDATE SET
  geom_5179 = EXCLUDED.geom_5179,
  area_sqm = EXCLUDED.area_sqm,
  ingested_at = NOW()
"""
# 원본 좌표계(기본 EPSG:5186)로 만들어 5179로 옮긴다. 면적은 옮긴 뒤에 재야
# 미터 단위가 맞다.
_VALUES_TEMPLATE = (
    "(%s,"
    " ST_Multi(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), %s), 5179)),"
    " ST_Area(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), %s), 5179)),"
    " %s, NOW())"
)

BATCH_SIZE = 2000


def _as_multipolygon(geom) -> MultiPolygon | None:
    """폴리곤/멀티폴리곤만 통과시킨다.

    연속지적도에 선·점이 섞여 오는 경우가 있는데, 그건 필지 경계가 아니라
    지적 부속 요소다. 저장하면 지도에서 클릭이 안 되는 유령 행이 된다.
    """
    if geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    if geom.geom_type == "MultiPolygon":
        return geom
    return None


def ingest(patterns: list[str], srid: int, src: str, ldong: str | None) -> None:
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

    kept = skipped_pnu = skipped_geom = filtered = 0
    for zip_path in paths:
        for shp_name, reader in _shp_readers_from_zip(zip_path):
            pnu_idx = _pnu_field_index(reader)
            batch: list[tuple] = []
            for sr in reader.iterShapeRecords():
                pnu = str(sr.record[pnu_idx]).strip()
                if len(pnu) != 19 or not pnu.isdigit():
                    skipped_pnu += 1
                    continue
                if ldong and not pnu.startswith(ldong):
                    filtered += 1
                    continue
                try:
                    geom = _as_multipolygon(shape(sr.shape.__geo_interface__))
                except Exception:
                    geom = None
                if geom is None:
                    skipped_geom += 1
                    continue
                gj = json.dumps(geom.__geo_interface__)
                # 같은 GeoJSON을 두 번 넘긴다 — 기하용과 면적용.
                batch.append((pnu, gj, srid, gj, srid, src))
                if len(batch) >= BATCH_SIZE:
                    psycopg2.extras.execute_values(
                        cur, _UPSERT_SQL, batch,
                        template=_VALUES_TEMPLATE, page_size=500,
                    )
                    conn.commit()
                    kept += len(batch)
                    print(f"  {kept}필지 저장")
                    batch = []
            if batch:
                psycopg2.extras.execute_values(
                    cur, _UPSERT_SQL, batch,
                    template=_VALUES_TEMPLATE, page_size=500,
                )
                conn.commit()
                kept += len(batch)

    cur.close()
    conn.close()
    dt = time.perf_counter() - t0
    print(
        f"완료 — {kept}필지 저장 ({dt:.1f}s)"
        f" · 범위 밖 {filtered} · PNU 이상 {skipped_pnu} · 기하 이상 {skipped_geom}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patterns", nargs="+", help="연속지적도 zip 경로(글롭 가능)")
    parser.add_argument("--srid", type=int, default=5186, help="원본 좌표계 (기본 5186)")
    parser.add_argument("--src", default="cadastral")
    parser.add_argument(
        "--ldong",
        default=None,
        help="PNU 접두사로 거른다. 법정동 10자리(1156011000) 또는 시군구 5자리(11560)",
    )
    args = parser.parse_args()
    ingest(args.patterns, args.srid, args.src, args.ldong)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
