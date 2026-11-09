"""블록 단위 정확도 — 도시 하나를 빼고 배우고, 뺀 도시로 채점한다 (cityseer 가상환경).

  C:\\dev\\tools\\cityseer-venv\\Scripts\\python.exe evaluate_towns.py <data_dir>

잣대 (뺀 도시 안에서):
  순위상관   블록 점수 순서 vs 실제 가게 수 순서 (Spearman)
  AUC        「가게 10곳 이상 들어선 블록」을 가르는 힘 (0.5 찍기, 1 완벽)
  상위20%    점수 상위 20% 블록에 든 가게 비율 (무작위면 20%)
요인은 도시 안 백분위로 바꿔 도시끼리 견줄 수 있게 한다.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

COMMERCIAL = 10


def load(data: Path) -> pd.DataFrame:
    frames = [pd.read_csv(p, encoding="utf-8") for p in sorted(data.glob("blocks_*.csv"))]
    df = pd.concat(frames, ignore_index=True)
    feats = [c for c in df.columns if c.startswith("cc_") or c in ("area", "compact", "hw_max", "edges_touch")]
    df[feats] = df[feats].astype(float)
    # 역까지 거리: 접근성 열이 「가장 가까운 거리」. 없으면(세종) 창 크기로 채운다 — 그 도시 안에서는 모두 같다.
    for c in feats:
        if "station" in c and "nearest" in c:
            df[c] = df[c].fillna(3000)
        else:
            df[c] = df[c].fillna(df.groupby("town")[c].transform("median")).fillna(0)
    pct = df.groupby("town")[feats].rank(pct=True)
    pct.columns = [c + "_p" for c in feats]
    return pd.concat([df, pct], axis=1)


def score(y, pred):
    rho = spearmanr(pred, y).correlation
    lab = y >= COMMERCIAL
    auc = roc_auc_score(lab, pred) if lab.any() and (~lab).any() else np.nan
    order = np.argsort(-np.asarray(pred))
    top = order[: max(1, int(len(order) * 0.2))]
    share = y.iloc[top].sum() / max(1, y.sum())
    return rho, auc, share


def main() -> None:
    data = Path(sys.argv[1])
    df = load(data)
    towns = sorted(df["town"].unique())
    pcols = [c for c in df.columns if c.endswith("_p")]
    syntax = [c for c in pcols if c.startswith("cc_") and "station" not in c and "apt" not in c]
    ang5000 = [c for c in pcols if "_5000_ang" in c and c.endswith("__max_p")]  # cityseer가 이미 _ang를 붙여 _ang_ang가 된다
    h5 = next(c for c in ang5000 if "harmonic" in c)
    b5 = next(c for c in ang5000 if "betweenness" in c and "beta" not in c)
    df["now_formula"] = (df[h5] + df[b5]) / 2  # 지금 화면: 각도 통합·선택(사실상 반경 없음) 평균, 가장 좋은 변

    summary = {"towns": {t: {"blocks": int((df.town == t).sum()), "commercial": int(((df.town == t) & (df.stores >= COMMERCIAL)).sum()),
                             "stores": int(df.loc[df.town == t, "stores"].sum())} for t in towns}}

    # 1) 요인 하나씩 — 도시마다 채점해 평균
    singles = {}
    for c in pcols + ["now_formula"]:
        rs = [score(df.loc[df.town == t, "stores"].reset_index(drop=True), df.loc[df.town == t, c].values) for t in towns]
        singles[c] = [round(float(np.nanmean([r[i] for r in rs])), 3) for i in range(3)]
    best = sorted(singles.items(), key=lambda kv: -kv[1][1])
    summary["single_top15_by_auc"] = best[:15]
    summary["now_formula"] = singles["now_formula"]

    # 2) 도시 하나 빼고 배우기
    def loo(cols, kind):
        res = {}
        for t in towns:
            tr, te = df[df.town != t], df[df.town == t]
            if kind == "logit":
                m = LogisticRegression(C=0.5, max_iter=2000).fit(tr[cols], tr.stores >= COMMERCIAL)
                pred = m.predict_proba(te[cols])[:, 1]
            else:
                m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                                  l2_regularization=1.0, random_state=0).fit(tr[cols], np.log1p(tr.stores))
                pred = m.predict(te[cols])
            res[t] = [round(float(x), 3) for x in score(te.stores.reset_index(drop=True), pred)]
        mean = [round(float(np.nanmean([v[i] for v in res.values()])), 3) for i in range(3)]
        worst = [round(float(np.nanmin([v[i] for v in res.values()])), 3) for i in range(3)]
        return {"mean": mean, "worst": worst, "by_town": res}

    summary["model_syntax_only_gbm"] = loo(syntax, "gbm")
    summary["model_all_gbm"] = loo(pcols, "gbm")
    summary["model_all_logit"] = loo(pcols, "logit")
    print(json.dumps({k: v for k, v in summary.items() if k != "single_top15_by_auc"}, ensure_ascii=False, indent=1)[:6000])
    print("요인 하나씩 (AUC 순, [순위상관, AUC, 상위20%]):")
    for c, v in best[:15]:
        print(" ", c, v)
    (data / "evaluation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
