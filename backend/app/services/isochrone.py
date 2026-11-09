"""Time-based reachability (isochrone) on the walkable road graph.

Uses single-source Dijkstra with metre edge lengths — not euclidean circles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import networkx as nx
from shapely.geometry import MultiPoint, Point

from app.config import settings
from app.services.graph_builder import build_graph, mode_subgraph
from app.services.sbiz_stores import StoreRecord
from app.services.spatial_query import RoadSegment

WALK_SPEED_M_PER_MIN = 75.0  # ~4.5 km/h
BIKE_SPEED_M_PER_MIN = 200.0
MOTO_SPEED_M_PER_MIN = 250.0
DRIVE_SPEED_M_PER_MIN = 420.0  # ~25 km/h urban average

MODE_SPEED = {
    "walk": WALK_SPEED_M_PER_MIN,
    "bike": BIKE_SPEED_M_PER_MIN,
    "moto": MOTO_SPEED_M_PER_MIN,
    "drive": DRIVE_SPEED_M_PER_MIN,
}

MODE_LABEL_KO = {
    "walk": "도보",
    "bike": "자전거",
    "moto": "오토바이",
    "drive": "차도",
}

# 3 minutes is the finest band offered (GrowthFactor-style 3/5/10/15 presets).
# 1-2 minutes are intentionally excluded: at walk speed that is only ~67-134m,
# smaller than the minimum useful road-network radius, so the Dijkstra reach
# degenerates to a handful of segments and the band polygon is meaningless.
COMPETITION_MINUTES_BY_MODE: dict[str, tuple[int, ...]] = {
    "walk": (3, 5, 10, 15),
    "drive": (3, 5, 10, 15),
    "moto": (3, 5, 10, 15, 20, 25),
    "bike": (3, 5, 10, 15),
}


def allowed_competition_minutes(mode: str) -> tuple[int, ...]:
    return COMPETITION_MINUTES_BY_MODE.get(mode, COMPETITION_MINUTES_BY_MODE["walk"])


def reach_cutoff_m(mode: str, minutes: int) -> float:
    return MODE_SPEED.get(mode, WALK_SPEED_M_PER_MIN) * minutes


# Vehicular reach is a Euclidean disc, not a network Dijkstra: a 15-minute
# drive band needs a ~7.5km road fetch plus graph construction, which cannot
# fit the 5-second budget, and the product decision is that coarse coverage
# is enough for drive/moto. Walk/bike stay on the exact network computation.
APPROX_REACH_MODES = ("drive", "moto")

APPROX_REACH_WARNING_KO = "차량 도달권은 직선거리 기반 근사입니다."


def uses_reach_approximation(mode: str) -> bool:
    """True for modes whose reach is disc-approximated (no roads/graph needed)."""
    return mode in APPROX_REACH_MODES


def approx_reach_radius_m(mode: str, minutes: int) -> float:
    """Effective disc radius: network distance scaled down to straight-line."""
    return reach_cutoff_m(mode, minutes) * settings.network_detour_factor


def competition_fetch_radius_m(base_radius_m: int, mode: str, minutes: int) -> int:
    """Minimum SBIZ/road fetch radius to cover a time-based reach band."""
    reach = reach_cutoff_m(mode, minutes)
    if uses_reach_approximation(mode):
        # Disc-approximated reach never extends past factor×cutoff, so the
        # 1.2 network-hull margin would only fetch stores that can never be
        # counted (at 15min drive that is ~2.5× the useful area).
        needed = int(reach * settings.network_detour_factor * 1.05)
    else:
        needed = int(reach * 1.2)
    return max(base_radius_m, min(needed, 8000))


def network_attr_for_mode(mode: str) -> str:
    """Walk/bike use pedestrian network; moto/drive use vehicular network."""
    if mode in ("walk", "bike"):
        return "walkable"
    return "drivable"


@dataclass
class IsochroneBand:
    minutes: int
    max_distance_m: float
    polygon: dict  # GeoJSON geometry
    area_m2: float
    store_count: int
    same_category_count: int
    population_estimate: int | None


@dataclass
class IsochroneResult:
    lat: float
    lng: float
    mode: str
    bands: list[IsochroneBand]
    start_connected: bool
    warnings: list[str]


def _nearest_graph_node(G: nx.Graph, lng: float, lat: float) -> tuple | None:
    if G.number_of_nodes() == 0:
        return None
    point = Point(lng, lat)
    best: tuple | None = None
    best_dist = float("inf")
    for node in G.nodes:
        d = point.distance(Point(node[0], node[1]))
        if d < best_dist:
            best_dist = d
            best = node
    return best


def _reachable_edge_coords(
    G: nx.Graph, start: tuple, cutoff_m: float
) -> list[tuple[float, float]]:
    lengths = nx.single_source_dijkstra_path_length(G, start, weight="length", cutoff=cutoff_m)
    reachable = set(lengths.keys())
    coords: list[tuple[float, float]] = []
    for u, v, data in G.edges(data=True):
        if u in reachable and v in reachable:
            coords.append((u[0], u[1]))
            coords.append((v[0], v[1]))
    return coords


def _coords_to_polygon(coords: list[tuple[float, float]]) -> tuple[dict, float]:
    if len(coords) < 3:
        return {"type": "Polygon", "coordinates": []}, 0.0
    mlat = math.radians(sum(c[1] for c in coords) / len(coords))
    scale = 111_320 * math.cos(mlat)
    projected = [(c[0] * scale, c[1] * 111_320) for c in coords]
    hull = MultiPoint(projected).convex_hull
    if hull.is_empty or hull.geom_type not in ("Polygon", "MultiPolygon"):
        return {"type": "Polygon", "coordinates": []}, 0.0
    if hull.geom_type == "MultiPolygon":
        hull = max(hull.geoms, key=lambda g: g.area)
    inv = [(x / scale, y / 111_320) for x, y in hull.exterior.coords]
    geom = {"type": "Polygon", "coordinates": [inv]}
    return geom, float(hull.area)


def _point_in_polygon(lng: float, lat: float, polygon: dict) -> bool:
    rings = polygon.get("coordinates") or []
    if not rings:
        return False
    from shapely.geometry import shape

    return Point(lng, lat).within(shape(polygon))


def point_in_reach_polygon(lng: float, lat: float, polygon: dict) -> bool:
    """Return True when a WGS84 point lies inside a GeoJSON reach polygon."""
    return _point_in_polygon(lng, lat, polygon)


def _store_same_category(
    store: StoreRecord,
    *,
    category_keywords: list[str] | None = None,
    inds_mcls_cd: str | None = None,
    inds_scls_cd: str | None = None,
) -> bool:
    from app.services.site_score import store_matches_upjong

    if inds_mcls_cd or inds_scls_cd:
        return store_matches_upjong(
            store, inds_mcls_cd=inds_mcls_cd, inds_scls_cd=inds_scls_cd
        )
    if category_keywords:
        label = f"{store.inds_mcls_nm} {store.inds_scls_nm}"
        return any(k in label for k in category_keywords)
    return False


def _count_stores_in_polygon(
    stores: list[StoreRecord],
    polygon: dict,
    *,
    category_keywords: list[str] | None = None,
    inds_mcls_cd: str | None = None,
    inds_scls_cd: str | None = None,
) -> tuple[int, int]:
    total = 0
    same = 0
    for store in stores:
        if store.lon is None or store.lat is None:
            continue
        if not _point_in_polygon(store.lon, store.lat, polygon):
            continue
        total += 1
        if _store_same_category(
            store,
            category_keywords=category_keywords,
            inds_mcls_cd=inds_mcls_cd,
            inds_scls_cd=inds_scls_cd,
        ):
            same += 1
    return total, same


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _circle_polygon(
    lng: float, lat: float, radius_m: float, *, n_points: int = 48
) -> tuple[dict, float]:
    """WGS84 GeoJSON circle around a point — same coordinate frame and shape
    format as the network hull polygons (equirectangular metre scaling)."""
    lat_scale = 111_320.0
    lng_scale = 111_320.0 * math.cos(math.radians(lat))
    ring: list[tuple[float, float]] = []
    for i in range(n_points + 1):
        theta = 2 * math.pi * i / n_points
        ring.append(
            (
                lng + radius_m * math.cos(theta) / lng_scale,
                lat + radius_m * math.sin(theta) / lat_scale,
            )
        )
    geom = {"type": "Polygon", "coordinates": [ring]}
    return geom, math.pi * radius_m**2


def _approx_vehicle_isochrones(
    *,
    lat: float,
    lng: float,
    minutes_list: tuple[int, ...],
    mode: str,
    stores: list[StoreRecord] | None,
    category_keywords: list[str] | None,
    inds_mcls_cd: str | None,
    inds_scls_cd: str | None,
    dong_population: int | None,
) -> IsochroneResult:
    """Disc-approximated reach bands for vehicular modes — no roads, no graph.

    Band values (store counts, population share) reuse the same rules as the
    network path; only the reach geometry is a circle instead of a Dijkstra
    hull, so distance-to-center replaces point-in-polygon for counting.
    """
    located: list[tuple[StoreRecord, float]] = []
    for store in stores or []:
        if store.lon is None or store.lat is None:
            continue
        located.append((store, _haversine_m(lat, lng, store.lat, store.lon)))

    bands: list[IsochroneBand] = []
    for minutes in sorted(minutes_list):
        cutoff = reach_cutoff_m(mode, minutes)
        radius = approx_reach_radius_m(mode, minutes)
        polygon, area_m2 = _circle_polygon(lng, lat, radius)
        total = 0
        same = 0
        for store, dist in located:
            if dist > radius:
                continue
            total += 1
            if _store_same_category(
                store,
                category_keywords=category_keywords,
                inds_mcls_cd=inds_mcls_cd,
                inds_scls_cd=inds_scls_cd,
            ):
                same += 1
        pop_est: int | None = None
        if dong_population is not None and area_m2 > 0:
            # Same proxy as the network path: dong population scaled by the
            # band area vs the full speed×minutes circle.
            circle_area = math.pi * cutoff**2
            ratio = min(1.0, area_m2 / circle_area) if circle_area > 0 else 0
            pop_est = int(dong_population * ratio * 0.6)
        bands.append(
            IsochroneBand(
                minutes=minutes,
                max_distance_m=round(cutoff, 1),
                polygon=polygon,
                area_m2=round(area_m2, 1),
                store_count=total,
                same_category_count=same,
                population_estimate=pop_est,
            )
        )

    return IsochroneResult(
        lat=lat,
        lng=lng,
        mode=mode,
        bands=bands,
        start_connected=True,
        warnings=[APPROX_REACH_WARNING_KO],
    )


def compute_isochrones(
    *,
    G: nx.Graph | None,
    segments: list[RoadSegment] | None,
    lat: float,
    lng: float,
    minutes_list: tuple[int, ...] = (5, 10, 15),
    mode: str = "walk",
    stores: list[StoreRecord] | None = None,
    category_keywords: list[str] | None = None,
    inds_mcls_cd: str | None = None,
    inds_scls_cd: str | None = None,
    dong_population: int | None = None,
) -> IsochroneResult:
    """Build isochrone bands — network Dijkstra for walk/bike, disc for drive/moto."""
    if uses_reach_approximation(mode):
        return _approx_vehicle_isochrones(
            lat=lat,
            lng=lng,
            minutes_list=minutes_list,
            mode=mode,
            stores=stores,
            category_keywords=category_keywords,
            inds_mcls_cd=inds_mcls_cd,
            inds_scls_cd=inds_scls_cd,
            dong_population=dong_population,
        )
    if G is None:
        raise ValueError("walk/bike isochrones require a pre-built road graph")
    warnings: list[str] = []
    attr = network_attr_for_mode(mode)
    subgraph = mode_subgraph(G, attr)
    if subgraph.number_of_nodes() == 0:
        return IsochroneResult(
            lat=lat, lng=lng, mode=mode, bands=[], start_connected=False,
            warnings=[f"No {mode} network in radius"],
        )

    components = list(nx.connected_components(subgraph))
    largest = max(components, key=len)
    G_main = subgraph.subgraph(largest).copy()

    start_on_full = _nearest_graph_node(subgraph, lng, lat)
    if start_on_full is not None and start_on_full not in largest:
        mode_label = MODE_LABEL_KO.get(mode, mode)
        warnings.append(
            f"분석 위치가 {mode_label} 도로망의 고립 구간에 있어 "
            "가장 큰 연결 구간 기준으로 도달권을 계산했습니다."
        )

    start = _nearest_graph_node(G_main, lng, lat)
    if start is None:
        return IsochroneResult(
            lat=lat, lng=lng, mode=mode, bands=[], start_connected=False, warnings=["No start node"]
        )

    speed = MODE_SPEED.get(mode, WALK_SPEED_M_PER_MIN)
    store_list = stores or []
    bands: list[IsochroneBand] = []

    for minutes in sorted(minutes_list):
        cutoff = speed * minutes
        coords = _reachable_edge_coords(G_main, start, cutoff)
        polygon, area_m2 = _coords_to_polygon(coords)
        total_stores, same_stores = _count_stores_in_polygon(
            store_list,
            polygon,
            category_keywords=category_keywords,
            inds_mcls_cd=inds_mcls_cd,
            inds_scls_cd=inds_scls_cd,
        )
        pop_est: int | None = None
        if dong_population is not None and area_m2 > 0:
            # Rough proxy: scale dong population by isochrone area vs circle of same cutoff
            circle_area = math.pi * cutoff**2
            ratio = min(1.0, area_m2 / circle_area) if circle_area > 0 else 0
            pop_est = int(dong_population * ratio * 0.6)

        bands.append(
            IsochroneBand(
                minutes=minutes,
                max_distance_m=round(cutoff, 1),
                polygon=polygon,
                area_m2=round(area_m2, 1),
                store_count=total_stores,
                same_category_count=same_stores,
                population_estimate=pop_est,
            )
        )

    return IsochroneResult(
        lat=lat,
        lng=lng,
        mode=mode,
        bands=bands,
        start_connected=True,
        warnings=warnings,
    )


def build_graph_for_point(
    segments: list[RoadSegment],
) -> nx.Graph:
    G, _ = build_graph(segments)
    return G
