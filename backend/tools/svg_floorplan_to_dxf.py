"""Convert a vector floor-plan SVG into a DXF the analyzer can read.

Why this exists: the synthetic mall (``make_synthetic_mall_dxf.py``) is a
straight corridor, so its heat map comes out nearly uniform — it cannot show
"this route is good, that one is dead". A real mall has branching corridors,
atria and dead-end wings, and that is where the numbers separate. The repo
already carried ``wtc-mall-floorplan.svg`` (a real mall plan, vector), which
the DXF-only upload rule blocked. This converts it once, offline, into the
vector format the pipeline does take.

How it reads the SVG:

- Every ``<path>`` is flattened to straight segments (cubic/quadratic curves
  are subdivided). Text glyphs and decoration are dropped by size: a path whose
  bounding box is smaller than ``--min-size`` is not a wall.
- Each resulting polygon becomes wall LINEs. Closed store outlines therefore
  enclose stores, and what is left between them is the circulation — exactly
  the space the analysis should walk.
- Stores drawn as sealed boxes would be cut out of the analysis, so a doorway
  is punched in each: the edge midpoint closest to the plan's open space, which
  in a mall is the shopfront. This is a heuristic and it is why the result is a
  **test fixture, not a survey** — see the note the tool prints.

Usage (from ``backend/``)::

    python tools/svg_floorplan_to_dxf.py ../frontend-next/public/samples/wtc-mall-floorplan.svg
"""

from __future__ import annotations

import argparse
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf

# 곡선 하나를 몇 조각으로 쪼갤지. 벽 윤곽에서 곡선은 드물고, 잘게 쪼개면
# 선분 수만 늘어 분석이 느려진다.
CURVE_STEPS = 6
# 문 너비(도면 단위). 몰 매장 전면은 보통 3~4m다.
DOOR_WIDTH = 12.0

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")
_TOKENS = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)")


def _flatten_path(d: str) -> list[list[tuple[float, float]]]:
    """SVG path -> list of point runs (one per subpath). Curves subdivided."""
    tokens = [(cmd, num) for cmd, num in _TOKENS.findall(d)]
    runs: list[list[tuple[float, float]]] = []
    points: list[tuple[float, float]] = []
    cx = cy = 0.0
    start = (0.0, 0.0)
    cmd = ""
    i = 0

    def nums(count: int) -> list[float]:
        nonlocal i
        out: list[float] = []
        while len(out) < count and i < len(tokens):
            c, n = tokens[i]
            if c:
                break
            out.append(float(n))
            i += 1
        return out

    def cubic(p0, p1, p2, p3):
        for step in range(1, CURVE_STEPS + 1):
            t = step / CURVE_STEPS
            u = 1 - t
            x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
            y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
            points.append((x, y))

    while i < len(tokens):
        token_cmd, _ = tokens[i]
        if token_cmd:
            cmd = token_cmd
            i += 1
        elif not cmd:
            i += 1
            continue

        rel = cmd.islower()
        key = cmd.upper()

        if key == "M":
            xy = nums(2)
            if len(xy) < 2:
                break
            cx, cy = (cx + xy[0], cy + xy[1]) if rel else (xy[0], xy[1])
            if len(points) > 1:
                runs.append(points)
            points = [(cx, cy)]
            start = (cx, cy)
            cmd = "l" if rel else "L"  # 이후 좌표쌍은 lineto로 이어진다 (SVG 규칙)
        elif key == "L":
            xy = nums(2)
            if len(xy) < 2:
                break
            cx, cy = (cx + xy[0], cy + xy[1]) if rel else (xy[0], xy[1])
            points.append((cx, cy))
        elif key == "H":
            v = nums(1)
            if not v:
                break
            cx = cx + v[0] if rel else v[0]
            points.append((cx, cy))
        elif key == "V":
            v = nums(1)
            if not v:
                break
            cy = cy + v[0] if rel else v[0]
            points.append((cx, cy))
        elif key == "C":
            v = nums(6)
            if len(v) < 6:
                break
            base = (cx, cy)
            pts = [(v[0], v[1]), (v[2], v[3]), (v[4], v[5])]
            if rel:
                pts = [(cx + px, cy + py) for px, py in pts]
            cubic(base, pts[0], pts[1], pts[2])
            cx, cy = pts[2]
        elif key in ("S", "Q", "T"):
            # 근사: 제어점을 무시하고 끝점까지 직선으로 잇는다. 벽 윤곽에서
            # 이 명령은 드물고, 정확히 살려도 격자 해상도에서 차이가 없다.
            need = {"S": 4, "Q": 4, "T": 2}[key]
            v = nums(need)
            if len(v) < need:
                break
            ex, ey = v[-2], v[-1]
            cx, cy = (cx + ex, cy + ey) if rel else (ex, ey)
            points.append((cx, cy))
        elif key == "A":
            v = nums(7)
            if len(v) < 7:
                break
            ex, ey = v[5], v[6]
            cx, cy = (cx + ex, cy + ey) if rel else (ex, ey)
            points.append((cx, cy))
        elif key == "Z":
            if points:
                points.append(start)
                runs.append(points)
                points = [start]
                cx, cy = start
        else:
            i += 1

    if len(points) > 1:
        runs.append(points)
    return runs


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _is_closed(points: list[tuple[float, float]]) -> bool:
    return math.dist(points[0], points[-1]) < 1e-6


