"""계획 단계의 땅 — 아직 없는 길 위에서 획지 등급을 낸다 (B안).

`parcel_structure.py`(A안)는 **이미 있는 동네**를 다룬다. 적재된
`road_segment_structure`를 읽어 필지에 붙이는 일이라, 길이 이미 있고 필지 경계도
지적도에 있어야 한다.

택지지구·지구단위계획은 그 둘이 다 없다. 길은 도면 위에만 있고 획지는 아직
지적이 아니다. 그런데 **분양가를 매겨야 하는 시점은 바로 그때**다. 여기가 그
경우를 맡는다: 계획 도로망(폴리라인)과 획지(폴리곤)를 받아 그 자리에서
공간구문론을 돌리고, A안과 **같은 등급·배분 규칙**을 적용한다.

같은 규칙을 쓰는 건 의도된 것이다 — 두 화면이 다른 점수를 내면 "계획일 때와
지어진 뒤가 다르다"는 말을 할 수 없다. 실제로 그 비교가 이 기능의 쓸모다.

⚠️ 한계
- 계획 도로망만으로 돌리면 **그 지구 안에서의 상대값**이다. 주변 기존 도로를
  함께 넣어야 "이 지구가 도시 안에서 어디쯤인지"가 나온다 — `include_existing`.
- 필지 단위 매출·지가 검증은 여전히 없다(A안 주석 참조). 계획 단계라 검증할
  실적 자체가 존재하지 않는다는 점도 화면이 말해야 한다.

변별 근거와 이웃 비교 (2026-10-24)
  등급(1~5)은 지구를 어디까지 잘라 넣느냐에 따라 흔들린다 — 운정 샘플을 만들며
  F10이 2→3→1등급으로 바뀌었다. 그리고 「1등급」만으로는 **왜** 좋은지 모른다.
  그래서 화면은 등급 대신 획지마다 **변(북·동·남·서)별로 어떤 길에 얼마나 닿는지**와
  **맞닿은·길 건너 이웃 획지**를 돌려받아 「고른 땅 vs 옆 땅」으로 견준다.
  등급 필드는 A안과 같은 규칙을 유지하려고 그대로 둔다.
"""

from __future__ import annotations

import logging
import contextvars
import re
import math
from dataclasses import dataclass, field

from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import nearest_points, unary_union
from shapely.ops import transform as shapely_transform

from app.services.plan_model import HW_RANK, BlockScore, PlanLine, score_lots, width_rank
from app.services.parcel_structure import ParcelStructure, _percentiles, grade_parcels, summarize
from app.services.simulation import SimulationError, VirtualRoad, _walk_metrics
from app.services.spatial_query import RoadSegment

logger = logging.getLogger(__name__)

# 획지 경계에서 이 거리 안의 길을 "접한 길"로 본다. A안(20m)과 같은 값을 쓴다 —
# 두 경로가 같은 기준이어야 계획과 준공 후를 견줄 수 있다.
FRONTAGE_M = 20.0
MAX_LOTS = 2000
MAX_ROADS = 400

