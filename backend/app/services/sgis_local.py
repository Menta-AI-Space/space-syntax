"""로컬 PostGIS 행정동 스토어에서 DemandProfile 조회 (원격 SGIS API 대체).

sgis_dong_boundary / sgis_dong_stats는 tools/ingest_sgis_dong.py가 연 1회 적재.
포인트가 경계 밖이거나 테이블이 비어 있으면 None을 반환해 호출측이
원격 SGIS API로 폴백한다. DB 오류도 None으로 강등한다 (서비스 생존 우선).
"""

from __future__ import annotations

import logging

from app.services.sgis import (
    AgeBand,
    DemandProfile,
    GenderStats,
    HousingMix,
    IndustryStats,
)
from app.services.spatial_query import get_connection

logger = logging.getLogger(__name__)

_PROFILE_SQL = """
SELECT b.adm_cd, b.adm_nm, s.stats_year, s.population, s.households,
       s.companies, s.workers, s.avg_age, s.population_density,
       s.age_bands, s.gender, s.housing, s.industries
FROM sgis_dong_boundary b
LEFT JOIN sgis_dong_stats s USING (adm_cd)
WHERE ST_Contains(
    b.geom,
    ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179)
)
LIMIT 1
"""


def _age_bands(raw) -> list[AgeBand]:
    if not raw:
        return []
    return [
        AgeBand(band=b["band"], count=int(b["count"]), pct=float(b["pct"]))
        for b in raw
    ]


def _gender(raw) -> GenderStats | None:
    if not raw:
        return None
    return GenderStats(
        male_pct=raw["male_pct"],
        female_pct=raw["female_pct"],
        male_count=raw.get("male_count"),
        female_count=raw.get("female_count"),
    )


def _housing(raw) -> HousingMix | None:
    if not raw:
        return None
    return HousingMix(
        apartment_pct=raw.get("apartment_pct", 0.0),
        officetel_pct=raw.get("officetel_pct", 0.0),
        row_house_pct=raw.get("row_house_pct", 0.0),
        detached_pct=raw.get("detached_pct", 0.0),
        apartment_count=raw.get("apartment_count"),
        officetel_count=raw.get("officetel_count"),
    )


def _industries(raw) -> list[IndustryStats]:
    if not raw:
        return []
    return [
        IndustryStats(
            code=i["code"],
            name=i["name"],
            companies=int(i.get("companies") or 0),
            workers=int(i.get("workers") or 0),
        )
        for i in raw
    ]


def load_demand_profile_from_db(lat: float, lng: float) -> DemandProfile | None:
    """포인트가 속한 행정동의 DemandProfile — 없으면 None (호출측이 API 폴백)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_PROFILE_SQL, (lng, lat))
                row = cur.fetchone()
    except Exception as e:
        logger.warning("sgis_local lookup failed (falling back to API): %s", e)
        return None

    if row is None:
        return None
    (
        adm_cd,
        adm_nm,
        stats_year,
        population,
        households,
        companies,
        workers,
        avg_age,
        population_density,
        age_bands,
        gender,
        housing,
        industries,
    ) = row
    if population is None and workers is None:
        # 경계만 있고 통계 미적재 — API가 더 나은 답을 줄 수 있다.
        return None

    scope_year = stats_year or "최근"
    return DemandProfile(
        adm_cd=adm_cd,
        adm_name=adm_nm,
        population=population,
        households=households,
        companies=companies,
        workers=workers,
        avg_age=avg_age,
        population_density=population_density,
        age_bands=_age_bands(age_bands),
        gender=_gender(gender),
        housing=_housing(housing),
        industries=_industries(industries),
        stats_year=stats_year,
        data_scope=f"행정동 (총조사 {scope_year})",
    )
