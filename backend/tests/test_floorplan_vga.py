import pytest

from app.services.floorplan_vga import (
    FloorplanVGAError,
    WallSegment,
    compute_floorplan_vga,
    rule_based_floorplan_explanation,
)


def _rect_outline(x0, y0, x1, y1):
    return [
        WallSegment(x0, y0, x1, y0),
        WallSegment(x1, y0, x1, y1),
        WallSegment(x1, y1, x0, y1),
        WallSegment(x0, y1, x0, y0),
    ]


def single_room_walls():
    """One closed rectangular room, no interior obstacles."""
    return _rect_outline(0, 0, 60, 30)


def two_rooms_with_doorway_walls():
    """Two 60x60 rooms side by side, joined by a 20-wide doorway gap."""
    return [
        WallSegment(0, 0, 120, 0),
        WallSegment(0, 60, 120, 60),
        WallSegment(0, 0, 0, 60),
        WallSegment(120, 0, 120, 60),
        WallSegment(60, 0, 60, 20),
        WallSegment(60, 40, 60, 60),
    ]


def test_single_room_has_free_cells_and_positive_integration():
    result = compute_floorplan_vga(single_room_walls(), 60, 30, max_cells=400)
    assert len(result.cells) > 0
    assert result.integration_mean > 0
    assert all(cell.connectivity > 0 for cell in result.cells)


def test_no_walls_raises():
    with pytest.raises(FloorplanVGAError):
        compute_floorplan_vga([], 60, 30)


def test_invalid_canvas_size_raises():
    with pytest.raises(FloorplanVGAError):
        compute_floorplan_vga(single_room_walls(), 0, 30)


def test_doorway_cell_more_integrated_than_far_corner():
    """Cells near the connecting doorway should be more integrated than a
    corner deep inside one room — the classic VGA "bottleneck reads as more
    central" signature."""
    result = compute_floorplan_vga(two_rooms_with_doorway_walls(), 120, 60, max_cells=900)

    def nearest(target_x, target_y):
        return min(result.cells, key=lambda c: (c.x - target_x) ** 2 + (c.y - target_y) ** 2)

    doorway_cell = nearest(60, 30)
    far_corner_cell = nearest(5, 5)

    assert doorway_cell.integration > far_corner_cell.integration


def sealed_two_rooms_walls():
    """Two rooms with NO gap between them — the shape real CAD wall layers have,
    since doors live on their own layer and never break the wall geometry."""
    return [
        WallSegment(0, 0, 120, 0),
        WallSegment(0, 60, 120, 60),
        WallSegment(0, 0, 0, 60),
        WallSegment(120, 0, 120, 60),
        WallSegment(60, 0, 60, 60),  # solid divider, no doorway
    ]


def test_sealed_rooms_warn_and_report_low_coverage():
    """Regression: a sealed plan used to be analyzed silently as one room, so the
    output looked like a whole-building result. Measured on a real DXF/JPEG this
    dropped 60-70% of the floor with no indication."""
    result = compute_floorplan_vga(sealed_two_rooms_walls(), 120, 60, max_cells=900)

    assert result.coverage_pct < 70
    assert any("문 자리 표시" in w for w in result.warnings)


def test_openings_reconnect_sealed_rooms():
    """Carving the door entity out of a continuous wall should reunite the plan —
    this is what takes the real apartment DXF from 36% to ~100% coverage."""
    walls = sealed_two_rooms_walls()
    doorway = [WallSegment(60, 25, 60, 40)]

    sealed = compute_floorplan_vga(walls, 120, 60, max_cells=900)
    opened = compute_floorplan_vga(walls, 120, 60, max_cells=900, openings=doorway)

    assert opened.coverage_pct > sealed.coverage_pct
    assert opened.coverage_pct > 90
    assert not any("문 자리 표시" in w for w in opened.warnings)


def test_sealed_plan_reports_which_cells_were_excluded():
    """The warning is only actionable if the user can see WHERE it was cut off."""
    result = compute_floorplan_vga(sealed_two_rooms_walls(), 120, 60, max_cells=900)

    assert result.excluded_cells
    analyzed = {(c.row, c.col) for c in result.cells}
    excluded = {(c.row, c.col) for c in result.excluded_cells}
    assert not (analyzed & excluded)  # a cell is analyzed or dropped, never both


