"""도면 상담(2026-11-09) — 사실 블록에 카드·방이 들어가는지, 카드 표지를 떼어 번호로
돌려주는지, LLM이 없을 때 카드 목록에서 고른 규칙 답으로 돌아가는지."""

import asyncio

from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import FloorplanIssueOut, FloorplanRoomOut, FloorplanSpotOut, FloorplanSummaryOut
from app.services import floorplan_chat

client = TestClient(app)


def _summary() -> FloorplanSummaryOut:
    spot = FloorplanSpotOut(x=50.0, y=700.0, value=0.9, percentile=1.0, x_m=8.0, y_m=105.0)
    return FloorplanSummaryOut(
        cols=66, rows=33, cell_size=24.1, cell_count=1512,
        integration_mean=2.08, integration_max=2.70, connectivity_mean=261.0, coverage_pct=97.0,
        cell_size_m=3.6, dead_spots=[spot],
    )


def _issues() -> list[FloorplanIssueOut]:
    return [
        FloorplanIssueOut(
            severity="high", title="남동쪽 약 1,031㎡가 외진 구역입니다", detail="손님 발길이 잘 안 닿습니다.",
            x=1400.0, y=700.0, cell_count=80, area_m2=1031.0, evidence="목 하위 10%", kind="dead_zone",
        ),
        FloorplanIssueOut(
            severity="medium", title="68번 매장 근처 199㎡가 벽에 가려 잘 안 보입니다", detail="안내 표지가 필요합니다.",
            x=300.0, y=300.0, cell_count=15, area_m2=199.0, evidence="시야 하위 10%",
        ),
        FloorplanIssueOut(
            severity="low", title="가운데 광장 약 802㎡가 가장 목이 좋은 곳입니다", detail="눈에 띄어야 하는 매장 자리입니다.",
            x=800.0, y=400.0, cell_count=60, area_m2=802.0, evidence="목 상위 10%", kind="best_zone",
        ),
    ]


def _rooms() -> list[FloorplanRoomOut]:
    def room(i, name, pct, analyzed=100.0):
        return FloorplanRoomOut(
            index=i, name=name, cell_count=10, area_m2=120.0, x=1.0, y=1.0,
            integration_mean=2.0, choice_mean=1.0, connectivity_mean=100.0,
            integration_percentile=pct, analyzed_pct=analyzed,
        )
    return [room(1, "매장 06", 95.0), room(2, "매장 68", 12.0), room(3, "창고", 50.0, analyzed=0.0)]


def test_context_carries_cards_rooms_and_rule_text():
    ctx = floorplan_chat.build_context(_summary(), _issues(), _rooms(), "규칙 문장", "**한 줄 요약** 광장이 목입니다.")
    assert "1. [즉시 검토, 외진 구역] 남동쪽 약 1,031㎡" in ctx
    assert "3. [참고, 좋은 구역 안내(문제 아님)]" in ctx
    assert "매장 06, 바닥 약 120㎡: 방들끼리 견준 목 상위 5%" in ctx
    assert "창고, 바닥 약 120㎡: 벽에 막혀 분석에서 빠짐" in ctx
    assert "규칙 요약문: 규칙 문장" in ctx and "광장이 목입니다" in ctx
    assert "가장 외진 자리: 남서쪽" in ctx


def test_context_warns_when_coverage_is_low():
    s = _summary()
    s.coverage_pct = 64.0
    assert "도면 일부만 본 것" in floorplan_chat.build_context(s, [], [], "규칙", None)


def test_card_marker_is_stripped_and_returned():
    body, card = floorplan_chat.split_card_marker("창고는 1번 카드 자리가 맞습니다.\n\n【카드 1】", 3)
    assert (body, card) == ("창고는 1번 카드 자리가 맞습니다.", 1)
    body, card = floorplan_chat.split_card_marker("여러 곳입니다. [카드 2]", 3)
    assert (body, card) == ("여러 곳입니다.", 2)
    # 범위 밖 번호는 표지만 떼고 버린다 — 없는 카드를 칠하면 화면이 깨진다.
    body, card = floorplan_chat.split_card_marker("답입니다. 【카드 9】", 3)
    assert (body, card) == ("답입니다.", None)
    # 표지가 없으면 그대로.
    assert floorplan_chat.split_card_marker("그냥 답입니다.", 3) == ("그냥 답입니다.", None)