# 이웃: 경계끼리 이 거리 안이면 견줄 상대로 본다. 왕복 6차로(≈35m)를 건너편까지 잡는 크기.
NEIGHBOR_M = 60.0
TOUCH_M = 3.0  # 이 안이면 「맞닿음」, 넘으면 「길 건너」
MAX_NEIGHBORS = 8
# 상가를 낼 수 없는 땅 — 순위·비교에서는 빼고, 옆 상가 땅의 「근처에 무엇이 있나」로만 쓴다
# (2026-10-24 사용자: 학교 땅이 몇 위인지는 쓸모없고, 학교가 옆 상가에 주는 영향이 중요하다).
# LH 획지 이름 앞글자로 가른다: 초·중·고·유 = 학교, A = 공동주택.
SCHOOL_PREFIX = ("초", "중", "고", "유")
HOUSING_PREFIX = ("A",)
# 2026-11-06 소유자: 「상권 분석이니까 상업용지 이외의 것은 회색으로」. 학교·아파트 말고도
# 공원·녹지·주차장·공공청사·문화·종교·의료·단독주택 등 **상가를 못 내는 땅**을 이름으로 가른다.
# 상가를 낼 수 있는 땅(F·C·상업·근린·업무·주상복합·점포겸용·복합)과 이름이 없는 땅은 후보로 둔다.
# 이름 앞머리가 지구마다 조금씩 달라, 여러 표기를 받는다.
HOUSING_WORDS = ("단독", "연립", "공동", "공주", "아파트", "B")
PUBLIC_WORDS = (
    "문", "공원", "녹지", "근공", "어린이공원", "주차", "P", "공공", "청사", "공청", "종교", "종",
    "의료", "의", "사회복지", "복지", "체육", "체", "주유", "광장", "하천", "유수지", "저류", "변전",
    "전기", "열공급", "통신", "도서관", "파출소", "소방", "우체국", "동사무소", "주민센터",
)
COMMERCIAL_WORDS = ("F", "C", "상", "근생", "근린", "업", "주복", "점", "복", "판매", "숙박")
NEARBY_M = 150.0  # 「근처 학교·아파트」로 적는 거리
MIN_SIDE_M = 5.0  # 모서리만 스친 길은 변으로 치지 않는다
STRONG_PCT = 80.0  # 도면 안 상위 20%
# 주변 도로를 넣을 때, 도면 가장자리에서 끊긴 계획 길 끝을 이 거리 안의 실제 길에 잇는다.
# 운정 샘플은 도면을 잘라 낸 탓에 동쪽 길 끝이 F10 옆 간선 40~50m 앞에서 멈춰,
# 역·간선 쪽에서 지구 안으로 들어오는 길이 계산에 없었다(2026-10-24 검증).
CONNECT_M = 60.0
# 기존 길이 이 비율 넘게 계획 획지 안에 들어 있으면 뺀다 — 지금 그 땅의 주차장 통로·임시 길은
# 계획이 끝나면 없어진다. 운정 샘플은 문2·F10 안에 바둑판 길이 깔려 있어, 그대로 두면 두 땅이
# 「사방이 길에 닿은 땅」으로 부풀었다(2026-10-24 검증).
INSIDE_LOT_SHARE = 0.5
# 도면 길에서 이 거리 안을 따라가는 기존 길은 같은 길이 지도 데이터에 먼저 들어간 것이다.
# 운정 샘플: OSM에 계획 도로가 1,083m짜리 길 하나로 들어가 있어, 그 옆 북쪽 획지 7곳이
# 같은 점수(70.5)를 나눠 가졌다(2026-10-24 검증). 도면 범위 안에서는 도면이 우선이다.
DUPLICATE_M = 15.0

# 미터 좌표계. 한국 도면은 EPSG:5179(종전과 같은 값), 한국 밖 도면은 그 자리의 UTM —
# 두바이 도면을 5179로 재면 수백 km 밖 투영이라 길이가 틀어진다(2026-11-09, 글로벌 샘플).
# 요청마다 다를 수 있어 contextvar에 둔다(run_in_threadpool은 context를 복사한다).
_KOREA = (124.0, 33.0, 132.0, 39.5)
_DEFAULT_PAIR = (
    Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True),
    Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True),
)
_metric_pair: contextvars.ContextVar[tuple[Transformer, Transformer]] = contextvars.ContextVar(
    "planned_site_metric_pair", default=_DEFAULT_PAIR
)


