"""서울 지하철 역별 일평균 승하차 인원 → subway_ridership 적재.

출처: 서울열린데이터광장 `CardSubwayStatsNew`
      (지하철 역별·노선별 일별 승하차 인원, 교통카드 집계)

역세권 가치는 "역이 가깝다"가 아니라 "그 역으로 사람이 얼마나 오가느냐"로
갈린다. 강남역과 한적한 단일노선 역을 같은 100점으로 매기던 문제를 없애기
위한 데이터다.

설계 메모:
- API는 하루 단위다. 요일 편차(주말 업무지구 급감)를 없애려 여러 날을
  받아 역별 평균을 낸다. 기본 28일 = 4주 → 요일이 고르게 섞인다.
- 같은 역이 노선마다 따로 집계된다(예: 왕십리역 2/5/경의중앙/수인분당).
  환승 규모를 그대로 반영해야 하므로 역 이름 기준으로 **합산**한다.
  이게 곧 환승역 가중치 역할을 한다.
- 역 이름 표기가 소스마다 다르다: "서울역"/"서울", 괄호 부기("역삼(GS타워)").
  OSM 좌표와 붙이려면 정규화가 필요해 정규화된 이름도 함께 저장한다.
- 최근 며칠은 아직 집계 전이라 비어 있다. 빈 날짜는 건너뛴다(치명 아님).

사용:
    cd backend && python -m tools.ingest_subway_ridership
    python -m tools.ingest_subway_ridership --days 56   # 8주로 넓히기
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

import httpx
import psycopg2
import psycopg2.extras

from app.config import settings
from app.services.spatial_query import get_connection

API_BASE = "http://openapi.seoul.go.kr:8088"
DATASET = "CardSubwayStatsNew"
PAGE_SIZE = 1000
# 집계 지연 — 오늘 데이터는 아직 없다. 넉넉히 띄우고 시작한다.
LAG_DAYS = 5
REQUEST_DELAY_SEC = 0.15

_DDL = """
CREATE TABLE IF NOT EXISTS subway_ridership (
  station_nm        text PRIMARY KEY,
  station_key       text NOT NULL,
  line_count        int  NOT NULL,
  lines             text NOT NULL,
  daily_boarding    bigint NOT NULL,
  daily_alighting   bigint NOT NULL,
  daily_total       bigint NOT NULL,
  sample_days       int  NOT NULL,
  period_start      date,
  period_end        date,
  ingested_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS subway_ridership_key_idx ON subway_ridership (station_key);
"""

_UPSERT = """
INSERT INTO subway_ridership (
  station_nm, station_key, line_count, lines,
  daily_boarding, daily_alighting, daily_total,
  sample_days, period_start, period_end
) VALUES %s
ON CONFLICT (station_nm) DO UPDATE SET
  station_key     = EXCLUDED.station_key,
  line_count      = EXCLUDED.line_count,
  lines           = EXCLUDED.lines,
  daily_boarding  = EXCLUDED.daily_boarding,
  daily_alighting = EXCLUDED.daily_alighting,
  daily_total     = EXCLUDED.daily_total,
  sample_days     = EXCLUDED.sample_days,
  period_start    = EXCLUDED.period_start,
  period_end      = EXCLUDED.period_end,
  ingested_at     = now()
"""


def normalize_station(name: str) -> str:
    """OSM 역 이름과 맞추기 위한 정규화 키.

    괄호 부기 제거("역삼(GS타워)"→"역삼"), 공백 제거, 접미 '역' 제거.
    '서울역'은 정규화하면 '서울'이 되어 OSM의 '서울역'과도 붙는다.
    """
    n = re.sub(r"\(.*?\)", "", name or "").strip()
    n = n.replace(" ", "")
    if len(n) > 1 and n.endswith("역"):
        n = n[:-1]
    return n


def _to_int(value: object) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def fetch_day(client: httpx.Client, key: str, ymd: str) -> list[dict]:
    """하루치 전체 행. 데이터 없는 날은 빈 리스트."""
    rows: list[dict] = []
    start = 1
    while True:
        url = f"{API_BASE}/{key}/json/{DATASET}/{start}/{start + PAGE_SIZE - 1}/{ymd}"
        try:
            resp = client.get(url, timeout=30)
            resp.raise_for_status()
            body = resp.json().get(DATASET) or {}
        except Exception as exc:  # 하루 실패로 전체를 죽이지 않는다
            print(f"  {ymd}: 요청 실패 ({type(exc).__name__}) — 건너뜀")
            return []

        code = (body.get("RESULT") or {}).get("CODE", "")
        if code and code != "INFO-000":
            # INFO-200 = 해당 날짜 데이터 없음 (집계 전이거나 미제공일)
            return []

        page = body.get("row") or []
        rows.extend(page)
        total = body.get("list_total_count") or 0
        start += PAGE_SIZE
        if start > total or not page:
            break
        time.sleep(REQUEST_DELAY_SEC)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="서울 지하철 승하차 인원 적재")
    parser.add_argument("--days", type=int, default=28, help="집계할 일수 (기본 28 = 4주)")
    args = parser.parse_args()

    key = settings.data_seoul_go_api_key.strip()
    if not key:
        print("DATA_SEOUL_GO_API_KEY 가 설정되지 않았습니다.", file=sys.stderr)
        return 2

    # 역별 누계: (승차합, 하차합, 등장일수, 노선집합)
    boarding: dict[str, int] = defaultdict(int)
    alighting: dict[str, int] = defaultdict(int)
    lines: dict[str, set[str]] = defaultdict(set)
    day_seen: dict[str, set[str]] = defaultdict(set)

    end = date.today() - timedelta(days=LAG_DAYS)
    days = [end - timedelta(days=i) for i in range(args.days)]
    got_days: list[date] = []

    print(f"수집 기간: {days[-1]} ~ {days[0]} ({args.days}일 요청)")
    with httpx.Client() as client:
        for d in days:
            ymd = d.strftime("%Y%m%d")
            rows = fetch_day(client, key, ymd)
            if not rows:
                continue
            got_days.append(d)
            for r in rows:
                name = (r.get("SBWY_STNS_NM") or "").strip()
                if not name:
                    continue
                boarding[name] += _to_int(r.get("GTON_TNOPE"))
                alighting[name] += _to_int(r.get("GTOFF_TNOPE"))
                line = (r.get("SBWY_ROUT_LN_NM") or "").strip()
                if line:
                    lines[name].add(line)
                day_seen[name].add(ymd)
            print(f"  {ymd}: {len(rows):,}행")
            time.sleep(REQUEST_DELAY_SEC)

    if not got_days:
        print("수집된 날짜가 없습니다. API 키나 기간을 확인하세요.", file=sys.stderr)
        return 1

    period_start, period_end = min(got_days), max(got_days)
    payload = []
    for name in boarding:
        n_days = len(day_seen[name]) or 1
        b = round(boarding[name] / n_days)
        a = round(alighting[name] / n_days)
        payload.append(
            (
                name,
                normalize_station(name),
                len(lines[name]),
                ",".join(sorted(lines[name])),
                b,
                a,
                b + a,
                n_days,
                period_start,
                period_end,
            )
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            psycopg2.extras.execute_values(cur, _UPSERT, payload, page_size=500)
        conn.commit()

    top = sorted(payload, key=lambda r: r[6], reverse=True)[:5]
    print(f"\n적재 완료: 역 {len(payload)}개 · 실제 수집일 {len(got_days)}일")
    print("상위 5개 역 (일평균 승하차):")
    for r in top:
        print(f"  {r[0]:12} {r[6]:>9,}명  노선 {r[2]}개 ({r[3]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
