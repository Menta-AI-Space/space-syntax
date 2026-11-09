"""시연용 계획 도면(DXF)을 만든다 — LH 공고 도면과 같은 모양으로.

⚠️ 2026-10-24부터 화면 샘플이 아니다. 샘플은 실제 LH 도면에서 뽑은 운정3지구
(`make_unjeong_sample_dxf.py`)로 바뀌었다. 이 가상 지구는 `tools/out/`에 떨어진다.

`make_planned_site_fixture.py`는 이미 손질된 `{roads, lots}` JSON을 만든다. 그건
DXF 입력 경로(`planned_site_dxf.py`)를 거치지 않는다. 시연에서 보여야 하는 건
「도면을 넣으면 나온다」이므로, 실제 LH 도면이 가진 성질을 그대로 갖춘 DXF를
따로 만든다 (2026-10-22, 10/26 랩미팅 시연용):

- **도로는 중심선이 아니라 경계선**이다. 길 양쪽 가장자리 선이 `00_계획선-도로`에.
- **획지는 닫힌 도형이 아니라 나누는 선**이다. 블록 안을 가르는 선만
  `00_계획선-획지선`에 있고, 블록 바깥 테두리는 도로 경계선이 맡는다.
- **좌표는 실좌표(EPSG:5174)**다. 5186으로 읽어도 한국 땅에 떨어지므로 화면이
  좌표계를 물어야 한다 — 그 확인 단계까지 시연된다.
- **도면틀·범례 레이어가 같은 파일에** 있다. 추출기가 이름으로 걸러야 한다.

LH 원본 도면(성남낙생 등)은 공고 첨부라 저장소에 넣지 않는다. 자리는 세종 인근
빈 땅(`make_planned_site_fixture.py`와 같은 자리)을 빌린 **가상 지구**다.

구조는 일부러 불균등하다 — 가운데 대로(25m)·중간 집산로(20m)·막다른 길이 섞여
있다. 계획 도로만으로 돌리면(2026-10-22 실측: 획지 129개 전부 등급, 상·하위 10%
차이 1.53배) **지구 가운데 교차부가 높고 외곽이 낮다** — 북쪽 간선(30m)도 지구
끝에 있어 낮게 나온다. 바깥 도시와의 연결은 「주변 기존 도로도 함께 보기」가 맡는다.

실행 (backend 폴더에서): python tools/make_planned_site_dxf.py
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point, box
from shapely.ops import unary_union

OUT = Path(__file__).resolve().parent / "out" / "planned-site-virtual.dxf"

BASE_LNG, BASE_LAT = 127.2300, 36.5300
W, H = 960.0, 640.0

ROAD_LAYER = "00_계획선-도로"
LOT_LAYER = "00_계획선-획지선"

# (중심선 점들, 폭 m)
ROADS: list[tuple[list[tuple[float, float]], float]] = [
    ([(0, H), (W, H)], 30),  # 북쪽 간선
    ([(0, 420), (W, 420)], 20),  # 중간 집산
    ([(0, 0), (W, 0)], 12),  # 남쪽 외곽
    ([(0, 0), (0, H)], 12),  # 서쪽 외곽
    ([(W, 0), (W, H)], 12),  # 동쪽 외곽
    ([(480, 0), (480, H)], 25),  # 가운데 대로
    ([(240, 0), (240, 420)], 12),
    ([(720, 0), (720, 420)], 12),
    ([(240, 420), (240, H)], 12),
    ([(0, 220), (480, 220)], 12),  # 서쪽만 가로지른다 — 동쪽 480~720은 큰 블록
    ([(720, 220), (W, 220)], 12),
    ([(840, 220), (840, 95)], 10),  # 막다른 길
]
CUL_DE_SAC = ((840, 95), 14.0)


def _road_area():
    parts = [LineString(pts).buffer(width / 2, cap_style=2, join_style=2) for pts, width in ROADS]
    parts.append(Point(*CUL_DE_SAC[0]).buffer(CUL_DE_SAC[1]))
    return unary_union(parts)


def _lot_lines(block) -> list[LineString]:
    """블록 하나를 획지로 가르는 선. 긴 변을 따라 두 줄로 놓고 일정 간격으로 끊는다."""
    minx, miny, maxx, maxy = block.bounds
    w, h = maxx - minx, maxy - miny
    big = block.area > 50_000  # 가운데 큰 블록은 상업용지처럼 큼직하게
    step = 70.0 if big else 34.0
    cuts: list[LineString] = []
    if w >= h:
        if h > 70:
            cuts.append(LineString([(minx - 1, miny + h / 2), (maxx + 1, miny + h / 2)]))
        x = minx + step
        while x < maxx - step * 0.6:
            cuts.append(LineString([(x, miny - 1), (x, maxy + 1)]))
            x += step
    else:
        if w > 70:
            cuts.append(LineString([(minx + w / 2, miny - 1), (minx + w / 2, maxy + 1)]))
        y = miny + step
        while y < maxy - step * 0.6:
            cuts.append(LineString([(minx - 1, y), (maxx + 1, y)]))
            y += step
    out: list[LineString] = []
    for cut in cuts:
        piece = cut.intersection(block)
        if isinstance(piece, LineString) and piece.length > 1:
            out.append(piece)
        elif isinstance(piece, MultiLineString):
            out.extend(p for p in piece.geoms if p.length > 1)
    return out


def _lines(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString" or geom.geom_type == "LinearRing":
        return [geom]
    return [g for part in getattr(geom, "geoms", []) for g in _lines(part)]


def main() -> None:
    x0, y0 = Transformer.from_crs("EPSG:4326", "EPSG:5174", always_xy=True).transform(BASE_LNG, BASE_LAT)

    def at(pt):
        return (round(x0 + pt[0], 3), round(y0 + pt[1], 3))

    roads = _road_area()
    blocks = box(0, 0, W, H).difference(roads)
    blocks = [b for b in getattr(blocks, "geoms", [blocks]) if b.area > 2_000]

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6  # m
    for name, color in ((ROAD_LAYER, 1), (LOT_LAYER, 3), ("00_도면틀", 7), ("00_범례", 7), ("00_표제", 7)):
        doc.layers.add(name, color=color)
    msp = doc.modelspace()

    # 도로 경계선 — 바깥 테두리와 블록 구멍 테두리 전부.
    for ring in _lines(roads.boundary):
        coords = list(ring.coords)
        msp.add_lwpolyline([at(p) for p in coords], dxfattribs={"layer": ROAD_LAYER})

    for block in blocks:
        for line in _lot_lines(block):
            msp.add_lwpolyline([at(p) for p in line.coords], dxfattribs={"layer": LOT_LAYER})

    # 도면틀·범례·표제 — 본체에서 떨어져 있다. 추출기가 이름으로 걸러야 한다.
    frame = [(-260, -220), (W + 260, -220), (W + 260, H + 220), (-260, H + 220)]
    msp.add_lwpolyline([at(p) for p in frame], close=True, dxfattribs={"layer": "00_도면틀"})
    msp.add_lwpolyline(
        [at(p) for p in [(W + 40, -200), (W + 240, -200), (W + 240, -80), (W + 40, -80)]],
        close=True, dxfattribs={"layer": "00_범례"},
    )
    msp.add_text(
        "가상 택지개발지구 지구단위계획 결정도 (시연용)",
        height=18, dxfattribs={"layer": "00_표제", "insert": at((-240, H + 170))},
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(OUT)
    print(f"{OUT} : 블록 {len(blocks)}개, EPSG:5174 기준점 ({x0:.1f}, {y0:.1f})")


if __name__ == "__main__":
    main()
