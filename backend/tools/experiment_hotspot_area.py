"""「동네 층」 백테스트 — 목이 「어느 동네가 오르나」를 맞히는가.

docs/DECISIONS_LAND_MODEL.md §13.

`experiment_hotspot_backtest.py`(필지 층)와 짝이다. 필지 층에서 목이 실패한 이유가
**초과상승률을 만들 때 동네 평균을 빼면서 목이 맞히던 것을 지웠기 때문**이라는 진단을
검증한다. 여기서는 동네 평균을 빼지 않고, 동네 자체를 예측 단위로 둔다.

⚠️ **동네를 법정동으로 두면 강남+금천에 17개뿐이라 표본이 안 된다.**
그래서 **500m 격자**로 정의한다. 격자 크기(500m) · 최소 필지 수(20) · 정답 비율(10%)은
**실행 전에 고정한 값**이고, 결과를 보고 바꾸지 않는다(§10).

⚠️ **운영 DB에 쓰지 않는다.** SELECT만 한다.

⚠️ 이 층의 Y는 **원 상승률**이다(동네 평균을 빼지 않는다). 그래서 §4의 순환성이
그대로 살아 있고, 2020년 공시지가 현실화 로드맵의 영향도 걸린다 — 창별로 나눠 보는
이유가 그것이다.

Usage:
    python tools/experiment_hotspot_area.py
    python tools/experiment_hotspot_area.py --grid 500 --min-parcels 20
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WINDOWS = [(2018, 2021), (2020, 2023), (2023, 2026)]
TOP_FRAC = 0.10          # 정답 비율 — 실행 전 고정


def db_url() -> str:
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("backend/.env 에 DATABASE_URL 이 없다 — bash scripts/setup-laptop.sh")


def load(conn, grid_m: int) -> pd.DataFrame:
    """필지 패널 + 격자 셀 id + 점포밀도."""
    years = sorted({y for w in WINDOWS for y in w})
    sql = """
        SELECT p.pnu, p.base_year, p.official_price_per_sqm AS price,
               p.structure_score, p.ldong_code,
               ST_X(ST_SnapToGrid(c.geom_5179, %(g)s)) AS gx,
               ST_Y(ST_SnapToGrid(c.geom_5179, %(g)s)) AS gy
        FROM   parcel_panel p
        JOIN   parcel_centroids c ON c.pnu = p.pnu
        WHERE  p.base_year = ANY(%(y)s)
    """
    df = pd.read_sql(sql, conn, params={"g": grid_m, "y": years})
    df["cell"] = df.gx.astype("int64").astype(str) + "_" + df.gy.astype("int64").astype(str)
    return df


def store_density(conn, grid_m: int) -> pd.DataFrame:
    """대조군 — 격자별 점포 수. ⚠️ sbiz_stores 는 2026 스냅샷뿐이라 과거 창에 누수가 있다.
    대조군에 유리하게 두는 쪽이 보수적이라 그대로 둔다."""
    sql = """
        SELECT ST_X(ST_SnapToGrid(geom_5179, %(g)s))::bigint AS gx,
               ST_Y(ST_SnapToGrid(geom_5179, %(g)s))::bigint AS gy,
               count(*) AS stores
        FROM   sbiz_stores
        GROUP BY 1, 2
    """
    d = pd.read_sql(sql, conn, params={"g": grid_m})
    d["cell"] = d.gx.astype(str) + "_" + d.gy.astype(str)
    return d[["cell", "stores"]]


def build_cells(df: pd.DataFrame, t0: int, t1: int, min_parcels: int) -> pd.DataFrame:
    a = df[df.base_year == t0][["pnu", "price", "structure_score", "cell"]]
    b = df[df.base_year == t1][["pnu", "price"]].rename(columns={"price": "p1"})
    m = a.merge(b, on="pnu")
    m = m[(m.price > 0) & (m.p1 > 0)].copy()
    m["growth"] = m.p1 / m.price - 1.0

    cells = m.groupby("cell").agg(
        n=("pnu", "size"),
        mok=("structure_score", "mean"),
        price=("price", "median"),
        growth=("growth", "median"),
    )
    return cells[(cells.n >= min_parcels) & cells.mok.notna()].reset_index()


def score(cells: pd.DataFrame, by: str, k: int, hot: np.ndarray, asc=False) -> int:
    order = cells[by].rank(ascending=asc, method="first").nsmallest(k).index
    return int(hot[order].sum())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", type=int, default=500, help="격자 한 변(m) — 실행 전 고정")
    ap.add_argument("--min-parcels", type=int, default=20, help="셀 최소 필지 수")
    ap.add_argument("--out", default="backend/tools/out/hotspot_area.csv")
    args = ap.parse_args()

    print(f"\n동네 층 백테스트 — {args.grid}m 격자 · 셀당 최소 {args.min_parcels}필지 · "
          f"정답 상위 {TOP_FRAC:.0%}")
    print("=" * 74)

    conn = psycopg2.connect(db_url())
    try:
        df = load(conn, args.grid)
        dens = store_density(conn, args.grid)
    finally:
        conn.close()

    rows = []
    for t0, t1 in WINDOWS:
        cells = build_cells(df, t0, t1, args.min_parcels).merge(dens, on="cell", how="left")
        cells["stores"] = cells.stores.fillna(0)
        n = len(cells)
        k = max(1, int(round(n * TOP_FRAC)))
        thr = cells.growth.quantile(1 - TOP_FRAC)
        hot = (cells.growth >= thr).values.astype(int)

        rng = np.random.default_rng(0)
        r = {
            "창": f"{t0}→{t1}",
            "셀수": n, "정답셀": int(hot.sum()), "K": k,
            "문턱상승률(%)": round(float(thr) * 100, 1),
            "corr(목,상승률)": round(float(cells.mok.corr(cells.growth)), 3),
            "목 상위": score(cells, "mok", k, hot),
            "점포밀도 상위": score(cells, "stores", k, hot),
            "가격 상위": score(cells, "price", k, hot),
            "무작위": int(np.mean([hot[rng.permutation(n)[:k]].sum() for _ in range(200)])),
        }
        rows.append(r)

        q = pd.qcut(cells.mok, 4, labels=["1(낮음)", "2", "3", "4(높음)"], duplicates="drop")
        qm = (cells.groupby(q, observed=True).growth.median() * 100).round(1)

        print(f"\n[{t0}→{t1}]  셀 {n} · 정답 {int(hot.sum())} · K={k} · "
              f"정답 문턱 상승률 {r['문턱상승률(%)']}%")
        print(f"    corr(목, 상승률) = {r['corr(목,상승률)']:+.3f}")
        for key in ("목 상위", "점포밀도 상위", "가격 상위", "무작위"):
            print(f"    {key:<14} {r[key]:>3} / {k}  {'█' * int(r[key] * 20 / max(k,1))}")
        print(f"    목 4분위별 셀 상승률(중앙값, %): "
              + " · ".join(f"{i} {v}" for i, v in qm.items()))

    res = pd.DataFrame(rows)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}\n")
    print(res.to_string(index=False))


if __name__ == "__main__":
    main()
