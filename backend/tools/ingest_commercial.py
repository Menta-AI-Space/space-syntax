"""Ingest 서울 상권분석서비스 file downloads into postgres.

Handles four datasets (CSV or ZIP-of-CSV, cp949 or utf-8, auto-detected
by header):

- 추정매출-상권   → trdar_sales    (분기 × 상권 × 서비스업종; 요일/시간대/성연령 분해는 jsonb)
- 점포-상권      → trdar_stores   (분기 × 상권 × 서비스업종; 점포수·개폐업·프랜차이즈 수)
- 직장인구-상권   → trdar_workpop  (분기 × 상권)
- 길단위인구-행정동 → dong_footfall (분기 × 행정동; 시간대/요일 분해 jsonb — joins on adm_cd)
- 상권변화지표-자치구 → gu_change_index (분기 × 자치구)

상권-coded tables become spatially queryable once the 상권영역 polygon
dataset is loaded (needs Seoul OpenAPI key or the 영역 file download);
until then they are joinable by 상권_코드/상권_코드_명.

Usage:
    python tools/ingest_commercial.py <file-or-dir> [...]

Re-ingest replaces rows for the (dataset, 분기) combinations present in
the input files.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS trdar_sales (
    yyq text NOT NULL,
    trdar_cd text NOT NULL,
    trdar_nm text NOT NULL DEFAULT '',
    trdar_se_nm text NOT NULL DEFAULT '',
    svc_cd text NOT NULL,
    svc_nm text NOT NULL DEFAULT '',
    month_sales_amt bigint,
    month_sales_cnt bigint,
    weekday_sales_amt bigint,
    weekend_sales_amt bigint,
    detail jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (yyq, trdar_cd, svc_cd)
);
CREATE INDEX IF NOT EXISTS trdar_sales_svc_idx ON trdar_sales (svc_cd, yyq);
CREATE TABLE IF NOT EXISTS trdar_stores (
    yyq text NOT NULL,
    trdar_cd text NOT NULL,
    trdar_nm text NOT NULL DEFAULT '',
    svc_cd text NOT NULL,
    svc_nm text NOT NULL DEFAULT '',
    store_count integer,
    similar_store_count integer,
    open_count integer,
    close_count integer,
    franchise_count integer,
    PRIMARY KEY (yyq, trdar_cd, svc_cd)
);
CREATE INDEX IF NOT EXISTS trdar_stores_svc_idx ON trdar_stores (svc_cd, yyq);
CREATE TABLE IF NOT EXISTS trdar_workpop (
    yyq text NOT NULL,
    trdar_cd text NOT NULL,
    trdar_nm text NOT NULL DEFAULT '',
    total_workers integer,
    male_workers integer,
    female_workers integer,
    detail jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (yyq, trdar_cd)
);
CREATE TABLE IF NOT EXISTS dong_footfall (
    yyq text NOT NULL,
    adm_cd text NOT NULL,
    adm_nm text NOT NULL DEFAULT '',
    total_footfall bigint,
    detail jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (yyq, adm_cd)
);
CREATE TABLE IF NOT EXISTS gu_change_index (
    yyq text NOT NULL,
    gu_cd text NOT NULL,
    gu_nm text NOT NULL DEFAULT '',
    indicator text NOT NULL DEFAULT '',
    indicator_nm text NOT NULL DEFAULT '',
    detail jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (yyq, gu_cd)
);
"""


def _int(value: str) -> int | None:
    value = value.strip().replace(",", "")
    if not value or value == "*":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _detail(header: list[str], row: list[str], skip: set[int]) -> str:
    d = {header[i]: _int(v) for i, v in enumerate(row) if i not in skip and i < len(header)}
    return json.dumps(d, ensure_ascii=False)


def detect_dataset(header: list[str]) -> str | None:
    cols = set(header)
    if "서비스_업종_코드" in cols and "당월_매출_금액" in cols:
        return "sales"
    if "stor_co" in cols and "trdar_cd" in cols:
        return "stores"
    if "총_직장_인구_수" in cols and "상권_코드" in cols:
        return "workpop"
    if "총_유동인구_수" in cols and "행정동_코드" in cols:
        return "footfall"
    if "상권_변화_지표" in cols:
        return "change"
    return None


def decode(raw: bytes, name: str) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{name}: unknown encoding")


def iter_files(paths: list[str]):
    for arg in paths:
        p = Path(arg)
        candidates = (
            sorted([*p.glob("*.csv"), *p.glob("*.zip")]) if p.is_dir() else [p]
        )
        for f in candidates:
            if f.suffix.lower() == ".zip":
                with zipfile.ZipFile(f) as zf:
                    for info in zf.infolist():
                        if info.filename.lower().endswith(".csv"):
                            yield f"{f.name}:{info.filename}", zf.read(info)
            else:
                yield f.name, f.read_bytes()


