"""가상 물류센터 평면도 DXF — 로봇 통로 시연용 (2026-11-09, 해커톤 01).

쇼핑몰 샘플이 「손님 흐름」이라면 이 도면은 「로봇(AGV·AMR) 통로」를 보여 준다. 실제 물류센터
CAD는 공개돼 있지 않아 **합성 도면**이다(화면에도 그렇게 적는다). 레이어는 다른 샘플과 같다 —
WALL·DOOR·ROOM_NAME·SYMBOL, 단위 m($INSUNITS=6).

배치(50×37m): 남쪽 도크 문 4개, 랙 6줄(깊이 1.2m)을 가로로 두고 통로 폭을 일부러 다르게 —
3.4m(로봇 통과) · 1.4m(로봇 못 감, 사람만) · 2.4m(아슬아슬) — 가운데 세로 횡단 통로 4m, 동쪽 블록
3·4번째 랙은 벽에 붙여 그 사이를 **막다른 통로**로 만든다. 북쪽에 사무실·충전 구역·입고 검수장.
로봇 기준 통로 폭은 `floorplan_vga.ROBOT_AISLE_MIN_M`(1.6m). 건물을 60×40에서 줄인 이유: 격자 상한
2,200칸이라 칸이 1.04m였고 3.0m 통로가 2칸(=좁음)으로 읽혔다 — 0.92m 칸이면 3.4m 통로가 3칸이다.

    python tools/make_logistics_center_dxf.py
"""

from __future__ import annotations

from pathlib import Path

import ezdxf

OUT = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "logistics-center.dxf"

W, H = 50.0, 37.0
RACK_D = 1.2
RACK_LEN = 22.0  # 서쪽 블록 x 2~24
CROSS_X0, CROSS_X1 = 24.0, 28.0  # 가운데 세로 횡단 통로(4m)
EAST_LEN = 20.0  # 동쪽 블록 x 28~48 (벽까지 2m 남김)


def _build():
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    for name, color in (("WALL", 7), ("DOOR", 3), ("ROOM_NAME", 4), ("SYMBOL", 1)):
        doc.layers.add(name, color=color)

    def wall(x1, y1, x2, y2):
        msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "WALL"})

    def door(x1, y1, x2, y2):
        msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "DOOR"})

    def rack(x0, y0, length):
        """랙 한 줄 = 닫힌 네모(벽). 안은 막힌 공간."""
        msp.add_lwpolyline(
            [(x0, y0), (x0 + length, y0), (x0 + length, y0 + RACK_D), (x0, y0 + RACK_D)],
            close=True,
            dxfattribs={"layer": "WALL"},
        )
        # 랙 속은 비어 있지 않다 — 가운데 선을 하나 더 그어 격자 칸이 「벽에 막힌 빈 방」으로 잡히지 않게.
        msp.add_line((x0, y0 + RACK_D / 2), (x0 + length, y0 + RACK_D / 2), dxfattribs={"layer": "WALL"})

    def label(text, x, y):
        msp.add_text(text, dxfattribs={"layer": "ROOM_NAME", "height": 1.2}).set_placement((x, y))

    # 외벽 — 남쪽에 도크 문 4개(각 4m)
    docks = [(5, 9), (15, 19), (31, 35), (41, 45)]
    x = 0.0
    for a, b in docks:
        wall(x, 0, a, 0)
        door(a, 0, b, 0)
        x = b
    wall(x, 0, W, 0)
    wall(0, H, W, H)
    wall(0, 0, 0, H)
    wall(W, 0, W, H)

    # 북쪽 부속실(세로 8m): 사무실 · 충전 구역 · 입고 검수장 — 각각 문 하나
    y_room = H - 8.0
    wall(0, y_room, W, y_room)
    for x_div in (12.0, 25.0, 38.0):
        wall(x_div, y_room, x_div, H)
    for a in (5.0, 18.0, 31.0, 43.0):
        door(a, y_room, a + 2.0, y_room)
    label("사무실", 4.0, H - 4.5)
    label("로봇 충전구역", 14.5, H - 4.5)
    label("입고 검수장", 27.5, H - 4.5)
    label("포장·출고", 41.0, H - 4.5)

    # 랙 6줄 — 서쪽 블록(x 2~24)과 동쪽 블록(x 28~48), 사이 횡단 통로 4m.
    # 통로 폭을 줄마다 다르게: 아래부터 3.4 / 1.4(좁음) / 3.4 / 2.4 / 3.4
    gaps = [3.4, 1.4, 3.4, 2.4, 3.4]
    y = 5.0  # 도크 앞 5m는 비워 둔다(하역장)
    rows = []
    for i in range(6):
        rows.append(y)
        rack(2.0, y, RACK_LEN)
        # 동쪽 블록 3·4번째 랙(i=2,3)은 벽까지 붙여 그 사이 통로(폭 3.4m)를 **막다른 통로**로 만든다 —
        # 가운데 횡단 통로에서 들어가면 되돌아 나와야 한다.
        rack(CROSS_X1, y, W - CROSS_X1 if i in (2, 3) else EAST_LEN)
        y += RACK_D + (gaps[i] if i < 5 else 0)
    label("A 랙", 12.0, rows[0] + 0.3)
    label("B 랙", 12.0, rows[3] + 0.3)
    label("C 랙", 37.0, rows[0] + 0.3)
    label("D 랙", 37.0, rows[3] + 0.3)
    label("하역장", 23.0, 2.0)

    # 출입구 기호 블록은 넣지 않는다 — 기호 외곽선이 벽으로 잡혀 도크 앞에 「계산에서 빠진」 2×2칸이
    # 생겼다(2026-11-09 실측). 도크는 DOOR 개구부만으로 충분하다.
    return doc


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    _build().saveas(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
