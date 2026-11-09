"""문제 탐지는 지표를 문장으로 옮기는 층이다 — 지표가 맞아도 여기서 엉뚱한 곳을
가리키면 사용자는 틀린 곳을 고친다. 합성 도면으로 '어디'가 맞는지 본다."""

from pathlib import Path

import pytest

from app.services.floorplan_extraction import extract_from_dxf
from app.services.floorplan_issues import detect_issues
from app.services.floorplan_vga import WallSegment, compute_floorplan_vga

SAMPLE = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "synthetic-large-mall.dxf"


@pytest.fixture(scope="module")
def mall_result():
    extracted = extract_from_dxf(SAMPLE.read_bytes())
    walls = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.lines]
    openings = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.openings]
    return compute_floorplan_vga(
        walls, extracted.canvas_width, extracted.canvas_height,
        openings=openings, meters_per_unit=extracted.meters_per_unit,
    )


def test_issues_are_sorted_by_severity(mall_result):
    issues = detect_issues(mall_result)
    assert issues, "합성 몰에서 문제를 하나도 못 찾으면 탐지가 죽은 것"
    order = {"high": 0, "medium": 1, "low": 2}
    ranks = [order[i.severity] for i in issues]
    assert ranks == sorted(ranks)


def test_dead_zones_sit_in_corners_not_the_corridor(mall_result):
    """중앙 통로(y≈60m)는 목이 가장 좋은 곳이다. 외진 구역 마커가 거기 찍히면
    탐지가 뒤집힌 것."""
    dead = [i for i in detect_issues(mall_result) if "외진" in i.title]
    assert dead
    for issue in dead:
        assert issue.y_m is not None
        assert abs(issue.y_m - 60) > 20, f"외진 구역이 통로 위에 찍혔다: {issue.title}"
        assert issue.area_m2 and issue.area_m2 > 0


def test_every_issue_points_somewhere_on_the_plan(mall_result):
    result = mall_result
    width = result.cols * result.cell_size
    height = result.rows * result.cell_size
    for issue in detect_issues(result):
        assert 0 <= issue.x <= width and 0 <= issue.y <= height
        assert issue.title and issue.detail


def test_low_coverage_becomes_the_first_issue(mall_result):
    """반영률이 낮으면 다른 어떤 문제보다 앞에 온다 — 나머지 결과가 도면 일부만
    본 것이기 때문."""
    from dataclasses import replace

    from app.services.floorplan_vga import ExcludedCell

    crippled = replace(
        mall_result,
        coverage_pct=40.0,
        excluded_cells=[ExcludedCell(row=1, col=1), ExcludedCell(row=1, col=2)],
    )
    issues = detect_issues(crippled)
    assert issues[0].severity == "high"
    assert "빠졌습니다" in issues[0].title


def test_no_single_cell_through_claim_and_best_zone_has_cells():
    """2026-10-24: 「가장 많이 지나가는 자리」 한 칸은 통로 모서리에 튀어 외진 골목 구석을
    가리켰다. 그 주장을 내리고 목 좋은 구역을 칸과 함께 낸다."""
    from pathlib import Path

    from app.routers.floorplan import _segments
    from app.services.floorplan_extraction import extract_from_dxf
    from app.services.floorplan_vga import MAX_GRID_CELLS, compute_floorplan_vga

    sample = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "showcase-mall.dxf"
    ex = extract_from_dxf(sample.read_bytes())
    result = compute_floorplan_vga(
        _segments(ex.lines), ex.canvas_width, ex.canvas_height, MAX_GRID_CELLS,
        _segments(ex.openings), ex.meters_per_unit,
    )
    issues = detect_issues(result, [(l.text, l.x, l.y) for l in ex.labels])
    assert not any("지나가는 자리가 다릅니다" in i.title for i in issues)
    best = [i for i in issues if "가장 목이 좋은 곳" in i.title]
    assert len(best) == 1 and best[0].cells
    # 구역 칸들의 목이 실제로 상위권이어야 한다.
    by_cell = {(c.row, c.col): c.integration for c in result.cells}
    values = sorted(by_cell.values())
    cut = values[int(len(values) * 0.85)]
    assert all(by_cell[(r, c)] >= cut for r, c in best[0].cells)
    # 큰 문제(외진 구역)에도 칸이 실린다 — 화면이 그 부분을 잘라 그린다.
    assert all(i.cells for i in issues if "외진" in i.title)


