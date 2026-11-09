"""§10 경로 ③ — 목 × 건축인허가 상호작용이 초과상승을 예측하는가.

docs/DECISIONS_LAND_MODEL.md §17.

12·13·15·16절에서 기각된 것은 **「시불변 목 단독」과 「목 + 값 백분위 조합(갭)」**이다.
10절이 세운 세 경로 중 ③(목 × 시변 변수 상호작용)은 재료가 없어 손도 대지 못했다.
2026-09-04에 건축인허가 서울 55만 건을 적재해 처음으로 시험한다.

가설: 길 구조는 안 변하므로 단독으로는 변화를 설명하지 못한다. 그러나
**「목이 좋은데 지금 그 옆에 짓고 있다」**는 조합은 다르다 — 허가는 준공 2~3년 «전»에
나므로 선행이고, 목은 그 개발이 실제로 사람을 끌어올 자리인지를 가른다.

⚠️ **실행 전에 고정한 것** (§10):
  · 창    2018→2021 · 2020→2023  (2023→2026은 §12에서 정답 문턱 1.76%p로 퇴화 판정)
  · 정답  초과상승률 상위 10%
  · K     100
  · 분할  법정동 블록 5-fold 공간 홀드아웃

⚠️ **§16의 교훈을 강제한다 — 성분별 대조군을 처음부터 넣는다.**
상호작용이 성능을 내면 «목만», «인허가만», «가격 하위(기저효과)»도 함께 재서,
어느 성분이 공을 세웠는지 가른다. 성분 하나가 전체와 비슷하면 그 지표의 공은 우리 것이 아니다.

⚠️ **시점 절단** — base_year 12월 31일까지 «허가된» 건만 센다. 착공일·사용승인일은
쓰지 않는다(준공은 미래다).

⚠️ **운영 DB에 쓰지 않는다.** SELECT만 한다.

Usage:
    python tools/experiment_permit_interaction.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WINDOWS = [(2018, 2021), (2020, 2023)]
TOP_FRAC = 0.10
K = 100


def db_url() -> str:
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("backend/.env 에 DATABASE_URL 이 없다")


def load_permit_features(conn, base_year: int) -> pd.DataFrame:
    """base_year 시점에 이미 «허가된» 건만 세어 필지별 반경 집계."""
    sql = """
        WITH cut AS (
            SELECT geom_5179, permit_date, tot_area
            FROM   building_permits
            WHERE  geom_5179 IS NOT NULL
              AND  permit_date IS NOT NULL
              AND  permit_date <= %(cut)s
              AND  permit_date >  %(cut3)s          -- 최근 3년: 허가→준공 주기
        ),
        p AS (
            SELECT DISTINCT pp.pnu, c.geom_5179
            FROM   parcel_panel pp
            JOIN   parcel_centroids c ON c.pnu = pp.pnu
            WHERE  pp.base_year = %(y)s
        )
        SELECT p.pnu,
               count(*) FILTER (WHERE ST_DWithin(p.geom_5179, k.geom_5179, 100)) AS permits_100m,
               count(*) FILTER (WHERE ST_DWithin(p.geom_5179, k.geom_5179, 200)) AS permits_200m,
               coalesce(sum(k.tot_area) FILTER
                        (WHERE ST_DWithin(p.geom_5179, k.geom_5179, 100)), 0)     AS area_100m,
               coalesce(sum(k.tot_area) FILTER
                        (WHERE ST_DWithin(p.geom_5179, k.geom_5179, 200)), 0)     AS area_200m,
               count(*) FILTER (WHERE k.tot_area >= 10000)                        AS big_500m,
               min(ST_Distance(p.geom_5179, k.geom_5179))                         AS nearest_m
        FROM   p
        LEFT   JOIN cut k ON ST_DWithin(p.geom_5179, k.geom_5179, 500)
        GROUP  BY p.pnu
    """
    return pd.read_sql(sql, conn, params={
        "y": base_year,
        "cut": f"{base_year}-12-31",
        "cut3": f"{base_year - 3}-12-31",
    })


def build(panel: pd.DataFrame, perm: pd.DataFrame, t0: int, t1: int) -> pd.DataFrame:
    a = panel[panel.base_year == t0].copy()
    b = panel[panel.base_year == t1][["pnu", "price"]].rename(columns={"price": "p1"})
    d = a.merge(b, on="pnu").merge(perm, on="pnu", how="left")
    d = d[(d.price > 0) & (d.p1 > 0)].dropna(subset=["structure_score"]).copy()
    for c in ("permits_100m", "permits_200m", "area_100m", "area_200m", "big_500m"):
        d[c] = d[c].fillna(0)
    d["nearest_m"] = d.nearest_m.fillna(9999)

    d["stratum"] = d.ldong_code.astype(str) + "|" + d.zone_group.fillna("NA")
    d["growth"] = d.p1 / d.price - 1.0
    d["excess"] = d.growth - d.groupby("stratum").growth.transform("mean")

    g = d.groupby("stratum")
    d["mok_pct"] = g.structure_score.rank(pct=True) * 100
    d["price_low"] = -g.price.rank(pct=True) * 100            # ★기저효과 대조 (§16)
    # 강도 = 반경 100m 안 허가 연면적 합 ÷ 필지 면적 (건수가 아니라 «얼마나 크게»)
    d["intensity_100m"] = d.area_100m / d.area_sqm.clip(lower=1)
    d["permit_pct"] = g.permits_100m.rank(pct=True) * 100
    # 상호작용 — 둘 다 높아야 큰 값이 되도록 곱한다
    d["mok_x_permit"] = d.mok_pct * d.permit_pct / 100
    return d.reset_index(drop=True)


def prec(score: np.ndarray, hot: np.ndarray, k: int) -> int:
    return int(hot[np.argsort(-score, kind="stable")[:k]].sum())


def oof(X, y, groups, seed=0) -> np.ndarray:
    import lightgbm as lgb
    from sklearn.model_selection import GroupKFold
    out = np.zeros(len(X))
    for tr, te in GroupKFold(n_splits=5).split(X, y, groups):
        m = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.05, num_leaves=31,
                               min_child_samples=40, subsample=0.9, colsample_bytree=0.8,
                               random_state=seed, verbose=-1)
        m.fit(X.iloc[tr], y[tr])
        out[te] = m.predict_proba(X.iloc[te])[:, 1]
    return out


FEATS_BASE = ["log_price", "area_sqm", "nearest_station_m", "nearest_road_m"]
FEATS_MOK = ["structure_score", "mok_pct"]
FEATS_PERMIT = ["permits_100m", "permits_200m", "area_100m", "area_200m",
                "big_500m", "nearest_m", "intensity_100m"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="backend/tools/out/permit_interaction.csv")
    args = ap.parse_args()

    print(f"\n목 × 인허가 상호작용 — 정답 상위 {TOP_FRAC:.0%} · Precision@{K} · 무작위 기대 10")
    print("=" * 78)

    conn = psycopg2.connect(db_url())
    try:
        panel = pd.read_sql(
            """SELECT pnu, base_year, official_price_per_sqm AS price, structure_score,
                      zone_group, ldong_code, area_sqm, nearest_station_m, nearest_road_m
               FROM parcel_panel WHERE base_year = ANY(%s)""",
            conn, params=(sorted({y for w in WINDOWS for y in w}),))
        perms = {t0: load_permit_features(conn, t0) for t0, _ in WINDOWS}
    finally:
        conn.close()

    rows = []
    for t0, t1 in WINDOWS:
        d = build(panel, perms[t0], t0, t1)
        d["log_price"] = np.log(d.price)
        thr = d.excess.quantile(1 - TOP_FRAC)
        hot = (d.excess >= thr).values.astype(int)
        groups = d.ldong_code.fillna("NA").values

        Xall = d[FEATS_BASE + FEATS_MOK + FEATS_PERMIT]
        r = {
            "창": f"{t0}→{t1}", "n": len(d),
            "허가 있는 필지(100m)": int((d.permits_100m > 0).sum()),
            "모델 전체": prec(oof(Xall, hot, groups), hot, K),
            "모델 − 목": prec(oof(d[FEATS_BASE + FEATS_PERMIT], hot, groups), hot, K),
            "모델 − 인허가": prec(oof(d[FEATS_BASE + FEATS_MOK], hot, groups), hot, K),
            "★상호작용 목×인허가": prec(d.mok_x_permit.values, hot, K),
            "★강도 상위 (연면적/필지면적)": prec(d.intensity_100m.values, hot, K),
            "★대형허가 500m": prec(d.big_500m.values.astype(float), hot, K),
            "★인허가 건수만 100m": prec(d.permits_100m.values.astype(float), hot, K),
            "★목만": prec(d.structure_score.values, hot, K),
            "★가격 하위 (기저효과)": prec(d.price_low.values, hot, K),
            "무작위": int(np.mean([hot[np.random.default_rng(s).permutation(len(d))[:K]].sum()
                                for s in range(200)])),
        }
        rows.append(r)
        print(f"\n[{t0}→{t1}]  필지 {r['n']:,} · 반경 100m 안 허가가 있는 필지 "
              f"{r['허가 있는 필지(100m)']:,} ({r['허가 있는 필지(100m)']/r['n']*100:.0f}%)")
        for k2 in ("모델 전체", "모델 − 목", "모델 − 인허가", "★상호작용 목×인허가",
                   "★강도 상위 (연면적/필지면적)", "★대형허가 500m",
                   "★인허가 건수만 100m", "★목만", "★가격 하위 (기저효과)", "무작위"):
            print(f"    {k2:<24} {r[k2]:>3} / {K}  {'█' * int(r[k2] / 2)}")
        for col, nm in (("permits_100m", "허가건수 100m"), ("intensity_100m", "강도")):
            q = pd.qcut(d[col].rank(method="first"), 5, labels=["1", "2", "3", "4", "5"])
            v = (d.groupby(q, observed=True).excess.median() * 100).round(2)
            print(f"    {nm} 5분위별 초과상승률: " + " · ".join(f"{i} {x:+.2f}%p" for i, x in v.items()))

    res = pd.DataFrame(rows)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}\n")
    print(res.to_string(index=False))


if __name__ == "__main__":
    main()
