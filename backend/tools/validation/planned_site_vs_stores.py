"""검증 1: 이미 지어진 운정신도시에서 「길 점수가 높은 곳에 실제 가게가 몰리나」.

계획도면 화면과 같은 계산(보행 통합·선택 → 도면 안 백분위 평균)을 실제 길에 돌리고,
소상공인 상가정보(반경 조회 API)의 가게를 가장 가까운 길에 붙여 견준다.
비교 기준: 도로 등급(큰길일수록 높게)만으로 가른 결과.
"""
import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, r"C:\dev\space-syntax-next\backend")
import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, Point


S = Path(sys.argv[1])
NAME, LAT, LNG = sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
NET_R, EVAL_R, SNAP_M = 1500, 1000, 40.0
KEEP = {"음식", "소매", "생활서비스", "수리·개인서비스"}  # 길가 상가 업종
HW_RANK = {"motorway": 0, "trunk": 6, "primary": 6, "secondary": 5, "tertiary": 4, "unclassified": 3,
           "residential": 3, "living_street": 2, "service": 1, "pedestrian": 2, "footway": 1, "path": 1, "steps": 0}

cache = S / f"stores_{NAME}.json"
KAKAO_CATS = {"FD6": "음식", "CE7": "음식", "CS2": "소매"}  # 음식점·카페·편의점 — 길가 상가의 대표


def kakao_stores():
    """카카오 로컬 카테고리 검색. 한 칸에 45곳까지만 주므로 넘치면 네 칸으로 나눈다."""
    import time

    import httpx

    from app.config import settings

    key = settings.kakao_rest_key
    dlat = NET_R / 111_320
    dlng = NET_R / (111_320 * math.cos(math.radians(LAT)))
    out = {}

    def rect(code, x0, y0, x1, y1, depth=0):
        params = {"category_group_code": code, "rect": f"{x0},{y0},{x1},{y1}", "page": 1, "size": 15}
        r = httpx.get("https://dapi.kakao.com/v2/local/search/category.json", params=params,
                      headers={"Authorization": f"KakaoAK {key}"}, timeout=20)
        r.raise_for_status()
        meta = r.json()["meta"]
        time.sleep(0.05)
        if meta["total_count"] > 45 and depth < 8:
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            for a in ((x0, y0, mx, my), (mx, y0, x1, my), (x0, my, mx, y1), (mx, my, x1, y1)):
                rect(code, *a, depth=depth + 1)
            return
        page = 1
        while True:
            params["page"] = page
            j = httpx.get("https://dapi.kakao.com/v2/local/search/category.json", params=params,
                          headers={"Authorization": f"KakaoAK {key}"}, timeout=20).json()
            for d in j["documents"]:
                out[d["id"]] = {"lon": float(d["x"]), "lat": float(d["y"]), "cat": KAKAO_CATS[code], "name": d["place_name"]}
            if j["meta"]["is_end"]:
                break
            page += 1
            time.sleep(0.05)

    for code in KAKAO_CATS:
        rect(code, LNG - dlng, LAT - dlat, LNG + dlng, LAT + dlat)
    return list(out.values())


if cache.exists():
    stores = json.loads(cache.read_text(encoding="utf-8"))
else:
    stores = kakao_stores()
    cache.write_text(json.dumps(stores, ensure_ascii=False), encoding="utf-8")
if "--stores-only" in sys.argv:
    print("stores", len(stores))
    sys.exit(0)
from app.services.parcel_structure import _percentiles  # noqa: E402
from app.services.planned_site import _to_metric  # noqa: E402
from app.services.simulation import _walk_metrics  # noqa: E402
from app.services.spatial_query import fetch_roads_in_radius  # noqa: E402

cats = {}
for s in stores:
    cats[s["cat"]] = cats.get(s["cat"], 0) + 1
stores = [s for s in stores if s["cat"] in KEEP]

segs = fetch_roads_in_radius(LNG, LAT, NET_R)
w = []
m, covered = _walk_metrics(segs, w, "python")
centre = _to_metric(Point(LNG, LAT))
rows = []
for i, s in enumerate(segs):
    v = m["integration"].get(i)
    if i not in covered or not v:
        continue
    rows.append({"i": i, "geom": _to_metric(LineString(s.coordinates)), "int": v, "ch": m["choice"].get(i, 0.0), "hw": s.highway})
ip = _percentiles([r["int"] for r in rows])
cp = _percentiles([r["ch"] for r in rows])
for r in rows:
    r["score"] = (ip[r["int"]] + cp[r["ch"]]) / 2
    r["hwrank"] = HW_RANK.get(r["hw"], 2)
    r["n"] = 0

tree = STRtree([r["geom"] for r in rows])
snapped = 0
for s in stores:
    p = _to_metric(Point(s["lon"], s["lat"]))
    j = tree.nearest(p)
    if j is not None and rows[int(j)]["geom"].distance(p) <= SNAP_M:
        rows[int(j)]["n"] += 1
        snapped += 1

inner = [r for r in rows if r["geom"].centroid.distance(centre) <= EVAL_R and r["geom"].length >= 20]
for r in inner:
    r["dens"] = r["n"] / (r["geom"].length / 100)


def rank(a):
    order = np.argsort(a, kind="mergesort")
    out = np.empty(len(a))
    vals = np.asarray(a)[order]
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
    pos = [s for s, l in zip(score, label) if l]
    neg = [s for s, l in zip(score, label) if not l]
    if not pos or not neg:
        return float("nan")
    r = rank(list(pos) + list(neg))
    return float((r[: len(pos)].sum() + len(pos) - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))  # Mann-Whitney


def top_share(key, frac=0.2):
    total_len = sum(r["geom"].length for r in inner)
    total_n = sum(r["n"] for r in inner)
    acc = n = 0.0
    for r in sorted(inner, key=lambda r: -r[key]):
        if acc >= frac * total_len:
            break
        acc += r["geom"].length
        n += r["n"]
    return n / total_n if total_n else float("nan")


dens = [r["dens"] for r in inner]
label = [r["n"] > 0 for r in inner]
out = {"place": NAME, "centre": [LAT, LNG], "stores_all": sum(cats.values()), "stores_kept": len(stores),
       "stores_snapped": snapped, "segments_eval": len(inner), "segments_with_store": sum(label),
       "categories": cats, "warnings": w}
for key, label_name in (("score", "길 점수(화면과 같은 계산)"), ("int", "통합만"), ("ch", "선택만"), ("hwrank", "비교 기준: 도로 등급")):
    x = [r[key] for r in inner]
    out[label_name] = {
        "순위상관(점수 vs 가게밀도)": round(spearman(x, dens), 3),
        "상위20%길에 든 가게 비율": round(top_share(key), 3),
        "가게 있는 길 가르기 AUC": round(auc(x, label), 3),
    }
print(json.dumps(out, ensure_ascii=False, indent=1))
(S / f"validate_{NAME}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
