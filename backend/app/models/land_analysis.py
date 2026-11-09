"""Structured Analysis — 필지 판정 결과의 **유일한** 대외 계약.

여기서 갈라지는 것: 정적 서술 · LLM 서술 · 에이전트 도구 · 결과 화면이 전부
이 JSON 하나만 본다. 그래서 이 모듈에는 두 가지 성질이 있어야 한다.

1. **프런트가 이 JSON만으로 화면 전체를 그릴 수 있다.** 표시할 문자열은 여기서
   완성해 넣는다 — 프런트가 코드값을 한글로 번역하거나 임계값과 비교해 문구를
   고르는 일이 없어야 한다. 규칙이 두 곳에 생기면 반드시 갈라진다.
2. **LLM이 없어도 서비스가 동작한다.** LLM 서술은 이 JSON을 입력으로 받는
   선택적 계층이고, 있으나 없으나 판정 값 자체는 바뀌지 않는다.

⚠️ **ML raw feature를 여기에 담지 않는다.** SHAP 값·Integration·Choice 같은
내부 수치는 서비스 안에서 소비하고, 밖으로는 `Factor.label`처럼 사람이 읽는
형태만 나간다. 한 번 새어 나가면 프런트가 그걸 근거로 문구를 만들기 시작하고,
그때부터 모델을 못 바꾼다.

⚠️ **표기 규칙** — 금액은 전부 **개별공시지가**다. 시세도 감정평가액도 아니다
(`CONTEXT.md`의 구분). 그래서 금액 필드에는 이름에 `official`이 박혀 있고,
`OfficialPrice.disclaimer`가 화면에 반드시 함께 나가도록 필수 필드로 두었다.
예상값은 `PriceRange`(범위)로만 담는다 — 단일 확정값 필드를 두면 화면에서
확정 감정평가액처럼 읽힌다.

방법론 A(미래 가격)·B(초과성장)·C(현재 저평가)의 자리를 **처음부터 셋 다** 둔다.
나중에 붙이려면 프런트까지 다시 손대야 하기 때문이다. 아직 없는 것은 `None`으로
오고, 프런트는 `None`이면 그 섹션을 그리지 않는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"

# 판정 라벨. 다섯 개뿐이고 늘리지 않는다.
#   undervalued        : 단점으로 설명되지 않는 할인분이 크다 — 우리가 파는 판정
#   cheap_with_reason  : 싸긴 싼데 그 이유가 단점으로 설명된다 (맹지·형상·급경사)
#   aligned            : 같은 조건 필지들과 비슷하다
#   above_peers        : 같은 조건 대비 높다. **"고평가"라고 부르지 않는다** —
#                        한강뷰·학군처럼 우리가 안 보는 요인으로 비쌀 수 있다
#   undetermined       : 판정하지 않는다 (비교군 부족·입력 결측)
VerdictLabel = Literal[
    "undervalued",
    "cheap_with_reason",
    "aligned",
    "above_peers",
    "undetermined",
]

# 표현 강도. 신뢰도가 낮으면 강한 말을 쓰지 못하게 하는 장치다 —
# 서술 계층이 이 값만 보고 어조를 정한다.
Strength = Literal["strong", "moderate", "weak"]

ConfidenceLevel = Literal["high", "medium", "low"]

FactorDirection = Literal["positive", "negative"]

# 왜 판정하지 않았는가. "비교군이 얇다"와 "추정 모형이 아직 없다"는 사용자에게
# 전혀 다른 뜻이다 — 앞은 이 필지의 사정이고 뒤는 우리 사정이다. 한 문구로
# 뭉치면 사용자가 데이터가 없는 줄 안다.
UndeterminedReason = Literal["few_peers", "no_estimate", "missing_price"]


class Subject(BaseModel):
    """무엇을 판정했는가."""

    pnu: str
    base_year: int
    address: str = Field(description="사람이 읽는 주소. 예: 영등포구 여의도동 12-3")
    ldong_name: str | None = None
    jibun: str | None = None
    area_sqm: float | None = None
    lat: float | None = None
    lng: float | None = None


class Attribute(BaseModel):
    """화면에 그대로 찍는 속성 한 줄. 코드값이 아니라 표시 문자열이다."""

    key: str = Field(description="프런트 정렬·아이콘용 안정 키. 예: zoning")
    label: str = Field(description="예: 용도지역")
    value: str = Field(description="예: 제3종일반주거지역")
    note: str | None = Field(None, description="예: 82% 필지에만 채워진 항목")


class PriceRange(BaseModel):
    """예상 금액은 **범위**로만 담는다.

    ``point``는 정렬·순위 계산용 대표값이고 화면에 단독으로 찍지 않는다.
    """

    low: float
    high: float
    point: float | None = None
    unit: str = "원/㎡"


class OfficialPrice(BaseModel):
    """실제 개별공시지가. 금액을 보여주되 시세로 읽히지 않게 한다."""

    per_sqm: float
    base_year: int
    label: str = "개별공시지가"
    disclaimer: str = Field(
        default=(
            "개별공시지가는 나지상정 평가액으로 시세·감정평가액과 다릅니다. "
            "시세반영률이 100%가 아니므로 금액 자체를 거래가로 읽지 마세요."
        ),
        description="화면에 반드시 함께 나간다 — 그래서 선택 필드가 아니다.",
    )


class PeerGroup(BaseModel):
    """무엇과 비교했는가. 모든 숫자에 이 크기가 붙어야 뜻이 생긴다."""

    description: str = Field(description="예: 같은 동·같은 용도지역·같은 도로접면 1,586필지")
    size: int
    level: str = Field(description="계층 사다리 중 실제로 쓰인 단계. 예: dong_zone_road")
    criteria: list[str] = Field(default_factory=list, description="예: ['여의도동', '제3종일반주거']")
    median_official_price_per_sqm: float | None = None


class Factor(BaseModel):
    """가격을 끌어올린/끌어내린 요인 하나. 내부 용어를 쓰지 않는다."""

    key: str
    label: str = Field(description="예: 맹지 (도로에 닿지 않음)")
    direction: FactorDirection
    magnitude_label: str = Field(description="예: 큰 감점 / 약한 가점")
    explains_discount: bool = Field(
        False,
        description=(
            "이 요인이 '싼 이유'로 설명되는 단점인가. True인 요인들의 몫이 "
            "explained_discount_pct가 된다."
        ),
    )
    detail: str | None = None


class DiscountBreakdown(BaseModel):
    """할인분을 **설명되는 몫**과 **설명되지 않는 몫**으로 쪼갠다.

    비교군 중앙값을 기준(100%)으로 한 비율이고, 셋이 정확히 더해진다::

        total = explained + unexplained

    - ``explained``   : 단점(맹지·형상·급경사·좁은 도로)이 설명하는 몫.
                        비교군 중앙값 → 모델 예상값 사이의 거리.
    - ``unexplained`` : 남는 몫. 모델 예상값 → 실제 공시지가 사이의 거리.
                        **이것이 저평가의 정의다.**
    """

    total_pct: float
    explained_pct: float
    unexplained_pct: float


class Confidence(BaseModel):
    """임의 숫자가 아니다 — 네 가지 근거에서 나온다.

    비교군 수 · 비교군 유사도 · 모델 불확실성 · 학습분포 내 여부.
    ``reasons``에 무엇이 신뢰도를 깎았는지 사람이 읽는 문장으로 남긴다.
    """

    score: float = Field(ge=0.0, le=1.0)
    level: ConfidenceLevel
    peer_count: int
    peer_similarity: float | None = Field(None, ge=0.0, le=1.0)
    model_uncertainty_pct: float | None = Field(
        None, description="예측구간 폭 / 예측값. 클수록 모델이 모른다."
    )
    in_training_distribution: bool | None = None
    reasons: list[str] = Field(default_factory=list)


class PeerPosition(BaseModel):
    """비교군 안에서의 자리. **모델이 없어도 계산된다.**

    `current_valuation`과 일부러 나눠 두었다. 백분위와 갭 점수는 지금 DB에 이미
    있는 값이고, 추정 모형은 아직 붙지 않았다 — 한 덩어리로 묶으면 모형이 없는
    동안 이 값들까지 화면에서 사라진다(실제로 그렇게 만들었다가 되돌렸다).
    """

    price_percentile: float | None = Field(
        None, description="비교군 안 가격 백분위. 0이 가장 싸다."
    )
    structure_percentile: float | None = Field(
        None, description="비교군 안 길 구조 백분위."
    )
    gap_score: float | None = Field(
        None, description="구조 백분위 − 가격 백분위. 기존 지도·목록과 이어지는 값."
    )


class CurrentValuation(BaseModel):
    """방법론 C — 지금 이 필지가 같은 조건 대비 낮은가 높은가.

    **추정 모형이 있어야만 나온다.** 없으면 이 섹션이 통째로 `None`이고, 그때도
    `official_price`와 `peer_position`은 남는다.
    """

    expected_official_price: PriceRange
    discount: DiscountBreakdown
    is_cheap: bool = Field(
        description="비교군 대비 싸다. 이유를 따지지 않은 사실."
    )
    is_undervalued: bool = Field(
        description="설명되지 않는 할인분이 크다. cheap과 **별개 개념**이다."
    )


class PriceForecastHorizon(BaseModel):
    years: int
    expected_official_price: PriceRange
    annualized_change_pct: float | None = None


class PriceForecast(BaseModel):
    """방법론 A — h=1·3·5년 미래 공시지가."""

    horizons: list[PriceForecastHorizon] = Field(default_factory=list)


class GrowthOutlook(BaseModel):
    """방법론 B — peer 대비 초과성장. MVP의 주력."""

    horizon_years: int
    own_growth_pct: float | None = None
    peer_growth_pct: float | None = None
    excess_growth_pct: float | None = None
    hot_probability: float | None = Field(None, ge=0.0, le=1.0)


class RankCheck(BaseModel):
    """잔차 순위와 갭 점수 순위가 어긋나는지.

    강동구 실측에서 두 순위의 상위 20개가 **하나도 겹치지 않았다**(Spearman 0.35).
    어느 쪽이 옳은지 사용자가 판별할 수단이 없으므로, 어긋나면 감추지 않고
    강도를 낮추고 둘을 함께 보여 준다.
    """

    unexplained_discount_rank_pct: float | None = None
    gap_score_rank_pct: float | None = None
    agreement: Literal["agree", "disagree", "unknown"] = "unknown"


class AuditTrail(BaseModel):
    """나중에 왜 그런 판정이 나왔는지 되짚을 수 있게 남긴다."""

    model_version: str
    rules_version: str
    thresholds: dict[str, float] = Field(default_factory=dict)
    rank_check: RankCheck = Field(default_factory=RankCheck)
    notes: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    label: VerdictLabel
    headline: str = Field(description="화면 제목. 낙인 표현을 쓰지 않는다.")
    strength: Strength
    confidence: Confidence
    undetermined_reason: UndeterminedReason | None = Field(
        None,
        description="label이 undetermined일 때만 채운다. 그 밖에는 None.",
    )


class Narrative(BaseModel):
    """사람이 읽는 설명. **판정 값과 분리해 둔다.**

    `source`가 `static`이면 토큰 비용 0의 규칙 서술, `llm`이면 그것을 LLM이 다시
    쓴 것이다. **어느 쪽이든 위의 판정 값은 바뀌지 않는다** — LLM은 문장만 만들고
    숫자·라벨을 만들지 않는다. LLM이 실패하면 `static`으로 되돌아온다.
    """

    source: Literal["static", "llm"] = "static"
    headline: str
    paragraphs: list[str] = Field(default_factory=list)


class StructuredAnalysis(BaseModel):
    """필지 하나의 판정 전체. 프런트·에이전트·서술이 보는 유일한 형태."""

    schema_version: str = SCHEMA_VERSION
    subject: Subject
    attributes: list[Attribute] = Field(default_factory=list)
    peer_group: PeerGroup
    factors: list[Factor] = Field(default_factory=list)
    verdict: Verdict

    # 사실 — 추정 모형과 무관하게 DB에 있는 값이다. 판정을 보류해도 남는다.
    official_price: OfficialPrice | None = None
    peer_position: PeerPosition | None = None

    # 방법론 셋의 자리. 아직 없는 것은 None으로 온다.
    current_valuation: CurrentValuation | None = None
    growth_outlook: GrowthOutlook | None = None
    price_forecast: PriceForecast | None = None

    # 토큰 0의 정적 서술은 항상 채워 보낸다 — 화면이 LLM을 기다릴 이유가 없다.
    narrative: Narrative | None = None

    caveats: list[str] = Field(
        default_factory=list,
        description="인과 추정이 아니라는 것 등. 화면에 반드시 나간다.",
    )
    audit: AuditTrail
