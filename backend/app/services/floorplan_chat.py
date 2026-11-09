"""도면 분석 결과를 두고 일반인과 **이어 묻고 답하는** 상담 (2026-11-09).

`floorplan_narrative.explain_floorplan`이 분석 직후 한 번 쓰는 「AI 해석」이라면,
이 층은 그 뒤에 사용자가 "그래서 창고는 어디 두지?", "2번 카드가 무슨 말이야?"처럼
되묻는 데 답한다. 사실은 전부 입력(summary·카드 목록·방 목록·규칙 요약·AI 해석)에
있고, 모델은 그 사실을 쉬운 말로 골라 말하기만 한다 — 입력에 없는 장소·수치·매출·
임대료는 말하지 않는다(SPEC_FLOORPLAN_TABS_UI Q6의 절충: 위치는 규칙이, 문장만 모델이).

답이 어느 카드 얘기면 끝에 「【카드 N】」 표지를 달게 하고, 여기서 그 표지를 떼어
번호만 따로 돌려준다 — 화면이 그 카드 구역을 도면에 칠하기 위해서다(Q10: 자연어만으로는
강조할 수 없으니 구조화된 계약이 필요하다).

LLM이 꺼져 있거나 실패하면 규칙 답(`rule_based_answer`)으로 돌아간다. 분석 5초 룰은
분석에만 걸리고 이 상담은 `/explain`처럼 그 뒤에 따로 부른다.
"""

from __future__ import annotations

import re

from app.models.schemas import FloorplanIssueOut, FloorplanRoomOut, FloorplanSummaryOut
from app.services.llm_client import complete_chat

# 한 번에 넘기는 대화 턴 수 — 더 길면 앞을 자른다(사실 블록은 매번 다시 싣는다).
MAX_HISTORY_TURNS = 12

SYSTEM_PROMPT = """당신은 상가 건물 평면도의 구조 분석 결과를 **건축을 모르는 일반인**에게 설명해 주는 상담원입니다.
사용자는 가게를 열려는 사람, 건물주, 설계를 검토하는 사람입니다. 이미 화면에서 분석 결과(번호 붙은 카드, 색칠된 도면, AI 해석)를 보고 있고, 그걸 두고 되묻습니다.

지켜야 할 것:
- **입력에 있는 사실만** 말합니다. 없는 장소·수치·매출·임대료·손님 수·법규 판정을 지어내지 않습니다. 모르면 "이 분석으로는 알 수 없습니다"라고 하고, 대신 무엇은 알 수 있는지 한 줄 덧붙입니다.
- 쉬운 한국어로, 존댓말로, **3~5문장**으로 짧게 답합니다. 영어·전문 용어(Integration, Choice, VGA, 통합도, 선택도, 격자)를 쓰지 않습니다. 대신 "손님이 잘 모이는 자리", "발길이 잘 안 닿는 구석", "둘러보면 보이는 범위"처럼 말합니다.
- 위치는 입력에 적힌 대로 **카드 번호와 방향**으로 가리킵니다 (예: "2번 카드의 남서쪽 68번 매장 근처"). 좌표(m) 숫자는 한 답에 한 곳까지만, 꼭 필요할 때만 씁니다.
- 방 이름은 "68번 매장"처럼 말로 씁니다. 도면 글자를 따옴표로 옮기지 않습니다.
- 넓이를 말할 때는 무엇의 넓이인지 밝힙니다 ("벽에 가려 잘 안 보이는 바닥 약 191㎡").
- 답이 주로 **카드 하나**에 관한 것이면 답 맨 끝에 줄을 바꿔 「【카드 N】」(N은 카드 번호)을 붙입니다. 여러 카드를 다루거나 카드와 무관하면 붙이지 않습니다. 표지는 한 개만, 맨 끝에만.
- 이 결과는 **벽 구조만** 본 것이고 실제 매출·방문자 수로 검증된 것이 아닙니다. 사용자가 믿어도 되냐고 묻거나 돈이 걸린 결정(계약·공사)을 말하면 그 한계를 한 문장으로 분명히 말합니다. 그 밖에는 매번 되풀이하지 않습니다.
- 앞선 대화를 이어 받습니다. 같은 말을 되풀이하지 말고 새로 물은 것에 답합니다."""

_CARD_RE = re.compile(r"\s*【\s*카드\s*(\d+)\s*】\s*\.?\s*$")
_CARD_RE_LOOSE = re.compile(r"\s*[\[(【]\s*카드\s*(\d+)\s*[\])】]\s*\.?\s*$")

