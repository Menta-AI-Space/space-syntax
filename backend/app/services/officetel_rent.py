"""오피스텔 월세 참고 밴드 (국토부 OffiRent 전월세 실거래).

상가 임대료는 정부 실거래가 없어 R-ONE 지역통계를 쓰지만(reb_rent.py), 오피스텔은
전월세 신고 실거래가 있으므로 물건 단위 반경 comparable로 월세 밴드를 만든다.
매매 comparable(property_market.fetch_market_band)과 같은 반경·면적 필터 방식.
전세(monthly_rent=0)는 제외하고 월세만 집계한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.property_market import RADIUS_LADDER_M, MIN_SAMPLE, AREA_TOLERANCE
from app.services.spatial_query import get_connection

DATA_SOURCE = "국토교통부 오피스텔 전월세 실거래(월세, 반경 comparable)"

_POINT_SQL = "ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179)"


@dataclass
class OfficetelRentBand:
    n: int
    radius_m: int
    months_back: int
    monthly_rent_p25_manwon: float | None
    monthly_rent_p50_manwon: float | None
    monthly_rent_p75_manwon: float | None
    deposit_median_manwon: float | None
    min_deal_date: str | None
    max_deal_date: str | None
    relaxations: list[str] = field(default_factory=list)
    insufficient_sample: bool = False
    data_source: str = DATA_SOURCE


def build_rent_where(
    months_back: int, area_bounds: tuple[float, float] | None
) -> tuple[str, list]:
    """월세 밴드 WHERE 절 + 파라미터 (반경 조건 제외). 순수 함수(테스트용).

    월세만(전세 제외), 좌표 정확(exact/parcel), 최근 N개월. area_bounds가 있으면 면적 필터.
    """
    clauses = [
        "monthly_rent_manwon > 0",
        "geo_precision IN ('exact', 'parcel')",
        "deal_date >= CURRENT_DATE - make_interval(months => %s)",
    ]
    params: list = [months_back]
    if area_bounds is not None:
        clauses.append("area_sqm BETWEEN %s AND %s")
        params += [area_bounds[0], area_bounds[1]]
    return " AND ".join(clauses), params


def area_bounds_for(area_sqm: float | None) -> tuple[float, float] | None:
    if area_sqm is None:
        return None
    return (area_sqm * (1 - AREA_TOLERANCE), area_sqm * (1 + AREA_TOLERANCE))


def fetch_officetel_rent_band(
    lat: float,
    lng: float,
    area_sqm: float | None = None,
    months_back: int = 12,
) -> OfficetelRentBand:
    """반경 내 오피스텔 월세 실거래 → P25/P50/P75 밴드(만원) + 보증금 중위."""
    use_area = area_sqm is not None
    bounds = area_bounds_for(area_sqm)

    def where_and_params(with_area: bool) -> tuple[str, list]:
        return build_rent_where(months_back, bounds if with_area else None)

    with get_connection() as conn:
        with conn.cursor() as cur:
            chosen = None  # (with_area, radius, n)
            for with_area in ([True, False] if use_area else [False]):
                where, params = where_and_params(with_area)
                for radius in RADIUS_LADDER_M:
                    cur.execute(
                        f"SELECT count(*) FROM officetel_rents "
                        f"WHERE {where} AND ST_DWithin(geom_5179, {_POINT_SQL}, %s)",
                        params + [lng, lat, radius],
                    )
                    n = cur.fetchone()[0]
                    if chosen is None or n > chosen[2]:
                        chosen = (with_area, radius, n)
                    if n >= MIN_SAMPLE:
                        break
                if chosen[2] >= MIN_SAMPLE:
                    break

            with_area, radius, n = chosen
            relaxations: list[str] = []
            if use_area and not with_area:
                relaxations.append("면적 ±20% 필터 해제")
            if radius != RADIUS_LADDER_M[0]:
                relaxations.append(f"반경 {radius}m로 확대")

            band = OfficetelRentBand(
                n=n, radius_m=radius, months_back=months_back,
                monthly_rent_p25_manwon=None, monthly_rent_p50_manwon=None,
                monthly_rent_p75_manwon=None, deposit_median_manwon=None,
                min_deal_date=None, max_deal_date=None,
                relaxations=relaxations, insufficient_sample=n < MIN_SAMPLE,
            )
            if n > 0:
                where, params = where_and_params(with_area)
                cur.execute(
                    f"""
                    SELECT
                      percentile_cont(0.25) WITHIN GROUP (ORDER BY monthly_rent_manwon),
                      percentile_cont(0.50) WITHIN GROUP (ORDER BY monthly_rent_manwon),
                      percentile_cont(0.75) WITHIN GROUP (ORDER BY monthly_rent_manwon),
                      percentile_cont(0.50) WITHIN GROUP (ORDER BY deposit_manwon),
                      min(deal_date)::text, max(deal_date)::text
                    FROM officetel_rents
                    WHERE {where} AND ST_DWithin(geom_5179, {_POINT_SQL}, %s)
                    """,
                    params + [lng, lat, radius],
                )
                p25, p50, p75, dep, dmin, dmax = cur.fetchone()
                band.monthly_rent_p25_manwon = round(p25, 0) if p25 is not None else None
                band.monthly_rent_p50_manwon = round(p50, 0) if p50 is not None else None
                band.monthly_rent_p75_manwon = round(p75, 0) if p75 is not None else None
                band.deposit_median_manwon = round(dep, 0) if dep is not None else None
                band.min_deal_date, band.max_deal_date = dmin, dmax
    return band
