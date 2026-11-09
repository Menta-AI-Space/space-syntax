"""Raster Visibility Graph Analysis (VGA) for uploaded floor plans.

Unlike ``space_syntax.py`` (segment-angular dual graph over the real road
network), an indoor floor plan has no road segments — it works on a 2D
occupancy grid instead: wall lines become blocked cells, the remaining free
cells become VGA nodes, and two nodes connect when they have unobstructed
line-of-sight (Turner's VGA). Integration reuses the same NAIN formula the
road-network engines use (``space_syntax.py`` / ``depthmapx_bridge.py``:
``node_count**1.2 / total_depth``) so the number means the same thing
everywhere in the app, even though the graph under it is different.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.sparse import csr_matrix

# Visibility-graph construction is effectively O(N^2) in the free-cell count,
# so this caps the grid to keep analysis inside the app's 5-second budget.
MAX_GRID_CELLS = 2200
MIN_GRID_DIM = 10
# Local integration radius (topological steps), the R3 of axial analysis.
LOCAL_RADIUS = 3
# Choice (betweenness) is O(N·E); above this many cells we sample pivots so
# the whole analysis still lands inside the 5-second budget. Measured on the
# synthetic mall (N=1512, E=395k): exact 2.7s, 400 pivots 0.65s, top-5 stable.
CHOICE_EXACT_MAX_NODES = 800
CHOICE_SAMPLE_PIVOTS = 400
# 시선 한 줄을 칸마다 몇 점씩 찍어 볼지 — `_line_of_sight_batch` 주석 참고 (depthmapX 대조로 정함).
LOS_SAMPLES_PER_CELL = 2
# 벽 끝점이 이 거리(격자 칸 대비 비율) 안에서 서로 어긋나 있으면 그은 사람이
# 이으려던 것으로 보고 메워 준다. 실무 도면은 선이 딱 맞물리지 않는 일이 흔하고,
# 한 칸짜리 틈으로도 바깥 공기가 새어 들어와 분석 면적이 조용히 줄어든다.
GAP_SNAP_CELLS = 1.5
# ⚠️ 칸 크기에만 비례하면 큰 도면에서 위험하다. 200×150m 도면은 한 칸이 3.7m라
# 허용 거리가 5.5m가 되는데, 그건 **문 폭(1~3m)보다 넓어 문을 막아 버린다.**
# 축척을 알면 실제 치수로 상한을 둔다 — 사람이 그리다 어긋나는 정도는 cm 단위지
# 미터 단위가 아니다.
GAP_SNAP_MAX_M = 0.5
# 공간 덩어리들의 경계 상자가 이 칸 수보다 떨어져 있으면 **한 장에 그린 별개
# 평면**으로 본다. 붙어 있으면 같은 건물 안의 막힌 방이다 — 조치가 정반대라
# (자르기 vs 문 찍기) 이 구분이 필요하다.
SEPARATE_PLAN_GAP_CELLS = 3
# 벽이 감싼 넓이 대비 실내로 인정된 넓이가 이 비율보다 작으면 외곽이 뚫린 것으로
# 본다 — 조용히 틀린 결과를 내는 대신 경고한다.
ENVELOPE_MIN_RATIO = 0.75


class FloorplanVGAError(Exception):
    pass


@dataclass
class WallSegment:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class GridCell:
    row: int
    col: int
    x: float
    y: float
    connectivity: float
    integration: float
    # Integration within LOCAL_RADIUS steps — how well the spot connects to
    # its immediate surroundings (vs. the whole floor).
    integration_local: float = 0.0
    # Mean topological depth to every other cell; lower = more central.
    mean_depth: float = 0.0
    # Betweenness — how often the cell lies on shortest paths between other
    # cells. High = "people pass through here" as opposed to "people end here".
    choice: float = 0.0
    # 로봇 통로용(2026-11-09): 이 칸을 지나는 통로의 폭 — 양쪽 벽 사이 열린 칸 수 × 칸 크기.
    # 축척을 알면 m, 모르면 도면 단위. 랙 사이 통로가 로봇 폭보다 좁은지 보려고 둔다.
    aisle_width: float = 0.0


@dataclass
class Spot:
    """A ranked location the explanation can point at."""

    x: float
    y: float
    value: float
    percentile: float
    x_m: float | None = None
    y_m: float | None = None


@dataclass
class ExcludedCell:
    """Open floor that never reached the analyzed component — i.e. walled off."""

    row: int
    col: int


@dataclass
class VGAResult:
    cols: int
    rows: int
    cell_size: float
    cells: list[GridCell]
    integration_mean: float
    integration_max: float
    connectivity_mean: float
    # Share of open floor area that ended up in the analyzed component. Low
    # values mean the walls sealed the plan into pieces and most of it was
    # dropped — the result describes one room, not the building.
    coverage_pct: float = 100.0
    integration_local_mean: float = 0.0
    mean_depth_mean: float = 0.0
    choice_max: float = 0.0
    choice_sampled: bool = False
    # Real-world size of one cell, when the plan's unit was known.
    cell_size_m: float | None = None
    # 방 복원용 — 문을 뚫기 전 벽 격자와 분석 대상 마스크. 화면에 나가지 않고
    # floorplan_rooms가 쓴다 (문을 뚫으면 방들이 한 덩어리로 붙어 버린다).
    blocked_no_openings: "np.ndarray | None" = None
    exterior_mask: "np.ndarray | None" = None
    analyzed_mask: "np.ndarray | None" = None
    integration_grid: "np.ndarray | None" = None
    choice_grid: "np.ndarray | None" = None
    connectivity_grid: "np.ndarray | None" = None
    best_spots: list[Spot] = field(default_factory=list)
    through_spots: list[Spot] = field(default_factory=list)
    dead_spots: list[Spot] = field(default_factory=list)
    # 로봇 통로(2026-11-09, 물류센터 샘플): 로봇이 지나갈 수 있는 바닥 비율(%)과 막다른 통로 끝.
    # 축척을 모르면 None/빈 목록 — 폭을 m로 잴 수 없다.
    robot_passable_pct: float | None = None
    robot_dead_ends: list[Spot] = field(default_factory=list)
    aisle_width_grid: "np.ndarray | None" = None
    robot_dead_end_cells: list[tuple[int, int]] = field(default_factory=list)
    # Open cells that were cut off from the analyzed component. The frontend
    # draws these so "54% 분석됨" points at the rooms it actually dropped
    # instead of leaving the user to hunt for the sealed doorway.
    excluded_cells: list[ExcludedCell] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 경고가 아니라 「이렇게 계산했습니다」 — 엔진이 알아서 바로잡은 것. warnings에 넣으면
    # 화면에서 주황 경고로 떠 무언가 잘못된 것처럼 읽힌다 (2026-11-08 UX 자동 개선).
    notes: list[str] = field(default_factory=list)


def _choose_grid(width: float, height: float, target_cells: int = MAX_GRID_CELLS) -> tuple[int, int, float]:
    """Pick a cell size so cols*rows stays near ``target_cells``."""
    area = max(width * height, 1.0)
    cell_size = math.sqrt(area / target_cells)
    cols = max(MIN_GRID_DIM, int(width / cell_size))
    rows = max(MIN_GRID_DIM, int(height / cell_size))
    return cols, rows, cell_size


def _draw_wall(blocked: np.ndarray, wall: WallSegment, cell_size: float) -> None:
    rows, cols = blocked.shape
    c1, r1 = wall.x1 / cell_size, wall.y1 / cell_size
    c2, r2 = wall.x2 / cell_size, wall.y2 / cell_size
    steps = int(max(abs(c2 - c1), abs(r2 - r1))) * 2 + 2

    def put(r: int, c: int) -> None:
        # A wall drawn exactly on the far edge (x == canvas_width) rounds to
        # index ``cols``, one past the last cell. Dropping it would leave the
        # plan open along that edge, which reads as a doorway to the outdoors
        # and lets the analysis leak out of the building. Snap it onto the
        # edge cell instead; anything further out really is off-sheet.
        if c == cols:
            c = cols - 1
        if r == rows:
            r = rows - 1
        if 0 <= c < cols and 0 <= r < rows:
            blocked[r, c] = True

    previous: tuple[int, int] | None = None
    for t in np.linspace(0.0, 1.0, steps):
        c = int(round(c1 + (c2 - c1) * t))
        r = int(round(r1 + (r2 - r1) * t))
        # 비스듬한 벽은 계단 모양으로 찍힌다. 그 계단은 상하좌우로는 막혀도
        # **모서리가 열려 있어**, 대각선까지 이웃으로 보는 이 파일의 채우기가
        # 그 틈으로 새어 나간다. 실측: 30도 돌린 도면의 반영률이 28%까지
        # 떨어졌다(건물 대부분이 '실외'로 판정됐다). 대각선으로 움직일 때
        # 한 칸을 더 찍어 틈을 없앤다.
        if previous is not None and previous[0] != r and previous[1] != c:
            put(previous[0], c)
        put(r, c)
        previous = (r, c)


def _snap_wall_gaps(walls: list[WallSegment], tol: float) -> tuple[list[WallSegment], int]:
    """Bridge hairline gaps between wall endpoints.

    A real drawing's lines rarely meet exactly — they overshoot or fall a few
    millimetres short. On the raster that tiny gap is a doorway, and the
    outdoor flood pours through it, so part of the building silently drops out
    of the analysis (measured: one missing wall segment cut the analyzed area
    from 1,415 cells to 1,118 while still reporting 100% coverage).

    Only endpoints are joined, and only within ``tol``; a real doorway is much
    wider than that, so this does not weld doors shut.
    """
    if not walls or tol <= 0:
        return walls, 0
    endpoints: list[tuple[float, float]] = []
    for w in walls:
        endpoints.append((w.x1, w.y1))
        endpoints.append((w.x2, w.y2))

    bridges: list[WallSegment] = []
    used: set[int] = set()
    # 점이 많아야 수천 개라 격자 버킷으로 이웃만 훑는다 (전수 비교는 O(N^2)).
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, (x, y) in enumerate(endpoints):
        buckets.setdefault((int(x // tol), int(y // tol)), []).append(i)

    for i, (x, y) in enumerate(endpoints):
        if i in used:
            continue
        cx, cy = int(x // tol), int(y // tol)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((cx + dx, cy + dy), ()):
                    if j <= i or j in used or j // 2 == i // 2:
                        continue  # 같은 선분의 두 끝은 잇지 않는다
                    ox, oy = endpoints[j]
                    distance = math.hypot(ox - x, oy - y)
                    if 1e-9 < distance <= tol:
                        bridges.append(WallSegment(x, y, ox, oy))
                        used.add(i)
                        used.add(j)
                        break
    return walls + bridges, len(bridges)


def _rasterize_walls(walls: list[WallSegment], cols: int, rows: int, cell_size: float) -> np.ndarray:
    blocked = np.zeros((rows, cols), dtype=bool)
    for wall in walls:
        _draw_wall(blocked, wall, cell_size)
    return blocked


def _carve_openings(
    blocked: np.ndarray, openings: list[WallSegment], cell_size: float
) -> np.ndarray:
    """Clear wall cells where a door/window entity crosses.

    CAD files draw walls as continuous lines and put doors on their own layer,
    so the wall layer alone seals every room. The door entities say where the
    holes belong; this punches them back in. The 1-cell margin keeps a doorway
    passable when the wall and the door land in adjacent cells after rounding.
    """
    if not openings:
        return blocked
    holes = _rasterize_walls(openings, blocked.shape[1], blocked.shape[0], cell_size)
    holes = ndimage.binary_dilation(holes, structure=np.ones((3, 3), dtype=bool))
    return blocked & ~holes


# Kernel widths (in cells) tried when sealing entrance gaps to find the
# outdoors. Which one is right depends on how wide the plan draws its
# entrances, so we try them all and keep whichever recovers the most indoor
# floor — see ``_exterior_mask``.
#
# ⚠️ 25·31이 있는 이유: **도면이 돌아가 있으면 출입구가 대각선 틈이 되고, 정사각
# 커널로는 같은 너비라도 훨씬 덜 막힌다.** 30도 돌린 도면에서 13까지만 쓰면 실내가
# 149칸으로 무너졌는데(반영률 27%), 25를 넣으면 1,028칸으로 제대로 잡혔다. 정방향
# 도면은 13에서 이미 최대라 결과가 그대로다(1,991칸). 실제 도면은 대지 방향을 따라
# 돌아가 있는 일이 흔하므로 이건 예외가 아니라 기본에 가깝다.
_SEAL_KERNELS = (3, 5, 7, 9, 13, 17, 21, 25, 31)


def _exterior_mask(blocked: np.ndarray) -> np.ndarray:
    """Find the open space *outside* the building.

    A site plan draws streets and blank margin around the building, and that
    outdoor space reaches the canvas edge. It is also usually the single
    biggest open region on the sheet — bigger than the corridors, which the
    tenant walls chop into pieces. Worse, entrance doorways are drawn as gaps,
    so outdoors and corridors fuse into one component: on the WTC mall plan
    that fused blob is 90% of the open floor and spans both the canvas edge
    and the deepest interior. Analyzing it averages the street in with the
    mall and the metrics stop meaning anything.

    So: seal the entrance gaps with a morphological closing, flood the
    still-open space inward from the canvas edge, and call that the exterior.
    The closing is used *only* to identify the exterior — the mask returned
    here is subtracted from the original grid, so interior doorways stay open
    and rooms keep connecting to corridors as they should.

    The kernel that seals entrances without also swallowing corridors differs
    per plan (the WTC plan wants 3, a skewed photo of the Lulu Mall plan wants
    13), so try several and keep the one leaving the most indoor floor.
    """
    free = ~blocked
    best: np.ndarray | None = None
    best_indoor = -1
    for k in _SEAL_KERNELS:
        # Pad with open space before closing. Two reasons: the outdoors really
        # does continue past the edge of the sheet, and ``binary_closing``
        # erodes against the array border, which would chew holes in a wall
        # drawn flush to the edge and flood the whole plan as "outdoors".
        pad = k + 1
        padded = np.pad(blocked, pad, mode="constant", constant_values=False)
        sealed = ndimage.binary_closing(padded, structure=np.ones((k, k), dtype=bool))
        labeled, num_labels = ndimage.label(~sealed, structure=np.ones((3, 3), dtype=int))
        if num_labels == 0:
            continue
        edge_labels = {
            int(v)
            for edge in (labeled[0, :], labeled[-1, :], labeled[:, 0], labeled[:, -1])
            for v in np.unique(edge)
            if v > 0
        }
        if not edge_labels:
            continue
        exterior = np.isin(labeled, list(edge_labels))[pad:-pad, pad:-pad]
        if not exterior.any():
            # The padding ring never got inside — the plan is cropped to the
            # building itself, so there is no outdoors to remove.
            continue
        # Score by the largest indoor component, not by total indoor area: an
        # over-aggressive kernel eats corridors, and that shows up here as the
        # remaining indoor space breaking into smaller pieces.
        indoor = free & ~exterior
        indoor_labeled, indoor_count = ndimage.label(indoor, structure=np.ones((3, 3), dtype=int))
        if indoor_count == 0:
            continue
        largest = int(
            ndimage.sum(indoor, indoor_labeled, index=range(1, indoor_count + 1)).max()
        )
        if largest > best_indoor:
            best_indoor = largest
            best = exterior
    return best if best is not None else np.zeros_like(free)


def _wall_hull_cells(blocked: np.ndarray) -> int:
    """Rough count of cells inside the walls' bounding box.

    A convex hull would be tighter, but the bounding box needs no extra
    dependency and the ratio only has to catch "a whole side is missing".
    """
    rows, cols = np.nonzero(blocked)
    if rows.size == 0:
        return 0
    return int((rows.max() - rows.min() + 1) * (cols.max() - cols.min() + 1))


def _largest_free_component(
    blocked: np.ndarray, exterior: np.ndarray | None = None
) -> np.ndarray:
    """Keep only the largest connected free-space region *inside* the building.

    A floor-plan scan usually carries dimension lines, text, and stray marks
    outside the walls — without this they'd show up as disconnected
    "rooms" with meaningless VGA values. The outdoors is dropped first
    (``_exterior_mask``); on a plan with no margin around the building that
    step is a no-op and this behaves as a plain largest-component pick.

    ``exterior`` lets a caller that already computed the mask pass it in;
    finding it costs several closings, so it is worth not doing twice.
    """
    free = ~blocked
    free = free & ~(_exterior_mask(blocked) if exterior is None else exterior)
    labeled, num_labels = ndimage.label(free, structure=np.ones((3, 3), dtype=int))
    if num_labels == 0:
        return np.zeros_like(free)
    sizes = ndimage.sum(free, labeled, index=range(1, num_labels + 1))
    largest_label = int(np.argmax(sizes)) + 1
    return labeled == largest_label


def _line_of_sight_batch(
    r0: int, c0: int, targets: np.ndarray, blocked: np.ndarray, flat_blocked: np.ndarray
) -> np.ndarray:
    """Vectorized visibility test from one cell to many target cells.

    ``targets`` is an (M, 2) array of (row, col). Returns a boolean array of
    length M — True where the straight line to that target crosses no
    blocked cell.

    Rays are sampled twice per cell along the dominant axis, and a diagonal
    hop between two samples is blocked when both cells beside it are walls —
    i.e. the ray would slip through the joint where two wall cells touch only
    at a corner. Checked against depthmapX (exact line geometry, same mask and
    grid; ``tests/test_floorplan_depthmapx_parity.py``) on 2026-10-26: the old
    one-sample-per-cell ray saw 4–6% more pairs than depthmapX, all of it
    through such joints and wall corners, and on complex-mall that moved the
    top-10% integration cells (overlap 59%, ρ 0.937). This version: overlap
    98%, ρ 0.995; showcase-mall 96%, ρ 0.992. Cost on this laptop: whole
    analysis 1.3→2.7s (complex) and 1.6→3.8s (showcase). Sampling even denser
    (8x, grazing corners blocked too) matched visible pairs almost exactly but
    took 54–75s — rejected.

    ``flat_blocked`` is ``blocked.ravel()``; indexing it once beats a 2-D fancy
    index, and float32 stepping avoids float64 rounding on every sample.
    """
    if len(targets) == 0:
        return np.zeros(0, dtype=bool)
    width = blocked.shape[1]
    dr = targets[:, 0] - r0
    dc = targets[:, 1] - c0
    span = int(max(np.abs(dr).max(), np.abs(dc).max()))
    t = np.linspace(0.0, 1.0, LOS_SAMPLES_PER_CELL * span + 1, dtype=np.float32)[None, :]
    rs = (r0 + dr[:, None].astype(np.float32) * t + 0.5).astype(np.int32)
    cs = (c0 + dc[:, None].astype(np.float32) * t + 0.5).astype(np.int32)
    hit = flat_blocked[rs * width + cs].any(axis=1)
    diagonal = (rs[:, 1:] != rs[:, :-1]) & (cs[:, 1:] != cs[:, :-1])
    side_a = flat_blocked[rs[:, :-1] * width + cs[:, 1:]]
    side_b = flat_blocked[rs[:, 1:] * width + cs[:, :-1]]
    hit |= (diagonal & side_a & side_b).any(axis=1)
    return ~hit


def _visibility_adjacency(free_mask: np.ndarray, blocked: np.ndarray) -> tuple[np.ndarray, csr_matrix]:
    coords = np.argwhere(free_mask)
    n = len(coords)
    if n == 0:
        raise FloorplanVGAError("추출된 벽 안에서 열린 공간을 찾지 못했습니다")

    flat_blocked = blocked.ravel()
    rows_parts: list[np.ndarray] = []
    cols_parts: list[np.ndarray] = []
    for i in range(n - 1):
        r0, c0 = coords[i]
        rest = coords[i + 1:]
        visible = _line_of_sight_batch(int(r0), int(c0), rest, blocked, flat_blocked)
        hits = np.nonzero(visible)[0] + i + 1
        if len(hits):
            rows_parts.append(np.full(len(hits), i, dtype=np.int32))
            cols_parts.append(hits.astype(np.int32))

    if rows_parts:
        upper_rows = np.concatenate(rows_parts)
        upper_cols = np.concatenate(cols_parts)
    else:
        upper_rows = np.zeros(0, dtype=np.int32)
        upper_cols = np.zeros(0, dtype=np.int32)
    # Only i<j pairs are tested; mirror them for the undirected graph.
    row_idx = np.concatenate([upper_rows, upper_cols])
    col_idx = np.concatenate([upper_cols, upper_rows])
    data = np.ones(len(row_idx), dtype=np.uint8)
    adjacency = csr_matrix((data, (row_idx, col_idx)), shape=(n, n))
    return coords, adjacency


def _all_pairs_depth(
    adjacency: csr_matrix, local_radius: int = LOCAL_RADIUS
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Total topological depth and reachable-node count for every node,
    globally and within ``local_radius`` steps.

    A visibility graph is unweighted and dense — in an open plan most cells see
    most others — so running N separate shortest-path searches spends its time
    re-walking the same edges. Instead grow reachability one step at a time with
    a matrix multiply and count how many nodes join at each depth; BLAS does that
    N^3 work in a single pass per depth level.

    Measured on a real shopping-mall plan (N=1575, 16% dense): scipy's Dijkstra
    5.09s, this 0.51s, identical output. The slow path broke the project's 5s
    rule (7.55s end-to-end); dropping grid resolution instead would have
    re-sealed the narrow doorways that _carve_openings exists to keep open.
    """
    n = adjacency.shape[0]
    dense = np.asarray(adjacency.todense(), dtype=bool)
    as_float = dense.astype(np.float32)

    # Depth 1 is just the direct neighbours.
    degree = dense.sum(axis=1).astype(np.float64)
    total_depth = degree.copy()
    node_count = degree.copy()
    local_depth = degree.copy()
    local_count = degree.copy()

    reached = dense | np.eye(n, dtype=bool)
    step = 1
    while True:
        step += 1
        nxt = ((reached.astype(np.float32) @ as_float) > 0) | reached
        newly = nxt & ~reached
        counts = newly.sum(axis=1)
        if not counts.any():
            break
        total_depth += step * counts
        node_count += counts
        if step <= local_radius:
            local_depth += step * counts
            local_count += counts
        reached = nxt
    return total_depth, node_count, local_depth, local_count


