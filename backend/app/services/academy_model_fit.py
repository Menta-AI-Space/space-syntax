"""Academy operating-model fit scores — cluster-score driven."""

from __future__ import annotations

from app.services.academy_cluster_analysis import AcademyClusterResult
from app.services.restaurant_model_fit import (
    ModelFitItem,
    ModelFitResult,
    SiteMetrics,
    _clamp,
    _verdict,
)


def _cluster_count(cluster: AcademyClusterResult | None, key: str) -> int:
    if not cluster:
        return 0
    bucket = next((c for c in cluster.clusters if c.key == key), None)
    return bucket.count if bucket else 0


def compute_academy_model_fit(
    site: SiteMetrics,
    cluster: AcademyClusterResult | None = None,
) -> ModelFitResult:
    school = site.school_age_pct or 15.0
    parent = site.parent_pct or 35.0
    hh = site.households or 0
    apt = site.apartment_pct or 40.0
    w = site.workers or 0
    youth = site.youth_pct or 25.0
    integration = site.integration_top_pct or 50.0
    transit = site.transit_distance_m or 800.0

    pressure = cluster.scores.direct_competition_pressure if cluster else 0.0
    synergy = cluster.scores.cluster_synergy if cluster else 0.0
    safety = cluster.scores.parent_safety if cluster else 80.0

    direct_n = _cluster_count(cluster, "direct")
    partial_n = _cluster_count(cluster, "partial")
    english_synergy = _cluster_count(cluster, "synergy")
    arts_n = _cluster_count(cluster, "arts_synergy")
    infra_n = _cluster_count(cluster, "infra")

    models: list[ModelFitItem] = []
    warnings: list[str] = []

    s = 34.0
    if school >= 20 and school < 28:
        s += 18
    if apt >= 45:
        s += 12
    if parent >= 30:
        s += 10
    if site.has_parking:
        s += 8
    s -= min(20, pressure * 0.18)
    s += min(12, synergy * 0.1)
    s -= min(15, (100 - safety) * 0.12)
    if school < 15:
        warnings.append("초등 대상 학원은 학교 거리와 픽업 동선 확인이 필요합니다.")
    models.append(
        ModelFitItem(
            "elem_tutor",
            "초등 보습학원",
            _clamp(s),
            _verdict(s),
            "아파트·학부모층·5–19세 인구가 맞으면 유리합니다. 직접 경쟁보다 학부모 안심도·픽업 동선을 확인하세요.",
        )
    )

    s = 32.0
    if school >= 22:
        s += 20
    if direct_n + partial_n >= 3:
        s += 10
    if transit <= 500:
        s += 10
    s -= min(18, pressure * 0.22)
    s += min(15, synergy * 0.12)
    models.append(
        ModelFitItem(
            "exam_hs",
            "중고등 입시학원",
            _clamp(s),
            _verdict(s),
            "학원가 시너지(보완 과목·인프라)와 직접 경쟁 압박을 함께 봅니다. 경쟁↑+시너지↑면 차별화가 핵심입니다.",
        )
    )

    s = 30.0
    if school >= 18:
        s += 15
    if apt >= 40:
        s += 12
    if parent >= 28:
        s += 10
    s -= min(22, pressure * 0.2)
    s += min(18, synergy * 0.15)
    if english_synergy >= 2:
        s += 8
    models.append(
        ModelFitItem(
            "english_math",
            "영어/수학학원",
            _clamp(s),
            _verdict(s),
            "같은 과목은 직접 경쟁, 다른 교과는 보완 시너지로 분리 집계됩니다. 학원가 밀집·학부모 안심도를 함께 확인하세요.",
        )
    )

    s = 28.0
    if school >= 16:
        s += 18
    if hh >= 5000:
        s += 10
    if site.has_parking:
        s += 10
    s -= min(15, pressure * 0.15)
    s += min(10, arts_n * 2)
    s -= min(20, (100 - safety) * 0.15)
    models.append(
        ModelFitItem(
            "arts_sports",
            "예체능 학원",
            _clamp(s),
            _verdict(s),
            "초등 인구·픽업 편의·주거 배후가 맞으면 유리합니다. PC방·노래방 등 유해시설 민감도가 높습니다.",
        )
    )

    s = 26.0
    if apt >= 50 and parent >= 30:
        s += 18
    if school >= 18:
        s += 12
    s -= min(15, pressure * 0.18)
    s += min(12, synergy * 0.1)
    models.append(
        ModelFitItem(
            "coding",
            "코딩/AI 학원",
            _clamp(s),
            _verdict(s),
            "중산층 이상 주거·초중등 학생·교육 관심도가 맞을 때 검토합니다.",
        )
    )

    s = 30.0
    if w >= 6000:
        s += 20
    if transit <= 400:
        s += 15
    if youth >= 25:
        s += 8
    s -= min(12, pressure * 0.12)
    s += min(10, infra_n * 2)
    models.append(
        ModelFitItem(
            "adult",
            "성인 어학/자격증 학원",
            _clamp(s),
            _verdict(s),
            "역세권·직장인·야간 접근성이 핵심입니다. 유해시설 민감도는 상대적으로 낮습니다.",
        )
    )

    s = 28.0
    if school >= 20 or youth >= 28:
        s += 15
    if infra_n >= 2:
        s += 10
    s += min(12, synergy * 0.08)
    s -= min(10, pressure * 0.1)
    models.append(
        ModelFitItem(
            "study_cafe",
            "스터디카페",
            _clamp(s),
            _verdict(s),
            "학습 인프라·보완 학원 밀집 지역에서 스터디·독서실 대체 경쟁과 함께 봅니다.",
        )
    )

    models.sort(key=lambda m: m.score, reverse=True)
    top, second = models[0], models[1]
    headline = (
        f"이 위치는 {models[-1].label}보다 {top.label}({top.verdict})에 더 적합하고, "
        f"{second.label}은 {second.verdict} 수준입니다."
    )
    if cluster and pressure >= 55 and synergy >= 50:
        headline += " 직접 경쟁과 학원가 시너지가 동시에 높은 학원가 입지입니다."
    return ModelFitResult(
        vertical="academy", headline=headline, models=models, warnings=warnings
    )


def top_model_labels(result: ModelFitResult, n: int = 2) -> list[str]:
    return [m.label for m in result.models[:n]]
