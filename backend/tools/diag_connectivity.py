"""Diagnose graph connectivity gaps for a point/radius."""
import sys

import networkx as nx
from shapely.geometry import LineString, Point

from app.services.graph_builder import (
    _endpoint_line_tolerance,
    _snap_key,
    build_angular_graph,
    build_graph,
    mode_subgraph,
)
from app.services.space_syntax import _prepare_angular_main
from app.services.spatial_query import fetch_roads_in_radius


def same_grade(left, right) -> bool:
    if left.layer != right.layer:
        return False
    if left.bridge != right.bridge or left.tunnel != right.tunnel:
        return False
    return True


def main() -> None:
    lng = float(sys.argv[1]) if len(sys.argv) > 1 else 126.936
    lat = float(sys.argv[2]) if len(sys.argv) > 2 else 37.555
    radius = int(sys.argv[3]) if len(sys.argv) > 3 else 600

    segments = fetch_roads_in_radius(lng, lat, radius)
    graph, edge_map = build_graph(segments)
    walk = mode_subgraph(graph, "walkable")
    comps = list(nx.connected_components(walk))
    largest = max(comps, key=len)
    small = [c for c in comps if c is not largest]

    angular = build_angular_graph(graph, "walkable")
    warnings: list[str] = []
    _prepare_angular_main(graph, "walkable", "Walkable", warnings)

    print(f"segments={len(segments)} walk_components={len(comps)} angular_components={nx.number_connected_components(angular)}")
    print(f"warnings={warnings}")

    for pair in [(17, 18), (30, 14), (28, 30), (117, 118), (668, 669)]:
        if pair[0] >= len(segments) or pair[1] >= len(segments):
            continue
        a, b = pair
        sa, sb = segments[a], segments[b]
        la, lb = LineString(sa.coordinates), LineString(sb.coordinates)
        print(f"\n--- seg {a} ({sa.highway}) vs {b} ({sb.highway}) same_grade={same_grade(sa, sb)} ---")
        print(f"  layer/bridge/tunnel A: {sa.layer}/{sa.bridge}/{sa.tunnel}")
        print(f"  layer/bridge/tunnel B: {sb.layer}/{sb.bridge}/{sb.tunnel}")
        print(f"  intersects={la.intersects(lb)} intersection={la.intersection(lb)}")
        for label, coord, other in [
            ("A0", sa.coordinates[0], lb),
            ("A1", sa.coordinates[-1], lb),
            ("B0", sb.coordinates[0], la),
            ("B1", sb.coordinates[-1], la),
        ]:
            pt = Point(coord)
            tol = _endpoint_line_tolerance(sa if label.startswith("A") else sb)
            print(
                f"  {label} dist_line={pt.distance(other) * 111320:.1f}m "
                f"tol={tol * 111320:.1f}m snap={_snap_key(*coord)}"
            )

    print("\n--- Small component near-misses (endpoint within 12m of foreign line) ---")
    walk_lines = [
        (i, LineString(segments[i].coordinates))
        for i in range(len(segments))
        if segments[i].walkable
    ]
    count = 0
    for comp_idx, comp in enumerate(small):
        seg_in_comp: set[int] = set()
        for u, v, data in walk.edges(data=True):
            if u in comp and v in comp:
                seg_in_comp.update(data.get("segment_indices", []))
        for si in seg_in_comp:
            seg = segments[si]
            line = LineString(seg.coordinates)
            for coord in (seg.coordinates[0], seg.coordinates[-1]):
                pt = Point(coord)
                for j, other_line in walk_lines:
                    if j == si or not same_grade(seg, segments[j]):
                        continue
                    dist_m = pt.distance(other_line) * 111320
                    if dist_m > 12:
                        continue
                    # Check if j is in same component
                    j_nodes = set()
                    for u, v, data in walk.edges(data=True):
                        if j in data.get("segment_indices", []):
                            j_nodes.add(u)
                            j_nodes.add(v)
                    if j_nodes & comp:
                        continue
                    count += 1
                    if count <= 25:
                        print(
                            f"  comp#{comp_idx} seg{si}({seg.highway}) -> seg{j}({segments[j].highway}) "
                            f"dist={dist_m:.1f}m tol={_endpoint_line_tolerance(seg) * 111320:.1f}m"
                        )
    print(f"total near-misses: {count}")


def trace_segments(segments, graph, walk, indices: list[int]) -> None:
    comps = list(nx.connected_components(walk))
    node_comp = {node: idx for idx, comp in enumerate(comps) for node in comp}

    for si in indices:
        if si >= len(segments):
            continue
        seg = segments[si]
        edges = [(u, v, d) for u, v, d in graph.edges(data=True) if si in d.get("segment_indices", [])]
        print(f"seg{si} {seg.highway} walk={seg.walkable} primal_edges={len(edges)}")
        for u, v, data in edges:
            print(f"  {u} -- {v} walk={data['walkable']}")
        walk_edges = [(u, v, d) for u, v, d in walk.edges(data=True) if si in d.get("segment_indices", [])]
        nodes = set()
        for u, v, _ in walk_edges:
            nodes.add(u)
            nodes.add(v)
        comp_ids = {node_comp.get(n) for n in nodes}
        print(f"  walk_edges={len(walk_edges)} component_ids={comp_ids}")


if __name__ == "__main__":
    lng = float(sys.argv[1]) if len(sys.argv) > 1 else 126.936
    lat = float(sys.argv[2]) if len(sys.argv) > 2 else 37.555
    radius = int(sys.argv[3]) if len(sys.argv) > 3 else 600
    segments = fetch_roads_in_radius(lng, lat, radius)
    graph, _ = build_graph(segments)
    walk = mode_subgraph(graph, "walkable")
    if len(sys.argv) > 4 and sys.argv[4] == "trace":
        trace_segments(segments, graph, walk, [14, 17, 18, 30, 117, 118, 668, 669])
    else:
        main()