def load_rows(cur, dataset: str, header: list[str], rows) -> int:
    idx = {c: i for i, c in enumerate(header)}
    n = 0
    cleared: set[tuple[str, str]] = set()

    def clear(table: str, yyq: str):
        if (table, yyq) not in cleared:
            cur.execute(f"DELETE FROM {table} WHERE yyq = %s", (yyq,))
            cleared.add((table, yyq))

    for row in rows:
        if len(row) < len(header):
            continue
        yyq = row[0].strip()
        if dataset == "sales":
            clear("trdar_sales", yyq)
            skip = {idx[c] for c in (
                "기준_년분기_코드", "상권_구분_코드", "상권_구분_코드_명", "상권_코드",
                "상권_코드_명", "서비스_업종_코드", "서비스_업종_코드_명",
                "당월_매출_금액", "당월_매출_건수", "주중_매출_금액", "주말_매출_금액",
            )}
            cur.execute(
                "INSERT INTO trdar_sales VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (yyq, trdar_cd, svc_cd) DO NOTHING",
                (
                    yyq, row[idx["상권_코드"]].strip(), row[idx["상권_코드_명"]].strip(),
                    row[idx["상권_구분_코드_명"]].strip(), row[idx["서비스_업종_코드"]].strip(),
                    row[idx["서비스_업종_코드_명"]].strip(),
                    _int(row[idx["당월_매출_금액"]]), _int(row[idx["당월_매출_건수"]]),
                    _int(row[idx["주중_매출_금액"]]), _int(row[idx["주말_매출_금액"]]),
                    _detail(header, row, skip),
                ),
            )
        elif dataset == "stores":
            clear("trdar_stores", yyq)
            cur.execute(
                "INSERT INTO trdar_stores VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (yyq, trdar_cd, svc_cd) DO NOTHING",
                (
                    yyq, row[idx["trdar_cd"]].strip(), row[idx["trdar_cd_nm"]].strip(),
                    row[idx["svc_induty_cd"]].strip(), row[idx["svc_induty_cd_nm"]].strip(),
                    _int(row[idx["stor_co"]]), _int(row[idx["similr_induty_stor_co"]]),
                    _int(row[idx["opbiz_stor_co"]]), _int(row[idx["clsbiz_stor_co"]]),
                    _int(row[idx["frc_stor_co"]]),
                ),
            )
        elif dataset == "workpop":
            clear("trdar_workpop", yyq)
            skip = {idx[c] for c in (
                "기준_년분기_코드", "상권_구분_코드", "상권_구분_코드_명", "상권_코드",
                "상권_코드_명", "총_직장_인구_수", "남성_직장_인구_수", "여성_직장_인구_수",
            )}
            cur.execute(
                "INSERT INTO trdar_workpop VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (yyq, trdar_cd) DO NOTHING",
                (
                    yyq, row[idx["상권_코드"]].strip(), row[idx["상권_코드_명"]].strip(),
                    _int(row[idx["총_직장_인구_수"]]), _int(row[idx["남성_직장_인구_수"]]),
                    _int(row[idx["여성_직장_인구_수"]]), _detail(header, row, skip),
                ),
            )
        elif dataset == "footfall":
            clear("dong_footfall", yyq)
            skip = {idx[c] for c in ("기준_년분기_코드", "행정동_코드", "행정동_코드_명", "총_유동인구_수")}
            cur.execute(
                "INSERT INTO dong_footfall VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (yyq, adm_cd) DO NOTHING",
                (
                    yyq, row[idx["행정동_코드"]].strip(), row[idx["행정동_코드_명"]].strip(),
                    _int(row[idx["총_유동인구_수"]]), _detail(header, row, skip),
                ),
            )
        elif dataset == "change":
            clear("gu_change_index", yyq)
            skip = {idx[c] for c in (
                "기준_년분기_코드", "자치구_코드", "자치구_코드_명", "상권_변화_지표", "상권_변화_지표_명",
            )}
            cur.execute(
                "INSERT INTO gu_change_index VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (yyq, gu_cd) DO NOTHING",
                (
                    yyq, row[idx["자치구_코드"]].strip(), row[idx["자치구_코드_명"]].strip(),
                    row[idx["상권_변화_지표"]].strip(), row[idx["상권_변화_지표_명"]].strip(),
                    _detail(header, row, skip),
                ),
            )
        n += 1
    return n


def main(paths: list[str]) -> None:
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute(DDL)
    for name, raw in iter_files(paths):
        try:
            reader = csv.reader(io.StringIO(decode(raw, name)))
            header = next(reader, None) or []
            dataset = detect_dataset(header)
            if dataset is None:
                print(f"SKIP {name}: unrecognized header {header[:4]}")
                continue
            n = load_rows(cur, dataset, header, reader)
            print(f"OK   {name}: dataset={dataset} rows={n:,}")
        except Exception as e:
            print(f"FAIL {name}: {e}")
            conn.rollback()
            continue
        conn.commit()
    for table in ("trdar_sales", "trdar_stores", "trdar_workpop", "dong_footfall", "gu_change_index"):
        cur.execute(f"SELECT count(*), count(DISTINCT yyq) FROM {table}")
        total, quarters = cur.fetchone()
        print(f"TOTAL {table}: {total:,} rows, {quarters} quarters")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    main(sys.argv[1:])
