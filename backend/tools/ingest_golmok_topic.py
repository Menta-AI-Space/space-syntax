"""Ingest Seoul 상권분석서비스 non-sales topics into Postgres.

Topics:
  flpop   길단위인구  (OA-15568 상권 / OA-22178 행정동 / OA-22179 자치구)
  stores  점포        (OA-15577 상권 / OA-22172 행정동 / OA-22173 자치구)
  change  상권변화지표 (OA-15576 상권 / OA-15575 행정동)

Accepts sheet-export CSV/JSON and yearly CSV/zip attachments (same formats
as ingest_golmok_sales; parsing helpers are imported from it). Region
granularity is auto-detected per row (상권 > 행정동 > 자치구).

Usage:
    python tools/ingest_golmok_topic.py <flpop|stores|change> <files...>
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

# extra Korean-header mappings for these topics' indexed columns
KO_MAP.update({
    "총_유동인구_수": "tot_flpop_co",
    "전체_점포_수": "stor_co",
    "점포_수": "stor_co",
    "상권_변화_지표": "trdar_chnge_ix",
    "상권_변화_지표_명": "trdar_chnge_ix_nm",
})

TOPICS = {
    "flpop": {
        "table": "golmok_flpop",
        "has_svc": False,
        "ddl": """
            CREATE TABLE IF NOT EXISTS golmok_flpop (
                stdr_yyqu_cd text NOT NULL,
                region_type text NOT NULL,
                region_code text NOT NULL,
                region_name text,
                tot_flpop bigint,
                detail jsonb NOT NULL,
                PRIMARY KEY (stdr_yyqu_cd, region_type, region_code)
            );
            CREATE INDEX IF NOT EXISTS golmok_flpop_region_idx
                ON golmok_flpop (region_type, region_code);
        """,
        "insert": """
            INSERT INTO golmok_flpop
              (stdr_yyqu_cd, region_type, region_code, region_name, tot_flpop, detail)
            VALUES %s
            ON CONFLICT (stdr_yyqu_cd, region_type, region_code)
            DO UPDATE SET region_name = EXCLUDED.region_name,
                          tot_flpop = EXCLUDED.tot_flpop,
                          detail = EXCLUDED.detail
        """,
        "values": lambda row, base: (*base, to_int(row.get("tot_flpop_co")), Json(row)),
    },
    "stores": {
        "table": "golmok_stores",
        "has_svc": True,
        "ddl": """
            CREATE TABLE IF NOT EXISTS golmok_stores (
                stdr_yyqu_cd text NOT NULL,
                region_type text NOT NULL,
                region_code text NOT NULL,
                region_name text,
                svc_cd text NOT NULL DEFAULT '',
                svc_nm text,
                stor_co integer,
                detail jsonb NOT NULL,
                PRIMARY KEY (stdr_yyqu_cd, region_type, region_code, svc_cd)
            );
            CREATE INDEX IF NOT EXISTS golmok_stores_region_idx
                ON golmok_stores (region_type, region_code);
            CREATE INDEX IF NOT EXISTS golmok_stores_svc_idx
                ON golmok_stores (svc_cd);
        """,
        "insert": """
            INSERT INTO golmok_stores
              (stdr_yyqu_cd, region_type, region_code, region_name,
               svc_cd, svc_nm, stor_co, detail)
            VALUES %s
            ON CONFLICT (stdr_yyqu_cd, region_type, region_code, svc_cd)
            DO UPDATE SET region_name = EXCLUDED.region_name,
                          svc_nm = EXCLUDED.svc_nm,
                          stor_co = EXCLUDED.stor_co,
                          detail = EXCLUDED.detail
        """,
        "values": lambda row, base: (
            *base,
            str(row.get("svc_induty_cd") or ""),
            row.get("svc_induty_cd_nm"),
            to_int(row.get("stor_co")),
            Json(row),
        ),
    },
    "change": {
        "table": "golmok_change",
        "has_svc": False,
        "ddl": """
            CREATE TABLE IF NOT EXISTS golmok_change (
                stdr_yyqu_cd text NOT NULL,
                region_type text NOT NULL,
                region_code text NOT NULL,
                region_name text,
                change_ix text,
                change_ix_nm text,
                detail jsonb NOT NULL,
                PRIMARY KEY (stdr_yyqu_cd, region_type, region_code)
            );
            CREATE INDEX IF NOT EXISTS golmok_change_region_idx
                ON golmok_change (region_type, region_code);
        """,
        "insert": """
            INSERT INTO golmok_change
              (stdr_yyqu_cd, region_type, region_code, region_name,
               change_ix, change_ix_nm, detail)
            VALUES %s
            ON CONFLICT (stdr_yyqu_cd, region_type, region_code)
            DO UPDATE SET region_name = EXCLUDED.region_name,
                          change_ix = EXCLUDED.change_ix,
                          change_ix_nm = EXCLUDED.change_ix_nm,
                          detail = EXCLUDED.detail
        """,
        "values": lambda row, base: (
            *base,
            row.get("trdar_chnge_ix"),
            row.get("trdar_chnge_ix_nm"),
            Json(row),
        ),
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
