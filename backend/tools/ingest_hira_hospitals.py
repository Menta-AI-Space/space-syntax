"""Ingest HIRA (심평원) nationwide hospitals with weekend hours into PostGIS.

Source: 보건의료빅데이터개방시스템 "전국 병의원 및 약국 현황" quarterly ZIP
(opendata.hira.or.kr sno=11925). Joins 1.병원정보서비스 (coords, 종별) with
4.의료기관별상세정보서비스_02_세부정보 (토/일 진료시간) on 암호화요양기호.

Usage:
    python tools/ingest_hira_hospitals.py path/to/전국병의원약국현황.zip

Quarterly re-run: truncate + reload (registry churn is high; upsert not worth it).
open_sat/open_sun are derived: both start & end present and start != end.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

BASIC_MARKER = "1.병원정보서비스"
DETAIL_MARKER = "_02_세부정보"


def to_hhmm(value) -> str | None:
    """Normalize 진료시간 cell (900, '0900', NaN, datetime.time) → 'HHMM' or None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "hour"):  # datetime.time
        return f"{value.hour:02d}{value.minute:02d}"
    text = str(value).strip().replace(":", "")
    if not text or text.lower() == "nan":
        return None
    if text.replace(".", "").isdigit():
        text = str(int(float(text))).zfill(4)
    if len(text) != 4 or not text.isdigit() or not 0 <= int(text[:2]) < 24:
        return None
    return text


def main(zip_path: str) -> int:
    zf = zipfile.ZipFile(zip_path)
    basic_name = next(n for n in zf.namelist() if BASIC_MARKER in n)
    detail_name = next(n for n in zf.namelist() if DETAIL_MARKER in n)

    with zf.open(basic_name) as f:
        basic = pd.read_excel(
            f,
            usecols=[
                "암호화요양기호", "요양기관명", "종별코드명",
                "시도코드명", "시군구코드명", "주소", "좌표(X)", "좌표(Y)",
            ],
        )
    with zf.open(detail_name) as f:
        detail = pd.read_excel(
            f,
            usecols=[
                "암호화요양기호",
                "진료시작시간_토요일", "진료종료시간_토요일",
                "진료시작시간_일요일", "진료종료시간_일요일",
                "휴진안내_일요일",
            ],
        )

    df = basic.merge(detail, on="암호화요양기호", how="left")
    # usecols keeps FILE order, not list order — pin positions explicitly
    df = df[[
        "암호화요양기호", "요양기관명", "종별코드명", "시도코드명", "시군구코드명",
        "주소", "좌표(X)", "좌표(Y)",
        "진료시작시간_토요일", "진료종료시간_토요일",
        "진료시작시간_일요일", "진료종료시간_일요일",
        "휴진안내_일요일",
    ]]
    rows = []
    skipped = 0
    for r in df.itertuples(index=False):
        lon, lat = r[6], r[7]
        if pd.isna(lon) or pd.isna(lat) or not lon or not lat:
            skipped += 1
            continue
        sat_s, sat_e = to_hhmm(r[8]), to_hhmm(r[9])
        sun_s, sun_e = to_hhmm(r[10]), to_hhmm(r[11])
        no_sun = None if pd.isna(r[12]) else str(r[12]).strip() or None
        lon, lat = float(lon), float(lat)
        rows.append((
            r[0], r[1], r[2], r[3], r[4],
            None if pd.isna(r[5]) else str(r[5]),
            lon, lat,
            sat_s, sat_e, sun_s, sun_e, no_sun,
            bool(sat_s and sat_e and sat_s != sat_e),
            bool(sun_s and sun_e and sun_s != sun_e),
            lon, lat, lon, lat,  # geom + geom_5179 in template
        ))

    conn = psycopg2.connect(settings.database_url)
    with conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE hira_hospitals")
            execute_values(
                cur,
                """
                INSERT INTO hira_hospitals
                  (ykiho, name, kind, sido, sgg, addr, lon, lat,
                   sat_start, sat_end, sun_start, sun_end, no_trmt_sun,
                   open_sat, open_sun, geom, geom_5179)
                VALUES %s
                """,
                rows,
                template=(
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    " ST_SetSRID(ST_MakePoint(%s,%s),4326),"
                    " ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s),4326),5179))"
                ),
                page_size=2000,
            )
    conn.close()
    print(f"Imported {len(rows):,} hospitals ({skipped} without coords)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1]))
