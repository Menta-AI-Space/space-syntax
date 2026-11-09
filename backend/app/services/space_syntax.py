import logging
import math

import networkx as nx

from app.config import settings
from app.services.depthmapx_engine import compute_depthmapx_metrics
from app.services.graph_builder import build_angular_graph
from app.services.spatial_query import RoadSegment

logger = logging.getLogger(__name__)

# Result keys per mode.
WALK = "walk"
DRIVE = "drive"


METRICS = ("choice", "integration", "mean_depth", "connectivity")


def extend_unique_warnings(target: list[str], more: list[str]) -> None:
    """Append warnings without duplicating identical messages."""
    seen = set(target)
    for warning in more:
        if warning not in seen:
            target.append(warning)
            seen.add(warning)


def _empty_mode(n: int) -> dict[str, dict[int, float]]:
    return {m: {i: 0.0 for i in range(n)} for m in METRICS}


def _prepare_angular_main(
    G: nx.Graph,
    attr: str,
    label: str,
    warnings: list[str],
    *,
    warn_about_fragments: bool = True,
) -> nx.Graph | None:
    """Build the largest connected component of the segment-angular dual graph."""
    angular = build_angular_graph(G, attr)
    if angular.number_of_nodes() < 2:
        return None

    components = list(nx.connected_components(angular))
    largest = max(components, key=len)
    excluded_count = angular.number_of_nodes() - len(largest)
    largest_excluded = max((len(c) for c in components if c is not largest), default=0)
    if warn_about_fragments and (
        excluded_count / angular.number_of_nodes() > 0.02 or largest_excluded >= 10
    ):
        warnings.append(
            f"{label} 도로망이 {len(components)}개로 끊겨 있어 "
            f"떨어진 구간 {excluded_count}개를 분석에서 제외했습니다."
        )

    G_main = angular.subgraph(largest).copy()
    if G_main.number_of_nodes() > 3000:
        warnings.append(
            f"{label} 도로망이 커서({G_main.number_of_nodes():,}개 구간) "
            "분석에 시간이 걸릴 수 있습니다."
        )

    for _, _, data in G_main.edges(data=True):
        data["path_cost"] = max(float(data["angular_cost"]), 1e-9)
    return G_main


def _accumulate_segment_metrics(
    G_main: nx.Graph,
    *,
    include_choice: bool,
    raw_choice: dict | None,
    distances: dict,
) -> dict[int, dict[str, float]]:
    """Length-weight dual-node metrics onto source road segments."""
    weighted: dict[int, dict[str, float]] = {}
    for node, data in G_main.nodes(data=True):
        reachable = distances.get(node, {})
        total_depth = sum(d for target, d in reachable.items() if target != node)
        node_count = max(len(reachable) - 1, 0)
        mean_depth = (total_depth / node_count) if node_count > 0 else 0.0
        connectivity = float(G_main.degree(node))
        nach = 0.0
        nain = 0.0
        if include_choice and raw_choice is not None:
            nach = (
                math.log(raw_choice.get(node, 0.0) + 1.0) / math.log(total_depth + 3.0)
                if total_depth > 0
                else 0.0
            )
            nain = (node_count ** 1.2 / total_depth) if total_depth > 0 else 0.0
        length = max(float(data.get("length", 0.0)), 1e-9)
        for seg_idx in data.get("segment_indices", []):
            bucket = weighted.setdefault(
                seg_idx,
                {
                    "length": 0.0,
                    "choice": 0.0,
                    "integration": 0.0,
                    "mean_depth": 0.0,
                    "connectivity": 0.0,
                },
            )
            bucket["length"] += length
            if include_choice:
                bucket["choice"] += nach * length
                bucket["integration"] += nain * length
            bucket["mean_depth"] += mean_depth * length
            bucket["connectivity"] += connectivity * length
    return weighted


def _finalize_segment_metrics(
    weighted: dict[int, dict[str, float]], n_segments: int
) -> dict[str, dict[int, float]]:
    result = _empty_mode(n_segments)
    for seg_idx, values in weighted.items():
        length = values["length"]
        if length <= 0:
            continue
        for metric in METRICS:
            result[metric][seg_idx] = values[metric] / length
    return result


def _compute_mode(
    G: nx.Graph,
    attr: str,
    n_segments: int,
    label: str,
    warnings: list[str],
    *,
    warn_about_fragments: bool = True,
) -> dict[str, dict[int, float]]:
    """Compute segment-angular Choice (NACH), Integration (NAIN), mean depth, connectivity."""
    result = _empty_mode(n_segments)
    G_main = _prepare_angular_main(
        G, attr, label, warnings, warn_about_fragments=warn_about_fragments
    )
    if G_main is None:
        return result

    raw_choice = nx.betweenness_centrality(
        G_main, weight="path_cost", normalized=False, endpoints=False
    )
    distances = dict(nx.all_pairs_dijkstra_path_length(G_main, weight="path_cost"))
    weighted = _accumulate_segment_metrics(
        G_main, include_choice=True, raw_choice=raw_choice, distances=distances
    )
    return _finalize_segment_metrics(weighted, n_segments)


