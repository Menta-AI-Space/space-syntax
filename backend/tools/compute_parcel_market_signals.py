"""필지 × 연도 시장 신호(통제변수) 계산 — parcel_market_signals 채우기.

멘토 지적("저평가 근거가 길 구조뿐이라 주변에 새 건물이 들어서는 변화를 반영
못한다")에 대한 답이다. 여기서 만든 변수는 **통제변수로만** 쓰고 우리 기여(ΔR²)로
세지 않는다 — 통제의 목적은 정확도가 아니라 «판정을 틀리지 않는 것»이다.

⚠️⚠️ **이 파일이 시점 절단을 책임지는 유일한 곳이다.**

개별공시지가의 기준일은 1월 1일이다. 그래서 base_year 행에는 **그해 1월 1일까지
이미 공표된 사실만** 담는다. 건축인허가 표에는 허가일·착공일·사용승인일이 함께
들어 있는데, 준공 사실을 그 이전 연도 행에 붙이면 **미래를 보는 것**이고 그러면
백테스트가 전부 거짓이 된다 (CLAUDE.md "A/B에는 발표일이 없는 feature를 넣을 수
없다").

그래서 규칙은 하나다 — **가장 이른 날짜(허가일)를 시점 기준으로 쓰고, 그 날짜가
컷 이전인 행만 센다.** 착공일·사용승인일은 원시 표에 보존만 하고 여기서 쓰지 않는다.

신설 교통도 같다. 개통 «예정»일은 자주 바뀌므로 쓰지 않고, 시장이 그 사실을 안
날짜인 **고시일(announced_on)** 을 기준으로 쓴다.

Run:
  cd backend && .venv/Scripts/python -m tools.compute_parcel_market_signals --years 2018-2026
  cd backend && .venv/Scripts/python -m tools.compute_parcel_market_signals --years 2025 --sgg 11110
"""

from __future__ import annotations

import argparse
import sys
import time

import psycopg2

from app.config import settings

# 「최근 3년」인 이유 — 허가에서 준공까지 대개 2~3년이라, 이 창이 곧 "지금 짓고 있다"다.
PERMIT_WINDOW_YEARS = 3
# 연면적 1만㎡ 이상을 대형으로 본다. 대형 하나가 소규모 여러 건보다 영향이 크므로
# 건수와 따로 센다.
BIG_PERMIT_SQM = 10_000.0

DDL = """
CREATE TABLE IF NOT EXISTS parcel_market_signals (
  pnu                       text NOT NULL,
  base_year                 int  NOT NULL,
  permits_300m_3y           int NOT NULL DEFAULT 0,
  permits_500m_3y           int NOT NULL DEFAULT 0,
  permit_area_300m_3y       double precision NOT NULL DEFAULT 0,
  big_permit_500m_3y        int NOT NULL DEFAULT 0,
  nearest_permit_m          double precision,
  years_since_permit        double precision,
  large_store_1km           int NOT NULL DEFAULT 0,
  large_store_opened_1km_3y int NOT NULL DEFAULT 0,
  nearest_large_store_m     double precision,
  nearest_planned_station_m double precision,
  planned_station_stage     text,
  vacancy_rate              double precision,
  vacancy_region_level      text,
  computed_at               timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (pnu, base_year)
);
CREATE INDEX IF NOT EXISTS parcel_market_signals_year_idx ON parcel_market_signals (base_year);
"""

