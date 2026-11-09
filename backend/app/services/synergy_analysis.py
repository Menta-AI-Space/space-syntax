"""Generic complementary-business synergy engine — relation matrix, distance/same-building
weights, and a headline +score.

This module generalizes the classify/resolve/distance-decay/bucket engine that
``app/services/academy_cluster_analysis.py`` pioneered for 학원(academy) subjects, so the
same "nearby store -> category -> relation+weight -> distance-decayed cluster score" pipeline
can drive OTHER verticals (starting with the food_drink archetype: cafe/restaurant/bakery)
without hardcoding academy's VALID_TARGET_MODELS or filenames.

``academy_cluster_analysis.py`` is intentionally left untouched (its own classify_store /
resolve_relation / compute_academy_cluster and dataclasses keep their exact existing
behavior and public API — tests and the academy router import them directly). This module
is a parameterized sibling that reuses the identical JSON shape
(``categories`` with mcls_codes/scls_codes/scls_names/keywords, and
``target_models``/``rules``/``distance_bands_m``) so future verticals can add a categories
JSON + rules JSON and call ``compute_synergy_cluster`` with no engine changes.

For food_drink (cafe/restaurant/bakery) the default data files are:
  - backend/data/food_drink_synergy_categories.json
  - backend/data/food_drink_synergy_rules.json
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from app.services.sbiz_stores import StoreRecord

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Default data files for the food_drink archetype (cafe/restaurant/bakery).
FOOD_DRINK_CATEGORIES_FILE = "food_drink_synergy_categories.json"
FOOD_DRINK_RULES_FILE = "food_drink_synergy_rules.json"

VALID_FOOD_DRINK_TARGETS = frozenset({"cafe", "restaurant", "bakery"})

RELATION_TO_CLUSTER = {
    "direct_competitor": "direct",
    "partial_competitor": "partial",
    "synergy": "synergy",
    "infrastructure": "infra",
    "arts_synergy": "arts_synergy",
    "risk": "risk",
    "severe_risk": "severe_risk",
}

CLUSTER_LABELS_DEFAULT = {
    "direct": "직접 경쟁",
    "partial": "부분 경쟁",
    "synergy": "보완 시너지",
    "arts_synergy": "운동·웰빙 시너지",
    "infra": "생활 인프라",
    "risk": "주의 요인",
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

# Relation groups that count toward the positive "+score" (complementary synergy).
SYNERGY_RELATIONS = ("synergy", "infrastructure", "arts_synergy")
# Relation groups that count toward the negative pressure score.
PRESSURE_RELATIONS = ("direct_competitor", "partial_competitor")


@dataclass
class SynergyStore:
    name: str
    category: str
    nearby_category: str
    relation: str
    distance_m: float
    same_building: bool
    weight: float
    address: str = ""


@dataclass
class SynergyClusterBucket:
    key: str
    label: str
    relation_group: str
    count: int
    weighted_score: float
    intensity: str
    stores: list[SynergyStore] = field(default_factory=list)


@dataclass
class SynergyScores:
    complementary_synergy: float
    direct_competition_pressure: float


@dataclass
class SynergyResult:
    target: str
    target_label: str
    clusters: list[SynergyClusterBucket]
    scores: SynergyScores
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


def classify_store(
    store: StoreRecord,
    categories_data: dict | None = None,
    *,
    categories_file: str = FOOD_DRINK_CATEGORIES_FILE,
    default_key: str = "generic_food_drink",
) -> str:
    """Classify a nearby StoreRecord into a fine-grained category key.

    Same scoring algorithm as academy_cluster_analysis.classify_store (scls exact code
    match > mcls code match > scls official-name match > keyword substring match), but
    parameterized by the categories JSON file and default fallback key so it isn't
    academy-specific.
    """
    data = categories_data or _load_json(categories_file)
    categories = data.get("categories") or []
    best_key = default_key
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
    target: str,
    nearby_category: str,
    rules_data: dict | None = None,
    *,
    rules_file: str = FOOD_DRINK_RULES_FILE,
) -> tuple[str, float]:
    """Look up (target, nearby_category) -> (relation, weight) from the rules JSON.

    Same behavior as academy_cluster_analysis.resolve_relation: no entry defaults to
    ("synergy", 0.0), which the engine treats as "no measurable relation" and skips.
    """
    data = rules_data or _load_json(rules_file)
    target_rules = (data.get("rules") or {}).get(target) or {}
    entry = target_rules.get(nearby_category)
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


def _normalize_pressure(raw: float) -> float:
    return round(min(100.0, max(0.0, raw * 8.0)), 1)


def _normalize_synergy(raw: float) -> float:
    return round(min(100.0, max(0.0, raw * 12.0)), 1)


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


def compute_synergy_cluster(
    *,
    lat: float,
    lng: float,
    stores: list[StoreRecord],
    target: str,
    radius_m: int = 500,
    bld_mng_no: str | None = None,
    categories_data: dict | None = None,
    rules_data: dict | None = None,
    categories_file: str = FOOD_DRINK_CATEGORIES_FILE,
    rules_file: str = FOOD_DRINK_RULES_FILE,
    valid_targets: frozenset[str] = VALID_FOOD_DRINK_TARGETS,
    default_category_key: str = "generic_food_drink",
    cluster_labels: dict[str, str] | None = None,
) -> SynergyResult:
    """Compute a complementary-business synergy (+score) result for a target archetype.

    Generalizes academy_cluster_analysis.compute_academy_cluster: classify every nearby
    store into a category, resolve (target, category) -> relation+weight, apply
    distance-decay (with a same-building multiplier), bucket by relation group, and
    produce a headline 0-100 ``complementary_synergy`` score (higher = better, e.g. an
    office tower or gym next door to a salad cafe) alongside a
    ``direct_competition_pressure`` score for parity/diagnostics.
    """
    if target not in valid_targets:
        raise ValueError(f"target must be one of {sorted(valid_targets)}")

    categories_data = categories_data or _load_json(categories_file)
    rules_data = rules_data or _load_json(rules_file)
    labels = cluster_labels or CLUSTER_LABELS_DEFAULT
    target_meta = (rules_data.get("target_models") or {}).get(target) or {}
    target_label = target_meta.get("label") or target
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
            "대체 추정했습니다. 동일건물 판정(경쟁·시너지 가중치)이 실제와 다를 수 "
            "있습니다."
        )

    cluster_entries: dict[str, list[SynergyStore]] = {k: [] for k in CLUSTER_ORDER}
    pressure_raw = 0.0
    synergy_raw = 0.0

    for store in stores:
        if store.lat is None or store.lon is None:
            continue
        dist = _haversine_m(lat, lng, store.lat, store.lon)
        if dist > radius_m:
            continue

        nearby_cat = classify_store(
            store,
            categories_data,
            categories_file=categories_file,
            default_key=default_category_key,
        )
        relation, type_weight = resolve_relation(
            target, nearby_cat, rules_data, rules_file=rules_file
        )
        if type_weight == 0.0 and relation == "synergy":
            continue

        same_bld = bool(
            site_bld and store.bld_mng_no and site_bld == store.bld_mng_no
        )
        dist_w = _distance_weight(dist, bands, same_building=same_bld, relation=relation)
        weighted = abs(type_weight) * dist_w
        cluster_key = RELATION_TO_CLUSTER.get(relation, "synergy")

        entry = SynergyStore(
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

        if relation in PRESSURE_RELATIONS:
            pressure_raw += weighted
        elif relation in SYNERGY_RELATIONS:
            synergy_raw += weighted

    clusters: list[SynergyClusterBucket] = []
    for key in CLUSTER_ORDER:
        entries = cluster_entries.get(key) or []
        entries.sort(key=lambda e: (not e.same_building, e.distance_m))
        weighted_sum = round(sum(e.weight for e in entries), 2)
        clusters.append(
            SynergyClusterBucket(
                key=key,
                label=labels.get(key, key),
                relation_group=key,
                count=len(entries),
                weighted_score=weighted_sum,
                intensity=_intensity_label(weighted_sum),
                stores=entries,
            )
        )

    pressure = _normalize_pressure(pressure_raw)
    synergy = _normalize_synergy(synergy_raw)

    complementary_bucket_keys = ("synergy", "arts_synergy", "infra")
    complementary_n = sum(
        c.count for c in clusters if c.key in complementary_bucket_keys
    )
    direct_bucket = next((c for c in clusters if c.key == "direct"), None)
    direct_n = direct_bucket.count if direct_bucket else 0

    headline_parts = [f"{target_label} 기준 보완업체 시너지 분석"]
    if complementary_n:
        headline_parts.append(f"보완 시설 {complementary_n}곳 (+{synergy}점)")
    if direct_n:
        headline_parts.append(f"직접 경쟁 {direct_n}곳")
    headline = " · ".join(headline_parts)

    interpretation: list[str] = []
    if synergy >= 50 and pressure < 40:
        interpretation.append(
            "오피스·헬스장·병원·대학 등 배후 수요를 보내줄 보완업체가 풍부하고 직접 경쟁은 "
            "상대적으로 적습니다. +시너지를 살릴 수 있는 입지입니다."
        )
    elif synergy >= 50 and pressure >= 40:
        interpretation.append(
            "보완업체 시너지는 높지만 동일 업종 직접 경쟁도 함께 몰려 있습니다. "
            "차별화 없이는 시너지만으로 우위를 만들기 어렵습니다."
        )
    elif synergy < 20:
        interpretation.append(
            "인근에 오피스·헬스장·병원·대학·환승시설 등 보완업체 밀도가 낮습니다. "
            "배후 수요를 직접 확인하거나 다른 채널(배달권 등)을 함께 고려하세요."
        )
    else:
        interpretation.append(
            "보완업체 시너지가 보통 수준입니다. 어떤 보완 카테고리가 가장 강한지 "
            "버킷별 목록에서 확인하세요."
        )

    return SynergyResult(
        target=target,
        target_label=target_label,
        clusters=clusters,
        scores=SynergyScores(
            complementary_synergy=synergy,
            direct_competition_pressure=pressure,
        ),
        headline=headline,
        interpretation=interpretation,
        warnings=warnings,
    )
