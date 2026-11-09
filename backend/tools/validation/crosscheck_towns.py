"""신도시 길 점수 대조 2단계 — 신도시 10곳에서 cityseer(각도) ↔ 앱 depthmapX 엔진이 길 순서를 얼마나 같게 매기나 (cityseer 가상환경).

  C:\\dev\\tools\\cityseer-venv\\Scripts\\python.exe crosscheck_towns.py <data_dir> <out_json>

운정 한 곳 대조(`crosscheck_cityseer_depthmapx.py`, 10-26)를 10곳으로 넓힌 것. 식은 같다: cityseer는 OSM을 cityseer 방식으로
정리해 50m로 쪼갠 dual 그래프의 simplest(각도) harmonic(통합)·betweenness(선택), depthmapX 쪽은 1단계
`crosscheck_towns_dmx.py`가 만든 `dmx_<town>.json`. 조각 가운데점 ↔ 가장 가까운 cityseer 점(15m 안)을 짝지어
순위상관(Spearman)과 「상위 10% 길이 얼마나 겹치나」를 잰다.
다른 점: 길 원본 창이 중심 3.5km라(운정 대조는 4.5km) 반경 2km 값이 창 가장자리에 덜 잘리게 **중심 1.5km 안** 길만 견준다.
"""
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
from cityseer.metrics import networks
from cityseer.tools import graphs, io
from scipy.stats import spearmanr
from shapely.geometry import LineString, Point

logging.disable(logging.WARNING)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cityseer_town import CRS, WINDOW_M  # noqa: E402
from towns import TOWNS  # noqa: E402

EVAL_M = 1500
PAIRS = (("i1000", "cc_harmonic_1000"), ("i2000", "cc_harmonic_2000"),
         ("c1000", "cc_betweenness_1000"), ("c2000", "cc_betweenness_2000"))


def cityseer_dual(town: str, data: Path) -> gpd.GeoDataFrame:
    import osmnx as ox
    from osmnx._errors import InsufficientResponseError

    def _no_features(*_a, **_k):  # cityseer_town과 같게 — 공원·광장 조회 건너뜀
        raise InsufficientResponseError("validation: parks/plazas skipped")

    ox.features_from_polygon = _no_features
    ox.settings.cache_folder = str(data / "osmnx_cache")
    lat, lng = TOWNS[town]
    poly, _ = io.buffered_point_poly(lng, lat, WINDOW_M)
    G = io.osm_graph_from_poly(poly, poly_crs_code=4326, to_crs_code=CRS, cache_path=data / f"osm_{town}.json")
    G = graphs.nx_decompose(G, 50)
    dn, _, dns = io.network_structure_from_nx(graphs.nx_to_dual(G))
    dn = dn.rename_geometry("geometry")
    dn = networks.node_centrality_simplest(dns, dn, distances=[1000, 2000])
    dn["geometry"] = dn.geometry.centroid
    return dn


def compare(town: str, data: Path) -> dict:
    lat, lng = TOWNS[town]
    centre = gpd.GeoSeries([Point(lng, lat)], crs=4326).to_crs(CRS).iloc[0]
    d = json.loads((data / f"dmx_{town}.json").read_text(encoding="utf-8"))
    lines = gpd.GeoSeries([LineString(c) for c in d["coords"]], crs=4326).to_crs(CRS)
    seg = gpd.GeoDataFrame({k: d[k] for k, _ in PAIRS}, geometry=lines.interpolate(0.5, normalized=True), crs=CRS)
    seg = seg[(seg.distance(centre) <= EVAL_M) & (seg["i1000"] > 0)]
    dn = cityseer_dual(town, data)
    j = gpd.sjoin_nearest(seg, dn, how="inner", max_distance=15, distance_col="dist")
    j = j[~j.index.duplicated()]
    out = {"segments_in_1500m": int(len(seg)), "pairs": int(len(j))}
    for ours, theirs in PAIRS:
        col = next(c for c in j.columns if c.startswith(theirs) and "beta" not in c)
        a, b = j[ours].astype(float), j[col].astype(float)
        ok = a.notna() & b.notna()
        n = int(ok.sum() * 0.1)
        top_a, top_b = set(a[ok].nlargest(n).index), set(b[ok].nlargest(n).index)
        out[ours] = {"rho": round(float(spearmanr(a[ok], b[ok]).correlation), 3),
                     "top10_overlap": round(len(top_a & top_b) / max(1, n), 3)}
    return out


def main() -> None:
    data, out_json = Path(sys.argv[1]), Path(sys.argv[2])
    towns = [t for t in TOWNS if (data / f"dmx_{t}.json").exists()]
    res = {"date": "2026-10-27", "eval_radius_m": EVAL_M, "window_m": WINDOW_M, "towns": {}}
    for t in towns:
        res["towns"][t] = compare(t, data)
        print(t, json.dumps(res["towns"][t], ensure_ascii=False), flush=True)
    summary = {}
    for k, _ in PAIRS:
        for m in ("rho", "top10_overlap"):
            v = {t: r[k][m] for t, r in res["towns"].items()}
            summary[f"{k}_{m}"] = {"mean": round(float(np.mean(list(v.values()))), 3),
                                   "min": [min(v, key=v.get), min(v.values())],
                                   "max": [max(v, key=v.get), max(v.values())]}
    res["summary"] = summary
    out_json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