# ⚠️ 컷은 %(cut)s = make_date(base_year, 1, 1) 하나로만 표현한다.
#    조건을 두 군데로 나누면 한쪽만 고치는 사고가 난다.
UPSERT = """
WITH target AS (
    SELECT p.pnu, p.geom_5179
      FROM parcel_centroids p
     WHERE (%(sgg)s IS NULL OR p.pnu LIKE %(sgg_like)s)
),
-- 컷 이전에 «허가가 난» 건만 본다. 착공·준공일은 여기서 쓰지 않는다.
permit AS (
    SELECT b.geom_5179, b.tot_area, b.permit_date
      FROM building_permits b
     WHERE b.geom_5179 IS NOT NULL
       AND b.permit_date IS NOT NULL
       AND b.permit_date <  %(cut)s
       AND b.permit_date >= (%(cut)s::date - make_interval(years => %(win)s))
),
-- 최근접 허가는 창(3년)을 두지 않는다 — "언제였나"를 years_since_permit 으로
-- 따로 내보내므로, 여기서 자르면 그 값이 항상 3 미만으로 눌린다.
permit_any AS (
    SELECT b.geom_5179, b.permit_date
      FROM building_permits b
     WHERE b.geom_5179 IS NOT NULL
       AND b.permit_date IS NOT NULL
       AND b.permit_date < %(cut)s
),
store AS (
    SELECT s.geom_5179, s.license_date
      FROM large_stores s
     WHERE s.geom_5179 IS NOT NULL
       AND s.license_date IS NOT NULL
       AND s.license_date < %(cut)s
       -- 폐업한 점포를 "지금 있는 것"으로 세면 반대로 틀린다.
       AND (s.close_date IS NULL OR s.close_date >= %(cut)s)
),
transit AS (
    SELECT t.geom_5179, t.stage
      FROM planned_transit t
     WHERE t.announced_on < %(cut)s
)
INSERT INTO parcel_market_signals AS m (
    pnu, base_year,
    permits_300m_3y, permits_500m_3y, permit_area_300m_3y, big_permit_500m_3y,
    nearest_permit_m, years_since_permit,
    large_store_1km, large_store_opened_1km_3y, nearest_large_store_m,
    nearest_planned_station_m, planned_station_stage
)
SELECT t.pnu,
       %(year)s,
       COALESCE(pa.n300, 0),
       COALESCE(pa.n500, 0),
       COALESCE(pa.area300, 0),
       COALESCE(pa.big500, 0),
       np.dist_m,
       CASE WHEN np.permit_date IS NULL THEN NULL
            ELSE (%(cut)s::date - np.permit_date) / 365.25 END,
       COALESCE(st.n1km, 0),
       COALESCE(st.opened3y, 0),
       ns.dist_m,
       nt.dist_m,
       nt.stage
  FROM target t
  LEFT JOIN LATERAL (
      SELECT count(*) FILTER (WHERE ST_DWithin(p.geom_5179, t.geom_5179, 300))          AS n300,
             count(*)                                                                    AS n500,
             COALESCE(sum(p.tot_area) FILTER (WHERE ST_DWithin(p.geom_5179, t.geom_5179, 300)), 0) AS area300,
             count(*) FILTER (WHERE p.tot_area >= %(big)s)                               AS big500
        FROM permit p
       WHERE ST_DWithin(p.geom_5179, t.geom_5179, 500)
  ) pa ON TRUE
  LEFT JOIN LATERAL (
      SELECT ST_Distance(p.geom_5179, t.geom_5179) AS dist_m, p.permit_date
        FROM permit_any p
       WHERE ST_DWithin(p.geom_5179, t.geom_5179, 2000)
       ORDER BY p.geom_5179 <-> t.geom_5179
       LIMIT 1
  ) np ON TRUE
  LEFT JOIN LATERAL (
      SELECT count(*) AS n1km,
             count(*) FILTER (
                 WHERE s.license_date >= (%(cut)s::date - make_interval(years => %(win)s))
             ) AS opened3y
        FROM store s
       WHERE ST_DWithin(s.geom_5179, t.geom_5179, 1000)
  ) st ON TRUE
  LEFT JOIN LATERAL (
      SELECT ST_Distance(s.geom_5179, t.geom_5179) AS dist_m
        FROM store s
       WHERE ST_DWithin(s.geom_5179, t.geom_5179, 3000)
       ORDER BY s.geom_5179 <-> t.geom_5179
       LIMIT 1
  ) ns ON TRUE
  LEFT JOIN LATERAL (
      SELECT ST_Distance(tr.geom_5179, t.geom_5179) AS dist_m, tr.stage
        FROM transit tr
       WHERE ST_DWithin(tr.geom_5179, t.geom_5179, 3000)
       ORDER BY tr.geom_5179 <-> t.geom_5179
       LIMIT 1
  ) nt ON TRUE
ON CONFLICT (pnu, base_year) DO UPDATE SET
    permits_300m_3y = EXCLUDED.permits_300m_3y,
    permits_500m_3y = EXCLUDED.permits_500m_3y,
    permit_area_300m_3y = EXCLUDED.permit_area_300m_3y,
    big_permit_500m_3y = EXCLUDED.big_permit_500m_3y,
    nearest_permit_m = EXCLUDED.nearest_permit_m,
    years_since_permit = EXCLUDED.years_since_permit,
    large_store_1km = EXCLUDED.large_store_1km,
    large_store_opened_1km_3y = EXCLUDED.large_store_opened_1km_3y,
    nearest_large_store_m = EXCLUDED.nearest_large_store_m,
    nearest_planned_station_m = EXCLUDED.nearest_planned_station_m,
    planned_station_stage = EXCLUDED.planned_station_stage,
    computed_at = NOW()
"""

# 공실률은 단위가 필지가 아니다(시도·상권). 통제용으로만 복사해 오고, 화면에
# 필지 값으로 표시하지 않는다 — 그렇게 하면 생태학적 오류다.
VACANCY = """
UPDATE parcel_market_signals m
   SET vacancy_rate = v.vacancy_rate,
       vacancy_region_level = v.region_level
  FROM (
      SELECT DISTINCT ON (region_cd) region_cd, region_level, vacancy_rate
        FROM vacancy_rates
       WHERE left(yyq, 4)::int = %(year)s
         AND property_type = 'medium_large'
       ORDER BY region_cd, yyq DESC
  ) v
 WHERE m.base_year = %(year)s
   AND left(m.pnu, 2) = left(v.region_cd, 2)
   AND v.region_level = 'sido'
"""


def parse_years(spec: str) -> list[int]:
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", default="2018-2026", help="예: 2025 또는 2018-2026")
    ap.add_argument("--sgg", default=None, help="시군구코드 5자리로 한정 (예: 11110)")
    args = ap.parse_args()

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute(DDL)
    conn.commit()

    for year in parse_years(args.years):
        t0 = time.time()
        params = {
            "year": year,
            "cut": f"{year}-01-01",
            "win": PERMIT_WINDOW_YEARS,
            "big": BIG_PERMIT_SQM,
            "sgg": args.sgg,
            "sgg_like": f"{args.sgg}%" if args.sgg else None,
        }
        cur.execute(UPSERT, params)
        n = cur.rowcount
        cur.execute(VACANCY, {"year": year})
        conn.commit()
        print(f"  {year}: {n:,}행  ({time.time() - t0:.1f}s)")

    cur.execute(
        "SELECT base_year, count(*), "
        "       round(avg(permits_300m_3y)::numeric, 2), "
        "       count(nearest_planned_station_m) "
        "  FROM parcel_market_signals GROUP BY base_year ORDER BY base_year"
    )
    print("\n연도 | 필지 | 300m 내 평균 허가건수 | 신설역 결합")
    for row in cur.fetchall():
        print(f"  {row[0]} | {row[1]:,} | {row[2]} | {row[3]:,}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
