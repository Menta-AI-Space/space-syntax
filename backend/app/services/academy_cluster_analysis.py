"""Academy subject cluster analysis — relation matrix, distance/same-building weights, 3 scores."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from app.services.sbiz_stores import StoreRecord

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

VALID_TARGET_MODELS = frozenset(
    {"math", "english", "korean", "science", "exam_cram", "arts", "coding", "adult"}
)

RELATION_TO_CLUSTER = {
    "direct_competitor": "direct",
    "partial_competitor": "partial",
    "synergy": "synergy",
    "infrastructure": "infra",
    "arts_synergy": "arts_synergy",
    "risk": "risk",
    "severe_risk": "severe_risk",
}

CLUSTER_LABELS = {
    "direct": "직접 경쟁",
    "partial": "부분 경쟁",
    "synergy": "보완 시너지",
    "arts_synergy": "예체능 시너지",
    "infra": "학습 인프라",
    "risk": "유해·주의 시설",
    "severe_risk": "강한 리스크",
}

CLUSTER_ORDER = [
    "direct",
    "partial",
    "synergy",
    "arts_synergy",
    "infra",
    "risk",
    "severe_risk",
]


@dataclass
class ClusterStore:
    name: str
    category: str
    nearby_category: str
    relation: str
    distance_m: float
    same_building: bool
    weight: float
    address: str = ""


@dataclass
class AcademyClusterBucket:
    key: str
    label: str
    relation_group: str
    count: int
    weighted_score: float
    intensity: str
    stores: list[ClusterStore] = field(default_factory=list)


@dataclass
class AcademyScores:
    direct_competition_pressure: float
    cluster_synergy: float
    parent_safety: float


@dataclass
class ParentSafetyDetail:
    level: str
    same_building_risks: list[str] = field(default_factory=list)
    nearby_risks: list[str] = field(default_factory=list)
    sensitivity_note: str = ""


@dataclass
class AcademyClusterResult:
    target_model: str
    target_label: str
    clusters: list[AcademyClusterBucket]
    scores: AcademyScores
    parent_safety_detail: ParentSafetyDetail
    headline: str
    interpretation: list[str]
    warnings: list[str] = field(default_factory=list)


def _load_json(name: str) -> dict:
    with (DATA_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _text_blob(store: StoreRecord) -> str:
    return " ".join(
        [store.bizes_nm, store.inds_lcls_nm, store.inds_mcls_nm, store.inds_scls_nm]
    )


def _matches_category(store: StoreRecord, cat: dict) -> bool:
    mcls = set(cat.get("mcls_codes") or [])
    scls = set(cat.get("scls_codes") or [])
    if store.inds_mcls_cd in mcls or store.inds_scls_cd in scls:
        return True
    blob = _text_blob(store).lower()
    for kw in cat.get("keywords") or []:
        if kw and kw.lower() in blob:
            return True
    return False


def classify_store(store: StoreRecord, keywords_data: dict | None = None) -> str:
    data = keywords_data or _load_json("academy_subject_keywords.json")
    categories = data.get("categories") or []
    best_key = "generic_academy"
    best_score = 0
    blob = _text_blob(store).lower()

    for cat in categories:
        key = cat["key"]
        mcls = set(cat.get("mcls_codes") or [])
        scls = set(cat.get("scls_codes") or [])
        score = 0
        if store.inds_scls_cd in scls:
            score = 3
        elif store.inds_mcls_cd in mcls:
            score = 1
        scls_nm = (store.inds_scls_nm or "").strip()
        for official in cat.get("scls_names") or []:
            official = str(official).strip()
            if official and (official == scls_nm or official in scls_nm or scls_nm in official):
                score = max(score, 3)
        for kw in cat.get("keywords") or []:
            if kw and kw.lower() in blob:
                score = max(score, 4)
        if score > best_score:
            best_score = score
            best_key = key
    return best_key


def resolve_relation(
    target_model: str,
    nearby_category: str,
    rules_data: dict | None = None,
) -> tuple[str, float]:
    data = rules_data or _load_json("academy_competitor_rules.json")
    model_rules = (data.get("rules") or {}).get(target_model) or {}
    entry = model_rules.get(nearby_category)
    if not entry:
        return "synergy", 0.0
    return entry["relation"], float(entry["weight"])


def _distance_weight(
    distance_m: float,
    bands: list[dict],
    *,
    same_building: bool,
    relation: str,
) -> float:
    for band in bands:
        if same_building and band.get("same_building"):
            base = float(band["weight"])
            break
        if not same_building and distance_m <= band.get("max", 0):
            base = float(band["weight"])
            break
    else:
        base = 0.1

    if same_building:
        if relation in ("risk", "severe_risk"):
            return base * 2.0
        if relation in ("direct_competitor", "partial_competitor"):
            return base * 1.2
    return base


def _intensity_label(score: float) -> str:
    s = abs(score)
    if s >= 8:
        return "매우 높음"
    if s >= 4:
        return "높음"
    if s >= 1.5:
        return "보통"
    return "낮음"


def _score_level(value: float, *, higher_is_bad: bool = False) -> str:
    if higher_is_bad:
        if value >= 70:
            return "높음"
        if value >= 40:
            return "보통"
        return "낮음"
    if value >= 70:
        return "좋음"
    if value >= 40:
        return "보통"
    return "낮음"


def _normalize_pressure(raw: float) -> float:
    return round(min(100.0, max(0.0, raw * 8.0)), 1)


def _normalize_synergy(raw: float) -> float:
    return round(min(100.0, max(0.0, raw * 12.0)), 1)


def _normalize_safety(raw_risk: float, sensitivity: float) -> float:
    penalty = raw_risk * sensitivity * 10.0
    return round(min(100.0, max(0.0, 100.0 - penalty)), 1)


def _infer_site_bld_mng_no(
    lat: float,
    lng: float,
    stores: list[StoreRecord],
    radius_m: int,
) -> str:
    best = ""
    best_dist = float("inf")
    for store in stores:
        if store.lat is None or store.lon is None or not store.bld_mng_no:
            continue
        dist = _haversine_m(lat, lng, store.lat, store.lon)
        if dist <= radius_m and dist < best_dist:
            best_dist = dist
            best = store.bld_mng_no
    return best


def compute_academy_cluster(
    *,
    lat: float,
    lng: float,
    stores: list[StoreRecord],
    target_model: str,
    radius_m: int = 500,
    bld_mng_no: str | None = None,
    keywords_data: dict | None = None,
    rules_data: dict | None = None,
) -> AcademyClusterResult:
    if target_model not in VALID_TARGET_MODELS:
        raise ValueError(f"target_model must be one of {sorted(VALID_TARGET_MODELS)}")

    keywords_data = keywords_data or _load_json("academy_subject_keywords.json")
    rules_data = rules_data or _load_json("academy_competitor_rules.json")
    target_meta = (rules_data.get("target_models") or {}).get(target_model) or {}
    target_label = target_meta.get("label") or target_model
    risk_sensitivity = float(target_meta.get("risk_sensitivity") or 1.0)
    bands = rules_data.get("distance_bands_m") or []

    provided_bld = (bld_mng_no or "").strip()
    site_bld = provided_bld
    bld_estimated = False
    if not site_bld:
        site_bld = _infer_site_bld_mng_no(lat, lng, stores, radius_m)
        bld_estimated = bool(site_bld)

    warnings: list[str] = []
    if bld_estimated:
        warnings.append(
            "후보지 건물관리번호가 입력되지 않아 반경 내 최근접 점포의 건물번호로 "
            "대체 추정했습니다. 동일건물 판정(리스크 가중치·학부모 안심도)이 실제와 "
            "다를 수 있습니다."
        )

    cluster_entries: dict[str, list[ClusterStore]] = {k: [] for k in CLUSTER_ORDER}
    pressure_raw = 0.0
    synergy_raw = 0.0
    risk_raw = 0.0
    same_building_risks: list[str] = []
    nearby_risks: list[str] = []

    for store in stores:
        if store.lat is None or store.lon is None:
            continue
        dist = _haversine_m(lat, lng, store.lat, store.lon)
        if dist > radius_m:
            continue

        nearby_cat = classify_store(store, keywords_data)
        relation, type_weight = resolve_relation(target_model, nearby_cat, rules_data)
        if type_weight == 0.0 and relation == "synergy":
            continue

        same_bld = bool(
            site_bld and store.bld_mng_no and site_bld == store.bld_mng_no
        )
        dist_w = _distance_weight(dist, bands, same_building=same_bld, relation=relation)
        weighted = abs(type_weight) * dist_w
        cluster_key = RELATION_TO_CLUSTER.get(relation, "synergy")

        entry = ClusterStore(
            name=store.bizes_nm,
            category=store.inds_scls_nm or store.inds_mcls_nm,
            nearby_category=nearby_cat,
            relation=relation,
            distance_m=round(dist, 0),
            same_building=same_bld,
            weight=round(weighted, 3),
            address=store.rdnm_adr,
        )
        cluster_entries[cluster_key].append(entry)

        if relation in ("direct_competitor", "partial_competitor"):
            pressure_raw += weighted
        elif relation in ("synergy", "infrastructure", "arts_synergy"):
            synergy_raw += weighted
        elif relation in ("risk", "severe_risk"):
            risk_raw += weighted
            if same_bld:
                same_building_risks.append(store.bizes_nm)
            else:
                nearby_risks.append(store.bizes_nm)

    clusters: list[AcademyClusterBucket] = []
    for key in CLUSTER_ORDER:
        entries = cluster_entries.get(key) or []
        entries.sort(key=lambda e: (not e.same_building, e.distance_m))
        weighted_sum = round(sum(e.weight for e in entries), 2)
        clusters.append(
            AcademyClusterBucket(
                key=key,
                label=CLUSTER_LABELS.get(key, key),
                relation_group=key,
                count=len(entries),
                weighted_score=weighted_sum,
                intensity=_intensity_label(weighted_sum),
                stores=entries,
            )
        )

    pressure = _normalize_pressure(pressure_raw)
    synergy = _normalize_synergy(synergy_raw)
    safety = _normalize_safety(risk_raw, risk_sensitivity)

    if same_building_risks:
        safety_level = "주의"
        sensitivity_note = (
            f"{target_label} 기준 유해·주의 시설 민감도가 적용되었습니다. "
            f"동일건물 {len(same_building_risks)}곳이 학부모 안심도에 큰 영향을 줍니다."
        )
    elif nearby_risks:
        safety_level = "보통"
        sensitivity_note = (
            f"인근 유해·주의 시설 {len(nearby_risks)}곳 — "
            f"동일건물 유해업소는 없습니다."
        )
    else:
        safety_level = "양호"
        sensitivity_note = "동일건물·인근 유해·주의 시설이 거의 없습니다."

    direct_bucket = next((c for c in clusters if c.key == "direct"), None)
    synergy_bucket = next((c for c in clusters if c.key == "synergy"), None)
    direct_n = direct_bucket.count if direct_bucket else 0
    synergy_n = (synergy_bucket.count if synergy_bucket else 0) + sum(
        c.count for c in clusters if c.key in ("arts_synergy", "infra")
    )

    headline_parts = [f"{target_label} 기준 클러스터 분석"]
    if direct_n:
        headline_parts.append(f"직접 경쟁 {direct_n}곳")
    if synergy_n:
        headline_parts.append(f"보완·인프라 {synergy_n}곳")
    headline = " · ".join(headline_parts)

    interpretation: list[str] = []
    if pressure >= 60 and synergy >= 50:
        interpretation.append(
            "직접·부분 경쟁이 많지만 영어·국어·과학 등 보완 학원과 학습 인프라도 함께 밀집한 "
            "전형적인 학원가입니다. 차별화 브랜드·커리큘럼 없이는 경쟁 압박이 큽니다."
        )
    elif pressure >= 60:
        interpretation.append(
            "같은 과목·유사 과목 경쟁이 강합니다. 학원가 시너지는 상대적으로 약해 "
            "독립 입지 또는 강한 차별화가 필요합니다."
        )
    elif synergy >= 50:
        interpretation.append(
            "직접 경쟁은 적고 보완 과목·학습 인프라가 풍부합니다. "
            "학원가 시너지를 활용한 신규 입지에 유리할 수 있습니다."
        )
    else:
        interpretation.append(
            "주변 학원·인프라 밀도가 낮은 편입니다. 선점 입지이나 "
            "배후 수요·접근성을 별도로 확인하세요."
        )

    if same_building_risks:
        interpretation.append(
            f"동일건물 유해·주의 업소({', '.join(same_building_risks[:3])}"
            f"{'' if len(same_building_risks) <= 3 else ' 등'}) — "
            f"학부모 안심도 {_score_level(safety, higher_is_bad=True)}."
        )

    return AcademyClusterResult(
        target_model=target_model,
        target_label=target_label,
        clusters=clusters,
        scores=AcademyScores(
            direct_competition_pressure=pressure,
            cluster_synergy=synergy,
            parent_safety=safety,
        ),
        parent_safety_detail=ParentSafetyDetail(
            level=safety_level,
            same_building_risks=same_building_risks,
            nearby_risks=nearby_risks,
            sensitivity_note=sensitivity_note,
        ),
        headline=headline,
        interpretation=interpretation,
        warnings=warnings,
    )


def is_academy_upjong(*, archetype: str, inds_mcls_cd: str | None) -> bool:
    return archetype == "education" or bool(inds_mcls_cd and inds_mcls_cd.startswith("P10"))
