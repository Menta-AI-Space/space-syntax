"""신도시 하나 → 블록(땅)마다 「실제 가게 수」와 후보 요인. cityseer 가상환경에서 돌린다.

  C:\\dev\\tools\\cityseer-venv\\Scripts\\python.exe cityseer_town.py <town> <data_dir>

1. 길: cityseer로 OSM을 직접 받는다(중심 3.5km, EPSG:5179). 50m 간격으로 쪼개 길 위 점을 고르게 둔다.
2. 중심성: 각도(simplest — 공간구문론과 같은 「덜 꺾이는 길」)·거리(shortest) 두 방식,
   반경 500·1000·2000·5000m. 5000m는 창이 3.5km라 사실상 「반경 없음」 = 지금 화면 공식.
3. 접근성: 걸어서 가장 가까운 역까지(m), 400·800m 안 아파트 건물 수.
4. 블록: 길로 둘러싸인 면. 가게는 블록 안이면 그 블록, 아니면 30m 안 가장 가까운 블록.
   블록 요인 = 블록 둘레 30m 안 길 점의 최댓값(가장 좋은 변)·평균.
"""
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from cityseer.metrics import layers, networks
from cityseer.tools import graphs, io
from pyproj import Transformer
from shapely.geometry import Point
from shapely.ops import polygonize, unary_union

logging.disable(logging.WARNING)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from towns import TOWNS  # noqa: E402

CRS = 5179
WINDOW_M = 3500
DISTANCES = [500, 1000, 2000, 5000]
EVAL_M = 1800  # 가게는 중심 2km까지 모았다 — 블록이 통째로 들어가게 조금 안쪽만 평가
SIDE_M = 30.0
OVERPASS_MIRRORS = ("https://overpass.private.coffee/api", "https://overpass-api.de/api", "https://overpass.kumi.systems/api")
HW_RANK = {"trunk": 6, "primary": 6, "secondary": 5, "tertiary": 4, "unclassified": 3, "residential": 3,
           "living_street": 2, "pedestrian": 2, "service": 1, "footway": 1, "path": 1, "cycleway": 1, "steps": 0}


def osm_apartments(data: Path, town: str, lat: float, lng: float) -> list[tuple[float, float]]:
    """아파트 건물 중심(경도, 위도) — OSM Overpass. 10곳 모두 같은 출처로 맞춘다(DB는 일부 도시만 덮는다)."""
    import requests

    cache = data / f"apts_{town}.json"
    if cache.exists():
        return [tuple(p) for p in json.loads(cache.read_text(encoding="utf-8"))]
    q = (f'[out:json][timeout:180];(way["building"="apartments"](around:{WINDOW_M},{lat},{lng});'
         f'relation["building"="apartments"](around:{WINDOW_M},{lat},{lng}););out center;')
    import time

    headers = {"User-Agent": "space-syntax-next validation (planned-site accuracy check)", "Accept": "application/json"}
    for url in ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
                "https://overpass.kumi.systems/api/interpreter"):
        time.sleep(3)
        try:
            r = requests.post(url, data={"data": q}, headers=headers, timeout=240)
            r.raise_for_status()
            pts = [(e["center"]["lon"], e["center"]["lat"]) for e in r.json()["elements"] if "center" in e]
            cache.write_text(json.dumps(pts), encoding="utf-8")
            return pts
        except Exception as exc:  # noqa: BLE001
            print("overpass 실패", url, exc)
    return []


