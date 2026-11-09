"""시연 샘플 도면 — 파주 운정3지구 운정중앙역 일대 (LH 실제 도면에서).

예전 샘플(`make_planned_site_dxf.py`)은 세종 빈 땅에 얹은 **가상 지구**였다.
화면에서 보면 바둑판이라 「이게 뭔 도면이냐」가 먼저 나왔다(2026-10-24 사용자).
이 화면의 취지는 **아직 가게가 없는 신도시에서 목 좋은 획지를 고르는 것**이니,
샘플도 실제로 지금 팔리는 땅이어야 한다 — 운정3지구 업무복합용지 F10
(25,904㎡, LH 입찰 2026-10-28)과 그 둘레의 주상복합·상업·공동주택 블록.

원본: LH 공고 BN-0008014 첨부 「획지 및 건축물에 관한 도시관리계획결정도(20차)」
벡터 PDF. 공고 첨부라 저장소에는 넣지 않고, 여기서 만든 DXF만 넣는다.

어떻게 만드나:
1. PDF에서 **채움색이 있는 조각**(= 용도가 정해진 땅)을 모아 색별로 합친다.
   CAD 출력 PDF는 블록 하나가 수십 조각으로 쪼개져 있다.
2. 땅을 모두 합쳐 **닫기(buffer +r → −r)** 하면 블록 사이 틈이 메워진다.
   그 메운 면에서 땅을 빼면 **도로(와 보행축) 면**이다. 그 테두리를 도로 경계선으로,
   땅 조각 테두리를 획지선으로 쓴다 — LH CAD 도면과 같은 모양.
3. 획지 이름(F10, 주복1, A23 …)은 PDF 글자를 그 자리에 TEXT로 남긴다.
4. 좌표: PDF pt → EPSG:5179는 `georef_lots.py`가 지적도 7필지로 맞춘 아핀식
   (잔차 0.24m 이하). 그것을 LH 도면이 쓰는 **EPSG:5174**로 옮겨 적는다 —
   화면의 좌표계 확인 단계까지 실제처럼 시연된다.

실행 (PyMuPDF가 있는 파이썬): py -3.12 tools/make_unjeong_sample_dxf.py
"""

from __future__ import annotations

import collections
import glob
import json
import re
import sys
from pathlib import Path

import ezdxf
from pyproj import Transformer
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

LH = Path(r"C:\dev\tools\scratch\unjeong-f10\lh")
sys.path.insert(0, str(LH))
from pdfgeom import polys_of  # noqa: E402

import pymupdf  # noqa: E402

OUT = (
    Path(__file__).resolve().parents[2]
    / "frontend-next" / "public" / "samples" / "planned-site-sample.dxf"
)

# 운정중앙역 남북 띠(PDF pt). 서쪽 교하지구·동쪽 기존 운정은 채움이 없어 저절로 빠진다.
WINDOW = (330, 470, 505, 700)
CLOSE_PT = 6.0  # ≈ 43m — 녹지 낀 대로(80m 안팎)까지 메운다
SIMPLIFY_PT = 0.35  # ≈ 2.5m
MIN_PIECE_PT2 = 0.6  # 글자 테두리·심볼 자투리
ROAD_LAYER = "00_계획선-도로"
LOT_LAYER = "00_계획선-획지선"
NAME_LAYER = "00_계획선-획지명"
# 획지 이름: A23, F10, F1-2, 주복3, 초5, 고2, 유7, 업2, P28 …
LABEL = re.compile(r"^(?:[A-Z]{1,2}\d{1,3}(?:-\d)?|[가-힣]{1,2}\d{1,2}(?:-\d)?)$")
# 발췌 테두리에 걸려 앞길이 발췌 밖(교하지구 기존 도로)에 있는 획지. 넣으면 「길에 안 닿음」으로 뜬다.
OUTSIDE_FRONTAGE = {"복7"}
# 단독주택(연노랑)·점포주택(노랑)·주차장 등 작은 필지 색
VILLAGE_FILL = {(1.0, 1.0, 0.498), (1.0, 1.0, 0.0), (0.729, 0.729, 0.729), (0.722, 0.541, 0.0)}
VILLAGE_CLOSE_PT = 1.6  # ≈ 11m — 골목은 메우고 단지 둘레 도로(15m 이상)는 남긴다
SKIP_FILL = {(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)}
# 채움 없는 광장(PDF pt). 폭이 100m 남짓이라 닫기(43m)로 안 메워져 도로 면에 구멍이 났고,
# 광장으로 들어가는 길 네 개가 막다른 길로 끝나 북쪽 획지가 9~19점으로 떨어졌다(2026-10-24 검증).
# 사람이 가로지르는 보행 공간이니 도로 면에 넣는다.
PLAZA_PT = [(404.6, 513.5), (406.7, 524.0)]  # 광장 위·아래 — 가운데가 좁아 두 조각으로 갈린다
PLAZA_MAX_PT = 10.0  # 반경 ≈ 72m — 광장 조각이 발췌 바깥과 이어져 있어도 이 안만 넣는다


