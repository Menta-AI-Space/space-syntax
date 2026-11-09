"""LLM 서술 — 정적 서술을 사람 말투로 다시 쓰는 **선택적** 계층.

세 가지를 구조로 못 박는다:

1. **LLM에 raw DB 데이터를 넣지 않는다.** 넘기는 것은 Structured Analysis를
   서술에 필요한 만큼만 줄인 dict다 — PNU도, 좌표도, 임계값도, 감사 정보도
   가지 않는다. 프롬프트에 있는 것만 LLM이 말할 수 있으므로, 넣지 않는 것이
   유출을 막는 유일하게 확실한 방법이다.
2. **판정 값을 바꾸지 못한다.** 이 모듈은 `Narrative`만 만들어 돌려준다.
   `verdict`·`current_valuation`을 건드리는 경로가 아예 없어서, LLM을 켜고
   끄는 것으로 저평가 판정이 달라질 수 없다.
3. **실패하면 정적 서술이다.** 키가 없거나 타임아웃이거나 형식이 깨지거나
   금지어가 섞이면 그냥 `build_narrative`의 결과를 돌려준다. 화면은 어느 쪽인지
   `Narrative.source`로 알 수 있다.

⚠️ **출력을 검사하고 버린다.** 지시만으로는 부족하다 — 이 저장소의 다른 서술
레이어에서도 LLM이 판정어를 다른 말로 바꿔 부르는 일이 있었다. 그래서 내부
용어·"감정평가"·"시세" 같은 말이 섞이면 그 응답을 채택하지 않는다.
"""

from __future__ import annotations

import json
import logging

from app.models.land_analysis import Narrative, StructuredAnalysis
from app.services.land_narrative import build_narrative
from app.services.llm_client import complete_json

logger = logging.getLogger(__name__)

# 서술은 카드 하나에 들어가야 한다. 길어지면 사용자가 안 읽고, 길이를 안 정하면
# LLM이 열 문단을 쓴다.
MAX_PARAGRAPHS = 3
MAX_CHARS_PER_PARAGRAPH = 220
MAX_HEADLINE_CHARS = 60

# 이 말이 섞이면 응답을 버린다.
#  - 내부 용어: 사용자에게 뜻이 없고, 한 번 노출되면 프런트가 그걸 근거로 문구를
#    만들기 시작해 모델을 못 바꾼다.
#  - "감정평가"·"시세"·"실거래": 공시지가를 다른 값으로 읽히게 만든다. 우리가
#    가진 것은 공시지가뿐이다.
#  - "고평가": 낙인 표현. 계약이 above_peers로 중립화해 둔 것을 되돌린다.
BANNED_TERMS = (
    "SHAP",
    "shap",
    "Integration",
    "integration",
    "잔차",
    "백분위",
    "헤도닉",
    "감정평가",
    "시세",
    "실거래",
    "고평가",
)

SYSTEM = (
    "당신은 한국의 토지 컨설턴트입니다. 이미 분석이 끝난 필지 판정을 "
    "처음 땅을 보는 사람도 이해할 수 있는 쉬운 한국어로 다시 써 주세요.\n"
    "규칙:\n"
    "1. 주어진 JSON에 있는 값만 인용하세요. 새 숫자를 계산하거나 추정하지 "
    "마세요. 평균·차이·비율을 직접 계산하지 마세요.\n"
    "2. 판정(verdict)은 이미 정해져 있습니다. 뒤집거나 다른 말로 바꿔 부르지 "
    "마세요.\n"
    "3. **'값이 낮다'와 '저평가다'를 구분하세요.** 단점(맹지·형상·경사)이 그 "
    "값을 설명하면 저평가가 아니라 '이유가 있어서 낮은 것'입니다. "
    "is_undervalued가 false면 저평가라고 쓰지 마세요.\n"
    "4. 금액은 **개별공시지가**입니다. 시세·감정평가액·실거래가라고 쓰지 마세요. "
    "예상값은 반드시 범위로 쓰고, 확정된 평가액처럼 쓰지 마세요.\n"
    "5. 값이 높은 쪽을 '고평가'라고 부르지 마세요. 우리가 보지 않는 요인으로 "
    "높을 수 있습니다.\n"
    "6. 전문용어 금지: SHAP·Integration·잔차·백분위·헤도닉 같은 말을 쓰지 말고 "
    "'큰길에 닿음'·'지나다니는 길목'처럼 일상어를 쓰세요.\n"
    "7. strength가 weak이면 단정하지 말고 '~일 수 있습니다'처럼 쓰세요.\n"
    "8. 반드시 아래 JSON 형식으로만 답하세요:\n"
    '{"headline": "...", "paragraphs": ["...", "..."]}\n'
    f"headline: {MAX_HEADLINE_CHARS}자 이내 한 줄. "
    f"paragraphs: {MAX_PARAGRAPHS}개 이내, 각 {MAX_CHARS_PER_PARAGRAPH}자 이내."
)


