"""주변 매매 실거래 comparable 조회 (real_estate_trades PostGIS).

수익성 분석의 시장 컨텍스트: 대상 물건과 '같은 종류'의 최근 거래만 골라
₩/㎡ P25/P50/P75 밴드를 만든다. 원칙:

- 거래유형 엄격 분리 — 집합(구분호실)과 일반(통건물)은 절대 섞지 않는다.
- 해제(cdealType=O)·지분·직거래·동중심(approx) 좌표 거래는 밴드에서 제외하고
  제외 사유별 건수만 보고한다.
- 표본이 부족해도 몰래 섞지 않는다: 완화 단계(면적→층 필터 해제, 반경 확대)를
  명시적으로 기록해 반환한다. 필터 유지가 반경 축소보다 우선한다(대체성 원칙).
- 시점 보정 없음 — 최근 N개월 원자료임을 as_of/time_adjusted로 명시한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from app.services.spatial_query import get_connection

RADIUS_LADDER_M = (500, 1000, 2000)
MIN_SAMPLE = 8
MAX_COMPARABLES = 50
AREA_TOLERANCE = 0.20

PropertyKind = Literal["commercial", "officetel"]
FloorBucket = Literal["basement", "first", "upper", "unknown"]

_INT_RE = re.compile(r"^-?\d+$")

DATA_SOURCE = "국토교통부 실거래가(RTMS) 스냅샷 · 시점 미보정"


def floor_bucket(floor: str | None) -> FloorBucket:
    """층 문자열 → 버킷. 상가는 1층/지하/상층 단가 차이가 커서 반드시 분리."""
    f = (floor or "").strip()
    if not _INT_RE.match(f):
        return "unknown"
    n = int(f)
    if n < 0:
        return "basement"
    if n == 1:
        return "first"
    return "upper"


_FLOOR_BUCKET_SQL: dict[str, str] = {
    "basement": r"floor ~ '^-'",
    "first": r"floor = '1'",
    "upper": r"floor ~ '^\d+$' AND floor <> '1'",
}


@dataclass
class ComparableTrade:
    deal_date: str
    price_per_sqm: float
    deal_amount_manwon: int
    area_sqm: float
    floor: str
    umd_nm: str
    bldg_nm: str
    building_type: str
    lat: float
    lng: float
    distance_m: float


@dataclass
class MarketBand:
    property_type: PropertyKind
    building_type: str
    n: int
    radius_m: int
    months_back: int
    p25: float | None
    p50: float | None
    p75: float | None
    min_deal_date: str | None
    max_deal_date: str | None
    relaxations: list[str] = field(default_factory=list)
    insufficient_sample: bool = False
    excluded: dict[str, int] = field(default_factory=dict)
    comparables: list[ComparableTrade] = field(default_factory=list)
    subject_price_per_sqm: float | None = None
    subject_percentile: float | None = None
    time_adjusted: bool = False
    as_of: str = ""
    data_source: str = DATA_SOURCE


def _filter_levels(
    property_type: PropertyKind,
    bucket: FloorBucket,
    area_sqm: float | None,
) -> list[tuple[bool, bool, list[str]]]:
    """(use_floor, use_area, relaxation_labels) — 엄격한 조합부터.

    상가 구분호실은 층 혼합이 면적 혼합보다 왜곡이 커서 층 필터를 나중에 푼다.
    오피스텔은 층 영향이 작아 층 필터를 걸지 않는다.
    """
    use_floor = property_type == "commercial" and bucket != "unknown"
    use_area = area_sqm is not None

    levels: list[tuple[bool, bool, list[str]]] = [(use_floor, use_area, [])]
    if use_area:
        levels.append((use_floor, False, ["면적 ±20% 필터 해제"]))
    if use_floor:
        relax = ["면적 ±20% 필터 해제"] if use_area else []
        levels.append((False, False, relax + ["층 버킷 필터 해제"]))
    # 중복 조합 제거 (use_area=False && use_floor=False가 이미 첫 레벨인 경우 등)
    seen: set[tuple[bool, bool]] = set()
    out: list[tuple[bool, bool, list[str]]] = []
    for lv in levels:
        key = (lv[0], lv[1])
        if key not in seen:
            seen.add(key)
            out.append(lv)
    return out


def _build_where(
    property_type: PropertyKind,
    building_type: str,
    months_back: int,
    use_floor: bool,
    bucket: FloorBucket,
    use_area: bool,
    area_sqm: float | None,
) -> tuple[str, list]:
    """밴드 표본용 WHERE 절과 파라미터 (반경 조건 제외)."""
    clauses = [
        "property_type = %s",
        "building_type = %s",
        "cdeal_type <> 'O'",
        "share_dealing_type = ''",
        "dealing_gbn <> '직거래'",
        "geo_precision IN ('exact', 'parcel')",
        "price_per_sqm IS NOT NULL",
        "deal_date >= CURRENT_DATE - make_interval(months => %s)",
    ]
    params: list = [property_type, building_type, months_back]
    if use_floor and bucket in _FLOOR_BUCKET_SQL:
        clauses.append(f"({_FLOOR_BUCKET_SQL[bucket]})")
    if use_area and area_sqm is not None:
        clauses.append("area_sqm BETWEEN %s AND %s")
        params.extend(
            [area_sqm * (1 - AREA_TOLERANCE), area_sqm * (1 + AREA_TOLERANCE)]
        )
    return " AND ".join(clauses), params


_POINT_SQL = "ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179)"


def fetch_market_band(
    lat: float,
    lng: float,
    property_type: PropertyKind,
    *,
    building_type: str = "집합",
    floor: str | None = None,
    area_sqm: float | None = None,
    months_back: int = 12,
    subject_price_manwon: int | None = None,
) -> MarketBand:
    bucket = floor_bucket(floor)
    levels = _filter_levels(property_type, bucket, area_sqm)

    with get_connection() as conn:
        with conn.cursor() as cur:
            chosen = None  # (where, params, radius, relaxations, n)
            for use_floor, use_area, relaxations in levels:
                where, params = _build_where(
                    property_type, building_type, months_back,
                    use_floor, bucket, use_area, area_sqm,
                )
                for radius in RADIUS_LADDER_M:
                    cur.execute(
                        f"SELECT count(*) FROM real_estate_trades "
                        f"WHERE {where} AND ST_DWithin(geom_5179, {_POINT_SQL}, %s)",
                        params + [lng, lat, radius],
                    )
                    n = cur.fetchone()[0]
                    if chosen is None or n > chosen[4]:
                        radius_relax = (
                            [f"반경 {radius}m로 확대"] if radius != RADIUS_LADDER_M[0] else []
                        )
                        chosen = (where, params, radius, relaxations + radius_relax, n)
                    if n >= MIN_SAMPLE:
                        break
                if chosen[4] >= MIN_SAMPLE:
                    break

            where, params, radius, relaxations, n = chosen
            geo_params = [lng, lat, radius]

            band = MarketBand(
                property_type=property_type,
                building_type=building_type,
                n=n,
                radius_m=radius,
                months_back=months_back,
                p25=None, p50=None, p75=None,
                min_deal_date=None, max_deal_date=None,
                relaxations=relaxations,
                insufficient_sample=n < MIN_SAMPLE,
                as_of=date.today().isoformat(),
            )

            if n > 0:
                cur.execute(
                    f"""
                    SELECT
                      percentile_cont(0.25) WITHIN GROUP (ORDER BY price_per_sqm),
                      percentile_cont(0.50) WITHIN GROUP (ORDER BY price_per_sqm),
                      percentile_cont(0.75) WITHIN GROUP (ORDER BY price_per_sqm),
                      min(deal_date)::text, max(deal_date)::text
                    FROM real_estate_trades
                    WHERE {where} AND ST_DWithin(geom_5179, {_POINT_SQL}, %s)
                    """,
                    params + geo_params,
                )
                p25, p50, p75, dmin, dmax = cur.fetchone()
                band.p25, band.p50, band.p75 = (
                    round(p25, 0), round(p50, 0), round(p75, 0)
                )
                band.min_deal_date, band.max_deal_date = dmin, dmax

                cur.execute(
                    f"""
                    SELECT deal_date::text, price_per_sqm, deal_amount_manwon,
                           area_sqm, floor, umd_nm, bldg_nm, building_type,
                           lat, lon,
                           ST_Distance(geom_5179, {_POINT_SQL}) AS dist
                    FROM real_estate_trades
                    WHERE {where} AND ST_DWithin(geom_5179, {_POINT_SQL}, %s)
                    ORDER BY deal_date DESC, dist ASC
                    LIMIT %s
                    """,
                    [lng, lat] + params + geo_params + [MAX_COMPARABLES],
                )
                band.comparables = [
                    ComparableTrade(
                        deal_date=r[0],
                        price_per_sqm=float(r[1]),
                        deal_amount_manwon=int(r[2]),
                        area_sqm=float(r[3]),
                        floor=r[4],
                        umd_nm=r[5],
                        bldg_nm=r[6],
                        building_type=r[7],
                        lat=float(r[8]) if r[8] is not None else 0.0,
                        lng=float(r[9]) if r[9] is not None else 0.0,
                        distance_m=round(float(r[10]), 1),
                    )
                    for r in cur.fetchall()
                ]

                if subject_price_manwon and area_sqm:
                    pps = subject_price_manwon * 10_000 / area_sqm
                    band.subject_price_per_sqm = round(pps, 0)
                    cur.execute(
                        f"""
                        SELECT count(*) FILTER (WHERE price_per_sqm < %s), count(*)
                        FROM real_estate_trades
                        WHERE {where} AND ST_DWithin(geom_5179, {_POINT_SQL}, %s)
                        """,
                        [pps] + params + geo_params,
                    )
                    below, total = cur.fetchone()
                    if total:
                        band.subject_percentile = round(below / total * 100, 1)

            # 제외 사유별 건수 (같은 반경·타입·기간, 밴드 필터와 무관하게 집계)
            cur.execute(
                f"""
                SELECT
                  count(*) FILTER (WHERE cdeal_type = 'O')            AS cancelled,
                  count(*) FILTER (WHERE share_dealing_type <> '')    AS share_deal,
                  count(*) FILTER (WHERE dealing_gbn = '직거래')       AS direct_deal,
                  count(*) FILTER (WHERE geo_precision NOT IN ('exact','parcel'))
                                                                      AS approx_geo,
                  count(*) FILTER (WHERE building_type <> %s)         AS other_building_type
                FROM real_estate_trades
                WHERE property_type = %s
                  AND deal_date >= CURRENT_DATE - make_interval(months => %s)
                  AND ST_DWithin(geom_5179, {_POINT_SQL}, %s)
                """,
                [building_type, property_type, months_back] + geo_params,
            )
            row = cur.fetchone()
            band.excluded = {
                "cancelled": row[0],
                "share_deal": row[1],
                "direct_deal": row[2],
                "approx_geo": row[3],
                "other_building_type": row[4],
            }

    return band
