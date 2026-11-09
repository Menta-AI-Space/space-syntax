"""엔진 대조 — cityseer로 길 구조값(NACH)을 다시 계산해 지도에 쓰는 값과 맞춰 본다.

**무엇을 묻나** — 「지도 분석」 탭은 ``road_segment_structure.choice_raw``(depthmapX로 계산한
NACH, 800m 타일 + 500m 헤일로 ≈ 1.8km 창 안의 radius-n)를 서울 전역 백분위로 칠한다.
이 도구는 **계산 자체가 엔진을 바꿔도 재현되는가**만 본다. 「실제 장사가 잘 되나」는
다른 질문이다(상권 매출 검증은 ``SPEC_SPACE_ANALYSIS_MODULES.md``).

**같게 맞춘 것** — 길망 읽기(``compute_global_segment_structure.fetch_all_roads``)와
위상(``graph_builder.build_graph`` → 보행 간선 → 가장 큰 연결요소)을 depthmapX 경로와
똑같이 쓰고, 그 원자 간선을 cityseer 쌍대(dual) 그래프로 옮긴다. 지표 식도 같다:
``NACH = log(choice+1) / log(TD+3)`` — TD는 각도 깊이 합(90° = 1, depthmapX 단위),
choice는 각도 최단경로 betweenness(쌍마다 한 번). 원자 간선 값을 길이 가중 평균으로
``roads_clean`` 행에 모으는 것도 ``depthmapx_bridge``와 같다.

**다른 것** — cityseer는 metric 반경(500·1000·2000m)이고, ``choice_raw``는 1.8km 창의
radius-n이라 어느 반경과도 정의가 같지 않다. 정의가 같은 짝은 **같은 반경의
depthmapX metric 결과**(``compute_global_segment_structure``로 만든 CSV, ``--dmx-csv``)다 —
그 대조가 순수한 엔진 대조이고, ``choice_raw``와의 대조는 「창 정의까지 다른」 대조다.

**DB에 쓰지 않는다** — 운영 DB는 읽기만 한다(CLAUDE.md §1). 결과는 CSV.

Run (마포구 = 11140, sgis 체계):
  cd backend && .venv/Scripts/python -m tools.validation.cityseer_vs_depthmapx \
      --sgg 11140 --radii 500,1000,2000 \
      --dmx-csv C:/dev/tools/scratch/mapo.csv \
      --out C:/dev/tools/scratch/cityseer_mapo.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import sys
import time

import networkx as nx
import numpy as np
import pandas as pd
import psycopg2.extras
from pyproj import Transformer
from scipy.stats import spearmanr
from shapely import wkb
import shapely
from shapely.geometry import LineString

from app.services.depthmapx_engine import _largest_component_edges
from app.services.graph_builder import build_graph
from app.services.spatial_query import RoadSegment, get_connection
from tools.compute_global_segment_structure import fetch_all_roads, seg_key

logger = logging.getLogger(__name__)

# 「지도 분석」 탭이 칠하는 서울 전역 경계값 (2026-10-26)
SEOUL_TOP10_NACH = 1.2394
SEOUL_MEDIAN_NACH = 0.8698


def cityseer_nach(graph: nx.Graph, n_sources: int, radii: list[int]) -> dict[str, dict[int, float]]:
    """build_graph 위상 → cityseer 쌍대 그래프 → 원자 간선 NACH → roads_clean 행 길이 가중 평균."""
    from cityseer.metrics import networks
    from cityseer.tools import graphs, io

    walk_edges = _largest_component_edges(graph, "walkable")
    to_5179 = Transformer.from_crs(4326, 5179, always_xy=True)

    primal = nx.MultiGraph()
    primal.graph["crs"] = 5179
    names: dict[tuple, str] = {}
    atoms: dict[tuple[str, str], dict] = {}
    for u, v, data in graph.edges(data=True):
        if tuple(sorted((u, v))) not in walk_edges:
            continue
        ends = []
        for node in (u, v):
            if node not in names:
                names[node] = f"n{len(names)}"
                x, y = to_5179.transform(*node)
                primal.add_node(names[node], x=x, y=y)
            ends.append(names[node])
        a, b = ends
        line = LineString([(primal.nodes[a]["x"], primal.nodes[a]["y"]),
                           (primal.nodes[b]["x"], primal.nodes[b]["y"])])
        primal.add_edge(a, b, geom=line)
        atoms[(a, b)] = {
            "length": max(float(data.get("length", 0.0)), 1e-9),
            "sources": list(data.get("segment_indices", [])),
        }
    logger.info("cityseer 원시 그래프 노드 %s · 간선 %s",
                f"{primal.number_of_nodes():,}", f"{primal.number_of_edges():,}")

    dual = graphs.nx_to_dual(primal)
    nodes_gdf, _, structure = io.network_structure_from_nx(dual)
    t0 = time.perf_counter()
    nodes_gdf = networks.centrality_simplest(
        structure, nodes_gdf, distances=radii,
        closeness={"density": "1", "td": "c / 90"},
        postprocess={},
    )
    logger.info("cityseer 각도 중심성 %s — %.1f초", radii, time.perf_counter() - t0)

    out: dict[str, dict[int, float]] = {}
    for r in radii:
        bet = nodes_gdf[f"cc_betweenness_{r}_ang"].to_numpy(dtype=float)
        td = nodes_gdf[f"cc_td_{r}_ang"].to_numpy(dtype=float)
        nach = np.where(td > 0, np.log(bet + 1.0) / np.log(td + 3.0), 0.0)
        acc = np.zeros(n_sources)
        wts = np.zeros(n_sources)
        for value, a, b in zip(nach, nodes_gdf["primal_edge_node_a"], nodes_gdf["primal_edge_node_b"]):
            atom = atoms.get((a, b)) or atoms.get((b, a))
            if atom is None:
                continue
            for src in atom["sources"]:
                acc[src] += value * atom["length"]
                wts[src] += atom["length"]
        out[f"cs_nach_r{r}"] = {i: acc[i] / wts[i] for i in range(n_sources) if wts[i] > 0}
    return out


# 구 경계에서 길망을 자르면 경계 근처 구간의 반경이 잘린다. --buffer-m 만큼 바깥 길까지
# 그래프에 넣고, 비교는 구에 걸친 구간만 한다(in_target).
_BUFFERED_SQL = """
WITH area AS (
  SELECT ST_Union(geom) AS g FROM sgis_dong_boundary WHERE left(adm_cd, 5) = ANY(%s)
)
SELECT osm_id, highway, length_m, walkable, drivable, layer, bridge, tunnel,
       ST_AsGeoJSON(r.geom)::json AS geom,
       ST_Intersects(r.geom_5179, area.g) AS in_target
