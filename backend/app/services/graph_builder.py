import hashlib
import math
import pickle
import threading
from collections import OrderedDict

import networkx as nx
import numpy as np
import shapely
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, Point
from shapely.strtree import STRtree

from app.services.spatial_query import RoadSegment

# OSM ways that logically meet are often digitised a few metres apart. Around
# Korea, 3e-5 degrees is roughly 2.6-3.3 m. Grade-separated crossings are
# filtered before these tolerances are used.
SNAP_TOLERANCE = 4e-5
# Corner gaps where endpoints almost meet but lines do not intersect.
ENDPOINT_ENDPOINT_TOLERANCE = 6e-5
# Footway/path endpoints often stop at a road edge, not its centreline.
FOOTWAY_SNAP_TOLERANCE = 10e-5
# Node identity grid — wide enough to unify junction marks that differ by a few
# metres, but finer than typical parallel-road separation (~5-6 m).
JUNCTION_SNAP_TOLERANCE = 5e-5
MAX_SNAP_TOLERANCE = max(SNAP_TOLERANCE, ENDPOINT_ENDPOINT_TOLERANCE, FOOTWAY_SNAP_TOLERANCE)
# Last-resort bridge for tiny disconnected walkable fragments near the main network.
COMPONENT_BRIDGE_TOLERANCE_M = 12.0
PEDESTRIAN_HIGHWAYS = frozenset({"footway", "steps", "path", "pedestrian", "service", "track"})


def _snap_key(lng: float, lat: float) -> tuple[float, float]:
    return (
        round(lng / JUNCTION_SNAP_TOLERANCE) * JUNCTION_SNAP_TOLERANCE,
        round(lat / JUNCTION_SNAP_TOLERANCE) * JUNCTION_SNAP_TOLERANCE,
    )