_SEVERITY_KO = {"high": "즉시 검토", "medium": "개선 권장", "low": "참고"}
_KIND_KO = {"best_zone": "좋은 구역 안내(문제 아님)", "dead_zone": "외진 구역"}


def _side(summary: FloorplanSummaryOut, x: float, y: float) -> str:
    width = summary.cols * summary.cell_size
    height = summary.rows * summary.cell_size
    ns = "북" if y < height / 3 else ("남" if y > 2 * height / 3 else "")
    ew = "서" if x < width / 3 else ("동" if x > 2 * width / 3 else "")
    label = ns + ew
    return f"{label}쪽" if label else "가운데"


def _room_line(room: FloorplanRoomOut) -> str:
    name = room.name or f"{room.index}번 공간"
    area = f", 바닥 약 {room.area_m2:,.0f}㎡" if room.area_m2 else ""
    if room.analyzed_pct <= 0:
        return f"  - {name}{area}: 벽에 막혀 분석에서 빠짐"
    return f"  - {name}{area}: 방들끼리 견준 목 상위 {max(1, round(100 - room.integration_percentile)):.0f}%"


def build_context(
    summary: FloorplanSummaryOut,
    issues: list[FloorplanIssueOut],
    rooms: list[FloorplanRoomOut],
    rule_text: str,
    narrative: str | None,
) -> str:
    """모델이 인용할 수 있는 사실 전부. 매 턴 system 다음 첫 user 메시지로 싣는다."""
    size = f"한 칸 약 {summary.cell_size_m:.1f}m" if summary.cell_size_m else "축척 없음(캔버스 좌표)"
    card_lines = [
        f"  {i}. [{_SEVERITY_KO[c.severity]}{', ' + _KIND_KO[c.kind] if c.kind in _KIND_KO else ''}] "
        f"{c.title} — {c.detail} (근거: {c.evidence}; 위치: {_side(summary, c.x, c.y)})"
        for i, c in enumerate(issues, 1)
    ] or ["  (없음)"]
    # 방은 이름이 읽힌 것 위주로, 목 좋은 순 위 5곳 + 아래 3곳만 — 다 넘기면 모델이 표를 옮겨 적는다.
    ranked = sorted((r for r in rooms if r.analyzed_pct > 0), key=lambda r: -r.integration_percentile)
    picked = ranked[:5] + ([r for r in ranked[5:]][-3:] if len(ranked) > 8 else ranked[5:])
    # 벽에 막혀 빠진 방은 이름 있는 것만 3곳까지 — 「왜 68번 매장은 안 나오냐」에 답하려면 있어야 한다.
    blocked = [r for r in rooms if r.analyzed_pct <= 0 and r.name][:3]
    room_lines = [_room_line(r) for r in picked + blocked] or ["  (방 정보 없음)"]
    dead = ""
    if summary.dead_spots:
        s = summary.dead_spots[0]
        dead = f"가장 외진 자리: {_side(summary, s.x, s.y)}"
    parts = [
        "[도면 분석 사실]",
        f"격자 {summary.cell_count:,}칸, {size}, 도면 반영률 {summary.coverage_pct:.0f}%"
        + (" (반영률이 낮으면 도면 일부만 본 것)" if summary.coverage_pct < 80 else ""),
        "화면의 카드(번호는 도면 동그라미와 같음):",
        *card_lines,
    ]
    if dead:
        parts.append(dead)
    if summary.robot_passable_pct is not None:
        ends = len(summary.robot_dead_ends)
        parts.append(
            f"로봇 통로(물류 로봇 기준 폭 {summary.robot_aisle_min_m:.1f}m 이상): 바닥의 {summary.robot_passable_pct:.0f}%를 로봇이 "
            f"지나갈 수 있고, 로봇 통로의 막다른 끝이 {ends}곳" + (f" (가장 좁은 곳 {_side(summary, summary.robot_dead_ends[0].x, summary.robot_dead_ends[0].y)})" if ends else "")
        )
    parts += ["공간(방)별 목 순위:", *room_lines, "", f"규칙 요약문: {rule_text}"]
    if narrative and narrative.strip():
        parts += ["", f"화면에 보이는 AI 해석:\n{narrative.strip()}"]
    parts += ["", "위 사실만으로 아래 대화에 답하세요."]
    return "\n".join(parts)