FROM roads_clean r, area
WHERE (walkable OR drivable) AND ST_DWithin(r.geom_5179, area.g, %s)
"""


def fetch_buffered_roads(codes: list[str], buffer_m: int) -> tuple[list[RoadSegment], list[bool]]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_BUFFERED_SQL, (codes, buffer_m))
            rows = cur.fetchall()
    segments, in_target = [], []
    for row in rows:
        geom = row["geom"]
        coords = geom.get("coordinates", []) if geom.get("type") == "LineString" else []
        if len(coords) < 2:
            continue
        segments.append(RoadSegment(
            osm_id=int(row["osm_id"]), highway=str(row["highway"]),
            length_m=float(row["length_m"] or 0), coordinates=coords,
            walkable=bool(row["walkable"]), drivable=bool(row["drivable"]),
            layer=int(row["layer"] or 0), bridge=bool(row["bridge"]), tunnel=bool(row["tunnel"]),
        ))
        in_target.append(bool(row["in_target"]))
    return segments, in_target


def fetch_reference(keys: list[str]) -> dict[str, float]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT seg_key, choice_raw FROM road_segment_structure WHERE seg_key = ANY(%s)",
            (keys,),
        )
        return {k: float(v) for k, v in cur.fetchall() if v is not None}


def fetch_boundary(codes: list[str]):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ST_AsBinary(ST_Boundary(ST_Union(geom))) FROM sgis_dong_boundary"
            " WHERE left(adm_cd, 5) = ANY(%s)",
            (codes,),
        )
        row = cur.fetchone()
    return wkb.loads(bytes(row[0])) if row and row[0] else None


def fetch_districts(codes: list[str]) -> tuple[list[str], list]:
    """구 코드와 구 면(5179). 구간 중점이 어느 구에 드는지 붙여 구별로 나눠 보려고 쓴다."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT left(adm_cd, 5), ST_AsBinary(ST_Union(geom)) FROM sgis_dong_boundary"
            " WHERE left(adm_cd, 5) = ANY(%s) GROUP BY 1 ORDER BY 1",
            (codes,),
        )
        rows = cur.fetchall()
    return [r[0] for r in rows], [wkb.loads(bytes(r[1])) for r in rows]