def _nain(node_count: np.ndarray, total_depth: np.ndarray) -> np.ndarray:
    return np.divide(
        node_count ** 1.2,
        total_depth,
        out=np.zeros_like(total_depth),
        where=total_depth > 0,
    )


def _choice(adjacency: csr_matrix, pivots: int | None, seed: int = 0) -> np.ndarray:
    """Betweenness (Brandes) driven by sparse mat-vecs instead of Python BFS.

    Each pivot grows shortest-path counts one level at a time with ``A @ x``
    and accumulates dependencies backwards the same way. With ``pivots`` set
    the pivot set is sampled and the result rescaled to the full count.
    """
    n = adjacency.shape[0]
    a = adjacency.tocsr().astype(np.float64)
    if pivots is None or pivots >= n:
        sources = np.arange(n)
        sampled = False
    else:
        sources = np.random.default_rng(seed).choice(n, pivots, replace=False)
        sampled = True

    between = np.zeros(n)
    for s in sources:
        sigma = np.zeros(n)
        sigma[s] = 1.0
        dist = np.full(n, -1)
        dist[s] = 0
        levels: list[np.ndarray] = [np.array([s])]
        depth = 0
        while True:
            depth += 1
            grown = a @ (sigma * (dist == depth - 1))
            fresh = (grown > 0) & (dist < 0)
            if not fresh.any():
                break
            dist[fresh] = depth
            sigma[fresh] = grown[fresh]
            levels.append(np.flatnonzero(fresh))
        delta = np.zeros(n)
        for level in range(len(levels) - 1, 0, -1):
            w = levels[level]
            coef = np.zeros(n)
            coef[w] = (1.0 + delta[w]) / sigma[w]
            delta += (a @ coef) * sigma * (dist == level - 1)
        delta[s] = 0.0
        between += delta
    if sampled:
        between *= n / len(sources)
    return between / 2.0


