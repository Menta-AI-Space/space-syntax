"""Cafe operating-model fit scores (8 models)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.real_competition import RealCompetitionResult
from app.services.restaurant_model_fit import SiteMetrics, _bucket_score, _clamp, _verdict


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


def compute_cafe_model_fit(
    site: SiteMetrics,
    competition: RealCompetitionResult | None = None,
) -> ModelFitResult:
    w = site.workers or 0
    pop = site.population or 0
    youth = site.youth_pct or 25.0
    choice = site.choice_top_pct or 50.0
    integration = site.integration_top_pct or 50.0
    transit = site.transit_distance_m or 800.0
    direct = _bucket_score(competition, "direct")
    low_brand = _bucket_score(competition, "low_price_brand")
    bakery = _bucket_score(competition, "substitute_bakery")
    conv = _bucket_score(competition, "substitute_convenience")

    models: list[ModelFitItem] = []

    s = 35.0
    if transit <= 400:
        s += 18
    if choice <= 35:
        s += 15
    if w >= 5000:
        s += 12
    s -= min(30, low_brand * 0.8)
    s -= min(15, direct * 0.2)
    models.append(
        ModelFitItem(
            "low_price_takeout",
            "저가 테이크아웃",
            _clamp(s),
            _verdict(s),
            "역·통과 유입은 좋지만 저가 브랜드가 가까우면 가격 경쟁이 치열합니다.",
        )
    )

    s = 32.0
    if youth >= 28:
        s += 22
    if pop >= 12000:
        s += 12
    s -= min(20, bakery * 0.4)
    s -= min(10, direct * 0.15)
    models.append(
        ModelFitItem(
            "dessert_cafe",
            "개인 디저트 카페",
            _clamp(s),
            _verdict(s),
            "2030·배후 수요가 있으면 디저트 특화가 가능합니다. 베이커리·디저트 경쟁을 확인하세요.",
        )
    )

    s = 30.0
    if pop >= 15000:
        s += 18
    if site.area_pyeong and site.area_pyeong >= 18:
        s += 10
    s -= min(15, bakery * 0.35)
    models.append(
        ModelFitItem(
            "bakery_cafe",
            "베이커리 카페",
            _clamp(s),
            _verdict(s),
            "주거 배후와 면적이 맞으면 베이커리 결합형을 검토할 수 있습니다.",
        )
    )

    s = 28.0
    if youth >= 30:
        s += 15
    if site.area_pyeong and site.area_pyeong >= 22:
        s += 12
    if site.has_parking:
        s += 10
    s -= min(12, direct * 0.15)
    models.append(
        ModelFitItem(
            "brunch_cafe",
            "브런치 카페",
            _clamp(s),
            _verdict(s),
            "체류·주차·면적이 맞아야 브런치 모델이 현실적입니다.",
        )
    )

    s = 30.0
    if pop >= 10000:
        s += 15
    s -= min(25, low_brand * 0.6)
    s -= min(20, direct * 0.25)
    models.append(
        ModelFitItem(
            "unmanned_cafe",
            "무인카페",
            _clamp(s),
            _verdict(s),
            "주거·오피스텔 배후와 낮은 임대료가 맞을 때 검토합니다. 브랜드 압박이 크면 어렵습니다.",
        )
    )

    s = 24.0
    if site.area_pyeong and site.area_pyeong >= 30:
        s += 20
    if site.has_parking:
        s += 15
    if integration <= 45:
        s += 10
    s -= min(15, direct * 0.2)
    models.append(
        ModelFitItem(
            "large_stay",
            "대형 체류형 카페",
            _clamp(s),
            _verdict(s),
            "넓은 면적·주차·목적 방문성이 필요합니다. 도심 소형 입지와는 맞기 어렵습니다.",
        )
    )

    s = 34.0
    if w >= 7000:
        s += 20
    if transit <= 350:
        s += 12
    s -= min(20, low_brand * 0.5)
    models.append(
        ModelFitItem(
            "office_takeout",
            "오피스 테이크아웃",
            _clamp(s),
            _verdict(s),
            "종사자·역 접근이 좋으면 출근·점심 수요형이 유리합니다.",
        )
    )

    s = 30.0
    if youth >= 27:
        s += 18
    if integration <= 40:
        s += 12
    s -= min(15, direct * 0.2)
    models.append(
        ModelFitItem(
            "specialty",
            "스페셜티/감성 카페",
            _clamp(s),
            _verdict(s),
            "목적 방문·차별화가 핵심입니다. 단순 커피 판매만으로는 경쟁이 어렵습니다.",
        )
    )

    models.sort(key=lambda m: m.score, reverse=True)
    top, second = models[0], models[1]
    headline = (
        f"이 위치는 {top.label}({top.verdict})에 가장 가깝고, "
        f"{second.label}은 {second.verdict} 수준입니다."
    )
    return ModelFitResult(vertical="cafe", headline=headline, models=models)


def top_model_labels(result: ModelFitResult, n: int = 2) -> list[str]:
    return [m.label for m in result.models[:n]]
