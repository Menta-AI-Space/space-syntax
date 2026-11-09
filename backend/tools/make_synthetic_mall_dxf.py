"""Generate the synthetic shopping-mall DXF used by tests and the demo.

Real free CAD samples are DWG with unclear licences, so the repo carries a
synthetic plan instead. Layout (metres, $INSUNITS=6): 240×120 building,
20 m central corridor at y=50..70, anchor stores at both ends (0..24 and
216..240), 16 shops each side of the corridor, main entrances on the north
and south walls at x=114..126.

It writes ``synthetic-mall-labeled.dxf``: the same plan as the committed
``synthetic-large-mall.dxf`` (walls + doors only, 2026-08-23) plus what a real
architect's export carries and the extractor learned to read on 2026-09-18 —
room names as TEXT on layer ROOM_NAME, stairs / elevators / entrances as
INSERT blocks on layer SYMBOL. The older file is left untouched so its
documented numbers (벽 46개·문 36개) stay valid; its 4 duplicated anchor walls
are not reproduced here.

Run from ``backend/``::

    python tools/make_synthetic_mall_dxf.py
"""

from __future__ import annotations

from pathlib import Path

import ezdxf

OUT_DIR = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples"

W, H = 240.0, 120.0
CORRIDOR_Y0, CORRIDOR_Y1 = 50.0, 70.0
ANCHOR_W = 24.0
SHOP_W = 12.0
SHOP_DOOR_W = 3.0
MAIN_DOOR = (114.0, 126.0)
ANCHOR_DOOR = (56.0, 59.0)


def _wall(msp, x1, y1, x2, y2):
    msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "WALL"})


def _door(msp, x1, y1, x2, y2):
    msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "DOOR"})


def _build(labeled: bool = True):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    for name, color in (("WALL", 7), ("DOOR", 3), ("ROOM_NAME", 4), ("SYMBOL", 1)):
        doc.layers.add(name, color=color)

    # Envelope with the two main entrances punched in.
    _wall(msp, 0, 0, MAIN_DOOR[0], 0)
    _wall(msp, MAIN_DOOR[1], 0, W, 0)
    _door(msp, MAIN_DOOR[0], 0, MAIN_DOOR[1], 0)
    _wall(msp, 0, H, MAIN_DOOR[0], H)
    _wall(msp, MAIN_DOOR[1], H, W, H)
    _door(msp, MAIN_DOOR[0], H, MAIN_DOOR[1], H)
    _wall(msp, 0, 0, 0, H)
    _wall(msp, W, 0, W, H)

    # Corridor walls.
    _wall(msp, 0, CORRIDOR_Y0, W, CORRIDOR_Y0)
    _wall(msp, 0, CORRIDOR_Y1, W, CORRIDOR_Y1)

    # Anchor partitions + their doors onto the corridor.
    for x in (ANCHOR_W, W - ANCHOR_W):
        _wall(msp, x, 0, x, CORRIDOR_Y0)
        _wall(msp, x, CORRIDOR_Y1, x, H)
        _door(msp, x, ANCHOR_DOOR[0], x, ANCHOR_DOOR[1])

    # Shops: partitions every SHOP_W between the anchors, doors centred on
    # each shop front.
    x = ANCHOR_W + SHOP_W
    while x < W - ANCHOR_W - 1e-6:
        _wall(msp, x, 0, x, CORRIDOR_Y0)
        _wall(msp, x, CORRIDOR_Y1, x, H)
        x += SHOP_W
    x = ANCHOR_W
    while x < W - ANCHOR_W - 1e-6:
        cx = x + SHOP_W / 2
        _door(msp, cx - SHOP_DOOR_W / 2, CORRIDOR_Y0, cx + SHOP_DOOR_W / 2, CORRIDOR_Y0)
        _door(msp, cx - SHOP_DOOR_W / 2, CORRIDOR_Y1, cx + SHOP_DOOR_W / 2, CORRIDOR_Y1)
        x += SHOP_W

    if not labeled:
        return doc

    # ── What a real export adds ───────────────────────────────────────────
    text = {"layer": "ROOM_NAME", "height": 2.5}
    msp.add_text("앵커매장 A", dxfattribs=text).set_placement((ANCHOR_W / 2, 25))
    msp.add_text("앵커매장 B", dxfattribs=text).set_placement((W - ANCHOR_W / 2, 25))
    msp.add_text("중앙통로", dxfattribs=text).set_placement((W / 2, 60))
    n = 1
    x = ANCHOR_W
    while x < W - ANCHOR_W - 1e-6:
        cx = x + SHOP_W / 2
        msp.add_text(f"매장 {n:02d}", dxfattribs=text).set_placement((cx, 25))
        msp.add_text(f"매장 {n + 1:02d}", dxfattribs=text).set_placement((cx, 95))
        n += 2
        x += SHOP_W

    def block(name: str, size: float):
        blk = doc.blocks.new(name=name)
        s = size / 2
        blk.add_lwpolyline([(-s, -s), (s, -s), (s, s), (-s, s)], close=True)
        blk.add_line((-s, -s), (s, s))
        return blk

    block("STAIR", 4.0)
    block("ELEVATOR", 3.0)
    block("ENTRANCE", 2.0)
    block("EXIT", 2.0)
    sym = {"layer": "SYMBOL"}
    # Stairs and lifts inside the corridor near both anchors, entrance
    # symbols on the two main doors, an emergency exit on the east wall.
    msp.add_blockref("STAIR", (ANCHOR_W + 4, 66), dxfattribs=sym)
    msp.add_blockref("STAIR", (W - ANCHOR_W - 4, 66), dxfattribs=sym)
    msp.add_blockref("ELEVATOR", (W / 2, 53), dxfattribs=sym)
    msp.add_blockref("ENTRANCE", (W / 2, 0), dxfattribs=sym)
    msp.add_blockref("ENTRANCE", (W / 2, H), dxfattribs=sym)
    msp.add_blockref("EXIT", (W, 60), dxfattribs=sym)
    return doc


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _build(labeled=True).saveas(OUT_DIR / "synthetic-mall-labeled.dxf")
    print("wrote", OUT_DIR / "synthetic-mall-labeled.dxf")


if __name__ == "__main__":
    main()
