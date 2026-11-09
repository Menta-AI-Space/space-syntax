"""Site score computation for the click-to-diagnose feature (MVP 1).

Pure logic only — no I/O. Takes precomputed space-syntax metrics, store lists,
transit/demand lookups and turns them into 0–100 sub-scores plus a weighted
total per business type.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import networkx as nx
from shapely.geometry import LineString, Point

from app.services.sbiz_stores import StoreRecord
from app.services.spatial_query import RoadSegment

# Scoring weights by business archetype (mapped from SBIZ middle upjong).
ARCHETYPE_PROFILES: dict[str, dict] = {
    "food_drink": {
        "weights": {
            "integration": 0.22,
            "choice": 0.12,
            "mean_depth": 0.10,
            "connectivity": 0.10,
            "transit": 0.15,
            "demand": 0.21,
            "competition": 0.10,
        },
    },
    "daily_retail": {
        "weights": {
            "integration": 0.15,
            "choice": 0.25,
            "mean_depth": 0.05,
            "connectivity": 0.15,
            "transit": 0.10,
            "demand": 0.20,
            "competition": 0.10,
        },
    },
    "medical": {
        "weights": {
            "integration": 0.20,
            "choice": 0.05,
            "mean_depth": 0.05,
            "connectivity": 0.05,
            "transit": 0.30,
            "demand": 0.30,
            "competition": 0.05,
        },
    },
    "education": {
        "weights": {
            "integration": 0.20,
            "choice": 0.05,
            "mean_depth": 0.05,
            "connectivity": 0.05,
            "transit": 0.20,
            "demand": 0.35,
            "competition": 0.10,
        },
    },
    "beauty_service": {
        "weights": {
            "integration": 0.20,
            "choice": 0.10,
            "mean_depth": 0.08,
            "connectivity": 0.08,
            "transit": 0.14,
            "demand": 0.30,
            "competition": 0.10,
        },
    },
    "general": {
        "weights": {
            "integration": 0.20,
            "choice": 0.15,
            "mean_depth": 0.10,
            "connectivity": 0.10,
            "transit": 0.15,
            "demand": 0.20,
            "competition": 0.10,
        },
    },
}

# Deprecated — preset slug → SBIZ codes (see sbiz_upjong.BUSINESS_TYPE_PRESETS)
BUSINESS_PROFILES: dict[str, dict] = {}


def _site_feature_weights(archetype: str) -> dict[str, float]:
    """Read site-score weights from the canonical industry profile (Phase 0 shim).

    Falls back to the hardcoded ARCHETYPE_PROFILES table when the archetype
    has no canonical profile (e.g. "general") or if profile loading fails
    for any reason, so this can never change compute_site_score's output —
    it only changes where the *same* weight values are sourced from.
    """
    try:
        from app.services.profile_resolver import ProfileResolver

        resolver = ProfileResolver.load()
        profile_id = resolver.resolve_by_archetype(archetype)
        if profile_id:
            weights = resolver.profiles[profile_id].get("analysis", {}).get("siteFeatures")
            if weights:
                return weights
    except Exception:
        pass
    return ARCHETYPE_PROFILES.get(archetype, ARCHETYPE_PROFILES["general"])["weights"]


METRIC_SUMMARY_LABELS: dict[str, str] = {
    "integration": "찾아오기 쉬움",
    "choice": "지나가는 길",
    "mean_depth": "도로망 중심성",
    "connectivity": "눈에 띄는 위치",
    "transit": "대중교통 접근",
    "demand": "배후수요",
    "competition": "경쟁 강도",
}
METRIC_LABELS: dict[str, str] = {
    "integration": "접근성",
    "choice": "자주 지나가게 되는 길",
    "mean_depth": "큰길에서 꺾어 들어오는 정도",
    "connectivity": "바로 연결된 길 수",
    "transit": "대중교통",
    "demand": "배후 수요",
    "competition": "경쟁 밀도",
}

METRIC_TOOLTIPS: dict[str, str] = {
    "integration": "이 길이 다른 곳에서 얼마나 쉽게 찾아올 수 있는 위치인지 보여줘요.",
    "choice": "우회·통행에 자주 쓰이는 축인지 보여줘요.",
    "mean_depth": (
        "어떤 길·공간에 가기까지 몇 번 꺾고, 얼마나 안쪽으로 들어가야 하는지 보는 값이에요. "
        "상위%가 낮을수록 도로망 중심에 가까워요."
    ),
    "connectivity": "이 길에서 바로 갈 수 있는 다른 길이 얼마나 많은지 보여줘요.",
    "transit": "가장 가까운 지하철역까지 거리예요.",
    "demand": "분석 반경(500m 또는 1km) 안 거주·종사 수요예요. SGIS 1km 격자 합산이 설정되면 격자 기반, 아니면 행정동 밀도 추정입니다.",
    "competition": "도보 5분(도로망) 도달권 내 동일업종 점포 수·밀도예요. '전체 점포'는 Analyze에서 고른 반경(500m 또는 1km) 안 SBIZ 점포 수입니다.",
}


@dataclass
class CategoryMatchResult:
    count: int
    match_method: str  # scls | mcls | disabled


@dataclass
class NearestSegment:
    index: int
    osm_id: int
    highway: str
    distance_m: float


@dataclass
class CornerInfo:
    is_corner: bool
    junction_degree: int


@dataclass
class SubScore:
    key: str
    label: str
    score: float | None  # 0–100 internal weighting; None = unavailable
    detail: str
    tooltip: str = ""
    summary_label: str = ""
    display: str = ""  # primary UI text (e.g. "상위 8%", "매우 높음")
    rank_top_pct: float | None = None  # SS metrics: lower = better


@dataclass
class SiteScoreResult:
    total_score: float
    grade: str
    business_type: str
    business_label: str
    verdict_one_liner: str = ""
    inds_lcls_cd: str | None = None
    inds_mcls_cd: str | None = None
    inds_scls_cd: str | None = None
    upjong_breadcrumb: str = ""
    subscores: list[SubScore] = field(default_factory=list)


def nearest_segment(
    segments: list[RoadSegment], lat: float, lng: float
) -> NearestSegment | None:
    """Find the road segment closest to the clicked point."""
    if not segments:
        return None
    point = Point(lng, lat)
    best_idx = -1
    best_dist = float("inf")
    for idx, seg in enumerate(segments):
        d = LineString(seg.coordinates).distance(point)
        if d < best_dist:
            best_dist = d
            best_idx = idx
    seg = segments[best_idx]
    # degrees → meters (equirectangular, fine at this scale)
    meters = best_dist * 111_320 * math.cos(math.radians(lat))
    return NearestSegment(
        index=best_idx,
        osm_id=seg.osm_id,
        highway=seg.highway,
        distance_m=round(meters, 1),
    )


def top_percent(values: list[float], value: float) -> float:
    """Return the value's rank as 'top X%' within values (lower = better rank)."""
    if not values:
        return 100.0
    higher = sum(1 for v in values if v > value)
    return round(100.0 * (higher + 1) / len(values), 1)


