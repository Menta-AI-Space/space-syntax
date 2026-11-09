"""LLM 서술 테스트 — LLM을 실제로 부르지 않는다.

보는 것은 셋이다: **무엇을 넘기는가**(raw 데이터가 새지 않는가), **무엇을
버리는가**(형식·금지어), **실패하면 어떻게 되는가**(정적 서술로 폴백).
"""

import asyncio

import pytest

from app.models.land_analysis import PeerGroup, Subject
from app.services import land_narrative_llm as llm
from app.services.land_narrative_llm import (
    build_llm_narrative,
    narrative_input,
)
from app.services.land_verdict import (
    FactorInput,
    VerdictInput,
    build_structured_analysis,
)


def _analysis():
    return build_structured_analysis(
        subject=Subject(
            pnu="1156011000100120003",
            base_year=2026,
            address="영등포구 여의도동 12-3",
            lat=37.5202,
            lng=126.9317,
        ),
        peer_group=PeerGroup(
            description="같은 조건 1,586필지", size=1586, level="dong_zone_road"
        ),
        inp=VerdictInput(
            peer_count=1586,
            peer_median_price_per_sqm=12_000_000,
            actual_price_per_sqm=8_300_000,
            expected_price_point=11_500_000,
            expected_price_low=10_400_000,
            expected_price_high=12_600_000,
            factors=[FactorInput("road", "큰길에 바로 닿음", "positive", "큰 가점")],
        ),
        model_version="test-model-9",
    )


# ── 무엇을 넘기는가 ──────────────────────────────────────────────────────

def test_raw_identifiers_and_internals_are_not_sent_to_the_llm():
    payload = repr(narrative_input(_analysis()))
    # PNU·좌표는 서술에 쓸 데가 없고, 임계값·모델 판번호는 내부 정보다.
    assert "1156011000100120003" not in payload
    assert "37.5202" not in payload
    assert "test-model-9" not in payload
    assert "undervalued_min_unexplained_pct" not in payload
    assert "dong_zone_road" not in payload


def test_the_cheap_vs_undervalued_distinction_is_sent_explicitly():
    """LLM이 둘을 구분할 근거를 프롬프트가 갖고 있어야 한다."""
    payload = narrative_input(_analysis())
    assert payload["가격"]["값이_낮은가"] is True
    assert payload["가격"]["저평가인가"] is True
    assert "설명되지_않는_할인분_pct" in payload["가격"]


# ── 무엇을 버리는가 ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw",
    [
        {"headline": "", "paragraphs": ["문장"]},
        {"headline": "제목", "paragraphs": []},
        {"headline": "제목", "paragraphs": "문자열은 목록이 아니다"},
        {"headline": "제" * 200, "paragraphs": ["문장"]},
        {"headline": "제목", "paragraphs": ["가" * 500]},
        # 금지어 — 공시지가를 다른 값으로 읽히게 만든다.
        {"headline": "제목", "paragraphs": ["시세보다 30% 싼 땅입니다."]},
        {"headline": "제목", "paragraphs": ["감정평가액 기준으로 저렴합니다."]},
        # 낙인 표현
        {"headline": "고평가된 땅", "paragraphs": ["비쌉니다."]},
        # 내부 용어
        {"headline": "제목", "paragraphs": ["잔차가 크게 남습니다."]},
    ],
)
def test_bad_llm_output_is_rejected(raw):
    assert llm._clean(raw) is None


def test_good_llm_output_is_accepted():
    narrative = llm._clean(
        {"headline": "큰길에 닿은 값싼 땅", "paragraphs": ["첫 문단.", "둘째 문단."]}
    )
    assert narrative is not None
    assert narrative.source == "llm"
    assert len(narrative.paragraphs) == 2


def test_extra_paragraphs_are_trimmed_not_rejected():
    narrative = llm._clean(
        {"headline": "제목", "paragraphs": ["1", "2", "3", "4", "5"]}
    )
    assert narrative is not None
    assert len(narrative.paragraphs) == llm.MAX_PARAGRAPHS


# ── 실패하면 정적 서술이다 ───────────────────────────────────────────────

def test_llm_unavailable_falls_back_to_static(monkeypatch):
    async def _none(**kwargs):
        return None

    monkeypatch.setattr(llm, "complete_json", _none)
    analysis = _analysis()
    narrative = asyncio.run(build_llm_narrative(analysis))
    assert narrative.source == "static"
    assert narrative.paragraphs == analysis.narrative.paragraphs


def test_rejected_llm_output_falls_back_to_static(monkeypatch):
    async def _banned(**kwargs):
        return {"headline": "시세 대비 저평가", "paragraphs": ["시세보다 쌉니다."]}

    monkeypatch.setattr(llm, "complete_json", _banned)
    narrative = asyncio.run(build_llm_narrative(_analysis()))
    assert narrative.source == "static"


def test_llm_cannot_change_the_verdict(monkeypatch):
    """LLM을 켜고 끄는 것으로 판정 값이 달라지지 않는다."""
    async def _ok(**kwargs):
        return {"headline": "다시 쓴 제목", "paragraphs": ["다시 쓴 문장."]}

    monkeypatch.setattr(llm, "complete_json", _ok)
    analysis = _analysis()
    before = analysis.model_dump(mode="json")
    narrative = asyncio.run(build_llm_narrative(analysis))
    after = analysis.model_dump(mode="json")

    assert narrative.source == "llm"
    # build_llm_narrative는 Narrative만 돌려주고 analysis를 건드리지 않는다.
    assert before == after
    assert after["verdict"]["label"] == "undervalued"
