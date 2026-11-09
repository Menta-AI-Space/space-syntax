"""Clinic specialty model fit scores (7 models)."""

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


def _pharmacy_nearby(site: SiteMetrics, competition: RealCompetitionResult | None) -> bool:
    if site.has_pharmacy_nearby:
        return True
    pharm = _bucket_score(competition, "substitute_pharmacy")
    return pharm > 0.3


def compute_clinic_model_fit(
    site: SiteMetrics,
    competition: RealCompetitionResult | None = None,
) -> ModelFitResult:
    w = site.workers or 0
    pop = site.population or 0
    hh = site.households or 0
    female = site.female_pct or 50.0
    parent = site.parent_pct or 35.0
    senior = site.senior_pct or 20.0
    school = site.school_age_pct or 15.0
    choice = site.choice_top_pct or 50.0
    integration = site.integration_top_pct or 50.0
    transit = site.transit_distance_m or 800.0
    direct = _bucket_score(competition, "direct")
    aesthetic = _bucket_score(competition, "substitute_aesthetic")
    derma_spec = _bucket_score(competition, "specialty_derma")
    dental_spec = _bucket_score(competition, "specialty_dental")
    ortho_spec = _bucket_score(competition, "specialty_ortho")
    hani_sub = _bucket_score(competition, "substitute_hani")
    rehab = _bucket_score(competition, "substitute_rehab")
    pharmacy = _pharmacy_nearby(site, competition)

    models: list[ModelFitItem] = []
    warnings: list[str] = []

    s = 32.0
    if female >= 52 and parent >= 30:
        s += 18
    if w >= 5000:
        s += 15
    if transit <= 400:
        s += 12
    if choice <= 40:
        s += 8
    s -= min(25, derma_spec * 0.35 + aesthetic * 0.4)
    models.append(
        ModelFitItem(
            "derma_aesthetic",
            "피부과/미용의원",
            _clamp(s),
            _verdict(s),
            "20–40대 여성·직장인·역 접근성이 핵심입니다. 에스테틱·피부관리 밀집 시 차별화가 필요합니다.",
        )
    )

    s = 34.0
    if hh >= 6000 or pop >= 12000:
        s += 16
    if w >= 4000:
        s += 12
    if transit <= 500:
        s += 10
    s -= min(22, dental_spec * 0.4)
    models.append(
        ModelFitItem(
            "dental",
            "치과",
            _clamp(s),
            _verdict(s),
            "주거·직장 혼합 배후와 대중교통 접근이 장기 환자 확보에 유리합니다.",
        )
    )

    s = 36.0
    if pop >= 10000:
        s += 18
    if hh >= 7000:
        s += 12
    if pharmacy:
        s += 10
    s -= min(18, direct * 0.2)
    models.append(
        ModelFitItem(
            "internal_family",
            "동네 내과/가정의학",
            _clamp(s),
            _verdict(s),
            "주거인구·가구·약국 근접이 생활형 내과에 유리합니다.",
        )
    )

    s = 30.0
    if senior >= 22:
        s += 22
    if site.has_parking:
        s += 15
    if site.area_pyeong and site.area_pyeong >= 20:
        s += 10
    if site.has_elevator or (site.floor_level is not None and site.floor_level <= 3):
        s += 8
    s -= min(20, ortho_spec * 0.35 + rehab * 0.3)
    if not site.has_parking:
        warnings.append("정형외과·통증의학과는 주차·엘리베이터·면적 조건 확인이 필요합니다.")
    models.append(
        ModelFitItem(
            "ortho_pain",
            "정형외과/통증의학과",
            _clamp(s),
            _verdict(s),
            "중장년·노년층과 주차·층수·면적이 맞아야 합니다. 재활·한의원 대체 경쟁을 봅니다.",
        )
    )

    s = 28.0
    if school >= 18:
        s += 22
    if pop >= 8000:
        s += 12
    if pharmacy:
        s += 8
    s -= min(15, direct * 0.15)
    models.append(
        ModelFitItem(
            "pediatric",
            "소아과",
            _clamp(s),
            _verdict(s),
            "5–19세 인구·가족가구·약국이 핵심입니다. 주거 깊은 곳은 접근성 확인이 필요합니다.",
        )
    )

    s = 33.0
    if hh >= 5000:
        s += 15
    if senior >= 18 or parent >= 32:
        s += 12
    if w >= 3000:
        s += 8
    s -= min(20, hani_sub * 0.35)
    models.append(
        ModelFitItem(
            "hani",
            "한의원",
            _clamp(s),
            _verdict(s),
            "주거 배후·중장년·직장인 통증 수요가 맞으면 유리합니다. 한의원 밀집 시 경쟁이 치열합니다.",
        )
    )

    s = 26.0
    if site.has_parking:
        s += 20
    if site.area_pyeong and site.area_pyeong >= 30:
        s += 18
    if integration <= 45:
        s += 10
    s -= min(12, direct * 0.1)
    if not site.has_parking or not (site.area_pyeong and site.area_pyeong >= 25):
        warnings.append("검진센터는 면적·주차·광역 접근성 확인이 필요합니다.")
    models.append(
        ModelFitItem(
            "checkup",
            "검진센터",
            _clamp(s),
            _verdict(s),
            "넓은 면적·주차·목적 방문 접근성이 필수입니다. 소형 의원 입지와는 결이 다릅니다.",
        )
    )

    models.sort(key=lambda m: m.score, reverse=True)
    top, second = models[0], models[1]
    headline = (
        f"이 위치는 동네 생활형 병원보다 {top.label}({top.verdict})에 더 가깝고, "
        f"{second.label}은 {second.verdict} 수준입니다."
    )
    return ModelFitResult(
        vertical="clinic", headline=headline, models=models, warnings=warnings
    )


def top_model_labels(result: ModelFitResult, n: int = 2) -> list[str]:
    return [m.label for m in result.models[:n]]
