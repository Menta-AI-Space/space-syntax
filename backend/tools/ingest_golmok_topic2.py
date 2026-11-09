"""Ingest Seoul 상권분석서비스 Tier-2 topics into Postgres.

Topics:
  consumption 소비        (상권/행정동/자치구) — expndtr_totamt 총지출금액
  apartment   아파트      (상권/행정동)        — apt_hsmp_co 단지수, avrg_mktc 평균시세
  workplace   직장인구    (상권/행정동)        — tot_wrc_popltn_co 총직장인구
  facility    집객시설    (상권/행정동)        — no headline column, detail jsonb only

Skips 상권배후지 (catchment buffer, different region concept from 상권
itself — territory_cannibalization.py already computes this) and 서울시
(city-wide single row, trivially derived from gu aggregates) files; don't
feed those into this script.

Accepts the portal's JSON export ({"DESCRIPTION","DATA"}, lowercase keys)
and sheet/attachment CSV (Korean headers). Region granularity auto-detected
per row (상권 > 행정동 > 자치구) via classify_region.

Usage:
    python tools/ingest_golmok_topic2.py <consumption|apartment|workplace|facility> <files...>
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from tools.ingest_golmok_sales import (  # noqa: E402
    KO_MAP,
    classify_region,
    iter_source_files,
    rows_from_csv,
    rows_from_json,
    to_int,
)

KO_MAP.update({
    "총_지출_금액": "expndtr_totamt",
    "총_직장_인구_수": "tot_wrc_popltn_co",
    "아파트_단지_수": "apt_hsmp_co",
    "평균_시가": "avrg_mktc",
})


def to_float(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


TOPICS = {
    "consumption": {
        "table": "golmok_consumption",
        "ddl": """
            CREATE TABLE IF NOT EXISTS golmok_consumption (
                stdr_yyqu_cd text NOT NULL,
                region_type text NOT NULL,
                region_code text NOT NULL,
                region_name text,
                expndtr_totamt bigint,
                detail jsonb NOT NULL,
                PRIMARY KEY (stdr_yyqu_cd, region_type, region_code)
            );
            CREATE INDEX IF NOT EXISTS golmok_consumption_region_idx
                ON golmok_consumption (region_type, region_code);
        """,
        "insert": """
            INSERT INTO golmok_consumption
              (stdr_yyqu_cd, region_type, region_code, region_name, expndtr_totamt, detail)
            VALUES %s
            ON CONFLICT (stdr_yyqu_cd, region_type, region_code)
            DO UPDATE SET region_name = EXCLUDED.region_name,
                          expndtr_totamt = EXCLUDED.expndtr_totamt,
                          detail = EXCLUDED.detail
        """,
        "values": lambda row, base: (*base, to_int(row.get("expndtr_totamt")), Json(row)),
    },
    "apartment": {
        "table": "golmok_apartment",
        "ddl": """
            CREATE TABLE IF NOT EXISTS golmok_apartment (
                stdr_yyqu_cd text NOT NULL,
                region_type text NOT NULL,
                region_code text NOT NULL,
                region_name text,
                apt_hsmp_co integer,
                avrg_mktc double precision,
                detail jsonb NOT NULL,
                PRIMARY KEY (stdr_yyqu_cd, region_type, region_code)
            );
            CREATE INDEX IF NOT EXISTS golmok_apartment_region_idx
                ON golmok_apartment (region_type, region_code);
        """,
        "insert": """
            INSERT INTO golmok_apartment
              (stdr_yyqu_cd, region_type, region_code, region_name,
               apt_hsmp_co, avrg_mktc, detail)
            VALUES %s
            ON CONFLICT (stdr_yyqu_cd, region_type, region_code)
            DO UPDATE SET region_name = EXCLUDED.region_name,
                          apt_hsmp_co = EXCLUDED.apt_hsmp_co,
                          avrg_mktc = EXCLUDED.avrg_mktc,
                          detail = EXCLUDED.detail
        """,
        "values": lambda row, base: (
            *base,
            to_int(row.get("apt_hsmp_co")),
            to_float(row.get("avrg_mktc")),
            Json(row),
        ),
    },
    "workplace": {
        "table": "golmok_workplace",
        "ddl": """
            CREATE TABLE IF NOT EXISTS golmok_workplace (
                stdr_yyqu_cd text NOT NULL,
                region_type text NOT NULL,
                region_code text NOT NULL,
                region_name text,
                tot_wrc_popltn_co integer,
                detail jsonb NOT NULL,
                PRIMARY KEY (stdr_yyqu_cd, region_type, region_code)
            );
            CREATE INDEX IF NOT EXISTS golmok_workplace_region_idx
                ON golmok_workplace (region_type, region_code);
        """,
        "insert": """
            INSERT INTO golmok_workplace
              (stdr_yyqu_cd, region_type, region_code, region_name,
               tot_wrc_popltn_co, detail)
            VALUES %s
            ON CONFLICT (stdr_yyqu_cd, region_type, region_code)
            DO UPDATE SET region_name = EXCLUDED.region_name,
                          tot_wrc_popltn_co = EXCLUDED.tot_wrc_popltn_co,
                          detail = EXCLUDED.detail
        """,
        "values": lambda row, base: (*base, to_int(row.get("tot_wrc_popltn_co")), Json(row)),
    },
    "facility": {
        "table": "golmok_facility",
        "ddl": """
            CREATE TABLE IF NOT EXISTS golmok_facility (
                stdr_yyqu_cd text NOT NULL,
                region_type text NOT NULL,
                region_code text NOT NULL,
                region_name text,
                detail jsonb NOT NULL,
                PRIMARY KEY (stdr_yyqu_cd, region_type, region_code)
            );
            CREATE INDEX IF NOT EXISTS golmok_facility_region_idx
                ON golmok_facility (region_type, region_code);
        """,
        "insert": """
            INSERT INTO golmok_facility
              (stdr_yyqu_cd, region_type, region_code, region_name, detail)
            VALUES %s
            ON CONFLICT (stdr_yyqu_cd, region_type, region_code)
            DO UPDATE SET region_name = EXCLUDED.region_name,
                          detail = EXCLUDED.detail
        """,
        "values": lambda row, base: (*base, Json(row)),
    },
}


def main(topic: str, paths: list[str]) -> None:
    cfg = TOPICS[topic]
    conn = psycopg2.connect(settings.database_url)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(cfg["ddl"])

    total = 0
    for label, fname, raw in iter_source_files(paths):
        try:
            rows = rows_from_json(raw) if fname.lower().endswith(".json") else rows_from_csv(raw)
        except Exception as e:
            print(f"SKIP {label}: {e}")
            continue
        records, dropped = [], 0
        for r in rows:
            r = {k.lower(): v for k, v in r.items()}
            quarter = r.get("stdr_yyqu_cd")
            region = classify_region(r)
            if not quarter or region is None:
                dropped += 1
                continue
            rtype, rcode, rname = region
            base = (str(quarter), rtype, rcode, rname)
            records.append(cfg["values"](r, base))
        if not records:
            print(f"SKIP {label}: no usable rows (dropped={dropped})")
            continue
        execute_values(cur, cfg["insert"], records, page_size=1000)
        conn.commit()
        total += len(records)
        print(f"OK   {label}: {len(records):,} rows (dropped={dropped})")

    cur.execute(
        f"SELECT region_type, count(*), min(stdr_yyqu_cd), max(stdr_yyqu_cd) "
        f"FROM {cfg['table']} GROUP BY region_type ORDER BY region_type"
    )
    for rtype, n, qmin, qmax in cur.fetchall():
        print(f"TABLE {rtype}: {n:,} rows, quarters {qmin}..{qmax}")
    conn.close()
    print(f"DONE topic={topic} ingested {total:,} rows this run")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in TOPICS:
        print(__doc__)
        raise SystemExit(1)
    main(sys.argv[1], sys.argv[2:])
