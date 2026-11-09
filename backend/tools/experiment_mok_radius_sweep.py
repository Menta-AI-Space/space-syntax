"""§14를 반경별로 다시 돌린다 — 어느 스케일이 상권 매출을 설명하는가.

`DECISIONS_LAND_MODEL.md` §22가 남긴 질문의 정면 시험이다.

기존 `road_segment_structure`의 목 값은 **1.8km 창(코어 800m + 헤일로 500m) 안의
radius-n**이다. §14는 그 지표로 목↔매출 면적통제 편상관 **+0.235**를 얻었다.
`compute_global_segment_structure.py`가 서울 전역을 한 그래프로 놓고 여러 metric
반경으로 다시 계산했으므로, 같은 분석을 반경별로 돌려 **기존 값과 나란히** 놓는다.

    새 지표가 기존보다 뚜렷이 크다  → 측정이 문제였다. §12~18 재실행 필요
    비슷하거나 낮다                → 1.8km 창은 문제가 아니었다. 기각 확정

⚠️ **바뀐 것은 목 지표 하나뿐이다.** 상권·분기·면적 통제·집계 방식(mean/p90)은 §14와
같게 두었다. 안 그러면 무엇 때문에 달라졌는지 못 가린다.

⚠️ **운영 DB에 쓰지 않는다.** SELECT만 한다.

Run:
  cd backend && .venv/Scripts/python -m tools.experiment_mok_radius_sweep \
      --csv C:/dev/tools/scratch/seoul_global.csv
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUARTERS = ("20251", "20252", "20253", "20254")  # §14와 동일 — 실행 전 고정


def db_url() -> str:
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("backend/.env 에 DATABASE_URL 이 없다")


def load_seg_to_trdar(conn) -> pd.DataFrame:
    """세그먼트 → 상권 대응과 «기존» 목 값을 함께 가져온다.

    공간조인은 road_segment_structure 의 geom_5179 로 한다. seg_key 공식이
    compute_global_segment_structure.py 와 같으므로 CSV 와 그대로 붙는다.
    """
    sql = """
        SELECT r.seg_key, a.trdar_cd,
               r.choice_raw      AS old_choice,
               r.integration_raw AS old_integration,
               r.structure_raw   AS old_structure
        FROM   trdar_area a
        JOIN   road_segment_structure r
               ON ST_Intersects(ST_Transform(a.geom, 5179), r.geom_5179)
    """
    return pd.read_sql(sql, conn)


def load_trdar(conn) -> pd.DataFrame:
    sql = """
        WITH pop AS (
            SELECT region_code AS trdar_cd, avg(tot_flpop) AS flpop
            FROM   golmok_flpop
            WHERE  region_type = 'trdar' AND stdr_yyqu_cd = ANY(%(q)s)
            GROUP BY region_code
        ),
        sal AS (
            SELECT region_code AS trdar_cd, sum(selng_amt) / %(nq)s AS sales
            FROM   golmok_sales
            WHERE  region_type = 'trdar' AND stdr_yyqu_cd = ANY(%(q)s)
            GROUP BY region_code
        )
        SELECT a.trdar_cd, a.trdar_nm, a.trdar_se_nm, a.area_m2, p.flpop, sal.sales
        FROM   trdar_area a
        LEFT   JOIN pop p   ON p.trdar_cd = a.trdar_cd
        LEFT   JOIN sal     ON sal.trdar_cd = a.trdar_cd
        WHERE  a.area_m2 > 0
    """
    return pd.read_sql(sql, conn, params={"q": list(QUARTERS), "nq": len(QUARTERS)})


def partial_corr(x, y, z) -> float:
    """면적(z)을 통제한 x↔y 편상관 — §14와 같은 구현."""
    A = np.column_stack([np.ones(len(z)), np.asarray(z, dtype=float)])

    def resid(v):
        v = np.asarray(v, dtype=float)
        beta, *_ = np.linalg.lstsq(A, v, rcond=None)
        return v - A @ beta

    return float(np.corrcoef(resid(x), resid(y))[0, 1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="compute_global_segment_structure.py 산출물")
    parser.add_argument("--min-seg", type=int, default=1, help="상권당 최소 세그먼트 수")
    args = parser.parse_args()

    new = pd.read_csv(args.csv)
    metric_cols = [c for c in new.columns if c.startswith(("nach_r", "nain_r"))]
    print(f"새 지표 CSV — {len(new):,}행 · 지표 {metric_cols}")

    with psycopg2.connect(db_url()) as conn:
        link = load_seg_to_trdar(conn)
        trdar = load_trdar(conn)
    print(f"세그먼트-상권 대응 {len(link):,}쌍 · 상권 {trdar.trdar_cd.nunique():,}개")

    merged = link.merge(new[["seg_key", *metric_cols]], on="seg_key", how="inner")
    # 분모는 «CSV 세그먼트»다. link 는 서울 전역 상권을 덮으므로 그걸로 나누면
    # 부분집합(자치구 몇 개)을 돌릴 때 항상 낮게 나와 헛경보가 뜬다.
    matched = merged.seg_key.nunique()
    covered = matched / max(len(new), 1)
    print(f"CSV 세그먼트 중 상권 안에 든 것 {matched:,} / {len(new):,} ({covered:.1%})")
    if covered < 0.2:
        print("⚠️ 너무 낮다 — seg_key 공식이 어긋났을 수 있다")

    all_cols = ["old_choice", "old_integration", "old_structure", *metric_cols]
    agg = merged.groupby("trdar_cd").agg(
        n_seg=("seg_key", "size"),
        **{f"{c}_mean": (c, "mean") for c in all_cols},
        **{f"{c}_p90": (c, lambda s: s.quantile(0.9)) for c in all_cols},
    ).reset_index()

    d = trdar.merge(agg, on="trdar_cd", how="inner")
    d = d[d.n_seg >= args.min_seg]
    g = d[d.sales.notna() & (d.sales > 0)].copy()
    ly, la = np.log(g.sales), np.log(g.area_m2)
    print(f"\n═══ 목 ↔ 상권 매출 · 면적 통제 편상관 — 상권 {len(g):,}개 ═══")
    print(f"{'지표':>22} {'mean':>9} {'p90':>9}")
    print("-" * 44)
    rows = []
    for c in all_cols:
        pm = partial_corr(g[f"{c}_mean"], ly, la)
        pp = partial_corr(g[f"{c}_p90"], ly, la)
        mark = "  ← 기존(1.8km 창)" if c.startswith("old_") else ""
        print(f"{c:>22} {pm:>+9.3f} {pp:>+9.3f}{mark}")
        rows.append((c, pm, pp))

    print("\n" + "=" * 44)
    old_best = max((abs(p) for c, pm, pp in rows if c.startswith("old_") for p in (pm, pp)), default=0.0)
    # ⚠️ nach(Choice)만 보면 안 된다. 기존 쪽 최고가 integration 인데 새 쪽을 choice 로만
    #    고르면 지표 종류가 다른 것끼리 견주게 되어 결론이 뒤집힌다.
    new_rows = [(c, pm, pp) for c, pm, pp in rows if c.startswith(("nach_r", "nain_r"))]
    if new_rows:
        best_c, best_v = max(
            ((c, p) for c, pm, pp in new_rows for p in (pm, pp)),
            key=lambda t: abs(t[1]),
        )
        print(f"기존 최고 |편상관|      : {old_best:.3f}")
        print(f"새 지표 최고           : {best_v:+.3f}  ({best_c})")
        delta = abs(best_v) - old_best
        print(f"차이                   : {delta:+.3f}")
        print()
        if delta > 0.05:
            print("→ 새 지표가 뚜렷이 낫다. 측정이 문제였다 — §12~18을 재실행해야 한다.")
        elif delta < -0.05:
            print("→ 새 지표가 오히려 나쁘다. 1.8km 창이 이 문제에는 오히려 맞았다.")
        else:
            print("→ 차이가 없다. 창 크기는 문제가 아니었다 — 기존 기각이 유지된다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