def bottom_percent(values: list[float], value: float) -> float:
    """For metrics where lower raw values are better (e.g. mean depth)."""
    if not values:
        return 100.0
    lower = sum(1 for v in values if v < value)
    return round(100.0 * (lower + 1) / len(values), 1)


def corner_info(G: nx.Graph, edge_map: dict[int, list[tuple]], seg_idx: int) -> CornerInfo:
    """A segment is a corner site when one of its junctions has degree >= 3."""
    max_degree = 0
    for u, v in edge_map.get(seg_idx, []):
        for node in (u, v):
            if G.has_node(node):
                max_degree = max(max_degree, G.degree(node))
    return CornerInfo(is_corner=max_degree >= 3, junction_degree=max_degree)


def store_matches_upjong(
    store: StoreRecord,
    *,
    inds_mcls_cd: str | None,
    inds_scls_cd: str | None = None,
) -> bool:
    """Match SBIZ official upjong codes."""
    if inds_scls_cd:
        return store.inds_scls_cd.strip() == inds_scls_cd
    if inds_mcls_cd:
        return store.inds_mcls_cd.strip() == inds_mcls_cd
    return False


def count_same_category(
    stores: list[StoreRecord],
    *,
    inds_mcls_cd: str | None,
    inds_scls_cd: str | None = None,
) -> int:
    return match_same_category(
        stores, inds_mcls_cd=inds_mcls_cd, inds_scls_cd=inds_scls_cd
    ).count


