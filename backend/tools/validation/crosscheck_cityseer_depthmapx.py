"""교차 검증: 같은 운정에서 cityseer(각도, 반경 1·2km)와 F10 조사의 depthmapX(반경 1·2km)가
길 순서를 얼마나 같게 매기나. 두 엔진·두 망(cityseer OSM 정리 vs F10 정리)이 달라도 순서가
비슷하면 「계산 자체」는 믿을 수 있다는 뜻이다.

  C:\\dev\\tools\\cityseer-venv\\Scripts\\python.exe crosscheck_cityseer_depthmapx.py <data_dir>
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
data = Path(sys.argv[1])
F10 = Path(r"C:\dev\tools\scratch\unjeong-f10\work\segments_scored.json")
LAT, LNG = 37.7161461, 126.7282857  # F10 조사 중심(운정중앙역)

poly, _ = io.buffered_point_poly(LNG, LAT, 4500)
G = io.osm_graph_from_poly(poly, poly_crs_code=4326, to_crs_code=5179, cache_path=data / "osm_운정_f10.json")
G = graphs.nx_decompose(G, 50)
Gd = graphs.nx_to_dual(G)
dn, _, dns = io.network_structure_from_nx(Gd)
dn = dn.rename_geometry("geometry")
dn = networks.node_centrality_simplest(dns, dn, distances=[1000, 2000])
dn["geometry"] = dn.geometry.centroid

d = json.loads(F10.read_text(encoding="utf-8"))["current"]
mid = [LineString(l).interpolate(0.5, normalized=True) for l in d["lines"]]
seg = gpd.GeoDataFrame({"i1000": d["integration_r1000"], "i2000": d["integration_r2000"],
                        "c2000": d["choice_r2000"]}, geometry=mid, crs=5179)
centre = gpd.GeoSeries([Point(LNG, LAT)], crs=4326).to_crs(5179).iloc[0]
seg = seg[seg.distance(centre) <= 2500]
j = gpd.sjoin_nearest(seg, dn, how="inner", max_distance=15, distance_col="dist")
out = {"pairs": len(j)}
for ours, theirs in (("i1000", "cc_harmonic_1000"), ("i2000", "cc_harmonic_2000"), ("c2000", "cc_betweenness_2000")):
    col = next(c for c in j.columns if c.startswith(theirs) and "beta" not in c)
    a, b = j[ours].astype(float), j[col].astype(float)
    ok = a.notna() & b.notna()
    rho = spearmanr(a[ok], b[ok]).correlation
    top_a = set(a[ok].nlargest(int(ok.sum() * 0.1)).index)
    top_b = set(b[ok].nlargest(int(ok.sum() * 0.1)).index)
    out[f"{ours} vs {col}"] = {"rho": round(float(rho), 3), "top10_overlap": round(len(top_a & top_b) / max(1, len(top_a)), 3)}
print(json.dumps(out, ensure_ascii=False, indent=1))
(data / "crosscheck.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
