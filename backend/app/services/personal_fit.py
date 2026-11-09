"""사용자 우선순위 기반 맞춤 점수 (기본 업종 적합도 + 맞춤 적합도 이중 스코어).

사용자가 "가장 중요한 조건" 최대 3개를 고르면 해당 요인의 가중치를
키워(×BOOST) 재정규화한 맞춤 점수를 기본 점수 옆에 함께 보여준다.
기본 점수(업종 적합도)는 절대 바뀌지 않는다 — 신뢰의 기준선.

데이터가 아직 없는 우선순위(임대료·주차·성장성)는 가중치를 조작하지
않고 정직한 안내 노트만 남긴다 (원하는 답을 만들어주는 점수 금지).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.site_score import competition_advantage_score

# 우선순위 → 점수 요인 매핑.
# factors가 빈 리스트 = 해당 데이터 미연동 (노트만 제공, 점수 무변화).
PRIORITIES: dict[str, dict] = {
    "high_sales": {"label": "높은 매출 가능성", "factors": ["sales"]},
    "high_footfall": {"label": "유동인구가 많은 곳", "factors": ["choice", "integration"]},
    "low_competition": {"label": "경쟁이 적은 곳", "factors": ["competition"]},
    "strong_residential": {"label": "배후 주거인구가 많은 곳", "factors": ["demand"]},
    "strong_workers": {"label": "직장인이 많은 곳", "factors": ["demand", "choice"]},
    "youth": {"label": "20대 인구가 많은 곳", "factors": ["youth"]},
    "teen": {"label": "10대 인구가 많은 곳", "factors": ["teen"]},
    # 업종마다 노리는 손님이 다르다 — 20·10대만 고를 수 있으면 40대 상대 업종은
    # 인구 조건을 아예 못 쓴다. 행정동 통계에 이미 연령대·성비가 있어 그대로 쓴다.
    "age_30s": {"label": "30대 인구가 많은 곳", "factors": ["age_30s"]},
    "age_40s": {"label": "40대 인구가 많은 곳", "factors": ["age_40s"]},
    "age_50plus": {"label": "50대 이상이 많은 곳", "factors": ["age_50plus"]},
    "female": {"label": "여성 인구가 많은 곳", "factors": ["female"]},
    "male": {"label": "남성 인구가 많은 곳", "factors": ["male"]},
    "foreign": {"label": "외국인 유동이 많은 곳", "factors": ["foreign"]},
    "delivery": {"label": "배달 수요가 높은 곳", "factors": ["demand"]},
    "stability": {"label": "안정적인 장기 운영", "factors": ["competition", "demand"]},
    "low_rent": {
        "label": "낮은 임대료",
        "factors": [],
        "note": "임대료 데이터 연동 중 (투자수익성 기능에서 제공 예정)",
    },
    "parking": {
        "label": "주차가 편한 곳",
        "factors": [],
        "note": "주차장 데이터 미연동 — 점수에 반영되지 않습니다",
    },
    "growth": {
        "label": "향후 성장 가능성이 높은 곳",
        "factors": [],
        "note": "상권변화지표 연동 준비 중 — 점수에 반영되지 않습니다",
    },
}

MAX_PRIORITIES = 3
BOOST = 1.8  # 선택된 요인 가중치 배율 (재정규화 전)
# 맞춤 계산에서 매출 요인이 처음 얻는 기본 가중치 (프로파일 siteFeatures에는 없음)
SALES_BASE_WEIGHT = 0.12

FACTOR_LABELS = {
    "integration": "접근성(도로망)",
    "choice": "유동 통행축",
    "mean_depth": "도로망 중심성",
    "connectivity": "가시성(연결)",
    "transit": "대중교통",
    "demand": "배후 수요",
    "competition": "경쟁 여건",
    "sales": "매출 수준",
    "youth": "20대 인구",
    "teen": "10대 인구",
    "age_30s": "30대 인구",
    "age_40s": "40대 인구",
    "age_50plus": "50대 이상 인구",
    "female": "여성 인구",
    "male": "남성 인구",
    "foreign": "외국인 유동",
}


@dataclass
class PriorityNote:
    key: str
    label: str
    applied: bool
    note: str = ""


@dataclass
class PersonalFit:
    base_score: float
    personal_score: float
    priorities: list[PriorityNote] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    weights_used: dict[str, float] = field(default_factory=dict)


def _factor_scores(
    subscores: list,
    sales_ratio: float | None,
    extra_factor_scores: dict[str, float] | None = None,
) -> dict[str, float]:
    """서브스코어 → 요인 점수 (경쟁은 밀도→유리함 변환, 매출은 서울 대비 배율)."""
    scores: dict[str, float] = {}
    for ss in subscores:
        if ss.score is None:
            continue
        val = ss.score
        if ss.key == "competition":
            val = competition_advantage_score(val)
        scores[ss.key] = val
    if sales_ratio is not None:
        scores["sales"] = round(min(100.0, 50.0 * sales_ratio), 1)
    if extra_factor_scores:
        scores.update(
            {
                key: max(0.0, min(100.0, float(value)))
                for key, value in extra_factor_scores.items()
            }
        )
    return scores


def compute_personal_fit(
    *,
    base_score: float,
    subscores: list,
    base_weights: dict[str, float],
    priorities: list[str],
    sales_ratio: float | None = None,
    extra_factor_scores: dict[str, float] | None = None,
    priority_boosts: dict[str, float] | None = None,
) -> PersonalFit:
    """기본 점수는 그대로, 선택 우선순위 요인만 가중치 부스트해 맞춤 점수 산출."""
    scores = _factor_scores(subscores, sales_ratio, extra_factor_scores)

    notes: list[PriorityNote] = []
    boosted_factors: set[str] = set()
    for key in priorities[:MAX_PRIORITIES]:
        spec = PRIORITIES.get(key)
        if spec is None:
            notes.append(PriorityNote(key, key, False, "알 수 없는 우선순위"))
            continue
        factors = [f for f in spec["factors"] if f in scores]
        if not factors:
            notes.append(
                PriorityNote(
                    key, spec["label"], False,
                    spec.get("note", "해당 데이터 없음 — 점수에 반영되지 않습니다"),
                )
            )
            continue
        boosted_factors.update(factors)
        notes.append(PriorityNote(key, spec["label"], True))

    weights = dict(base_weights)
    if "sales" in boosted_factors:
        weights.setdefault("sales", SALES_BASE_WEIGHT)
    # 우선순위별 강도 슬라이더(GROWTHFACTOR_GAP P3 — GF의 "your weight").
    # 값은 배율 그 자체다: 1.0=기본과 동일, BOOST(1.8)=종전 기본 부스트,
    # 3.0=최대. 범위를 벗어난 값은 조용히 자르지 않고 경계로 고정한다 —
    # 프론트 버그로 0이나 100이 와도 점수가 무너지면 안 된다.
    boosts = priority_boosts or {}

    def _boost_for(factor: str) -> float:
        raw = None
        for key in priorities[:MAX_PRIORITIES]:
            spec = PRIORITIES.get(key)
            if spec and factor in spec["factors"] and key in boosts:
                raw = boosts[key]
                break
        if raw is None:
            return BOOST
        try:
            return min(3.0, max(1.0, float(raw)))
        except (TypeError, ValueError):
            return BOOST

    for f in boosted_factors:
        mult = _boost_for(f)
        weights[f] = weights.get(f, 0.0) * mult if weights.get(f) else SALES_BASE_WEIGHT * mult

    weighted = 0.0
    total_w = 0.0
    for f, score in scores.items():
        w = weights.get(f, 0.0)
        weighted += score * w
        total_w += w
    personal = round(weighted / total_w, 1) if total_w > 0 else base_score

    # 맞춤 설명 재료: 부스트된 요인 중심으로 강점/주의 분리
    strengths: list[str] = []
    cautions: list[str] = []
    for f in sorted(boosted_factors, key=lambda x: -scores.get(x, 0)):
        label = FACTOR_LABELS.get(f, f)
        s = scores[f]
        if s >= 60:
            strengths.append(f"{label} 우수 ({s:.0f}점)")
        elif s <= 45:
            cautions.append(f"{label} 취약 ({s:.0f}점) — 선택하신 조건과 상충")
    # 부스트 안 된 요인 중 크게 약한 것도 단점으로 노출 (장점만 보여주기 금지)
    for f, s in scores.items():
        if f not in boosted_factors and s <= 35:
            cautions.append(f"{FACTOR_LABELS.get(f, f)} 약함 ({s:.0f}점)")

    norm = {k: round(v / total_w, 3) for k, v in weights.items() if k in scores} if total_w else {}
    return PersonalFit(
        base_score=base_score,
        personal_score=personal,
        priorities=notes,
        strengths=strengths,
        cautions=cautions,
        weights_used=norm,
    )
