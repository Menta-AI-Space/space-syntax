"""Bakery operating-model fit scores (6 models)."""

from __future__ import annotations

from app.services.real_competition import RealCompetitionResult
from app.services.restaurant_model_fit import (
    ModelFitItem,
    ModelFitResult,
    SiteMetrics,
    _bucket_score,
    _clamp,
    _verdict,
)


def compute_bakery_model_fit(
    site: SiteMetrics,
    competition: RealCompetitionResult | None = None,
) -> ModelFitResult:
    pop = site.population or 0
    hh = site.households or 0
    apt = site.apartment_pct or 40.0
    parent = site.parent_pct or 35.0
    school = site.school_age_pct or 15.0
    w = site.workers or 0
    choice = site.choice_top_pct or 50.0
    transit = site.transit_distance_m or 800.0
    direct = _bucket_score(competition, "direct")
    franchise = _bucket_score(competition, "franchise_bakery")
    dessert = _bucket_score(competition, "dessert_specialty")
    cafe = _bucket_score(competition, "substitute_cafe")
    conv = _bucket_score(competition, "substitute_convenience")
    sandwich = _bucket_score(competition, "substitute_sandwich")

    models: list[ModelFitItem] = []
    warnings: list[str] = []

    s = 36.0
    if pop >= 12000:
        s += 18
    if apt >= 45:
        s += 12
    if hh >= 6000:
        s += 10
    s -= min(22, franchise * 0.5 + direct * 0.2)
    if franchise >= 2:
        warnings.append("프랜차이즈 빵집 근접 시 가격·인지도 압박이 큽니다.")
    models.append(
        ModelFitItem(
            "neighborhood",
            "생활형 동네빵집",
            _clamp(s),
            _verdict(s),
            "아파트·가족가구·반복 구매 수요가 핵심입니다. 프랜차이즈 근접 시 차별화가 필요합니다.",
        )
    )

    s = 32.0
    if w >= 5000:
        s += 20
    if transit <= 400:
        s += 15
    if choice <= 38:
        s += 12
    s -= min(18, sandwich * 0.3 + conv * 0.25)
    models.append(
        ModelFitItem(
            "morning_sandwich",
            "샌드위치/아침형",
            _clamp(s),
            _verdict(s),
            "출퇴근·직장인·역 접근과 통과 유입이 유리합니다. 편의점·샌드위치 대체 경쟁을 확인하세요.",
        )
    )

    s = 30.0
    if parent >= 30:
        s += 15
    if site.youth_pct and site.youth_pct >= 26:
        s += 12
    if transit <= 500:
        s += 8
    s -= min(20, dessert * 0.35 + cafe * 0.3)
    models.append(
        ModelFitItem(
            "dessert_cake",
            "디저트/케이크 전문",
            _clamp(s),
            _verdict(s),
            "20–40대·선물 수요·역세권이 맞으면 유리합니다. 카페·디저트 경쟁이 분산시킵니다.",
        )
    )

    s = 24.0
    if apt >= 55 and parent >= 32:
        s += 18
    integration = site.integration_top_pct or 50.0
    if integration <= 40:
        s += 12
    s -= min(15, direct * 0.2)
    models.append(
        ModelFitItem(
            "premium",
            "프리미엄 베이커리",
            _clamp(s),
            _verdict(s),
            "고소득 주거·목적 방문·브랜드 경쟁이 맞아야 합니다. 단순 유동만으로는 어렵습니다.",
        )
    )

    s = 26.0
    if site.area_pyeong and site.area_pyeong >= 18:
        s += 15
    if pop >= 10000:
        s += 10
    s -= min(25, cafe * 0.45)
    models.append(
        ModelFitItem(
            "bakery_cafe",
            "베이커리 카페",
            _clamp(s),
            _verdict(s),
            "면적·체류·카페 수요가 함께 맞아야 합니다. 카페 경쟁이 많으면 디저트만으로 버티기 어렵습니다.",
        )
    )

    s = 30.0
    if choice <= 35:
        s += 18
    if w >= 4000:
        s += 15
    if transit <= 450:
        s += 10
    s -= min(20, franchise * 0.4)
    models.append(
        ModelFitItem(
            "franchise_style",
            "프랜차이즈형(출퇴근)",
            _clamp(s),
            _verdict(s),
            "대로변·유동·출퇴근 동선이 맞을 때 검토합니다. 기존 프랜차이즈와의 거리·가격 경쟁이 중요합니다.",
        )
    )

    models.sort(key=lambda m: m.score, reverse=True)
    top, second = models[0], models[1]
    headline = (
        f"이 위치는 {models[-1].label}보다 {top.label}({top.verdict})에 더 적합하고, "
        f"{second.label}은 {second.verdict} 수준입니다."
    )
    strategy = ""
    if top.model_id == "morning_sandwich":
        strategy = (
            "출근길 커피+샌드위치, 퇴근길 식빵/디저트 픽업형 모델이 적합합니다. "
            "대형 베이커리카페보다 소형 회전형이 현실적입니다."
        )
    return ModelFitResult(
        vertical="bakery",
        headline=headline + (f" {strategy}" if strategy else ""),
        models=models,
        warnings=warnings,
    )


def top_model_labels(result: ModelFitResult, n: int = 2) -> list[str]:
    return [m.label for m in result.models[:n]]
