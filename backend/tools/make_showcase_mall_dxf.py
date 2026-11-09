"""시연용 대형 복합몰 평면도 — 히트맵이 「여기는 붐비고 저기는 죽는다」를 크게 말하게.

`make_complex_mall_dxf.py`(200×160m)는 차이가 보이지만 통로가 셋뿐이라 화면이
얌전하다. 랩미팅 시연(2026-10-26)에는 한눈에 갈리는 도면이 필요해서 만든다
(2026-10-22 사용자 요청: "depthmapX를 더 극적으로 보여줄 수 있는 복잡한 것").

    ┌────────────── 북쪽 순환 통로 (고리) ──────────────┐
    │                     │                              │
    │          ┌──── 중앙 광장 ────┐                     │
    ├── 메인 몰(동서) ─────┼─────────────────────────────┤── 동쪽 막다른 날개(남쪽으로 길게)
    │     ║ 별관 연결 통로(폭 4m) │
    │ ┌── 서쪽 별관 ──┐   │(주출입구에서 올라오는 척추)
    │ └───────────────┘   │
    └──────────── 주출입구(남쪽 하나) ──────────────────┘

- **중앙 광장 + 북쪽 고리**: 돌아 나올 수 있는 길이 겹쳐 목이 가장 좋다 → 「가장 목이 좋은 곳」.
- **동쪽 날개**: 메인 몰 끝에서 남쪽으로 50m 내려가는 막다른 길 → 「외진 구역」.
- **서쪽 별관**: 좁은 통로 하나로만 들어가는 별관 — 들어가면 밖이 안 보인다 → 「시야가 막혀 있습니다」.
- 입구가 남쪽 하나뿐이라 깊이 차이가 커진다.

2026-10-25: 예전엔 남서쪽이 「푸드코트 골목 → 막다른 끝」이었다. 동쪽 날개와 같은 막다른 길이라
「외진 구역」 카드가 두 장(매장 68·69 부근) 나와 제목·설명이 똑같았다(사용자 「1과 2가 동일한 답」).
골목을 좁은 목 하나로 들어가는 별관으로 바꿔, 막다른 길(외진)과 막힌 시야를 서로 다른 장소가
보여 주게 했다. 「동선이 몰립니다」는 어떤 배치로도 안 나왔다 — 고리가 여럿인 몰은 통행이 한
덩어리로 몰리지 않는다. 규칙 기준을 샘플에 맞춰 낮추지 않았다.

매장은 통로 가장자리를 따라 자동으로 깐다. 다른 통로·매장과 겹치는 자리는 건너뛴다.

실행 (backend 폴더에서): python tools/make_showcase_mall_dxf.py
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
from shapely.geometry import LineString, box
from shapely.ops import unary_union

OUT = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "showcase-mall.dxf"

# 크기가 곧 해상도다. 분석 격자는 도면 전체에 약 2,200칸이라(`floorplan_vga.MAX_GRID_CELLS`)
# 300×200m로 그렸더니 한 칸이 6m가 돼 3m 문과 벽이 뭉개지고 통로 대신 매장 뒤 빈
# 땅이 칠해졌다(2026-10-22 실측). 160×105m면 한 칸 약 2.8m — 통로 8m가 세 칸이다.
W, H = 160.0, 105.0
DOOR = 3.0
SHOP_W, SHOP_D = 9.0, 9.0
ENTRANCE = (76.0, 84.0)  # 남쪽 외벽의 주출입구 구간

# 통로(열린 공간) — (x0, y0, x1, y1, 이름). 폭 8m.
CORRIDORS = [
    (76, 0, 84, 46, "주출입 통로"),
    (60, 42, 100, 66, "중앙 광장"),
    (10, 50, 150, 58, "메인 몰"),
    (76, 64, 84, 94, None),
    (30, 86, 130, 94, "북쪽 순환 통로"),
    (30, 58, 38, 94, None),
    (122, 58, 130, 94, None),
    (142, 8, 150, 58, "동쪽 날개 끝"),
    (22, 24, 26, 50, "별관 연결 통로"),  # 폭 4m — 별관으로 들어가는 유일한 길
    (8, 10, 56, 24, "서쪽 별관"),
]


def _lines(geom):
    if geom.is_empty:
        return []
    if geom.geom_type in ("LineString", "LinearRing"):
        return [geom]
    return [g for part in getattr(geom, "geoms", []) for g in _lines(part)]


def build():
    corridors = unary_union([box(x0, y0, x1, y1) for x0, y0, x1, y1, _ in CORRIDORS])
    building = box(0, 0, W, H)

    # 매장 후보: 통로 직사각형 네 변마다 바깥쪽으로 SHOP_D 깊이, SHOP_W 폭.
    shops: list = []
    doors: list = []
    taken = corridors.buffer(-0.01)
    for x0, y0, x1, y1, _ in CORRIDORS:
        sides = [
            ((x0, y0), (x1, y0), (0, -1)),  # 남쪽 변 → 아래로
            ((x0, y1), (x1, y1), (0, 1)),
            ((x0, y0), (x0, y1), (-1, 0)),
            ((x1, y0), (x1, y1), (1, 0)),
        ]
        for (ax, ay), (bx, by), (nx, ny) in sides:
            horizontal = ay == by
            length = (bx - ax) if horizontal else (by - ay)
            t = 0.0
            while t + SHOP_W <= length + 1e-6:
                if horizontal:
                    sx0, sx1 = ax + t, ax + t + SHOP_W
                    sy0, sy1 = (ay - SHOP_D, ay) if ny < 0 else (ay, ay + SHOP_D)
                    door_mid = ((sx0 + sx1) / 2, ay)
                else:
                    sy0, sy1 = ay + t, ay + t + SHOP_W
                    sx0, sx1 = (ax - SHOP_D, ax) if nx < 0 else (ax, ax + SHOP_D)
                    door_mid = (ax, (sy0 + sy1) / 2)
                shop = box(sx0, sy0, sx1, sy1)
                t += SHOP_W
                if not building.contains(shop):
                    continue
                if shop.buffer(-0.01).intersects(taken):
                    continue
                # 문 앞이 정말 통로여야 한다 (모서리에서 벽만 맞댄 자리 제외).
                probe = box(door_mid[0] - 0.5, door_mid[1] - 0.5, door_mid[0] + 0.5, door_mid[1] + 0.5)
                if corridors.intersection(probe).area < 0.2:
                    continue
                shops.append(shop)
                taken = taken.union(shop.buffer(-0.01))
                half = DOOR / 2
                if horizontal:
                    doors.append(LineString([(door_mid[0] - half, door_mid[1]), (door_mid[0] + half, door_mid[1])]))
                else:
                    doors.append(LineString([(door_mid[0], door_mid[1] - half), (door_mid[0], door_mid[1] + half)]))

    # 매장 뒤에 남는 빈 땅을 없앤다. 남겨 두면 외벽과 매장 뒷벽 사이가 통로보다 큰
    # 빈 공간이 돼 분석이 그쪽을 건물로 집는다(2026-10-22 실측). 통로에 닿는 조각은
    # 문을 낸 큰 매장으로, 안 닿는 조각은 맞닿은 매장에 붙여 넓힌다.
    leftover = building.difference(unary_union([corridors, *shops]))
    for piece in sorted(getattr(leftover, "geoms", [leftover]), key=lambda g: -g.area):
        if piece.area < 1:
            continue
        edge = piece.boundary.intersection(corridors.boundary)
        if edge.length > DOOR + 1:
            line = max(_lines(edge), key=lambda g: g.length)
            mid = line.interpolate(0.5, normalized=True)
            (ax, ay), (bx, by) = line.coords[0], line.coords[-1]
            ux, uy = (bx - ax) / line.length, (by - ay) / line.length
            half = DOOR / 2
            doors.append(LineString([(mid.x - ux * half, mid.y - uy * half), (mid.x + ux * half, mid.y + uy * half)]))
            shops.append(piece)
            continue
        touching = [(piece.boundary.intersection(s.boundary).length, i) for i, s in enumerate(shops)]
        shared, idx = max(touching, default=(0, -1))
        if shared > 0:
            shops[idx] = unary_union([shops[idx], piece])

    gaps = unary_union([d.buffer(0.05, cap_style=2) for d in doors])
    entrance_gap = box(ENTRANCE[0], -0.1, ENTRANCE[1], 0.1)

    walls = []
    walls += _lines(corridors.boundary.difference(gaps).difference(entrance_gap))
    for shop in shops:
        walls += _lines(shop.boundary.difference(gaps))
    walls += _lines(building.boundary.difference(entrance_gap))

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    for name, color in (("WALL", 7), ("DOOR", 3), ("ROOM_NAME", 4), ("SYMBOL", 1)):
        doc.layers.add(name, color=color)
    msp = doc.modelspace()
    for line in walls:
        coords = list(line.coords)
        if line.length > 0.2:
            msp.add_lwpolyline(coords, dxfattribs={"layer": "WALL"})
    for d in doors:
        msp.add_line(d.coords[0], d.coords[1], dxfattribs={"layer": "DOOR"})
    msp.add_line((ENTRANCE[0], 0), (ENTRANCE[1], 0), dxfattribs={"layer": "DOOR"})

    text = {"layer": "ROOM_NAME", "height": 3.0}
    for x0, y0, x1, y1, name in CORRIDORS:
        if name:
            msp.add_text(name, dxfattribs=text).set_placement(((x0 + x1) / 2, (y0 + y1) / 2))
    small = {"layer": "ROOM_NAME", "height": 2.2}
    for i, shop in enumerate(shops, start=1):
        # 붙여 넓힌 매장은 ㄱ자라 무게중심이 옆 매장에 떨어진다 — 안쪽 점을 쓴다.
        c = shop.representative_point()
        msp.add_text(f"매장 {i:02d}", dxfattribs=small).set_placement((c.x, c.y))

    def block(name, size):
        blk = doc.blocks.new(name=name)
        s = size / 2
        blk.add_lwpolyline([(-s, -s), (s, -s), (s, s), (-s, s)], close=True)
        blk.add_line((-s, -s), (s, s))

    for name, size in (("STAIR", 4.0), ("ELEVATOR", 3.0), ("ENTRANCE", 2.5), ("EXIT", 2.0)):
        block(name, size)
    sym = {"layer": "SYMBOL"}
    # 160×105m 배치 기준 (2026-10-24 고침 — 300×200m 때 좌표가 남아 기호가 건물 밖에 있었다).
    msp.add_blockref("ENTRANCE", (80, 1.5), dxfattribs=sym)  # 주출입 통로 입구
    msp.add_blockref("STAIR", (64, 62), dxfattribs=sym)  # 중앙 광장 서쪽
    msp.add_blockref("ELEVATOR", (96, 62), dxfattribs=sym)  # 중앙 광장 동쪽
    msp.add_blockref("STAIR", (80, 90), dxfattribs=sym)  # 북쪽 순환 통로
    msp.add_blockref("EXIT", (146, 10), dxfattribs=sym)  # 동쪽 날개 끝
    return doc, len(shops)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc, n = build()
    doc.saveas(OUT)
    print("wrote", OUT, "shops", n)


if __name__ == "__main__":
    main()
