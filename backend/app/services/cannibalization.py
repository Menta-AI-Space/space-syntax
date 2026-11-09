"""Competition cannibalization: isochrone-filtered rivals + overlap."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import networkx as nx
from shapely.geometry import shape

from app.services.isochrone import _point_in_polygon, compute_isochrones
from app.services.living_pop import fetch_living_pop_profile
from app.services.sbiz_stores import StoreRecord
from app.services.site_score import store_matches_upjong
from app.services.spatial_query import RoadSegment


@dataclass
class CompetitorOut:
    name: str
    category: str
    distance_m: float
    address: str
    lat: float | None = None
    lng: float | None = None


@dataclass
class CannibalizationResult:
    risk_score: float
    risk_label: str
    competitors: list[CompetitorOut]
    overlap_pct: float | None
    overlap_with: str | None
    overlap_recommended_max_pct: float
    detail: str
    mode: str = "walk"
    minutes: int = 5
    total_count: int = 0
    reach_polygon: dict | None = None
    self_total_count: int = 0
    self_competitors: list[CompetitorOut] = field(default_factory=list)
    self_overlap_pct: float | None = None
    demand_factor: float | None = None
    warnings: list[str] = field(default_factory=list)


# Saturating-risk divisor: S = sum(100*exp(-d/120)); risk_raw = 100*(1-exp(-S/250)).
# Calibrated so 1 rival @50m ~ 23, 5 rivals within 150m ~ 61, 15+ within 300m >= 85.
RISK_SATURATION_DIVISOR = 250.0
# Seoul-typical weekday-hourly living population within 500m (median of diverse
# sample points: 신촌 26.1k, 강남역 60.0k, 성수 23.6k, 상계 19.6k -> ~24.8k).
DEMAND_BASELINE = 25_000.0
# Demand is always measured at the same radius the baseline was calibrated on,
# regardless of the (mode-dependent, possibly multi-km) competitor fetch radius.
DEMAND_RADIUS_M = 500


MODE_LABELS = {
    "walk": "도보",
    "drive": "차량",
    "bike": "자전거",
    "moto": "오토바이",
}


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _polygon_area_m2(polygon: dict) -> float:
    if not polygon.get("coordinates"):
        return 0.0
    mlat = shape(polygon).centroid.y
    scale = 111_320 * math.cos(math.radians(mlat))
    from shapely.geometry import Polygon

    ring = [(lng * scale, lat * 111_320) for lng, lat in polygon["coordinates"][0]]
    return float(Polygon(ring).area)


def _overlap_pct(poly_a: dict, poly_b: dict) -> float:
    if not poly_a.get("coordinates") or not poly_b.get("coordinates"):
        return 0.0
    mlat = shape(poly_a).centroid.y
    scale = 111_320 * math.cos(math.radians(mlat))

    def project(poly: dict):
        ring = [(lng * scale, lat * 111_320) for lng, lat in poly["coordinates"][0]]
        from shapely.geometry import Polygon

        return Polygon(ring)

    pa = project(poly_a)
    pb = project(poly_b)
    if pa.is_empty or pa.area <= 0:
        return 0.0
    return round(100.0 * pa.intersection(pb).area / pa.area, 1)


def _mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode)


def _normalize_brand(brand: str | None) -> str | None:
    """Trimmed lowercase brand string, or None when too short to match safely."""
    if not brand:
        return None
    cleaned = brand.strip().lower()
    return cleaned if len(cleaned) >= 2 else None


def _is_self_store(name: str | None, brand_norm: str) -> bool:
    n = (name or "").lower()
    if brand_norm in n:
        return True
    return brand_norm.replace(" ", "") in n.replace(" ", "")


def _demand_factor(lat: float, lng: float) -> float | None:
    """clamp(baseline/demand, 0.6, 1.6) from living population, None if unavailable."""
    profile = fetch_living_pop_profile(lat, lng, DEMAND_RADIUS_M)
    if profile is None or not profile.weekday_hourly:
        return None
    demand = sum(profile.weekday_hourly.values()) / len(profile.weekday_hourly)
    if demand <= 0:
        return None
    return max(0.6, min(1.6, DEMAND_BASELINE / demand))


def _overlap_with_store(
    *,
    G: nx.Graph,
    segments: list[RoadSegment],
    reach_poly: dict,
    store: StoreRecord,
    minutes: int,
    mode: str,
) -> tuple[float | None, list[str]]:
    rival_iso = compute_isochrones(
        G=G,
        segments=segments,
        lat=store.lat,
        lng=store.lon,
        minutes_list=(minutes,),
        mode=mode,
    )
    rival_warnings = [
        f"{store.bizes_nm} 도달권 계산: {w}" for w in rival_iso.warnings
    ]
    if rival_iso.bands:
        return _overlap_pct(reach_poly, rival_iso.bands[0].polygon), rival_warnings
    return None, rival_warnings


def compute_cannibalization(
    *,
    G: nx.Graph | None,
    segments: list[RoadSegment] | None,
    lat: float,
    lng: float,
    inds_mcls_cd: str | None = None,
    inds_scls_cd: str | None = None,
    stores: list[StoreRecord],
    radius_m: int = 500,
    mode: str = "walk",
    minutes: int = 5,
    max_list: int = 100,
    brand: str | None = None,
) -> CannibalizationResult:
    reach_iso = compute_isochrones(
        G=G,
        segments=segments,
        lat=lat,
        lng=lng,
        minutes_list=(minutes,),
        mode=mode,
        stores=stores,
        inds_mcls_cd=inds_mcls_cd,
        inds_scls_cd=inds_scls_cd,
    )
    reach_poly = reach_iso.bands[0].polygon if reach_iso.bands else None
    mode_text = _mode_label(mode)
    brand_norm = _normalize_brand(brand)
    warnings: list[str] = list(reach_iso.warnings)

    # (CompetitorOut, StoreRecord) pairs so overlap lookups don't rely on
    # name-based re-matching against the full store list.
    comp_pairs: list[tuple[CompetitorOut, StoreRecord]] = []
    self_pairs: list[tuple[CompetitorOut, StoreRecord]] = []
    for store in stores:
        if store.lat is None or store.lon is None:
            continue
        if not store_matches_upjong(
            store, inds_mcls_cd=inds_mcls_cd, inds_scls_cd=inds_scls_cd
        ):
            continue
        dist = _haversine_m(lat, lng, store.lat, store.lon)
        if reach_poly and reach_poly.get("coordinates"):
            if not _point_in_polygon(store.lon, store.lat, reach_poly):
                continue
        elif dist > radius_m:
            continue
        out = CompetitorOut(
            name=store.bizes_nm,
            category=store.inds_scls_nm or store.inds_mcls_nm,
            distance_m=round(dist, 0),
            address=store.rdnm_adr,
            lat=store.lat,
            lng=store.lon,
        )
        if brand_norm and _is_self_store(store.bizes_nm, brand_norm):
            self_pairs.append((out, store))
        else:
            comp_pairs.append((out, store))
    comp_pairs.sort(key=lambda p: p[0].distance_m)
    self_pairs.sort(key=lambda p: p[0].distance_m)
    competitors = [c for c, _ in comp_pairs]
    self_competitors = [c for c, _ in self_pairs]
    total_count = len(competitors)
    self_total_count = len(self_competitors)

    # Sum-based saturating risk (competition only): each rival contributes
    # 100*exp(-d/120) and the sum saturates so 20 close rivals >> 1 close rival.
    saturation = sum(100.0 * math.exp(-c.distance_m / 120.0) for c in competitors)
    risk_raw = 100.0 * (1.0 - math.exp(-saturation / RISK_SATURATION_DIVISOR))

    # Demand normalization: the same rival count is less risky where living
    # population (demand) is far above the Seoul-typical baseline.
    demand_factor = _demand_factor(lat, lng)
    risk = round(max(0.0, min(100.0, risk_raw * (demand_factor or 1.0))), 1)
    if risk >= 70:
        label = "높음"
    elif risk >= 45:
        label = "중간"
    elif risk >= 20:
        label = "낮음"
    else:
        label = "매우 낮음"

    overlap_pct: float | None = None
    overlap_with: str | None = None
    if comp_pairs and (inds_mcls_cd or inds_scls_cd) and reach_poly:
        nearest, near_store = comp_pairs[0]
        overlap_pct, rival_warnings = _overlap_with_store(
            G=G,
            segments=segments,
            reach_poly=reach_poly,
            store=near_store,
            minutes=minutes,
            mode=mode,
        )
        if overlap_pct is not None:
            overlap_with = nearest.name
        for w in rival_warnings:
            if w not in warnings:
                warnings.append(w)

    self_overlap_pct: float | None = None
    if self_pairs and reach_poly:
        self_overlap_pct, self_rival_warnings = _overlap_with_store(
            G=G,
            segments=segments,
            reach_poly=reach_poly,
            store=self_pairs[0][1],
            minutes=minutes,
            mode=mode,
        )
        for w in self_rival_warnings:
            if w not in warnings:
                warnings.append(w)

    detail = f"{mode_text} {minutes}분 도달권 동일업종 {total_count}곳"
    if brand_norm is not None:
        detail += f" · 내 브랜드 {self_total_count}곳"
    if overlap_pct is not None:
        detail += f" · 최근접 {overlap_with}과 도달권 겹침 {overlap_pct}%"
    for w in warnings:
        detail += f" · {w}"

    return CannibalizationResult(
        risk_score=risk,
        risk_label=label,
        competitors=competitors[:max_list],
        overlap_pct=overlap_pct,
        overlap_with=overlap_with,
        overlap_recommended_max_pct=25.0,
        detail=detail,
        mode=mode,
        minutes=minutes,
        total_count=total_count,
        reach_polygon=reach_poly,
        self_total_count=self_total_count,
        self_competitors=self_competitors[:max_list],
        self_overlap_pct=self_overlap_pct,
        demand_factor=demand_factor,
        warnings=warnings,
    )
