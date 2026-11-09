"""Natural-language explanation of a site score.

The LLM never computes — the backend passes finished numbers and the model
only translates them into business Korean. Without an OPENAI_API_KEY the
rule-based template keeps the feature working.
"""

from __future__ import annotations

from app.config import settings
from app.services.llm_client import narrative_model_name
from app.services.site_score import SiteScoreResult

SYSTEM_PROMPT = (
    "당신은 한국 상업 입지 분석 전문가입니다. 백엔드가 계산한 데이터를 받아 "
    "일반 임차인·창업자가 바로 판단할 수 있는 한국어 해석을 씁니다.\n\n"
    "규칙:\n"
    "- 본문에 0–100 점수를 나열하지 마세요. 상위%·거리·점포 수 등 꼭 필요한 숫자만 최소로 쓰세요.\n"
    "- 아래 4개 섹션을 **굵은 제목**으로 구분해 작성하세요.\n"
    "  **한 줄 결론** — 한 문장\n"
    "  **왜 좋은지** — 2~3문장\n"
    "  **주의할 점** — 1~2문장\n"
    "  **추천 전략** — 1~2문장 (확정적 투자 조언 금지)\n"
    "- 도로 구조는 '상위 X%'로만 언급하세요(X가 낮을수록 좋음). "
    "접근성·통합도·Integration 같은 전문 용어 대신 '찾아오기 쉬움', '지나가는 길', "
    "'눈에 띄는 위치' 같은 일상어를 쓰세요.\n"
    "- 경쟁은 '경쟁 강도: 매우 높음/높음/…' 형태로 쓰고, 동일업종 점포 수를 함께 언급하세요. "
    "경쟁 밀도 점수(1.8점 등)는 쓰지 마세요.\n"
    "- 인구·종사자·가구 같은 배후 수요는 절대수만 던지지 마세요. '28,503명'만으로는 "
    "많은지 적은지 알 수 없습니다. 자료에 '서울 인구 상위 X%' 같은 값이 있으면 반드시 "
    "함께 적고 '많은 편/적은 편'까지 판정하세요. 상위 % 자료가 없으면 절대수 대신 "
    "다른 근거를 쓰거나 그 대목을 생략하세요.\n"
    "- 존댓말, 과장 금지."
)


def _fmt_subscores_for_llm(result: SiteScoreResult) -> str:
    lines = []
    for ss in result.subscores:
        if ss.key == "competition":
            if ss.display and ss.display != "—":
                lines.append(f"- 경쟁 강도: {ss.display} ({ss.detail})")
            continue
        if ss.rank_top_pct is not None:
            name = ss.summary_label or ss.label
            lines.append(f"- {name}: 상위 {ss.rank_top_pct}%")
        elif ss.score is not None:
            name = ss.summary_label or ss.label
            lines.append(f"- {name}: {ss.display or ss.detail}")
        else:
            lines.append(f"- {ss.summary_label or ss.label}: 데이터 없음")
    return "\n".join(lines)


def rule_based_explanation(
    result: SiteScoreResult,
    demand_facts: str | None = None,
    spatial_facts: str | None = None,
) -> str:
    """Template fallback when no LLM is configured."""
    headline = result.verdict_one_liner or "입지 조건을 검토해 보세요."

    strengths: list[str] = []
    cautions: list[str] = []
    for ss in result.subscores:
        if ss.score is None:
            continue
        if ss.key == "competition":
            if ss.display in ("높음", "매우 높음"):
                cautions.append(f"경쟁 강도가 {ss.display}입니다 ({ss.detail}).")
            continue
        if ss.rank_top_pct is not None and ss.rank_top_pct <= 25:
            strengths.append(
                f"{ss.summary_label or ss.label}이 분석 반경 상위 {ss.rank_top_pct}%입니다."
            )
        elif ss.rank_top_pct is not None and ss.rank_top_pct >= 60:
            cautions.append(
                f"{ss.summary_label or ss.label}이 분석 반경 상위 {ss.rank_top_pct}%로 다소 약합니다."
            )
        elif ss.key in ("transit", "demand") and ss.score >= 70:
            strengths.append(f"{ss.summary_label or ss.label}이 {ss.display}입니다.")
        elif ss.key in ("transit", "demand") and ss.score < 55:
            cautions.append(f"{ss.summary_label or ss.label}이 {ss.display}입니다.")

    why = " ".join(strengths[:3]) or "유입·배후 수요 지표를 함께 확인해 보세요."
    caution = " ".join(cautions[:2]) or "세부 지표에서 약한 항목이 없는지 확인하세요."
    strategy = (
        f"{result.business_label} 관점에서 차별화 포인트(메뉴·콘셉트·운영 방식)를 "
        "분명히 하는 것이 유리해 보입니다."
        if any("경쟁" in c for c in cautions)
        else "현재 조건을 바탕으로 후보지 간 비교에 활용해 보세요."
    )

    parts = [
        f"**한 줄 결론**\n{headline}",
        f"**왜 좋은지**\n{why}",
        f"**주의할 점**\n{caution}",
        f"**추천 전략**\n{strategy}",
    ]
    if demand_facts:
        first = demand_facts.split("\n")[1] if "\n" in demand_facts else demand_facts
        parts[1] = f"**왜 좋은지**\n{why} {first}"
    return "\n\n".join(parts)


async def explain_site_score(
    result: SiteScoreResult,
    demand_facts: str | None = None,
    spatial_facts: str | None = None,
) -> tuple[str, str]:
    """Return (explanation, source) where source is 'llm' or 'rule'."""
    if not settings.openai_api_key.strip():
        return rule_based_explanation(result, demand_facts, spatial_facts), "rule"

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        demand_block = f"\n배후 수요:\n{demand_facts}\n" if demand_facts else ""
        spatial_block = f"\n{spatial_facts}\n" if spatial_facts else ""
        user_prompt = (
            f"업종: {result.business_label}\n"
            f"한 줄 결론(참고): {result.verdict_one_liner}\n"
            f"지표 요약:\n{_fmt_subscores_for_llm(result)}\n"
            f"{spatial_block}"
            f"{demand_block}\n"
            "위 데이터로 4섹션(한 줄 결론·왜 좋은지·주의할 점·추천 전략) 해석을 작성하세요. "
            "본문에 0–100 점수를 나열하지 마세요."
        )
        response = await client.chat.completions.create(
            model=narrative_model_name(),  # 서사 전용 모델 (파싱과 분리)
            max_completion_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            return rule_based_explanation(result, demand_facts, spatial_facts), "rule"
        text = (choice.message.content or "").strip()
        if not text:
            return rule_based_explanation(result, demand_facts, spatial_facts), "rule"
        return text, "llm"
    except Exception:
        return rule_based_explanation(result, demand_facts, spatial_facts), "rule"
