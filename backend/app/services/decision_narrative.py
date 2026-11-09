"""이미 결정된 순위를 한국어 산문으로 옮기는 서술 레이어.

**LLM은 숫자를 만들지 않는다.** 순위·점수·판정·적정 월세는 decision.py가
전부 계산해 놓고, 여기서는 그 값을 그대로 인용해 문장으로만 바꾼다.
키가 없거나 호출이 실패하면 동일한 결론을 규칙 기반 템플릿으로 만든다.
"""

from __future__ import annotations

import json

from app.config import settings
from app.services.llm_client import narrative_model_name
from app.services.decision import VERDICT_LABELS, RankedCandidate, josa

NARRATIVE_SYSTEM = (
    "당신은 한국의 상가 입지 컨설턴트입니다. 이미 분석이 끝난 후보지 비교 결과를 "
    "창업 초보자도 이해할 수 있는 쉬운 한국어로 설명하세요.\n"
    "규칙:\n"
    "1. 주어진 JSON에 있는 숫자만 그대로 인용하세요. 새로운 숫자를 계산하거나 "
    "추정하지 마세요. 평균·차이·비율을 직접 계산하지 마세요.\n"
    "2. 순위와 판정은 이미 정해져 있습니다. 뒤집거나 다르게 해석하지 마세요. "
    "1위의 '판정' 값(예: 비추천)을 그대로 쓰고, 다른 판정어로 바꿔 부르지 마세요 "
    "— '비추천'을 '보류'라고 쓰면 안 됩니다.\n"
    "3. 전문용어 금지: '통합도', 'Integration', 'Choice', '공간구문론', '축선도' 등을 "
    "쓰지 말고 '찾아오기 쉬움', '지나가는 길', '유동인구' 같은 일상어를 쓰세요.\n"
    "4. 금액은 '만원' 단위로 그대로 씁니다.\n"
    "5. JSON의 키 이름(예: 적정월세상한_만원)을 문장에 그대로 쓰지 마세요. "
    "'감당 가능한 월세'처럼 사람이 쓰는 말로 바꿔 쓰세요.\n"
    "6. '조건부_결론'이 주어지면 그 문장을 그대로 옮겨 적으세요. 임계 금액을 "
    "직접 고르지 마세요.\n"
    "7. 반드시 아래 JSON 형식으로만 답하세요:\n"
    '{"headline": "...", "comparison_note": "..."}\n'
    "headline: 2~3문장. 1위가 어디고 왜인지, 어떤 조건에서 추천인지.\n"
    "comparison_note: 2~4문장. 1위와 2위의 장단점 맞교환을 설명하고, "
    "'조건부_결론' 문장으로 끝내세요."
)


def rent_cap_phrase(max_rent_man: float | None) -> str | None:
    """적정 월세 상한을 **문장으로** 넘긴다.

    숫자 0.0을 그대로 주면 LLM이 "월세 상한이 0.0만원이라", 심하면
    "월세 0만원 이하면 망원동, 아니면 강남역"처럼 뜻이 뒤집힌 조건문을 만든다.
    상한 0은 금액이 아니라 '이 자리는 월세를 낼 수 없다'는 결론이므로 그렇게 적는다.
    """
    if max_rent_man is None:
        return None
    if max_rent_man <= 0:
        return "월세를 낼 여력 없음 (예상 매출이 인건비·고정비도 못 덮음)"
    return f"{max_rent_man:,.0f}만원까지"