def _integration_connectivity(adjacency: csr_matrix) -> tuple[np.ndarray, np.ndarray]:
    connectivity = np.asarray(adjacency.sum(axis=1)).flatten().astype(float)
    total_depth, node_count, _, _ = _all_pairs_depth(adjacency)
    return _nain(node_count, total_depth), connectivity


def _rank_spots(
    cells: list[GridCell],
    key,
    *,
    reverse: bool,
    count: int,
    min_gap_cells: float,
    cell_size: float,
    meters_per_unit: float | None,
) -> list[Spot]:
    """Top-``count`` cells by ``key``, skipping ones that sit right next to an
    already-picked spot so the list names distinct places, not one blob."""
    values = np.array([key(c) for c in cells])
    order = np.argsort(-values if reverse else values)
    picked: list[GridCell] = []
    gap = min_gap_cells * cell_size
    for i in order:
        cell = cells[int(i)]
        if any(math.hypot(cell.x - p.x, cell.y - p.y) < gap for p in picked):
            continue
        picked.append(cell)
        if len(picked) >= count:
            break
    spots: list[Spot] = []
    for cell in picked:
        value = key(cell)
        percentile = float((values <= value).mean() * 100.0)
        spots.append(
            Spot(
                x=cell.x,
                y=cell.y,
                value=float(value),
                percentile=percentile,
                x_m=cell.x * meters_per_unit if meters_per_unit else None,
                y_m=cell.y * meters_per_unit if meters_per_unit else None,
            )
        )
    return spots


