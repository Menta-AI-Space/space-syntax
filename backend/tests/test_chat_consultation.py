"""ChatSection의 '임대료 밴드 필터' 칩이 실제로 lease_verdict급 판정을 내는지 검증.

이 대화창은 여러 후보지를 찾아 비교하지 않는다 — 임대료가 언급되면 지금
선택된 지점 하나를 lease_verdict 엔진으로 실제 계산해야 하고, LLM이 숫자를
지어내서는 안 된다. 아래 테스트는 화면에 보일 값이 실제로 lease_verdict가
돌려준 값과 일치하는지(값 자체를 검증)를 확인한다 — 문구만 맞고 값이 틀린
상태를 잡아내려는 것이라 응답 문자열 존재 여부가 아니라 숫자를 assert한다.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.models.schemas import ChatMessageIn, ChatRequest
from app.routers.goals import LeaseVerdictResponse, NegotiationOut
from app.services import chat_consultation as cc


def _request(
    text: str,
    *,
    lat: float | None = 37.55,
    lng: float | None = 126.9,
    business_type: str = "cafe",
) -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessageIn(role="user", content=text)],
        lat=lat,
        lng=lng,
        business_type=business_type,
    )


def _fake_lease_response(**overrides) -> LeaseVerdictResponse:
    base = dict(
        verdict="marginal",
        verdict_label="조건부 추천",
        recommend_score=61.5,
        rent_burden_pct=18.4,
        appropriate_rent_low_man=220.0,
        appropriate_rent_high_man=280.0,
        break_even_monthly_man=1450.0,
        negotiation=NegotiationOut(),
    )
    base.update(overrides)
    return LeaseVerdictResponse(**base)


# ─────────────────────── _extract_rent_man ───────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("월세 300 이하 후보만 추려줘", 300.0),
        ("임대료 1200만원인데 계약해도 될까", 1200.0),
        ("월세 3천만원짜리인데 괜찮을까", 3000.0),
        ("차임 250 정도면 어때", 250.0),
    ],
)
def test_extract_rent_man_reads_the_labelled_amount(text, expected):
    assert cc._extract_rent_man(text) == expected


def test_extract_rent_man_returns_none_without_a_number():
    assert cc._extract_rent_man("월세가 저렴한 편이야?") is None


def test_extract_rent_man_ignores_numbers_far_from_the_label():
    # "월세" 뒤 20자를 넘어가는 숫자는 다른 맥락(유동인구)의 값이라 임대료로
    # 읽으면 안 된다.
    text = "월세 이야기 말고, 이 근처에 하루에 다니는 사람이 3000명쯤 되는지 알려줘"
    assert cc._extract_rent_man(text) is None


# ─────────────────────── _rent_band_reply ───────────────────────


def test_rent_band_reply_asks_for_a_location_when_none_is_selected(monkeypatch):
    called = False

    async def _boom(*_a, **_k):
        nonlocal called
        called = True
        raise AssertionError("lat/lng 없이 lease_verdict를 부르면 안 된다")

    monkeypatch.setattr(cc, "lease_verdict", _boom)
    request = _request("월세 300 이하 후보만 추려줘", lat=None, lng=None)

    reply, source = asyncio.run(cc._rent_band_reply(request, 300.0))

    assert not called
    assert source == "rule"
    assert "지점을 선택" in reply


def test_rent_band_reply_uses_the_real_lease_verdict_numbers(monkeypatch):
    captured: dict = {}

    async def _fake_lease_verdict(req):
        captured["lat"] = req.lat
        captured["lng"] = req.lng
        captured["monthly_rent_man"] = req.monthly_rent_man
        captured["inds_mcls_cd"] = req.inds_mcls_cd
        return _fake_lease_response()

    monkeypatch.setattr(cc, "lease_verdict", _fake_lease_verdict)
    request = _request(
        "월세 300 이하 후보만 추려줘", lat=37.5665, lng=126.978, business_type="cafe"
    )

    reply, source = asyncio.run(cc._rent_band_reply(request, 300.0))

    # lease_verdict가 받은 값이 실제 요청에서 뽑힌 값과 같아야 한다 — 지어낸
    # 숫자가 아니라 사용자가 물은 지점·업종·임대료 그대로 넘어가야 한다.
    assert captured["lat"] == 37.5665
    assert captured["lng"] == 126.978
    assert captured["monthly_rent_man"] == 300.0
    assert captured["inds_mcls_cd"] == "I212"  # cafe 프리셋 코드

    # 화면에 보일 문구는 fake lease_verdict가 돌려준 값 그대로여야 한다.
    assert source == "rule"
    assert "조건부 추천" in reply
    assert "18.4%" in reply
    assert "220~280만원" in reply
    assert "1,450만원" in reply
    # 여러 후보를 찾아 필터링한 게 아니라 이 지점 하나만 봤다는 사실을 밝힌다.
    assert "후보를 여러 곳 찾아 비교하지 않" in reply


def test_rent_band_reply_does_not_fabricate_numbers_when_the_engine_fails(monkeypatch):
    async def _fail(_req):
        raise HTTPException(status_code=404, detail="도로망을 찾지 못했습니다")

    monkeypatch.setattr(cc, "lease_verdict", _fail)
    request = _request("월세 300 이하 후보만 추려줘")

    reply, source = asyncio.run(cc._rent_band_reply(request, 300.0))

    assert source == "rule"
    assert "도로망을 찾지 못했습니다" in reply
    # 실패했을 때 조건부 추천 같은 판정 문구를 지어내면 안 된다.
    assert "판정" not in reply


# ─────────────────────── chat_consultation dispatch ───────────────────────


def test_chat_consultation_routes_rent_mentions_to_the_real_engine_before_the_llm(
    monkeypatch,
):
    async def _fake_lease_verdict(req):
        return _fake_lease_response(verdict_label="추천")

    llm_called = False

    class _ExplodingOpenAI:
        def __init__(self, *a, **k):
            nonlocal llm_called
            llm_called = True
            raise AssertionError("임대료 질문은 LLM으로 넘어가면 안 된다")

    monkeypatch.setattr(cc, "lease_verdict", _fake_lease_verdict)
    monkeypatch.setattr(cc.settings, "openai_api_key", "sk-test")
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _ExplodingOpenAI)

    request = _request("월세 300 이하 후보만 추려줘")
    reply, source = asyncio.run(cc.chat_consultation(request, context="업종: 카페"))

    assert not llm_called
    assert source == "rule"
    assert "추천" in reply


def test_chat_consultation_falls_back_to_rule_based_chat_without_a_rent_mention(
    monkeypatch,
):
    monkeypatch.setattr(cc.settings, "openai_api_key", "")
    request = _request("이 위치 상권을 요약해줘")

    reply, source = asyncio.run(
        cc.chat_consultation(
            request, context="업종: 카페\n5분 도보권 점포 12곳\n잠식 위험 34/100 (낮음)"
        )
    )

    assert source == "rule"
    # 규칙 폴백이 실제 계산된 컨텍스트를 그대로 보여줘야 한다 — 잘라내거나
    # 생략하면 안 된다.
    assert "5분 도보권 점포 12곳" in reply
    assert "잠식 위험 34/100 (낮음)" in reply
