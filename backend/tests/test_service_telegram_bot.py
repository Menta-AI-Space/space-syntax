"""상권분석 Agent를 Telegram에서 쓰는 서비스 봇의 동작 계약."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import telegram_agent_bot as bot


def _response(**overrides):
    base = {
        "query": "신촌 카페 어때?",
        "address": "서대문구 신촌동",
        "business_type": "cafe",
        "business_label": "카페",
        "site": {
            "total_score": 63.3,
            "grade": "보통",
            "verdict_one_liner": "배후 수요는 있으나 경쟁이 치열한 입지입니다.",
        },
        "answer": "유동 수요는 있지만 같은 업종 경쟁을 현장에서 확인하세요.",
        "notice": "",
        "pending": None,
        "upjong": {"inds_scls_cd": "I21201", "inds_scls_nm": "카페"},
        "parameters": {},
    }
    base.update(overrides)
    return base


def test_allowed_ids_fail_closed_and_accept_numeric_ids():
    assert bot.allowed_ids(None) == set()
    assert bot.allowed_ids("") == set()
    assert bot.allowed_ids("123, -456;invalid") == {123, -456}
    assert bot.is_authorized(123, {123}) is True
    assert bot.is_authorized(999, {123}) is False


def test_analysis_request_carries_recent_history_and_pending_context():
    state = bot.ChatState(
        history=[{"query": str(i), "address": "신촌", "business_type": "cafe"} for i in range(7)],
        context={"tool": "lease_verdict", "address": "신촌", "business_type": "cafe"},
    )

    request = bot.analysis_request("월세 300이야", state)

    assert request["query"] == "월세 300이야"
    assert [turn["query"] for turn in request["history"]] == ["2", "3", "4", "5", "6"]
    assert request["context"]["tool"] == "lease_verdict"
    assert request["include_answer"] is True


def test_remember_response_keeps_followup_context_and_only_five_turns():
    state = bot.ChatState(
        history=[{"query": str(i), "address": "신촌", "business_type": "cafe"} for i in range(5)]
    )
    response = _response(
        pending={"tool": "lease_verdict", "address": "신촌", "business_type": "cafe"},
        parameters={"rent_man": 300},
    )

    bot.remember_response(state, response)

    assert len(state.history) == 5
    assert state.history[-1]["query"] == "신촌 카페 어때?"
    assert state.history[-1]["params"] == {"rent_man": 300}
    assert state.context["tool"] == "lease_verdict"


def test_format_analysis_exposes_place_score_verdict_notice_and_answer():
    text = bot.format_analysis(
        _response(notice="비교할 두 번째 주소가 없어 첫 주소만 분석했습니다.")
    )

    assert "서대문구 신촌동 · 카페" in text
    assert "63.3점 · 보통" in text
    assert "배후 수요는 있으나 경쟁이 치열한 입지입니다." in text
    assert "비교할 두 번째 주소" in text
    assert "유동 수요는 있지만" in text
    assert "웹사이트에 접속" not in text


def test_format_analysis_matches_web_result_sections_without_dropping_evidence():
    response = _response(
        site={
            "total_score": 63.3,
            "grade": "보통",
            "verdict_one_liner": "경쟁이 치열합니다.",
            "subscores": [
                {"key": "demand", "label": "배후 수요", "score": 100, "display": "매우 좋음", "detail": "거주 17,023명"},
                {"key": "competition", "label": "경쟁 밀도", "score": 100, "display": "매우 높음", "detail": "동일업종 78곳"},
            ],
            "industry_kpis": [
                {"label": "테이크아웃 수요", "score": 80.3, "display": "좋음", "detail": "출근시간 체류 22,159명"},
            ],
            "warnings": ["가까운 도로 1,000개만 사용했습니다."],
            "lat": 37.55,
            "lng": 126.93,
        },
        steps=[{"label": "주소 좌표 변환"}, {"label": "경쟁 업종 탐색"}],
        tool="area_search",
        tool_result={
            "headline": "후보 2곳을 찾았습니다.",
            "candidates": [
                {
                    "label": "서울 서대문구 창천동 30-18",
                    "recommend_score": 68.6,
                    "verdict_label": "보류",
                    "expected_monthly_sales_man": 1562.8,
                    "max_acceptable_rent_man": 234.4,
                    "strengths": ["찾아오기 쉬운 길"],
                    "risks": ["동일업종 89곳"],
                }
            ],
        },
        suggested_questions=["다른 후보지와 비교할까요?"],
    )

    text = bot.format_analysis(response)

    assert "실행한 분석: 주소 좌표 변환 → 경쟁 업종 탐색" in text
    assert "세부 점수" in text
    assert "배후 수요: 100점 · 매우 좋음 · 거주 17,023명" in text
    assert "경쟁 밀도: 100점 · 매우 높음 · 동일업종 78곳" in text
    assert "업종 핵심 지표" in text
    assert "테이크아웃 수요: 80.3점 · 좋음" in text
    assert "후보 2곳을 찾았습니다." in text
    assert "서울 서대문구 창천동 30-18" in text
    assert "예상 월매출 1,562.8만원" in text
    assert "적정 월세 상한 234.4만원" in text
    assert "가까운 도로 1,000개만 사용했습니다." in text
    assert "다른 후보지와 비교할까요?" in text
    assert "네이버 지도에서 상가·매물 확인" in text


def test_format_analysis_does_not_show_empty_tool_section():
    text = bot.format_analysis(_response(tool="area_insights", tool_result={"apartment": {}}))

    assert "🔎 실행 도구 결과" not in text


def test_format_analysis_preserves_primary_verdict_card_fields():
    text = bot.format_analysis(
        _response(
            tool="lease_verdict",
            tool_result={
                "headline": "조건부 계약입니다.",
                "verdict_label": "조건부 추천",
                "recommend_score": 72,
                "appropriate_rent_low_man": 220,
                "appropriate_rent_high_man": 260,
                "rent_gap_man": 40,
                "strengths": ["배후 수요가 충분함"],
                "risks": ["월세가 적정선보다 높음"],
                "actions": ["월세 40만원 인하 협상"],
                "warnings": ["매출은 추정치입니다."],
            },
        )
    )

    assert "조건부 계약입니다." in text
    assert "판정: 조건부 추천" in text
    assert "추천도: 72점" in text
    assert "적정 월세: 220~260만원" in text
    assert "월세 차이: 40만원" in text
    assert "강점: 배후 수요가 충분함" in text
    assert "위험: 월세가 적정선보다 높음" in text
    assert "다음 행동: 월세 40만원 인하 협상" in text
    assert "매출은 추정치입니다." in text


def test_split_message_never_exceeds_telegram_limit_and_loses_no_text():
    text = ("가" * 2500) + "\n\n" + ("나" * 2500)

    chunks = bot.split_message(text, limit=4096)

    assert all(len(chunk) <= 4096 for chunk in chunks)
    assert "".join(chunks) == text


def test_handle_text_runs_real_agent_callback_and_preserves_conversation():
    calls = []

    async def ask(request):
        calls.append(request)
        return _response(query=request["query"])

    state = bot.ChatState()
    first = asyncio.run(bot.handle_text("신촌 카페 어때?", state, ask))
    second = asyncio.run(bot.handle_text("월세 300이면?", state, ask))

    assert "63.3점" in first
    assert "63.3점" in second
    assert calls[0]["history"] == []
    assert calls[1]["history"][-1]["query"] == "신촌 카페 어때?"


def test_new_command_clears_conversation_without_calling_agent():
    called = False

    async def ask(_request):
        nonlocal called
        called = True
        return _response()

    state = bot.ChatState(history=[{"query": "old"}], context={"tool": "old"})
    text = asyncio.run(bot.handle_text("/new", state, ask))

    assert called is False
    assert state.history == []
    assert state.context is None
    assert "새 대화" in text


def test_start_if_configured_only_starts_daemon_when_service_token_exists(monkeypatch):
    started = []

    async def ask(_request):
        return _response()

    monkeypatch.delenv("TELEGRAM_AGENT_BOT_TOKEN", raising=False)
    assert bot.start_if_configured(ask, thread_factory=lambda **kw: started.append(kw)) is False
    assert started == []

    class FakeThread:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def start(self):
            started.append("started")

    monkeypatch.setenv("TELEGRAM_AGENT_BOT_TOKEN", "configured")
    assert bot.start_if_configured(ask, thread_factory=FakeThread) is True
    assert started[0]["daemon"] is True
    assert started[0]["target"] is bot.run_bot
    assert started[-1] == "started"
