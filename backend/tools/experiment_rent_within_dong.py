"""§21 — 같은 동 «안»에서 목이 「시장 임대료」 차이를 설명하는가.

docs/DECISIONS_LAND_MODEL.md §21.

20절이 같은 상권 안에서 목과 공시지가의 관계를 확인했지만 **최상/최하 1.14배**로
작았다. 그리고 공시지가는 **행정이 매기는 값이라 평활하고**(4절 순환성까지 걸린다),
그 1.14배가 「길 구조가 값을 가르는 정도」인지 「행정이 그 차이를 반영하는 정도」인지
가릴 수 없다.

이 실험은 **시장이 매기는 값**으로 다시 잰다. `officetel_rents`(오피스텔 임대 실거래
88,304건, 2025-08~2026-07)는 지번이 있어 PNU를 조립할 수 있고, **행정이 매기지
않으므로 순환성이 없다.**

⚠️ **한계를 먼저 적는다.**
  · 오피스텔은 **상가가 아니다.** 주거 수요가 크므로 상업 동선(Choice)과의 관계가
    상가보다 약할 수 있다. 이건 대리 지표이지 정답이 아니다.
  · 오피스텔은 역세권·대로변에 몰려 있어 **범위 제한**이 걸린다(이미 다 목이 좋은
    곳끼리 비교하면 변별이 안 된다 — 14절 발달상권에서 본 그 문제).
  · 기간이 1년뿐이라 시점 비교가 아니라 **수준 비교**다.

⚠️ **환산월세** = 월세 + 보증금 × 4%/12 (전월세전환율 연 4% 가정). 전세는 제외한다.

⚠️ **운영 DB에 쓰지 않는다.** SELECT만 한다.

Usage:
    python tools/experiment_rent_within_dong.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONV = 0.04 / 12          # 전월세전환율 연 4% → 월

SQL = """
WITH r AS (
    SELECT o.lawd_cd, o.umd_nm, o.jibun, o.bldg_nm,
           o.monthly_rent_manwon + o.deposit_manwon * %(conv)s AS rent_eq,
           o.area_sqm, o.floor
    FROM   officetel_rents o
    WHERE  o.is_jeonse = false
      AND  o.monthly_rent_manwon > 0
      AND  o.area_sqm > 5
      AND  o.jibun ~ '^[0-9]+(-[0-9]+)?$'
),
keyed AS (
    SELECT r.*,
           l.region_cd,
           split_part(r.jibun, '-', 1) AS bun,
           coalesce(nullif(split_part(r.jibun, '-', 2), ''), '0') AS ji
    FROM   r
    JOIN   legal_dong_codes l
           ON left(l.region_cd, 5) = r.lawd_cd
          AND l.lowest_name = r.umd_nm
),
pnued AS (
    SELECT region_cd || '1' || lpad(bun, 4, '0') || lpad(ji, 4, '0') AS pnu,
           bldg_nm, rent_eq / area_sqm AS rent_per_sqm, area_sqm, floor
    FROM   keyed
),
agg AS (                       -- 필지 단위로 모은다 (같은 건물 여러 호실 → 중앙값)
    SELECT pnu, count(*) AS n_deal,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY rent_per_sqm) AS rent_sqm,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY area_sqm)     AS area_med,
           min(bldg_nm) AS bldg_nm
    FROM   pnued
    GROUP  BY pnu
    HAVING count(*) >= %(mindeal)s
)
SELECT a.pnu, a.n_deal, a.rent_sqm, a.area_med, a.bldg_nm,
       left(a.pnu, 10) AS ldong_code, left(a.pnu, 5) AS sgg,
       m.choice_raw, m.integration_raw, m.structure_raw, m.dist,
       lp.price_per_sqm
