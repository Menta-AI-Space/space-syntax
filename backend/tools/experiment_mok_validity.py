"""목 지표가 「지금 좋은 자리」를 맞히는가 — 유동인구·매출 동시 검증.

docs/DECISIONS_LAND_MODEL.md §14.

`experiment_mok_vs_footfall.py`의 확장. 지표 세 개(structure/choice/integration)와
결과 두 개(유동인구·매출)를 한 번에 교차한다. 공간조인이 비싸므로 세그먼트 집계는
**한 번만** 하고 재사용한다.

    목 점수 (road_segment_structure, 우리 계산)
      ↔ 유동인구 (golmok_flpop)  ·  매출 (golmok_sales)

⚠️ **이건 「현재 맞히기」다.** 같은 시점의 두 값을 본다. 여기서 나온다고 미래 예측이
되는 것은 아니지만, **여기서조차 안 나오면 서비스의 전제가 무너진다.**

⚠️ **면적을 통제하지 않으면 「큰 상권에 사람이 많다」를 재게 된다.** 면적 통제
편상관과 밀도(면적당) 상관을 함께 낸다.

⚠️ **목 지표의 한계를 기억할 것.** `road_segment_structure`는 `radius_m`(국소 반경)과
`tile_key`(타일 분할) 위에서 계산돼 있다. 타일 안의 국소 구조이지 서울 전역 위상구조가
아니다. 결과가 나쁘게 나오면 「공간구문론이 틀렸다」가 아니라 **「우리 구현이 국소적이다」**
일 수 있다 — 둘을 섞어 결론 내지 말 것.

⚠️ **운영 DB에 쓰지 않는다.** SELECT만 한다.

Usage:
    python tools/experiment_mok_validity.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUARTERS = ("20251", "20252", "20253", "20254")   # 실행 전 고정
METRICS = ("structure_raw", "choice_raw", "integration_raw")


def db_url() -> str:
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("backend/.env 에 DATABASE_URL 이 없다")


def load(conn) -> pd.DataFrame:
    agg = ",\n".join(
        f"avg(r.{m}) AS {m}_mean, "
        f"percentile_cont(0.9) WITHIN GROUP (ORDER BY r.{m}) AS {m}_p90"
        for m in METRICS
    )
    sql = f"""
        WITH seg AS (
            SELECT a.trdar_cd, count(*) AS n_seg,
                   {agg}
            FROM   trdar_area a
            JOIN   road_segment_structure r
                   ON ST_Intersects(ST_Transform(a.geom, 5179), r.geom_5179)
            GROUP BY a.trdar_cd
        ),
        pop AS (
            SELECT region_code AS trdar_cd, avg(tot_flpop) AS flpop
            FROM   golmok_flpop
            WHERE  region_type = 'trdar' AND stdr_yyqu_cd = ANY(%(q)s)
            GROUP BY region_code
        ),
        sal AS (
            SELECT region_code AS trdar_cd,
                   sum(selng_amt) / %(nq)s AS sales,      -- 분기 평균 총매출
                   sum(selng_co)  / %(nq)s AS sales_cnt
            FROM   golmok_sales
            WHERE  region_type = 'trdar' AND stdr_yyqu_cd = ANY(%(q)s)
            GROUP BY region_code
        )
        SELECT a.trdar_cd, a.trdar_nm, a.trdar_se_nm, a.signgu_nm, a.area_m2,
               s.n_seg, {", ".join(f"s.{m}_mean, s.{m}_p90" for m in METRICS)},
               p.flpop, sal.sales, sal.sales_cnt
        FROM   trdar_area a
        JOIN   seg s   ON s.trdar_cd = a.trdar_cd
        LEFT   JOIN pop p   ON p.trdar_cd = a.trdar_cd
        LEFT   JOIN sal ON sal.trdar_cd = a.trdar_cd
        WHERE  a.area_m2 > 0
    """
    return pd.read_sql(sql, conn, params={"q": list(QUARTERS), "nq": len(QUARTERS)})


def partial_corr(x, y, z) -> float:
    A = np.column_stack([np.ones(len(z)), np.asarray(z)])
    def resid(v):
        beta, *_ = np.linalg.lstsq(A, np.asarray(v), rcond=None)
        return np.asarray(v) - A @ beta
    return float(np.corrcoef(resid(x), resid(y))[0, 1])


def cross(d: pd.DataFrame, ycol: str, ylabel: str) -> pd.DataFrame:
    g = d[d[ycol].notna() & (d[ycol] > 0)].copy()
    ly, la = np.log(g[ycol]), np.log(g.area_m2)
    ldens = np.log(g[ycol] / g.area_m2 * 10000)
    rows = []
    for m in METRICS:
        for how in ("mean", "p90"):
            col = f"{m}_{how}"
            rows.append({
                "지표": f"{m.replace('_raw','')} · {how}",
                "원상관": round(float(g[col].corr(ly)), 3),
                "면적통제": round(partial_corr(g[col], ly, la), 3),
                "밀도상관": round(float(g[col].corr(ldens)), 3),
            })
    t = pd.DataFrame(rows)
    print(f"\n══ {ylabel} — 상권 {len(g):,}개 ══")
    print(t.to_string(index=False))
    best = t.loc[t.면적통제.abs().idxmax()]
    print(f"   → 면적 통제 기준 가장 강한 것: {best.지표}  {best.면적통제:+.3f}")
    return t


def quintiles(d: pd.DataFrame, col: str, ycol: str, ylabel: str) -> None:
    g = d[d[ycol].notna() & (d[ycol] > 0)].copy()
    q = pd.qcut(g[col], 5, labels=["1(낮음)", "2", "3", "4", "5(높음)"], duplicates="drop")
    g["dens"] = g[ycol] / g.area_m2 * 10000
    t = g.groupby(q, observed=True).agg(n=("trdar_cd", "size"),
                                        med=(ycol, "median"), dens=("dens", "median"))
    print(f"\n  {col} 5분위별 {ylabel} (중앙값):")
    for i, r in t.iterrows():
        print(f"    {i:<8} n={int(r.n):>4}   {r.med:>15,.0f}   밀도 {r.dens:>10,.0f}/만㎡")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="backend/tools/out/mok_validity.csv")
    args = ap.parse_args()

    print(f"\n목 지표 타당성 — 상권 단위 · {QUARTERS[0]}~{QUARTERS[-1]} 평균")
    print("=" * 76)
    conn = psycopg2.connect(db_url())
    try:
        d = load(conn)
    finally:
        conn.close()
    print(f"  상권 {len(d):,}개 · 세그먼트 중앙값 {d.n_seg.median():.0f}개/상권 · "
          f"유동인구 {d.flpop.notna().sum():,} · 매출 {d.sales.notna().sum():,}")

    t1 = cross(d, "flpop", "유동인구")
    t2 = cross(d, "sales", "매출액")

    quintiles(d, "choice_raw_p90", "flpop", "유동인구")
    quintiles(d, "choice_raw_p90", "sales", "매출액")

    print("\n══ 상권 유형별 · choice p90 ══")
    for se, g in d.groupby("trdar_se_nm"):
        g = g[g.sales.notna() & (g.sales > 0) & g.flpop.notna() & (g.flpop > 0)]
        if len(g) >= 30:
            print(f"  {se:<10} n={len(g):>4}   유동인구 {g.choice_raw_p90.corr(np.log(g.flpop)):+.3f}"
                  f"   매출 {g.choice_raw_p90.corr(np.log(g.sales)):+.3f}")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