def test_connected_plan_excludes_nothing():
    result = compute_floorplan_vga(single_room_walls(), 60, 30, max_cells=400)
    assert result.excluded_cells == []


def test_full_coverage_plan_does_not_warn():
    result = compute_floorplan_vga(single_room_walls(), 60, 30, max_cells=400)
    assert result.coverage_pct > 90
    assert not any("문 자리 표시" in w for w in result.warnings)


def test_all_pairs_depth_matches_reference_shortest_path():
    """The matrix-expansion depth replaced scipy's Dijkstra for speed (7.5s ->
    2.4s on a real DXF, which is the difference between breaking the project's
    5s rule and not). It has to produce the same numbers, not just faster ones."""
    import numpy as np
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import shortest_path

    from app.services.floorplan_vga import _all_pairs_depth

    rng = np.random.default_rng(20260822)
    # A connected-ish sparse graph plus a deliberately disconnected pair, so the
    # unreachable-node handling is exercised too.
    size = 40
    dense = rng.random((size, size)) < 0.12
    dense |= dense.T
    np.fill_diagonal(dense, False)
    dense[size - 1, :] = False
    dense[:, size - 1] = False
    adjacency = csr_matrix(dense.astype(np.uint8))

    total_depth, node_count, local_depth, local_count = _all_pairs_depth(adjacency)

    reference = shortest_path(adjacency, method="D", directed=False, unweighted=True)
    finite = np.isfinite(reference) & (reference > 0)
    expected_depth = np.where(finite, reference, 0).sum(axis=1)
    expected_count = finite.sum(axis=1)

    assert np.allclose(total_depth, expected_depth)
    assert np.allclose(node_count, expected_count)

    within = finite & (reference <= 3)
    assert np.allclose(local_depth, np.where(within, reference, 0).sum(axis=1))
    assert np.allclose(local_count, within.sum(axis=1))


def test_choice_matches_networkx_betweenness():
    """Choice is Brandes betweenness done with sparse mat-vecs. Exact mode has
    to reproduce networkx's numbers; sampled mode only has to rank the same
    top spots (that is all the explanation uses it for)."""
    import networkx as nx
    import numpy as np
    from scipy.sparse import csr_matrix

    from app.services.floorplan_vga import _choice

    rng = np.random.default_rng(20260917)
    size = 60
    dense = rng.random((size, size)) < 0.15
    dense |= dense.T
    np.fill_diagonal(dense, False)
    adjacency = csr_matrix(dense.astype(np.uint8))

    exact = _choice(adjacency, None)
    graph = nx.from_scipy_sparse_array(adjacency)
    expected = nx.betweenness_centrality(graph, normalized=False)
    assert np.allclose(exact, [expected[i] for i in range(size)])

    sampled = _choice(adjacency, 30)
    top_exact = set(np.argsort(-exact)[:5])
    top_sampled = set(np.argsort(-sampled)[:8])
    assert len(top_exact & top_sampled) >= 3


def test_wide_open_plan_stays_within_time_budget():
    """A plan with no interior walls is the worst case — every cell sees every
    other, so the visibility graph is at its densest. This shipped at 8.3s and
    broke the 5s rule; the bound here is loose enough for a slow machine but
    still catches a return to the old all-pairs search."""
    import time

    width, height = 1600.0, 1200.0
    walls = _rect_outline(0, 0, width, height)

    start = time.time()
    result = compute_floorplan_vga(walls, width, height)
    elapsed = time.time() - start

    assert result.cells
    assert elapsed < 8.0, f"open-plan VGA took {elapsed:.1f}s"


def test_explanation_mentions_cell_count():
    result = compute_floorplan_vga(single_room_walls(), 60, 30, max_cells=400)
    text = rule_based_floorplan_explanation(result)
    assert str(len(result.cells)) in text


def test_explanation_uses_plain_korean_only():
    """frontend-next/lib/metricInfo.ts: 화면에 처음 보이는 이름은 쉬운 한국어만
    쓰고 영어는 금지한다. 이 설명문은 그대로 결과 화면에 나가므로 같은 규칙을
    받는다 — 처음 구현이 "평균 Integration은 …", "가시성 그래프(VGA)"를 그대로
    노출해 규칙을 어겼다."""
    result = compute_floorplan_vga(two_rooms_with_doorway_walls(), 120, 60, max_cells=900)
    text = rule_based_floorplan_explanation(result)

    banned = ["Integration", "Connectivity", "VGA", "Choice", "통합도", "연결도"]
    found = [word for word in banned if word in text]
    assert not found, f"설명문에 전문 용어가 남아 있다: {found}"


