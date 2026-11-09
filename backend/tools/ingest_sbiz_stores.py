"""소상공인 상가(상권)정보 분기 ZIP → sbiz_stores 테이블 적재.

data.go.kr 「소상공인시장진흥공단_상가(상권)정보」 분기 전체 덤프(ZIP 안에
시도별 CSV)를 읽어 적재한다. 기본은 서울 CSV만; --all로 전국.
같은 분기 재실행은 안전(전체 교체), 새 분기 실행 시 이전 분기 데이터를 지운다.

Run: cd backend && .venv/Scripts/python -m tools.ingest_sbiz_stores "C:\\...\\상가(상권)정보_20260331.zip"
     (전국: ... --all)
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import zipfile

import psycopg2
import psycopg2.extras

from app.config import settings

# 덤프 헤더명 → sbiz_stores 컬럼. 연도에 따라 헤더가 조금씩 바뀌어 후보를 둔다.
_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "bizes_id": ("상가업소번호",),
    "bizes_nm": ("상호명",),
    "inds_lcls_cd": ("상권업종대분류코드",),
    "inds_lcls_nm": ("상권업종대분류명",),
    "inds_mcls_cd": ("상권업종중분류코드",),
    "inds_mcls_nm": ("상권업종중분류명",),
    "inds_scls_cd": ("상권업종소분류코드",),
    "inds_scls_nm": ("상권업종소분류명",),
    "lon": ("경도",),
    "lat": ("위도",),
    "bld_mng_no": ("건물관리번호",),
    "flr_no": ("층정보",),
    "rdnm_adr": ("도로명주소", "도로명"),
}
_REQUIRED = ("bizes_id", "lon", "lat")

_INSERT_SQL = """
INSERT INTO sbiz_stores (
  bizes_id, bizes_nm, inds_lcls_cd, inds_lcls_nm, inds_mcls_cd, inds_mcls_nm,
  inds_scls_cd, inds_scls_nm, lon, lat, bld_mng_no, flr_no, rdnm_adr,
  data_quarter, geom_5179, ingested_at
)
VALUES %s
ON CONFLICT (bizes_id) DO NOTHING
"""
_VALUES_TEMPLATE = (
    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
    "ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179), NOW())"
)


def _quarter_from_name(name: str) -> str:
    m = re.search(r"(20\d{6})", name)
    return m.group(1) if m else "unknown"


def _resolve_header(fieldnames: list[str]) -> dict[str, str]:
    cleaned = {f.strip().lstrip("﻿"): f for f in fieldnames}
    mapping: dict[str, str] = {}
    for column, candidates in _COLUMN_CANDIDATES.items():
        for cand in candidates:
            if cand in cleaned:
                mapping[column] = cleaned[cand]
                break
    missing = [c for c in _REQUIRED if c not in mapping]
    if missing:
        raise SystemExit(
            f"CSV header missing required columns {missing}; got: {fieldnames[:30]}"
        )
    return mapping


def _open_csv(zf: zipfile.ZipFile, name: str) -> io.TextIOWrapper:
    raw = zf.open(name)
    head = raw.read(4096)
    raw.close()
    encoding = "utf-8-sig"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "cp949"
    return io.TextIOWrapper(zf.open(name), encoding=encoding, newline="")


def ingest(zip_path: str, *, all_regions: bool = False) -> None:
    t0 = time.perf_counter()
    zf = zipfile.ZipFile(zip_path)
    csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if not csv_names:
        raise SystemExit("ZIP contains no CSV files")
    targets = csv_names if all_regions else [n for n in csv_names if "서울" in n]
    if not targets:
        raise SystemExit(f"서울 CSV not found; entries: {csv_names[:10]} (use --all?)")

    quarter = _quarter_from_name(zip_path) or _quarter_from_name(targets[0])
    print(f"quarter {quarter}: importing {len(targets)}/{len(csv_names)} CSVs")

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute("TRUNCATE sbiz_stores")
    conn.commit()

    total = skipped = 0
    for name in targets:
        f = _open_csv(zf, name)
        reader = csv.DictReader(f)
        mapping = _resolve_header(list(reader.fieldnames or []))

        batch: list[tuple] = []
        file_rows = 0
        for row in reader:
            def val(col: str) -> str:
                key = mapping.get(col)
                return (row.get(key) or "").strip() if key else ""

            bizes_id = val("bizes_id")
            try:
                lon = float(val("lon"))
                lat = float(val("lat"))
            except ValueError:
                skipped += 1
                continue
            if not bizes_id or not lon or not lat:
                skipped += 1
                continue
            batch.append(
                (
                    bizes_id,
                    val("bizes_nm"),
                    val("inds_lcls_cd"),
                    val("inds_lcls_nm"),
                    val("inds_mcls_cd"),
                    val("inds_mcls_nm"),
                    val("inds_scls_cd"),
                    val("inds_scls_nm"),
                    lon,
                    lat,
                    val("bld_mng_no"),
                    val("flr_no"),
                    val("rdnm_adr"),
                    quarter,
                    lon,
                    lat,
                )
            )
            file_rows += 1
            if len(batch) >= 5000:
                psycopg2.extras.execute_values(
                    cur, _INSERT_SQL, batch, template=_VALUES_TEMPLATE, page_size=1000
                )
                conn.commit()
                batch = []
        if batch:
            psycopg2.extras.execute_values(
                cur, _INSERT_SQL, batch, template=_VALUES_TEMPLATE, page_size=1000
            )
            conn.commit()
        total += file_rows
        print(f"  {name}: {file_rows:,} rows")
        f.close()

    cur.execute("SELECT count(*) FROM sbiz_stores")
    n = cur.fetchone()[0]
    conn.close()
    dt = time.perf_counter() - t0
    print(f"DONE in {dt:.0f}s: {n:,} stores in DB (parsed {total:,}, skipped {skipped:,})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", help="분기 전체 덤프 ZIP 경로")
    parser.add_argument("--all", action="store_true", help="서울 외 전국 CSV도 적재")
    args = parser.parse_args()
    try:
        ingest(args.zip_path, all_regions=args.all)
    except zipfile.BadZipFile:
        print("ZIP이 아직 완전하지 않습니다 (다운로드 진행 중?)", file=sys.stderr)
        sys.exit(1)