def seoul_codes() -> list[str]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT left(adm_cd, 5) FROM sgis_dong_boundary WHERE adm_cd LIKE '11%%' ORDER BY 1")
        return [r[0] for r in cur.fetchall()]


def overlap(a: pd.Series, b: pd.Series, top: float) -> float:
    """둘 다 같은 몫(top)만큼 뽑았을 때 겹치는 비율 % — 순위 기준이라 엔진 간 척도 차이를 탄다."""
    k = max(1, int(round(len(a) * top)))
    sa = set(a.nlargest(k).index)
    sb = set(b.nlargest(k).index)
    return 100.0 * len(sa & sb) / k


def bottom_overlap(a: pd.Series, b: pd.Series, share: float = 0.5) -> float:
    k = max(1, int(round(len(a) * share)))
    sa = set(a.nsmallest(k).index)
    sb = set(b.nsmallest(k).index)
    return 100.0 * len(sa & sb) / k


def compare(df: pd.DataFrame, ref: str, cols: list[str], label: str) -> list[dict]:
    rows = []
    for col in cols:
        sub = df[[ref, col]].dropna()
        if len(sub) < 30:
            continue
        rho = spearmanr(sub[ref], sub[col]).statistic
        rows.append({
            "subset": label, "reference": ref, "cityseer": col, "n": len(sub),
            "spearman": round(float(rho), 3),
            "top10_overlap_pct": round(overlap(sub[ref], sub[col], 0.10), 1),
            "bottom50_overlap_pct": round(bottom_overlap(sub[ref], sub[col]), 1),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sgg", default="11140", help="자치구 코드(sgis 체계), 쉼표로 복수. 'all'이면 서울 전역(구별 요약도 낸다)")
    parser.add_argument("--radii", default="500,1000,2000")
    parser.add_argument("--dmx-csv", default=None,
                        help="같은 구의 depthmapX metric 반경 CSV (compute_global_segment_structure 출력)")
    parser.add_argument("--buffer-m", type=int, default=0,
                        help="구 바깥으로 그래프에 더 넣을 거리(m). 0이면 구 경계에서 자른다(depthmapX CSV와 같은 조건)")
    parser.add_argument("--order", default="db",
                        help="길 입력 순서: db(조회 순서 그대로, 기본 — 운영 배치와 같은 조건이지만 실행마다 조금 다르다) | osm(osm_id·시작점) | key(seg_key 해시) | shuffle:SEED")
    parser.add_argument("--out", required=True, help="세그먼트별 값 CSV. 요약은 *_summary.csv")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("cityseer").setLevel(logging.WARNING)
    radii = [int(r) for r in args.radii.split(",") if r.strip()]
    seoul = args.sgg.strip().lower() == "all"
    codes = seoul_codes() if seoul else [c.strip() for c in args.sgg.split(",") if c.strip()]

    t0 = time.perf_counter()
    if args.buffer_m > 0:
        segments, in_target = fetch_buffered_roads(codes, args.buffer_m)
    else:
        segments = fetch_all_roads(None if seoul else args.sgg)
        in_target = [True] * len(segments)
    # roads_clean 조회에 ORDER BY가 없어 행 순서가 실행마다 바뀌고, build_graph의 접점 정리가 그 순서를
    # 타서 같은 길망에서도 원자 간선 수가 달라진다(마포 15,623~15,641). 순서를 고정하면 재현되지만,
    # 고정 순서는 운영 값과 덜 닮는다(마포 500m ρ 0.80 vs DB 순서 0.85) — 기본은 운영과 같은 db.
    if args.order == "db":
        order = list(range(len(segments)))
    elif args.order == "osm":
        order = sorted(range(len(segments)), key=lambda i: (segments[i].osm_id, segments[i].coordinates[0]))
    elif args.order.startswith("shuffle:"):
        order = list(np.random.default_rng(int(args.order.split(":", 1)[1])).permutation(len(segments)))
    else:
        order = sorted(range(len(segments)), key=lambda i: seg_key(segments[i].osm_id, segments[i].coordinates))
    segments = [segments[i] for i in order]
    in_target = [in_target[i] for i in order]
    graph, _ = build_graph(segments)
    logger.info("도로 %s개 · 그래프 %.1f초", f"{len(segments):,}", time.perf_counter() - t0)

    metrics = cityseer_nach(graph, len(segments), radii)

    target_idx = [i for i, flag in enumerate(in_target) if flag]
    keys = [seg_key(s.osm_id, s.coordinates) for s in segments]
    ref = fetch_reference([keys[i] for i in target_idx])
    boundary = fetch_boundary(codes)
    district_codes, district_polys = fetch_districts(codes)
    district_tree = shapely.STRtree(district_polys)
    to_5179 = Transformer.from_crs(4326, 5179, always_xy=True)
    mids = [segments[i].coordinates[len(segments[i].coordinates) // 2] for i in target_idx]
    xs, ys = to_5179.transform([m[0] for m in mids], [m[1] for m in mids])
    points = shapely.points(xs, ys)
    dists = shapely.distance(boundary, points) if boundary is not None else np.full(len(points), math.nan)
    pt_idx, poly_idx = district_tree.query(points, predicate="within")
    district_of = dict(zip(pt_idx.tolist(), (district_codes[j] for j in poly_idx.tolist())))

    records = []
    for n, i in enumerate(target_idx):
        s = segments[i]
        rec = {"seg_key": keys[i], "osm_id": s.osm_id, "highway": s.highway,
               "length_m": round(s.length_m, 2), "edge_dist_m": round(float(dists[n]), 1),
               "sgg": district_of.get(n), "choice_raw": ref.get(keys[i])}
        for name, values in metrics.items():
            v = values.get(i)
            rec[name] = round(v, 6) if v is not None else None
        records.append(rec)
    df = pd.DataFrame.from_records(records).set_index("seg_key")
    df = df[~df.index.duplicated()]

    cs_cols = list(metrics)
    if args.dmx_csv:
        dmx = pd.read_csv(args.dmx_csv).set_index("seg_key")
        dmx = dmx[~dmx.index.duplicated()]
        for col in [c for c in dmx.columns if c.startswith("nach_r")]:
            df[f"dmx_{col}"] = dmx[col]

    df.to_csv(args.out, encoding="utf-8")
    logger.info("세그먼트 %s행 기록 — %s (choice_raw 조인 %s)",
                f"{len(df):,}", args.out, f"{df['choice_raw'].notna().sum():,}")

    # 값이 없는 행(보행 최대 연결요소 밖)만 뺀다. 막다른 길의 NACH 0은 지도에도 칠해지므로 남긴다.
    live = df[df[cs_cols].notna().all(axis=1)]
    summary: list[dict] = compare(live, "choice_raw", cs_cols, "all")
    for r in radii:
        col = f"dmx_nach_r{r}"
        if col in live:
            summary += compare(live, col, [f"cs_nach_r{r}"], "all")
    # 구 경계에서 그 반경 이상 떨어진 구간만 — 길망을 잘라서 생기는 차이를 걷어낸다.
    for r in radii:
        inner = live[live["edge_dist_m"] >= r]
        label = f"interior>={r}m"
        summary += compare(inner, "choice_raw", [f"cs_nach_r{r}"], label)
        if f"dmx_nach_r{r}" in inner:
            summary += compare(inner, f"dmx_nach_r{r}", [f"cs_nach_r{r}"], label)

    # 화면이 실제로 쓰는 선: choice_raw ≥ 서울 상위 10% 경계(1.2394). 같은 개수만큼 cityseer 상위를 뽑아 겹침.
    painted = live[live["choice_raw"] >= SEOUL_TOP10_NACH].index
    for col in cs_cols:
        top = set(live[col].nlargest(len(painted)).index)
        summary.append({
            "subset": "all", "reference": "choice_raw>=seoul_top10(1.2394)", "cityseer": col,
            "n": len(painted), "spearman": None,
            "top10_overlap_pct": round(100.0 * len(top & set(painted)) / max(len(painted), 1), 1),
            "bottom50_overlap_pct": None,
        })

    # 구별 — 어느 동네에서 두 엔진/정의가 갈리는지 본다.
    if len(district_codes) > 1:
        for code in district_codes:
            summary += compare(live[live["sgg"] == code], "choice_raw", cs_cols, f"sgg={code}")

    out_summary = args.out.replace(".csv", "_summary.csv")
    with open(out_summary, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    print(pd.DataFrame(summary).to_string(index=False))
    logger.info("요약 기록 — %s", out_summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
