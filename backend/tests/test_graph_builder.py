"""Unit tests for graph building and space syntax metrics (no DB required)."""

import networkx as nx
import csv

from app.services.graph_builder import (
    build_angular_graph,
    build_graph,
    get_largest_component,
    mode_subgraph,
)
from app.services.engine_comparison import compare_engines
from app.services.depthmapx_engine import _aggregate, _payload
from app.services.space_syntax import DRIVE, WALK, compute_metrics
from app.services.spatial_query import RoadSegment
from app.services.stats import build_geojson
from tools.depthmapx_bridge import find_column, find_exact_column, write_input


def _seg(osm_id, highway, coords, length_m=100.0, walkable=True, drivable=True, **kwargs):
    return RoadSegment(
        osm_id=osm_id,
        highway=highway,
        length_m=length_m,
        coordinates=coords,
        walkable=walkable,
        drivable=drivable,
        **kwargs,
    )


def test_build_graph_and_metrics():
    segments = [
        _seg(1, "primary", [[126.978, 37.5665], [126.979, 37.5665]], 111.0),
        _seg(2, "secondary", [[126.979, 37.5665], [126.979, 37.5675]], 111.0),
        _seg(3, "residential", [[126.978, 37.5665], [126.978, 37.5675]], 111.0),
    ]
    G, _ = build_graph(segments)
    assert G.number_of_edges() == 3
    assert G.number_of_nodes() == 4

    metrics, warnings = compute_metrics(G, segments)
    assert set(metrics.keys()) == {WALK, DRIVE}
    for mode in (WALK, DRIVE):
        assert len(metrics[mode]["choice"]) == 3
        assert len(metrics[mode]["integration"]) == 3
        assert all(v >= 0 for v in metrics[mode]["choice"].values())
        assert all(v >= 0 for v in metrics[mode]["integration"].values())
    assert max(metrics[WALK]["choice"].values()) > 0
    assert max(metrics[DRIVE]["choice"].values()) > 0

    geojson = build_geojson(segments, metrics)
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 3
    props = geojson["features"][0]["properties"]
    assert {
        "choice_walk", "integration_walk", "choice_drive", "integration_drive",
        "walkable", "drivable", "layer", "bridge", "tunnel",
    }.issubset(props.keys())
    assert geojson["features"][0]["geometry"]["coordinates"][0][0] == 126.978


def test_largest_component():
    segments = [
        _seg(1, "primary", [[126.0, 37.0], [126.001, 37.0]]),
        _seg(2, "primary", [[127.0, 37.0], [127.001, 37.0]]),
    ]
    G, _ = build_graph(segments)
    main = get_largest_component(G)
    assert main.number_of_edges() == 1


def test_metrics_exclude_tiny_disconnected_fragments():
    segments = [
        _seg(1, "residential", [[0.0, 0.0], [0.001, 0.0]]),
        _seg(2, "residential", [[0.001, 0.0], [0.002, 0.0]]),
        _seg(3, "residential", [[1.0, 1.0], [1.001, 1.0]]),
        _seg(4, "residential", [[1.001, 1.0], [1.002, 1.0]]),
    ]
    graph, _ = build_graph(segments)

    metrics, warnings = compute_metrics(graph, segments)

    component_values = [metrics[WALK]["integration"][idx] for idx in range(4)]
    assert sum(value > 0 for value in component_values) == 2
    # 경고는 사용자에게 그대로 보이므로 한국어여야 한다.
    assert any("분석에서 제외" in warning for warning in warnings), warnings
    assert not any(
        "isolated segments" in warning or "network has" in warning
        for warning in warnings
    ), warnings