def match_same_category(
    stores: list[StoreRecord],
    *,
    inds_mcls_cd: str | None,
    inds_scls_cd: str | None = None,
    match_method: str | None = None,
) -> CategoryMatchResult:
    if match_method == "disabled" or (not inds_mcls_cd and not inds_scls_cd):
        return CategoryMatchResult(count=0, match_method="disabled")
    method = "scls" if inds_scls_cd else "mcls"
    count = sum(
        1
        for s in stores
        if store_matches_upjong(
            s, inds_mcls_cd=inds_mcls_cd, inds_scls_cd=inds_scls_cd
        )
    )
    return CategoryMatchResult(count=count, match_method=method)


def nearest_same_category_distance_m(
    stores: list[StoreRecord],
    *,
    lat: float,
    lng: float,
    inds_mcls_cd: str | None = None,
    inds_scls_cd: str | None = None,
    match_method: str | None = None,
) -> float | None:
    """Distance to the closest same-category store (equirectangular metres)."""
    if match_method == "disabled" or (not inds_mcls_cd and not inds_scls_cd):
        return None
    best: float | None = None
    for s in stores:
        if s.lat is None or s.lon is None:
            continue
        if not store_matches_upjong(
            s, inds_mcls_cd=inds_mcls_cd, inds_scls_cd=inds_scls_cd
        ):
            continue
        dy = (s.lat - lat) * 111_320
        dx = (s.lon - lng) * 111_320 * math.cos(math.radians(lat))
        d = math.hypot(dx, dy)
        if best is None or d < best:
            best = d
    return round(best, 1) if best is not None else None


#: 거리별 접근성 계수 — (거리 m, 계수). 사이는 선형 보간.
#: 선형 감쇠는 도보 현실과 다르다. 3분(250m)까지는 사실상 역 앞이고,
#: 10분(800m)을 넘어가면 "역세권"으로 안 친다. 그 구간을 급하게 떨어뜨린다.
_TRANSIT_DISTANCE_CURVE: tuple[tuple[float, float], ...] = (
    (250.0, 1.00),
    (500.0, 0.85),
    (800.0, 0.55),
    (1200.0, 0.20),
    (1500.0, 0.00),
)

#: 승하차 규모 계수의 하한/상한과 로그 구간.
#: 서울 지하철 일평균 승하차는 9명~24만명으로 극단적으로 치우쳐 있어
#: (중앙값 1.9만) 선형으로 쓰면 대형역만 살아남는다. 로그로 편다.
_RIDERSHIP_LOG_MIN = 3.0   # 1천명
_RIDERSHIP_LOG_MAX = 5.4   # 25만명
_RIDERSHIP_FLOOR = 0.25
_BUS_MAX_BONUS = 18.0
_BUS_SATURATION = 12.0     # 정류장 12개면 버스 보정 만점


def _transit_distance_factor(distance_m: float) -> float:
    if distance_m <= _TRANSIT_DISTANCE_CURVE[0][0]:
        return 1.0
    prev_d, prev_f = _TRANSIT_DISTANCE_CURVE[0]
    for d, f in _TRANSIT_DISTANCE_CURVE[1:]:
        if distance_m <= d:
            span = d - prev_d
            return prev_f + (f - prev_f) * ((distance_m - prev_d) / span) if span else f
        prev_d, prev_f = d, f
    return 0.0


