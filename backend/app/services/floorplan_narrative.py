"""LLM narrative for a floor-plan analysis — "so what should I change".

The rule-based explanation (``floorplan_vga.rule_based_floorplan_explanation``)
and the issue list are the facts. This layer only reorders and connects them
into advice; it must not invent places or numbers that are not in its input.
Same split as ``llm_explain.py`` for the site score: rule text is returned
immediately with the analysis, the narrative is fetched afterwards and swapped
in when it arrives, so the 5-second analysis budget is untouched.
"""

from __future__ import annotations

from app.models.schemas import FloorplanIssueOut, FloorplanSummaryOut
from app.services.llm_client import complete_text

SYSTEM_PROMPT = """당신은 상가 건물의 평면도를 보고 매장 배치를 조언하는 사람입니다.
입력으로 도면의 구조 분석 결과(목이 좋은 자리, 외진 자리, 발견된 문제)가 옵니다.

규칙:
- 입력에 있는 사실만 씁니다. 없는 장소·수치를 지어내지 않습니다. 매출·임대료 수치도 말하지 않습니다.
- 영어·전문 용어(Integration, Choice, VGA)를 쓰지 않습니다. "목", "외진 자리"로 말합니다.
- 「가장 많이 지나가는 곳」은 입력에 없으니 말하지 않습니다.
- 위치는 입력에 적힌 그대로 말합니다 (예: "남서쪽 68번 매장 근처", "도면 왼쪽 위에서 오른쪽으로 147m, 아래로 60m").
- 방 이름은 "68번 매장"처럼 말로 씁니다. "'매장 68'"처럼 따옴표로 도면 글자를 옮기지 않습니다.
- 넓이를 말할 때는 무엇의 넓이인지 밝힙니다 — "벽에 가려 잘 안 보이는 바닥 약 191㎡", "이 구역 안에서 둘러보면 보이는 바닥 약 462㎡(건물 평균 1,450㎡)". 넓이 숫자만 따로 쓰지 않습니다.
- **「한 줄 요약」에는 좌표(m)를 쓰지 않습니다.** 방향과 자리 이름으로만 말합니다 (예: "중앙 통로 가운데가 가장 목이 좋습니다").
- 좌표는 「고칠 곳과 방법」과 「자리 추천」에서만, 한 줄에 **한 곳까지만** 씁니다. 좌표를 여러 개 나열하지 않습니다.
- 입력에 자리가 여러 곳 있어도 **가장 중요한 한 곳만 골라** 말합니다. 목록을 그대로 옮겨 적지 않습니다.
- 이 결과는 벽 구조만 본 것이며 실제 매출로 검증된 것이 아닙니다. 마지막 문장에 그 한계를 한 번 밝힙니다.
- 형식: 소제목 3개 — **한 줄 요약**(1~2문장, 좌표 없이 이 도면이 어떤 구조인지) / **고칠 곳과 방법**(문제마다 줄을 바꿔 한 줄씩, 한 줄에는 한 구역만 — "남서쪽 68번 매장 근처는 벽에 가려 잘 안 보이는 바닥이 약 191㎡라, 벽을 트거나 안내 표지를 다세요." 꼴. 두 구역을 한 문장에 섞지 않습니다) / **자리 추천**(머물게 하는 매장·핵심 매장은 목 좋은 곳, 창고·사무는 외진 곳처럼 입력에 있는 자리로만).
- 전체 250자 이내의 문장 12개를 넘기지 않습니다."""


def _side(summary: FloorplanSummaryOut, x: float, y: float) -> str:
    """방향 이름. 좌표만 주면 모델이 그걸 그대로 읊어 문장이 숫자 나열이 된다."""
    width = summary.cols * summary.cell_size
    height = summary.rows * summary.cell_size
    ns = "북" if y < height / 3 else ("남" if y > 2 * height / 3 else "")
    ew = "서" if x < width / 3 else ("동" if x > 2 * width / 3 else "")
    label = ns + ew
    return f"{label}쪽" if label else "가운데"