@dataclass
class AnalysisMask:
    """벽을 읽은 뒤 계산에 들어가기 직전까지의 격자들 — depthmapX 대조 브리지도
    같은 것을 쓴다(그래야 두 계산을 칸 대 칸으로 견줄 수 있다)."""

    blocked_no_openings: np.ndarray
    blocked: np.ndarray
    exterior: np.ndarray
    indoor: np.ndarray
    free_mask: np.ndarray
    bridged: int


def _analysis_mask(
    walls: list[WallSegment],
    cols: int,
    rows: int,
    cell_size: float,
    openings: list[WallSegment] | None,
    meters_per_unit: float | None,
) -> AnalysisMask:
    snap_tol = GAP_SNAP_CELLS * cell_size
    if meters_per_unit:
        # 실제 치수로 상한. 문보다 넓게 이어 붙이면 도면에 없는 벽을 만드는 셈이다.
        snap_tol = min(snap_tol, GAP_SNAP_MAX_M / meters_per_unit)
    walls, bridged = _snap_wall_gaps(walls, snap_tol)
    # 문·창 선도 **벽과 함께 그린 뒤에 뚫는다.** 그 자리는 실제로 벽체 두께가
    # 있는 자리이고, 안 그리면 외벽에 구멍이 남아 바깥이 새어 들어온다 —
    # `PLAN-1ST FLOOR.dwg`에서 창틀(`A-WX-SIL`)을 개구부로 알아보게 한 순간
    # 외곽 비율이 80%→52%로 떨어졌다(2026-10-01). 알아본 것이 손해가 되면 안 된다.
    blocked_no_openings = _rasterize_walls(walls + (openings or []), cols, rows, cell_size)
    blocked = _carve_openings(blocked_no_openings.copy(), openings or [], cell_size)
    exterior = _exterior_mask(blocked)
    indoor = ~blocked & ~exterior
    free_mask = _largest_free_component(blocked, exterior)
    return AnalysisMask(blocked_no_openings, blocked, exterior, indoor, free_mask, bridged)