def _ridership_factor(daily_total: int | None) -> float:
    """역 규모 계수 0.25~1.0. 데이터가 없으면 중립값(0.75)."""
    if daily_total is None:
        # 경기권 역 등 미적재 구간에서 점수가 부당하게 깎이면 안 된다.
        return 0.75
    if daily_total <= 0:
        return _RIDERSHIP_FLOOR
    span = _RIDERSHIP_LOG_MAX - _RIDERSHIP_LOG_MIN
    normalized = (math.log10(daily_total) - _RIDERSHIP_LOG_MIN) / span
    return round(
        min(1.0, max(_RIDERSHIP_FLOOR, _RIDERSHIP_FLOOR + normalized * (1 - _RIDERSHIP_FLOOR))),
        4,
    )


def _transit_detail(
    station_name: str | None,
    distance_m: float | None,
    daily_riders: int | None,
    line_count: int | None,
    bus_stops: int,
) -> str:
    """무엇 때문에 이 점수가 나왔는지 사용자가 읽을 수 있게 적는다."""
    parts: list[str] = []
    if station_name and distance_m is not None:
        head = f"{station_name} {distance_m:.0f}m"
        if line_count and line_count > 1:
            head += f" · {line_count}개 노선 환승"
        parts.append(head)
    if daily_riders:
        parts.append(f"이용객 일 {daily_riders:,}명")
    if bus_stops:
        parts.append(f"버스정류장 {bus_stops}개")
    return " · ".join(parts) if parts else "지하철역 정보 없음"


def transit_score(
    nearest_station_m: float | None,
    daily_riders: int | None = None,
    bus_stops: int = 0,
) -> float | None:
    """대중교통 접근성 0~100.

    지하철은 "가까운가"와 "그 역이 큰가"를 곱해서 본다. 거리만 보면 강남역
    앞과 한적한 단일노선 역이 똑같이 만점이 나오는데, 상권 가치는 전혀
    다르기 때문이다. 버스는 지하철이 약한 자리를 메우는 보정으로 더한다
    (서울 외곽·주거지는 버스가 실제 주 교통수단이다).
    """
    if nearest_station_m is None and not bus_stops:
        return None

    subway = 0.0
    if nearest_station_m is not None:
        subway = 100.0 * _transit_distance_factor(nearest_station_m) * _ridership_factor(daily_riders)

    # 버스는 남은 여유분에만 채워 넣는다. 그냥 더하면 역세권 자리는 전부
    # 상한 100에 걸려서, 애초에 고치려던 "강남역=신촌역=100점" 문제가
    # 그대로 돌아온다. 버스가 실제로 의미 있는 건 지하철이 먼 자리다.
    bus = 0.0
    if bus_stops > 0:
        headroom = (100.0 - subway) / 100.0
        bus = _BUS_MAX_BONUS * min(1.0, bus_stops / _BUS_SATURATION) * headroom

    return round(min(100.0, subway + bus), 1)


def demand_score(
    population: int | None,
    workers: int | None,
    demographic_fit: float | None = None,
) -> float | None:
    """Heuristic: 반경 추정 인구 2만 + 종사자 1만이면 만점에 수렴."""
    if population is None and workers is None and demographic_fit is None:
        return None
    pop = population or 0
    wrk = workers or 0
    base = 100.0 * (1 - math.exp(-(pop / 20_000 + wrk / 10_000)))
    if demographic_fit is not None:
        base = base * 0.7 + demographic_fit * 0.3
    return round(min(base, 100.0), 1)


def _age_band_pct(age_bands: dict[str, float], *labels: str) -> float:
    return sum(age_bands.get(label, 0.0) for label in labels)