def narrative_input(analysis: StructuredAnalysis) -> dict:
    """LLM에 넘길 것만 남긴 dict. **여기 없는 것은 LLM이 말할 수 없다.**

    빼는 것: `subject.pnu`·좌표(개인정보성 식별자이고 서술에 쓸 데가 없다),
    `audit`(임계값·모델 판번호는 내부 정보다), `peer_group.level`(내부 코드),
    `confidence.score`(숫자를 주면 "신뢰도 0.62"라고 적는다).
    """
    valuation = analysis.current_valuation
    official = analysis.official_price
    payload: dict = {
        "주소": analysis.subject.address,
        "판정": {
            "결론": analysis.verdict.headline,
            "표현강도": analysis.verdict.strength,
            "신뢰도": analysis.verdict.confidence.level,
            "신뢰도를_깎은_이유": analysis.verdict.confidence.reasons,
        },
        "비교군": analysis.peer_group.description,
        "속성": [f"{a.label}: {a.value}" for a in analysis.attributes],
        "요인": [
            {
                "설명": f.label,
                "방향": "값을 올림" if f.direction == "positive" else "값을 내림",
                "크기": f.magnitude_label,
                "싼_이유로_설명되는_단점인가": f.explains_discount,
            }
            for f in analysis.factors
        ],
        "정적_서술": analysis.narrative.paragraphs if analysis.narrative else [],
    }
    if official is not None:
        # 금액은 추정 모형과 무관하게 있는 사실이다 — 판정을 보류해도 넘긴다.
        payload["가격"] = {
            "개별공시지가_원per㎡": official.per_sqm,
            "기준연도": official.base_year,
        }
    if valuation is not None:
        payload.setdefault("가격", {})
        payload["가격"].update(
            {
                "미루어_본_범위_원per㎡": [
                    valuation.expected_official_price.low,
                    valuation.expected_official_price.high,
                ],
                "값이_낮은가": valuation.is_cheap,
                "저평가인가": valuation.is_undervalued,
                "설명되는_할인분_pct": valuation.discount.explained_pct,
                "설명되지_않는_할인분_pct": valuation.discount.unexplained_pct,
            }
        )
    if analysis.growth_outlook is not None:
        payload["과거_초과성장"] = {
            "기간_년": analysis.growth_outlook.horizon_years,
            "peer_대비_초과상승_pct": analysis.growth_outlook.excess_growth_pct,
        }
    if analysis.price_forecast is not None:
        payload["전망"] = [
            {
                "년": h.years,
                "범위_원per㎡": [
                    h.expected_official_price.low,
                    h.expected_official_price.high,
                ],
            }
            for h in analysis.price_forecast.horizons
        ]
    return payload


def _clean(raw: dict) -> Narrative | None:
    """형식과 금지어를 검사한다. 하나라도 어기면 채택하지 않는다."""
    headline = str(raw.get("headline") or "").strip()
    paragraphs = raw.get("paragraphs")
    if not headline or not isinstance(paragraphs, list):
        return None
    texts = [str(p).strip() for p in paragraphs if str(p).strip()]
    if not texts:
        return None
    texts = texts[:MAX_PARAGRAPHS]
    if len(headline) > MAX_HEADLINE_CHARS:
        return None
    if any(len(t) > MAX_CHARS_PER_PARAGRAPH for t in texts):
        return None
    blob = headline + " " + " ".join(texts)
    if any(term in blob for term in BANNED_TERMS):
        return None
    return Narrative(source="llm", headline=headline, paragraphs=texts)


async def build_llm_narrative(
    analysis: StructuredAnalysis, *, timeout_s: float = 12.0
) -> Narrative:
    """LLM 서술 한 번. 실패하면 정적 서술을 그대로 돌려준다.

    호출은 **한 번뿐이다** — 재시도하지 않는다. 재시도는 5초 룰을 두 배로 쓰면서
    같은 형식 오류를 반복하는 쪽이 흔하고, 폴백이 이미 완성된 문장을 갖고 있다.
    """
    static = analysis.narrative or build_narrative(analysis)
    raw = await complete_json(
        system=SYSTEM,
        user=json.dumps(narrative_input(analysis), ensure_ascii=False),
        max_tokens=700,
        timeout_s=timeout_s,
    )
    if raw is None:
        return static
    cleaned = _clean(raw)
    if cleaned is None:
        logger.info("LLM 서술을 채택하지 않았다(형식·금지어) — 정적 서술로 폴백")
        return static
    return cleaned
