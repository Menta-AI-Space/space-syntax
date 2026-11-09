"""Recover rooms from the wall grid and score each one.

The spec's tab 3 (공간 효율) needs "매장 07 · 32㎡ · 목 하위 15%" — a per-room
table. Until now the pipeline only had grid cells, so it could say "남동쪽 약
537㎡" but never "which store".

The trick is that we already rasterize walls twice over: once with the door
openings carved out (that grid is what VGA walks, because people walk through
doors) and once without. **Without** the openings carved, every enclosed space
is its own connected blob — which is exactly a room. So no polygonisation of
broken wall lines is needed; the rooms fall out of a second flood fill.

What this does not do: it cannot name a room that has no TEXT label near it,
and it cannot tell a shop from a corridor except by size and shape. Rooms that
leak into each other (a wall drawn with a gap) merge into one blob — the same
failure the coverage warning already reports.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

# 이 칸 수보다 작은 덩어리는 방이 아니라 벽 사이 틈이다.
MIN_ROOM_CELLS = 4
# 실명은 방 안에 있으면 그 방 것이다. 방 밖이면 이 칸 수 안쪽까지만 인정한다
# (실명을 방 경계 밖에 찍는 도면이 흔하다).
LABEL_SNAP_CELLS = 3.0


@dataclass
class Room:
    index: int
    name: str | None
    cell_count: int
    area_m2: float | None
    # 무게중심 (캔버스 좌표) — 화면 라벨·마커 위치.
    x: float
    y: float
    x_m: float | None
    y_m: float | None
    # 방 안 칸들의 지표 평균. 문을 뚫은 격자에서 계산한 값을 그대로 평균 낸다.
    integration_mean: float
    choice_mean: float
    connectivity_mean: float
    # 전체 방 중 이 방의 목 백분위 (0~100, 높을수록 목이 좋다).
    integration_percentile: float
    # 이 방이 분석 대상(가장 큰 연결 덩어리)에 포함된 비율. 낮으면 벽에 막혀
    # 빠진 방이라 지표를 믿을 수 없다.
    analyzed_pct: float


def _room_labels(blocked_no_openings: np.ndarray, exterior: np.ndarray) -> tuple[np.ndarray, int]:
    """Label enclosed free blobs. ``blocked_no_openings`` must be the wall
    raster *before* doors were carved, so rooms stay separated."""
    interior_free = ~blocked_no_openings & ~exterior
    labels, count = ndimage.label(interior_free)
    return labels, count


def detect_rooms(
    *,
    blocked_no_openings: np.ndarray,
    exterior: np.ndarray,
    analyzed_mask: np.ndarray,
    cell_size: float,
    cell_size_m: float | None,
    integration: np.ndarray,
    choice: np.ndarray,
    connectivity: np.ndarray,
    labels_xy: list[tuple[str, float, float]] | None = None,
    min_cells: int = MIN_ROOM_CELLS,
) -> list[Room]:
    """``integration``/``choice``/``connectivity`` are full grids (NaN where no
    cell was analyzed). ``labels_xy`` are room names in canvas coordinates."""
    room_ids, count = _room_labels(blocked_no_openings, exterior)
    if count == 0:
        return []

    names = labels_xy or []
    snap = LABEL_SNAP_CELLS * cell_size
    h, w = room_ids.shape

    # 실명 하나는 방 하나에만 붙는다. 방 안에 찍힌 실명은 그 방 것이고, 다른 방이
    # 「가까우니까」 가져가지 못한다 — 예전엔 실명 없는 큰 통로가 옆 매장 실명을
    # 끌어와 공간별 비교 표에 「매장 68」이 두 줄(405㎡·31㎡) 나왔다(2026-11-08).
    label_room: list[int] = []
    for _, lx, ly in names:
        col, row = int(lx // cell_size), int(ly // cell_size)
        label_room.append(int(room_ids[row, col]) if 0 <= row < h and 0 <= col < w else 0)

    blobs: list[tuple[int, np.ndarray, int, float, float]] = []
    for rid in range(1, count + 1):
        mask = room_ids == rid
        n_cells = int(mask.sum())
        if n_cells < min_cells:
            continue
        rs, cs = np.nonzero(mask)
        blobs.append((rid, mask, n_cells, (cs.mean() + 0.5) * cell_size, (rs.mean() + 0.5) * cell_size))
    kept = {blob[0] for blob in blobs}
    label_room = [rid if rid in kept else 0 for rid in label_room]

    room_name: dict[int, str] = {}
    for i, (text, _, _) in enumerate(names):
        rid = label_room[i]
        if rid and rid not in room_name:
            room_name[rid] = text
    # 방 밖에 찍힌 실명(경계 밖에 적는 도면)은 무게중심이 가장 가까운 실명 없는 방
    # 하나에만 준다.
    for i, (text, lx, ly) in enumerate(names):
        if label_room[i]:
            continue
        best = None
        for rid, _, _, x, y in blobs:
            if rid in room_name:
                continue
            distance = ((lx - x) ** 2 + (ly - y) ** 2) ** 0.5
            if distance <= snap and (best is None or distance < best[0]):
                best = (distance, rid)
        if best is not None:
            room_name[best[1]] = text

    rooms: list[Room] = []
    for rid, mask, n_cells, x, y in blobs:
        inside = mask & analyzed_mask

        def mean_of(grid: np.ndarray) -> float:
            values = grid[inside]
            values = values[~np.isnan(values)]
            return float(values.mean()) if values.size else 0.0

        name = room_name.get(rid)

        rooms.append(
            Room(
                index=len(rooms) + 1,
                name=name,
                cell_count=n_cells,
                area_m2=n_cells * cell_size_m ** 2 if cell_size_m else None,
                x=x,
                y=y,
                x_m=x * (cell_size_m / cell_size) if cell_size_m else None,
                y_m=y * (cell_size_m / cell_size) if cell_size_m else None,
                integration_mean=mean_of(integration),
                choice_mean=mean_of(choice),
                connectivity_mean=mean_of(connectivity),
                integration_percentile=0.0,
                analyzed_pct=100.0 * int(inside.sum()) / n_cells,
            )
        )

    # 백분위는 방들끼리 비교한 값이다 — 칸 백분위를 쓰면 큰 방이 불리해진다.
    scored = [r for r in rooms if r.analyzed_pct > 0]
    if scored:
        values = np.array([r.integration_mean for r in scored])
        for room in scored:
            room.integration_percentile = float((values <= room.integration_mean).mean() * 100.0)

    rooms.sort(key=lambda r: r.integration_mean, reverse=True)
    for i, room in enumerate(rooms, 1):
        room.index = i
    return rooms