def compute_floorplan_vga(
    walls: list[WallSegment],
    canvas_width: float,
    canvas_height: float,
    max_cells: int = MAX_GRID_CELLS,
    openings: list[WallSegment] | None = None,
    meters_per_unit: float | None = None,
) -> VGAResult:
    """Compute per-cell Integration/Connectivity for a traced floor plan.

    ``walls`` are wall line segments in the same pixel/canvas coordinate
    space as ``canvas_width``/``canvas_height`` — i.e. the user-corrected
    lines from the extraction step, not raw CV/CAD output. ``max_cells``
    exists mainly so tests can run on a coarser, faster grid.
    ``meters_per_unit`` (canvas unit → metres) is optional; when known the
    ranked spots also carry metre coordinates.
    """
    if canvas_width <= 0 or canvas_height <= 0:
        raise FloorplanVGAError("캔버스 크기가 올바르지 않습니다")
    if not walls:
        raise FloorplanVGAError("벽 선이 없어 분석할 수 없습니다")

    cols, rows, cell_size = _choose_grid(canvas_width, canvas_height, max_cells)
    warnings: list[str] = []
    notes: list[str] = []
    if cols * rows >= max_cells:
        warnings.append(
            f"도면이 커서 격자를 {cols}x{rows}로 단순화해 분석했습니다 (5초 내 분석 유지)."
        )

    mask = _analysis_mask(walls, cols, rows, cell_size, openings, meters_per_unit)
    if mask.bridged:
        notes.append(
            f"벽 끝이 살짝 어긋난 곳 {mask.bridged:,}군데를 이어 붙였습니다 — 그 틈으로 바깥이 새어 들어와 "
            "분석 면적이 줄어드는 것을 막기 위해서입니다."
        )
    blocked_no_openings, blocked = mask.blocked_no_openings, mask.blocked
    exterior, indoor, free_mask = mask.exterior, mask.indoor, mask.free_mask
    if not free_mask.any():
        raise FloorplanVGAError("추출된 벽 안에서 열린 공간을 찾지 못했습니다")

    # Coverage is measured against indoor floor only. Counting the street and
    # margin outside the building would report a low number and hatch the
    # outdoors as "rooms we couldn't reach", pointing the user at a doorway
    # that does not exist.
    free_total = int(indoor.sum())
    analyzed = int(free_mask.sum())
    coverage_pct = 100.0 * analyzed / max(free_total, 1)
    if coverage_pct < 70.0:
        # Silence here would be the dangerous outcome: the numbers below would
        # describe one room while looking like a whole-building analysis.
        #
        # 원인이 둘인데 조치가 정반대다. 문이 막힌 거면 문을 찍어야 하고, 한 장에
        # 평면이 여러 개 그려진 거면(층별 도면을 나란히 둔 도면) **자르는 게 답**
        # 이다. 덩어리가 몇 개인지로 갈라 말한다 — 한쪽 안내만 내면 사용자가
        # 없는 문을 찾아 헤맨다.
        chunks, chunk_count = ndimage.label(indoor, structure=np.ones((3, 3), dtype=int))
        boxes = []
        for label in range(1, chunk_count + 1):
            mask = chunks == label
            if int(mask.sum()) < max(20, free_total * 0.15):
                continue
            rs, cs = np.nonzero(mask)
            boxes.append((rs.min(), rs.max(), cs.min(), cs.max()))
        # 덩어리 개수만으로는 못 가른다 — **문이 막힌 방도 별개 덩어리다.** 갈리는
        # 건 거리다: 한 장에 나란히 그린 평면들은 사이에 빈 공간이 있고, 막힌 방은
        # 같은 건물 안에 붙어 있다.
        def far_apart(a, b) -> bool:
            row_gap = max(a[0] - b[1], b[0] - a[1])
            col_gap = max(a[2] - b[3], b[2] - a[3])
            return max(row_gap, col_gap) > SEPARATE_PLAN_GAP_CELLS

        separate = any(
            far_apart(boxes[i], boxes[j])
            for i in range(len(boxes))
            for j in range(i + 1, len(boxes))
        )
        big_chunks = len(boxes)
        if separate and big_chunks >= 2:
            warnings.append(
                f"이 도면엔 서로 이어지지 않은 공간 덩어리가 {big_chunks}개 있어 그중 가장 큰 "
                f"것만 분석했습니다({coverage_pct:.0f}%). 한 장에 여러 층·여러 동이 그려진 "
                "도면이면 그중 한 평면을 본 것입니다 — 다른 평면을 보려면 아래에서 고르세요."
            )
        else:
            warnings.append(
                f"도면에서 뚫린 공간의 {coverage_pct:.0f}%만 서로 이어져 있어 그 부분만 살펴봤습니다. "
                "문이 벽으로 막혀 있을 가능성이 높습니다 — 빗금 친 곳의 경계에서 실제 문이 있는 자리를 "
                "'문 자리 표시'로 찍고 다시 분석하면 전체를 함께 볼 수 있습니다."
            )

    # 외곽이 뚫려 있으면 바깥 공기가 안으로 들어와 실내가 작아진다. 반영률은
    # 실내 대비로 재기 때문에 이때도 100%가 나와 문제를 가린다 — 그래서 벽이
    # 감싼 넓이와 따로 견준다.
    wall_cells = int(blocked.sum())
    hull = _wall_hull_cells(blocked)
    if hull > 0:
        inside_ratio = (free_total + wall_cells) / hull
        if inside_ratio < ENVELOPE_MIN_RATIO:
            warnings.append(
                f"건물 외곽선이 닫혀 있지 않은 것 같습니다 — 벽이 감싼 범위의 {inside_ratio * 100:.0f}%만 "
                "실내로 잡혔습니다. 외벽이 끊긴 곳이 있는지 확인하고, 없다면 그 틈을 '벽 그리기'로 이어 주세요."
            )

    excluded = np.argwhere(indoor & ~free_mask)
    excluded_cells = [ExcludedCell(row=int(r), col=int(c)) for r, c in excluded]

    coords, adjacency = _visibility_adjacency(free_mask, blocked)
    connectivity = np.asarray(adjacency.sum(axis=1)).flatten().astype(float)
    total_depth, node_count, local_depth, local_count = _all_pairs_depth(adjacency)
    integration = _nain(node_count, total_depth)
    integration_local = _nain(local_count, local_depth)
    mean_depth = np.divide(
        total_depth, node_count, out=np.zeros_like(total_depth), where=node_count > 0
    )
    choice_sampled = adjacency.shape[0] > CHOICE_EXACT_MAX_NODES
    choice = _choice(adjacency, CHOICE_SAMPLE_PIVOTS if choice_sampled else None)

    # 통로 폭: 열린 칸에서 가장 가까운 막힌 칸까지의 거리(칸)로 양쪽 벽 사이 열린 칸 수를 센다.
    unit = cell_size * meters_per_unit if meters_per_unit else cell_size
    aisle_width_grid = _aisle_width_grid(free_mask, unit)
    cells = [
        GridCell(
            row=int(r),
            col=int(c),
            x=(float(c) + 0.5) * cell_size,
            y=(float(r) + 0.5) * cell_size,
            connectivity=float(connectivity[i]),
            integration=float(integration[i]),
            integration_local=float(integration_local[i]),
            mean_depth=float(mean_depth[i]),
            choice=float(choice[i]),
            aisle_width=float(aisle_width_grid[r, c]),
        )
        for i, (r, c) in enumerate(coords)
    ]
    robot_passable_pct, robot_dead_ends, robot_dead_end_cells = (
        _robot_summary(free_mask, aisle_width_grid, blocked_no_openings & ~blocked, cell_size, meters_per_unit)
        if meters_per_unit
        else (None, [], [])
    )

    integration_values = [cell.integration for cell in cells]
    connectivity_values = [cell.connectivity for cell in cells]
    def _grid(values: np.ndarray) -> np.ndarray:
        grid = np.full((rows, cols), np.nan)
        for i, (r, c) in enumerate(coords):
            grid[r, c] = values[i]
        return grid

    rank = dict(count=5, min_gap_cells=4.0, cell_size=cell_size, meters_per_unit=meters_per_unit)
    return VGAResult(
        cols=cols,
        rows=rows,
        cell_size=cell_size,
        cells=cells,
        integration_mean=float(np.mean(integration_values)) if integration_values else 0.0,
        integration_max=float(np.max(integration_values)) if integration_values else 0.0,
        connectivity_mean=float(np.mean(connectivity_values)) if connectivity_values else 0.0,
        coverage_pct=coverage_pct,
        integration_local_mean=float(np.mean(integration_local)) if cells else 0.0,
        mean_depth_mean=float(np.mean(mean_depth)) if cells else 0.0,
        choice_max=float(np.max(choice)) if cells else 0.0,
        choice_sampled=choice_sampled,
        cell_size_m=cell_size * meters_per_unit if meters_per_unit else None,
        blocked_no_openings=blocked_no_openings,
        exterior_mask=exterior,
        analyzed_mask=free_mask,
        integration_grid=_grid(integration),
        choice_grid=_grid(choice),
        connectivity_grid=_grid(connectivity),
        best_spots=_rank_spots(cells, lambda c: c.integration, reverse=True, **rank),
        through_spots=_rank_spots(cells, lambda c: c.choice, reverse=True, **rank),
        dead_spots=_rank_spots(cells, lambda c: c.integration, reverse=False, **rank),
        robot_passable_pct=robot_passable_pct,
        robot_dead_ends=robot_dead_ends,
        aisle_width_grid=aisle_width_grid,
        robot_dead_end_cells=robot_dead_end_cells,
        excluded_cells=excluded_cells,
        warnings=warnings,
        notes=notes,
    )


