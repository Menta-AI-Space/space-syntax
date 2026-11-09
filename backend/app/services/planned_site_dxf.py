"""계획 도면(DXF)에서 계획 도로선과 계획 획지를 꺼낸다.

`/api/planned-site`는 지금까지 `{roads, lots}` JSON만 받았다. 그 JSON을 손으로
만들 수 있는 사람은 없다 — 손님이 가진 것은 **LH 공고에 붙어 오는 도면**이다.
여기가 그 간극을 메운다.

**왜 이게 되는지** (2026-10-07 실사, `SPEC_SPACE_ANALYSIS_MODULES.md`):
LH 토지 공급 공고 3건을 받아 보니 지구단위계획 결정도면이 CAD 원본이나 벡터
PDF로 붙어 있었고, CAD 쪽은 **레이어가 용도별로 갈려 있고 좌표가 실좌표**였다.

- 화성동탄2: `00_계획선-도로` · `00_계획선-획지선` — EPSG:5174
- 성남낙생: `-계획선` · `-계획선-획지` — EPSG:5174

**이름이 같지 않다.** 접두도 구분자도 다르다. 그래서 고정 이름으로 찾지 않고
**토큰 단위로 맞춘다** — 건축도면의 벽 레이어에 쓴 방식(`floorplan_extraction`)과
같은 이유다. 공통으로 들어 있는 말은 「계획선」이고, 거기에 「도로」가 붙으면 길,
「획지」가 붙으면 땅이다.

⚠️ **좌표계는 추정하지 말고 사람에게 확인받는다.** 5174와 5186은 false northing이
10만m 차이라 **둘 다 한국 땅에 떨어진다** (실측: 같은 도면이 5174면 동탄2,
5186이면 금산 부근). 범위만 보고 고르면 100km를 틀린다. 그래서 여기서는
**후보를 전부 돌려주고** 화면이 지도에 얹어 "여기가 맞습니까"를 묻게 한다.

DWG는 받지 않는다. 읽으려면 GPL 라이브러리를 써야 해서 제품에 넣을 수 없다
(`ARCHITECTURE_RULES.md`). 손님에게는 DXF로 저장해 달라고 해야 하는데, 그건
여전히 숙제를 넘기는 것이라 미결로 남아 있다.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

import ezdxf
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, MultiPoint, Polygon, mapping
from shapely.ops import linemerge, nearest_points, polygonize, unary_union
from shapely.prepared import prep

logger = logging.getLogger(__name__)

# 레이어 이름을 쪼갤 구분자. 건축도면과 같은 규칙을 쓴다.
_SPLIT = ("-", "_", ".", " ", "/", "|")

# 「이 레이어는 계획 도면의 본체다」를 알리는 말. 도면틀·범례·수치지형도를 거른다.
_PLAN_NEEDLES = ("계획선", "계획", "plan")
# 길. `기타도로`도 걸려야 하므로 부분 일치를 쓴다.
_ROAD_NEEDLES = ("도로", "road", "street", "가로")
# 땅. 「획지」가 표준어이고 「가구」(블록)도 같이 쓰인다.
_LOT_NEEDLES = ("획지", "가구", "lot", "block", "parcel")
# 본체가 아닌 것 — 도면틀·범례·방위표·삽도·수치지형도·지적 바탕.
_SKIP_NEEDLES = (
    "도면틀", "범례", "방위", "삽도", "표제", "layout", "form", "범례및방위",
    "수치지형도", "지적", "지번", "텍스트", "text", "txt", "ps_", "주기",
)

# 좌표계 후보. 한국 도시계획 도면은 거의 다 중부원점이고, 5174(보정)가 가장 흔하다.
# 실측 2건 다 5174였지만 **그래서 기본값으로 두되 확인은 받는다.**
CRS_CANDIDATES = ("EPSG:5174", "EPSG:5186", "EPSG:5179", "EPSG:5185", "EPSG:5187")
# 한국 본토 대략 범위. 후보를 거르는 1차 체.
KOREA_BOUNDS = (124.0, 33.0, 132.0, 39.5)

# 곡선을 몇 m 오차로 펼지. 계획 도로의 곡선은 완만해서 이 정도면 충분하다.
CURVE_SAGITTA_M = 1.0
# 너무 짧은 선은 심볼·치수 조각이다. **5m는 임의값이 아니다** — 분석 엔진이
# 5m 미만 가상 도로를 거부한다(`simulation.py`). 여기서 걸러 두지 않으면 추출은
# 성공해 놓고 분석에서 "가상 도로 14: 5m 이상이어야 합니다"로 떨어진다.
MIN_ROAD_LEN_M = 5.0
# 길 하나가 이보다 길면 잘라 넘긴다. **임의값이 아니다** — 분석 엔진이 2km 넘는
# 가상 도로를 거부한다(`simulation.py`). 도면의 간선은 이어 붙이면 쉽게 넘어간다
# (실측: 성남낙생에서 213번 길이 2km 초과로 분석이 통째로 실패했다).
MAX_ROAD_LEN_M = 1900.0
# 획지로 치기엔 너무 작은 면(글자 테두리·심볼이 만든 자투리).
MIN_LOT_AREA_M2 = 300.0
# 도로 사이 긴 띠(도로 자체)를 획지에서 거르는 기준. 4πA/L²로, 원이 1·정사각형이
# 0.79다. 도로 회랑은 가늘고 길어 0.1 아래로 떨어진다.
LOT_COMPACTNESS = 0.25
# 도면 글자 중 획지 이름 꼴: A23, F10, F1-2, 주복3, 초5, 업2 … (면적·용적률 숫자는 빠진다)
_LOT_NAME = re.compile(r"^(?:[A-Z]{1,2}\d{1,3}(?:-\d)?|[가-힣]{1,2}\d{1,2}(?:-\d)?)$")

MAX_ROADS = 400
MAX_LOTS = 2000

# 도로 경계선 → 중심선. 도면의 도로는 **길 양쪽 가장자리 선**으로 그려지고, 블록마다
# 가장자리 선이 닫힌 고리가 된다. 그 선을 그대로 길로 쓰면 고리끼리 서로 안 닿아
# 망이 섬으로 쪼개지고, 분석은 가장 큰 섬 하나만 남긴다 — 2026-10-22 실측(시연용
# 가상 지구): 획지 129개 중 98개가 「길에 안 닿음」으로 빠졌다. 그래서 가장자리 선이
# 감싼 도로 면(회랑)을 찾아 그 가운데 선을 뽑는다.
# 회랑으로 볼 면의 모양 기준(4πA/L²). 블록은 0.5 안팎, 도로망 회랑은 0.05 아래다.
CORRIDOR_COMPACTNESS = 0.1
# 도로 경계선 중 회랑 테두리가 이만큼은 돼야 중심선으로 간다. 도면 끝에서 한쪽
# 가장자리만 그려진 길은 회랑이 안 닫히는데, 그게 많으면 중심선이 길을 빠뜨린다.
CORRIDOR_SHARE = 0.8
# 회랑 테두리를 몇 m 간격 점으로 찍어 보로노이를 돌릴지.
CENTERLINE_STEP_M = 4.0
# 점이 너무 많으면 간격을 넓힌다 — 보로노이 시간을 묶어 두려고.
MAX_CENTERLINE_POINTS = 40_000
# 막다른 가지 중 이보다 짧은 것은 모서리가 만든 잔가지로 보고 자른다. 실제
# 막다른 길(쿨데삭)은 이보다 훨씬 길다.
SPUR_M = 30.0
# 본체와 이만큼 안에 떨어진 가운데 선 조각은 이어 붙인다(넓은 광장에서 생기는 끊김).
ISLAND_JOIN_M = 20.0
# 이 안에 모인 가운데 선 끝점은 한 교차점으로 본다. 분석 그래프의 모서리 틈 고치기(≈5m)보다 넓게.
END_JOIN_M = 6.0
# 본체에서 이만큼 벗어난 선은 버린다(본체 크기 대비). 계획선 레이어에 도면틀·삽도
# 조각이 섞여 있어서, 그대로 두면 **좌표가 제주도까지 퍼진다** — 실측(동탄2):
# 도로 경도 범위가 124.87~127.15, 위도 33.48~37.24로 나왔다. 그 상태로 분석하면
# 길이 서로 수백 km 떨어진 것으로 계산돼 획지에 접한 길이 하나도 안 잡힌다.
OUTLIER_MARGIN = 0.25
# 본체 범위를 잴 때 쓰는 백분위. 양 끝 2%는 떠돌이 점으로 본다.
BODY_PCTL = 0.02


class PlanExtractionError(Exception):
    pass


@dataclass
class PlanLayer:
    """도면의 레이어 하나. 화면이 「이렇게 읽었습니다」를 보여 주고 고칠 수 있게."""

    name: str
    entity_count: int
    total_length: float
    closed_count: int
    guess: str  # road | lot | other


@dataclass
class CrsCandidate:
    """좌표계 후보 하나와 그때 도면이 놓이는 자리."""

    epsg: str
    lat: float
    lng: float


@dataclass
class PlanExtraction:
    roads: list[dict] = field(default_factory=list)  # {coordinates, road_type, name}
    lots: list[dict] = field(default_factory=list)  # {lot_id, geometry}
    layers: list[PlanLayer] = field(default_factory=list)
    crs: str = ""
    crs_candidates: list[CrsCandidate] = field(default_factory=list)
    centre_lat: float | None = None
    centre_lng: float | None = None
    notes: list[str] = field(default_factory=list)


def _tokens(layer: str) -> list[str]:
    name = layer.lower()
    for sep in _SPLIT:
        name = name.replace(sep, "|")
    return [t for t in name.split("|") if t]


def _has(name: str, needles) -> bool:
    lowered = name.lower()
    return any(n in lowered for n in needles)


def _layer_kind(name: str) -> str:
    """레이어 이름으로 길·땅·그 외를 가린다.

    「계획선」이 들어간 것만 본체로 친다. 도면틀·범례·수치지형도·지적 바탕은
    같은 파일에 함께 들어 있고, 그것들까지 넣으면 종이 전체가 분석 대상이 된다.
    """
    if _has(name, _SKIP_NEEDLES):
        return "other"
    if not _has(name, _PLAN_NEEDLES):
        return "other"
    if _has(name, _LOT_NEEDLES):
        return "lot"
    if _has(name, _ROAD_NEEDLES):
        return "road"
    # 「-계획선」처럼 더 안 갈린 레이어(성남낙생)가 있다. 길로 본다 — 계획 도면에서
    # 가장 많은 선이 도로 경계선이고, 틀리면 사람이 화면에서 고친다.
    return "road"


def _points(entity) -> list[tuple[float, float]]:
    """엔티티 하나를 점 목록으로.

    곡선은 ezdxf에 맡겨 편다 — 각도 규약을 직접 다루지 않는다(건축도면에서 손으로
    호를 계산했다가 반원이 뒤집힌 적이 있다).

    ⚠️ **LWPOLYLINE에 `flattening()`이 없는 ezdxf 버전이 있다.** 거기에 기대고
    예외를 삼켰더니 계획선 폴리라인 208개가 **통째로 조용히 사라졌다**
    (2026-10-07 실측, 성남낙생). bulge가 없으면 정점을 그대로 쓰고, 있으면
    `virtual_entities()`로 ezdxf가 풀게 한다.
    """
    kind = entity.dxftype()
    try:
        if kind == "LINE":
            s, e = entity.dxf.start, entity.dxf.end
            return [(float(s.x), float(s.y)), (float(e.x), float(e.y))]
        if kind in ("ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
            return [(float(p[0]), float(p[1])) for p in entity.flattening(CURVE_SAGITTA_M)]
        if kind == "LWPOLYLINE":
            raw = list(entity.get_points())
            if any(abs(float(p[4])) > 1e-9 for p in raw if len(p) > 4):
                pts: list[tuple[float, float]] = []
                for sub in entity.virtual_entities():
                    for pt in _points(sub):
                        if not pts or pt != pts[-1]:
                            pts.append(pt)
            else:
                pts = [(float(p[0]), float(p[1])) for p in raw]
            if entity.closed and len(pts) > 2 and pts[0] != pts[-1]:
                pts.append(pts[0])
            return pts
        if kind == "POLYLINE":
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
            if entity.is_closed and len(pts) > 2 and pts[0] != pts[-1]:
                pts.append(pts[0])
            return pts
    except Exception:
        logger.debug("점을 못 뽑은 엔티티: %s", kind, exc_info=True)
        return []
    return []


def _road_width(line: LineString, rim) -> float | None:
    """가운데 선에서 도로 가장자리까지 거리 × 2 — 길 가운데 점 여러 개의 중앙값.

    교차로에선 가장자리가 멀어져 폭이 크게 재지므로 평균 대신 중앙값을 쓴다. 계획도면 모델이
    「둘레 길의 가장 높은 도로 등급」을 보는데, 도면 길은 OSM 분류가 없어 폭으로 등급을 대신한다(2026-10-27).
    """
    if rim is None or line.length <= 0:
        return None
    n = max(3, min(25, int(line.length // 10)))
    dists = sorted(rim.distance(line.interpolate(i / (n + 1), normalized=True)) for i in range(1, n + 1))
    return round(2 * dists[len(dists) // 2], 1)


def _split_long(pts: list) -> list[list]:
    """2km를 넘는 길은 토막 낸다 — 엔진이 받지 않는다(`MAX_ROAD_LEN_M`)."""
    out, cur, run = [], [tuple(pts[0])], 0.0
    for a, b in zip(pts, pts[1:]):
        step = math.dist(a, b)
        if run + step > MAX_ROAD_LEN_M and len(cur) > 1:
            out.append(cur)
            cur, run = [tuple(a)], 0.0
        cur.append(tuple(b))
        run += step
    if len(cur) > 1:
        out.append(cur)
    return out


def _length(pts) -> float:
    return sum(
        math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)
    ) if len(pts) > 1 else 0.0


def _is_closed(pts) -> bool:
    return len(pts) > 3 and math.dist(pts[0], pts[-1]) < max(_length(pts) * 0.01, 0.5)


def _compactness(poly) -> float:
    return 4 * math.pi * poly.area / max(poly.length ** 2, 1e-9)


def _road_corridors(road_lines: list[LineString]) -> list[Polygon]:
    """도로 경계선이 감싼 도로 면. 경계선이 회랑을 못 닫으면 빈 목록이다."""
    try:
        faces = list(polygonize(unary_union(road_lines)))
    except Exception:
        return []
    corridors = [
        f for f in faces
        if f.area >= MIN_LOT_AREA_M2 and _compactness(f) < CORRIDOR_COMPACTNESS
    ]
    if not corridors:
        return []
    drawn = unary_union(road_lines).length
    rim = sum(c.boundary.length for c in corridors)
    return corridors if drawn > 0 and rim / drawn >= CORRIDOR_SHARE else []


def _prune_spurs(lines: list[LineString]) -> list[LineString]:
    """한쪽 끝이 어디에도 안 닿는 짧은 가지를 자른다 (모서리가 만드는 잔가지)."""
    for _ in range(3):
        degree: dict[tuple[float, float], int] = {}
        for line in lines:
            for end in (line.coords[0], line.coords[-1]):
                key = (round(end[0], 1), round(end[1], 1))
                degree[key] = degree.get(key, 0) + 1
        kept = [
            line for line in lines
            if not (
                line.length < SPUR_M
                and min(
                    degree[(round(e[0], 1), round(e[1], 1))]
                    for e in (line.coords[0], line.coords[-1])
                ) == 1
            )
        ]
        if len(kept) == len(lines):
            break
        merged = linemerge(unary_union(kept)) if kept else None
        lines = (
            list(merged.geoms) if isinstance(merged, MultiLineString)
            else [merged] if merged is not None and not merged.is_empty else []
        )
    return lines


def _centerlines(corridors: list[Polygon]) -> list[LineString]:
    """회랑의 가운데 선(중축). 테두리를 점으로 찍어 보로노이를 돌리고, 회랑 안에
    온전히 든 변만 남긴다 — 테두리를 가로지르는 변은 같은 쪽 이웃 점 사이의
    수직이등분선이라 길이 아니다."""
    out: list[LineString] = []
    for corridor in corridors:
        rim = corridor.boundary
        step = max(CENTERLINE_STEP_M, rim.length / MAX_CENTERLINE_POINTS)
        points = shapely.get_coordinates(shapely.segmentize(rim, step))
        if len(points) < 4:
            continue
        edges = shapely.voronoi_polygons(MultiPoint(points), only_edges=True)
        inside = prep(corridor)
        kept = [e for e in getattr(edges, "geoms", [edges]) if inside.contains(e)]
        if not kept:
            continue
        merged = linemerge(unary_union(kept))
        parts = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
        out.extend(_drop_islands(_prune_spurs(parts)))
    return _join_close_ends([line.simplify(1.0) for line in out if line.length > 0])


def _join_close_ends(lines: list[LineString]) -> list[LineString]:
    """몇 m 안에 모인 길 끝점을 한 점으로 모은다.

    보로노이 가운데 선은 넓은 교차로·광장에서 교차점을 두세 개(4m 남짓 간격)로 낸다.
    그대로 넘기면 분석 그래프가 모서리 틈 고치기로 그중 하나만 옮겨, 같은 점에서 만난 길이
    서로 다른 교차점에 붙어 끊겼다(운정 샘플 2026-10-24: 광장 안 길 1개가 외톨이). 공용
    그래프 코드를 건드리는 대신 여기서 끝점을 먼저 한 점으로 맞춘다.
    """
    pts = [line.coords[k] for line in lines for k in (0, -1)]
    parent = list(range(len(pts)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            if (pts[a][0] - pts[b][0]) ** 2 + (pts[a][1] - pts[b][1]) ** 2 <= END_JOIN_M**2:
                parent[find(b)] = find(a)
    groups: dict[int, list[int]] = {}
    for a in range(len(pts)):
        groups.setdefault(find(a), []).append(a)
    centre: dict[int, tuple[float, float]] = {}
    for members in groups.values():
        cx = sum(pts[m][0] for m in members) / len(members)
        cy = sum(pts[m][1] for m in members) / len(members)
        for m in members:
            centre[m] = (cx, cy)
    out = []
    for i, line in enumerate(lines):
        coords = list(line.coords)
        coords[0], coords[-1] = centre[2 * i], centre[2 * i + 1]
        if coords[0] != coords[-1]:
            joined = LineString(coords)
            if joined.length >= 1.0:
                out.append(joined)
    return out


def _drop_islands(lines: list[LineString]) -> list[LineString]:
    """회랑 하나 안에서 본체와 안 이어진 짧은 조각을 버린다.

    회랑은 이어진 도로 면 하나라 그 가운데 선도 한 덩어리여야 한다. 따로 떨어진 짧은
    조각은 회랑 안의 좁은 틈(시설 부지 등)이 보로노이에 만든 것이고, 그대로 두면 분석
    그래프에서 떨어진 덩어리가 된다(운정 샘플 2026-10-24: 광장 옆 틈에서 1개).
    """
    if len(lines) < 2:
        return lines
    groups: list[list[int]] = []
    seen: set[int] = set()
    for i in range(len(lines)):
        if i in seen:
            continue
        stack, group = [i], []
        seen.add(i)
        while stack:
            j = stack.pop()
            group.append(j)
            for k in range(len(lines)):
                if k not in seen and lines[j].distance(lines[k]) < 0.5:
                    seen.add(k)
                    stack.append(k)
        groups.append(group)
    if len(groups) == 1:
        return lines
    length = [sum(lines[j].length for j in g) for g in groups]
    main = max(range(len(groups)), key=lambda g: length[g])
    body = unary_union([lines[j] for j in groups[main]])
    out = [lines[j] for j in groups[main]]
    for g, group in enumerate(groups):
        if g == main:
            continue
        piece = unary_union([lines[j] for j in group])
        # 가까우면 잇는다 — 광장처럼 넓은 면에서는 보로노이 변이 회랑 밖으로 살짝 나가 끊긴다.
        if piece.distance(body) <= ISLAND_JOIN_M:
            a, b = nearest_points(piece, body)
            out += [lines[j] for j in group] + [LineString([a, b])]
        elif length[g] >= 2 * SPUR_M:
            out += [lines[j] for j in group]
    return out


def _lot_names(doc) -> list[tuple[str, "shapely.Point"]]:
    """도면에 적힌 획지 이름(F10, 주복1, A23 …)과 그 자리.

    「획지 37」로는 공고문과 맞춰 볼 수가 없다 — 손님은 「F10」을 사러 온다.
    도면 글자에는 면적·용적률 같은 숫자도 섞여 있어서 이름 꼴만 받는다.
    """
    out = []
    for entity in doc.modelspace().query("TEXT MTEXT"):
        try:
            text = (entity.plain_text() if entity.dxftype() == "MTEXT" else entity.dxf.text).strip()
            if _LOT_NAME.match(text):
                at = entity.dxf.insert
                out.append((text, shapely.Point(float(at.x), float(at.y))))
        except Exception:
            continue
    return out


def _pick_crs(cx: float, cy: float) -> list[CrsCandidate]:
    """도면 좌표가 한국 땅에 떨어지는 좌표계 후보를 모은다.

    ⚠️ 하나로 좁히지 않는다. 5174와 5186은 10만m 차이라 **둘 다 한국 안**에
    들어온다. 범위만 보고 고르면 100km를 틀린 채로 조용히 돌아간다.
    """
    out: list[CrsCandidate] = []
    for epsg in CRS_CANDIDATES:
        try:
            lng, lat = Transformer.from_crs(epsg, "EPSG:4326", always_xy=True).transform(cx, cy)
        except Exception:
            continue
        if not (math.isfinite(lat) and math.isfinite(lng)):
            continue
        x0, y0, x1, y1 = KOREA_BOUNDS
        if x0 <= lng <= x1 and y0 <= lat <= y1:
            out.append(CrsCandidate(epsg=epsg, lat=round(lat, 6), lng=round(lng, 6)))
    return out


def _candidate_anywhere(epsg: str, cx: float, cy: float) -> CrsCandidate | None:
    """지정한 좌표계로 도면 중심을 지구 위 어디든 놓아 본다. 못 놓으면 None."""
    try:
        lng, lat = Transformer.from_crs(epsg, "EPSG:4326", always_xy=True).transform(cx, cy)
    except Exception:
        return None
    if not (math.isfinite(lat) and math.isfinite(lng)) or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return CrsCandidate(epsg=epsg, lat=round(lat, 6), lng=round(lng, 6))


def extract_plan(
    data: bytes,
    crs: str | None = None,
    road_layers: list[str] | None = None,
    lot_layers: list[str] | None = None,
) -> PlanExtraction:
    """DXF에서 계획 도로·획지를 꺼내 `/api/planned-site`가 받는 모양으로 돌려준다.

    ``crs``를 주지 않으면 후보만 돌려주고 좌표 변환은 하지 않는다 — 어느 좌표계인지
    사람이 확인해야 하기 때문이다(모듈 주석의 ⚠️).
    """
    # ⚠️ `ezdxf.read(StringIO(...))`로 질러가지 말 것 — **예외 없이 빈 문서**를
    # 돌려준다(2026-10-07 실측: 19,985개 엔티티짜리 도면이 0개로 읽혔다). 조용히
    # 틀리는 쪽이라 더 나쁘다. 임시 파일로 떨어뜨려 `readfile`을 쓴다.
    import tempfile
    from pathlib import Path

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as fh:
            fh.write(data)
            tmp = Path(fh.name)
        try:
            doc = ezdxf.readfile(str(tmp))
        except Exception:
            from ezdxf import recover

            doc, _ = recover.readfile(str(tmp))
    except Exception as exc:
        # 2026-11-07 UX 루프: ezdxf 영어 원문(「Invalid group code "…" at line 1.」)을 붙여 화면에 보였다 —
        # 평면도 탭(floorplan_extraction._read_dxf)과 같이 할 일만 한국어로 말한다(원문은 __cause__에 남는다).
        raise PlanExtractionError(
            "DXF 파일을 읽을 수 없습니다 — 파일이 깨졌거나 DXF 형식이 아닙니다. "
            "CAD 프로그램에서 「다른 이름으로 저장 → DXF」로 다시 저장해 올려 주세요."
        ) from exc
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)

    by_layer: dict[str, list[list[tuple[float, float]]]] = {}
    for entity in doc.modelspace():
        pts = _points(entity)
        if len(pts) >= 2:
            by_layer.setdefault(str(getattr(entity.dxf, "layer", "0")), []).append(pts)

    if not by_layer:
        raise PlanExtractionError("도면에서 선을 찾지 못했습니다")

    road_pick = {n.lower() for n in (road_layers or [])}
    lot_pick = {n.lower() for n in (lot_layers or [])}
    chosen = bool(road_pick or lot_pick)

    layers: list[PlanLayer] = []
    for name, groups in sorted(by_layer.items()):
        if chosen:
            kind = "road" if name.lower() in road_pick else "lot" if name.lower() in lot_pick else "other"
        else:
            kind = _layer_kind(name)
        layers.append(
            PlanLayer(
                name=name,
                entity_count=len(groups),
                total_length=round(sum(_length(p) for p in groups), 1),
                closed_count=sum(1 for p in groups if _is_closed(p)),
                guess=kind,
            )
        )

    kinds = {layer.name: layer.guess for layer in layers}
    body = [
        pt
        for name, groups in by_layer.items()
        if kinds.get(name) in ("road", "lot")
        for pts in groups
        for pt in pts
    ]
    if not body:
        if chosen:
            raise PlanExtractionError(
                "고른 길·획지 레이어에서 선을 찾지 못했습니다. 다른 레이어를 골라 주세요."
            )
        # 레이어 이름으로 못 알아본 도면도 레이어 목록은 돌려준다 — 오류로 끝내면 화면에
        # 「직접 골라 주세요」만 뜨고 고를 표가 없어 막다른 길이었다
        # (2026-11-07 UX 자동 개선, 390px에서 평면도 DXF를 계획도면 탭에 올림).
        return PlanExtraction(
            layers=layers,
            notes=[
                "계획 도로·획지로 볼 레이어를 이름으로 알아보지 못했습니다 — 아래 표에서 "
                "길·획지 레이어를 골라 「이 레이어로 다시 읽기」를 눌러 주세요. 건물 평면도라면 "
                "「건축 평면도 분석」 탭에 올려 주세요."
            ],
        )
    # 떠돌이 조각을 먼저 버린다. 본체 범위를 백분위로 잡고, 거기서 크게 벗어난
    # 선을 들어내는 것이다 — 평균이나 전체 범위로 재면 떠돌이 하나가 범위를
    # 통째로 늘려 아무것도 안 걸러진다.
    xs = sorted(p[0] for p in body)
    ys = sorted(p[1] for p in body)
    lo = lambda v: v[int(len(v) * BODY_PCTL)]
    hi = lambda v: v[min(len(v) - 1, int(len(v) * (1 - BODY_PCTL)))]
    bx0, bx1, by0, by1 = lo(xs), hi(xs), lo(ys), hi(ys)
    mx = max(bx1 - bx0, 1.0) * OUTLIER_MARGIN
    my = max(by1 - by0, 1.0) * OUTLIER_MARGIN
    keep_box = (bx0 - mx, by0 - my, bx1 + mx, by1 + my)

    def in_body(pts) -> bool:
        return all(
            keep_box[0] <= x <= keep_box[2] and keep_box[1] <= y <= keep_box[3]
            for x, y in pts
        )

    dropped = 0
    for name, groups in list(by_layer.items()):
        if kinds.get(name) not in ("road", "lot"):
            continue
        kept_groups = [g for g in groups if in_body(g)]
        dropped += len(groups) - len(kept_groups)
        by_layer[name] = kept_groups
    if dropped:
        result_notes_outlier = (
            f"도면틀·삽도처럼 본체에서 떨어진 선 {dropped:,}개를 뺐습니다."
        )
    else:
        result_notes_outlier = ""

    body = [
        pt
        for name, groups in by_layer.items()
        if kinds.get(name) in ("road", "lot")
        for pts in groups
        for pt in pts
    ]
    if not body:
        raise PlanExtractionError("본체로 볼 선이 남지 않았습니다")

    cx = sum(p[0] for p in body) / len(body)
    cy = sum(p[1] for p in body) / len(body)
    candidates = _pick_crs(cx, cy)

    result = PlanExtraction(layers=layers, crs_candidates=candidates)
    if result_notes_outlier:
        result.notes.append(result_notes_outlier)

    # 2026-11-09(해커톤 01, 글로벌 샘플): 사람이 좌표계를 **직접 지정**하면 한국 밖이어도 받는다 —
    # 두바이·미국 신도시 도면은 UTM(EPSG:326xx)이라 한국 후보 어디에도 안 맞는다. 추정은 여전히
    # 한국 안에서만 하고(모듈 주석의 ⚠️), 밖에 놓이면 그 사실을 적어 둔다.
    abroad = _candidate_anywhere(crs, cx, cy) if crs and crs not in [c.epsg for c in candidates] else None
    if abroad is not None:
        candidates = [abroad] + candidates
        result.crs_candidates = candidates
        result.notes.append(
            f"{crs}로 보고 도면을 한국 밖({abroad.lat:.3f}N, {abroad.lng:.3f}E)에 놓았습니다 — "
            "주변 기존 도로·아파트·역 데이터는 한국만 있어 도면 안 길만으로 계산합니다."
        )
    elif not candidates:
        result.notes.append(
            "도면 좌표가 한국 좌표계 어디에도 맞지 않습니다 — 도면 좌표가 0 근처에서 "
            "그려졌을 수 있습니다(실좌표가 아닌 도면). 좌표계를 직접 지정해 주세요."
        )
        return result
    elif crs and crs not in [c.epsg for c in candidates]:
        result.notes.append(f"{crs}는 이 도면을 어디에도 놓지 못합니다 — 좌표계를 확인해 주세요.")

    use = crs if crs in [c.epsg for c in candidates] else candidates[0].epsg
    result.crs = use
    to_wgs = Transformer.from_crs(use, "EPSG:4326", always_xy=True)
    picked = next((c for c in candidates if c.epsg == use), candidates[0])
    result.centre_lat, result.centre_lng = picked.lat, picked.lng

    if len(candidates) > 1:
        others = " · ".join(f"{c.epsg}({c.lat:.3f}N)" for c in candidates if c.epsg != use)
        result.notes.append(
            f"좌표계를 {use}로 보고 {picked.lat:.4f}N, {picked.lng:.4f}E에 놓았습니다. "
            f"다른 후보도 한국 안에 떨어집니다({others}) — 지도에서 위치가 맞는지 "
            "확인해 주세요. 틀리면 100km 단위로 어긋납니다."
        )

    def to_lonlat(pts):
        return [[round(v, 7) for v in to_wgs.transform(x, y)] for x, y in pts]

    road_lines: list[LineString] = []
    lot_lines: list[LineString] = []
    for name, groups in by_layer.items():
        kind = kinds.get(name)
        target = road_lines if kind == "road" else lot_lines if kind == "lot" else None
        if target is None:
            continue
        for pts in groups:
            if len(pts) < 2 or _length(pts) <= 0:
                continue
            try:
                target.append(LineString(pts))
            except Exception:
                continue

    roads: list[dict] = []
    lots: list[dict] = []

    # 도로: 조각을 이어 붙인다. 캐드 도면의 길 하나는 수십 개 폴리라인으로 쪼개져
    # 있어서(동탄2: 8,245조각) 그대로 쓰면 상한에 걸리고, 망 구조도 꼭짓점이
    # 과하게 많아진다. `unary_union`이 교차점에서 끊고 `linemerge`가 이어 준다.
    if road_lines:
        corridors = _road_corridors(road_lines)
        centre = _centerlines(corridors) if corridors else []
        rim = unary_union([c.boundary for c in corridors]) if centre else None
        if centre:
            parts = centre
            result.notes.append(
                "도로가 양쪽 가장자리 선으로 그려져 있어, 그 사이 가운데 선을 길로 썼습니다."
            )
        else:
            # 선이 한 줄로 합쳐지면 unary_union이 LineString을 돌려주고, shapely linemerge는
            # 그걸 받지 못해 500이 났다 — 레이어 표에서 선 하나짜리 레이어를 「계획 도로」로
            # 골라 다시 읽으면 화면엔 아무 말 없이 「길 0개」만 남았다(2026-11-08 UX 자동 개선).
            joined = unary_union(road_lines)
            merged = linemerge(joined) if isinstance(joined, MultiLineString) else joined
            parts = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
        for i, line in enumerate(parts):
            if line.length < MIN_ROAD_LEN_M:
                continue
            width = _road_width(line, rim) if centre else None
            for part in _split_long(list(line.coords)):
                roads.append(
                    {
                        "coordinates": to_lonlat(part),
                        "road_type": "residential",
                        "name": f"계획도로 {len(roads) + 1}",
                        "width_m": width,
                    }
                )
        result.notes.append(
            f"계획 도로선 {len(road_lines):,}조각을 이어 붙여 {len(roads):,}개 길로 읽었습니다."
        )

    # 획지: 경계가 **선으로만** 그려져 있다. 닫힌 폴리라인을 기대하면 0개가 나온다.
    # 게다가 **획지선만으로는 닫히지 않는다** — 블록 한 칸은 바깥을 도로 경계선이,
    # 안을 획지선이 나눈다. 실측(2026-10-07): 동탄2 획지선 2,080개만 넣으면 면이
    # 6개(300㎡ 넘는 것 0개), 도로선까지 함께 넣으면 1,311개가 나왔다.
    # 그래서 **도로선과 획지선을 함께** 교차점에서 끊어 면을 만든다.
    if lot_lines or road_lines:
        try:
            faces = list(polygonize(unary_union(road_lines + lot_lines)))
        except Exception as exc:
            logger.warning("획지 면 만들기 실패: %s", exc)
            faces = []
        kept = [
            f for f in faces
            if f.area >= MIN_LOT_AREA_M2
            and 4 * math.pi * f.area / max(f.length ** 2, 1e-9) >= LOT_COMPACTNESS
        ]
        names = _lot_names(doc)
        used: set[str] = set()
        for i, poly in enumerate(sorted(kept, key=lambda f: -f.area)):
            ring = to_lonlat(list(poly.exterior.coords))
            inside = prep(poly)
            name = next((t for t, pt in names if t not in used and inside.contains(pt)), None)
            if name:
                used.add(name)
            lots.append({"lot_id": name or f"획지 {i + 1}", "geometry": mapping(Polygon(ring))})
        if lots:
            dropped = len(faces) - len(kept)
            # 도로선이 0개(또는 획지선이 0개)인데도 「도로선과 획지선을 이어」라 해, 화면의 「길 0개」와
            # 어긋났다 — 실제로 쓴 선만 말한다 (2026-11-08 UX 자동 개선).
            used_lines = (
                "도로선과 획지선을" if road_lines and lot_lines else "도로선만" if road_lines else "획지선만"
            )
            result.notes.append(
                f"{used_lines} 이어 획지 {len(lots):,}개를 만들었습니다"
                + (f" (도로 회랑·자투리 {dropped:,}개는 뺐습니다)." if dropped > 0 else ".")
            )

    # 선이 많으면 분석이 느려지고 상한에도 걸린다. 긴 것부터 남긴다 — 짧은 조각은
    # 대개 모서리 마감이나 심볼이고, 망의 뼈대는 긴 선이 만든다.
    if len(roads) > MAX_ROADS:
        roads.sort(key=lambda r: -len(r["coordinates"]))
        result.notes.append(f"계획 도로선이 {len(roads):,}개라 긴 것부터 {MAX_ROADS}개만 썼습니다.")
        roads = roads[:MAX_ROADS]
    if len(lots) > MAX_LOTS:
        result.notes.append(f"획지가 {len(lots):,}개라 {MAX_LOTS}개만 썼습니다.")
        lots = lots[:MAX_LOTS]

    result.roads, result.lots = roads, lots
    if not roads:
        result.notes.append("계획 도로선을 찾지 못했습니다 — 레이어를 직접 골라 주세요.")
    if not lots:
        result.notes.append(
            "획지 면을 만들지 못했습니다 — 경계선이 서로 닿지 않아 면이 닫히지 "
            "않았을 수 있습니다. 레이어를 직접 골라 다시 시도해 주세요."
        )
    return result