def _dedup(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """겹친 점을 지운다. 단 **닫는 점은 살린다** — 시작점과 같다고 지워 버리면
    닫힌 매장 윤곽이 열린 선으로 보여 문을 뚫을 대상에서 빠진다 (2026-09-30에
    203개 중 17개만 문이 났던 원인)."""
    closed = len(points) > 2 and math.dist(points[0], points[-1]) < 1e-6
    out = [points[0]]
    for p in points[1:]:
        if math.dist(p, out[-1]) > 0.5:
            out.append(p)
    if closed and math.dist(out[0], out[-1]) > 1e-6:
        out.append(out[0])
    return out


def convert(
    svg_path: Path,
    dxf_path: Path,
    *,
    min_size: float,
    plan_width_m: float,
    punch_doors: bool,
) -> dict:
    tree = ET.parse(svg_path)
    root = tree.getroot()
    ns = {"svg": "http://www.w3.org/2000/svg"}

    runs: list[list[tuple[float, float]]] = []
    for element in root.iter():
        if not element.tag.endswith("}path") and element.tag != "path":
            continue
        d = element.get("d")
        if not d:
            continue
        for run in _flatten_path(d):
            run = _dedup(run)
            if len(run) < 2:
                continue
            x0, y0, x1, y1 = _bbox(run)
            if max(x1 - x0, y1 - y0) < min_size:
                continue  # 글자·기호
            runs.append(run)

    if not runs:
        raise SystemExit("벽으로 쓸 만한 경로를 찾지 못했습니다 — --min-size를 낮춰 보세요.")

    all_points = [p for run in runs for p in run]
    min_x, min_y, max_x, max_y = _bbox(all_points)
    span_x = max(max_x - min_x, 1e-6)
    # SVG는 y가 아래로 커지고 DXF는 위로 커진다. 뒤집지 않으면 도면이 상하 반전된다.
    scale = plan_width_m / span_x

    def to_dxf(p: tuple[float, float]) -> tuple[float, float]:
        return ((p[0] - min_x) * scale, (max_y - p[1]) * scale)

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6  # metres
    doc.layers.add("WALL", color=7)
    doc.layers.add("DOOR", color=3)
    msp = doc.modelspace()

    # 가장 큰 윤곽은 건물 외곽이다 — 여기엔 문을 뚫지 않는다(주출입구는 따로).
    areas = [
        (max(_bbox(r)[2] - _bbox(r)[0], 0) * max(_bbox(r)[3] - _bbox(r)[1], 0), idx)
        for idx, r in enumerate(runs)
    ]
    outer_idx = max(areas)[1] if areas else -1
    centre = (sum(p[0] for p in all_points) / len(all_points), sum(p[1] for p in all_points) / len(all_points))

    walls = doors = 0
    for idx, run in enumerate(runs):
        door_edge = None
        if punch_doors and idx != outer_idx and _is_closed(run) and len(run) > 3:
            # 매장 전면은 통로 쪽이다. 통로가 어디인지 모르므로 도면 중심에
            # 가장 가까운 변을 전면으로 본다 — 몰은 가운데가 통로다.
            best = None
            for a, b in zip(run, run[1:]):
                length = math.dist(a, b)
                if length < DOOR_WIDTH * 1.5:
                    continue
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                distance = math.dist(mid, centre)
                if best is None or distance < best[0]:
                    best = (distance, a, b)
            if best is not None:
                door_edge = (best[1], best[2])

        for a, b in zip(run, run[1:]):
            if door_edge and (a, b) == door_edge:
                # 변 가운데를 문 너비만큼 비우고 양옆만 벽으로 그린다.
                length = math.dist(a, b)
                ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
                half = DOOR_WIDTH / 2
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                p1 = (mid[0] - ux * half, mid[1] - uy * half)
                p2 = (mid[0] + ux * half, mid[1] + uy * half)
                msp.add_line(to_dxf(a), to_dxf(p1), dxfattribs={"layer": "WALL"})
                msp.add_line(to_dxf(p2), to_dxf(b), dxfattribs={"layer": "WALL"})
                msp.add_line(to_dxf(p1), to_dxf(p2), dxfattribs={"layer": "DOOR"})
                walls += 2
                doors += 1
            else:
                msp.add_line(to_dxf(a), to_dxf(b), dxfattribs={"layer": "WALL"})
                walls += 1

    doc.saveas(dxf_path)
    return {
        "outlines": len(runs),
        "walls": walls,
        "doors": doors,
        "size_m": (span_x * scale, (max_y - min_y) * scale),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("svg", type=Path)
    parser.add_argument("-o", "--out", type=Path, default=None)
    parser.add_argument(
        "--min-size", type=float, default=12.0,
        help="이 크기(SVG 단위)보다 작은 경로는 글자·기호로 보고 버린다",
    )
    parser.add_argument(
        "--width-m", type=float, default=180.0,
        help="도면 가로 실치수(m). 원본에 치수가 없어 사람이 정해 준다",
    )
    parser.add_argument("--no-doors", action="store_true", help="매장 전면 문을 뚫지 않는다")
    args = parser.parse_args()

    out = args.out or args.svg.with_suffix(".dxf")
    stats = convert(
        args.svg, out,
        min_size=args.min_size,
        plan_width_m=args.width_m,
        punch_doors=not args.no_doors,
    )
    print(f"wrote {out}")
    print(
        f"  윤곽 {stats['outlines']}개 → 벽 {stats['walls']}개, 문 {stats['doors']}개, "
        f"크기 {stats['size_m'][0]:.0f}m × {stats['size_m'][1]:.0f}m"
    )
    print(
        "  ⚠️ 매장 문 위치와 실치수는 추정이다. 파이프라인 시연·시험용이고 "
        "실제 건물의 측량 결과가 아니다."
    )


if __name__ == "__main__":
    main()
