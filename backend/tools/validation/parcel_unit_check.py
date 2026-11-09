"""신도시 한 곳에서 「땅을 어떤 크기로 나눠 채점하나」를 견준다 (2026-10-27, cityseer 가상환경).

  C:\\dev\\tools\\cityseer-venv\\Scripts\\python.exe parcel_unit_check.py <town> <data_dir> <out_json>

견주는 세 단위 — 모두 같은 OSM 길 원본(osm_<town>.json)·가게(pois)·아파트(apts)·지적도(parcels)로 만든다.
  big     큰 도로(3차로 tertiary 이상)로만 둘러싼 블록
  all     보행로·골목까지 모든 길로 둘러싼 블록 (지금 학습 방식과 같은 뜻)
  parcel  지적도 필지 (도로·하천·구거·철도·제방 필지는 뺀다) — 실제로 나눠 파는 단위
요인은 계획도면에서도 얻을 수 있는 것만: 넓이·모양·둘레 30m 안 가장 큰 길·닿은 길 수·아파트 여부·
주변 400/800m 아파트 땅 넓이·역까지 직선거리. 길 구조(syntax)는 10곳 학습에서 +0.02~0.03뿐이라 뺐다.
parcel에는 「그 필지가 든 블록(all)」 요인을 더한 판(parcel+block)도 잰다.

한 도시 안에서 600m 격자칸 단위로 5겹 교차검증(GroupKFold) — 옆 땅이 답을 흘리지 않게 칸째로 뺀다.
같은 잣대로 견주려고 정답은 모두 **필지 단위**로 둔다: 필지마다 그 필지가 든 블록의 점수(블록 방식)
또는 필지 자기 점수(필지 방식)로 순서를 매겨, 가게가 있는 필지·5곳 이상인 필지를 얼마나 앞에 두나.
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.stats import spearmanr
from shapely.geometry import LineString, Point, shape
from shapely.ops import polygonize, unary_union
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cityseer_town import CRS, EVAL_M, HW_RANK, SIDE_M  # noqa: E402
from towns import TOWNS  # noqa: E402

PARAMS = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0, random_state=0)
NON_LAND = set("도구천철제")  # 지목 끝 글자: 도로·구거·하천·철도용지·제방
GRID_M = 600


def osm_ways(path: Path, to_m) -> gpd.GeoDataFrame:
    d = json.loads(path.read_text(encoding="utf-8"))
    nodes = {e["id"]: to_m.transform(e["lon"], e["lat"]) for e in d["elements"] if e["type"] == "node"}
    rows = []
    for e in d["elements"]:
        hw = (e.get("tags") or {}).get("highway")
        if e["type"] != "way" or hw not in HW_RANK or (e.get("tags") or {}).get("footway") == "sidewalk":
            continue
        pts = [nodes[n] for n in e["nodes"] if n in nodes]
        if len(pts) >= 2:
            rows.append((HW_RANK[hw], LineString(pts)))
    return gpd.GeoDataFrame({"hw": [r[0] for r in rows]}, geometry=[r[1] for r in rows], crs=CRS)


def faces(lines: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    fs = [p for p in polygonize(unary_union(list(lines.geometry))) if 800 <= p.area <= 300_000]
    return gpd.GeoDataFrame(geometry=fs, crs=CRS)


def add_features(u: gpd.GeoDataFrame, ways: gpd.GeoDataFrame, apts: gpd.GeoDataFrame, stations: list[Point]) -> pd.DataFrame:
    u = u.reset_index(drop=True)
    f = pd.DataFrame(index=u.index)
    f["area"] = u.area
    f["compact"] = (4 * np.pi * u.area / u.length**2).clip(0, 1)
    ring = gpd.GeoDataFrame(geometry=u.boundary.buffer(SIDE_M), crs=CRS)
    ej = gpd.sjoin(ways[["hw", "geometry"]], ring, predicate="intersects")
    g = ej.groupby("index_right")["hw"]
    f["hw_max"] = g.max().reindex(u.index).fillna(0)
    f["edges_touch"] = g.count().reindex(u.index).fillna(0)
    inside = gpd.sjoin(apts, u[["geometry"]], predicate="within")["index_right"]
    f["res"] = u.index.isin(inside.unique()).astype(float)
    cen = u.centroid
    cx, cy = cen.x.values, cen.y.values
    rmask = f["res"].values == 1
    for r in (400, 800):
        out = np.zeros(len(u))
        for i in range(len(u)):
            d = np.hypot(cx[rmask] - cx[i], cy[rmask] - cy[i])
            out[i] = f["area"].values[rmask][d <= r].sum() - (f["area"].values[i] if rmask[i] else 0)
        f[f"res_area_{r}"] = out
    f["station_m"] = [min((p.distance(c) for p in stations), default=5000.0) for c in cen]
    f["station_m"] = f["station_m"].clip(upper=3000)
    return f


def count_stores(u: gpd.GeoDataFrame, stores: gpd.GeoDataFrame) -> np.ndarray:
    u = u.reset_index(drop=True)
    hit = gpd.sjoin(stores, u[["geometry"]], how="left", predicate="within")
    hit = hit[~hit.index.duplicated()]
    miss = hit[hit["index_right"].isna()].drop(columns="index_right")
    near = gpd.sjoin_nearest(miss, u[["geometry"]], how="left", max_distance=SIDE_M)
    near = near[~near.index.duplicated()]
    got = pd.concat([hit["index_right"].dropna(), near["index_right"].dropna()]).astype(int).value_counts()
    return u.index.map(got).fillna(0).astype(int).values


def cv_predict(x: pd.DataFrame, y: np.ndarray, groups: np.ndarray) -> np.ndarray:
    pred = np.zeros(len(x))
    for tr, te in GroupKFold(5).split(x, y, groups):
        m = HistGradientBoostingRegressor(**PARAMS).fit(x.iloc[tr], np.log1p(y[tr]))
        pred[te] = m.predict(x.iloc[te])
    return pred


def score(stores: np.ndarray, pred: np.ndarray) -> dict:
    top = pred >= np.quantile(pred, 0.8)
    return {
        "auc_any": round(float(roc_auc_score(stores >= 1, pred)), 3),
        "auc_5plus": round(float(roc_auc_score(stores >= 5, pred)), 3),
        "rho": round(float(spearmanr(stores, pred).statistic), 3),
        "top20_share": round(float(stores[top].sum() / max(stores.sum(), 1)), 3),
    }


def main() -> None:
    town, data, out_json = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    lat, lng = TOWNS[town]
    to_m = Transformer.from_crs("EPSG:4326", f"EPSG:{CRS}", always_xy=True)
    centre = Point(*to_m.transform(lng, lat))
    ways = osm_ways(data / f"osm_{town}.json", to_m)
    pois = json.loads((data / f"pois_{town}.json").read_text(encoding="utf-8"))
    stores = gpd.GeoDataFrame(geometry=[Point(*to_m.transform(s["lon"], s["lat"])) for s in pois["stores"]], crs=CRS)
    stations = [Point(*to_m.transform(s["lon"], s["lat"])) for s in pois["stations"]]
    apts = gpd.GeoDataFrame(geometry=[Point(*to_m.transform(x, y)) for x, y in
                                      json.loads((data / f"apts_{town}.json").read_text(encoding="utf-8"))], crs=CRS)

    pj = json.loads((data / f"parcels_{town}.geojson").read_text(encoding="utf-8"))["features"]
    jimok = [str(f["properties"].get("jibun") or "")[-1:] for f in pj]
    parcels = gpd.GeoDataFrame({"jimok": jimok}, geometry=[shape(f["geometry"]) for f in pj], crs=4326).to_crs(CRS)
    parcels = parcels[~parcels["jimok"].isin(NON_LAND) & (parcels.area >= 30) & (parcels.area <= 300_000)]
    parcels = parcels[parcels.centroid.distance(centre) <= EVAL_M].reset_index(drop=True)

    units = {"big": faces(ways[ways.hw >= 4]), "all": faces(ways), "parcel": parcels[["geometry"]]}
    feats, counts = {}, {}
    for k, u in units.items():
        u = u[u.centroid.distance(centre) <= EVAL_M + 300].reset_index(drop=True)  # 둘레 블록도 주변 아파트 계산에 쓴다
        units[k] = u
        feats[k] = add_features(u, ways, apts, stations)
        counts[k] = count_stores(u, stores)

    # 필지 → 그 필지가 든 블록
    pc = units["parcel"].representative_point()
    parent = {}
    for k in ("big", "all"):
        j = gpd.sjoin(gpd.GeoDataFrame(geometry=pc, crs=CRS), units[k][["geometry"]], how="left", predicate="within")
        parent[k] = j[~j.index.duplicated()]["index_right"].values

    pcen = units["parcel"].centroid
    eval_mask = (pcen.distance(centre) <= EVAL_M).values
    groups_of = lambda c: (np.floor(c.x.values / GRID_M) * 10_000 + np.floor(c.y.values / GRID_M)).astype(int)  # noqa: E731

    preds = {}
    for k in ("big", "all", "parcel"):
        preds[k] = cv_predict(feats[k], counts[k], groups_of(units[k].centroid))
    xb = feats["parcel"].join(feats["all"].add_prefix("blk_").reindex(parent["all"]).reset_index(drop=True))
    preds["parcel+block"] = cv_predict(xb, counts["parcel"], groups_of(pcen))
    # 넓이 하나만 — 「큰 필지에 가게가 많다」만으로 얼마나 나오나 (바닥선)
    preds["parcel_area_only"] = cv_predict(feats["parcel"][["area"]], counts["parcel"], groups_of(pcen))

    y = counts["parcel"][eval_mask]
    result = {"town": town, "date": "2026-10-27", "units": {}, "parcel_level": {}}
    for k in ("big", "all", "parcel"):
        u = units[k]
        m = (u.centroid.distance(centre) <= EVAL_M).values
        result["units"][k] = {
            "count": int(m.sum()),
            "median_area_m2": round(float(u.area[m].median())),
            "with_stores": int((counts[k][m] > 0).sum()),
            "own_unit_score": score(counts[k][m], preds[k][m]),
        }
    for k in ("big", "all"):
        par = parent[k][eval_mask]
        ok = ~pd.isna(par)
        p = np.full(len(par), np.nan)
        p[ok] = preds[k][par[ok].astype(int)]
        result["parcel_level"][f"{k}_block_score"] = {**score(y[ok], p[ok]), "parcels_in_a_block": int(ok.sum())}
        # 같은 블록 안 필지는 점수가 같다 — 블록당 필지 수
        result["units"][k]["parcels_per_block_median"] = float(pd.Series(par[ok]).value_counts().median())
    for k in ("parcel_area_only", "parcel", "parcel+block"):
        result["parcel_level"][k] = score(y, preds[k][eval_mask])
    result["parcel_level"]["parcels"] = int(eval_mask.sum())
    result["parcel_level"]["parcels_with_stores"] = int((y > 0).sum())
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