def _fmt_spots(summary: FloorplanSummaryOut, label: str, spots) -> str:
    """자리는 **한 곳만** 넘긴다. 여러 곳을 주면 모델이 목록을 그대로 옮겨 적어
    "147m, 60m와 85m, 63m, 99m…"가 된다 (2026-09-24 라이브에서 실제로 그랬다)."""
    if not spots:
        return ""
    spot = spots[0]
    where = (
        f"도면 왼쪽 위에서 오른쪽으로 {spot.x_m:.0f}m, 아래로 {spot.y_m:.0f}m"
        if spot.x_m is not None and spot.y_m is not None
        else f"({spot.x:.0f}, {spot.y:.0f})"
    )
    return f"{label}: {_side(summary, spot.x, spot.y)} — {where}"


def build_user_prompt(summary: FloorplanSummaryOut, issues: list[FloorplanIssueOut], rule_text: str) -> str:
    severity_ko = {"high": "즉시 검토", "medium": "개선 권장", "low": "참고"}
    issue_lines = [
        f"  {i}. [{severity_ko[x.severity]}] {x.title} — {x.detail} (근거: {x.evidence})"
        for i, x in enumerate(issues, 1)
    ] or ["  (없음)"]
    size = f"한 칸 약 {summary.cell_size_m:.1f}m" if summary.cell_size_m else "축척 없음(캔버스 좌표)"
    return "\n".join(
        [
            f"격자 {summary.cell_count:,}칸, {size}, 도면 반영률 {summary.coverage_pct:.0f}%",
            f"목 최고/평균 = {summary.integration_max:.2f}/{summary.integration_mean:.2f}",
            # 「목이 좋은 자리」 1위 칸도 넘기지 않는다 — 좋은 통로 전체 중 한쪽 끝이 뽑혀
            # 「서쪽이 가장 목이 좋다」가 됐다. 대신 발견된 문제의 「가장 목이 좋은 곳」 구역을 쓴다.
            # 「지나가는 길목」 1위 칸은 넘기지 않는다 — 통로 모서리 한 칸에 튀는 값이라 모델이
            # 외진 구석을 「눈에 띄어야 하는 매장 자리」로 추천했다(2026-10-24).
            _fmt_spots(summary, "외진 자리", summary.dead_spots),
            "발견된 문제:",
            *issue_lines,
            "",
            f"규칙 기반 요약(참고): {rule_text}",
            "",
            "위 사실만으로 세 소제목 형식의 조언을 쓰세요.",
        ]
    )


# 소제목 이름 (2026-10-27 사용자 결정) — 「한눈에」는 평면도의 「한눈에 보이는 범위」(시야)로 읽혀
# 「한 줄 요약」으로, 「어디를 어떻게」는 「고칠 곳과 방법」으로 바꿨다. 모델이 옛 이름을 내도 바로잡는다.
_HEADING_RENAMES = (("**한눈에**", "**한 줄 요약**"), ("**어디를 어떻게**", "**고칠 곳과 방법**"))


def _rename_headings(text: str) -> str:
    for old, new in _HEADING_RENAMES:
        text = text.replace(old, new)
    return text


async def explain_floorplan(
    summary: FloorplanSummaryOut, issues: list[FloorplanIssueOut], rule_text: str
) -> tuple[str, str]:
    """(text, source). Falls back to the rule text when the LLM is off or fails."""
    text = await complete_text(
        system=SYSTEM_PROMPT,
        user=build_user_prompt(summary, issues, rule_text),
        # 추론 모델은 예산의 상당부분을 추론에 쓰므로 본문 분량(250자)보다
        # 넉넉히 준다 — 모자라면 본문이 빈 채로 돌아온다.
        max_tokens=2000,
        timeout_s=25.0,
    )
    if text and text.strip():
        return _rename_headings(text.strip()), "llm"
    return rule_text, "rule"