def test_room_names_are_spoken_not_quoted():
    """「'매장 68' 부근」이 뭐냐는 물음(2026-10-27) — 번호 매장은 「68번 매장」, 따옴표 없이."""
    from app.services.floorplan_issues import _room

    assert _room("매장 68") == "68번 매장"
    assert _room("매장 07") == "7번 매장"
    assert _room("중앙 광장") == "중앙 광장"


def test_zone_titles_say_what_the_area_is(mall_result):
    """구역 넓이와 그 구역에서 보이는 넓이를 섞지 않는다 — 제목은 무엇의 넓이인지 밝힌다."""
    issues = detect_issues(mall_result, [("매장 68", 0.0, 0.0)])
    assert any("외진" in i.title or "시야" in i.title for i in issues)
    for issue in issues:
        assert "'" not in issue.title
        if "외진" in issue.title:
            assert "찾아가기 먼 바닥" in issue.title
        if "시야" in issue.title:
            assert "벽에 가려 잘 안 보이는 바닥" in issue.title
            assert "이 구역 안에서 둘러보면 바닥" in issue.detail


def test_rule_explanation_points_at_card_numbers_not_coordinates(mall_result):
    """규칙 요약문이 「왼쪽 위에서 오른쪽으로 137m, 아래로 54m 지점」 같은 좌표 대신
    카드 번호로 가리킨다 — 도면에 눈금이 없어 좌표로는 그 자리를 찾을 수 없다(2026-11-07)."""
    from app.services.floorplan_vga import rule_based_floorplan_explanation

    result = mall_result
    issues = detect_issues(result, [])
    text = rule_based_floorplan_explanation(result, issues)

    assert "왼쪽 위에서" not in text
    best = next(i for i, x in enumerate(issues, 1) if x.kind == "best_zone")
    assert f"목이 가장 좋은 곳은 도면 " in text and f"{best}번 카드의 구역" in text
    dead = next(i for i, x in enumerate(issues, 1) if x.kind == "dead_zone")
    assert f"가장 외진 곳은 도면 " in text and f"{dead}번 카드의 구역" in text

    # 외진 구역 카드가 있으면 「어디를 봐도 … 고른 편」이라 하지 않는다 — 카드와 어긋난다(2026-11-07).
    assert "고른 편" not in text

    # 카드 없이 부르면 방향만 말한다 — 좌표는 여전히 없다.
    bare = rule_based_floorplan_explanation(result)
    assert "왼쪽 위에서" not in bare and "번 카드" not in bare


def test_issue_out_carries_kind_so_the_tab_count_can_skip_the_best_zone():
    """「발견된 문제」 개수에서 좋은 구역 안내를 빼려면 응답에 kind가 실려야 한다 (2026-11-07)."""
    from app.routers.floorplan import _issue_out
    from app.services.floorplan_issues import Issue

    out = _issue_out(Issue(severity="low", title="t", detail="d", x=0.0, y=0.0, kind="best_zone"))
    assert out.kind == "best_zone"
    assert _issue_out(Issue(severity="high", title="t", detail="d", x=0.0, y=0.0)).kind == ""


def test_rule_explanation_says_when_part_of_plan_was_left_out(mall_result):
    """도면 일부가 벽에 막혀 빠졌으면 요약이 그 비율과 카드 번호를 먼저 밝힌다 — 예전엔
    「건물 안을 … 살펴봤습니다」로 전체를 본 듯 읽혔다(2026-11-08, wtc-mall 반영률 65%)."""
    import dataclasses

    from app.services.floorplan_vga import ExcludedCell, rule_based_floorplan_explanation

    partial = dataclasses.replace(
        mall_result, coverage_pct=65.0, excluded_cells=[ExcludedCell(row=0, col=0), ExcludedCell(row=0, col=1)]
    )
    issues = detect_issues(partial, [])
    assert issues[0].kind == "excluded"
    text = rule_based_floorplan_explanation(partial, issues)
    assert "35%가 벽에 막혀 계산에서 빠졌고" in text and "1번 카드를 먼저 확인하세요" in text
    assert "빠졌고" not in rule_based_floorplan_explanation(mall_result, detect_issues(mall_result, []))
