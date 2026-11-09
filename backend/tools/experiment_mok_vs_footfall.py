"""목 점수가 실제 유동인구를 맞히는가 — 「현재 맞히기」 1차 검증.

docs/DECISIONS_LAND_MODEL.md §14.

지금까지 이 저장소는 "공간구문론은 유동인구를 잘 맞힌다"를 **학계 정설로만** 인용해
왔다. 우리 데이터로 잰 적이 없다. 이 스크립트가 그것을 처음 잰다.

    목 점수 (road_segment_structure, 우리 계산)
      ↔ 실제 유동인구 (golmok_flpop, 서울시 상권분석서비스)

⚠️ **이건 「미래 맞히기」가 아니라 「현재 맞히기」다.** 같은 시점의 두 값을 비교한다.
여기서 상관이 나온다고 미래 예측이 되는 것은 아니다 — 12·13절이 보여준 그대로다.
그러나 **여기서조차 안 나오면 이 서비스의 전제 자체가 무너진다.**

⚠️ **통제하지 않으면 「큰 상권이 사람 많다」를 재게 된다.** 면적을 반드시 통제하고,
밀도(유동인구/면적)로도 함께 본다. 상권 유형(발달상권·골목상권·전통시장·관광특구)도
나눠 본다 — 유형마다 사람이 모이는 이유가 다르다.

⚠️ **운영 DB에 쓰지 않는다.** SELECT만 한다.

Usage:
    python tools/experiment_mok_vs_footfall.py
    python tools/experiment_mok_vs_footfall.py --metric choice_raw
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUARTERS = ("20251", "20252", "20253", "20254")   # 최근 4분기 평균 — 실행 전 고정


def db_url() -> str:
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("backend/.env 에 DATABASE_URL 이 없다")


def load(conn, metric: str) -> pd.DataFrame:
    """상권 × (목 점수 집계 + 유동인구)."""
    sql = f"""
        WITH seg AS (
            SELECT a.trdar_cd,
                   avg(r.{metric})              AS mok_mean,
                   max(r.{metric})              AS mok_max,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY r.{metric}) AS mok_p90,
                   count(*)                     AS n_seg
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
        )
        SELECT a.trdar_cd, a.trdar_nm, a.trdar_se_nm, a.signgu_nm,
               a.area_m2, s.mok_mean, s.mok_max, s.mok_p90, s.n_seg, p.flpop
        FROM   trdar_area a
        JOIN   seg s ON s.trdar_cd = a.trdar_cd
        JOIN   pop p ON p.trdar_cd = a.trdar_cd
        WHERE  p.flpop > 0 AND a.area_m2 > 0
    """
    return pd.read_sql(sql, conn, params={"q": list(QUARTERS)})


def partial_corr(x: pd.Series, y: pd.Series, z: pd.Series) -> float:
    """z를 통제한 x·y 편상관 — 각각 z에 회귀시킨 잔차의 상관."""
    def resid(v):
        A = np.column_stack([np.ones(len(z)), z.values])
        beta, *_ = np.linalg.lstsq(A, v.values, rcond=None)
        return v.values - A @ beta
    rx, ry = resid(x), resid(y)
    return float(np.corrcoef(rx, ry)[0, 1])


def report(d: pd.DataFrame, col: str, label: str) -> None:
    lf = np.log(d.flpop)
    la = np.log(d.area_m2)
    ld = np.log(d.flpop / d.area_m2 * 10000)      # 유동인구 밀도 (만㎡당)

    print(f"\n── {label} ──")
    print(f"  corr(목, 유동인구)            = {d[col].corr(lf):+.3f}")
    print(f"  corr(목, 유동인구) 면적 통제  = {partial_corr(d[col], lf, la):+.3f}")
    print(f"  corr(목, 유동인구 «밀도»)     = {d[col].corr(ld):+.3f}")

    q = pd.qcut(d[col], 5, labels=["1(낮음)", "2", "3", "4", "5(높음)"], duplicates="drop")
    t = d.groupby(q, observed=True).agg(
        상권수=("trdar_cd", "size"),
        유동인구=("flpop", "median"),
        밀도=("flpop", lambda s: 0),
    )
    t["밀도"] = d.assign(dens=d.flpop / d.area_m2 * 10000).groupby(q, observed=True).dens.median()
    print("  목 5분위별 (중앙값):")
    for i, r in t.iterrows():
        print(f"    {i:<8} 상권 {int(r.상권수):>4}개   유동인구 {r.유동인구:>12,.0f}   밀도 {r.밀도:>8,.0f}/만㎡")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default="structure_raw",
                    choices=["structure_raw", "choice_raw", "integration_raw"])
    ap.add_argument("--out", default="backend/tools/out/mok_vs_footfall.csv")
    args = ap.parse_args()

    print(f"\n목 ↔ 유동인구 — 상권 단위 · 지표 {args.metric} · {QUARTERS[0]}~{QUARTERS[-1]} 평균")
    print("=" * 74)

    conn = psycopg2.connect(db_url())
    try:
        d = load(conn, args.metric)
    finally:
        conn.close()

    print(f"  상권 {len(d):,}개 매칭 (도로 세그먼트 중앙값 {d.n_seg.median():.0f}개/상권)")

    report(d, "mok_mean", "상권 내 세그먼트 «평균» 목")
    report(d, "mok_p90", "상권 내 세그먼트 «상위 10%» 목 (제일 좋은 길 기준)")

    print("\n── 상권 유형별 corr(평균 목, log 유동인구) ──")
    for se, g in d.groupby("trdar_se_nm"):
        if len(g) >= 30:
            print(f"  {se:<10} n={len(g):>4}   {g.mok_mean.corr(np.log(g.flpop)):+.3f}")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