def conditional_conclusion(
    top: RankedCandidate, second: RankedCandidate
) -> str:
    """'어떤 조건이면 1위, 아니면 2위'를 **파이썬이** 정한다.

    이 결론의 임계값은 1위의 적정 월세 상한이다. 그런데 LLM에게 형식만 주고
    맡겼더니 2위의 상한을 골라 "월세 221만원 이하면 신촌, 아니면 신촌B"처럼
    앞뒤가 바뀐 문장을 냈고, 상한이 0인 후보에서는 "월세 0만원 이하면 망원동"이
    나왔다. 임계값을 고르는 건 숫자를 만드는 일이므로 여기서 확정한다.
    """
    threshold = top.max_acceptable_rent_man
    top_name = f"'{top.label}'"
    second_obj = f"'{second.label}'{josa(second.label, '을', '를')}"
    if threshold is None:
        return (
            f"{top_name}의 매출 근거가 확보되면 그쪽을, "
            f"아니면 {second_obj} 검토하세요."
        )
    if threshold <= 0:
        return (
            f"{top_name}은(는) 예상 매출로 월세를 낼 여력이 없어 월세를 깎는 것으로는 "
            f"풀리지 않습니다 — 매출이 더 나오는 조건을 못 만들면 {second_obj} 보세요."
        )
    return (
        f"월세를 {threshold:,.0f}만원 이하로 맞출 수 있으면 {top_name}, "
        f"그렇지 않으면 {second_obj} 선택하세요."
    )


def _compact_payload(ranked: list[RankedCandidate], priority: str) -> dict:
    """LLM에 넘길 최소 JSON — 이미 결정된 값만."""
    payload = {
        "우선순위": {
            "revenue": "매출 우선",
            "stability": "안정성 우선",
            "balanced": "균형",
        }.get(priority, "균형"),
        "후보지": [
            {
                "순위": r.rank,
                "이름": r.label,
                "추천점수": r.recommend_score,
                "판정": r.verdict_label,
                "예상월매출_만원": r.expected_monthly_sales_man,
                "감당가능한월세": rent_cap_phrase(r.max_acceptable_rent_man),
                "현재월세_만원": r.current_rent_man,
                "월세초과_만원": r.rent_gap_man,
                "순위를가른요인": [
                    d.get("text") for d in (r.evidence or {}).get("rank_drivers", [])
                ],
                "강점": r.strengths,
                "위험": r.risks,
                "권고": r.actions,
            }
            for r in ranked
        ],
    }
    if len(ranked) >= 2:
        payload["조건부_결론"] = conditional_conclusion(ranked[0], ranked[1])
    return payload


def _contradicts_verdict(text: str, verdict: str) -> bool:
    """서술이 1위의 판정을 **다른 판정어로 바꿔 불렀는지** 검사한다.

    실제로 판정이 '비추천'인데 headline이 "추천보다 보류 쪽에 가깝습니다"라고
    나온 적이 있다. 결론을 바꿔 부르는 서술은 규칙 문장으로 되돌린다.

    맨 '추천'은 세지 않는다. "월세를 낮춰야 추천할 수 있습니다"처럼 조언
    동사로도 쓰이는 흔한 말이라, 이걸 판정 주장으로 보면 판정이 '보류'인
    정상 서술까지 전부 규칙 문장으로 떨어진다(실제로 그렇게 됐다).
    '비추천'·'조건부 추천'은 '추천'을 부분문자열로 가지므로 먼저 치환해 센다.
    """
    correct = VERDICT_LABELS.get(verdict, "")
    if not correct:
        return False

    stripped = text.replace("조건부 추천", "\x00").replace("비추천", "\x01")
    present: set[str] = set()
    if "\x01" in stripped:
        present.add("비추천")
    if "\x00" in stripped:
        present.add("조건부 추천")
    if "보류" in stripped:
        present.add("보류")

    if correct in present:
        return False
    return bool(present - {correct})


