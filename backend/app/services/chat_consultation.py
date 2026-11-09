"""Conversational site consultation (LLM with computed context)."""

from __future__ import annotations

import re

from fastapi import HTTPException

from app.config import settings
from app.services.llm_client import narrative_model_name
from app.models.schemas import ChatRequest
from app.routers.goals import LeaseVerdictRequest, lease_verdict
from app.services.sbiz_upjong import BUSINESS_TYPE_PRESETS


CHAT_SYSTEM = (
    "당신은 한국 상업 입지 상담 도우미입니다. 사용자 질문에 쉬운 한국어로 답하세요. "
    "제공된 위치·업종·지표 컨텍스트만 사용하고, 없는 숫자는 만들지 마세요. "
    "이 대화는 지금 선택된 지점 한 곳만 다룹니다 — 여러 후보지를 찾아 비교해 "
    "달라는 질문에는 후보 탐색 기능이 없다고 밝히고 이 지점 기준으로만 답하세요. "
    "투자 확정 조언은 하지 말고, 후보지 비교·탐색 관점으로 설명하세요. 3~6문장."
)

# "월세 300 이하 후보만 추려줘"류 질문이 실제로 여러 후보를 찾아 필터링하는 것
# 처럼 들리지만, 이 대화창은 지금 선택된 지점 하나만 안다 — 임대료가 언급되면
# LLM이 자연스러운 문장으로 지어내게 두는 대신 lease_verdict를 그대로 불러
# 이 지점 하나에 대한 실제 판정(임대료 부담률·적정 월세 구간·손익분기)으로 답한다.
_RENT_MENTION_RE = re.compile(r"월세|임대료|차임")
_MONEY_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|천|백|만)?\s*원?")
_MONEY_UNITS_MAN: dict[str, float] = {
    "억": 10_000.0,
    "천만": 1_000.0,
    "백만": 100.0,
    "천": 1_000.0,
    "백": 100.0,
    "만": 1.0,
}
# 라벨에서 이 거리 안의 숫자만 임대료로 본다 — 창을 너무 넓히면 문장 뒤쪽의
# 관계없는 숫자("5분 거리")까지 임대료로 오인한다.
_RENT_WINDOW = 20


def _extract_rent_man(text: str) -> float | None:
    """'월세 300 이하'류 문장에서 만원 단위 임대료를 뽑는다. 못 찾으면 None."""
    label = _RENT_MENTION_RE.search(text or "")
    if not label:
        return None
    m = _MONEY_RE.search(text, label.end(), label.end() + _RENT_WINDOW)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if value <= 0:
        return None
    return value * _MONEY_UNITS_MAN.get(m.group(2) or "", 1.0)


async def _rent_band_reply(request: ChatRequest, rent_man: float) -> tuple[str, str]:
    """임대료가 언급되면 lease_verdict 엔진으로 이 지점 하나를 실제로 판정한다.

    "후보만 추려줘"처럼 여러 곳을 찾아 달라는 말이어도, 이 대화창은 도로망을
    다시 불러오는 area_search급 탐색을 하지 않는다 — 없는 후보 목록을 지어내는
    대신 지금 선택된 지점 하나를 정확히 계산해 그 사실을 함께 밝힌다.
    """
    if request.lat is None or request.lng is None:
        return (
            "임대료 조건을 계산하려면 먼저 지도에서 지점을 선택해 주세요. "
            "이 대화는 후보를 여러 곳 찾아 비교하지 않고, 선택된 지점 하나만 "
            "계산합니다.",
            "rule",
        )
    preset = BUSINESS_TYPE_PRESETS.get(request.business_type) or {}
    try:
        result = await lease_verdict(
            LeaseVerdictRequest(
                lat=request.lat,
                lng=request.lng,
                inds_lcls_cd=preset.get("inds_lcls_cd"),
                inds_mcls_cd=preset.get("inds_mcls_cd"),
                inds_scls_cd=preset.get("inds_scls_cd"),
                monthly_rent_man=rent_man,
            )
        )
    except HTTPException as exc:
        return f"월세 {rent_man:,.0f}만원 기준으로 계산하지 못했어요: {exc.detail}", "rule"

    lines = [
        f"이 대화는 후보를 여러 곳 찾아 비교하지 않고, 지금 선택된 지점 하나만 "
        f"월세 {rent_man:,.0f}만원 기준으로 계산합니다.",
        f"판정: {result.verdict_label}",
    ]
    if result.rent_burden_pct is not None:
        lines.append(f"예상 매출 대비 임대료 부담률 {result.rent_burden_pct:.1f}%")
    if (
        result.appropriate_rent_low_man is not None
        and result.appropriate_rent_high_man is not None
    ):
        lines.append(
            "적정 월세 구간 "
            f"{result.appropriate_rent_low_man:,.0f}~{result.appropriate_rent_high_man:,.0f}만원"
        )
    if result.break_even_monthly_man is not None:
        lines.append(f"손익분기 월매출 {result.break_even_monthly_man:,.0f}만원")
    return "\n".join(lines), "rule"


def rule_based_chat(request: ChatRequest, context: str | None) -> str:
    last = request.messages[-1].content if request.messages else ""
    if context:
        return (
            f"'{last}'에 대해 지금 계산된 값으로 답할게요.\n{context}\n"
            "※ 더 자세한 설명은 OpenAI API 키가 설정되면 제공됩니다."
        )
    return (
        "입지 상담 기능은 OpenAI API 키가 설정되면 더 풍부한 답변을 제공합니다. "
        f"질문: {last}"
    )


async def chat_consultation(
    request: ChatRequest,
    *,
    context: str | None = None,
) -> tuple[str, str]:
    last = request.messages[-1].content if request.messages else ""
    rent_man = _extract_rent_man(last)
    if rent_man is not None:
        return await _rent_band_reply(request, rent_man)

    if not settings.openai_api_key.strip():
        return rule_based_chat(request, context), "rule"

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        msgs = [{"role": "system", "content": CHAT_SYSTEM}]
        if context:
            msgs.append({"role": "system", "content": f"위치 컨텍스트:\n{context}"})
        for m in request.messages[-8:]:
            if m.role in ("user", "assistant"):
                msgs.append({"role": m.role, "content": m.content})
        response = await client.chat.completions.create(
            model=narrative_model_name(),  # 서사 전용 모델 (파싱과 분리)
            max_completion_tokens=800,
            messages=msgs,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return rule_based_chat(request, context), "rule"
        return text, "llm"
    except Exception:
        return rule_based_chat(request, context), "rule"