def test_explanation_empty_result():
    from app.services.floorplan_vga import VGAResult

    empty = VGAResult(
        cols=0, rows=0, cell_size=1.0, cells=[], integration_mean=0.0,
        integration_max=0.0, connectivity_mean=0.0,
    )
    assert rule_based_floorplan_explanation(empty) == "분석 가능한 공간을 찾지 못했습니다."


def building_with_margin_walls():
    """A building drawn with street/margin around it — the shape of a real site
    plan, and the case that used to break the analysis.

    The outdoors is the single biggest open region on the sheet, and the two
    entrance gaps fuse it to the interior corridor, so a plain
    largest-component pick analyzed the street instead of the building.
    """
    return [
        # Outer building envelope, inset from the canvas, with entrance gaps
        # in the top wall.
        WallSegment(40, 40, 130, 40),
        WallSegment(150, 40, 260, 40),
        WallSegment(40, 200, 260, 200),
        WallSegment(40, 40, 40, 200),
        WallSegment(260, 40, 260, 200),
        # An interior divider with a doorway, so the inside is a corridor plus
        # a room rather than one empty box.
        WallSegment(150, 40, 150, 110),
        WallSegment(150, 140, 150, 200),
    ]


def test_outdoors_is_not_analyzed_as_if_it_were_indoor_space():
    """The analyzed cells must sit inside the building envelope.

    Before this was fixed the analysis ran on the margin outside the walls —
    on a real mall plan that meant reporting the street as the best-connected
    space in the building.
    """
    result = compute_floorplan_vga(building_with_margin_walls(), 300, 240, max_cells=3000)

    assert result.cells, "nothing was analyzed"
    # Every analyzed cell should land within the envelope, not out on the margin.
    outside = [c for c in result.cells if not (40 <= c.x <= 260 and 40 <= c.y <= 200)]
    assert not outside, f"{len(outside)}/{len(result.cells)} analyzed cells fell outside the building"


def test_outdoor_margin_is_not_reported_as_excluded_rooms():
    """Coverage and the hatched "excluded" cells describe indoor floor only.

    Counting the margin would report a low coverage and hatch the street as a
    room we failed to reach, sending the user hunting for a door that is not
    there.
    """
    result = compute_floorplan_vga(building_with_margin_walls(), 300, 240, max_cells=3000)

    for cell in result.excluded_cells:
        x = (cell.col + 0.5) * result.cell_size
        y = (cell.row + 0.5) * result.cell_size
        assert 40 <= x <= 260 and 40 <= y <= 200, "an outdoor cell was hatched as an unreached room"


def test_walls_flush_with_the_canvas_edge_still_seal_the_plan():
    """A wall drawn exactly at x == canvas_width used to round to one cell past
    the grid and get dropped, leaving the plan open along that edge — which
    then read as a doorway to the outdoors and leaked the analysis out."""
    result = compute_floorplan_vga(single_room_walls(), 60, 30, max_cells=400)

    assert result.cells
    assert result.coverage_pct == pytest.approx(100.0, abs=1.0)
    assert not result.excluded_cells


def test_gap_snapping_never_exceeds_a_doorway_when_scale_is_known():
    """칸 크기에만 비례하면 큰 도면에서 허용 거리가 문보다 넓어진다.

    200×150m 도면은 한 칸이 3.7m라 허용 거리가 5.5m가 됐고, 그건 문 폭(1~3m)을
    넘어 **도면에 없는 벽을 만드는** 셈이었다. 축척을 알면 실제 치수로 묶는다.
    """
    from app.services.floorplan_vga import (
        GAP_SNAP_CELLS,
        GAP_SNAP_MAX_M,
        WallSegment,
        compute_floorplan_vga,
    )

    # 200m 폭 도면 — 격자가 성겨 칸이 미터 단위가 된다.
    span = 2000.0  # 캔버스 단위
    meters_per_unit = 0.1  # 1단위 = 0.1m → 도면 폭 200m
    walls = [
        WallSegment(0, 0, span, 0),
        WallSegment(span, 0, span, span * 0.75),
        WallSegment(span, span * 0.75, 0, span * 0.75),
        WallSegment(0, span * 0.75, 0, 0),
    ]
    # 1m(=10단위) 떨어진 두 끝점 — 문 폭만 한 틈이므로 이어 붙이면 안 된다.
    walls.append(WallSegment(span / 2, 0, span / 2, span * 0.3))
    walls.append(WallSegment(span / 2, span * 0.3 + 10, span / 2, span * 0.75))

    result = compute_floorplan_vga(walls, span, span * 0.75, meters_per_unit=meters_per_unit)
    bridged = [w for w in result.warnings + result.notes if "이어 붙였습니다" in w]
    assert not bridged, "문 폭만 한 틈을 메워 버렸다"
    # 상한이 실제로 문보다 좁은지도 함께 고정한다.
    assert GAP_SNAP_MAX_M < 1.0
    assert GAP_SNAP_CELLS > 0