def split_card_marker(text: str, card_count: int) -> tuple[str, int | None]:
    """답 끝의 「【카드 N】」을 떼고 (본문, N)을 돌려준다. 범위 밖 번호는 버린다."""
    cleaned = text.strip()
    card: int | None = None
    for pattern in (_CARD_RE, _CARD_RE_LOOSE):
        m = pattern.search(cleaned)
        if m:
            n = int(m.group(1))
            cleaned = cleaned[: m.start()].rstrip()
            if 1 <= n <= card_count:
                card = n
            break
    return cleaned, card


def _first_index(issues: list[FloorplanIssueOut], pred) -> int | None:
    for i, c in enumerate(issues, 1):
        if pred(c):
            return i
    return None


def rule_based_answer(question: str, issues: list[FloorplanIssueOut], rule_text: str) -> tuple[str, int | None]:
    """LLM이 없을 때의 답 — 카드 목록에서 물음과 맞는 것을 고른다. 없는 말은 안 만든다."""
    q = question.replace(" ", "")
    limit = "이 결과는 벽 구조만 본 것이라 실제 매출이나 손님 수로 확인된 것은 아닙니다."
    if any(k in q for k in ("믿어", "정확", "확실", "맞아", "맞나", "근거")):
        return (
            "벽과 문의 배치만 보고 어디가 사람이 모이기 쉬운 구조인지 계산한 결과입니다. "
            "건축 평면도 샘플에서 같은 계산을 하는 다른 프로그램과 견줘 순위가 대체로 일치했지만, "
            + limit,
            None,
        )
    if any(k in q for k in ("문제", "위험", "나쁜", "안좋", "걱정", "고쳐", "고칠")):
        i = _first_index(issues, lambda c: c.kind != "best_zone" and c.severity == "high") or _first_index(
            issues, lambda c: c.kind != "best_zone"
        )
        if i:
            c = issues[i - 1]
            return (f"가장 먼저 볼 것은 {i}번 카드입니다. {c.title} {c.detail}", i)
        return ("이 도면에서는 따로 짚을 문제가 나오지 않았습니다. " + rule_text, None)
    if any(k in q for k in ("창고", "사무", "직원", "외진", "구석", "조용")):
        i = _first_index(issues, lambda c: c.kind == "dead_zone")
        if i:
            c = issues[i - 1]
            return (
                f"창고나 사무실처럼 손님이 올 필요가 없는 공간은 {i}번 카드 자리가 맞습니다. "
                f"{c.title} 발길이 잘 안 닿는 곳이라 매장으로는 불리하지만 뒷공간으로는 괜찮습니다.",
                i,
            )
    if any(k in q for k in ("목", "좋은", "모이", "추천", "어디에", "매장", "장사", "자리")):
        i = _first_index(issues, lambda c: c.kind == "best_zone")
        if i:
            c = issues[i - 1]
            return (
                f"손님이 가장 잘 모이는 구조는 {i}번 카드 자리입니다. {c.detail} "
                "눈에 띄어야 하는 매장이나 안내 데스크를 두기에 좋습니다. " + limit,
                i,
            )
    return (
        "지금은 AI 상담을 쓸 수 없어 요약문으로 답합니다. " + rule_text,
        None,
    )


def _trim_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(messages) <= MAX_HISTORY_TURNS:
        return messages
    return messages[-MAX_HISTORY_TURNS:]


async def answer_floorplan_question(
    *,
    summary: FloorplanSummaryOut,
    issues: list[FloorplanIssueOut],
    rooms: list[FloorplanRoomOut],
    rule_text: str,
    narrative: str | None,
    messages: list[dict[str, str]],
) -> tuple[str, int | None, str]:
    """(답, 카드 번호 또는 None, source). messages의 마지막은 user여야 한다."""
    question = messages[-1]["content"]
    history = _trim_history(messages[:-1])
    context = build_context(summary, issues, rooms, rule_text, narrative)
    # 사실 블록은 첫 user 턴으로, 모델이 "알겠습니다"로 받은 것처럼 둔다 — 그래야 이어지는
    # 대화에서 사실이 system 바로 다음에 한 번만 실린다.
    primed = [
        {"role": "user", "content": context},
        {"role": "assistant", "content": "네, 이 사실만 바탕으로 쉬운 말로 답하겠습니다."},
        *history,
    ]
    text = await complete_chat(
        system=SYSTEM_PROMPT,
        history=primed,
        user=question,
        max_tokens=1200,
        timeout_s=25.0,
    )
    if text and text.strip():
        body, card = split_card_marker(text, len(issues))
        if body:
            return body, card, "llm"
    body, card = rule_based_answer(question, issues, rule_text)
    return body, card, "rule"
