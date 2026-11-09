"""로봇 통로(2026-11-09) — 통로 폭이 국소 두께로 잡히는지, 로봇 기준으로 비율·막다른 끝이 나오는지."""

import numpy as np

from app.services import floorplan_vga as vga


def _corridor_mask() -> np.ndarray:
    """20×16 격자: 위쪽 넓은 홀(y 0~7, 8칸 높이)과 아래로 뻗은 세로 3칸 폭 통로(x 2~4)·1칸 폭 통로(x 8)."""
    m = np.zeros((16, 20), dtype=bool)
    m[0:8, :] = True          # 홀 — 8칸 높이라 「통로」가 아니다
    m[8:16, 2:5] = True       # 3칸 통로 — 아래 끝이 막힘(막다른 끝)
    m[8:16, 8:9] = True       # 1칸 통로
    return m


def test_aisle_width_is_corridor_width_not_distance_to_wall():
    width = vga._aisle_width_grid(_corridor_mask(), 1.0)
    # 3칸 통로는 가장자리 칸도 3칸 폭으로 읽혀야 한다(벽까지 거리로 재면 1이 된다)
    assert width[12, 2] == width[12, 3] == width[12, 4] == 3.0
    assert width[12, 8] == 1.0
    assert width[12, 6] == 0.0  # 막힌 칸은 0


def test_robot_summary_counts_passable_share_and_dead_end():
    mask = _corridor_mask()
    width = vga._aisle_width_grid(mask, 1.0)
    pct, spots, cells = vga._robot_summary(mask, width, np.zeros_like(mask), cell_size=10.0, meters_per_unit=0.1)
    free = int(mask.sum())
    passable = int((width >= vga.ROBOT_AISLE_MIN_M).sum())
    assert pct == round(100.0 * passable / free, 1)
    assert pct < 100.0  # 1칸 통로는 로봇이 못 간다
    # 막다른 끝 찾기는 꺼져 있다(DEAD_ENDS_ENABLED) — 켜면 3칸 통로 아래 끝(15행)이 잡혀야 한다
    if vga.DEAD_ENDS_ENABLED:
        assert spots and cells and 15 in {r for r, _ in cells}
        assert all(r >= 8 for r, _ in cells), sorted(cells)
    else:
        assert spots == [] and cells == []


def test_robot_metrics_absent_without_scale():
    walls = [vga.WallSegment(0, 0, 100, 0), vga.WallSegment(100, 0, 100, 60), vga.WallSegment(100, 60, 0, 60), vga.WallSegment(0, 60, 0, 0)]
    result = vga.compute_floorplan_vga(walls, 100, 60, 400, [], None)
    assert result.robot_passable_pct is None and result.robot_dead_ends == []
    assert all(c.aisle_width > 0 for c in result.cells)
