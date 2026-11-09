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


def test_compare_reports_robot_share_when_a_rack_row_is_removed():
    """물류센터 샘플에서 좁은 통로를 만드는 랙 한 줄을 지우면 로봇 비율이 오르고, 비교에 그 줄이 붙는다."""
    from pathlib import Path

    from app.services.floorplan_compare import compare_plans, summarize_comparison
    from app.services.floorplan_extraction import extract_from_dxf
    from app.services.floorplan_issues import detect_issues

    path = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "logistics-center.dxf"
    ex = extract_from_dxf(path.read_bytes())
    mpu = ex.meters_per_unit
    assert mpu
    walls = [vga.WallSegment(l.x1, l.y1, l.x2, l.y2) for l in ex.lines]
    openings = [vga.WallSegment(l.x1, l.y1, l.x2, l.y2) for l in ex.openings]
    # 두 번째 랙 줄(도면 y 9.6~10.8m)을 지운다 — 그 위아래 통로(3.4m + 1.4m)가 하나로 합쳐진다.
    # 캔버스는 y가 아래로 늘어 도면 y가 뒤집혀 들어온다(37 − y) — 양쪽 띠 다 본다.
    height_m = ex.canvas_height * mpu
    bands = ((9.5, 10.9), (height_m - 10.9, height_m - 9.5))

    def in_row(w: vga.WallSegment) -> bool:
        ys = (w.y1 * mpu, w.y2 * mpu)
        return any(all(lo <= y <= hi for y in ys) for lo, hi in bands) and (w.x1 * mpu) >= 1.5
    after_walls = [w for w in walls if not in_row(w)]
    assert len(after_walls) < len(walls)
    before = vga.compute_floorplan_vga(walls, ex.canvas_width, ex.canvas_height, vga.MAX_GRID_CELLS, openings, mpu)
    after = vga.compute_floorplan_vga(after_walls, ex.canvas_width, ex.canvas_height, vga.MAX_GRID_CELLS, openings, mpu)
    labels = [(l.text, l.x, l.y) for l in ex.labels]
    deltas, _, _ = compare_plans(before, after, detect_issues(before, labels), detect_issues(after, labels))
    robot = next(d for d in deltas if d.key == "robot_passable")
    assert robot.unit == "%" and robot.after > robot.before and robot.direction == "better"
    assert "로봇이 지나갈 수 있는 바닥이" in summarize_comparison(deltas, [], [])


def test_compare_skips_robot_line_when_every_aisle_is_wide():
    """통로가 다 넓은 도면(둘 다 100%)에는 로봇 줄을 붙이지 않는다."""
    from app.services.floorplan_compare import compare_plans

    walls = [vga.WallSegment(0, 0, 100, 0), vga.WallSegment(100, 0, 100, 60), vga.WallSegment(100, 60, 0, 60), vga.WallSegment(0, 60, 0, 0)]
    r = vga.compute_floorplan_vga(walls, 100, 60, 400, [], 0.5)
    assert r.robot_passable_pct == 100.0
    deltas, _, _ = compare_plans(r, r, [], [])
    assert not any(d.key == "robot_passable" for d in deltas)