# 로봇 통로 기준(2026-11-09): 물류 로봇(AGV·AMR) 폭 약 1.0m + 양쪽 여유 0.3m. 랙 사이 통로가 이보다
# 좁으면 로봇이 못 들어간다고 본다. 사람 기준이 아니라 로봇 기준 하나만 둔다 — 늘리면 화면이 복잡해진다.
ROBOT_AISLE_MIN_M = 1.6
# 막다른 통로 끝 찾기 — 아직 엉뚱한 자리를 잡아 꺼 둠(위 _robot_summary 주석). 고치면 켠다.
DEAD_ENDS_ENABLED = False


def _aisle_width_grid(free_mask: np.ndarray, unit: float) -> np.ndarray:
    """칸마다 「그 칸이 속한 통로의 폭」 = 그 칸을 담는 가장 큰 원의 지름(국소 두께).

    가장 가까운 벽까지 거리만 쓰면 3칸 통로의 가장자리 칸이 「폭 1칸」으로 읽혀 로봇 통로 비율이
    셋 중 하나로 줄었다(2026-11-09 실측 32%). 거리값 d가 큰 칸부터 반지름 d−1만큼 넓혀 아직 폭이
    없는 칸에 (2d−1)칸 폭을 준다. 거리 단계가 열 개 남짓이라 가볍다.
    """
    dist = ndimage.distance_transform_edt(free_mask)
    rounded = np.round(dist).astype(int)
    width_cells = np.zeros(free_mask.shape, dtype=float)
    for d in sorted({int(v) for v in np.unique(rounded[free_mask])}, reverse=True):
        if d <= 0:
            continue
        core = free_mask & (rounded == d)
        if d > 1:
            # 반지름 d 원판으로 넓힌다 — 네모(체비쇼프)로 넓히면 넓은 홀의 원이 옆 통로 안으로 새어 들어가
            # 3m 통로가 7m로 읽혔다(2026-11-09 실측).
            r = d
            yy, xx = np.ogrid[-r : r + 1, -r : r + 1]
            disk = (xx * xx + yy * yy) <= r * r
            grown = ndimage.binary_dilation(core, structure=disk) & free_mask
        else:
            grown = core
        fill = grown & (width_cells == 0)
        width_cells[fill] = max(2.0 * d - 1.0, 1.0)
    width_cells[free_mask & (width_cells == 0)] = 1.0
    width = width_cells * unit
    width[~free_mask] = 0.0
    return width


