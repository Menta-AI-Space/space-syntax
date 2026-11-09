"""Aggregate 환경부 EV 충전건별 충전량 합성데이터 → 시군구 수요 강도.

Source ZIP: data.go.kr 15149540 (2024년 12.2M rows, 월별×5파트 CSV 60개,
cp949). Streams every CSV, aggregates (시도, 시군구, 충전소유형대분류) →
sessions / total_kwh / distinct chargers, then truncate+reload
ev_charging_demand. 합성데이터라 건별 정밀도는 낮음 — 집계 비교 용도만.

Usage:
    python tools/ingest_ev_charging_demand.py path/to/inner_or_outer.zip
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402


def open_inner_zip(path: str) -> zipfile.ZipFile:
    z = zipfile.ZipFile(path)
    names = z.namelist()
    if len(names) == 1 and names[0].lower().endswith(".zip"):
        # 포털 배포본은 zip-in-zip — 내부 zip을 임시 추출
        inner_path = Path(path).parent / names[0]
        if not inner_path.exists():
            z.extract(names[0], Path(path).parent)
        return zipfile.ZipFile(inner_path)
    return z


def main(zip_path: str) -> int:
    z = open_inner_zip(zip_path)
    csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
    print(f"{len(csvs)} CSV files")

    agg: dict[tuple[str, str, str], list] = defaultdict(lambda: [0, 0.0])
    chargers: dict[tuple[str, str, str], set] = defaultdict(set)
    bad = 0
    for idx, name in enumerate(sorted(csvs), 1):
        with z.open(name) as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="cp949", errors="replace"))
            header = next(reader, None)
            for row in reader:
                if len(row) < 11:
                    bad += 1
                    continue
                try:
                    kwh = float(row[8] or 0)
                except ValueError:
                    bad += 1
                    continue
                key = (row[9].strip(), row[10].strip(), row[3].strip() or "미분류")
                entry = agg[key]
                entry[0] += 1
                entry[1] += kwh
                chargers[key].add((row[1], row[2]))
        if idx % 10 == 0:
            print(f"  {idx}/{len(csvs)} files, keys={len(agg):,}")

    rows = [
        (sido, sgg, st, cnt, round(kwh, 1), len(chargers[(sido, sgg, st)]),
         round(kwh / len(chargers[(sido, sgg, st)]), 1) if chargers[(sido, sgg, st)] else None)
        for (sido, sgg, st), (cnt, kwh) in agg.items()
        if sido and sgg
    ]
    conn = psycopg2.connect(settings.database_url)
    with conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE ev_charging_demand")
            execute_values(
                cur,
                """
                INSERT INTO ev_charging_demand
                  (sido, sgg, station_type, sessions, total_kwh, charger_count, kwh_per_charger)
                VALUES %s
                """,
                rows,
                page_size=2000,
            )
    conn.close()
    print(f"DONE {len(rows):,} aggregate rows ({bad:,} bad rows skipped)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1]))
