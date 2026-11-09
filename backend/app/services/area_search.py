"""지역 안에서 '열 만한 자리'를 기계적으로 뽑아낸다.

설계 원칙(decision.py와 동일):
**후보 지점은 LLM이 지어내는 것이 아니라, 이미 계산된 도로망 접근성 순위에서
결정론적으로 뽑는다.** 주소는 지어내지 않고 뽑힌 좌표를 역지오코딩해서 붙인다.
뽑힌 뒤의 점수·판정은 /api/decide와 **같은 경로**(decision.compare_candidates)로
매기므로, 같은 지점을 비교 화면에서 다시 봐도 같은 점수가 나온다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.services.spatial_query import RoadSegment

#: 후보 개수 상한. 5곳을 넘기면 사용자가 비교를 못 하고, /api/decide의
#: 후보 상한(5)과도 어긋난다.
MAX_CANDIDATES = 5

#: 후보 사이 최소 간격(m). 이보다 가까우면 같은 골목의 다른 번지일 뿐이라
#: 비교의 의미가 없다. 도보 2분 남짓 거리.
MIN_SPACING_M = 150.0

#: 지역 검색 반경(m). '○○구'처럼 넓은 행정구역과 '신촌'처럼 좁은 지역명을
#: 구분한다. 도로망 조회량이 이 값의 제곱에 비례하므로 무한정 넓힐 수 없다.
DISTRICT_SEARCH_RADIUS_M = 1200
NEIGHBORHOOD_SEARCH_RADIUS_M = 800

#: 지역 질의가 이 접미사로 끝나면 넓은 행정구역으로 본다.
DISTRICT_SUFFIXES = ("구", "시", "군")

#: 점포가 면할 수 없는 도로 등급 — 후보에서 제외한다.
#: 신촌 800m 안 도로 2,128개 중 1,106개가 footway(인도·캠퍼스 산책로), 190개가
#: steps(계단)라, 거르지 않으면 접근성 상위권이 전부 '가게를 낼 수 없는 자리'로
#: 채워진다. 반대로 pedestrian(연세로 같은 보행전용 상업가로)·living_street·
#: service(이면 골목)는 실제 상권이므로 남긴다.
#: 차단 목록(허용 목록이 아니라)으로 둔 것은, 새로운 highway 값이 들어와도
#: 조용히 후보에서 사라지지 않게 하기 위해서다.
NON_FRONTAGE_HIGHWAYS: frozenset[str] = frozenset(
    {
        "footway",
        "steps",
        "path",
        "track",
        "cycleway",
        "busway",
        "corridor",
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary_link",
        "secondary_link",
        "tertiary_link",
        "construction",
        "proposed",
    }
)


def can_front_a_store(highway: str) -> bool:
    return (highway or "").strip().lower() not in NON_FRONTAGE_HIGHWAYS


def search_radius_for(region_query: str) -> int:
    q = (region_query or "").strip()
    if q.endswith(DISTRICT_SUFFIXES):
        return DISTRICT_SEARCH_RADIUS_M
    return NEIGHBORHOOD_SEARCH_RADIUS_M


@dataclass
class CandidateSpot:
    lat: float
    lng: float
    osm_id: int
    integration_top_pct: float
    highway: str


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """등거리 근사 — 이 축척(수 km)에서는 오차가 무시할 수준이다."""
    dy = (lat2 - lat1) * 111_320.0
    dx = (lng2 - lng1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dx, dy)


def segment_midpoint(segment: RoadSegment) -> tuple[float, float]:
    """세그먼트 길이의 절반 지점 (lat, lng). 좌표 순서만으로 결정된다."""
    coords = segment.coordinates
    if not coords:
        return 0.0, 0.0
    if len(coords) == 1:
        return float(coords[0][1]), float(coords[0][0])

    lengths: list[float] = []
    total = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        d = _haversine_m(y1, x1, y2, x2)
        lengths.append(d)
        total += d
    if total <= 0:
        return float(coords[0][1]), float(coords[0][0])

    target = total / 2.0
    walked = 0.0
    for i, d in enumerate(lengths):
        if walked + d >= target:
            frac = (target - walked) / d if d > 0 else 0.0
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]
            return (
                float(y1 + (y2 - y1) * frac),
                float(x1 + (x2 - x1) * frac),
            )
        walked += d
    return float(coords[-1][1]), float(coords[-1][0])


def sample_candidate_spots(
    segments: list[RoadSegment],
    integration: dict[int, float],
    *,
    max_candidates: int = MAX_CANDIDATES,
    min_spacing_m: float = MIN_SPACING_M,
) -> list[CandidateSpot]:
    """점포가 면할 수 있는 도로 중 접근성 상위 지점을 간격 조건을 지키며 뽑는다.

    정렬 키는 (-접근성, osm_id, 좌표 repr) — 동점이어도 순서가 고정되어
    같은 입력이 같은 후보 목록을 낸다.

    백분위는 **전체 도로망** 기준으로 계산한다. 후보를 고를 때만 걸러낼 뿐,
    "이 길이 이 동네에서 몇 등인가"는 인도·계단을 포함한 전체 순위여야
    site-score가 보여 주는 값과 같은 의미가 된다.
    """
    if not segments or not integration:
        return []

    values = list(integration.values())
    ranked = sorted(
        (
            i
            for i in range(len(segments))
            if i in integration and can_front_a_store(segments[i].highway)
        ),
        key=lambda i: (-integration[i], segments[i].osm_id, repr(segments[i].coordinates)),
    )

    chosen: list[CandidateSpot] = []
    for idx in ranked:
        if len(chosen) >= max_candidates:
            break
        seg = segments[idx]
        lat, lng = segment_midpoint(seg)
        if lat == 0.0 and lng == 0.0:
            continue
        if any(
            _haversine_m(lat, lng, c.lat, c.lng) < min_spacing_m for c in chosen
        ):
            continue
        higher = sum(1 for v in values if v > integration[idx])
        chosen.append(
            CandidateSpot(
                lat=round(lat, 6),
                lng=round(lng, 6),
                osm_id=seg.osm_id,
                integration_top_pct=round(100.0 * (higher + 1) / len(values), 1),
                highway=seg.highway,
            )
        )
    return chosen


# ---------------------------------------------------------------------------
# 라벨 — 주소는 지어내지 않되, 서로 구별은 되어야 한다
# ---------------------------------------------------------------------------

#: 8방위. 역지오코딩이 여러 후보에 같은 주소를 돌려줄 때(대학 캠퍼스·대형 필지처럼
#: 한 지번이 넓은 경우) 후보를 구별하는 데 쓴다.
COMPASS_8 = ("북", "북동", "동", "남동", "남", "남서", "서", "북서")


def bearing_label(
    from_lat: float, from_lng: float, to_lat: float, to_lng: float
) -> str:
    """검색 중심에서 본 8방위. 순수 계산이라 같은 좌표면 같은 값이다."""
    dy = to_lat - from_lat
    dx = (to_lng - from_lng) * math.cos(math.radians((from_lat + to_lat) / 2.0))
    if dx == 0.0 and dy == 0.0:
        return COMPASS_8[0]
    angle = math.degrees(math.atan2(dx, dy)) % 360.0
    return COMPASS_8[int((angle + 22.5) % 360.0 // 45.0)]


def disambiguate_labels(
    labels: list[str], spots: list[CandidateSpot], center_lat: float, center_lng: float
) -> list[str]:
    """같은 주소가 겹친 후보에만 '방위 거리'를 덧붙인다.

    신촌을 검색하면 후보 다섯 곳이 전부 '연세로 50 연세대학교'로 나온다 —
    캠퍼스가 한 필지라서다. 주소를 지어내지 않으면서 후보를 구별할 수 있는
    유일한 방법이 중심 기준 상대 위치이고, 이는 좌표만으로 결정된다.
    겹치지 않는 라벨은 손대지 않는다.
    """
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1

    out: list[str] = []
    for label, spot in zip(labels, spots):
        if counts.get(label, 0) <= 1:
            out.append(label)
            continue
        direction = bearing_label(center_lat, center_lng, spot.lat, spot.lng)
        distance = _haversine_m(center_lat, center_lng, spot.lat, spot.lng)
        out.append(f"{label} ({direction}측 {distance:.0f}m)")
    return out


__all__ = [
    "COMPASS_8",
    "DISTRICT_SEARCH_RADIUS_M",
    "MAX_CANDIDATES",
    "MIN_SPACING_M",
    "NEIGHBORHOOD_SEARCH_RADIUS_M",
    "NON_FRONTAGE_HIGHWAYS",
    "CandidateSpot",
    "bearing_label",
    "can_front_a_store",
    "disambiguate_labels",
    "sample_candidate_spots",
    "search_radius_for",
    "segment_midpoint",
]