def test_intersection_splitting_at_shared_interior_vertex():
    # A drivable road with a mid-segment vertex that a walkable path touches.
    # The road must split at that vertex so both share a node (single component).
    road = _seg(1, "primary", [[0.0, 0.0], [0.001, 0.0], [0.002, 0.0]], 200.0)
    path = _seg(2, "footway", [[0.001, 0.0], [0.001, 0.001]], 100.0,
                walkable=True, drivable=False)

    G, _ = build_graph([road, path])

    # Road split into 2 spans + 1 path edge = 3 edges, 4 distinct nodes.
    assert G.number_of_edges() == 3
    assert G.number_of_nodes() == 4
    assert nx.number_connected_components(G) == 1


def test_t_junction_nodes_road_without_existing_vertex():
    road = _seg(1, "primary", [[0.0, 0.0], [0.002, 0.0]], 200.0)
    path = _seg(2, "footway", [[0.001, 0.0], [0.001, 0.001]], 100.0,
                walkable=True, drivable=False)

    graph, _ = build_graph([road, path])

    assert graph.number_of_edges() == 3
    assert nx.number_connected_components(graph) == 1


def test_grade_separated_crossing_stays_disconnected():
    road = _seg(1, "primary", [[0.0, 0.0], [0.002, 0.0]], layer=0)
    bridge = _seg(2, "footway", [[0.001, -0.001], [0.001, 0.001]],
                  walkable=True, drivable=False, layer=1, bridge=True)

    graph, _ = build_graph([road, bridge])

    assert graph.number_of_edges() == 2
    assert nx.number_connected_components(graph) == 2


def test_corner_gap_endpoint_snap_connects():
    gap = 5e-5  # ~5.6 m — beyond SNAP_TOLERANCE but within ENDPOINT_ENDPOINT_TOLERANCE
    road_ns = _seg(1, "primary", [[0.0, 0.0], [0.0, 0.001]])
    road_ew = _seg(2, "primary", [[gap, 0.001], [0.001, 0.001]])

    graph, _ = build_graph([road_ns, road_ew])

    assert nx.number_connected_components(graph) == 1


def test_footway_offset_snap_connects():
    offset = 6e-5  # ~6.7 m from road centreline
    road = _seg(1, "primary", [[0.0, 0.0], [0.002, 0.0]])
    footway = _seg(
        2,
        "footway",
        [[0.001, offset], [0.001, 0.001]],
        walkable=True,
        drivable=False,
    )

    graph, _ = build_graph([road, footway])

    assert nx.number_connected_components(graph) == 1


def test_parallel_roads_stay_separate():
    offset = 5e-5  # ~5.6 m apart — close but parallel
    road_a = _seg(1, "primary", [[0.0, 0.0], [0.001, 0.0]])
    road_b = _seg(2, "primary", [[0.0, offset], [0.001, offset]])

    graph, _ = build_graph([road_a, road_b])

    assert nx.number_connected_components(graph) == 2


def test_per_mode_separation():
    segments = [
        _seg(0, "primary", [[0.0, 0.0], [0.001, 0.0]], 100.0),
        _seg(1, "residential", [[0.001, 0.0], [0.002, 0.0]], 100.0),
        _seg(2, "footway", [[0.001, 0.0], [0.001, 0.001]], 100.0,
             walkable=True, drivable=False),
        _seg(3, "motorway", [[0.0, 0.0], [0.0, 0.001]], 100.0,
             walkable=False, drivable=True),
    ]
    G, _ = build_graph(segments)

    walk_sub = mode_subgraph(G, "walkable")
    drive_sub = mode_subgraph(G, "drivable")
    assert walk_sub.number_of_edges() == 3  # seg0, seg1, seg2
    assert drive_sub.number_of_edges() == 3  # seg0, seg1, seg3

    metrics, _ = compute_metrics(G, segments)

    # Footway (idx 2) contributes to walk only.
    assert metrics[DRIVE]["choice"][2] == 0.0
    assert metrics[DRIVE]["integration"][2] == 0.0
    # Motorway (idx 3) contributes to drive only.
    assert metrics[WALK]["choice"][3] == 0.0
    assert metrics[WALK]["integration"][3] == 0.0


