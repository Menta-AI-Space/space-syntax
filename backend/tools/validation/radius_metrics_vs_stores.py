"""검증 1b: F10 조사의 정밀 계산(OSM 정리 + depthmapX 반경 500·1000·2000m, '현재' 시나리오)을
운정 실제 가게(카카오 음식점·카페·편의점)와 견준다. validate_unjeong.py와 같은 잣대."""
import json
import math
import sys
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Point

S = Path(sys.argv[1])
NAME, LAT, LNG = sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
EVAL_R = float(sys.argv[5]) if len(sys.argv) > 5 else 1000.0
SNAP_M = 40.0
to_m = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
d = json.loads(Path(r"C:\dev\tools\scratch\unjeong-f10\work\segments_scored.json").read_text(encoding="utf-8"))["current"]
stores = json.loads((S / f"stores_{NAME}.json").read_text(encoding="utf-8"))
HW_RANK = {"motorway": 0, "trunk": 6, "primary": 6, "secondary": 5, "tertiary": 4, "unclassified": 3,
           "residential": 3, "living_street": 2, "service": 1, "pedestrian": 2, "footway": 1, "path": 1, "steps": 0}

centre = Point(*to_m.transform(LNG, LAT))
geoms = [LineString(l) for l in d["lines"]]
keys = [k for k in d if k.startswith(("integration_r", "choice_r")) and not k.endswith("_pct")]
rows = []
for i, g in enumerate(geoms):
    if d["integration_r1000"][i] is None:
        continue
    r = {"g": g, "n": 0, "hw": HW_RANK.get(d["meta"][i]["highway"], 2)}
    for k in keys:
        r[k] = d[k][i] if d[k][i] is not None else 0.0
    rows.append(r)

tree = STRtree([r["g"] for r in rows])
snapped = 0
for s in stores:
    p = Point(*to_m.transform(s["lon"], s["lat"]))
    j = tree.nearest(p)
    if j is not None and rows[int(j)]["g"].distance(p) <= SNAP_M:
        rows[int(j)]["n"] += 1
        snapped += 1
inner = [r for r in rows if r["g"].centroid.distance(centre) <= EVAL_R and r["g"].length >= 10]
# 백분위는 평가 구역 안에서 다시 — 화면과 같은 「통합·선택 백분위 평균」
for k in keys:
    order = sorted(r[k] for r in inner)
    for r in inner:
        r[k + "_p"] = 100 * np.searchsorted(order, r[k]) / max(1, len(order) - 1)
for R in ("500", "1000", "2000"):
    for r in inner:
        r[f"mix_r{R}"] = (r[f"integration_r{R}_p"] + r[f"choice_r{R}_p"]) / 2
for r in inner:
    r["dens"] = r["n"] / (r["g"].length / 100)


def rank(a):
    a = np.asarray(a, dtype=float)
    order = np.argsort(a, kind="mergesort")
    out = np.empty(len(a))
    vals = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and vals[j + 1] == vals[i]:
            j += 1
        out[order[i:j + 1]] = (i + j) / 2
        i = j + 1
    return out


def spearman(x, y):
    return float(np.corrcoef(rank(x), rank(y))[0, 1])


def auc(score, label):
    score, label = np.asarray(score, float), np.asarray(label, bool)
    r = rank(score)
    npos, nneg = label.sum(), (~label).sum()
    return float((r[label].sum() + npos - npos * (npos + 1) / 2 - 0) / (npos * nneg)) if npos and nneg else float("nan")


def auc_mw(score, label):
    score, label = np.asarray(score, float), np.asarray(label, bool)
    r = rank(score) + 1
    npos, nneg = label.sum(), (~label).sum()
    return float((r[label].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def top_share(key, frac=0.2):
    total_len = sum(r["g"].length for r in inner)
    total_n = sum(r["n"] for r in inner)
    acc = n = 0.0
    for r in sorted(inner, key=lambda r: -r[key]):
        if acc >= frac * total_len:
            break
        acc += r["g"].length
        n += r["n"]
    return n / total_n


dens = [r["dens"] for r in inner]
label = [r["n"] > 0 for r in inner]
out = {"place": NAME, "eval_r": EVAL_R, "segments_eval": len(inner), "with_store": int(sum(label)),
       "stores_snapped": snapped, "stores_in_eval": int(sum(r["n"] for r in inner))}
for key in ["integration_r500", "integration_r1000", "integration_r2000", "choice_r500",
            "choice_r1000", "choice_r2000", "mix_r500", "mix_r1000", "mix_r2000", "hw"]:
    x = [r[key] for r in inner]
    out[key] = [round(spearman(x, dens), 3), round(top_share(key), 3), round(auc_mw(x, label), 3)]
print(json.dumps(out, ensure_ascii=False, indent=0))
(S / f"validate_radius_{NAME}_{int(EVAL_R)}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

# 합치면 나아지나 — 도로 등급 + 통합(반경 2km) 백분위를 같은 무게로
hw_order = sorted(r["hw"] for r in inner)
for r in inner:
    r["hw_p"] = 100 * np.searchsorted(hw_order, r["hw"]) / max(1, len(hw_order) - 1)
    r["hw_plus_int2000"] = (r["hw_p"] + r["integration_r2000_p"]) / 2
x = [r["hw_plus_int2000"] for r in inner]
print("hw+int2000", [round(spearman(x, dens), 3), round(top_share("hw_plus_int2000"), 3), round(auc_mw(x, label), 3)])
