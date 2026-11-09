"""A/B 비교 — 한 줄 점수로 합치지 않는다(스펙 결정 3). 같은 자리의 문제가
조금 줄어든 것을 '없어짐 + 새로 생김'으로 두 번 세지 않는지도 본다."""

from dataclasses import replace
from pathlib import Path

import pytest

from app.services.floorplan_compare import compare_plans, summarize_comparison
from app.services.floorplan_extraction import extract_from_dxf
from app.services.floorplan_issues import Issue, detect_issues
from app.services.floorplan_vga import WallSegment, compute_floorplan_vga

SAMPLES = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples"


def _run(openings_extra=()):
    extracted = extract_from_dxf((SAMPLES / "synthetic-mall-labeled.dxf").read_bytes())
    walls = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.lines]
    openings = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.openings]
    openings.extend(openings_extra)
    result = compute_floorplan_vga(
        walls, extracted.canvas_width, extracted.canvas_height,
        openings=openings, meters_per_unit=extracted.meters_per_unit,
    )
    labels = [(l.text, l.x, l.y) for l in extracted.labels]
    return result, detect_issues(result, labels), extracted


@pytest.fixture(scope="module")
def pair():
    before, before_issues, extracted = _run()
    unit = 1.0 / extracted.meters_per_unit
    # 문이 없어 계산에서 빠져 있던 앵커 반쪽에 통로 쪽 문을 낸다.
    door = WallSegment(216 * unit, 50 * unit, 216 * unit, 53 * unit)
    after, after_issues, _ = _run([door])
    return before, before_issues, after, after_issues


def test_opening_a_door_raises_coverage_and_is_reported_as_better(pair):
    before, before_issues, after, after_issues = pair
    deltas, _, _ = compare_plans(before, after, before_issues, after_issues)
    coverage = next(d for d in deltas if d.key == "coverage")
    assert coverage.after > coverage.before
    assert coverage.direction == "better"


def test_comparison_never_collapses_into_one_winner(pair):
    """결정 3 — 좋아진 것과 나빠진 것을 함께 말해야 한다."""
    before, before_issues, after, after_issues = pair
    deltas, appeared, resolved = compare_plans(before, after, before_issues, after_issues)
    text = summarize_comparison(deltas, appeared, resolved)
    assert "한 줄로 말하지 않습니다" in text
    assert len({d.direction for d in deltas}) > 1, "모든 지표가 같은 방향이면 비교가 무의미하다"


def test_identical_plans_report_no_change():
    result, issues, _ = _run()
    deltas, appeared, resolved = compare_plans(result, result, issues, issues)
    assert all(d.direction == "same" for d in deltas)
    assert appeared == [] and resolved == []
    assert "거의 같습니다" in summarize_comparison(deltas, appeared, resolved)


def test_same_zone_shrinking_is_not_counted_twice(pair):
    """같은 자리 같은 종류인데 넓이만 달라 제목이 바뀐 경우 — 한 문제로 본다."""
    before, before_issues, after, _ = pair
    shrunk = [
        replace(i, title=i.title.replace("537㎡", "471㎡"), area_m2=(i.area_m2 or 0) * 0.9)
        if "537㎡" in i.title else i
        for i in before_issues
    ]
    _, appeared, resolved = compare_plans(before, before, before_issues, shrunk)
    assert appeared == [] and resolved == []


def test_different_category_at_same_place_is_a_new_problem(pair):
    before, before_issues, _, _ = pair
    origin = before_issues[0]
    other = Issue(
        severity="medium", title="가운데 한 곳으로 동선이 몰립니다", detail="",
        x=origin.x, y=origin.y,
    )
    _, appeared, resolved = compare_plans(before, before, before_issues, [other])
    assert other in appeared
    assert origin in resolved


def test_building_wide_issues_match_even_when_the_marker_moves(pair):
    """「가장 목이 좋은 곳」은 건물 전체를 두고 하는 안내라 마커가 옮겨다닌다.
    거리로 짝지으면 같은 문장이 없어짐·새로생김에 동시에 뜬다. (2026-10-24 전에는
    「모이는 자리와 지나가는 자리가 다릅니다」가 이 자리였다 — 모서리 한 칸에 튀어 내렸다.)"""
    before, before_issues, _, _ = pair
    split = next(i for i in before_issues if "가장 목이 좋은 곳" in i.title)
    moved = replace(split, x=split.x + before.cell_size * 40, y=split.y + before.cell_size * 20)
    _, appeared, resolved = compare_plans(before, before, [split], [moved])
    assert appeared == [] and resolved == []


def test_worst_zone_is_the_single_largest_remote_zone(pair):
    """「그중 가장 외진 구석」 — 외진 구역 중 가장 넓은 하나이고, 합계(dead_area)를 넘지 않는다."""
    before, before_issues, after, after_issues = pair
    deltas, _, _ = compare_plans(before, after, before_issues, after_issues)
    by_key = {d.key: d for d in deltas}
    remote = [i.area_m2 or i.cell_count for i in before_issues if "외진" in i.title]
    assert by_key["worst_zone"].before == (max(remote) if remote else 0.0)
    assert by_key["worst_zone"].before <= by_key["dead_area"].before