def test_hairline_gaps_are_still_bridged():
    """상한을 두더라도 cm 단위 어긋남은 메워야 한다 — 그게 원래 목적이다."""
    from app.services.floorplan_vga import WallSegment, compute_floorplan_vga

    span = 400.0
    meters_per_unit = 0.1  # 도면 폭 40m, 아래 틈은 0.2m
    walls = [
        WallSegment(0, 0, span, 0),
        WallSegment(span, 0, span, span),
        WallSegment(span, span, 0, span),
        WallSegment(0, span, 0, 2),  # 시작점에서 0.2m 못 미침
    ]
    result = compute_floorplan_vga(walls, span, span, meters_per_unit=meters_per_unit)
    assert any("이어 붙였습니다" in w for w in result.notes)
    # 엔진이 알아서 바로잡은 것은 경고가 아니다 — 주황 경고로 뜨면 잘못된 것처럼 읽힌다.
    assert not any("이어 붙였습니다" in w for w in result.warnings)


def _rotated_room_dxf(degrees: float) -> bytes:
    """대지 방향을 따라 돌아간 도면 — 실제 도면에서는 이쪽이 오히려 기본이다."""
    import math
    import tempfile

    import ezdxf

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    for name in ("WALL", "DOOR"):
        doc.layers.add(name)
    msp = doc.modelspace()
    rot = math.radians(degrees)

    def turn(point):
        x, y = point
        return (x * math.cos(rot) - y * math.sin(rot), x * math.sin(rot) + y * math.cos(rot))

    corners = [(0, 0), (30, 0), (30, 20), (0, 20)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        msp.add_line(turn(a), turn(b), dxfattribs={"layer": "WALL"})
    msp.add_line(turn((13, 0)), turn((17, 0)), dxfattribs={"layer": "DOOR"})

    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
    tmp.close()
    doc.saveas(tmp.name)
    from pathlib import Path

    data = Path(tmp.name).read_bytes()
    Path(tmp.name).unlink(missing_ok=True)
    return data


@pytest.mark.parametrize("degrees", [0, 15, 30, 45, 60])
def test_rotated_plans_do_not_leak_through_the_doorway(degrees):
    """돌아간 도면은 출입구가 대각선 틈이 되고, 정사각 커널로는 덜 막힌다.
    13까지만 쓰던 시절 30도 도면의 실내가 149칸(반영률 27%)으로 무너졌다 —
    에러 없이 건물 대부분이 '실외'로 판정됐다."""
    from app.services.floorplan_extraction import extract_from_dxf
    from app.services.floorplan_vga import WallSegment, compute_floorplan_vga

    extracted = extract_from_dxf(_rotated_room_dxf(degrees))
    walls = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.lines]
    openings = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.openings]
    result = compute_floorplan_vga(
        walls, extracted.canvas_width, extracted.canvas_height,
        openings=openings, meters_per_unit=extracted.meters_per_unit,
    )
    assert result.coverage_pct > 95, f"{degrees}도에서 실내가 새어 나갔다"
    # 돌아가면 외곽 상자 안에서 건물이 차지하는 비율이 줄지만, 방 자체는 남아야 한다.
    assert len(result.cells) > 800


