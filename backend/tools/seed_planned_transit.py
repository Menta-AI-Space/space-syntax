"""신설 역·노선 계획 → planned_transit 적재 (사람이 채우는 표).

⚠️ **이 데이터는 정형화된 공공데이터가 없다** (2026-08-26 확인). GTX·경전철·연장
노선이 노선별 고시·보도자료로 흩어져 있고, 좌표가 붙은 개방 데이터셋이 없다.
그래서 이 표는 **자동 적재가 아니라 사람이 확인해 채우는 표**다.

자동으로 채워지는 척하면 안 되므로 두 컬럼을 필수로 둔다:

- ``src_url``     — 근거 문서(고시·보도자료) 주소
- ``verified_on`` — 사람이 그 문서를 직접 확인한 날짜

둘 중 하나라도 비면 적재를 거부한다. 출처 없는 행이 통제변수로 들어가면
"우리가 근거를 만들었다"가 아니라 "우리가 지어냈다"가 되기 때문이다.

⚠️ **개통 예정일을 feature 로 쓰지 않는다.** 예정일은 자주 바뀐다. 시장이 그
사실을 안 날짜는 개통일이 아니라 **고시일(announced_on)** 이므로, 시점 기준은
고시일로만 잡는다 (compute_parcel_market_signals.py 가 그렇게 자른다).

CSV 형식 (헤더 필수):
    line_nm,station_nm,stage,announced_on,open_expected,lng,lat,src_url,verified_on,note

    stage 는 계획 / 예타 / 기본계획 / 착공 / 개통 중 하나.
    lng,lat 은 WGS84(EPSG:4326) 십진도.

Run:
  cd backend && .venv/Scripts/python -m tools.seed_planned_transit data/planned_transit.csv
  cd backend && .venv/Scripts/python -m tools.seed_planned_transit --template   # 서식 출력
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from datetime import date, datetime
from pathlib import Path

import psycopg2
import psycopg2.extras

from app.config import settings

DDL = """
CREATE TABLE IF NOT EXISTS planned_transit (
    plan_key        text PRIMARY KEY,
    line_nm         text NOT NULL,
    station_nm      text,
    stage           text NOT NULL,
    announced_on    date NOT NULL,
    open_expected   date,
    src_url         text NOT NULL,
    verified_on     date NOT NULL,
    note            text,
    geom_5179       geometry(Point, 5179) NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS planned_transit_geom_idx ON planned_transit USING GIST (geom_5179);
"""

INSERT = """
INSERT INTO planned_transit
    (plan_key, line_nm, station_nm, stage, announced_on, open_expected,
     src_url, verified_on, note, geom_5179)
VALUES %s
ON CONFLICT (plan_key) DO UPDATE SET
    stage = EXCLUDED.stage,
    announced_on = EXCLUDED.announced_on,
    open_expected = EXCLUDED.open_expected,
    src_url = EXCLUDED.src_url,
    verified_on = EXCLUDED.verified_on,
    note = EXCLUDED.note,
    ingested_at = NOW()
"""

TEMPLATE_SQL = ("(%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179))")

STAGES = ("계획", "예타", "기본계획", "착공", "개통")

CSV_TEMPLATE = """line_nm,station_nm,stage,announced_on,open_expected,lng,lat,src_url,verified_on,note
위례신사선,신사역,예타,2025-04-30,,127.0203,37.5163,https://example.go.kr/notice/1234,2026-08-26,신속예타 대상 선정
"""


def parse_date(raw: str, field: str, line_no: int) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise SystemExit(f"{line_no}행: {field} 날짜 형식을 못 읽었다 — {s!r}")


def read_rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for line_no, row in enumerate(csv.DictReader(fh), start=2):
            line = (row.get("line_nm") or "").strip()
            if not line:
                continue

            # 출처와 확인일이 없으면 «받지 않는다». 이 표의 존재 이유가
            # "사람이 확인했다"는 사실 자체이기 때문이다.
            src_url = (row.get("src_url") or "").strip()
            verified = parse_date(row.get("verified_on", ""), "verified_on", line_no)
            if not src_url or verified is None:
                raise SystemExit(
                    f"{line_no}행 거부: src_url·verified_on 은 필수다 "
                    f"(노선={line!r}). 출처 없는 계획은 통제변수로 쓸 수 없다."
                )

            stage = (row.get("stage") or "").strip()
            if stage not in STAGES:
                raise SystemExit(
                    f"{line_no}행: stage 는 {'/'.join(STAGES)} 중 하나여야 한다 — {stage!r}")

            announced = parse_date(row.get("announced_on", ""), "announced_on", line_no)
            if announced is None:
                raise SystemExit(f"{line_no}행: announced_on 은 필수다 (시점 기준이다)")

            try:
                lng = float(row["lng"])
                lat = float(row["lat"])
            except (KeyError, TypeError, ValueError):
                raise SystemExit(f"{line_no}행: lng·lat 을 읽지 못했다") from None
            if not (124 < lng < 132 and 33 < lat < 39):
                raise SystemExit(
                    f"{line_no}행: 좌표가 한국 밖이다 (lng={lng}, lat={lat}). "
                    "경도·위도 순서가 바뀌지 않았는지 확인할 것.")

            station = (row.get("station_nm") or "").strip() or None
            seed = f"{line}|{station}|{announced}"
            yield (
                hashlib.sha1(seed.encode()).hexdigest(),
                line, station, stage, announced,
                parse_date(row.get("open_expected", ""), "open_expected", line_no),
                src_url, verified,
                (row.get("note") or "").strip() or None,
                lng, lat,
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", nargs="?", help="계획 CSV 경로")
    ap.add_argument("--template", action="store_true", help="CSV 서식을 출력하고 끝낸다")
    args = ap.parse_args()

    if args.template:
        print(CSV_TEMPLATE)
        return 0
    if not args.csv:
        ap.error("csv 경로가 필요하다 (서식은 --template)")

    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"파일이 없다: {path}")
    rows = list(read_rows(path))

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute(DDL)
    if rows:
        psycopg2.extras.execute_values(cur, INSERT, rows,
                                       template=TEMPLATE_SQL, page_size=200)
    conn.commit()

    cur.execute("SELECT stage, count(*), min(announced_on), max(announced_on) "
                "FROM planned_transit GROUP BY stage ORDER BY 1")
    print(f"적재 {len(rows)}행\n단계 | 수 | 고시일 범위")
    for stage, n, lo, hi in cur.fetchall():
        print(f"  {stage} | {n} | {lo} ~ {hi}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
