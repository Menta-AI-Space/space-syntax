"""Territory-based cannibalization: competitor reach zones + entry simulation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import networkx as nx
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from app.services.isochrone import (
    MODE_LABEL_KO,
    MODE_SPEED,
    competition_fetch_radius_m,
    compute_isochrones,
    point_in_reach_polygon,
)
from app.services.sbiz_stores import StoreRecord
from app.services.site_score import store_matches_upjong
from app.services.spatial_query import RoadSegment

MODEL_FIT_REACH_MODE = "walk"
MODEL_FIT_REACH_MINUTES = 15
DELIVERY_TERRITORY_MODE = "moto"
DELIVERY_TERRITORY_MINUTES = 15
MAX_COMPETITOR_TERRITORIES = 10


@dataclass
class TerritoryPatch:
    brand: str
    store_name: str
    lat: float
    lng: float
    mode: str
    minutes: int
    polygon: dict
    area_m2: float
    is_new_entrant: bool = False


@dataclass
class TerritoryZone:
    zone_type: str
    label: str
    polygon: dict
    area_m2: float
    brands: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class TerritoryDemandProfile:
    population: int | None = None
    workers: int | None = None
    households: int | None = None
    youth_pct: float | None = None
    middle_age_pct: float | None = None
    senior_pct: float | None = None
    apartment_pct: float | None = None
    single_household_note: str = ""
    demand_notes: list[str] = field(default_factory=list)


@dataclass
class TerritoryAnalysisResult:
    delivery_mode: str
    delivery_minutes: int
    model_fit_mode: str
    model_fit_minutes: int
    before_territories: list[TerritoryPatch]
    after_territories: list[TerritoryPatch]
    zones: list[TerritoryZone]
    demand: TerritoryDemandProfile
    brand_fit_headline: str
    brand_fit_bullets: list[str]
    narrative: list[str]
    warnings: list[str] = field(default_factory=list)


def normalize_brand(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return "미상"
    text = re.sub(r"\s+(역점|역|본점|직영점|가맹점|지점).*$", "", text)
    parts = text.split()
    return parts[0] if parts else text


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _to_shape(polygon: dict):
    if not polygon or not polygon.get("coordinates"):
        return None
    try:
        geom = shape(polygon)
        return geom if not geom.is_empty else None
    except Exception:
        return None


def _to_geojson(geom) -> dict:
    if geom is None or geom.is_empty:
        return {"type": "Polygon", "coordinates": []}
    return mapping(geom)


def _area_m2(geom) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    mlat = geom.centroid.y
    scale = 111_320 * math.cos(math.radians(mlat))
    projected = type(geom)([(x * scale, y * 111_320) for x, y in geom.exterior.coords])
    return float(projected.area) if hasattr(projected, "area") else 0.0


def _iso_polygon(
    G: nx.Graph | None,
    segments: list[RoadSegment] | None,
    lat: float,
    lng: float,
    *,
    mode: str,
    minutes: int,
) -> dict | None:
    result = compute_isochrones(
        G=G,
        segments=segments,
        lat=lat,
        lng=lng,
        minutes_list=(minutes,),
        mode=mode,
    )
    if not result.bands:
        return None
    return result.bands[0].polygon


def _pct_from_bands(age_bands, *labels: str) -> float | None:
    total = 0.0
    found = False
    for band in age_bands:
        if band.band in labels:
            total += band.pct
            found = True
    return total if found else None


def _build_demand_profile(demand_profile) -> TerritoryDemandProfile:
    if demand_profile is None:
        return TerritoryDemandProfile(demand_notes=["배후 수요 데이터 없음"])
    youth = _pct_from_bands(demand_profile.age_bands, "20대", "30대")
    mid = _pct_from_bands(demand_profile.age_bands, "40대", "50대")
    senior = _pct_from_bands(demand_profile.age_bands, "60대", "70세+")
    apt = demand_profile.housing.apartment_pct if demand_profile.housing else None
    off = demand_profile.housing.officetel_pct if demand_profile.housing else None
    notes: list[str] = []
    if demand_profile.population:
        notes.append(f"행정동 거주 {demand_profile.population:,}명")
    if demand_profile.workers:
        notes.append(f"종사자 {demand_profile.workers:,}명")
    if youth is not None:
        notes.append(f"20~30대 {youth:.0f}%")
    if mid is not None:
        notes.append(f"40~50대 {mid:.0f}%")
    if apt is not None:
        notes.append(f"아파트 {apt:.0f}% · 오피스텔 {(off or 0):.0f}%")
    return TerritoryDemandProfile(
        population=demand_profile.population,
        workers=demand_profile.workers,
        households=demand_profile.households,
        youth_pct=youth,
        middle_age_pct=mid,
        senior_pct=senior,
        apartment_pct=apt,
        demand_notes=notes,
    )


def _brand_fit(
    *,
    brand_label: str,
    archetype: str,
    demand: TerritoryDemandProfile,
    absorbed_count: int,
    gap_area_m2: float,
    overlap_area_m2: float,
) -> tuple[str, list[str]]:
    label = brand_label or "해당 브랜드"
    bullets: list[str] = []
    youth = demand.youth_pct or 0
    workers = demand.workers or 0

    if archetype == "food_drink" or "샐러드" in label or "샐러디" in label:
        bullets.append(
            f"{label}는 20~30대·직장인·점심 배달 수요와 맞는지가 핵심입니다."
        )
        if youth >= 25:
            bullets.append("20~30대 비중이 높아 건강식·점심 수요 잠재력이 있습니다.")
        else:
            bullets.append("2030 비중이 낮아 점심·배달보다 직장인·오피스 상권 연계가 필요할 수 있습니다.")
        if workers >= 5000:
            bullets.append("종사자 밀도가 높아 평일 점심 수요를 기대할 수 있습니다.")
        if demand.apartment_pct and demand.apartment_pct >= 35:
            bullets.append("아파트 비중이 높아 주거 배달 수요도 함께 검토하세요.")
    elif archetype == "medical":
        bullets.append("연령·주거 밀도·접근성·기존 의료시설 밀도가 핵심입니다.")
        if (demand.senior_pct or 0) >= 20:
            bullets.append("고령 인구 비중이 높아 반복 방문 수요가 기대됩니다.")
    elif archetype == "cafe" or "카페" in label:
        bullets.append("유동·체류·직장인/학생·보행 접근성이 핵심입니다.")
        if workers >= 4000:
            bullets.append("직장인 밀도가 높아 평일 오전·점심 수요가 유리합니다.")
    else:
        bullets.append(f"{label} 업종 특성에 맞는 연령·주거·유동 수요를 확인하세요.")

    if absorbed_count > 0:
        bullets.append(
            f"신규 진입 시 기존 {absorbed_count}개 브랜드 권역과 겹쳐 잠식 가능성이 있습니다."
        )
    if gap_area_m2 > 50_000:
        bullets.append("아직 경쟁이 약한 공백 권역이 있어 신규 수요를 가져올 여지가 있습니다.")
    if overlap_area_m2 > 80_000:
        bullets.append("여러 브랜드 권역이 겹치는 과밀 구간이 있어 가격·차별화 경쟁이 치열할 수 있습니다.")

    headline = (
        f"{label} 기준, 이 입지는 "
        + ("공백 권역을 노릴 수 있는 편" if gap_area_m2 > overlap_area_m2 else "기존 브랜드와 권역 경쟁이 있는 편")
        + "입니다."
    )
    return headline, bullets


def compute_territory_analysis(
    *,
    G: nx.Graph | None,
    segments: list[RoadSegment] | None,
    lat: float,
    lng: float,
    stores: list[StoreRecord],
    inds_mcls_cd: str | None = None,
    inds_scls_cd: str | None = None,
    archetype: str = "food_drink",
    brand_label: str = "",
    demand_profile=None,
    delivery_mode: str = DELIVERY_TERRITORY_MODE,
    delivery_minutes: int = DELIVERY_TERRITORY_MINUTES,
) -> TerritoryAnalysisResult:
    warnings: list[str] = []
    candidate_poly = _iso_polygon(
        G, segments, lat, lng, mode=delivery_mode, minutes=delivery_minutes
    )
    if not candidate_poly or not candidate_poly.get("coordinates"):
        warnings.append(
            f"{MODE_LABEL_KO.get(delivery_mode, delivery_mode)} {delivery_minutes}분 "
            "도달권을 계산하지 못했습니다."
        )
        return TerritoryAnalysisResult(
            delivery_mode=delivery_mode,
            delivery_minutes=delivery_minutes,
            model_fit_mode=MODEL_FIT_REACH_MODE,
            model_fit_minutes=MODEL_FIT_REACH_MINUTES,
            before_territories=[],
            after_territories=[],
            zones=[],
            demand=_build_demand_profile(demand_profile),
            brand_fit_headline="도달권 계산 실패로 권역 분석을 완료하지 못했습니다.",
            brand_fit_bullets=[],
            narrative=["도로망 연결 또는 반경 데이터를 확인하세요."],
            warnings=warnings,
        )

    candidate_shape = _to_shape(candidate_poly)
    competitors: list[StoreRecord] = []
    for store in stores:
        if store.lat is None or store.lon is None:
            continue
        if not store_matches_upjong(
            store, inds_mcls_cd=inds_mcls_cd, inds_scls_cd=inds_scls_cd
        ):
            continue
        if not point_in_reach_polygon(store.lon, store.lat, candidate_poly):
            continue
        competitors.append(store)
    competitors.sort(
        key=lambda s: _haversine_m(lat, lng, s.lat or lat, s.lon or lng)
    )

    before: list[TerritoryPatch] = []
    seen_stores: set[str] = set()
    for store in competitors[:MAX_COMPETITOR_TERRITORIES]:
        key = f"{store.bizes_nm}:{store.lat}:{store.lon}"
        if key in seen_stores:
            continue
        seen_stores.add(key)
        poly = _iso_polygon(
            G,
            segments,
            store.lat,
            store.lon,
            mode=delivery_mode,
            minutes=delivery_minutes,
        )
        if not poly or not poly.get("coordinates"):
            continue
        geom = _to_shape(poly)
        before.append(
            TerritoryPatch(
                brand=normalize_brand(store.bizes_nm),
                store_name=store.bizes_nm,
                lat=store.lat,
                lng=store.lon,
                mode=delivery_mode,
                minutes=delivery_minutes,
                polygon=poly,
                area_m2=round(_area_m2(geom), 1),
            )
        )

    new_brand = brand_label or "신규 입지"
    after = list(before) + [
        TerritoryPatch(
            brand=new_brand,
            store_name=f"{new_brand} (후보)",
            lat=lat,
            lng=lng,
            mode=delivery_mode,
            minutes=delivery_minutes,
            polygon=candidate_poly,
            area_m2=round(_area_m2(candidate_shape), 1),
            is_new_entrant=True,
        )
    ]

    zones: list[TerritoryZone] = []
    existing_shapes = [_to_shape(t.polygon) for t in before]
    existing_shapes = [s for s in existing_shapes if s is not None]
    union_existing = unary_union(existing_shapes) if existing_shapes else None

    absorbed_count = 0
    for patch in before:
        comp_shape = _to_shape(patch.polygon)
        if comp_shape is None or candidate_shape is None:
            continue
        overlap = comp_shape.intersection(candidate_shape)
        if overlap.is_empty or overlap.area <= 0:
            continue
        overlap_area = _area_m2(overlap)
        comp_area = max(_area_m2(comp_shape), 1.0)
        absorbed_pct = round(100.0 * overlap_area / comp_area, 1)
        if absorbed_pct < 5:
            continue
        absorbed_count += 1
        zones.append(
            TerritoryZone(
                zone_type="absorbed",
                label=f"{patch.brand} → {new_brand} 흡수 가능",
                polygon=_to_geojson(overlap),
                area_m2=round(overlap_area, 1),
                brands=[patch.brand, new_brand],
                note=f"기존 {patch.store_name} 권역의 {absorbed_pct}%가 후보 권역과 겹칩니다.",
            )
        )

    candidate_overlap_area = 0.0
    if union_existing is not None and candidate_shape is not None:
        overlap_all = candidate_shape.intersection(union_existing)
        if not overlap_all.is_empty:
            candidate_overlap_area = round(_area_m2(overlap_all), 1)
            zones.append(
                TerritoryZone(
                    zone_type="overlap",
                    label="과밀 경쟁 권역",
                    polygon=_to_geojson(overlap_all),
                    area_m2=candidate_overlap_area,
                    brands=sorted({t.brand for t in before}),
                    note="여러 기존 브랜드와 후보 권역이 동시에 겹치는 구간입니다.",
                )
            )
        gap = candidate_shape.difference(union_existing)
        if not gap.is_empty:
            zones.append(
                TerritoryZone(
                    zone_type="gap",
                    label="공백 권역 (미커버)",
                    polygon=_to_geojson(gap),
                    area_m2=round(_area_m2(gap), 1),
                    brands=[new_brand],
                    note="기존 경쟁 브랜드 배달/영업권이 충분히 닿지 않는 구간입니다.",
                )
            )

    for i, shape_a in enumerate(existing_shapes):
        for j in range(i + 1, len(existing_shapes)):
            shape_b = existing_shapes[j]
            inter = shape_a.intersection(shape_b)
            if inter.is_empty:
                continue
            area = _area_m2(inter)
            if area < 30_000:
                continue
            zones.append(
                TerritoryZone(
                    zone_type="overlap",
                    label="기존 브랜드 간 겹침",
                    polygon=_to_geojson(inter),
                    area_m2=round(area, 1),
                    brands=[before[i].brand, before[j].brand],
                    note="진입 전부터 경쟁 브랜드 권역이 겹치는 구간입니다.",
                )
            )

    gap_area = sum(z.area_m2 for z in zones if z.zone_type == "gap")
    # 후보지와 무관한 '기존 브랜드끼리 겹침'(zone_type="overlap", 위 이중 루프에서
    # 추가됨)은 후보 권역의 경쟁 강도와 관계없으므로 headline·narrative가 보는
    # overlap_area에는 candidate_shape와의 겹침(candidate_overlap_area)만 넣는다.
    overlap_area = candidate_overlap_area
    demand = _build_demand_profile(demand_profile)
    headline, fit_bullets = _brand_fit(
        brand_label=new_brand,
        archetype=archetype,
        demand=demand,
        absorbed_count=absorbed_count,
        gap_area_m2=gap_area,
        overlap_area_m2=overlap_area,
    )

    mode_ko = MODE_LABEL_KO.get(delivery_mode, delivery_mode)
    narrative = [
        f"현재 동일업종 {len(before)}곳의 {mode_ko} {delivery_minutes}분 권역을 추정했습니다.",
        f"후보 입지({new_brand}) 진입 시 {absorbed_count}곳과 권역이 겹칠 수 있습니다.",
    ]
    if gap_area > 0:
        narrative.append(
            f"약 {int(gap_area / 10_000)}만㎡ 규모 공백 권역에서 신규 수요를 가져올 여지가 있습니다."
        )
    else:
        narrative.append("후보 권역 대부분이 기존 브랜드에 이미 커버되어 차별화가 중요합니다.")
    if overlap_area > gap_area:
        narrative.append("겹치는 과밀 구간이 넓어 '어디서 손님을 가져올까'가 핵심 질문입니다.")
    else:
        narrative.append("아직 비어 있는 권역이 있어 선점 전략이 유효할 수 있습니다.")

    return TerritoryAnalysisResult(
        delivery_mode=delivery_mode,
        delivery_minutes=delivery_minutes,
        model_fit_mode=MODEL_FIT_REACH_MODE,
        model_fit_minutes=MODEL_FIT_REACH_MINUTES,
        before_territories=before,
        after_territories=after,
        zones=zones,
        demand=demand,
        brand_fit_headline=headline,
        brand_fit_bullets=fit_bullets,
        narrative=narrative,
        warnings=warnings,
    )


def competition_fetch_radius_for_territory(
    base_radius_m: int,
    delivery_mode: str = DELIVERY_TERRITORY_MODE,
    delivery_minutes: int = DELIVERY_TERRITORY_MINUTES,
) -> int:
    return competition_fetch_radius_m(base_radius_m, delivery_mode, delivery_minutes)