def _run_lengths(mask: np.ndarray, dr: int, dc: int, limit: int) -> np.ndarray:
    """칸마다 (dr, dc) 방향으로 연속해 열린 칸 수(최대 limit). 이웃이 막혔으면 0."""
    out = np.zeros(mask.shape, dtype=int)
    alive = mask.copy()
    rows, cols = mask.shape
    for step in range(1, limit + 1):
        shifted = np.zeros_like(mask)
        r0, r1 = max(0, -dr * step), min(rows, rows - dr * step)
        c0, c1 = max(0, -dc * step), min(cols, cols - dc * step)
        if r0 >= r1 or c0 >= c1:
            break
        shifted[r0:r1, c0:c1] = mask[r0 + dr * step : r1 + dr * step, c0 + dc * step : c1 + dc * step]
        alive &= shifted
        out[alive] = step
    return out


def _dead_end_tips(passable: np.ndarray, long_run: int = 3, side_max: int = 2, limit: int = 6) -> np.ndarray:
    """막다른 통로의 끝 칸들.

    한 축으로는 한쪽이 길게 트이고(≥ long_run) 반대쪽은 바로 막혔으며, 그 직각 축은 양쪽 다 짧다
    (≤ side_max, 즉 통로 안이지 홀 가장자리가 아니다). 이웃 수로 세면 한 칸짜리 통로 끝만 잡힌다
    (3칸 폭 통로 끝은 이웃이 둘셋이다).
    """
    up, down = _run_lengths(passable, -1, 0, limit), _run_lengths(passable, 1, 0, limit)
    left, right = _run_lengths(passable, 0, -1, limit), _run_lengths(passable, 0, 1, limit)
    horiz_tip = ((left >= long_run) & (right == 0) | (right >= long_run) & (left == 0)) & (up <= side_max) & (down <= side_max)
    vert_tip = ((up >= long_run) & (down == 0) | (down >= long_run) & (up == 0)) & (left <= side_max) & (right <= side_max)
    return passable & (horiz_tip | vert_tip)


def _robot_summary(
    free_mask: np.ndarray,
    aisle_width_grid: np.ndarray,
    opening_cells: np.ndarray,
    cell_size: float,
    meters_per_unit: float,
    min_width_m: float = ROBOT_AISLE_MIN_M,
) -> tuple[float, list[Spot], list[tuple[int, int]]]:
    """(로봇이 지나갈 수 있는 바닥 비율 %, 막다른 통로 끝 자리 ≤5, 끝 칸 전부).

    막다른 끝 = 로봇이 갈 수 있는 칸 가운데 그런 이웃이 하나뿐인 칸. 문(개구부)에 붙은 끝은 밖으로
    나가는 자리라 빼고, 붙어 있는 끝 칸들은 한 곳으로 센다.
    """
    passable = free_mask & (aisle_width_grid >= min_width_m)
    total = int(free_mask.sum())
    pct = 100.0 * float(passable.sum()) / total if total else 0.0
    if not passable.any():
        return round(pct, 1), [], []
    # 2026-11-09: 끝 찾기(_dead_end_tips)는 물류센터 샘플에서 횡단 통로 가장자리·벽 옆 2칸 틈을 「끝」으로
    # 잡았다(진짜 막다른 통로는 5개 상한 밖). 시연 전까지 못 고쳐 **꺼 둔다** — 비율·좁은 통로 카드만 쓴다.
    if not DEAD_ENDS_ENABLED:
        return round(pct, 1), [], []
    ends = _dead_end_tips(passable)
    near_opening = ndimage.binary_dilation(opening_cells, iterations=2) if opening_cells.any() else np.zeros_like(passable)
    ends &= ~near_opening
    labels, n = ndimage.label(ends)
    spots: list[Spot] = []
    for k in range(1, n + 1):
        rows, cols = np.nonzero(labels == k)
        r, c = int(rows[0]), int(cols[0])
        x, y = (c + 0.5) * cell_size, (r + 0.5) * cell_size
        spots.append(Spot(x=x, y=y, value=float(aisle_width_grid[r, c]), percentile=0.0,
                          x_m=x * meters_per_unit, y_m=y * meters_per_unit))
    spots.sort(key=lambda s: s.value)
    end_cells = [(int(r), int(c)) for r, c in zip(*np.nonzero(ends))]
    return round(pct, 1), spots[:5], end_cells


