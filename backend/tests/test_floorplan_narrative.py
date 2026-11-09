"""LLM 해석 층 — 프롬프트에 사실이 다 들어가는지, LLM이 없을 때 규칙 문장으로
조용히 돌아가는지."""

import asyncio

from app.models.schemas import FloorplanIssueOut, FloorplanSpotOut, FloorplanSummaryOut
from app.services import floorplan_narrative


def _summary() -> FloorplanSummaryOut:
    spot = FloorplanSpotOut(x=977.0, y=398.0, value=2.7, percentile=100.0, x_m=147.0, y_m=60.0)
    return FloorplanSummaryOut(
        cols=66, rows=33, cell_size=24.1, cell_count=1512,
        integration_mean=2.08, integration_max=2.70, connectivity_mean=261.0, coverage_pct=97.0,
        integration_local_mean=1.5, mean_depth_mean=2.15, choice_max=6736.0, choice_sampled=True,
        cell_size_m=3.6, best_spots=[spot], through_spots=[spot], dead_spots=[],
    )


def _issue() -> FloorplanIssueOut:
    return FloorplanIssueOut(
        severity="medium", title="남동쪽 약 537㎡가 외진 구역입니다", detail="닿기 어렵습니다",
        x=1.0, y=2.0, x_m=228.0, y_m=97.0, cell_count=41, area_m2=537.0, evidence="목 하위 10%",
    )


def test_prompt_carries_every_fact_the_model_may_cite():
    prompt = floorplan_narrative.build_user_prompt(_summary(), [_issue()], "규칙 문장")
    assert "남동쪽 약 537㎡" in prompt
    assert "[개선 권장]" in prompt
    assert "반영률 97%" in prompt
    assert "규칙 문장" in prompt


def test_prompt_gives_one_spot_per_kind_with_a_direction():
    """자리를 여러 개 넘기면 모델이 목록을 그대로 옮겨 적어 "147m, 60m와 85m,
    63m, 99m…"가 된다 (2026-09-24 라이브). 종류마다 한 곳만, 방향과 함께 준다."""
    spot = FloorplanSpotOut(x=977.0, y=398.0, value=2.7, percentile=100.0, x_m=147.0, y_m=60.0)
    other = FloorplanSpotOut(x=566.0, y=420.0, value=2.6, percentile=99.0, x_m=85.0, y_m=63.0)
    summary = _summary()
    summary.dead_spots = [spot, other]
    prompt = floorplan_narrative.build_user_prompt(summary, [], "규칙 문장")
    dead_line = next(l for l in prompt.splitlines() if l.startswith("외진 자리"))
    assert "85m" not in dead_line, "두 번째 자리까지 넘어가면 문장이 좌표 나열이 된다"
    assert "가운데" in dead_line or "쪽" in dead_line


def test_prompt_does_not_pass_single_best_or_through_cells():
    """2026-10-24: 「지나가는 길목」 1위 칸은 통로 모서리에 튀어 외진 골목 구석을 「눈에 띄어야
    하는 매장 자리」로 추천하게 했고, 「목이 좋은 자리」 1위 칸은 좋은 통로의 한쪽 끝이라
    「서쪽이 가장 목이 좋다」가 됐다. 둘 다 넘기지 않고 「가장 목이 좋은 곳」 구역 카드를 쓴다."""
    prompt = floorplan_narrative.build_user_prompt(_summary(), [], "규칙 문장")
    assert not any(l.startswith(("목이 좋은 자리", "사람이 지나가는 길목")) for l in prompt.splitlines())
    assert "147m" not in prompt
    assert "지나가는 곳」은 입력에 없으니" in floorplan_narrative.SYSTEM_PROMPT


def test_system_prompt_bans_coordinates_in_the_opening_section():
    assert "「한 줄 요약」에는 좌표" in floorplan_narrative.SYSTEM_PROMPT
    assert "한눈에" not in floorplan_narrative.SYSTEM_PROMPT and "어디를 어떻게" not in floorplan_narrative.SYSTEM_PROMPT
    assert "한 곳까지만" in floorplan_narrative.SYSTEM_PROMPT


def test_falls_back_to_rule_text_when_llm_is_off(monkeypatch):
    async def off(**_):
        return None

    monkeypatch.setattr(floorplan_narrative, "complete_text", off)
    text, source = asyncio.run(floorplan_narrative.explain_floorplan(_summary(), [_issue()], "규칙 문장"))
    assert (text, source) == ("규칙 문장", "rule")


def test_uses_llm_text_when_it_answers(monkeypatch):
    async def on(**_):
        return "  **한눈에** 중앙 통로가 목입니다.\n\n**어디를 어떻게** 남쪽 벽을 틉니다.  "

    monkeypatch.setattr(floorplan_narrative, "complete_text", on)
    text, source = asyncio.run(floorplan_narrative.explain_floorplan(_summary(), [], "규칙 문장"))
    # 모델이 옛 소제목을 내도 새 이름으로 바로잡는다.
    assert source == "llm" and text.startswith("**한 줄 요약**")
    assert "**고칠 곳과 방법**" in text
