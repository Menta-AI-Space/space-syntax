"""앱에 넣을 계획도면 모델 — 나무(GBM)를 JSON으로 내보낸다 (cityseer 가상환경).

  C:\\dev\\tools\\cityseer-venv\\Scripts\\python.exe train_plan_model.py <data_dir> <out_json>

운영 서버에는 sklearn이 없다. 그래서 HistGradientBoosting 나무를 노드 표로 꺼내 `app/services/plan_model.py`가
순수 파이썬으로 읽는다. 꺼낸 뒤 sklearn 예측과 같은지 확인한다.

요인은 도면에서 앱이 같은 뜻으로 잴 수 있는 것만, 물리 단위 그대로 (2026-10-27):
  area        블록 넓이 m² (길 가운데 선으로 둘러싸인 면)
  compact     블록 모양 4πA/P²
  hw_max      둘레 30m 안 가장 높은 도로 등급 (OSM 분류, 도면 길은 폭으로 환산)
  res_block   블록 안에 아파트가 있나 (도면의 A용지, 도면 밖은 OSM 아파트 건물)
  res_area_400, res_area_800   블록 중심 400·800m 안 다른 아파트 블록 넓이 합 m²
  syntax      블록 둘레 길의 통합·선택 백분위 평균 (그 창 안 순서 — 엔진이 달라도 순서는 비슷)
  station_m   가장 가까운 역까지 직선거리 m (3,000에서 자름)
뺀 것: 닿은 길 수(edges_touch) — 학습은 OSM 길 단위, 앱은 잘린 조각 단위라 같은 뜻이 아니다.
도시 안 백분위로 바꾸지 않는다 — 도면 한 장은 블록이 수십 개라 백분위가 흔들린다(백분위 GBM 0.855 → 물리 단위 0.84).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from evaluate_towns import COMMERCIAL, score

PARAMS = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0, random_state=0)
STATION_CAP_M = 3000.0


def load(data: Path) -> pd.DataFrame:
    df = pd.concat([pd.read_csv(p, encoding="utf-8") for p in sorted(data.glob("blocks_*.csv"))], ignore_index=True)
    h = df["cc_harmonic_2000_ang_ang__max"].astype(float)
    b = df["cc_betweenness_2000_ang_ang__max"].astype(float)
    df["syntax"] = (h.groupby(df.town).rank(pct=True).fillna(0.5) + b.groupby(df.town).rank(pct=True).fillna(0.5)) / 2
    df["hw_max"] = df["hw_max"].fillna(2)
    df["station_m"] = df["station_m"].clip(upper=STATION_CAP_M)
    return df


FEATURES = {
    "with_station": ["area", "compact", "hw_max", "res_block", "res_area_400", "res_area_800", "syntax", "station_m"],
    "no_station": ["area", "compact", "hw_max", "res_block", "res_area_400", "res_area_800", "syntax"],
}


def export_trees(model: HistGradientBoostingRegressor) -> dict:
    trees = []
    for predictors in model._predictors:
        nodes = predictors[0].nodes
        trees.append({
            "feature": nodes["feature_idx"].tolist(),
            "threshold": [float(v) for v in nodes["num_threshold"]],  # 반올림하면 같은 값이 많은 요인(백분위)에서 갈림이 뒤집힌다
            "left": nodes["left"].tolist(),
            "right": nodes["right"].tolist(),
            "leaf": nodes["is_leaf"].astype(int).tolist(),
            "missing_left": nodes["missing_go_to_left"].astype(int).tolist(),
            "value": [round(float(v), 9) for v in nodes["value"]],
        })
    baseline = float(np.ravel(model._baseline_prediction)[0])
    return {"baseline": baseline, "trees": trees}


def predict_exported(m: dict, row: list[float]) -> float:
    total = m["baseline"]
    for t in m["trees"]:
        i = 0
        while not t["leaf"][i]:
            x = row[t["feature"][i]]
            go_left = t["missing_left"][i] if x is None or x != x else x <= t["threshold"][i]
            i = t["left"][i] if go_left else t["right"][i]
        total += t["value"][i]
    return total


def main() -> None:
    data, out = Path(sys.argv[1]), Path(sys.argv[2])
    df = load(data)
    towns = sorted(df.town.unique())
    result = {
        "trained_on": towns,
        "blocks": int(len(df)),
        "commercial_threshold": COMMERCIAL,
        "target": "log1p(블록 가게 수) — 카카오 음식점·카페·편의점·약국·병원·학원",
        "station_cap_m": STATION_CAP_M,
        "models": {},
    }
    for name, cols in FEATURES.items():
        x, y = df[cols].astype(float), np.log1p(df.stores)
        evals = {}
        for scope, mask in (("all_blocks", np.ones(len(df), bool)), ("non_residential", (df.res_block == 0).values)):
            res = {}
            for t in towns:
                tr, te = mask & (df.town != t).values, mask & (df.town == t).values
                m = HistGradientBoostingRegressor(**PARAMS).fit(x[tr], y[tr])
                res[t] = [round(float(v), 3) for v in score(df.stores[te].reset_index(drop=True), m.predict(x[te]))]
            evals[scope] = {
                "mean": [round(float(np.nanmean([v[i] for v in res.values()])), 3) for i in range(3)],
                "worst_auc": round(min(v[1] for v in res.values()), 3),
                "worst_town": min(res, key=lambda t: res[t][1]),
                "by_town": res,
            }
        full = HistGradientBoostingRegressor(**PARAMS).fit(x, y)
        exported = export_trees(full)
        sample = x.sample(300, random_state=1)
        mine = np.array([predict_exported(exported, r) for r in sample.values.tolist()])
        gap = float(np.abs(mine - full.predict(sample)).max())
        assert gap < 1e-6, f"내보낸 나무가 sklearn과 다르다: {gap}"
        # 앱이 「이 도면 안 순서」와 함께 「지어진 신도시에서 이 점수면 상가 블록이 몇 %였나」를 말할 수 있게
        pred_all = full.predict(x)
        cuts = np.quantile(pred_all, np.linspace(0, 1, 11)).tolist()
        share = [float(((df.stores >= COMMERCIAL) & (pred_all >= lo) & (pred_all <= hi)).sum()
                       / max(1, ((pred_all >= lo) & (pred_all <= hi)).sum())) for lo, hi in zip(cuts[:-1], cuts[1:])]
        result["models"][name] = {"features": cols, **exported, "eval": evals, "max_abs_gap_vs_sklearn": gap,
                                  "deciles": [round(c, 6) for c in cuts], "commercial_share_by_decile": [round(s, 3) for s in share]}
        print(name, {k: (v["mean"], v["worst_auc"], v["worst_town"]) for k, v in evals.items()}, "나무", len(exported["trees"]))
        print("  상가 블록 비율(예측 10분위 낮음→높음)", [round(s, 2) for s in share])
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print("저장", out, round(out.stat().st_size / 1024), "KB")


if __name__ == "__main__":
    main()
