"""상권 임대료·공실률 참고치 조회 (R-ONE 상업용부동산 임대동향조사).

lat/lng → commercial_districts 폴리곤(ST_Contains) → reb_rent_stats.
상가 임대료는 정부 실거래 미공개이므로 이건 '지역 참고치'다 (물건 단위 아님):
수익성 분석의 월세 입력 기본 밴드·공실률 기본값 근거로만 쓰고, 항상 출처·분기를
함께 노출한다. 사용자 입력을 대체하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.spatial_query import get_connection

DATA_SOURCE = "한국부동산원 상업용부동산 임대동향조사(상권 평균, 실거래 아님)"

# 점이 상권 폴리곤 밖일 때 최근접 상권을 참고치로 쓰는 최대 거리(m).
NEAR_MATCH_M = 1000.0

# 수익성 입력의 building_type → 참고할 R-ONE 상가 유형 우선순위.
# 집합(구분호실)→집합상가; 일반(통건물)→규모 미상이라 중대형·소규모 둘 다 참고.
BUILDING_TYPE_TO_REB: dict[str, tuple[str, ...]] = {
    "집합": ("집합",),
    "일반": ("중대형", "소규모"),
}


@dataclass
class RebRentType:
    property_type: str  # 중대형 | 소규모 | 집합
    rent_krw_per_sqm: float | None
    vacancy_pct: float | None


@dataclass
class RebRentReference:
    matched: bool
    sanggwon_nm: str = ""
    sido_cd: str = ""
    quarter: str = ""
    types: list[RebRentType] = field(default_factory=list)
    # 대표값(입력 building_type 기준): 월세 참고 밴드·공실률 기본값
    rent_low_krw_per_sqm: float | None = None
    rent_high_krw_per_sqm: float | None = None
    vacancy_default_pct: float | None = None
    data_source: str = DATA_SOURCE


def fetch_rent_reference(
    lat: float,
    lng: float,
    building_type: str = "집합",
) -> RebRentReference:
    reb_types = BUILDING_TYPE_TO_REB.get(building_type, ("중대형", "소규모", "집합"))

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1순위: 점이 포함된 상권. 없으면 NEAR_MATCH_M 이내 최근접 상권을 참고치로.
            # (상권 폴리곤은 상업 코어만 좁게 그려서, 인근 물건이 폴리곤 밖일 때가 많음)
            cur.execute(
                """
                SELECT sanggwon_nm, sido_cd, 0::float AS dist
                FROM commercial_districts
                WHERE ST_Contains(
                    geom_5179,
                    ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179)
                )
                LIMIT 1
                """,
                (lng, lat),
            )
            row = cur.fetchone()
            near_dist = 0.0
            if not row:
                pt = "ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179)"
                cur.execute(
                    f"""
                    SELECT sanggwon_nm, sido_cd,
                           ST_Distance(geom_5179, {pt}) AS dist
                    FROM commercial_districts
                    WHERE ST_DWithin(geom_5179, {pt}, %s)
                    ORDER BY geom_5179 <-> {pt}
                    LIMIT 1
                    """,
                    (lng, lat, lng, lat, NEAR_MATCH_M, lng, lat),
                )
                row = cur.fetchone()
            if not row:
                return RebRentReference(matched=False)
            sanggwon_nm, sido_cd, near_dist = row

            # 최신 분기
            cur.execute(
                "SELECT max(quarter) FROM reb_rent_stats WHERE sanggwon_nm = %s",
                (sanggwon_nm,),
            )
            quarter = cur.fetchone()[0]
            if not quarter:
                return RebRentReference(
                    matched=True, sanggwon_nm=sanggwon_nm, sido_cd=sido_cd
                )

            cur.execute(
                """
                SELECT property_type, rent_krw_per_sqm, vacancy_pct
                FROM reb_rent_stats
                WHERE sanggwon_nm = %s AND quarter = %s
                ORDER BY property_type
                """,
                (sanggwon_nm, quarter),
            )
            all_types = [
                RebRentType(pt, r, v) for pt, r, v in cur.fetchall()
            ]

    ref = RebRentReference(
        matched=True,
        sanggwon_nm=sanggwon_nm,
        sido_cd=sido_cd,
        quarter=quarter,
        types=all_types,
    )

    low, high, vacancy, data_source = compute_rent_band(
        all_types, reb_types, building_type
    )
    ref.rent_low_krw_per_sqm = low
    ref.rent_high_krw_per_sqm = high
    ref.vacancy_default_pct = vacancy
    if near_dist and near_dist > 0:
        data_source = f"{data_source} · 최근접 상권 기준(약 {round(near_dist)}m)"
    ref.data_source = data_source
    return ref


def compute_rent_band(
    all_types: list[RebRentType],
    reb_types: tuple[str, ...],
    building_type: str,
) -> tuple[float | None, float | None, float | None, str]:
    """상권 유형 목록 → (임대료 low, high, 공실률, data_source). 순수 함수(테스트용).

    입력 building_type에 해당하는 유형에 데이터가 없으면(예: 명동엔 집합상가 조사
    없음) 상권의 가용 유형을 프록시로 쓰고 그 사실을 data_source에 명시한다.
    """
    relevant = [t for t in all_types if t.property_type in reb_types]
    rents = [t.rent_krw_per_sqm for t in relevant if t.rent_krw_per_sqm is not None]
    data_source = DATA_SOURCE
    if not rents:
        relevant = all_types
        rents = [t.rent_krw_per_sqm for t in relevant if t.rent_krw_per_sqm is not None]
        used = ", ".join(
            sorted({t.property_type for t in relevant if t.rent_krw_per_sqm is not None})
        )
        if used:
            data_source = f"{DATA_SOURCE} · {building_type}상가 조사 없어 {used} 기준"
    vacs = [t.vacancy_pct for t in relevant if t.vacancy_pct is not None]
    low = round(min(rents), 0) if rents else None
    high = round(max(rents), 0) if rents else None
    vacancy = round(sum(vacs) / len(vacs), 1) if vacs else None
    return low, high, vacancy, data_source


def monthly_rent_band_manwon(
    ref: RebRentReference, area_sqm: float
) -> tuple[float, float] | None:
    """참고 임대료 밴드(원/㎡) × 전용면적 → 월세 만원 범위."""
    if ref.rent_low_krw_per_sqm is None or ref.rent_high_krw_per_sqm is None:
        return None
    low = ref.rent_low_krw_per_sqm * area_sqm / 10_000
    high = ref.rent_high_krw_per_sqm * area_sqm / 10_000
    return round(low, 0), round(high, 0)
