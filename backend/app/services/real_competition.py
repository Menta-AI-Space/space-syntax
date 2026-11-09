"""Weighted real competition analysis — direct, substitute, time-band buckets.

Vertical resolution delegates to ProfileResolver so industry-profile
matchRules are the single taxonomy source; profile ids double as
``competitor_rules_{id}.json`` file names.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.services.isochrone import MODE_LABEL_KO, point_in_reach_polygon
from app.services.sbiz_stores import StoreRecord

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@dataclass
class CompetitorSummary:
    name: str
    category: str
    distance_m: float
    weight: float = 1.0
    subjects: list[str] = field(default_factory=list)
    specialist_count: int | None = None


@dataclass
class CompetitionBucket:
    key: str
    label: str
    count: int
    weighted_score: float
    intensity: str
    nearest: list[CompetitorSummary] = field(default_factory=list)


@dataclass
class RealCompetitionResult:
    vertical: str
    total_weighted_score: float
    total_intensity: str
    buckets: list[CompetitionBucket]
    summary_one_liner: str
    reach_mode: str | None = None
    reach_minutes: int | None = None
    reach_label: str | None = None
    warnings: list[str] = field(default_factory=list)


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _distance_weight(distance_m: float, bands: list[dict]) -> float:
    for band in bands:
        if distance_m <= band["max"]:
            return float(band["weight"])
    return 0.1


def _intensity_label(score: float, thresholds: list[float]) -> str:
    if score >= thresholds[2]:
        return "매우 높음"
    if score >= thresholds[1]:
        return "높음"
    if score >= thresholds[0]:
        return "보통"
    return "낮음"


def _load_rules(vertical: str) -> dict:
    fname = f"competitor_rules_{vertical}.json"
    with (DATA_DIR / fname).open(encoding="utf-8") as f:
        return json.load(f)


def _text_blob(store: StoreRecord) -> str:
    return " ".join(
        [
            store.bizes_nm,
            store.inds_lcls_nm,
            store.inds_mcls_nm,
            store.inds_scls_nm,
        ]
    )


def _matches_bucket(store: StoreRecord, bucket: dict) -> bool:
    mcls = set(bucket.get("mcls_codes") or [])
    scls = set(bucket.get("scls_codes") or [])
    if store.inds_mcls_cd in mcls or store.inds_scls_cd in scls:
        return True
    blob = _text_blob(store)
    for kw in bucket.get("keywords") or []:
        if kw and kw.lower() in blob.lower():
            return True
    return False


@lru_cache(maxsize=1)
def _available_verticals() -> frozenset[str]:
    """competitor_rules_*.json이 존재하는 vertical 집합."""
    return frozenset(
        p.stem.replace("competitor_rules_", "", 1)
        for p in DATA_DIR.glob("competitor_rules_*.json")
    )


@lru_cache(maxsize=1)
def _profile_resolver():
    from app.services.profile_resolver import ProfileResolver

    return ProfileResolver.load()


@lru_cache(maxsize=1)
def _qcode_subject_map() -> dict:
    with (DATA_DIR / "clinic_qcode_subject_map.json").open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _subject_relations() -> dict:
    with (DATA_DIR / "clinic_subject_relations.json").open(encoding="utf-8") as f:
        return json.load(f)


def resolve_subject_codes(inds_scls_cd: str | None) -> list[str] | None:
    """clinic 소분류(Q102xx) → HIRA 진료과목코드 리스트.

    매핑이 없거나(scls 미선택) 매핑은 있지만 코드 단위로 특정 불가능한
    ambiguous catch-all(예: Q10209 기타 의원, subject_codes=[])인 경우 None을
    반환한다 — 호출부는 None이면 기존 SBIZ 기반 경쟁 로직을 그대로 사용해야 한다.
    """
    if not inds_scls_cd:
        return None
    entry = _qcode_subject_map().get(inds_scls_cd)
    if not entry:
        return None
    codes = entry.get("subject_codes") or []
    return codes or None


def _expand_subject_weights(subject_codes: list[str]) -> dict[str, float]:
    """direct subject_codes(가중치 1.0) + clinic_subject_relations.json의 adjacent
    과목(가중치 <1.0)을 합친 {subject_code: weight} 맵. 동일 코드가 여러 direct
    과목의 adjacent로 겹치면 더 높은 가중치를 취한다."""
    relations = _subject_relations()
    weights: dict[str, float] = {c: 1.0 for c in subject_codes}
    for c in subject_codes:
        rel = relations.get(c) or {}
        for adj_code, w in (rel.get("adjacent") or {}).items():
            weights[adj_code] = max(weights.get(adj_code, 0.0), float(w))
    return weights


def _competitor_summary_from_entry(obj, dist_m: float, weight: float) -> CompetitorSummary:
    """assignments의 (obj, dist, score) 튜플 → CompetitorSummary.

    obj는 StoreRecord(SBIZ) 또는 ClinicRecord(HIRA, clinic 직접경쟁 대체 소스) 둘 중 하나다.
    """
    if hasattr(obj, "bizes_nm"):
        return CompetitorSummary(
            name=obj.bizes_nm,
            category=obj.inds_scls_nm or obj.inds_mcls_nm,
            distance_m=round(dist_m, 0),
            weight=round(weight, 2),
        )
    return CompetitorSummary(
        name=obj.name,
        category=obj.cl_name or "의료기관",
        distance_m=round(dist_m, 0),
        weight=round(weight, 2),
        subjects=[s.subject_name for s in obj.subjects if s.subject_name],
        specialist_count=sum(s.specialist_count or 0 for s in obj.subjects) or None,
    )


def _hira_direct_entries(
    *,
    lat: float,
    lng: float,
    radius_m: int,
    subject_codes: list[str],
    bands: list[dict],
    bucket_defs: list[dict],
    use_polygon: bool,
    reachable_polygon: dict | None,
) -> list[tuple]:
    """clinic 수직 direct 버킷을 HIRA 진료과목 기반으로 채운다 (SBIZ 키워드 버킷 대체).

    호출부(compute_real_competition)에서 HiraError를 잡아 폴백 처리한다.
    """
    from app.services.hira_clinics import fetch_clinics_in_radius

    weights = _expand_subject_weights(subject_codes)
    # reach-polygon 모드는 radius_m보다 훨씬 넓은 권역을 커버할 수 있으므로(예: 대중교통
    # 40분 도달권) 후보 조회 반경을 넉넉히 잡는다. 8000m는 competition_fetch_radius_m의
    # 상한과 동일한 값을 재사용한 것.
    fetch_radius = max(radius_m, 8000) if use_polygon else radius_m
    clinics = fetch_clinics_in_radius(lat, lng, fetch_radius, subject_codes=list(weights))

    direct_bdef = next((b for b in bucket_defs if b["key"] == "direct"), None)
    type_w = float((direct_bdef or {}).get("weight") or 1.0)

    entries = []
    for c in clinics:
        if use_polygon:
            if not point_in_reach_polygon(c.lon, c.lat, reachable_polygon):
                continue
        elif c.distance_m > radius_m:
            continue
        match_w = max((weights.get(s.subject_code, 0.0) for s in c.subjects), default=0.0)
        if match_w <= 0:
            continue
        dist_w = _distance_weight(c.distance_m, bands)
        score = type_w * match_w * dist_w
        entries.append((c, c.distance_m, score))
    entries.sort(key=lambda x: x[1])
    return entries


def resolve_vertical(
    *,
    archetype: str,
    inds_mcls_cd: str | None,
    inds_scls_cd: str | None,
) -> str | None:
    """업종 선택 → 경쟁규칙 vertical. 프로필 matchRules가 단일 기준.

    industry-profiles의 id가 곧 competitor_rules_{id}.json 파일명이다.
    bakery만 예외(전용 규칙은 있지만 별도 프로필 없음 — scls 직접 매핑).
    프로필 matchRules가 못 잡는 코드 프리픽스는 구 하드코딩과 동일하게
    폴백으로 보존한다.
    """
    if inds_scls_cd == "I21001":
        return "bakery"

    profile_id = _profile_resolver().resolve(
        inds_mcls_cd=inds_mcls_cd,
        inds_scls_cd=inds_scls_cd,
        archetype=archetype,
    )
    if profile_id and profile_id in _available_verticals():
        return profile_id

    if inds_mcls_cd:
        if inds_mcls_cd.startswith("Q1"):
            return "clinic"
        if inds_mcls_cd.startswith("P10"):
            return "academy"
        if inds_mcls_cd.startswith("I2") and inds_mcls_cd != "I212":
            return "restaurant"
    return None


def compute_real_competition(
    *,
    lat: float,
    lng: float,
    stores: list[StoreRecord],
    vertical: str,
    radius_m: int = 500,
    reachable_polygon: dict | None = None,
    reach_mode: str | None = None,
    reach_minutes: int | None = None,
    subject_codes: list[str] | None = None,
) -> RealCompetitionResult:
    rules = _load_rules(vertical)
    bands = rules["distance_bands_m"]
    thresholds = rules["intensity_thresholds"]
    bucket_defs = rules["buckets"]
    warnings: list[str] = []
    use_polygon = bool(
        reachable_polygon and reachable_polygon.get("coordinates")
    )
    if reach_mode and reach_minutes and not use_polygon:
        warnings.append(
            f"{MODE_LABEL_KO.get(reach_mode, reach_mode)} {reach_minutes}분 "
            "도달권을 계산하지 못해 반경 기준으로 대체 집계했습니다."
        )

    # clinic 수직에서 선택된 진료과목(subject_codes)이 있으면 direct 버킷을 HIRA
    # 진료과목 기반으로 대체한다 (SBIZ 키워드 버킷보다 specialty-aware). subject_codes가
    # 없거나(사용자가 특정 소분류를 고르지 않음) HIRA 조회가 실패하면(테이블 미적재 등)
    # 기존 SBIZ 기반 direct 버킷 분류로 그대로 폴백한다.
    hira_direct_entries: list[tuple] | None = None
    if vertical == "clinic" and subject_codes:
        from app.services.hira_clinics import HiraError

        try:
            hira_direct_entries = _hira_direct_entries(
                lat=lat,
                lng=lng,
                radius_m=radius_m,
                subject_codes=subject_codes,
                bands=bands,
                bucket_defs=bucket_defs,
                use_polygon=use_polygon,
                reachable_polygon=reachable_polygon,
            )
        except HiraError as e:
            warnings.append(
                f"진료과목별 경쟁 분석에 필요한 HIRA 데이터를 사용할 수 없어 "
                f"기존 방식(SBIZ)으로 대체했습니다: {e}"
            )
            hira_direct_entries = None

    assignments: dict[str, list[tuple]] = {
        b["key"]: [] for b in bucket_defs
    }

    for store in stores:
        if store.lat is None or store.lon is None:
            continue
        dist = _haversine_m(lat, lng, store.lat, store.lon)
        if use_polygon:
            if not point_in_reach_polygon(store.lon, store.lat, reachable_polygon):
                continue
        elif dist > radius_m:
            continue
        dist_w = _distance_weight(dist, bands)
        for bdef in bucket_defs:
            if bdef["key"] == "direct" and hira_direct_entries is not None:
                continue  # direct는 HIRA 소스로 대체됨
            if not _matches_bucket(store, bdef):
                continue
            type_w = float(bdef.get("weight") or 1.0)
            score = type_w * dist_w
            assignments[bdef["key"]].append((store, dist, score))

    if hira_direct_entries is not None:
        assignments["direct"] = hira_direct_entries

    result_buckets: list[CompetitionBucket] = []
    total_score = 0.0
    for bdef in bucket_defs:
        key = bdef["key"]
        entries = assignments.get(key) or []
        entries.sort(key=lambda x: x[1])
        weighted = round(sum(e[2] for e in entries), 2)
        total_score += weighted
        nearest = [
            _competitor_summary_from_entry(e[0], e[1], e[2])
            for e in entries[:25]
        ]
        result_buckets.append(
            CompetitionBucket(
                key=key,
                label=bdef["label"],
                count=len(entries),
                weighted_score=weighted,
                intensity=_intensity_label(weighted, thresholds),
                nearest=nearest,
            )
        )

    direct = next((b for b in result_buckets if b.key == "direct"), None)
    reach_label = None
    parts = []
    if reach_mode and reach_minutes:
        mode_label = MODE_LABEL_KO.get(reach_mode, reach_mode)
        reach_label = f"{mode_label} {reach_minutes}분"
        if direct:
            parts.append(f"{reach_label} 권역 · 직접 {direct.count}곳 · {direct.intensity}")
        else:
            parts.append(f"{reach_label} 권역")
    elif direct:
        parts.append(f"반경 {radius_m}m · 직접 {direct.count}곳 · {direct.intensity}")
    summary = " · ".join(parts) if parts else "반경 내 경쟁 점포를 집계했습니다."

    return RealCompetitionResult(
        vertical=vertical,
        total_weighted_score=round(total_score, 2),
        total_intensity=_intensity_label(total_score, thresholds),
        buckets=result_buckets,
        summary_one_liner=summary,
        reach_mode=reach_mode,
        reach_minutes=reach_minutes,
        reach_label=reach_label,
        warnings=warnings,
    )
