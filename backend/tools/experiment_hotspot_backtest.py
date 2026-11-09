"""파일럿 백테스트 — 뜰 자리(B) 모델이 경쟁사 방식을 이기는가.

docs/DECISIONS_LAND_MODEL.md §10(백테스트 절차) · §11(양방향) · §12(파일럿).

과거로 돌아가 「그때 알 수 있었던 것」만으로 상위 100필지를 뽑고, 3년 뒤 실제
초과상승률 상위 10%(Hot)에 몇 개가 들었는지 센다(Precision@100). 무작위 기대값은
10이므로 68이면 6.8배다.

⚠️ **운영 DB에 쓰지 않는다.** SELECT만 하고 결과는 CSV로 떨어뜨린다.
    (CLAUDE.md — 적재는 로컬에서, 검증된 결과만 한 트랜잭션으로)

⚠️ **시점 누수가 이 스크립트의 유일한 위험이다.** feature 는 base_year 행에서만
    가져오고, 라벨은 base_year+3 행에서만 가져온다. 모델 출력 컬럼
    (expected_price · price_gap · growth_gap · hot_prob)은 target 파생이라 제외한다.

Usage:
    python tools/experiment_hotspot_backtest.py
    python tools/experiment_hotspot_backtest.py --top-frac 0.10 --k 100
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 창: (feature 연도, 라벨 연도) ─────────────────────────────────────────
# 코로나 전 창은 만들 수 없다 — 패널이 2018년부터다 (§12 실사 2026-09-02).
WINDOWS = [(2018, 2021), (2020, 2023), (2023, 2026)]

# 모델 출력·식별 컬럼. target 파생이라 feature 로 절대 들어가면 안 된다.
LEAK = {
    "expected_price", "price_gap", "growth_gap", "hot_prob",
    "model_version", "predicted_at", "computed_at",
    "official_price_per_sqm",   # 로그로 바꿔 따로 넣는다
    "pnu", "base_year", "ldong_code", "sgg_code",
    "access_as_of", "structure_as_of", "redev_notice_year",
}
CATEGORICAL = [
    "zone_name", "zone_group", "land_category", "use_situation",
    "road_side_name", "topograph_height", "topograph_form", "redev_zone_class",
]


def db_url() -> str:
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("backend/.env 에 DATABASE_URL 이 없다 — bash scripts/setup-laptop.sh")


def load_panel(conn) -> pd.DataFrame:
    years = sorted({y for w in WINDOWS for y in w})
    df = pd.read_sql("SELECT * FROM parcel_panel WHERE base_year = ANY(%s)", conn, params=(years,))
    print(f"  패널 {len(df):,}행 · {df.pnu.nunique():,}필지 · {sorted(df.base_year.unique())}")
    return df


def store_density(conn) -> pd.DataFrame:
    """경쟁사 방식 대조군 — 반경 300m 점포 수.

    ⚠️ sbiz_stores 는 2026 스냅샷 하나뿐이라 과거 창에는 **미래를 보는 값**이다.
    그래도 빼지 않는다 — 대조군에 유리하게 두는 쪽이 보수적이기 때문이다.
    이걸 이기면 "누수까지 준 경쟁사 방식을 이겼다"가 된다.
    """
    sql = """
        SELECT c.pnu, count(s.*) AS stores_300m
        FROM   parcel_centroids c
        JOIN   (SELECT DISTINCT pnu FROM parcel_panel) p ON p.pnu = c.pnu
        LEFT JOIN sbiz_stores s ON ST_DWithin(s.geom_5179, c.geom_5179, 300)
        GROUP BY c.pnu
    """
    df = pd.read_sql(sql, conn)
    print(f"  점포밀도 {len(df):,}필지 (중앙값 {df.stores_300m.median():.0f}개 / 300m)")
    return df


def build_window(panel: pd.DataFrame, t0: int, t1: int) -> pd.DataFrame:
    """t0 시점 feature + t0→t1 초과상승률 라벨."""
    a = panel[panel.base_year == t0].copy()
    b = panel[panel.base_year == t1][["pnu", "official_price_per_sqm"]].rename(
        columns={"official_price_per_sqm": "price_end"}
    )
    d = a.merge(b, on="pnu", how="inner")
    d = d[(d.official_price_per_sqm > 0) & (d.price_end > 0)].copy()
    d["growth"] = d.price_end / d.official_price_per_sqm - 1.0

    # 계층 = 같은 법정동 × 같은 용도지역군. 30필지 미만이면 구 × 용도지역군으로 물러난다.
    d["stratum"] = d.ldong_code.astype(str) + "|" + d.zone_group.fillna("NA").astype(str)
    small = d.groupby("stratum").pnu.transform("size") < 30
    d.loc[small, "stratum"] = (
        d.loc[small, "sgg_code"].astype(str) + "|" + d.loc[small, "zone_group"].fillna("NA").astype(str)
    )
    d["excess"] = d.growth - d.groupby("stratum").growth.transform("mean")

    # §10 「따라잡기」 가설 — 계층 안에서 목 백분위 − 가격 백분위.
    # 목 자체가 아니라 «목에 비해 아직 값이 안 붙은 정도»가 신호라는 가설이고,
    # 이 실험 이전에 문서에 적혀 있던 것이라 사후 선택이 아니다.
    g = d.groupby("stratum")
    d["gap_pct"] = (g.structure_score.rank(pct=True) - g.official_price_per_sqm.rank(pct=True)) * 100
    return d.reset_index(drop=True)


def make_xy(d: pd.DataFrame, top_frac: float):
    thr = d.excess.quantile(1 - top_frac)
    y = (d.excess >= thr).astype(int)

    drop_now = [c for c in d.columns
                if c in LEAK or c in ("price_end", "growth", "excess", "stratum")]
    feat = d.drop(columns=drop_now, errors="ignore").copy()
    feat["log_price"] = np.log(d.official_price_per_sqm)   # 가격 수준 (§11 평균회귀 통제)

    for c in CATEGORICAL:
        if c in feat.columns:
            feat[c] = feat[c].astype("category")
    feat = feat.drop(columns=[c for c in feat.columns if feat[c].dtype == object], errors="ignore")
    feat = feat.drop(columns=[c for c in feat.columns
                              if feat[c].isna().all() or feat[c].nunique(dropna=True) <= 1],
                     errors="ignore")
    return feat, y.values, thr


def precision_at_k(score: np.ndarray, y: np.ndarray, k: int) -> int:
    return int(y[np.argsort(-score, kind="stable")[:k]].sum())


def _oof(X, y, groups, seed: int) -> np.ndarray:
    import lightgbm as lgb
    from sklearn.model_selection import GroupKFold

    oof = np.zeros(len(X))
    for tr, te in GroupKFold(n_splits=5).split(X, y, groups):
        m = lgb.LGBMClassifier(
            n_estimators=400, learning_rate=0.05, num_leaves=31,
            min_child_samples=40, subsample=0.9, colsample_bytree=0.8,
            random_state=seed, verbose=-1,
        )
        m.fit(X.iloc[tr], y[tr])
        oof[te] = m.predict_proba(X.iloc[te])[:, 1]
    return oof


def run_window(d: pd.DataFrame, top_frac: float, k: int, seed: int = 0) -> dict:
    X, y, thr = make_xy(d, top_frac)
    groups = d.ldong_code.fillna("NA").values        # 공간 홀드아웃: 법정동 블록

    oof = _oof(X, y, groups, seed)
    X_ns = X.drop(columns=[c for c in X.columns if "structure" in c], errors="ignore")
    oof_ns = _oof(X_ns, y, groups, seed)             # 목 변수군을 뺀 모델

    rng = np.random.default_rng(seed)
    return {
        "n": len(d),
        "hot": int(y.sum()),
        "thr_excess_pp": round(float(thr) * 100, 2),
        "우리 (전체)": precision_at_k(oof, y, k),
        "목 변수군 뺀 모델": precision_at_k(oof_ns, y, k),
        "목만": precision_at_k(d.structure_score.fillna(-1).values, y, k),
        "갭 상위 (§10 따라잡기)": precision_at_k(d.gap_pct.fillna(-999).values, y, k),
        "점포밀도 상위 (경쟁사 방식)": precision_at_k(d.stores_300m.fillna(-1).values, y, k),
        "과거 3년 상승률 상위": precision_at_k(d.price_growth_3y.fillna(-999).values, y, k),
        "무작위": int(np.mean([precision_at_k(rng.random(len(d)), y, k) for _ in range(20)])),
    }


ROW_ORDER = ("우리 (전체)", "목 변수군 뺀 모델", "갭 상위 (§10 따라잡기)", "목만",
             "점포밀도 상위 (경쟁사 방식)", "과거 3년 상승률 상위", "무작위")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-frac", type=float, default=0.10, help="정답 비율 — 돌리기 전에 고정한다")
    ap.add_argument("--k", type=int, default=100, help="Precision@K 의 K")
    ap.add_argument("--out", default="backend/tools/out/hotspot_backtest.csv")
    args = ap.parse_args()

    print(f"\n뜰 자리 파일럿 백테스트 — 정답 상위 {args.top_frac:.0%} · Precision@{args.k}")
    print("=" * 72)

    conn = psycopg2.connect(db_url())
    try:
        panel = load_panel(conn)
        dens = store_density(conn)
    finally:
        conn.close()

    rows = []
    for t0, t1 in WINDOWS:
        d = build_window(panel, t0, t1).merge(dens, on="pnu", how="left")
        if len(d) < 1000:
            print(f"\n[{t0}→{t1}] 표본 {len(d)} — 건너뛴다")
            continue
        r = {"창": f"{t0}→{t1}", **run_window(d, args.top_frac, args.k)}
        rows.append(r)
        print(f"\n[{t0}→{t1}]  필지 {r['n']:,} · Hot {r['hot']:,} · "
              f"정답 문턱 초과상승 {r['thr_excess_pp']}%p")
        for key in ROW_ORDER:
            print(f"    {key:<26} {r[key]:>3} / {args.k}  {'█' * int(r[key] / 2)}")

    if rows:
        res = pd.DataFrame(rows)
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        res.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n저장: {out}\n")
        print(res.to_string(index=False))


if __name__ == "__main__":
    main()
