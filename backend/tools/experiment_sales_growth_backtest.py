"""상권 축 「미래 맞히기」 — 목이 매출이 늘어날 상권을 미리 맞히는가.

docs/DECISIONS_LAND_MODEL.md §15.

14절이 「현재 맞히기」(목 ↔ 지금 매출, +0.24)를 확인했다. 이건 그 다음 질문이다:
**목이 앞으로 «늘어날» 상권을 맞히는가.**

지가 축(12·13절)에서 목은 실패했다. 그러나 그 축은 공시지가라 순환성이 정면으로
걸려 있었다. **매출은 행정이 매기지 않으므로 이 축에는 순환성이 없다.** 여기서도
실패하면 "길 구조가 미래를 읽는다"는 주장은 근거를 잃는다.

⚠️ **실행 전에 고정한 것** (§10 — 돌려보고 고르면 조작이다):
  · 창       2022→2025 (주) · 2021→2024 (대조, 코로나 회복 구간 포함)
  · 정답     매출 증가율 상위 10%
  · K        정답 개수와 같게
  · 대조군   지금 매출 상위(경쟁사 방식) · 점포수 상위 · 유동인구 상위 · 무작위
  · 두 가지 Y를 함께 본다 — 원 증가율(동네층)과 구 평균을 뺀 초과증가율(필지층)

⚠️ **2021 시작 창은 코로나 바닥에서 출발한다.** 회복을 성장으로 오독하므로 주 창은
2022→2025로 두고, 2021→2024는 그 편향이 실제로 나타나는지 보는 대조용이다.

⚠️ **운영 DB에 쓰지 않는다.** SELECT만 한다.

Usage:
    python tools/experiment_sales_growth_backtest.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

YEARS = {y: [f"{y}{q}" for q in (1, 2, 3, 4)] for y in (2021, 2022, 2024, 2025)}
WINDOWS = [(2022, 2025), (2021, 2024)]
TOP_FRAC = 0.10
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
    quarters = sorted({q for qs in YEARS.values() for q in qs})
    sql = f"""
        WITH seg AS (
            SELECT a.trdar_cd, count(*) AS n_seg, {agg}
            FROM   trdar_area a
            JOIN   road_segment_structure r
                   ON ST_Intersects(ST_Transform(a.geom, 5179), r.geom_5179)
            GROUP BY a.trdar_cd
        ),
        sal AS (
            SELECT region_code AS trdar_cd, left(stdr_yyqu_cd, 4)::int AS yr,
                   sum(selng_amt) AS amt
            FROM   golmok_sales
            WHERE  region_type = 'trdar' AND stdr_yyqu_cd = ANY(%(q)s)
            GROUP BY 1, 2
        ),
        sto AS (
            SELECT region_code AS trdar_cd, left(stdr_yyqu_cd, 4)::int AS yr,
                   sum(stor_co) AS stores
            FROM   golmok_stores
            WHERE  region_type = 'trdar' AND stdr_yyqu_cd = ANY(%(q)s)
            GROUP BY 1, 2
        ),
        pop AS (
            SELECT region_code AS trdar_cd, left(stdr_yyqu_cd, 4)::int AS yr,
                   avg(tot_flpop) AS flpop
            FROM   golmok_flpop
            WHERE  region_type = 'trdar' AND stdr_yyqu_cd = ANY(%(q)s)
            GROUP BY 1, 2
        )
        SELECT a.trdar_cd, a.trdar_nm, a.trdar_se_nm, a.signgu_nm, a.area_m2,
               s.n_seg, {", ".join(f"s.{m}_mean, s.{m}_p90" for m in METRICS)},
               sal.yr, sal.amt, sto.stores, pop.flpop
        FROM   trdar_area a
        JOIN   seg s   ON s.trdar_cd = a.trdar_cd
        JOIN   sal     ON sal.trdar_cd = a.trdar_cd
        LEFT   JOIN sto ON sto.trdar_cd = a.trdar_cd AND sto.yr = sal.yr
        LEFT   JOIN pop ON pop.trdar_cd = a.trdar_cd AND pop.yr = sal.yr
        WHERE  a.area_m2 > 0
    """
    return pd.read_sql(sql, conn, params={"q": quarters})


def build(df: pd.DataFrame, t0: int, t1: int) -> pd.DataFrame:
    base = df[df.yr == t0].copy()
    end = df[df.yr == t1][["trdar_cd", "amt"]].rename(columns={"amt": "amt_end"})
    d = base.merge(end, on="trdar_cd")
    d = d[(d.amt > 0) & (d.amt_end > 0)].copy()
    d["growth"] = d.amt_end / d.amt - 1.0
    # 구 평균을 뺀 초과증가율 — 필지층에 대응하는 정의
    d["excess"] = d.growth - d.groupby("signgu_nm").growth.transform("mean")

    # 상권 갭 — 12절 「따라잡기」의 상권 축 대응.
    # 계층은 자치구(지가 축의 동×용도지역에 대응). 목 백분위 − 매출 백분위.
    # "길은 좋은데 아직 매출이 안 나오는 상권"이 이후 더 자라는가.
    g = d.groupby("signgu_nm")
    d["gap_pct"] = (g.choice_raw_p90.rank(pct=True) - g.amt.rank(pct=True)) * 100
    # 상권 유형까지 묶은 변형 — 전통시장과 발달상권을 나란히 세우지 않는다
    g2 = d.groupby(["signgu_nm", "trdar_se_nm"])
    d["gap_pct_se"] = (g2.choice_raw_p90.rank(pct=True) - g2.amt.rank(pct=True)) * 100
    # §23: 매출을 설명하는 것은 Choice 가 아니라 Integration 이었다(+0.343 대 +0.169).
    # §15 는 더 약한 쪽으로 미래를 맞히려 했던 셈이라, 같은 정의를 integration 으로도 만든다.
    d["gap_int"] = (g.integration_raw_p90.rank(pct=True) - g.amt.rank(pct=True)) * 100
    d["gap_int_se"] = (g2.integration_raw_p90.rank(pct=True) - g2.amt.rank(pct=True)) * 100

    # ⚠️ 기저효과 대조군 — 갭에는 「지금 매출이 낮다」가 절반 들어 있다.
    # 낮은 데서 시작하면 증가율이 높게 나오는 것은 산수이지 예측이 아니다.
    # 목을 빼고 «매출 백분위만 뒤집은» 점수가 갭과 비슷하면, 갭의 공은 목이 아니다.
    d["amt_low"] = -g.amt.rank(pct=True) * 100
    d["amt_low_se"] = -g2.amt.rank(pct=True) * 100
    return d.reset_index(drop=True)


def prec_at_k(scores: np.ndarray, hot: np.ndarray, k: int) -> int:
    return int(hot[np.argsort(-scores, kind="stable")[:k]].sum())


def evaluate(d: pd.DataFrame, ycol: str, ylabel: str, k: int, seed: int = 0) -> dict:
    thr = d[ycol].quantile(1 - TOP_FRAC)
    hot = (d[ycol] >= thr).values.astype(int)
    rng = np.random.default_rng(seed)
    n = len(d)
    r = {"정답": ylabel, "n": n, "정답수": int(hot.sum()), "K": k,
         "문턱(%)": round(float(thr) * 100, 1),
         "corr(choice,Y)": round(float(d.choice_raw_p90.corr(d[ycol])), 3),
         "corr(integration,Y)": round(float(d.integration_raw_p90.corr(d[ycol])), 3)}
    for name, col, asc in (("목 상위 (choice p90)", "choice_raw_p90", False),
                           ("목 상위 (integration p90)", "integration_raw_p90", False),
                           ("목 상위 (integration 평균)", "integration_raw_mean", False),
                           ("목 상위 (structure 평균)", "structure_raw_mean", False),
                           ("갭 상위 (구 안 목−매출)", "gap_pct", False),
                           ("갭 상위 (구×유형 안)", "gap_pct_se", False),
                           ("갭 상위 (integration 구 안)", "gap_int", False),
                           ("갭 상위 (integration 구×유형)", "gap_int_se", False),
                           ("★기저효과 대조: 매출 하위", "amt_low", False),
                           ("★기저효과 대조: 구×유형 매출 하위", "amt_low_se", False),
                           ("지금 매출 상위 (경쟁사)", "amt", False),
                           ("점포수 상위", "stores", False),
                           ("유동인구 상위", "flpop", False)):
        v = d[col].fillna(-np.inf).values.astype(float)
        r[name] = prec_at_k(v, hot, k)
    r["무작위"] = int(np.mean([hot[rng.permutation(n)[:k]].sum() for _ in range(300)]))
    return r


def quint(d: pd.DataFrame, col: str, ycol: str) -> str:
    q = pd.qcut(d[col], 5, labels=["1", "2", "3", "4", "5"], duplicates="drop")
    v = (d.groupby(q, observed=True)[ycol].median() * 100).round(1)
    return " · ".join(f"{i} {x:+.1f}%" for i, x in v.items())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="backend/tools/out/sales_growth_backtest.csv")
    args = ap.parse_args()

    print(f"\n상권 축 미래 맞히기 — 매출 증가율 상위 {TOP_FRAC:.0%} 맞히기")
    print("=" * 78)
    conn = psycopg2.connect(db_url())
    try:
        df = load(conn)
    finally:
        conn.close()

    rows = []
    for t0, t1 in WINDOWS:
        d = build(df, t0, t1)
        k = max(1, int(round(len(d) * TOP_FRAC)))
        tag = "주 창" if (t0, t1) == (2022, 2025) else "대조 창(코로나 회복 포함)"
        print(f"\n[{t0}→{t1}]  {tag} · 상권 {len(d):,}개 · K={k}")
        for ycol, ylabel in (("growth", "원 증가율"), ("excess", "구 평균 뺀 초과증가율")):
            r = evaluate(d, ycol, ylabel, k)
            r["창"] = f"{t0}→{t1}"
            rows.append(r)
            print(f"\n  ── {ylabel} (문턱 {r['문턱(%)']}%) ──")
            for key in ("목 상위 (choice p90)", "목 상위 (integration p90)",
                        "목 상위 (integration 평균)", "목 상위 (structure 평균)",
                        "갭 상위 (구 안 목−매출)", "갭 상위 (구×유형 안)",
                        "갭 상위 (integration 구 안)", "갭 상위 (integration 구×유형)",
                        "★기저효과 대조: 매출 하위", "★기저효과 대조: 구×유형 매출 하위",
                        "지금 매출 상위 (경쟁사)", "점포수 상위", "유동인구 상위", "무작위"):
                bar = "█" * int(r[key] * 40 / max(k, 1))
                print(f"    {key:<24} {r[key]:>3} / {k}  {bar}")
            print(f"    choice 5분위별 {ylabel} 중앙값     : {quint(d, 'choice_raw_p90', ycol)}")
            print(f"    integration 5분위별 {ylabel} 중앙값: {quint(d, 'integration_raw_p90', ycol)}")
            print(f"    갭 5분위별 {ylabel} 중앙값: {quint(d, 'gap_pct', ycol)}")

    res = pd.DataFrame(rows)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}\n")
    print(res[["창", "정답", "K", "목 상위 (choice p90)", "목 상위 (integration p90)",
               "갭 상위 (구 안 목−매출)", "갭 상위 (integration 구 안)",
               "★기저효과 대조: 매출 하위", "지금 매출 상위 (경쟁사)", "무작위"]].to_string(index=False))


if __name__ == "__main__":
    main()