def demographic_fit_score(
    archetype: str,
    age_bands: dict[str, float],
) -> tuple[float | None, str]:
    """Rule-based fit of age distribution to business archetype (0–100)."""
    if not age_bands:
        return None, "연령 데이터 없음"

    profiles: dict[str, tuple[float, str]] = {
        "food_drink": (_age_band_pct(age_bands, "20대", "30대"), "20–30대 비중"),
        "daily_retail": (
            _age_band_pct(age_bands, "20대", "30대", "40대"),
            "20–40대 수요",
        ),
        "medical": (_age_band_pct(age_bands, "40대", "50대", "60대"), "40–60대 비중"),
        "education": (
            _age_band_pct(age_bands, "10세 미만", "10대"),
            "10–19세·학령인구",
        ),
        "beauty_service": (
            _age_band_pct(age_bands, "20대", "30대", "40대"),
            "20–40대 수요",
        ),
        "general": (_age_band_pct(age_bands, "20대", "30대", "40대"), "전 연령 균형"),
    }
    target_pct, label = profiles.get(archetype, profiles["general"])
    score = round(min(100.0, target_pct * 2.5), 1)
    if score >= 75:
        grade = "매우 적합"
    elif score >= 55:
        grade = "적합"
    elif score >= 35:
        grade = "보통"
    else:
        grade = "낮음"
    return score, f"{label} {target_pct:.0f}% → {grade}"


def competition_density_per_km2(
    same_category_count: int,
    radius_m: int | None = None,
    *,
    area_m2: float | None = None,
) -> float:
    if area_m2 is not None and area_m2 > 0:
        area_km2 = area_m2 / 1_000_000
    elif radius_m is not None and radius_m > 0:
        area_km2 = math.pi * (radius_m / 1000) ** 2
    else:
        return 0.0
    return same_category_count / area_km2


def competition_density_display_score(density_per_km2: float) -> float:
    """0–100 display score: higher = more competition (경쟁 밀도)."""
    return round(min(100.0, density_per_km2 * 1.25), 1)


def competition_advantage_score(density_display: float) -> float:
    """For weighted total only — lower competition is better."""
    return round(100.0 - density_display, 1)


def competition_score(same_category_count: int, radius_m: int) -> float:
    """Deprecated alias: returns advantage score for tests expecting old API."""
    density = competition_density_per_km2(same_category_count, radius_m)
    return competition_advantage_score(competition_density_display_score(density))


def _ss_detail(top_pct: float, extra: str = "") -> str:
    base = f"상위 {top_pct}%"
    return f"{base} · {extra}" if extra else base


def depth_display(top_pct: float) -> str:
    """깊이 지표의 표시 문구.

    이름("안쪽으로 찾아 들어와야 하는 정도")은 **깊을수록 나쁜 쪽**을 가리키는데
    상위%는 **낮을수록 좋다**(중심에 가깝다). 둘을 그냥 붙이면 "상위 16.3%"가
    정반대로 읽힌다 — 어느 쪽이 좋은지 문구가 직접 말해야 한다.
    """
    where = "얕은 편" if top_pct <= 33 else "깊은 편" if top_pct >= 67 else "중간"
    # "낮을수록 좋음"까지 적는다. 얕고 깊은 게 뭐가 좋은 건지는 이 지표를 처음
    # 보는 사람에겐 자명하지 않다 — 상가는 얕을수록 지나가다 들어오는 손님이 는다.
    return f"{format_rank_display(top_pct)} · {where} (낮을수록 좋음)"


def format_rank_display(top_pct: float) -> str:
    return f"상위 {top_pct}%"


def score_qualitative_label(score: float) -> str:
    if score >= 85:
        return "매우 좋음"
    if score >= 70:
        return "좋음"
    if score >= 55:
        return "무난"
    if score >= 40:
        return "약함"
    return "매우 약함"


def competition_intensity_label(density_per_km2: float) -> str:
    """Fixed bands — no per-location tuning."""
    if density_per_km2 >= 50:
        return "매우 높음"
    if density_per_km2 >= 25:
        return "높음"
    if density_per_km2 >= 10:
        return "보통"
    if density_per_km2 >= 3:
        return "낮음"
    return "매우 낮음"