def main() -> None:
    pdf = [p for p in glob.glob(str(LH / "drawings" / "*.pdf")) if "획지" in p][0]
    pg = pymupdf.open(pdf)[0]
    x0, y0, x1, y1 = WINDOW
    win = pymupdf.Rect(*WINDOW)

    bycol: dict[tuple, list] = collections.defaultdict(list)
    for dr in pg.get_drawings():
        fill = dr.get("fill")
        if fill is None or not dr["rect"].intersects(win):
            continue
        col = tuple(round(v, 3) for v in fill)
        if col in SKIP_FILL:
            continue
        bycol[col] += [p for p in polys_of(dr) if p.area >= MIN_PIECE_PT2]

    clip = box(x0, y0, x1, y1)
    lots = []
    village = []
    for col, pieces in bycol.items():
        if col in VILLAGE_FILL:
            village += pieces
            continue
        u = unary_union([p.buffer(0.08) for p in pieces]).buffer(-0.08).intersection(clip)
        lots += [g for g in getattr(u, "geoms", [u]) if g.geom_type == "Polygon" and g.area > 4]
    # 빈칸(void): 땅이지만 샘플에서 뺀 곳. 길로 칠해지지 않게 닫기에는 넣고 도로 면에서는 뺀다.
    # 단독주택지는 비워 둔다. 필지 40여 개와 그 사이 골목(6~8m)을 그대로 두면 촘촘한 골목이
    # 구조값을 끌어올려 300㎡짜리 집터가 역 앞 상업용지보다 1등급으로 뜨고(2026-10-24 실측),
    # 한 덩어리로 합치면 동쪽 가장자리를 따라 길쭉해 추출기가 도로 회랑으로 본다.
    # 상가 입지를 고르는 화면에서 볼 땅이 아니다 — 둘레 도로만 남긴다.
    v = unary_union([p.buffer(0.08) for p in village]).buffer(VILLAGE_CLOSE_PT, join_style=2)
    village_area = v.buffer(-VILLAGE_CLOSE_PT, join_style=2).intersection(clip)
    # 발췌 서쪽 테두리에 잘린 획지는 앞길이 발췌 밖에 있어 「길에 안 닿음」으로 뜬다.
    west_edge = box(x0, y0, x0 + 0.6, y1)
    void = [village_area] + [g for g in lots if g.intersects(west_edge)]
    lots = [g for g in lots if not g.intersects(west_edge)]
    # 이름 글자를 먼저 모은다 — 발췌 밖 길에 면한 획지를 이름으로 뺀다.
    labels = []
    for w in pg.get_text("words"):
        if x0 < w[0] < x1 and y0 < w[1] < y1 and LABEL.match(w[4]):
            labels.append((w[4], Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)))
    outside = [p for t, p in labels if t in OUTSIDE_FRONTAGE]
    void += [g for g in lots if any(g.contains(p) for p in outside)]
    lots = [g for g in lots if not any(g.contains(p) for p in outside)]
    void = unary_union(void)
    land = unary_union(lots)
    closed = unary_union([land, void]).buffer(CLOSE_PT, join_style=2).buffer(-CLOSE_PT, join_style=2)
    # 가장 큰 덩어리(역 일대 본체)만 — 떨어진 마을 자투리는 길이 안 이어져 등급이 왜곡된다.
    closed = max(getattr(closed, "geoms", [closed]), key=lambda g: g.area)
    lots = [g for g in lots if closed.contains(g.representative_point())]
    land = unary_union(lots)
    roads = closed.difference(land.buffer(0.05)).difference(void.buffer(0.05))
    for px, py in PLAZA_PT:
        gap = clip.difference(land.buffer(0.05)).difference(void.buffer(0.05)).difference(roads)
        here = Point(px, py)
        disc = here.buffer(PLAZA_MAX_PT)
        # 광장 둘레 안의 빈틈은 모두 — 가는 틈 하나만 남아도 추출기가 그 둘레로 잔가지를 낸다.
        pieces = [g.intersection(disc) for g in getattr(gap, "geoms", [gap]) if g.intersects(disc)]
        if not any(p.contains(here) for p in pieces):
            print("광장 자리가 비어 있지 않습니다:", px, py)
        roads = unary_union([roads] + [p.buffer(0.05) for p in pieces])
    # 도로 면은 이어진 한 덩어리만 — 빈칸 둘레에 떨어져 남은 자투리는 추출기가 획지로 읽는다.
    roads = max(getattr(roads, "geoms", [roads]), key=lambda g: g.area)
    # 빈칸 자투리가 도로 면에 구멍으로 남으면 추출기가 그것을 획지로 읽는다 — 획지가 없는 구멍은 메운다.
    marks = [g.representative_point() for g in lots]
    roads = unary_union([
        Polygon(g.exterior, [r for r in g.interiors if any(Polygon(r).contains(m) for m in marks)])
        for g in getattr(roads, "geoms", [roads])
    ])
    # 곡선 테두리의 잔물결(≈1m)을 편다 — 그대로 두면 가운데 선 추출이 잔가지를 수백 개 낸다.
    roads = roads.simplify(SIMPLIFY_PT)
    lots = [g.simplify(SIMPLIFY_PT) for g in lots]

    m = json.load(open(LH / "pdf_to_5179.json"))
    to5174 = Transformer.from_crs("EPSG:5179", "EPSG:5174", always_xy=True)

    def xy(pts):
        out = []
        for x, y in pts:
            ex, ey = m[0] * x + m[1] * y + m[4], m[2] * x + m[3] * y + m[5]
            a, b = to5174.transform(ex, ey)
            out.append((round(a, 3), round(b, 3)))
        return out

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6  # m
    for name, color in ((ROAD_LAYER, 1), (LOT_LAYER, 3), (NAME_LAYER, 5), ("00_도면틀", 7), ("00_표제", 7)):
        doc.layers.add(name, color=color)
    msp = doc.modelspace()

    def rings(geom):
        for g in getattr(geom, "geoms", [geom]):
            yield g.exterior
            yield from g.interiors

    for ring in rings(roads):
        msp.add_lwpolyline(xy(ring.coords), close=True, dxfattribs={"layer": ROAD_LAYER})
    for lot in lots:
        msp.add_lwpolyline(xy(lot.exterior.coords), close=True, dxfattribs={"layer": LOT_LAYER})

    named = 0
    for lot in lots:
        inside = [(t, p) for t, p in labels if lot.contains(p)]
        if inside:
            text, pt = inside[0]
            msp.add_text(text, height=12, dxfattribs={"layer": NAME_LAYER, "insert": xy([pt.coords[0]])[0]})
            named += 1

    fx0, fy0, fx1, fy1 = clip.buffer(8).bounds
    msp.add_lwpolyline(xy([(fx0, fy0), (fx1, fy0), (fx1, fy1), (fx0, fy1)]), close=True, dxfattribs={"layer": "00_도면틀"})
    msp.add_text(
        "파주운정3지구 획지 및 건축물에 관한 도시관리계획결정도(20차) — 운정중앙역 일대 발췌",
        height=20, dxfattribs={"layer": "00_표제", "insert": xy([(fx0, fy0 - 4)])[0]},
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(OUT)
    area = affinity.affine_transform(closed, m).area
    print(f"{OUT}: 땅 조각 {len(lots)}개(이름 {named}개), 도로 면 {len(list(rings(roads)))}고리, 범위 {area / 1e6:.2f}㎢")


if __name__ == "__main__":
    main()
