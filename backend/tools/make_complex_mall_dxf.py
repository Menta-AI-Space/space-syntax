"""Generate a deliberately uneven mall plan — the one that makes the heat map talk.

``make_synthetic_mall_dxf.py`` draws a single straight corridor, so every shop
sits the same number of turns from every other and the map comes out almost one
colour (measured: best/mean integration 1.30). It proves the pipeline runs; it
cannot show *"this route is good, that one is dead"*.

Real malls are uneven, and the unevenness is the product. This plan is built to
have a clear best and a clear worst:

    ┌──────────── 북 날개 (막다른 끝) ───────────┐
    │                                            │
    ├──── 서 앵커 ──┬── 중앙 아트리움 ──┬── 동 앵커 ────┤
    │               │   (교차로)        │              │
    │          지하 서비스 복도(막다름)   └── 남동 골목 ──┘
    └────────────────────────────────────────────┘

- **중앙 아트리움**이 세 방향 통로가 만나는 교차로다 → 목 최상위.
- **북 날개 끝**과 **서비스 복도**는 한쪽만 뚫린 막다른 길 → 목 최하위.
- **남동 골목**은 통로에서 한 번 더 꺾여 들어가는 2차 동선 → 중간.
- 매장 크기를 통로 위치에 따라 다르게 둬서 「공간별 비교」 표도 단조롭지 않다.

Run from ``backend/``::

    python tools/make_complex_mall_dxf.py
"""

from __future__ import annotations

from pathlib import Path

import ezdxf

OUT = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "complex-mall.dxf"

W, H = 200.0, 160.0          # 건물 전체 (m)
CORRIDOR = 10.0              # 통로 폭
DOOR = 3.0                   # 매장 문 너비


def _mid_door(msp, x1, y1, x2, y2, width=DOOR):
    """벽 한 장을 그리되 가운데를 문만큼 비운다."""
    if abs(y2 - y1) < 1e-9:  # 수평
        cx = (x1 + x2) / 2
        msp.add_line((x1, y1), (cx - width / 2, y1), dxfattribs={"layer": "WALL"})
        msp.add_line((cx + width / 2, y1), (x2, y2), dxfattribs={"layer": "WALL"})
        msp.add_line((cx - width / 2, y1), (cx + width / 2, y1), dxfattribs={"layer": "DOOR"})
    else:                    # 수직
        cy = (y1 + y2) / 2
        msp.add_line((x1, y1), (x1, cy - width / 2), dxfattribs={"layer": "WALL"})
        msp.add_line((x1, cy + width / 2), (x2, y2), dxfattribs={"layer": "WALL"})
        msp.add_line((x1, cy - width / 2), (x1, cy + width / 2), dxfattribs={"layer": "DOOR"})


