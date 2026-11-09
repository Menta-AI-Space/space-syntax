"""계획도면에서 얻을 수 있는 요인만으로 — 도시 하나를 빼고 배우고, 뺀 도시로 채점 (cityseer 가상환경).

  C:\\dev\\tools\\cityseer-venv\\Scripts\\python.exe evaluate_plan.py <data_dir>

evaluate_towns.py와 같은 잣대(순위상관·AUC·상위20%)지만 요인을 「도면에 있는 것」으로 묶는다 (2026-10-26 소유자 지시):
  길 구조  각도 통합·선택 반경 2km (블록 둘레 30m 안 최댓값·평균) — 화면 반경을 2km로 맞춘다
  길 등급  둘레 길의 가장 높은 등급, 닿은 길 수 — 도면의 도로 폭
  땅       블록 넓이·모양
  주거     블록이 아파트 땅인가, 400·800m 안 다른 아파트 블록 넓이 — 도면의 A용지
  역       가장 가까운 역까지 직선거리 — 사용자가 찍을 계획 역
빼는 것: OSM 아파트 건물 수, 걸어서 역까지(카카오 역 위치로 잰 망 거리), 창 중심 거리.
채점은 두 가지 — 모든 블록, 그리고 아파트 블록을 뺀 블록(화면은 상가 땅끼리만 견준다).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression

from evaluate_towns import COMMERCIAL, score

SYNTAX = ["cc_harmonic_2000_ang_ang__max", "cc_harmonic_2000_ang_ang__mean",
          "cc_betweenness_2000_ang_ang__max", "cc_betweenness_2000_ang_ang__mean"]
ROAD = ["hw_max", "edges_touch"]
LAND = ["area", "compact"]
HOMES = ["res_block", "res_area_400", "res_area_800"]
STATION = ["station_m"]
GROUPS = {
    "길 구조 r2000만": SYNTAX,
    "+ 길 등급": SYNTAX + ROAD,
    "+ 땅 크기": SYNTAX + ROAD + LAND,
    "+ 주거": SYNTAX + ROAD + LAND + HOMES,
    "+ 역 (전부)": SYNTAX + ROAD + LAND + HOMES + STATION,
    "길 빼고 전부": ROAD + LAND + HOMES + STATION,
}


def load(data: Path) -> pd.DataFrame:
    frames = [pd.read_csv(p, encoding="utf-8") for p in sorted(data.glob("blocks_*.csv"))]
    df = pd.concat(frames, ignore_index=True)
    feats = sorted({c for g in GROUPS.values() for c in g})
    missing = [c for c in feats if c not in df.columns]
    if missing:
        raise SystemExit(f"요인 없음 {missing} — cityseer_town.py를 다시 돌린 결과가 필요하다")
    df[feats] = df[feats].astype(float)
    for c in feats:
        df[c] = df[c].fillna(df.groupby("town")[c].transform("median")).fillna(0)
    # 도시마다 규모가 달라 도시 안 백분위로. 역 거리는 m 그대로도 의미가 있어 둘 다 둔다.
    pct = df.groupby("town")[feats].rank(pct=True)
    pct.columns = [c + "_p" for c in feats]
    df = pd.concat([df, pct], axis=1)
    df["station_km"] = df["station_m"].clip(upper=5000) / 1000
    return df


def loo(df: pd.DataFrame, cols: list[str], kind: str) -> dict:
    towns = sorted(df["town"].unique())
    res = {}
    for t in towns:
        tr, te = df[df.town != t], df[df.town == t]
        if kind == "logit":
            m = LogisticRegression(C=0.5, max_iter=3000).fit(tr[cols], tr.stores >= COMMERCIAL)
            pred = m.predict_proba(te[cols])[:, 1]
        else:
            m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                              l2_regularization=1.0, random_state=0).fit(tr[cols], np.log1p(tr.stores))
            pred = m.predict(te[cols])
        res[t] = [round(float(x), 3) for x in score(te.stores.reset_index(drop=True), pred)]
    mean = [round(float(np.nanmean([v[i] for v in res.values()])), 3) for i in range(3)]
    worst = [round(float(np.nanmin([v[i] for v in res.values()])), 3) for i in range(3)]
    worst_town = min(res, key=lambda t: res[t][1])
    return {"mean": mean, "worst": worst, "worst_town": worst_town, "by_town": res}


def main() -> None:
    data = Path(sys.argv[1])
    df = load(data)
    out = {"towns": {t: {"blocks": int((df.town == t).sum()),
                         "commercial": int(((df.town == t) & (df.stores >= COMMERCIAL)).sum()),
                         "res_blocks": int(((df.town == t) & (df.res_block == 1)).sum()),
                         "apt_points": int(df.loc[df.town == t, "apt_points"].max()) if "apt_points" in df else None}
                     for t in sorted(df.town.unique())}}
    # 지금 화면에 가까운 기준선: 반경 2km 각도 통합·선택 최댓값 평균 (한 블록의 가장 좋은 변)
    df["now_r2000"] = (df["cc_harmonic_2000_ang_ang__max_p"] + df["cc_betweenness_2000_ang_ang__max_p"]) / 2
    for label, sub in (("all_blocks", df), ("non_residential", df[df.res_block == 0])):
        sub = sub.reset_index(drop=True)
        r = {"now_r2000_formula": [round(float(np.nanmean([score(sub.loc[sub.town == t, "stores"].reset_index(drop=True),
                                                                   sub.loc[sub.town == t, "now_r2000"].values)[i]
                                                             for t in sub.town.unique()])), 3) for i in range(3)]}
        for name, cols in GROUPS.items():
            pc = [c + "_p" for c in cols]
            if "station_m" in cols:
                pc = pc + ["station_km"]
            r[name] = {"gbm": loo(sub, pc, "gbm"), "logit": loo(sub, pc, "logit")}
        out[label] = r
        print(f"\n== {label} ({len(sub)} 블록) [순위상관, AUC, 상위20%] 평균 / 가장 나쁜 도시")
        print("  지금 화면 공식(r2000)", r["now_r2000_formula"])
        for name in GROUPS:
            g = r[name]["gbm"]
            lg = r[name]["logit"]
            print(f"  {name:12s} GBM {g['mean']} 최저 {g['worst']} ({g['worst_town']}) | 로지스틱 {lg['mean']} 최저 {lg['worst']}")
    (data / "evaluation_plan.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
