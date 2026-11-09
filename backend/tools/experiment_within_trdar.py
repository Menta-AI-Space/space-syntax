"""§20 — 같은 상권 «안»에서 길 구조가 값 차이를 설명하는가.

docs/DECISIONS_LAND_MODEL.md §20.

19절이 세운 문구 「상권 평균이 아니라, 이 자리를 봅니다」의 근거를 만든다.
14절이 증명한 것은 **상권 «사이»** 차이(상권 1,648개를 5분위로 갈랐다)이고,
문구가 주장하는 것은 **상권 «안» 필지 사이** 차이다. **둘은 다르다.**

이 실험은 상권을 고정효과로 흡수한 뒤(= 같은 상권 안에서만 비교) 필지별 목 차이가
개별공시지가 차이를 설명하는지 본다. 2026-09-04에 공시지가 서울 전역 12개 연도를
적재해 처음으로 가능해졌다.

⚠️ **이것은 「현재 맞히기」다.** 같은 시점의 목과 공시지가를 비교한다.
12~18절에서 기각된 것은 「미래 맞히기」이고, 이 실험은 그것을 되살리려는 것이 아니다.

⚠️ **순환성이 정면으로 걸린다(4절).** 공시지가 비준표에 도로접면·도로거리가 이미
들어 있으므로, 목과 공시지가의 상관 일부는 행정 산식의 재현이다. **그래서 이 결과를
「우리 기여」로 세지 않는다** — 여기서 재는 것은 "상권 평균으로는 지워지는 차이가
필지 수준에 실재하는가"이지 인과가 아니다.

⚠️ **운영 DB에 쓰지 않는다.** SELECT만 한다.

Usage:
    python tools/experiment_within_trdar.py --year 2026 --sample 60000
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def db_url() -> str:
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("backend/.env 에 DATABASE_URL 이 없다")


SQL = """
WITH samp AS (
    SELECT c.pnu, c.geom_5179
    FROM   parcel_centroids c
    WHERE  left(c.pnu, 2) = '11'
    ORDER  BY md5(c.pnu)
    LIMIT  %(n)s
),
inside AS (                       -- 어느 상권에 속하는가 (폴리곤이 4326이라 점을 변환한다)
    SELECT s.pnu, s.geom_5179, a.trdar_cd, a.trdar_nm, a.trdar_se_nm, a.signgu_nm
    FROM   samp s
    JOIN   trdar_area a ON ST_Intersects(a.geom, ST_Transform(s.geom_5179, 4326))
),
mok AS (                          -- 접면 도로 = 50m 안 최근접 세그먼트 (CONTEXT.md 규칙)
    SELECT i.*, r.choice_raw, r.integration_raw, r.structure_raw, r.dist
    FROM   inside i
    CROSS  JOIN LATERAL (
        SELECT rs.choice_raw, rs.integration_raw, rs.structure_raw,
               rs.geom_5179 <-> i.geom_5179 AS dist
        FROM   road_segment_structure rs
        WHERE  ST_DWithin(rs.geom_5179, i.geom_5179, 50)
        ORDER  BY rs.geom_5179 <-> i.geom_5179
        LIMIT  1
    ) r
)
SELECT m.pnu, m.trdar_cd, m.trdar_nm, m.trdar_se_nm, m.signgu_nm,
       m.choice_raw, m.integration_raw, m.structure_raw, m.dist,
       lp.price_per_sqm
FROM   mok m
JOIN   land_prices lp ON lp.pnu = m.pnu AND lp.base_year = %(y)s
WHERE  lp.price_per_sqm > 0
"""


def report(d: pd.DataFrame, col: str, label: str, min_n: int) -> None:
    """상권 «안» 상관과 상권 «사이» 상관을 나란히 낸다."""
    lp = np.log(d.price_per_sqm)

    # ① 상권 사이 — 상권 평균끼리 (14절에 대응)
    g = d.assign(lp=lp).groupby("trdar_cd").agg(mok=(col, "mean"), lp=("lp", "mean"),
                                                n=("pnu", "size"))
    g = g[g.n >= min_n]
    between = g.mok.corr(g.lp)

    # ② 상권 안 — 상권 평균을 뺀 잔차끼리 (= 상권 고정효과)
    big = d[d.trdar_cd.isin(g.index)].copy()
    big["lp"] = np.log(big.price_per_sqm)
    bg = big.groupby("trdar_cd")
    within = (big[col] - bg[col].transform("mean")).corr(big.lp - bg.lp.transform("mean"))

    print(f"\n── {label} ──")
    print(f"  상권 «사이» (상권 평균끼리)      corr = {between:+.3f}   (상권 {len(g):,}개)")
    print(f"  상권 «안»  (상권 평균 뺀 잔차)   corr = {within:+.3f}   (필지 {len(big):,}개)")

    # 상권 «안» 목 5분위별 공시지가 (상권 평균 대비 배수)
    big["rel"] = big.price_per_sqm / bg.price_per_sqm.transform("mean")
    big["q"] = bg[col].transform(lambda s: pd.qcut(s.rank(method="first"), 5,
                                                   labels=["1", "2", "3", "4", "5"]))
    t = big.groupby("q", observed=True).rel.median()
    print("  상권 안 목 5분위별 공시지가 (그 상권 평균 = 1.00):")
    print("    " + " · ".join(f"{i} {v:.2f}배" for i, v in t.items())
          + f"   → 최상/최하 {t.iloc[-1]/t.iloc[0]:.2f}배")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--sample", type=int, default=60000, help="필지 표본 수")
    ap.add_argument("--min-n", type=int, default=20, help="상권당 최소 표본 필지")
    ap.add_argument("--out", default="backend/tools/out/within_trdar.csv")
    args = ap.parse_args()

    print(f"\n같은 상권 «안»에서 길 구조가 값을 가르는가 — {args.year}년 공시지가")
    print("=" * 76)

    conn = psycopg2.connect(db_url())
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout='1200s'")
        d = pd.read_sql(SQL, conn, params={"n": args.sample, "y": args.year})
    finally:
        conn.close()

    print(f"  표본 필지 {args.sample:,} → 상권 안 + 접면도로 있음 {len(d):,}개 "
          f"· 상권 {d.trdar_cd.nunique():,}개 · 접면도로 거리 중앙값 {d.dist.median():.0f}m")

    for col, label in (("choice_raw", "Choice (지나다니는 길)"),
                       ("integration_raw", "Integration (닿기 쉬운 곳)"),
                       ("structure_raw", "종합 목 점수")):
        report(d, col, label, args.min_n)

    print("\n── 상권 유형별 · 상권 «안» corr(Choice, log 공시지가) ──")
    for se, gg in d.groupby("trdar_se_nm"):
        gg = gg.copy()
        cnt = gg.groupby("trdar_cd").pnu.transform("size")
        gg = gg[cnt >= args.min_n]
        if len(gg) < 200:
            continue
        b = gg.groupby("trdar_cd")
        lp = np.log(gg.price_per_sqm)
        w = (gg.choice_raw - b.choice_raw.transform("mean")).corr(lp - lp.groupby(gg.trdar_cd).transform("mean"))
        print(f"  {se:<10} 필지 {len(gg):>6,}   {w:+.3f}")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
