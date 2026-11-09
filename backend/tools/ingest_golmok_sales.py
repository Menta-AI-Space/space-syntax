"""Ingest Seoul 상권분석서비스 추정매출 (estimated sales) into Postgres.

Sources (서울열린데이터광장):
- OA-15572 추정매출-상권   (trdar)
- OA-22175 추정매출-행정동 (dong)
- OA-22176 추정매출-자치구 (gu)

Accepts the portal's JSON export ({"DESCRIPTION": {...}, "DATA": [...]},
lowercase english keys) and yearly CSV/zip attachments (Korean headers,
utf-8 or cp949). Region granularity is auto-detected per row from which
code column is present (상권 > 행정동 > 자치구).

Usage:
    python tools/ingest_golmok_sales.py <file.json|file.csv|file.zip|dir> [...]

Re-ingesting upserts on (quarter, region_type, region_code, svc_cd).
"""

from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values

# Windows console defaults to cp1252 — Korean filenames/labels crash print()
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS golmok_sales (
    stdr_yyqu_cd text NOT NULL,
    region_type text NOT NULL,
    region_code text NOT NULL,
    region_name text,
    svc_cd text NOT NULL DEFAULT '',
    svc_nm text,
    selng_amt bigint,
    selng_co bigint,
    detail jsonb NOT NULL,
    PRIMARY KEY (stdr_yyqu_cd, region_type, region_code, svc_cd)
);
CREATE INDEX IF NOT EXISTS golmok_sales_region_idx
    ON golmok_sales (region_type, region_code);
CREATE INDEX IF NOT EXISTS golmok_sales_svc_idx
    ON golmok_sales (svc_cd);
"""

# Korean CSV header -> canonical lowercase JSON key (identity for the rest
# is handled by snake-casing; only keys we index need explicit mapping).
KO_MAP = {
    "기준_년분기_코드": "stdr_yyqu_cd",
    "상권_코드": "trdar_cd",
    "상권_코드_명": "trdar_cd_nm",
    "행정동_코드": "adstrd_cd",
    "행정동_코드_명": "adstrd_cd_nm",
    "자치구_코드": "signgu_cd",
    "자치구_코드_명": "signgu_cd_nm",
    "서비스_업종_코드": "svc_induty_cd",
    "서비스_업종_코드_명": "svc_induty_cd_nm",
    "당월_매출_금액": "thsmon_selng_amt",
    "당월_매출_건수": "thsmon_selng_co",
}


def to_int(val) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def classify_region(row: dict) -> tuple[str, str, str | None] | None:
    """Return (region_type, code, name) from whichever code column exists."""
    for key, rtype in (("trdar_cd", "trdar"), ("adstrd_cd", "dong"), ("signgu_cd", "gu")):
        code = row.get(key)
        if code not in (None, ""):
            return rtype, str(code), row.get(f"{key}_nm")
    return None


def row_to_record(row: dict) -> tuple | None:
    row = {k.lower(): v for k, v in row.items()}
    quarter = row.get("stdr_yyqu_cd")
    region = classify_region(row)
    if not quarter or region is None:
        return None
    rtype, rcode, rname = region
    return (
        str(quarter),
        rtype,
        rcode,
        rname,
        str(row.get("svc_induty_cd") or ""),
        row.get("svc_induty_cd_nm"),
        to_int(row.get("thsmon_selng_amt")),
        to_int(row.get("thsmon_selng_co")),
        Json(row),
    )


def rows_from_json(raw: bytes) -> list[dict]:
    data = json.loads(raw.decode("utf-8-sig"))
    if isinstance(data, dict):
        return data.get("DATA", [])
    return data


def rows_from_csv(raw: bytes) -> list[dict]:
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("unknown encoding")
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for r in reader:
        out.append({KO_MAP.get((k or "").strip(), (k or "").strip().lower()): v
                    for k, v in r.items()})
    return out


def iter_source_files(paths: list[str]):
    for arg in paths:
        p = Path(arg)
        if p.is_dir():
            for f in sorted(p.iterdir()):
                if f.suffix.lower() in (".json", ".csv", ".zip"):
                    yield from iter_source_files([str(f)])
        elif p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as zf:
                for info in zf.infolist():
                    if info.filename.lower().endswith((".csv", ".json")):
                        yield f"{p.name}:{info.filename}", info.filename, zf.read(info)
        else:
            yield p.name, p.name, p.read_bytes()


def main(paths: list[str]) -> None:
    conn = psycopg2.connect(settings.database_url)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(DDL)

    total = 0
    for label, fname, raw in iter_source_files(paths):
        try:
            rows = rows_from_json(raw) if fname.lower().endswith(".json") else rows_from_csv(raw)
        except Exception as e:
            print(f"SKIP {label}: {e}")
            continue
        records, dropped = [], 0
        for r in rows:
            rec = row_to_record(r)
            if rec is None:
                dropped += 1
            else:
                records.append(rec)
        if not records:
            print(f"SKIP {label}: no usable rows (dropped={dropped})")
            continue
        execute_values(
            cur,
            """INSERT INTO golmok_sales
               (stdr_yyqu_cd, region_type, region_code, region_name,
                svc_cd, svc_nm, selng_amt, selng_co, detail)
               VALUES %s
               ON CONFLICT (stdr_yyqu_cd, region_type, region_code, svc_cd)
               DO UPDATE SET region_name = EXCLUDED.region_name,
                             svc_nm = EXCLUDED.svc_nm,
                             selng_amt = EXCLUDED.selng_amt,
                             selng_co = EXCLUDED.selng_co,
                             detail = EXCLUDED.detail""",
            records,
            page_size=1000,
        )
        conn.commit()
        total += len(records)
        print(f"OK   {label}: {len(records):,} rows (dropped={dropped})")

    cur.execute(
        "SELECT region_type, count(*), min(stdr_yyqu_cd), max(stdr_yyqu_cd) "
        "FROM golmok_sales GROUP BY region_type ORDER BY region_type"
    )
    for rtype, n, qmin, qmax in cur.fetchall():
        print(f"TABLE {rtype}: {n:,} rows, quarters {qmin}..{qmax}")
    conn.close()
    print(f"DONE ingested {total:,} rows this run")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    main(sys.argv[1:])