def rule_based_narrative(
    ranked: list[RankedCandidate], priority: str
) -> tuple[str, str]:
    """LLM 없이도 같은 결론을 내는 템플릿."""
    if not ranked:
        return "비교할 후보지가 없습니다.", ""

    top = ranked[0]
    parts = [
        f"{len(ranked)}곳 중 '{top.label}'{josa(top.label, '이', '가')} 추천점수 "
        f"{top.recommend_score:.0f}점으로 1순위이며 판정은 '{top.verdict_label}'입니다."
    ]
    if top.expected_monthly_sales_man:
        parts.append(
            f"예상 월매출은 약 {top.expected_monthly_sales_man:,.0f}만원으로 봅니다."
        )
        # decision.py가 순위·판정·적정 월세를 전부 이 숫자에서 계산하는데,
        # scope=="dong"이면 지정 상권이 아니라 행정동 평균으로 메운 값이다.
        # 헤드라인이 이걸 다른 후보와 똑같은 확정치처럼 말하면 안 된다.
        bench = (top.evidence or {}).get("sales_benchmark") or {}
        if bench.get("scope") == "dong":
            parts.append(
                "다만 이 수치는 지정 상권 데이터가 없어 행정동 평균으로 추정한 "
                "값이라 실제 상권 매출과 정밀도가 다를 수 있습니다."
            )
    # `if top.max_acceptable_rent_man`은 상한 0을 falsy로 걸러, **월세를 한 푼도
    # 낼 수 없다는 가장 중요한 사실**만 요약에서 통째로 빠뜨렸다.
    if top.max_acceptable_rent_man is not None:
        if top.max_acceptable_rent_man <= 0:
            parts.append(
                "다만 예상 매출로는 인건비·고정비를 빼고 나면 월세를 낼 여력이 "
                "남지 않아, 월세를 낮추는 것만으로는 조건이 맞지 않습니다."
            )
        elif top.rent_gap_man is not None and top.rent_gap_man > 0:
            parts.append(
                f"다만 지금 월세 {top.current_rent_man:,.0f}만원은 감당 가능한 상한 "
                f"{top.max_acceptable_rent_man:,.0f}만원보다 {top.rent_gap_man:,.0f}만원 비싸서, "
                f"월세를 낮춰야 추천할 수 있습니다."
            )
        else:
            parts.append(
                f"월세는 {top.max_acceptable_rent_man:,.0f}만원까지 감당 가능한 조건입니다."
            )
    headline = " ".join(parts)

    if len(ranked) < 2:
        return headline, ""

    second = ranked[1]
    note_parts = [
        f"'{top.label}'의 강점은 {top.strengths[0]}이고, "
        f"'{second.label}'{josa(second.label, '은', '는')} {second.strengths[0]}입니다."
    ]
    # 왜 이 순서인지 — 점수 차를 만든 항목을 그대로 인용한다.
    drivers = (top.evidence or {}).get("rank_drivers") or []
    lead = next((d for d in drivers if d.get("direction") == "up"), None)
    if lead and lead.get("text"):
        note_parts.append(f"순위를 가른 건 {lead['text']}입니다.")
    if top.risks:
        note_parts.append(f"{top.label}의 약점은 {top.risks[0]}입니다.")
    note_parts.append(conditional_conclusion(top, second))
    return headline, " ".join(note_parts)


async def narrate_decision(
    ranked: list[RankedCandidate], *, priority: str = "balanced"
) -> tuple[str, str, str]:
    """(headline, comparison_note, source) — source는 "llm" 또는 "rule"."""
    if not ranked:
        return "비교할 후보지가 없습니다.", "", "rule"
    if not settings.openai_api_key.strip():
        headline, note = rule_based_narrative(ranked, priority)
        return headline, note, "rule"

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        payload = json.dumps(
            _compact_payload(ranked, priority), ensure_ascii=False, indent=1
        )
        response = await client.chat.completions.create(
            model=narrative_model_name(),  # 서사 전용 모델 (파싱과 분리)
            max_completion_tokens=900,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": NARRATIVE_SYSTEM},
                {"role": "user", "content": f"분석 결과:\n{payload}"},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        data = json.loads(raw)
        headline = str(data.get("headline") or "").strip()
        note = str(data.get("comparison_note") or "").strip()
        if not headline:
            raise ValueError("empty headline")
        # 서술은 결론을 옮겨 적는 자리이지 바꾸는 자리가 아니다. 판정을 다른
        # 말로 바꿔 부른 서술은 버리고 규칙 문장을 쓴다 (판정 '비추천'인데
        # "추천보다 보류 쪽에 가깝습니다"라고 나온 적이 있다).
        if _contradicts_verdict(headline, ranked[0].verdict):
            raise ValueError("headline contradicts the fixed verdict")
        return headline, note, "llm"
    except Exception:
        headline, note = rule_based_narrative(ranked, priority)
        return headline, note, "rule"
