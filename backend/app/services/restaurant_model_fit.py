"""Restaurant operating-model fit scores (9 models)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.real_competition import CompetitionBucket, RealCompetitionResult


@dataclass
class SiteMetrics:
    workers: int | None = None
    population: int | None = None
    households: int | None = None
    youth_pct: float | None = None
    middle_age_pct: float | None = None
    school_age_pct: float | None = None
    parent_pct: float | None = None
    senior_pct: float | None = None
    female_pct: float | None = None
    apartment_pct: float | None = None
    integration_top_pct: float | None = None
    choice_top_pct: float | None = None
    connectivity_top_pct: float | None = None
    transit_distance_m: float | None = None
    total_score: float | None = None
    area_pyeong: float | None = None
    has_parking: bool | None = None
    has_exhaust: bool | None = None
    has_elevator: bool | None = None
    floor_level: int | None = None
    has_pharmacy_nearby: bool | None = None


@dataclass
class ModelFitItem:
    model_id: str
    label: str
    score: float
    verdict: str
    detail: str


@dataclass
class ModelFitResult:
    vertical: str
    headline: str
    models: list[ModelFitItem]
    warnings: list[str] = field(default_factory=list)


def _verdict(score: float) -> str:
    if score >= 68:
        return "좋음"
    if score >= 48:
        return "보통"
    return "낮음"


def _bucket_score(comp: RealCompetitionResult | None, key: str) -> float:
    if not comp:
        return 0.0
    b = next((x for x in comp.buckets if x.key == key), None)
    return b.weighted_score if b else 0.0


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def compute_restaurant_model_fit(
    site: SiteMetrics,
    competition: RealCompetitionResult | None = None,
) -> ModelFitResult:
    w = site.workers or 0
    pop = site.population or 0
    hh = site.households or 0
    youth = site.youth_pct or 25.0
    mid = site.middle_age_pct or 30.0
    choice = site.choice_top_pct or 50.0
    integration = site.integration_top_pct or 50.0
    transit = site.transit_distance_m or 800.0
    lunch_comp = _bucket_score(competition, "lunch_rotation")
    dinner_comp = _bucket_score(competition, "dinner_gathering")
    delivery_comp = _bucket_score(competition, "substitute_delivery")
    conv_comp = _bucket_score(competition, "substitute_convenience")
    direct_comp = _bucket_score(competition, "direct")

    models: list[ModelFitItem] = []

    # 직장인 점심형
    s = 40.0
    if w >= 8000:
        s += 25
    elif w >= 4000:
        s += 15
    if choice <= 35:
        s += 15
    if transit <= 400:
        s += 10
    s -= min(25, lunch_comp * 0.4)
    s -= min(15, direct_comp * 0.2)
    models.append(
        ModelFitItem(
            "lunch_office",
            "직장인 점심형",
            _clamp(s),
            _verdict(s),
            "종사자·통과 유입이 점심 회전에 유리한지 봅니다. 백반/분식/중식 경쟁이 많으면 메뉴·가격 차별화가 필요합니다.",
        )
    )

    # 주거 배후 밥집형
    s = 35.0
    if pop >= 15000:
        s += 20
    if hh >= 8000:
        s += 15
    s -= min(20, direct_comp * 0.25)
    models.append(
        ModelFitItem(
            "residential_dinner",
            "주거 배후 밥집형",
            _clamp(s),
            _verdict(s),
            "거주인구·가구가 저녁·단골 수요를 받쳐줍니다. 외부 유입이 약하면 재방문·가격 전략이 중요합니다.",
        )
    )

    # 배달 전문형
    s = 38.0
    if pop >= 12000:
        s += 18
    s -= min(30, delivery_comp * 0.5)
    if site.area_pyeong and site.area_pyeong >= 20:
        s += 8
    models.append(
        ModelFitItem(
            "delivery",
            "배달 전문형",
            _clamp(s),
            _verdict(s),
            "배후 인구는 배달권의 기본입니다. 치킨·피자·마라탕 등 배달 경쟁과 임대료 부담을 함께 보세요.",
        )
    )

    # 혼밥/간편식형
    s = 32.0
    if transit <= 350:
        s += 18
    if conv_comp >= 5:
        s += 8
    s -= min(15, lunch_comp * 0.3)
    models.append(
        ModelFitItem(
            "solo_quick",
            "혼밥/간편식형",
            _clamp(s),
            _verdict(s),
            "역세권·간편식 대체 수요가 있으면 혼밥·테이크아웃 모델을 검토할 수 있습니다.",
        )
    )

    # 저가 회전형
    s = 30.0
    if choice <= 40:
        s += 20
    if w >= 5000:
        s += 12
    s -= min(20, direct_comp * 0.3)
    models.append(
        ModelFitItem(
            "low_rotation",
            "저가 회전형",
            _clamp(s),
            _verdict(s),
            "유동·직장인이 많을수록 빠른 회전·저가 메뉴가 맞습니다. 프랜차이즈 밀집 시 가격 경쟁이 치열합니다.",
        )
    )

    # 고깃집/회식형
    s = 28.0
    if w >= 6000:
        s += 15
    if mid >= 25:
        s += 10
    if site.area_pyeong and site.area_pyeong >= 25:
        s += 12
    elif site.area_pyeong and site.area_pyeong < 15:
        s -= 15
    if site.has_exhaust is False:
        s -= 20
    if site.has_parking:
        s += 10
    s -= min(25, dinner_comp * 0.35)
    models.append(
        ModelFitItem(
            "bbq_gathering",
            "고깃집/회식형",
            _clamp(s),
            _verdict(s),
            "면적·배기·저녁 회식 수요가 핵심입니다. 점심 수요만 강한 자리는 회식형과 맞지 않을 수 있습니다.",
        )
    )

    # 데이트/목적방문형
    s = 30.0
    if youth >= 28:
        s += 22
    if integration <= 40:
        s += 12
    if transit <= 500:
        s += 8
    s -= min(15, direct_comp * 0.2)
    models.append(
        ModelFitItem(
            "date_destination",
            "데이트/목적방문형",
            _clamp(s),
            _verdict(s),
            "2030 비중·찾아오기 쉬운 입지가 중요합니다. 콘셉트·리뷰·차별화 없이는 경쟁이 어렵습니다.",
        )
    )

    # 가족 외식형
    s = 28.0
    if hh >= 10000:
        s += 18
    if mid >= 30:
        s += 12
    if site.has_parking:
        s += 15
    if site.area_pyeong and site.area_pyeong >= 20:
        s += 10
    models.append(
        ModelFitItem(
            "family_dining",
            "가족 외식형",
            _clamp(s),
            _verdict(s),
            "가구·중장년 비중과 주차·면적이 맞으면 가족 외식이 가능합니다.",
        )
    )

    # 술집/이자카야형
    s = 26.0
    if youth >= 26:
        s += 18
    if dinner_comp < 25:
        s += 12
    if transit <= 450:
        s += 8
    s -= min(20, dinner_comp * 0.3)
    models.append(
        ModelFitItem(
            "late_night_bar",
            "술집/이자카야형",
            _clamp(s),
            _verdict(s),
            "저녁·야간 체류와 2030 비중이 중요합니다. 술집·고깃집 밀집 시 차별 콘셉트가 필요합니다.",
        )
    )

    models.sort(key=lambda m: m.score, reverse=True)
    top = models[0]
    second = models[1] if len(models) > 1 else top
    headline = (
        f"이 위치는 {top.label}({top.verdict})에 가장 가깝고, "
        f"{second.label}은 {second.verdict} 수준입니다."
    )
    return ModelFitResult(vertical="restaurant", headline=headline, models=models)


def top_model_labels(result: ModelFitResult, n: int = 2) -> list[str]:
    return [m.label for m in result.models[:n]]