def main() -> None:
    town, data = sys.argv[1], Path(sys.argv[2])
    lat, lng = TOWNS[town]
    to_m = Transformer.from_crs("EPSG:4326", f"EPSG:{CRS}", always_xy=True)
    centre = Point(*to_m.transform(lng, lat))

    poly, _ = io.buffered_point_poly(lng, lat, WINDOW_M)
    # 공용 overpass-api.de는 자주 꽉 찬다(2026-10-26 502) — 다른 공개 서버를 쓴다. cityseer 정리 단계가
    # osmnx로 광장 면을 따로 받으므로 osmnx 쪽도 같은 서버로 맞춘다.
    import osmnx as ox

    # 정리 단계의 공원·광장 조회도 서버 상태 확인(status)에서 자주 멈춘다 — 확인을 끄고, 서버를 돌려 가며 쓴다.
    # 응답은 osmnx 캐시(data/osmnx_cache)에 남아 다시 돌릴 때는 받지 않는다.
    # 정리 단계의 공원·광장 면 조회는 건너뛴다(2026-10-26): 공개 서버가 자주 거절해 도시마다 정리 결과가 달라지고,
    # 이미 받은 길 원본만으로 10곳을 같은 조건에서 다시 돌릴 수 있게. 중복 길 합치기·작은 조각 정리는 그대로 한다.
    from osmnx._errors import InsufficientResponseError

    def _no_features(*_a, **_k):
        raise InsufficientResponseError("validation: parks/plazas skipped")

    ox.features_from_polygon = _no_features
    ox.settings.overpass_rate_limit = False
    ox.settings.requests_timeout = 400
    ox.settings.cache_folder = str(data / "osmnx_cache")
    G = None
    for base in OVERPASS_MIRRORS:
        ox.settings.overpass_url = base
        try:
            G = io.osm_graph_from_poly(poly, poly_crs_code=4326, to_crs_code=CRS, cache_path=data / f"osm_{town}.json",
                                       overpass_url=base + "/interpreter")
            break
        except Exception as exc:  # noqa: BLE001
            print("overpass 실패", base, type(exc).__name__, flush=True)
    if G is None:
        raise SystemExit(f"{town}: overpass 서버 모두 실패")
    # 도로 등급은 쪼개기 전 그래프에서 — 결과 표에는 남지 않는다
    ed = gpd.GeoDataFrame(
        {"hw": [max([HW_RANK.get(h, 2) for h in (d.get("highways") or [])], default=2) for _, _, d in G.edges(data=True)]},
        geometry=[d["geom"] for _, _, d in G.edges(data=True)], crs=CRS)
    G = graphs.nx_decompose(G, 50)
    nodes, edges, ns = io.network_structure_from_nx(G)
    nodes = nodes.rename_geometry("geometry")
    edges = edges.rename_geometry("geometry")
    nodes = networks.node_centrality_shortest(ns, nodes, distances=DISTANCES)
    # 각도(공간구문론의 선분 분석)는 dual 그래프 — 길 조각이 점이 된다. 결과는 가까운 primal 점에 옮긴다.
    G_dual = graphs.nx_to_dual(G)
    dnodes, _, dns = io.network_structure_from_nx(G_dual)
    dnodes = dnodes.rename_geometry("geometry")
    dnodes = networks.node_centrality_simplest(dns, dnodes, distances=DISTANCES)
    ang_cols = [c for c in dnodes.columns if c.startswith("cc_")]
    dpts = gpd.GeoDataFrame(dnodes[ang_cols].copy(), geometry=dnodes.geometry.centroid.values, crs=CRS)
    nodes = gpd.sjoin_nearest(nodes, dpts.rename(columns={c: c + "_ang" for c in ang_cols}), how="left",
                              max_distance=60).drop(columns="index_right")
    nodes = nodes[~nodes.index.duplicated()]

    pois = json.loads((data / f"pois_{town}.json").read_text(encoding="utf-8"))
    st = gpd.GeoDataFrame(
        {"cls": ["station"] * len(pois["stations"])},
        geometry=[Point(*to_m.transform(s["lon"], s["lat"])) for s in pois["stations"]], crs=CRS)
    if len(st):
        res = layers.compute_accessibilities(st, "cls", ["station"], nodes, ns, distances=[3000])
        nodes = res[0] if isinstance(res, tuple) else res
    apts = [Point(*to_m.transform(x, y)) for x, y in osm_apartments(data, town, lat, lng)]
    if apts:
        ap = gpd.GeoDataFrame({"cls": ["apt"] * len(apts)}, geometry=apts, crs=CRS)
        res = layers.compute_accessibilities(ap, "cls", ["apt"], nodes, ns, distances=[400, 800])
        nodes = res[0] if isinstance(res, tuple) else res

    # 블록: 길 선으로 둘러싸인 면
    lines = unary_union(list(edges.geometry))
    blocks = [p for p in polygonize(lines) if 800 <= p.area <= 300_000 and p.centroid.distance(centre) <= EVAL_M]
    bl = gpd.GeoDataFrame({"block": range(len(blocks))}, geometry=blocks, crs=CRS)
    bl["area"] = bl.area
    bl["compact"] = 4 * np.pi * bl.area / bl.length**2

    stores = gpd.GeoDataFrame(
        {"cat": [s["cat"] for s in pois["stores"]]},
        geometry=[Point(*to_m.transform(s["lon"], s["lat"])) for s in pois["stores"]], crs=CRS)
    hit = gpd.sjoin(stores, bl[["block", "geometry"]], how="left", predicate="within")
    miss = hit[hit["block"].isna()].drop(columns=["block", "index_right"])
    near = gpd.sjoin_nearest(miss, bl[["block", "geometry"]], how="left", max_distance=SIDE_M)
    assigned = pd.concat([hit["block"].dropna(), near["block"].dropna()])
    counts = assigned.astype(int).value_counts()
    bl["stores"] = bl["block"].map(counts).fillna(0).astype(int)
    food = stores.assign(block=None)
    bl["stores_food"] = 0
    fcat = hit.assign(block=hit["block"])
    food_counts = pd.concat([hit.loc[hit["cat"].isin(["FD6", "CE7"]), "block"].dropna(),
                             near.loc[near["cat"].isin(["FD6", "CE7"]), "block"].dropna()]).astype(int).value_counts()
    bl["stores_food"] = bl["block"].map(food_counts).fillna(0).astype(int)

    # 길 점 → 블록 둘레 30m 안
    metric_cols = [c for c in nodes.columns if c.startswith("cc_")]
    ring = bl.copy()
    ring["geometry"] = bl.boundary.buffer(SIDE_M)
    pts = gpd.sjoin(nodes[metric_cols + ["geometry"]], ring[["block", "geometry"]], predicate="within")
    agg_max = pts.groupby("block")[metric_cols].max().add_suffix("__max")
    agg_mean = pts.groupby("block")[metric_cols].mean().add_suffix("__mean")
    # 도로 등급: 둘레 30m 안 길의 가장 높은 등급, 닿은 길 수
    ej = gpd.sjoin(ed[["hw", "geometry"]], ring[["block", "geometry"]], predicate="intersects")
    hw = ej.groupby("block")["hw"].agg(["max", "count"]).rename(columns={"max": "hw_max", "count": "edges_touch"})
    out = bl.drop(columns="geometry").set_index("block").join([agg_max, agg_mean, hw])
    out["dist_centre"] = bl.set_index("block").centroid.distance(centre)
    # 계획도면에서도 얻을 수 있는 요인 (10-26 사용자 지시: 「신도시 고유 요소」)
    #   res_block   블록 안에 아파트 건물이 있나 → 도면의 A(공동주택)용지
    #   res_area_*  중심 400·800m 안 다른 주거 블록 넓이 합 → 도면의 주변 A용지 넓이
    #   station_m   가장 가까운 역까지 직선거리 → 도면에 사용자가 찍은 계획 역 (역 없는 세종은 5000)
    cen = bl.set_index("block").centroid
    if apts:
        ap_in = gpd.sjoin(gpd.GeoDataFrame(geometry=apts, crs=CRS), bl[["block", "geometry"]], predicate="within")
        res = set(ap_in["block"].astype(int))
    else:
        res = set()
    out["res_block"] = [1 if b in res else 0 for b in out.index]
    rb = out.index[out["res_block"] == 1]
    rx, ry = cen.loc[rb].x.values, cen.loc[rb].y.values
    ra = out.loc[rb, "area"].values
    for r in (400, 800):
        vals = []
        for b in out.index:
            d = np.hypot(rx - cen[b].x, ry - cen[b].y)
            vals.append(float(ra[(d <= r) & (rb != b)].sum()))
        out[f"res_area_{r}"] = vals
    out["station_m"] = [min((p.distance(cen[b]) for p in st.geometry), default=5000.0) for b in out.index]
    out["apt_points"] = len(apts)
    bl.set_index("block")[["geometry", "stores"]].join(out[["res_block"]]).to_file(data / f"blocks_{town}.geojson", driver="GeoJSON")
    out["town"] = town
    out.to_csv(data / f"blocks_{town}.csv", encoding="utf-8")
    print(town, "블록", len(out), "가게 있는 블록", int((out["stores"] > 0).sum()),
          "블록에 붙은 가게", int(out["stores"].sum()), "/", len(stores), "길 점", len(nodes), flush=True)


if __name__ == "__main__":
    main()
