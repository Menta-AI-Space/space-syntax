"""방 복원 — 문을 뚫기 전 격자의 연결 덩어리가 곧 방이다. 합성 몰의 실제 구조
(중앙통로 1 + 앵커 4조각 + 매장 32)와 맞는지 본다."""

from pathlib import Path

import pytest

from app.services.floorplan_extraction import extract_from_dxf
from app.services.floorplan_rooms import detect_rooms
from app.services.floorplan_vga import WallSegment, compute_floorplan_vga

SAMPLES = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples"


@pytest.fixture(scope="module")
def labeled():
    extracted = extract_from_dxf((SAMPLES / "synthetic-mall-labeled.dxf").read_bytes())
    walls = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.lines]
    openings = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.openings]
    result = compute_floorplan_vga(
        walls, extracted.canvas_width, extracted.canvas_height,
        openings=openings, meters_per_unit=extracted.meters_per_unit,
    )
    rooms = detect_rooms(
        blocked_no_openings=result.blocked_no_openings,
        exterior=result.exterior_mask,
        analyzed_mask=result.analyzed_mask,
        cell_size=result.cell_size,
        cell_size_m=result.cell_size_m,
        integration=result.integration_grid,
        choice=result.choice_grid,
        connectivity=result.connectivity_grid,
        labels_xy=[(l.text, l.x, l.y) for l in extracted.labels],
    )
    return result, rooms


def test_corridor_is_the_biggest_and_best_connected_room(labeled):
    _, rooms = labeled
    assert rooms, "방을 하나도 못 찾으면 복원이 죽은 것"
    top = rooms[0]
    assert top.name == "중앙통로"
    assert top.area_m2 == max(r.area_m2 for r in rooms)
    assert top.integration_percentile == 100.0


def test_room_names_come_from_the_text_inside_them(labeled):
    _, rooms = labeled
    named = {r.name for r in rooms if r.name}
    assert "앵커매장 A" in named and "앵커매장 B" in named
    assert sum(1 for r in rooms if r.name and r.name.startswith("매장")) >= 30


def test_areas_are_plausible_for_a_240x120_mall(labeled):
    result, rooms = labeled
    total = sum(r.area_m2 for r in rooms)
    building = 240.0 * 120.0
    # 벽이 차지하는 칸이 빠지므로 전체보다 작아야 하지만 절반은 넘어야 한다.
    assert building * 0.5 < total < building


def test_sealed_room_is_reported_but_not_scored(labeled):
    """문이 없는 방은 지표를 낼 수 없다 — 0점이 아니라 '분석 안 됨'으로 나가야
    화면이 '목이 최악인 매장'으로 잘못 그리지 않는다."""
    _, rooms = labeled
    sealed = [r for r in rooms if r.analyzed_pct == 0]
    assert sealed, "합성 몰에는 문 없는 앵커 반쪽이 하나 있다"
    for room in sealed:
        assert room.integration_mean == 0.0
        assert room.integration_percentile == 0.0
        assert room.area_m2 and room.area_m2 > 0


def test_unlabeled_plan_still_yields_rooms():
    extracted = extract_from_dxf((SAMPLES / "synthetic-large-mall.dxf").read_bytes())
    walls = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.lines]
    openings = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.openings]
    result = compute_floorplan_vga(
        walls, extracted.canvas_width, extracted.canvas_height,
        openings=openings, meters_per_unit=extracted.meters_per_unit,
    )
    rooms = detect_rooms(
        blocked_no_openings=result.blocked_no_openings,
        exterior=result.exterior_mask,
        analyzed_mask=result.analyzed_mask,
        cell_size=result.cell_size,
        cell_size_m=result.cell_size_m,
        integration=result.integration_grid,
        choice=result.choice_grid,
        connectivity=result.connectivity_grid,
    )
    assert len(rooms) > 20
    assert all(r.name is None for r in rooms)


def test_each_label_names_at_most_one_room():
    """실명 하나가 두 방에 붙으면 공간별 비교 표에 같은 매장이 두 줄 나온다
    (쇼케이스 샘플의 「매장 68」이 405㎡·31㎡ 두 줄이었다)."""
    extracted = extract_from_dxf((SAMPLES / "showcase-mall.dxf").read_bytes())
    walls = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.lines]
    openings = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.openings]
    result = compute_floorplan_vga(
        walls, extracted.canvas_width, extracted.canvas_height,
        openings=openings, meters_per_unit=extracted.meters_per_unit,
    )
    rooms = detect_rooms(
        blocked_no_openings=result.blocked_no_openings,
        exterior=result.exterior_mask,
        analyzed_mask=result.analyzed_mask,
        cell_size=result.cell_size,
        cell_size_m=result.cell_size_m,
        integration=result.integration_grid,
        choice=result.choice_grid,
        connectivity=result.connectivity_grid,
        labels_xy=[(l.text, l.x, l.y) for l in extracted.labels],
    )
    names = [r.name for r in rooms if r.name]
    assert len(names) == len(set(names)), [n for n in names if names.count(n) > 1]
    assert len(names) >= 60