def build_verdict_one_liner(
    *,
    integration_top_pct: float,
    choice_top_pct: float,
    competition_intensity: str | None,
) -> str:
    access_good = integration_top_pct <= 20 and choice_top_pct <= 25
    access_ok = integration_top_pct <= 40 and choice_top_pct <= 40
    comp_high = competition_intensity in ("높음", "매우 높음")
    comp_low = competition_intensity in ("낮음", "매우 낮음")

    if access_good and comp_high:
        return "유입 조건은 좋지만 경쟁이 매우 강한 입지입니다."
    if access_good and comp_low:
        return "유입과 경쟁 모두 유리한 편인 입지입니다."
    if access_ok and comp_high:
        return "배후 수요는 있으나 경쟁이 치열한 입지입니다."
    if not access_ok and comp_high:
        return "유입 조건과 경쟁 모두 까다로운 입지입니다."
    if access_good:
        return "유입 조건이 좋은 편인 입지입니다."
    if access_ok:
        return "조건이 무난한 입지입니다."
    return "입지 조건을 종합적으로 검토할 필요가 있습니다."


def _grade(total: float) -> str:
    if total >= 80:
        return "매우 좋음"
    if total >= 65:
        return "좋음"
    if total >= 50:
        return "보통"
    if total >= 35:
        return "주의"
    return "부적합"