def _approx_dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular metre approximation between two lng/lat points."""
    lng1, lat1 = a
    lng2, lat2 = b
    mlat = math.radians((lat1 + lat2) / 2)
    dx = (lng2 - lng1) * math.cos(mlat) * 111_320
    dy = (lat2 - lat1) * 111_320
    return math.hypot(dx, dy)


def _segment_bearing(line: LineString, *, at_end: bool) -> float:
    coords = list(line.coords)
    if at_end:
        x1, y1 = coords[-2]
        x2, y2 = coords[-1]
    else:
        x1, y1 = coords[0]
        x2, y2 = coords[1]
    return math.atan2(y2 - y1, x2 - x1)


def _bearings_form_corner(left: float, right: float, min_deg: float = 25.0) -> bool:
    """Return True when two bearings meet at a junction, not a parallel pair."""
    diff = abs(left - right)
    diff = min(diff, 2 * math.pi - diff)
    acute = min(diff, math.pi - diff)
    return acute > math.radians(min_deg)


def _endpoint_line_tolerance(segment: RoadSegment) -> float:
    if segment.walkable and not segment.drivable:
        return FOOTWAY_SNAP_TOLERANCE
    return SNAP_TOLERANCE


def _same_grade(left: RoadSegment, right: RoadSegment) -> bool:
    if left.layer != right.layer:
        return False
    if left.bridge != right.bridge or left.tunnel != right.tunnel:
        return False
    return True


def _bridge_grade_compatible(left: RoadSegment, right: RoadSegment, dist_m: float) -> bool:
    if _same_grade(left, right):
        return True
    if dist_m > 6.0:
        return False
    if left.highway not in PEDESTRIAN_HIGHWAYS or right.highway not in PEDESTRIAN_HIGHWAYS:
        return False
    # Short pedestrian links often carry inconsistent layer/bridge tags in OSM.
    return abs(left.layer - right.layer) <= 1


def _bridge_component_gaps(
    G: nx.Graph,
    segments: list[RoadSegment],
    edge_map: dict[int, list[tuple]],
) -> None:
    """Connect tiny walkable components to the largest one when endpoints are nearby."""
    walk_edges = [(u, v, d) for u, v, d in G.edges(data=True) if d.get("walkable")]
    if not walk_edges:
        return

    H = nx.Graph()
    for u, v, _ in walk_edges:
        H.add_edge(u, v)
    if H.number_of_nodes() == 0:
        return

    components = list(nx.connected_components(H))
    if len(components) <= 1:
        return

    largest = max(components, key=len)
    node_segments: dict[tuple[float, float], set[int]] = {}
    for u, v, data in walk_edges:
        for node in (u, v):
            node_segments.setdefault(node, set()).update(data.get("segment_indices", []))

    # Precompute, in a single O(edges) pass, everything the per-component loop
    # below used to re-derive by rescanning every walk edge: which component
    # owns each node, which segments live entirely inside a given component,
    # and which graph edges carry a given segment index. All edges sharing a
    # segment index are connected to each other (they're consecutive slices
    # of the same original line), so they always share one component — this
    # lets each segment/component pairing be computed exactly once.
    node_to_comp: dict[tuple[float, float], int] = {}
    for comp_idx, comp in enumerate(components):
        for node in comp:
            node_to_comp[node] = comp_idx

    comp_segments_map: dict[int, set[int]] = {}
    edges_by_segidx: dict[int, list[tuple]] = {}
    for u, v, data in walk_edges:
        seg_indices = data.get("segment_indices", [])
        for seg_idx in seg_indices:
            edges_by_segidx.setdefault(seg_idx, []).append((u, v))
        if node_to_comp[u] == node_to_comp[v]:
            comp_segments_map.setdefault(node_to_comp[u], set()).update(seg_indices)

    # Spatial index over the largest component's nodes: each small-component
    # endpoint below queries only nearby candidates instead of scanning every
    # node of the (potentially huge) main component.
    largest_nodes = list(largest)
    largest_tree = STRtree([Point(node) for node in largest_nodes]) if largest_nodes else None
    # Degree buffer that safely over-approximates COMPONENT_BRIDGE_TOLERANCE_M
    # at Korea's latitudes (1 degree of lng or lat is >= ~85 km here), so the
    # STRtree query is guaranteed to return every node truly within tolerance;
    # the exact `_approx_dist_m` check below still gates acceptance precisely.
    query_degrees = (COMPONENT_BRIDGE_TOLERANCE_M + 5.0) / 80_000

    for comp_idx, comp in enumerate(components):
        if comp is largest:
            continue

        comp_segments = comp_segments_map.get(comp_idx, set())
        if not comp_segments or not all(
            segments[seg_idx].highway in PEDESTRIAN_HIGHWAYS for seg_idx in comp_segments
        ):
            continue

        best: tuple[float, tuple[float, float], tuple[float, float], int] | None = None
        for seg_idx in comp_segments:
            seg = segments[seg_idx]
            if not seg.walkable:
                continue
            segment_nodes: set[tuple[float, float]] = set()
            for u, v in edges_by_segidx.get(seg_idx, []):
                if u in comp:
                    segment_nodes.add(u)
                if v in comp:
                    segment_nodes.add(v)
            for endpoint_node in segment_nodes:
                if largest_tree is None:
                    continue
                nearby = largest_tree.query(Point(endpoint_node).buffer(query_degrees))
                for main_raw in nearby:
                    main_node = largest_nodes[int(main_raw)]
                    dist_m = _approx_dist_m(endpoint_node, main_node)
                    if dist_m > COMPONENT_BRIDGE_TOLERANCE_M:
                        continue
                    compatible = any(
                        _bridge_grade_compatible(seg, segments[candidate], dist_m)
                        for candidate in node_segments.get(main_node, set())
                    )
                    if not compatible:
                        continue
                    if best is None or dist_m < best[0]:
                        best = (dist_m, endpoint_node, main_node, seg_idx)

        if best is None:
            continue

        _, u, v, seg_idx = best
        if u == v or G.has_edge(u, v):
            continue
        G.add_edge(
            u,
            v,
            length=best[0],
            walkable=True,
            drivable=segments[seg_idx].drivable,
            segment_indices=[seg_idx],
        )
        edge_map.setdefault(seg_idx, []).append((u, v))


def analysis_point_on_primary_network(
    G: nx.Graph,
    lng: float,
    lat: float,
    *,
    attr: str = "walkable",
) -> bool:
    """Return True when the nearest ``attr`` edge node belongs to the largest component."""
    mode_edges = [(u, v) for u, v, data in G.edges(data=True) if data.get(attr)]
    if not mode_edges:
        return False

    H = nx.Graph()
    H.add_edges_from(mode_edges)
    if H.number_of_nodes() == 0:
        return False

    components = list(nx.connected_components(H))
    largest = max(components, key=len)
    point = (lng, lat)
    nearest_node = min(H.nodes, key=lambda node: _approx_dist_m(point, node))
    return nearest_node in largest


def seg_nodes_for_endpoints(seg: RoadSegment) -> list[tuple[float, float]]:
    return [_snap_key(*seg.coordinates[0]), _snap_key(*seg.coordinates[-1])]


# ---------------------------------------------------------------------------
# Reuse + concurrency policy for graph construction
# ---------------------------------------------------------------------------
#
# build_graph is a *pure* function of the segment list: same segments in, same
# (graph, edge_map) out. Two facts measured on live Seoul data drive the two
# guards below.
#
# 1) It is the dominant cost of every multi-candidate request. A single
#    /api/decide with 3 candidates calls it six times (site-score's capped
#    network + the walk network the cannibalization path needs, per candidate).
#
# 2) It is GIL-bound. The work is tens of thousands of *scalar* GEOS calls
#    (Point(), .distance(), .project(), .interpolate()), none of which release
#    the GIL for long enough to overlap. Building two 1k-segment networks in two
#    threads took 9.3-16.0s wall against 3.3s for the same two builds run
#    back-to-back — concurrency does not buy parallelism here, it buys convoying.
#
# So: memoise on the segment content, and let only one build run at a time.
# Callers receive the *same* graph object on a hit. That is safe because no
# consumer mutates it — every reader (isochrone's mode_subgraph, depthmapx's
# _payload, space_syntax's build_angular_graph, site_score's corner_info,
# cannibalization, territory) either reads or works on its own .copy().
_GRAPH_CACHE: "OrderedDict[bytes, tuple[nx.Graph, dict[int, list[tuple]]]]" = OrderedDict()
_GRAPH_CACHE_MAX = 24
_GRAPH_CACHE_LOCK = threading.Lock()
_GRAPH_BUILD_LOCK = threading.Lock()


def _segments_fingerprint(segments: list[RoadSegment]) -> bytes:
    """Content hash over every RoadSegment field build_graph actually reads."""
    payload = pickle.dumps(
        [
            (
                seg.osm_id,
                seg.highway,
                seg.length_m,
                seg.walkable,
                seg.drivable,
                seg.layer,
                seg.bridge,
                seg.tunnel,
                seg.coordinates,
            )
            for seg in segments
        ],
        protocol=pickle.HIGHEST_PROTOCOL,
    )
    return hashlib.blake2b(payload, digest_size=20).digest()


def _cache_lookup(key: bytes):
    with _GRAPH_CACHE_LOCK:
        hit = _GRAPH_CACHE.get(key)
        if hit is not None:
            _GRAPH_CACHE.move_to_end(key)
        return hit


def build_graph(segments: list[RoadSegment]) -> tuple[nx.Graph, dict[int, list[tuple]]]:
    """
    Build one connected undirected graph from road LineStrings.

    Same-grade road centrelines are geometrically noded before modal filtering.
    This connects a footway endpoint to the middle of a general road even when
    OSM did not encode an identical vertex on both ways. Grade-separated
    bridge/tunnel/layer crossings remain disconnected. Edges carry the source's
    ``walkable``/``drivable`` flags, a metre ``length``, and the contributing
    ``segment_indices``.

    Results are memoised by segment content and builds are serialised — see the
    note above _GRAPH_CACHE for why both are safe and why they are needed.
    """
    key = _segments_fingerprint(segments)
    hit = _cache_lookup(key)
    if hit is not None:
        return hit

    with _GRAPH_BUILD_LOCK:
        # Another thread may have built exactly this network while we queued.
        hit = _cache_lookup(key)
        if hit is not None:
            return hit
        result = _build_graph_uncached(segments)

    with _GRAPH_CACHE_LOCK:
        _GRAPH_CACHE[key] = result
        _GRAPH_CACHE.move_to_end(key)
        while len(_GRAPH_CACHE) > _GRAPH_CACHE_MAX:
            _GRAPH_CACHE.popitem(last=False)
    return result


def _build_graph_uncached(
    segments: list[RoadSegment],
) -> tuple[nx.Graph, dict[int, list[tuple]]]:
    G = nx.Graph()
    edge_map: dict[int, list[tuple]] = {}

    lines = [LineString(seg.coordinates) for seg in segments]
    # A segment's endpoint coordinates, endpoint Points and end bearings are
    # re-derived once per candidate pair it takes part in — tens of thousands of
    # identical Point()/coords lookups per network. Deriving them once per
    # segment feeds the tolerance tests below exactly the same values.
    coord_seqs = [list(line.coords) for line in lines]
    first_coords = [coords[0] for coords in coord_seqs]
    last_coords = [coords[-1] for coords in coord_seqs]
    first_points = [Point(coord) for coord in first_coords]
    last_points = [Point(coord) for coord in last_coords]
    marks: list[dict[float, tuple[float, float]]] = [
        {0.0: tuple(first_coords[index]), line.length: tuple(last_coords[index])}
        for index, line in enumerate(lines)
    ]
    bearing_cache: dict[tuple[int, bool], float] = {}

    def bearing(index: int, at_end: bool) -> float:
        """_segment_bearing(lines[index], at_end=at_end), computed at most once."""
        cache_key = (index, at_end)
        value = bearing_cache.get(cache_key)
        if value is None:
            coords = coord_seqs[index]
            if at_end:
                (x1, y1), (x2, y2) = coords[-2], coords[-1]
            else:
                (x1, y1), (x2, y2) = coords[0], coords[1]
            value = math.atan2(y2 - y1, x2 - x1)
            bearing_cache[cache_key] = value
        return value

    def same_grade(left: RoadSegment, right: RoadSegment) -> bool:
        return _same_grade(left, right)

    def points_from(geometry) -> list[Point]:
        if geometry.is_empty:
            return []
        if isinstance(geometry, Point):
            return [geometry]
        if isinstance(geometry, MultiPoint):
            return list(geometry.geoms)
        if isinstance(geometry, (LineString, MultiLineString)):
            geoms = [geometry] if isinstance(geometry, LineString) else list(geometry.geoms)
            return [Point(coord) for geom in geoms for coord in (geom.coords[0], geom.coords[-1])]
        if isinstance(geometry, GeometryCollection):
            return [point for part in geometry.geoms for point in points_from(part)]
        return []

    def add_mark(index: int, point: Point, canonical: tuple[float, float] | None = None) -> None:
        line = lines[index]
        distance = line.project(point)
        snapped_distance = round(distance, 12)
        projected = line.interpolate(distance)
        marks[index][snapped_distance] = canonical or (projected.x, projected.y)

    tree = STRtree(lines)
    for left_idx, left_line in enumerate(lines):
        # Sorted: STRtree query order depends on tree packing, and add_mark
        # resolves same-distance mark collisions by last-writer-wins. Without a
        # fixed order, appending one segment anywhere (e.g. a simulation's
        # virtual road) reshuffles canonical node coordinates network-wide and
        # the before/after graphs diverge on segments the new road never touches.
        raw_candidates = sorted(
            int(i)
            for i in tree.query(left_line.buffer(MAX_SNAP_TOLERANCE))
            if int(i) > left_idx and same_grade(segments[left_idx], segments[int(i)])
        )
        if not raw_candidates:
            continue

        # Cheap pre-check before the expensive per-candidate Point()/.coords/
        # .distance() work below: every tolerance used further down (snap,
        # endpoint-endpoint, footway) is <= MAX_SNAP_TOLERANCE, and every
        # point tested is a point ON either left_line or right_line, so none
        # of those checks can pass unless the two full lines are themselves
        # within MAX_SNAP_TOLERANCE of each other. Batching the distance call
        # across all of this left_line's candidates (shapely 2.x vectorised
        # op) also avoids one Python/C-boundary crossing per candidate.
        candidate_lines = np.empty(len(raw_candidates), dtype=object)
        for i, idx in enumerate(raw_candidates):
            candidate_lines[i] = lines[idx]
        pair_dists = shapely.distance(left_line, candidate_lines)

        for right_idx, pair_dist in zip(raw_candidates, pair_dists):
            if pair_dist > MAX_SNAP_TOLERANCE:
                continue
            right_line = lines[right_idx]
            intersections = points_from(left_line.intersection(right_line))
            for point in intersections:
                canonical = (point.x, point.y)
                add_mark(left_idx, point, canonical)
                add_mark(right_idx, point, canonical)

            # Repair almost-touching OSM endpoints without joining parallel
            # roads or arbitrary close mid-spans. Tolerance is asymmetric
            # (depends on the source segment's own type), so both directions
            # must still be checked separately.
            for source_idx, target_idx, target in (
                (left_idx, right_idx, right_line),
                (right_idx, left_idx, left_line),
            ):
                source_segment = segments[source_idx]
                line_tolerance = _endpoint_line_tolerance(source_segment)
                for endpoint in (first_points[source_idx], last_points[source_idx]):
                    if endpoint.distance(target) <= line_tolerance:
                        projected = target.interpolate(target.project(endpoint))
                        canonical = (projected.x, projected.y)
                        add_mark(source_idx, endpoint, canonical)
                        add_mark(target_idx, projected, canonical)

            # Corner-gap repair is symmetric in (left, right): distance and
            # bearing-corner tests don't depend on which side is "source", so
            # checking each left-endpoint/right-endpoint pair once here
            # produces the same marks as running it once per direction would.
            left_last_coord = last_coords[left_idx]
            right_last_coord = last_coords[right_idx]
            for left_coord, left_point in (
                (first_coords[left_idx], first_points[left_idx]),
                (left_last_coord, last_points[left_idx]),
            ):
                left_at_end = left_coord == left_last_coord
                for right_coord, right_point in (
                    (first_coords[right_idx], first_points[right_idx]),
                    (right_last_coord, last_points[right_idx]),
                ):
                    if left_point.distance(right_point) > ENDPOINT_ENDPOINT_TOLERANCE:
                        continue
                    right_at_end = right_coord == right_last_coord
                    if not _bearings_form_corner(
                        bearing(left_idx, left_at_end),
                        bearing(right_idx, right_at_end),
                    ):
                        continue
                    canonical = (
                        (left_point.x + right_point.x) / 2,
                        (left_point.y + right_point.y) / 2,
                    )
                    add_mark(left_idx, left_point, canonical)
                    add_mark(right_idx, right_point, canonical)

    for idx, seg in enumerate(segments):
        line = lines[idx]
        ordered = sorted(marks[idx].items())
        total_geom = line.length
        fallback_length = sum(
            _approx_dist_m(tuple(seg.coordinates[i]), tuple(seg.coordinates[i + 1]))
            for i in range(len(seg.coordinates) - 1)
        )
        seg_length = seg.length_m if seg.length_m > 0 else fallback_length

        for (start_distance, start_coord), (end_distance, end_coord) in zip(ordered, ordered[1:]):
            u = _snap_key(*start_coord)
            v = _snap_key(*end_coord)
            if u == v or end_distance <= start_distance:
                continue
            length = (
                seg_length * ((end_distance - start_distance) / total_geom)
                if total_geom > 0
                else 0.0
            )

            if G.has_edge(u, v):
                data = G[u][v]
                data["walkable"] = data["walkable"] or seg.walkable
                data["drivable"] = data["drivable"] or seg.drivable
                data["length"] = min(data["length"], length) if length > 0 else data["length"]
                if idx not in data["segment_indices"]:
                    data["segment_indices"].append(idx)
            else:
                G.add_edge(
                    u,
                    v,
                    length=length,
                    walkable=seg.walkable,
                    drivable=seg.drivable,
                    segment_indices=[idx],
                )
            edge_map.setdefault(idx, []).append((u, v))

    _bridge_component_gaps(G, segments, edge_map)
    return G, edge_map


def mode_subgraph(G: nx.Graph, attr: str) -> nx.Graph:
    """Return the subgraph of edges whose ``attr`` flag (walkable/drivable) is set."""
    edges = [(u, v) for u, v, d in G.edges(data=True) if d.get(attr)]
    if not edges:
        return nx.Graph()
    return G.edge_subgraph(edges).copy()


def get_largest_component(G: nx.Graph) -> nx.Graph:
    if G.number_of_nodes() == 0:
        return G
    components = list(nx.connected_components(G))
    largest = max(components, key=len)
    return G.subgraph(largest).copy()


def build_angular_graph(G: nx.Graph, attr: str) -> nx.Graph:
    """Build a segment (dual) graph for angular Space Syntax analysis.

    Each eligible primal edge becomes one node. Two dual nodes are connected
    when their road spans meet. ``angular_cost`` is the normalised deflection
    angle in depthmapX units: 0 is straight, 1 is a right-angle turn, and 2
    is a U-turn.
    """
    eligible = {
        tuple(sorted((u, v))): data
        for u, v, data in G.edges(data=True)
        if data.get(attr)
    }
    dual = nx.Graph()
    incident: dict[tuple[float, float], list[tuple]] = {}

    for edge_id, data in eligible.items():
        u, v = edge_id
        dual.add_node(
            edge_id,
            length=float(data.get("length", 0.0)),
            segment_indices=list(data.get("segment_indices", [])),
        )
        incident.setdefault(u, []).append(edge_id)
        incident.setdefault(v, []).append(edge_id)

    for junction, edge_ids in incident.items():
        for i, left in enumerate(edge_ids):
            for right in edge_ids[i + 1 :]:
                left_other = left[1] if left[0] == junction else left[0]
                right_other = right[1] if right[0] == junction else right[0]
                longitude_scale = math.cos(math.radians(junction[1]))
                ax = (left_other[0] - junction[0]) * longitude_scale
                ay = left_other[1] - junction[1]
                bx = (right_other[0] - junction[0]) * longitude_scale
                by = right_other[1] - junction[1]
                amag = math.hypot(ax, ay)
                bmag = math.hypot(bx, by)
                if amag == 0 or bmag == 0:
                    continue
                cosine = max(-1.0, min(1.0, (ax * bx + ay * by) / (amag * bmag)))
                angle_between = math.acos(cosine)
                deflection = math.pi - angle_between
                dual.add_edge(left, right, angular_cost=2.0 * deflection / math.pi)

    return dual