def compute_depth_connectivity(
    G: nx.Graph,
    segments: list[RoadSegment],
    modes: tuple[str, ...] = (WALK, DRIVE),
    *,
    warn_about_fragments: bool = True,
) -> tuple[dict[str, dict[str, dict[int, float]]], list[str]]:
    """Mean depth and connectivity only — used to supplement depthmapX choice/integration."""
    warnings: list[str] = []
    n = len(segments)
    empty = {m: {i: 0.0 for i in range(n)} for m in ("mean_depth", "connectivity")}
    if G.number_of_edges() == 0:
        return {m: dict(empty) for m in modes}, ["No graph edges to analyze"]

    result: dict[str, dict[str, dict[int, float]]] = {}
    for mode in modes:
        attr = "walkable" if mode == WALK else "drivable"
        label = "Walkable" if mode == WALK else "Drivable"
        G_main = _prepare_angular_main(
            G, attr, label, warnings, warn_about_fragments=warn_about_fragments
        )
        if G_main is None:
            result[mode] = dict(empty)
            continue
        distances = dict(nx.all_pairs_dijkstra_path_length(G_main, weight="path_cost"))
        weighted = _accumulate_segment_metrics(
            G_main, include_choice=False, raw_choice=None, distances=distances
        )
        finalized = _finalize_segment_metrics(weighted, n)
        result[mode] = {
            "mean_depth": finalized["mean_depth"],
            "connectivity": finalized["connectivity"],
        }
    return result, warnings


def compute_metrics(
    G: nx.Graph,
    segments: list[RoadSegment],
    modes: tuple[str, ...] = (WALK, DRIVE),
    *,
    warn_about_fragments: bool = True,
) -> tuple[dict[str, dict[str, dict[int, float]]], list[str]]:
    """
    Compute Choice and Integration separately for the walkable and drivable
    subgraphs of ``G``.

    Each mode requires its own all-pairs shortest-path pass over the angular
    dual graph, so computing a mode the caller won't use (e.g. drive metrics
    for a walk-only feature) roughly doubles runtime for nothing. Pass
    ``modes=(WALK,)`` to skip drive entirely.

    Returns a mapping ``{"walk": {"choice": ..., "integration": ..., "mean_depth":
    ..., "connectivity": ...}, "drive": {...}}`` (only requested modes) alongside
    any warnings. All metrics use the segment-angular dual graph (depthmapX-style).
    """
    warnings: list[str] = []
    n = len(segments)

    if G.number_of_edges() == 0:
        return {m: _empty_mode(n) for m in modes}, ["No graph edges to analyze"]

    result: dict[str, dict[str, dict[int, float]]] = {}
    if WALK in modes:
        result[WALK] = _compute_mode(
            G, "walkable", n, "Walkable", warnings, warn_about_fragments=warn_about_fragments
        )
    if DRIVE in modes:
        result[DRIVE] = _compute_mode(
            G, "drivable", n, "Drivable", warnings, warn_about_fragments=warn_about_fragments
        )

    return result, warnings


def compute_metrics_preferred(
    G: nx.Graph,
    segments: list[RoadSegment],
    modes: tuple[str, ...] = (WALK,),
) -> tuple[dict[str, dict[str, dict[int, float]]], str]:
    """구조 지표 + 실제로 쓴 엔진 이름. depthmapX(C++) 우선, NetworkX 폴백.

    **배치 도구용이다.** 라우터는 각자 폴백을 다루는데(``analyze.py``는 strict
    모드에서 503을 내고, ``site_score.py``는 경고를 모은다) 배치는 그럴 필요가
    없어 여기로 묶었다.

    엔진 선택이 배치에서 중요한 이유: ``compute_metrics``는 그래프마다 전지점쌍
    최단경로를 두 번(betweenness + all_pairs_dijkstra) 돌려 세그먼트 수에
    초선형으로 느려진다. 마포구 실측에서 타일당 중위 **126초**가 나와 서울 전체가
    60시간대였고, depthmapX로 바꾸니 8.0초/타일(24분)이 됐다.

    ⚠️ 두 엔진의 결과가 완전히 같지는 않다 — 실측 순위상관이 Integration 0.98,
    Choice 0.88이다. 한 배치 안에서 엔진이 섞이면 타일 간 백분위에 계통차가
    생기므로, 호출자는 폴백이 몇 번 일어났는지 세어 로그에 남길 것.
    """
    if settings.depthmapx_enabled:
        metrics, error = compute_depthmapx_metrics(G, segments, modes=modes)
        if metrics is not None:
            return metrics, "depthmapx"
        logger.warning("depthmapX 실패 — NetworkX로 물러난다: %s", error)
    metrics, _warnings = compute_metrics(
        G, segments, modes=modes, warn_about_fragments=False
    )
    return metrics, "networkx"