def build():
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    for name, color in (("WALL", 7), ("DOOR", 3), ("ROOM_NAME", 4), ("SYMBOL", 1)):
        doc.layers.add(name, color=color)
    msp = doc.modelspace()
    wall = lambda x1, y1, x2, y2: msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "WALL"})
    door = lambda x1, y1, x2, y2: msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "DOOR"})
    text = {"layer": "ROOM_NAME", "height": 2.5}
    label = lambda s, x, y: msp.add_text(s, dxfattribs=text).set_placement((x, y))

    # ── 외벽 (주출입구는 남쪽 가운데 하나뿐 — 이게 깊이 차이를 만든다) ──
    wall(0, 0, 95, 0); wall(105, 0, W, 0); door(95, 0, 105, 0)
    wall(0, H, W, H); wall(0, 0, 0, H); wall(W, 0, W, H)

    # ── 주 통로 (남→북, 입구에서 아트리움까지) ──
    main_x0, main_x1 = 95.0, 105.0
    atrium_y0, atrium_y1 = 60.0, 80.0
    wall(main_x0, 0, main_x0, atrium_y0)
    wall(main_x1, 0, main_x1, atrium_y0)

    # ── 중앙 아트리움 (교차로) ──
    wall(60, atrium_y0, main_x0, atrium_y0)
    wall(main_x1, atrium_y0, 140, atrium_y0)
    wall(60, atrium_y1, 85, atrium_y1)
    wall(115, atrium_y1, 140, atrium_y1)
    wall(60, atrium_y0, 60, atrium_y1)
    wall(140, atrium_y0, 140, atrium_y1)
    label("중앙 아트리움", 100, 70)

    # ── 동서 통로 (아트리움에서 양쪽 앵커로) ──
    ew_y0, ew_y1 = 65.0, 75.0
    wall(24, ew_y0, 60, ew_y0); wall(24, ew_y1, 60, ew_y1)
    wall(140, ew_y0, 176, ew_y0); wall(140, ew_y1, 176, ew_y1)

    # ── 북 날개 (아트리움에서 위로, 끝이 막힌다) ──
    nw_x0, nw_x1 = 85.0, 115.0
    wall(nw_x0, atrium_y1, nw_x0, 140); wall(nw_x1, atrium_y1, nw_x1, 140)
    wall(nw_x0, 140, nw_x1, 140)  # 막다른 끝
    label("북 날개 끝", 100, 136)

    # ── 남동 골목 (동서 통로에서 한 번 더 꺾여 들어간다) ──
    wall(150, 20, 150, ew_y0); wall(166, 20, 166, ew_y0)
    wall(150, 20, 166, 20)
    label("남동 골목", 158, 24)

    # ── 서비스 복도 (서쪽 앵커 뒤, 좁고 한쪽만 뚫림) ──
    wall(6, 20, 6, 50); wall(16, 20, 16, 50); wall(6, 20, 16, 20)
    door(6, 50, 16, 50)
    label("서비스 복도", 11, 30)

    # ── 앵커 매장 (서/동 끝) ──
    wall(24, 0, 24, ew_y0); wall(24, ew_y1, 24, H)
    _mid_door(msp, 24, ew_y0, 24, ew_y1)
    label("서 앵커매장", 12, 100)
    wall(176, 0, 176, ew_y0); wall(176, ew_y1, 176, H)
    _mid_door(msp, 176, ew_y0, 176, ew_y1)
    label("동 앵커매장", 188, 100)

    # ── 매장: 통로마다 크기를 다르게 둔다 ──
    n = 1

    def shops(x0, x1, y_wall, depth, width, side, prefix):
        """통로 한쪽 면에 매장을 늘어놓는다. side=+1이면 통로 위쪽."""
        nonlocal n
        x = x0
        while x + width <= x1 + 1e-6:
            wall(x, y_wall, x, y_wall + depth * side)
            _mid_door(msp, x, y_wall, x + width, y_wall)
            label(f"{prefix}{n:02d}", x + width / 2, y_wall + depth * side / 2)
            n += 1
            x += width
        wall(x, y_wall, x, y_wall + depth * side)

    # 동서 통로 양옆 — 목이 좋은 자리 (큰 매장)
    shops(24, 60, ew_y0, -(ew_y0 - 0) + 40, 12, -1, "매장 ")
    shops(140, 176, ew_y0, -(ew_y0 - 0) + 40, 12, -1, "매장 ")
    shops(24, 60, ew_y1, 40, 12, 1, "매장 ")
    shops(140, 176, ew_y1, 40, 12, 1, "매장 ")

    # 북 날개 양옆 — 깊은 자리 (작은 매장)
    shops(atrium_y1 + 0, 0, 0, 0, 1, 1, "")  # no-op guard
    x = nw_x1
    y = atrium_y1
    while y + 20 <= 140:
        wall(nw_x1, y, W - 24, y)
        _mid_door(msp, nw_x1, y, nw_x1, y + 20)
        label(f"북{n:02d}", 130, y + 10)
        n += 1
        y += 20
    y = atrium_y1
    while y + 20 <= 140:
        wall(24, y, nw_x0, y)
        _mid_door(msp, nw_x0, y, nw_x0, y + 20)
        label(f"북{n:02d}", 55, y + 10)
        n += 1
        y += 20

    # 남동 골목 양옆 — 가장 외진 자리
    for y in (20, 35, 50):
        wall(166, y, W - 24, y)
        _mid_door(msp, 166, y, 166, min(y + 15, ew_y0))
        label(f"골목{n:02d}", 172, y + 7)
        n += 1

    # ── 심볼 ──
    def block(name, size):
        blk = doc.blocks.new(name=name)
        s = size / 2
        blk.add_lwpolyline([(-s, -s), (s, -s), (s, s), (-s, s)], close=True)
        blk.add_line((-s, -s), (s, s))

    for name, size in (("STAIR", 4.0), ("ELEVATOR", 3.0), ("ENTRANCE", 2.5), ("EXIT", 2.0)):
        block(name, size)
    sym = {"layer": "SYMBOL"}
    msp.add_blockref("ENTRANCE", (100, 0), dxfattribs=sym)
    msp.add_blockref("STAIR", (92, 70), dxfattribs=sym)
    msp.add_blockref("ELEVATOR", (108, 70), dxfattribs=sym)
    msp.add_blockref("STAIR", (100, 130), dxfattribs=sym)
    msp.add_blockref("EXIT", (11, 50), dxfattribs=sym)
    return doc


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build().saveas(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