def test_angular_graph_uses_deflection_not_length():
    segments = [
        _seg(1, "road", [[0.0, 0.0], [0.001, 0.0]]),
        _seg(2, "road", [[0.001, 0.0], [0.002, 0.0]]),
        _seg(3, "road", [[0.001, 0.0], [0.001, 0.001]]),
    ]
    primal, _ = build_graph(segments)
    angular = build_angular_graph(primal, "walkable")
    costs = sorted(data["angular_cost"] for _, _, data in angular.edges(data=True))
    assert costs[0] == 0.0
    assert costs[-1] == 1.0


def test_python_engine_distinguishes_central_segment():
    segments = [
        _seg(1, "road", [[0.0, 0.0], [0.001, 0.0]]),
        _seg(2, "road", [[0.001, 0.0], [0.002, 0.0]]),
        _seg(3, "road", [[0.002, 0.0], [0.003, 0.0]]),
    ]
    graph, _ = build_graph(segments)
    metrics, _ = compute_metrics(graph, segments)
    assert metrics[WALK]["choice"][1] > metrics[WALK]["choice"][0]
    assert metrics[WALK]["choice"][1] > metrics[WALK]["choice"][2]


def test_engine_comparison_reports_perfect_match():
    metrics = {
        mode: {
            metric: {0: 1.0, 1: 2.0, 2: 4.0}
            for metric in ("choice", "integration")
        }
        for mode in (WALK, DRIVE)
    }
    comparison = compare_engines(metrics, metrics)
    assert comparison["choice_walk"]["pearson"] == 1.0
    assert comparison["choice_walk"]["mae"] == 0.0


def test_depthmapx_bridge_preserves_source_mapping(tmp_path):
    path = tmp_path / "input.csv"
    write_input(
        path,
        [{
            "source_index": 7,
            "coordinates": [[126.0, 37.0], [126.1, 37.0], [126.2, 37.0]],
            "length_m": 200.0,
            "walkable": True,
            "drivable": False,
        }],
        "walk",
    )
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert {row["source_index"] for row in rows} == {"7"}
    assert find_column(["T1024 Total Depth", "source_index"], "total", "depth") == "T1024 Total Depth"
    assert find_exact_column(["Angular Connectivity", "Connectivity"], "connectivity") == "Connectivity"


def test_depthmapx_atomic_results_aggregate_to_source_segments():
    payload = {
        "source_count": 1,
        "segments": [
            {"source_indices": [0], "length_m": 1.0, "walkable": True, "drivable": True},
            {"source_indices": [0], "length_m": 3.0, "walkable": True, "drivable": True},
        ],
    }
    raw = {
        mode: {
            "choice": {"0": 1.0, "1": 3.0},
            "integration": {"0": 2.0, "1": 4.0},
            "mean_depth": {"0": 5.0, "1": 9.0},
            "connectivity": {"0": 2.0, "1": 6.0},
        }
        for mode in (WALK, DRIVE)
    }
    result = _aggregate(raw, payload)
    assert result[WALK]["choice"][0] == 2.5
    assert result[WALK]["integration"][0] == 3.5
    assert result[WALK]["mean_depth"][0] == 8.0
    assert result[WALK]["connectivity"][0] == 5.0


def test_depthmapx_payload_uses_primary_component_per_mode():
    segments = [
        _seg(1, "road", [[0.0, 0.0], [0.001, 0.0]]),
        _seg(2, "road", [[0.001, 0.0], [0.002, 0.0]]),
        _seg(3, "road", [[1.0, 1.0], [1.001, 1.0]]),
    ]
    graph, _ = build_graph(segments)

    payload = _payload(graph, len(segments))

    assert sum(item["walkable"] for item in payload["segments"]) == 2
    assert sum(item["drivable"] for item in payload["segments"]) == 2
