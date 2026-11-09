"""한국부동산원 상업용부동산 임대동향조사 CSV → vacancy_rates 적재.

data.go.kr 「한국부동산원_상업용부동산 임대동향조사_임대정보_분기별 지역별
공실률(오피스/중대형상가/소규모상가)」 파일데이터를 받는다.

**왜 넣는가.** 임대료만 보면 「비싸다」까지만 안다. 공실률을 함께 보면
**「비싼데 안 나간다」**를 구분할 수 있고, 저평가 판정에서 이 구분이 크다.
목이 좋은데 값이 안 붙은 자리와, 목이 좋은데 «아무도 안 들어오는» 자리는
전혀 다른 물건이다.

⚠️ **단위가 필지가 아니다.** 시도 또는 상권 단위다. 그래서 이 값을 필지 판정에
그대로 쓰면 생태학적 오류다 — 통제변수로만 쓰고 화면에 필지 값으로 표시하지
않는다 (CONTEXT.md 「동은 탐색, 필지는 판정」).

⚠️ 배포 파일은 «가로로 긴» 표다(행=지역, 열=분기). 그대로 넣으면 분기를 늘릴
때마다 스키마가 바뀌므로, 여기서 **세로로 녹여서(unpivot)** 넣는다.

Run:
  cd backend && .venv/Scripts/python -m tools.ingest_vacancy_rates \\
      "C:\\...\\공실률(중대형상가).csv" --type medium_large
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

from app.config import settings

DDL = """
CREATE TABLE IF NOT EXISTS vacancy_rates (
    yyq             text NOT NULL,
    region_cd       text NOT NULL,
    region_nm       text,
    region_level    text NOT NULL,
    property_type   text NOT NULL,
    vacancy_rate    double precision,
    rent_index      double precision,
    src             text NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT NOW(),
    PRIMARY KEY (yyq, region_cd, region_level, property_type)
);
"""

INSERT = """
INSERT INTO vacancy_rates
    (yyq, region_cd, region_nm, region_level, property_type, vacancy_rate, rent_index, src)
VALUES %s
ON CONFLICT (yyq, region_cd, region_level, property_type) DO UPDATE SET
    vacancy_rate = EXCLUDED.vacancy_rate,
    rent_index = COALESCE(EXCLUDED.rent_index, vacancy_rates.rent_index),
    src = EXCLUDED.src,
    ingested_at = NOW()
"""

# 시도명 → 앞 2자리 행정구역 코드. PNU 앞 2자리와 맞춘다.
SIDO_CODE = {
    "서울": "11", "부산": "26", "대구": "27", "인천": "28", "광주": "29",
    "대전": "30", "울산": "31", "세종": "36", "경기": "41", "강원": "42",
    "충북": "43", "충남": "44", "전북": "45", "전남": "46", "경북": "47",
    "경남": "48", "제주": "50",
}

# "2025년 1분기" · "2025 1/4" · "2025Q1" 을 모두 2025Q1 로 만든다.
QUARTER_RE = re.compile(r"(20\d{2})\D*([1-4])")


def normalize_quarter(header: str) -> str | None:
    m = QUARTER_RE.search(header or "")
    return f"{m.group(1)}Q{m.group(2)}" if m else None


def resolve_region(name: str) -> tuple[str, str] | None:
    """지역명 → (코드, 레벨). 시도면 sido, 그 외는 상권(trdar)으로 본다."""
    name = (name or "").strip()
    if not name:
        return None
    for key, code in SIDO_CODE.items():
        if name.startswith(key):
            # "서울" 과 "서울 강남대로" 를 구분한다 — 뒤에 말이 붙으면 상권이다.
            return (code, "sido") if len(name) <= 4 else (f"{code}:{name}", "trdar")
    return (name, "trdar")


def parse_num(raw: str) -> float | None:
    s = (raw or "").replace(",", "").replace("%", "").strip()
    if s in ("", "-", "X", "x", "..."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_rows(path: Path, property_type: str):
    for encoding in ("utf-8-sig", "cp949"):
        try:
            fh = path.open(encoding=encoding, newline="")
            fh.readline()
            fh.seek(0)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"인코딩을 판별하지 못했다: {path}")

    with fh:
        reader = csv.reader(fh)
        header = next(reader)
        # 첫 열은 지역명, 나머지 중 분기로 해석되는 열만 쓴다.
        quarters = [(i, normalize_quarter(h)) for i, h in enumerate(header)]
        quarters = [(i, q) for i, q in quarters if q and i > 0]
        if not quarters:
            raise SystemExit(f"분기 열을 찾지 못했다: {header[:6]}")

        for row in reader:
            if not row or not row[0].strip():
                continue
            region = resolve_region(row[0])
            if not region:
                continue
            code, level = region
            for i, yyq in quarters:
                if i >= len(row):
                    continue
                val = parse_num(row[i])
                if val is None:
                    continue
                yield (yyq, code, row[0].strip(), level, property_type, val, None, path.name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--type", dest="property_type", default="medium_large",
                    choices=["office", "medium_large", "small"],
                    help="오피스 / 중대형상가 / 소규모상가")
    args = ap.parse_args()

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute(DDL)

    total = 0
    for pattern in args.csv:
        for path in sorted(Path().glob(pattern)) or [Path(pattern)]:
            if not path.exists():
                print(f"  ✗ 없음: {path}")
                continue
            rows = list(read_rows(path, args.property_type))
            if rows:
                psycopg2.extras.execute_values(cur, INSERT, rows, page_size=1000)
            total += len(rows)
            print(f"  ✓ {path.name}: {len(rows):,}행")
    conn.commit()

    cur.execute("SELECT region_level, property_type, count(*), min(yyq), max(yyq) "
                "FROM vacancy_rates GROUP BY 1, 2 ORDER BY 1, 2")
    print("\n레벨 | 유형 | 행 | 기간")
    for lv, pt, n, lo, hi in cur.fetchall():
        print(f"  {lv} | {pt} | {n:,} | {lo}~{hi}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