def _two_room_dxf(*, separate: bool) -> bytes:
    """``separate``면 평면 두 개를 나란히, 아니면 한 건물 안에 막힌 방 하나."""
    import tempfile
    from pathlib import Path

    import ezdxf

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    for name in ("WALL", "DOOR"):
        doc.layers.add(name)
    msp = doc.modelspace()

    def room(ox: float, door: bool = True) -> None:
        corners = [(ox, 0), (ox + 30, 0), (ox + 30, 20), (ox, 20)]
        for a, b in zip(corners, corners[1:] + corners[:1]):
            msp.add_line(a, b, dxfattribs={"layer": "WALL"})
        if door:
            msp.add_line((ox + 13, 0), (ox + 17, 0), dxfattribs={"layer": "DOOR"})

    if separate:
        room(0)
        room(50)
        room(100)
    else:
        room(0, door=False)
        msp.add_line((10, 0), (10, 20), dxfattribs={"layer": "WALL"})  # 문 없는 칸막이

    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
    tmp.close()
    doc.saveas(tmp.name)
    data = Path(tmp.name).read_bytes()
    Path(tmp.name).unlink(missing_ok=True)
    return data


def _extract(data: bytes):
    from app.services.floorplan_extraction import extract_from_dxf

    return extract_from_dxf(data)


def _run(data: bytes):
    from app.services.floorplan_extraction import extract_from_dxf
    from app.services.floorplan_vga import WallSegment, compute_floorplan_vga

    extracted = extract_from_dxf(data)
    return compute_floorplan_vga(
        [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.lines],
        extracted.canvas_width,
        extracted.canvas_height,
        openings=[WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.openings],
        meters_per_unit=extracted.meters_per_unit,
    )


def test_several_plans_on_one_sheet_are_split_for_us():
    """조치가 정반대다 — 한 장에 여러 평면이면 **그중 하나를 본 것**이고, 문이
    막힌 거면 문을 찍어야 한다. 한쪽 안내만 내면 사용자가 없는 문을 찾아 헤맨다.

    ⚠️ 예전엔 여기서 "한 평면만 남기고 잘라서 올려 주세요"라고 했다. 2026-10-01에
    내렸다 — **자르는 건 우리 일이다**(`floorplan_extraction`의 도면 정규화).
    손님은 건축사사무소에서 받은 파일을 그대로 넣는다."""
    data = _two_room_dxf(separate=True)
    extracted = _extract(data)
    # 평면 3개를 나란히 둔 도면 → 우리가 하나만 남긴다.
    assert any("도면이 3개" in note for note in extracted.auto_notes)

    result = _run(data)
    # 한 평면만 남았으니 반영률이 온전하고, 손질하라는 안내가 뜨지 않는다.
    assert result.coverage_pct > 90.0
    assert not any("잘라서 올려" in w for w in result.warnings)
    assert not any("문이 벽으로 막혀" in w for w in result.warnings)


def test_openings_fill_their_wall_slot_before_being_carved():
    """문·창 선도 벽과 함께 그린 뒤에 뚫는다.

    그 자리는 실제로 벽체가 있는 자리다. 안 그리면 외벽에 구멍이 남아 바깥이
    새어 들어온다 — 창틀(`A-WX-SIL`)을 개구부로 알아보게 한 순간 외곽 비율이
    80%→52%로 떨어졌다(2026-10-01). **알아본 것이 손해가 되면 안 된다.**
    """
    from app.services.floorplan_vga import WallSegment, compute_floorplan_vga

    # 한 변에 구멍이 뚫린 방. 그 구멍은 문 선분이 메운다.
    walls = [
        WallSegment(0, 0, 30, 0),
        WallSegment(30, 0, 30, 20),
        WallSegment(30, 20, 0, 20),
        WallSegment(0, 20, 0, 12),
        WallSegment(0, 8, 0, 0),
    ]
    openings = [WallSegment(0, 8, 0, 12)]
    result = compute_floorplan_vga(walls, 30, 20, openings=openings, meters_per_unit=1.0)

    grid = result.blocked_no_openings
    assert grid is not None
    # 문 자리가 「문 뚫기 전 격자」에서는 막혀 있어야 한다.
    rows, cols = grid.shape
    left_edge = grid[:, 0]
    assert left_edge.sum() >= rows - 2, "왼쪽 벽에 구멍이 남았다"


def test_sealed_room_still_asks_for_a_doorway():
    result = _run(_two_room_dxf(separate=False))
    assert any("문이 벽으로 막혀" in w for w in result.warnings)
    assert not any("잘라서 올려" in w for w in result.warnings)