def test_system_prompt_speaks_to_laypeople_without_jargon():
    p = floorplan_chat.SYSTEM_PROMPT
    assert "일반인" in p and "3~5문장" in p
    assert "Integration, Choice, VGA" in p  # 쓰지 말라는 목록으로만 등장
    assert "【카드 N】" in p
    assert "벽 구조만" in p


def test_rule_answer_picks_the_matching_card():
    issues = _issues()
    text, card = floorplan_chat.rule_based_answer("이 도면에서 가장 큰 문제가 뭐예요?", issues, "규칙")
    assert card == 1 and "1번 카드" in text
    text, card = floorplan_chat.rule_based_answer("창고는 어디에 두면 좋아요?", issues, "규칙")
    assert card == 1 and "창고" in text
    text, card = floorplan_chat.rule_based_answer("손님이 잘 모이는 자리는 어디예요?", issues, "규칙")
    assert card == 3 and "3번 카드" in text
    text, card = floorplan_chat.rule_based_answer("이거 믿어도 돼요?", issues, "규칙")
    assert card is None and "벽 구조만" in text
    text, card = floorplan_chat.rule_based_answer("오늘 날씨 어때요?", issues, "규칙 문장")
    assert card is None and "규칙 문장" in text


def test_answer_falls_back_to_rule_when_llm_is_off(monkeypatch):
    async def off(**_):
        return None

    monkeypatch.setattr(floorplan_chat, "complete_chat", off)
    text, card, source = asyncio.run(
        floorplan_chat.answer_floorplan_question(
            summary=_summary(), issues=_issues(), rooms=[], rule_text="규칙", narrative=None,
            messages=[{"role": "user", "content": "가장 큰 문제가 뭐야?"}],
        )
    )
    assert source == "rule" and card == 1 and "1번 카드" in text


def test_answer_uses_llm_and_primes_history_with_facts(monkeypatch):
    seen = {}

    async def on(**kwargs):
        seen.update(kwargs)
        return "2번 카드 자리는 벽에 가려 잘 안 보여서 안내 표지를 다는 게 좋습니다.\n【카드 2】"

    monkeypatch.setattr(floorplan_chat, "complete_chat", on)
    text, card, source = asyncio.run(
        floorplan_chat.answer_floorplan_question(
            summary=_summary(), issues=_issues(), rooms=_rooms(), rule_text="규칙", narrative="해석",
            messages=[
                {"role": "user", "content": "가장 큰 문제가 뭐야?"},
                {"role": "assistant", "content": "1번 카드입니다."},
                {"role": "user", "content": "그럼 2번은?"},
            ],
        )
    )
    assert source == "llm" and card == 2 and "【" not in text
    assert seen["user"] == "그럼 2번은?"
    history = seen["history"]
    assert history[0]["role"] == "user" and "[도면 분석 사실]" in history[0]["content"]
    assert history[-1] == {"role": "assistant", "content": "1번 카드입니다."}


def test_history_is_trimmed_to_recent_turns():
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(30)]
    trimmed = floorplan_chat._trim_history(msgs)
    assert len(trimmed) == floorplan_chat.MAX_HISTORY_TURNS and trimmed[-1]["content"] == "29"


def test_chat_endpoint_rejects_when_last_message_is_not_from_user():
    body = {
        "summary": _summary().model_dump(), "issues": [], "rule_text": "규칙",
        "messages": [{"role": "assistant", "content": "안녕하세요"}],
    }
    assert client.post("/api/floorplan/chat", json=body).status_code == 400


def test_chat_endpoint_returns_rule_answer_without_llm(monkeypatch):
    async def off(**_):
        return None

    monkeypatch.setattr(floorplan_chat, "complete_chat", off)
    body = {
        "summary": _summary().model_dump(), "issues": [i.model_dump() for i in _issues()], "rule_text": "규칙",
        "messages": [{"role": "user", "content": "창고는 어디에 둘까요?"}],
    }
    r = client.post("/api/floorplan/chat", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "rule" and data["card"] == 1 and "창고" in data["text"]