def compute_site_score(
    *,
    archetype: str,
    business_label: str,
    business_type: str = "custom",
    inds_lcls_cd: str | None = None,
    inds_mcls_cd: str | None = None,
    inds_scls_cd: str | None = None,
    upjong_breadcrumb: str = "",
    match_method: str = "disabled",
    integration_top_pct: float,
    choice_top_pct: float,
    mean_depth_top_pct: float,
    connectivity_top_pct: float,
    connectivity_degree: int,
    nearest_station_m: float | None,
    station_name: str | None,
    station_daily_riders: int | None = None,
    station_line_count: int | None = None,
    bus_stops_300m: int = 0,
    same_category_count: int,
    total_store_count: int,
    population: int | None,
    workers: int | None,
    adm_name: str | None,
    radius_m: int,
    demographic_fit: float | None = None,
    demographic_fit_label: str | None = None,
    competition_area_m2: float | None = None,
    # 동일업종 집계가 어느 범위에서 나온 값인지. 보통은 도보 5분 도달권이지만,
    # 그 지점에서 보행 도로망이 끊겨 도달권을 못 믿을 때는 호출자가 반경 기준
    # 집계로 바꿔 넘긴다 — 그러면 설명 문구도 같이 바뀌어야 한다.
    competition_scope: str = "walk5",
) -> SiteScoreResult:
    weights = _site_feature_weights(archetype)

    integration_score = round(100.0 - integration_top_pct, 1)
    choice_score = round(100.0 - choice_top_pct, 1)
    depth_score = round(100.0 - mean_depth_top_pct, 1)
    connectivity_score = round(100.0 - connectivity_top_pct, 1)
    t_score = transit_score(nearest_station_m, station_daily_riders, bus_stops_300m)
    d_score = demand_score(population, workers, demographic_fit)
    demand_detail = (
        f"반경 {radius_m}m 추정 · {adm_name or '행정동'} 인구 {population:,}명 · 종사자 {workers:,}명"
        if d_score is not None and population is not None and workers is not None
        else "인구 데이터 없음 (SGIS 키 필요)"
    )
    if demographic_fit_label:
        demand_detail += f" · {demographic_fit_label}"

    match_method = match_method if match_method != "disabled" else (
        "disabled" if not inds_mcls_cd and not inds_scls_cd else ("scls" if inds_scls_cd else "mcls")
    )
    density_km2 = competition_density_per_km2(
        same_category_count,
        radius_m=radius_m if competition_area_m2 is None else None,
        area_m2=competition_area_m2,
    )
    c_display = (
        competition_density_display_score(density_km2)
        if match_method != "disabled"
        else None
    )
    comp_intensity = (
        competition_intensity_label(density_km2) if c_display is not None else None
    )

    subscores = [
        SubScore(
            "integration",
            METRIC_LABELS["integration"],
            integration_score,
            _ss_detail(integration_top_pct),
            METRIC_TOOLTIPS["integration"],
            METRIC_SUMMARY_LABELS["integration"],
            format_rank_display(integration_top_pct),
            integration_top_pct,
        ),
        SubScore(
            "choice",
            METRIC_LABELS["choice"],
            choice_score,
            _ss_detail(choice_top_pct),
            METRIC_TOOLTIPS["choice"],
            METRIC_SUMMARY_LABELS["choice"],
            format_rank_display(choice_top_pct),
            choice_top_pct,
        ),
        SubScore(
            "mean_depth",
            METRIC_LABELS["mean_depth"],
            depth_score,
            _ss_detail(mean_depth_top_pct, "낮을수록 중심"),
            METRIC_TOOLTIPS["mean_depth"],
            METRIC_SUMMARY_LABELS["mean_depth"],
            depth_display(mean_depth_top_pct),
            mean_depth_top_pct,
        ),
        SubScore(
            "connectivity",
            METRIC_LABELS["connectivity"],
            connectivity_score,
            _ss_detail(
                connectivity_top_pct,
                f"연결 {connectivity_degree}갈래",
            ),
            METRIC_TOOLTIPS["connectivity"],
            METRIC_SUMMARY_LABELS["connectivity"],
            format_rank_display(connectivity_top_pct),
            connectivity_top_pct,
        ),
        SubScore(
            "transit",
            METRIC_LABELS["transit"],
            t_score,
            _transit_detail(
                station_name,
                nearest_station_m,
                station_daily_riders,
                station_line_count,
                bus_stops_300m,
            )
            if t_score is not None
            else "지하철역 정보 없음",
            METRIC_TOOLTIPS["transit"],
            METRIC_SUMMARY_LABELS["transit"],
            score_qualitative_label(t_score) if t_score is not None else "—",
        ),
        SubScore(
            "demand",
            METRIC_LABELS["demand"],
            d_score,
            demand_detail,
            METRIC_TOOLTIPS["demand"],
            METRIC_SUMMARY_LABELS["demand"],
            score_qualitative_label(d_score) if d_score is not None else "—",
        ),
        SubScore(
            "competition",
            METRIC_LABELS["competition"],
            c_display,
            (
                (
                    f"도보 5분 내 동일업종 {same_category_count}곳 · 전체 점포 {total_store_count}곳 (분석 반경 {radius_m}m)"
                    if competition_scope == "walk5"
                    else f"반경 {radius_m}m 내 동일업종 {same_category_count}곳 · 전체 점포 {total_store_count}곳 (도보 도달권이 끊겨 반경 기준)"
                )
                if c_display is not None
                else f"업종 미선택 — 동일업종 집계 안 함 · 전체 점포 {total_store_count}곳 (분석 반경 {radius_m}m)"
            ),
            METRIC_TOOLTIPS["competition"],
            METRIC_SUMMARY_LABELS["competition"],
            comp_intensity or "—",
        ),
    ]

    # 가용한 지표만으로 가중치를 재정규화해 총점 계산
    weighted_sum = 0.0
    weight_total = 0.0
    for ss in subscores:
        if ss.score is None:
            continue
        w = weights.get(ss.key, 0.0)
        val = ss.score
        if ss.key == "competition":
            val = competition_advantage_score(val)
        weighted_sum += val * w
        weight_total += w
    total = round(weighted_sum / weight_total, 1) if weight_total > 0 else 0.0

    return SiteScoreResult(
        total_score=total,
        grade=_grade(total),
        business_type=business_type,
        business_label=business_label,
        verdict_one_liner=build_verdict_one_liner(
            integration_top_pct=integration_top_pct,
            choice_top_pct=choice_top_pct,
            competition_intensity=comp_intensity,
        ),
        inds_lcls_cd=inds_lcls_cd,
        inds_mcls_cd=inds_mcls_cd,
        inds_scls_cd=inds_scls_cd,
        upjong_breadcrumb=upjong_breadcrumb or business_label,
        subscores=subscores,
    )