def _side_of(result: VGAResult, x: float, y: float) -> str:
    """방향 이름 (floorplan_issues._side와 같은 규칙 — 그쪽이 이 모듈을 불러 순환을 피해 따로 둔다)."""
    w, h = result.cols * result.cell_size, result.rows * result.cell_size
    ns = "북" if y < h / 3 else ("남" if y > 2 * h / 3 else "")
    ew = "서" if x < w / 3 else ("동" if x > 2 * w / 3 else "")
    return f"도면 {ns + ew}쪽" if ns + ew else "도면 가운데"


def _zone_ref(result: VGAResult, issues, kind: str) -> str | None:
    """「도면 동쪽, 4번 카드의 구역」 — 화면 카드 번호(= issues 순서 + 1)로 가리킨다."""
    for i, issue in enumerate(issues or [], 1):
        if getattr(issue, "kind", "") == kind:
            return f"{_side_of(result, issue.x, issue.y)}, {i}번 카드의 구역"
    return None


def _card_no(issues, kind: str) -> int | None:
    for i, issue in enumerate(issues or [], 1):
        if getattr(issue, "kind", "") == kind:
            return i
    return None


def rule_based_floorplan_explanation(result: VGAResult, issues=None) -> str:
    """규칙 요약문. ``issues``(floorplan_issues.detect_issues 결과)를 주면 자리를 카드 번호로 가리킨다.

    예전엔 「목이 가장 좋은 자리는 왼쪽 위에서 오른쪽으로 137m, 아래로 54m 지점」처럼 목 1위
    **한 칸**의 좌표를 말했다. 사용자가 도면에서 그 지점을 찾을 수 없고(눈금이 없다), 한 칸은
    좋은 통로의 한쪽 끝이 뽑히기 쉬워 카드(목 상위 10% 구역)와 어긋날 수 있다
    (floorplan_narrative.build_user_prompt 주석). 그래서 카드 번호·방향으로 말한다(2026-11-07).
    """
    if not result.cells:
        return "분석 가능한 공간을 찾지 못했습니다."

    spread = (result.integration_max / result.integration_mean) if result.integration_mean > 0 else 0.0

    # 화면에 나가는 문장에는 영어·전문 용어를 쓰지 않는다
    # (frontend-next/lib/metricInfo.ts의 표기 원칙). "통합도"·"가시성 그래프"
    # 같은 말은 툴팁·도움말 쪽에서만 꺼낸다.
    size = ""
    if result.cell_size_m:
        size = f" (한 칸 약 {result.cell_size_m:.1f}m)"
    # 「방 안을」은 복합몰·사무층 도면에도 나가 한 방만 본 것처럼 읽혔다 — 「건물 안을」
    # (2026-11-07 UX 자동 개선).
    sentences = [f"건물 안을 격자 {len(result.cells):,}칸{size}으로 나눠 살펴봤습니다."]
    # 도면 일부가 벽에 막혀 빠졌는데 요약은 「건물 안을 … 살펴봤습니다」로 전체를 본 듯 읽혔다 —
    # 빠진 비율과 1번 카드를 결론 맨 앞에 밝힌다. 아래 「가장 좋은 곳·외진 곳」은 남은 부분만의
    # 이야기다 (2026-11-08 UX 자동 개선, wtc-mall 샘플 반영률 65%).
    if result.coverage_pct < 70.0:
        excluded_ref = _card_no(issues, "excluded")
        where = f" — {excluded_ref}번 카드를 먼저 확인하세요" if excluded_ref else ""
        sentences.append(
            f"다만 도면 열린 공간의 {100 - result.coverage_pct:.0f}%가 벽에 막혀 계산에서 빠졌고, "
            f"아래 내용은 이어진 나머지만의 결과입니다{where}."
        )

    best_ref = _zone_ref(result, issues, "best_zone")
    if best_ref:
        sentences.append(f"목이 가장 좋은 곳은 {best_ref}입니다.")
    elif result.best_spots:
        best = result.best_spots[0]
        sentences.append(f"목이 가장 좋은 자리는 {_side_of(result, best.x, best.y)}입니다.")
    # 「가장 많이 지나가는 길목」 한 칸은 문장에 쓰지 않는다 — 통로 모서리 한 칸에 뾰족하게
    # 튀어 외진 골목 구석을 「가장 많이 지나가는 곳」이라 말했다(2026-10-24, floorplan_issues 5번 주석).
    if spread >= 2.5:
        sentences.append(
            "공간이 뚜렷하게 나뉘어 있어, 사람이 자연히 지나게 되는 가운데 자리와 "
            "구석진 곳의 차이가 큽니다."
        )
    dead_ref = _zone_ref(result, issues, "dead_zone")
    if spread < 2.5:
        # 외진 구역 카드(「즉시 검토」일 수도 있다)가 있는데 「어디를 봐도 목이 비슷해 고른 편」이라
        # 하면 바로 아래 카드와 어긋나 읽혔다 — 카드가 있으면 「전체로는 비슷하지만 따로 떨어진 곳이
        # 있다」로 잇는다 (2026-11-07 UX 자동 개선).
        sentences.append(
            "전체로는 목 차이가 크지 않은 편이지만, 따로 떨어진 외진 구역이 있습니다."
            if dead_ref
            else "공간 어디를 봐도 목이 비슷해 고른 편입니다."
        )
    if dead_ref:
        sentences.append(f"가장 외진 곳은 {dead_ref}이며, 사람 발길이 닿기 어려울 수 있습니다.")
    elif result.dead_spots:
        dead = result.dead_spots[0]
        sentences.append(
            f"가장 외진 자리는 {_side_of(result, dead.x, dead.y)}이며, 사람 발길이 닿기 어려울 수 있습니다."
        )
    sentences.append(
        "도면에서 뽑아 확인한 벽을 기준으로 서로 보이는 정도를 계산한 결과이며, "
        "가구 배치나 문 폭 같은 세부는 반영되지 않았습니다."
    )
    return " ".join(sentences)