FROM   agg a
JOIN   parcel_centroids c ON c.pnu = a.pnu
CROSS  JOIN LATERAL (
    SELECT rs.choice_raw, rs.integration_raw, rs.structure_raw,
           rs.geom_5179 <-> c.geom_5179 AS dist
    FROM   road_segment_structure rs
    WHERE  ST_DWithin(rs.geom_5179, c.geom_5179, 50)
    ORDER  BY rs.geom_5179 <-> c.geom_5179
    LIMIT  1
) m
LEFT   JOIN land_prices lp ON lp.pnu = a.pnu AND lp.base_year = 2026
"""


def within(d: pd.DataFrame, col: str, ycol: str, strat: str, min_n: int):
    """계층 평균을 뺀 잔차끼리의 상관 (= 계층 고정효과)."""
    cnt = d.groupby(strat)[col].transform("size")
    g = d[cnt >= min_n].copy()
    if len(g) < 100:
        return None, 0, 0
    y = np.log(g[ycol])
    b = g.groupby(strat)
    r = (g[col] - b[col].transform("mean")).corr(y - y.groupby(g[strat]).transform("mean"))
    return r, len(g), g[strat].nunique()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-deal", type=int, default=3, help="필지당 최소 계약 건수")
    ap.add_argument("--min-n", type=int, default=5, help="계층당 최소 필지")
    ap.add_argument("--out", default="backend/tools/out/rent_within_dong.csv")
    args = ap.parse_args()

    print(f"\n같은 동 «안»에서 목이 시장 임대료를 가르는가 — 오피스텔 월세 실거래")
    print("=" * 76)

    conn = psycopg2.connect(_db_url())
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout='900s'")
        d = pd.read_sql(SQL, conn, params={"conv": CONV, "mindeal": args.min_deal})
    finally:
        conn.close()

    print(f"  필지 {len(d):,}개 (계약 {args.min_deal}건 이상) · 법정동 {d.ldong_code.nunique()}개 · "
          f"자치구 {d.sgg.nunique()}개 · 접면도로 중앙값 {d.dist.median():.0f}m")
    print(f"  ㎡당 환산월세 중앙값 {d.rent_sqm.median():.2f}만원 "
          f"(사분위 {d.rent_sqm.quantile(.25):.2f} ~ {d.rent_sqm.quantile(.75):.2f})")

    for col, label in (("choice_raw", "Choice"), ("integration_raw", "Integration"),
                       ("structure_raw", "종합 목")):
        print(f"\n── {label} ──")
        # 전체 (계층 통제 없음)
        print(f"  전체            corr(목, log 임대료) = {d[col].corr(np.log(d.rent_sqm)):+.3f}")
        for strat, nm in (("sgg", "자치구"), ("ldong_code", "법정동")):
            r, n, k = within(d, col, "rent_sqm", strat, args.min_n)
            if r is not None:
                print(f"  {nm} «안»        corr = {r:+.3f}   (필지 {n:,} · {nm} {k}개)")
        # 참고 — 같은 방식으로 공시지가도
        dd = d[d.price_per_sqm.notna() & (d.price_per_sqm > 0)]
        r2, n2, _ = within(dd, col, "price_per_sqm", "ldong_code", args.min_n)
        if r2 is not None:
            print(f"  (참고) 법정동 «안» 공시지가        corr = {r2:+.3f}   (필지 {n2:,})")

    # 법정동 안 목 5분위별 임대료 (동 평균 = 1.00)
    cnt = d.groupby("ldong_code").pnu.transform("size")
    g = d[cnt >= args.min_n].copy()
    b = g.groupby("ldong_code")
    g["rel"] = g.rent_sqm / b.rent_sqm.transform("mean")
    g["q"] = b.choice_raw.transform(lambda s: pd.qcut(s.rank(method="first"), 5,
                                                      labels=["1", "2", "3", "4", "5"]))
    t = g.groupby("q", observed=True).rel.median()
    print(f"\n법정동 «안» Choice 5분위별 ㎡당 임대료 (그 동 평균 = 1.00) · 필지 {len(g):,}")
    print("  " + " · ".join(f"{i} {v:.2f}배" for i, v in t.items())
          + f"   → 최상/최하 {t.iloc[-1]/t.iloc[0]:.2f}배")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}")


def _db_url() -> str:
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("backend/.env 에 DATABASE_URL 이 없다")


if __name__ == "__main__":
    main()