def metric_epsg_for(lng: float, lat: float) -> str:
    """도면 중심에 맞는 미터 좌표계 EPSG. 한국이면 5179, 밖이면 UTM 존."""
    x0, y0, x1, y1 = _KOREA
    if x0 <= lng <= x1 and y0 <= lat <= y1:
        return "EPSG:5179"
    zone = int((lng + 180) // 6) + 1
    zone = min(max(zone, 1), 60)
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def _use_metric_crs(lng: float, lat: float):
    """이 분석에 쓸 미터 좌표계를 정한다. 돌려준 토큰으로 되돌린다."""
    epsg = metric_epsg_for(lng, lat)
    if epsg == "EPSG:5179":
        return _metric_pair.set(_DEFAULT_PAIR)
    return _metric_pair.set(
        (
            Transformer.from_crs("EPSG:4326", epsg, always_xy=True),
            Transformer.from_crs(epsg, "EPSG:4326", always_xy=True),
        )
    )


class _Proxy:
    """`_TO_M.transform(x, y)` 꼴을 그대로 쓰기 위한 얇은 대리자."""

    def __init__(self, index: int):
        self._index = index

    def transform(self, x, y):
        return _metric_pair.get()[self._index].transform(x, y)


_TO_M = _Proxy(0)
_TO_WGS = _Proxy(1)
_DIRECTIONS = ("동", "북", "서", "남")  # atan2 각을 90°씩: 동(0°) → 북(90°) → …


@dataclass
class LotSide:
    """획지의 한 변(북·동·남·서)이 닿은 길. 값은 **이 도면 안** 도로 구간끼리의 백분위."""

    direction: str
    length_m: float
    integration_pct: float  # 어디서든 찾아오기 쉬운 길인가
    choice_pct: float  # 사람이 지나가는 길목인가
    score: float  # 둘의 평균 — 변끼리·이웃끼리 견주는 값
    footway: bool
    reasons: list[str] = field(default_factory=list)
    edge: dict | None = None  # 그 변의 경계선 (GeoJSON, WGS84)


@dataclass
class LotNeighbor:
    lot_id: str
    distance_m: float
    relation: str  # 「맞닿음」 | 「길 건너」


@dataclass
class LotNearby:
    lot_id: str
    kind: str  # 「school」 | 「housing」
    distance_m: float


@dataclass
class LotDetail:
    sides: list[LotSide]
    neighbors: list[LotNeighbor]  # 견줄 상가 땅만
    best_score: float | None
    kind: str = "candidate"  # 「candidate」(상가를 낼 수 있는 땅) | 「school」 | 「housing」
    nearby: list[LotNearby] = field(default_factory=list)  # 근처 학교·아파트
    plan: BlockScore | None = None  # 신도시 10곳에서 배운 「가게가 들어설 가능성」 (plan_model)


def lot_kind(lot_id: str) -> str:
    """획지 이름 → 땅 종류 「candidate」(상가를 낼 수 있는 땅) | 「school」 | 「housing」 | 「public」.

    이름 꼴이 아니면(「획지 5」) 상가 후보로 둔다 — 모르는 땅을 회색으로 빼면 놓친다.
    「점포겸용단독」·「근린생활」처럼 상가가 섞인 이름은 주거·공공어보다 먼저 후보로 잡는다.
    """
    name = (lot_id or "").strip()
    head = re.match(r"^([^\d\-]+)", name)
    prefix = head.group(1).strip() if head else ""
    if not prefix or name.startswith("획지"):
        return "candidate"
    if prefix in SCHOOL_PREFIX:
        return "school"
    if any(prefix.startswith(w) for w in ("점포", "근생", "근린상", "주상", "주복")) or "점포" in name:
        return "candidate"
    if prefix in HOUSING_PREFIX or prefix.startswith(HOUSING_WORDS):
        return "housing"
    # 「근린공원」은 「근린」으로 시작해도 공원이다 — 상가 낱말보다 먼저 본다.
    if any(w in prefix for w in ("공원", "녹지", "광장", "주차", "하천", "유수지")):
        return "public"
    if prefix.startswith(COMMERCIAL_WORDS):
        return "candidate"
    if prefix in PUBLIC_WORDS or prefix.startswith(tuple(w for w in PUBLIC_WORDS if len(w) > 1)):
        return "public"
    return "candidate"


@dataclass
class PlannedLot:
    """계획 도면의 획지 하나. ``lot_id``는 도면의 획지번호 그대로 쓰면 된다."""

    lot_id: str
    geometry: dict  # GeoJSON Polygon/MultiPolygon (WGS84)


def _to_metric(geometry):
    """WGS84 도형을 미터 좌표계(EPSG:5179)로 — 거리를 m로 재려면 필요하다."""
    return shapely_transform(lambda x, y, z=None: _TO_M.transform(x, y), geometry)


def _split_at_junctions(roads: list[VirtualRoad]) -> list[VirtualRoad]:
    """계획 길을 교차점마다 자른다.

    공간구문론 값은 **입력 길 하나에 하나**씩 나온다. 도면에서 뽑은 길은 교차점을 지나도
    한 줄로 이어져 있어(운정 샘플: 562m짜리), 그 옆 땅들이 위치와 상관없이 같은 점수를
    나눠 가졌다(2026-10-24 검증). 다른 길이 만나거나 가로지르는 자리에서 자른다.
    """
    from shapely.ops import substring

    lines = [_to_metric(LineString(r.coordinates)) for r in roads]
    tree = STRtree(lines)
    out: list[VirtualRoad] = []
    for i, (road, line) in enumerate(zip(roads, lines)):
        cuts: set[float] = set()
        for j in tree.query(line.buffer(1.0)):
            j = int(j)
            if j == i:
                continue
            other = lines[j]
            hit = line.intersection(other)
            pts = [g for g in getattr(hit, "geoms", [hit]) if g.geom_type == "Point"]
            pts += [Point(c) for c in (other.coords[0], other.coords[-1]) if line.distance(Point(c)) < 1.0]
            cuts.update(line.project(pt) for pt in pts)
        # 5m 안쪽 자투리는 만들지 않는다 (분석 엔진이 받지 않는 길이).
        marks = [0.0]
        for d in sorted(cuts):
            if d - marks[-1] >= 5.0 and line.length - d >= 5.0:
                marks.append(d)
        marks.append(line.length)
        for a, b in zip(marks, marks[1:]):
            piece = substring(line, a, b)
            out.append(VirtualRoad(
                coordinates=[list(_TO_WGS.transform(x, y)) for x, y in piece.coords],
                road_type=road.road_type,
            ))
    return out


def _segments_from_roads(roads: list[VirtualRoad]) -> list[RoadSegment]:
    """계획 도로를 분석 엔진이 받는 형태로 — 교차점마다 잘라서. `build_virtual_segments`와 달리
    기존 osm_id와 섞이지 않게 여기서도 음수 id를 쓴다."""
    from app.services.simulation import build_virtual_segments

    return build_virtual_segments(_split_at_junctions(roads))


def _top(pct: float) -> str:
    # 「상위 18%」는 읽기 어렵다(2026-10-26 사용자) — 「길 100개 중 18등 안」. 화면은 10-27부터 이 문구 대신
    # 변 값으로 「정면·쓸모」 문장을 직접 만든다(PlannedSitePanel frontText — 소유자 「입찰하는 사람에게 도움이 되는 말」).
    return f"길 100개 중 {max(1, math.ceil(100 - pct))}등 안"


def _side_reasons(side: LotSide) -> list[str]:
    """변 하나를 사람 말로. 숫자보다 「그래서 가게에 무슨 뜻인지」를 먼저 쓴다."""
    out: list[str] = []
    if side.integration_pct >= STRONG_PCT:
        out.append(f"동네 어디서나 찾아오기 쉬운 길({_top(side.integration_pct)})")
    if side.choice_pct >= STRONG_PCT:
        out.append(f"지나가는 사람이 많을 길({_top(side.choice_pct)}, 예상)")
    if side.footway:
        out.append("보행축에 닿음 — 걸어오는 손님이 들어오는 쪽")
    if side.length_m >= 100:
        out.append(f"길에 {side.length_m:.0f}m 닿음 — 가게 앞면을 넓게 낼 수 있음")
    if not out:
        out.append("안쪽 길 — 지나가는 사람이 적은 편" if side.score < 50 else "보통 길 — 도면 안 중간쯤")
    return out


def _edge_wgs(geom) -> dict | None:
    if geom.is_empty:
        return None
    return mapping(shapely_transform(lambda x, y, z=None: _TO_WGS.transform(x, y), geom))


def _lot_sides(polygon, lines, integ_pct, choice_pct) -> list[LotSide]:
    """획지 둘레를 북·동·남·서 변으로 나눠, 변마다 닿은 길 중 가장 좋은 값을 남긴다."""
    centre = polygon.centroid
    boundary = polygon.boundary
    groups: dict[str, dict] = {}
    for line, value, through, highway in lines:
        if polygon.distance(line) > FRONTAGE_M:
            continue
        edge = boundary.intersection(line.buffer(FRONTAGE_M))
        if edge.is_empty or edge.length < MIN_SIDE_M:
            continue
        # 변의 방향은 그 길과 맞닿은 경계 지점이 획지 중심에서 어느 쪽인가로 정한다.
        near, _ = nearest_points(edge, line)
        angle = math.degrees(math.atan2(near.y - centre.y, near.x - centre.x)) % 360
        direction = _DIRECTIONS[int(((angle + 45) % 360) // 90)]
        g = groups.setdefault(direction, {"edges": [], "integ": 0.0, "choice": 0.0, "score": -1.0, "foot": False})
        g["edges"].append(edge)
        score = (integ_pct[value] + choice_pct[through]) / 2
        if score > g["score"]:
            g.update(integ=integ_pct[value], choice=choice_pct[through], score=score)
        g["foot"] = g["foot"] or highway == "footway"

    sides = []
    for direction, g in groups.items():
        edge = unary_union(g["edges"])
        side = LotSide(
            direction=direction,
            length_m=round(edge.length, 1),
            integration_pct=round(g["integ"], 1),
            choice_pct=round(g["choice"], 1),
            score=round(g["score"], 1),
            footway=g["foot"],
            edge=_edge_wgs(edge.simplify(0.5)),
        )
        side.reasons = _side_reasons(side)
        sides.append(side)
    return sorted(sides, key=lambda s: -s.score)


def _neighbors(lot_ids: list[str], polygons: list) -> tuple[list[list[LotNeighbor]], list[list[LotNearby]]]:
    """(견줄 이웃, 근처 학교·아파트). 이웃은 맞닿은·길 건너 **상가 후보 땅**만 가까운 순으로 몇 곳,
    근처는 학교·아파트 땅을 거리와 함께."""
    tree = STRtree(polygons)
    kinds = [lot_kind(x) for x in lot_ids]
    neighbors: list[list[LotNeighbor]] = []
    nearby: list[list[LotNearby]] = []
    for i, poly in enumerate(polygons):
        found, around = [], []
        for j in tree.query(poly.buffer(max(NEIGHBOR_M, NEARBY_M))):
            j = int(j)
            if j == i:
                continue
            d = poly.distance(polygons[j])
            if kinds[j] == "candidate":
                if d <= NEIGHBOR_M:
                    found.append(LotNeighbor(lot_ids[j], round(d, 1), "맞닿음" if d <= TOUCH_M else "길 건너"))
            elif d <= NEARBY_M:
                around.append(LotNearby(lot_ids[j], kinds[j], round(d, 1)))
        neighbors.append(sorted(found, key=lambda n: n.distance_m)[:MAX_NEIGHBORS])
        nearby.append(sorted(around, key=lambda n: n.distance_m))
    return neighbors, nearby


def _drop_existing_inside_plan(
    existing: list[RoadSegment], planned: list[RoadSegment], lots: list[PlannedLot]
) -> list[RoadSegment]:
    """도면이 그린 자리의 기존 길을 뺀다 — 계획 획지 안의 길(없어질 길)과 도면 길을 따라가는 길(중복)."""
    inside = unary_union(
        [_to_metric(shape(l.geometry)).buffer(-2.0) for l in lots]
        + [_to_metric(LineString(s.coordinates)).buffer(DUPLICATE_M) for s in planned]
    )
    kept = []
    for seg in existing:
        line = _to_metric(LineString(seg.coordinates))
        if line.length > 0 and line.intersection(inside).length / line.length > INSIDE_LOT_SHARE:
            continue
        kept.append(seg)
    return kept


def _connect_to_existing(
    planned: list[RoadSegment], existing: list[RoadSegment], lots: list[PlannedLot]
) -> list[RoadSegment]:
    """끊긴 계획 길 끝 → 가장 가까운 실제 길로 잇는 짧은 연결선. 땅(획지)을 가로지르면 잇지 않는다."""
    from app.services.simulation import VIRTUAL_OSM_ID_BASE, build_virtual_segments

    plan_m = [_to_metric(LineString(s.coordinates)) for s in planned]
    exist_m = [_to_metric(LineString(s.coordinates)) for s in existing]
    if not plan_m or not exist_m:
        return []
    plan_tree, exist_tree = STRtree(plan_m), STRtree(exist_m)
    blocked = unary_union([_to_metric(shape(l.geometry)).buffer(-2.0) for l in lots])
    roads: list[VirtualRoad] = []
    for line in plan_m:
        for end in (line.coords[0], line.coords[-1]):
            here = Point(end)
            # 다른 계획 길과 만나는 끝은 끊긴 끝이 아니다.
            touching = [int(j) for j in plan_tree.query(here.buffer(1.0)) if plan_m[int(j)] is not line]
            if any(plan_m[j].distance(here) < 1.0 for j in touching):
                continue
            if any(e.distance(here) < 1.0 for e in (exist_m[int(j)] for j in exist_tree.query(here.buffer(1.0)))):
                continue  # 이미 실제 길에 닿아 있다
            near = [exist_m[int(j)] for j in exist_tree.query(here.buffer(CONNECT_M))]
            if not near:
                continue
            target = min(near, key=lambda e: e.distance(here))
            if target.distance(here) > CONNECT_M:
                continue
            link = LineString([here, nearest_points(here, target)[1]])
            if link.length < 5.0 or link.intersects(blocked):
                continue
            roads.append(VirtualRoad(
                coordinates=[list(_TO_WGS.transform(x, y)) for x, y in link.coords],
                road_type="residential",
            ))
    links = build_virtual_segments(roads) if roads else []
    for i, seg in enumerate(links):
        seg.osm_id = VIRTUAL_OSM_ID_BASE - len(planned) - i  # 계획 길 id와 겹치지 않게
    return links


def analyze_planned_site(
    roads: list[VirtualRoad],
    lots: list[PlannedLot],
    existing_segments: list[RoadSegment] | None = None,
) -> tuple[list[ParcelStructure], dict, list[str]]:
    """(획지별 등급, 요약, 경고). 변·이웃까지 필요하면 `analyze_planned_site_detail`."""
    graded, stats, warnings, _ = analyze_planned_site_detail(roads, lots, existing_segments)
    return graded, stats, warnings


def analyze_planned_site_detail(
    roads: list[VirtualRoad],
    lots: list[PlannedLot],
    existing_segments: list[RoadSegment] | None = None,
    *,
    road_widths: list[float | None] | None = None,
    apartments: list[tuple[float, float]] | None = None,
    stations: list[tuple[float, float]] | None = None,
    road_scores_out: list[dict] | None = None,
) -> tuple[list[ParcelStructure], dict, list[str], dict[str, LotDetail]]:
    """(획지별 등급, 요약, 경고, 획지별 변·이웃).

    ``existing_segments``를 주면 주변 기존 도로를 함께 계산한다. 주지 않으면
    계획 도로만으로 돌리고, 그 사실을 경고에 적는다.
    """
    if not roads:
        raise SimulationError("계획 도로가 없습니다")
    if not lots:
        raise SimulationError("획지가 없습니다")
    if len(roads) > MAX_ROADS:
        raise SimulationError(f"계획 도로가 너무 많습니다 (최대 {MAX_ROADS}개)")
    if len(lots) > MAX_LOTS:
        raise SimulationError(f"획지가 너무 많습니다 (최대 {MAX_LOTS}개)")

    warnings: list[str] = []
    first_pt = next(iter(roads[0].coordinates))
    token = _use_metric_crs(float(first_pt[0]), float(first_pt[1]))
    try:
        return _analyze_planned_site_detail_inner(
            roads, lots, existing_segments, road_widths=road_widths, apartments=apartments,
            stations=stations, road_scores_out=road_scores_out, warnings=warnings,
        )
    finally:
        _metric_pair.reset(token)


def _analyze_planned_site_detail_inner(
    roads: list[VirtualRoad],
    lots: list[PlannedLot],
    existing_segments: list[RoadSegment] | None,
    *,
    road_widths: list[float | None] | None,
    apartments: list[tuple[float, float]] | None,
    stations: list[tuple[float, float]] | None,
    road_scores_out: list[dict] | None,
    warnings: list[str],
) -> tuple[list[ParcelStructure], dict, list[str], dict[str, LotDetail]]:
    planned = _segments_from_roads(roads)
    if existing_segments:
        before = len(existing_segments)
        existing_segments = _drop_existing_inside_plan(existing_segments, planned, lots)
        if len(existing_segments) < before:
            warnings.append(
                f"도면 자리에 원래 있던 길 {before - len(existing_segments)}개(공사용 임시 길, 도면 길과 겹치는 길)는 "
                "지우고 도면에 그린 길을 썼습니다."
            )
    links = _connect_to_existing(planned, existing_segments, lots) if existing_segments else []
    segments = list(existing_segments or []) + planned + links
    first_link = len(segments) - len(links)
    if links:
        warnings.append(
            f"도면 끝에서 끊긴 길 {len(links)}곳은 {CONNECT_M:.0f}m 안에 있는 주변 도로와 이어서 계산했습니다."
        )
    if not existing_segments:
        warnings.append(
            "주변 기존 도로 없이 계획 도로만으로 계산했습니다 — 값은 이 지구 안에서의 "
            "상대값이고, 도시 전체에서의 위치는 말해 주지 않습니다."
        )

    metric_warnings: list[str] = []
    metrics, covered = _walk_metrics(segments, metric_warnings, "python")
    integration = metrics["integration"]
    choice = metrics["choice"]
    first_planned = len(existing_segments or [])
    if existing_segments:
        # 「도로망이 n개로 끊겨」는 주변 실제 도로 데이터(고가·지하차도 조각 등) 때문에 어디서나
        # 난다 — 계획도면 화면에서는 **도면 길이 빠졌을 때만** 알린다(2026-10-24).
        metric_warnings = [w for w in metric_warnings if "끊겨 있어" not in w]
    warnings.extend(metric_warnings)
    dropped = [i for i in range(first_planned, first_link) if not integration.get(i)]
    if dropped:
        warnings.append(
            f"계획 길 {len(dropped)}개가 나머지 길과 이어지지 않아 계산에서 빠졌습니다 — "
            "도면에서 그 길이 끊겨 있는지 확인해 주세요."
        )

    # 계산에 들어간 구간만 도형으로 만들어 둔다 (그래프에서 빠진 구간의 0.0은
    # "값이 0"이 아니라 "측정 안 됨"이므로 붙이면 안 된다).
    lines: list[tuple[LineString, float, float, str]] = []
    model_lines: list[PlanLine] = []
    _kept_segments: list[tuple[list, bool]] = []  # lines와 같은 순서의 (좌표, 계획 길인가) — 길 색칠용
    # 도면 길 → 폭 등급. 구간은 교차점에서 잘려 원래 길 번호를 잃으므로 가운데 점에 가장 가까운 도면 길로 찾는다.
    plan_geoms = [_to_metric(LineString(r.coordinates)) for r in roads]
    plan_tree = STRtree(plan_geoms)
    widths = road_widths or [None] * len(roads)
    for index, segment in enumerate(segments):
        if covered is not None and index not in covered:
            continue
        if index >= first_link:
            continue  # 연결선은 길 사이 통로일 뿐 — 땅이 닿은 길로 세지 않는다
        value = integration.get(index)
        if not value:
            continue  # 큰 덩어리에서 떨어진 구간의 0.0은 「측정 안 됨」
        geom = _to_metric(LineString([(x, y) for x, y in segment.coordinates]))
        lines.append((geom, float(value), float(choice.get(index, 0.0)), segment.highway))
        _kept_segments.append((segment.coordinates, index >= first_planned))
        if index >= first_planned:
            mid = geom.interpolate(0.5, normalized=True)
            rank = width_rank(widths[int(plan_tree.nearest(mid))])
        else:
            rank = HW_RANK.get(segment.highway, 2)
        model_lines.append(PlanLine(geom, float(value), float(choice.get(index, 0.0)), rank))

    if not lines:
        raise SimulationError("계획 도로망에서 계산된 구간이 없습니다")

    # 변 값은 획지가 아니라 **도로 구간끼리** 줄 세운다 — 어느 변이 센 길에 닿았는지를 보려는 것.
    integ_pct = _percentiles([v for _, v, _, _ in lines])
    choice_pct = _percentiles([c for _, _, c, _ in lines])

    # 2026-11-06 소유자: 「블록 단위가 아니라 길이 보여야 하고, 블록 안 길마다 좋고 나쁨이
    # 나와야 한다」. 구간마다 땅 변과 같은 값((찾아오기 쉬움 + 지나가는 길목) 백분위 평균)을
    # 낸다. 도형은 WGS84 그대로.
    if road_scores_out is not None:
        for (_geom, value, through, _hw), (coords, planned) in zip(lines, _kept_segments):
            road_scores_out.append(
                {
                    "coordinates": [[float(x), float(y)] for x, y in coords],
                    "score": round((integ_pct[value] + choice_pct[through]) / 2, 1),
                    "planned": planned,
                }
            )

    results: list[ParcelStructure] = []
    polygons = []
    sides: list[list[LotSide]] = []
    for lot in lots:
        polygon = _to_metric(shape(lot.geometry))
        polygons.append(polygon)
        sides.append(_lot_sides(polygon, lines, integ_pct, choice_pct))
        best: tuple[float, float] | None = None
        nearest = None
        frontage = 0
        for line, value, through, _ in lines:
            distance = polygon.distance(line)
            if nearest is None or distance < nearest:
                nearest = distance
            if distance <= FRONTAGE_M:
                frontage += 1
                if best is None or value > best[0]:
                    best = (value, through)
        results.append(
            ParcelStructure(
                pnu=lot.lot_id,
                area_sqm=float(polygon.area),
                structure=best[0] if best else None,
                integration=best[0] if best else None,
                choice=best[1] if best else None,
                road_distance_m=round(nearest, 1) if nearest is not None else None,
                frontage_count=frontage,
                lat=float(shape(lot.geometry).centroid.y),
                lng=float(shape(lot.geometry).centroid.x),
                geometry=lot.geometry,
            )
        )

    landlocked = [r for r in results if r.structure is None]
    if landlocked:
        warnings.append(
            f"획지 {len(landlocked)}곳이 계획 도로에서 {FRONTAGE_M:.0f}m 안에 닿지 않습니다 — "
            "맹지이거나 도면에서 그 길이 빠졌는지 확인해 주세요."
        )

    kinds = [lot_kind(lot.lot_id) for lot in lots]
    plan_scores: list[BlockScore | None] = [None] * len(lots)
    try:
        plan_scores = score_lots(
            polygons,
            [k == "housing" for k in kinds],
            model_lines,
            [_to_metric(Point(lng, lat)) for lng, lat in (apartments or [])],
            None if stations is None else [_to_metric(Point(lng, lat)) for lng, lat in stations],
        )
    except Exception as exc:  # 모델이 없거나 깨져도 기존 결과는 낸다
        logger.warning("계획도면 모델 점수 실패: %s", exc)
        warnings.append("「가게가 들어설 가능성」 점수를 계산하지 못했습니다 — 앞길 유동인구 비교만 보여 드립니다.")

    graded = grade_parcels(results)
    stats = summarize(graded)
    neighbors, nearby = _neighbors([lot.lot_id for lot in lots], polygons)
    detail = {
        lot.lot_id: LotDetail(
            sides=sides[i],
            neighbors=neighbors[i],
            best_score=sides[i][0].score if sides[i] else None,
            kind=kinds[i],
            nearby=nearby[i],
            plan=plan_scores[i],
        )
        for i, lot in enumerate(lots)
    }
    return graded, stats, warnings, detail
