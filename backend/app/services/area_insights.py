"""반경 기반 동네 인사이트 — 주변 아파트 실거래 시세 + 생활인구(유동인구).

결과 패널 표시용 경량 조회. 아파트 시세는 ``real_estate_trades``(RTMS,
tools/ingest_real_estate_trades.py 적재)에서 반경 내 최근 12개월 거래를,
유동인구는 ``living_pop_slot``(서울열린데이터 생활인구, living_pop.py)을
그대로 재사용한다. 데이터가 없으면 각 필드는 None — 하드 의존성이 아니다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.services.spatial_query import get_connection

_APT_TRADES_SQL = """
WITH pt AS (
  SELECT ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179) AS g
)
SELECT bldg_nm, umd_nm, area_sqm, floor, deal_amount_manwon, deal_date,
       price_per_sqm
FROM real_estate_trades, pt
WHERE property_type = 'apartment'
  AND cdeal_type != 'O'
  AND price_per_sqm IS NOT NULL
  AND deal_date >= (CURRENT_DATE - interval '12 months')
  AND ST_DWithin(geom_5179, pt.g, %s)
ORDER BY deal_date DESC
"""


@dataclass
class AptRecentTrade:
    bldg_nm: str
    umd_nm: str
    area_sqm: float
    floor: int | None
    deal_amount_manwon: int
    deal_date: str


@dataclass
class ApartmentPriceStats:
    trade_count: int
    median_per_sqm_manwon: float | None
    median_per_pyeong_manwon: float | None
    scope: str = ""
    # 어디서 나온 값인지 기계가 읽을 수 있게. scope 문자열("반경 500m" / "강남구
    # 전체")을 화면에서 파싱하면 문구를 손볼 때마다 조용히 깨진다.
    scope_kind: str = ""  # "radius" | "gu" | "" (데이터 없음)
    recent: list[AptRecentTrade] = field(default_factory=list)


# real_estate_trades.price_per_sqm 은 원/㎡ 단위다 (만원 아님).
_WON_PER_MANWON = 10_000
_SQM_PER_PYEONG = 3.3058

_DONG_AT_POINT_SQL = """
SELECT adm_cd, adm_nm
FROM sgis_dong_boundary
WHERE ST_Contains(geom, ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179))
LIMIT 1
"""

_GU_TRADES_SQL = """
SELECT price_per_sqm
FROM real_estate_trades
WHERE property_type = 'apartment'
  AND lawd_cd = %s
  AND cdeal_type != 'O'
  AND price_per_sqm IS NOT NULL
  AND deal_date >= (CURRENT_DATE - interval '12 months')
"""


def _stats_from_per_sqm_won(
    values: list[float], count: int, scope: str, recent: list[AptRecentTrade], scope_kind: str = ""
) -> ApartmentPriceStats:
    median_won_sqm = statistics.median(values) if values else None
    median_manwon_sqm = median_won_sqm / _WON_PER_MANWON if median_won_sqm is not None else None
    return ApartmentPriceStats(
        trade_count=count,
        median_per_sqm_manwon=round(median_manwon_sqm, 0) if median_manwon_sqm is not None else None,
        median_per_pyeong_manwon=round(median_manwon_sqm * _SQM_PER_PYEONG, 0) if median_manwon_sqm is not None else None,
        scope=scope,
        scope_kind=scope_kind,
        recent=recent,
    )


@dataclass
class FootfallBreakdown:
    adm_nm: str
    quarter: str
    total: int
    male: int
    female: int
    ages: list[tuple[str, int]] = field(default_factory=list)  # ("10대", n) … ("60대+", n)
    days: list[tuple[str, int]] = field(default_factory=list)  # ("월", n) … ("일", n)


_FOOTFALL_SQL = """
SELECT adm_nm, yyq, total_footfall, detail
FROM dong_footfall
WHERE adm_nm = %s AND adm_cd LIKE %s
ORDER BY yyq DESC
LIMIT 1
"""

_AGE_KEYS = [
    ("10대", "연령대_10_유동인구_수"),
    ("20대", "연령대_20_유동인구_수"),
    ("30대", "연령대_30_유동인구_수"),
    ("40대", "연령대_40_유동인구_수"),
    ("50대", "연령대_50_유동인구_수"),
    ("60대+", "연령대_60_이상_유동인구_수"),
]
_DAY_KEYS = [
    ("월", "월요일_유동인구_수"), ("화", "화요일_유동인구_수"), ("수", "수요일_유동인구_수"),
    ("목", "목요일_유동인구_수"), ("금", "금요일_유동인구_수"), ("토", "토요일_유동인구_수"),
    ("일", "일요일_유동인구_수"),
]


def fetch_footfall_breakdown(lat: float, lng: float) -> FootfallBreakdown | None:
    """행정동 단위 유동인구 성별·연령대·요일 분해 (서울 상권분석 길단위인구).

    dong_footfall.adm_cd(행정동코드)와 SGIS 경계 코드는 체계가 달라서, 점이
    속한 SGIS 행정동의 '동 이름 + 구(lawd_cd) 프리픽스'로 매칭한다.
    """
    from app.services.geocoding import REGION_LAWD_CD

    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(_DONG_AT_POINT_SQL, (lng, lat))
            dong = cur.fetchone()
            if not dong:
                return None
            parts = str(dong[1]).split()  # 예: 서울특별시 송파구 잠실6동
            if len(parts) < 2:
                return None
            dong_name = parts[-1]
            lawd_cd = REGION_LAWD_CD.get(parts[-2], "")
            cur.execute(_FOOTFALL_SQL, (dong_name, f"{lawd_cd}%"))
            row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None

    detail = row[3] or {}

    def num(key: str) -> int:
        try:
            return int(float(detail.get(key, 0) or 0))
        except (TypeError, ValueError):
            return 0

    yyq = str(row[1])
    quarter = f"{yyq[:4]}년 {yyq[4:]}분기" if len(yyq) == 5 else yyq
    return FootfallBreakdown(
        adm_nm=str(row[0]),
        quarter=quarter,
        total=int(row[2] or 0),
        male=num("남성_유동인구_수"),
        female=num("여성_유동인구_수"),
        ages=[(label, num(key)) for label, key in _AGE_KEYS],
        days=[(label, num(key)) for label, key in _DAY_KEYS],
    )


def fetch_apartment_prices(
    lat: float, lng: float, radius_m: int, recent_limit: int = 4
) -> ApartmentPriceStats | None:
    """반경 내 최근 12개월 아파트 매매 통계.

    아파트 거래의 좌표(geom) 적재율이 지역별로 다르므로, 반경 안에서 거래가
    안 잡히면 그 지점이 속한 구(lawd_cd) 전체 중앙값으로 폴백하고 scope에
    표기한다. 테이블/DB가 없으면 None.
    """
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(_APT_TRADES_SQL, (lng, lat, radius_m))
            rows = cur.fetchall()
            if rows:
                per_sqm = [float(r[6]) for r in rows if r[6] is not None]
                recent = [
                    AptRecentTrade(
                        bldg_nm=str(r[0] or "").strip() or "아파트",
                        umd_nm=str(r[1] or "").strip(),
                        area_sqm=float(r[2] or 0),
                        floor=int(r[3]) if r[3] is not None else None,
                        deal_amount_manwon=int(r[4] or 0),
                        deal_date=r[5].isoformat() if r[5] else "",
                    )
                    for r in rows[:recent_limit]
                ]
                return _stats_from_per_sqm_won(per_sqm, len(rows), f"반경 {radius_m}m", recent, "radius")

            # 반경 내 좌표 있는 거래 없음 → 구 단위 폴백.
            cur.execute(_DONG_AT_POINT_SQL, (lng, lat))
            dong = cur.fetchone()
            if not dong:
                return ApartmentPriceStats(0, None, None, scope="")
            from app.services.housing_market import _lawd_cd_from_adm

            lawd_cd = _lawd_cd_from_adm(str(dong[0]), str(dong[1]))
            if not lawd_cd:
                return ApartmentPriceStats(0, None, None, scope="")
            cur.execute(_GU_TRADES_SQL, (lawd_cd,))
            gu_rows = cur.fetchall()
            if not gu_rows:
                return ApartmentPriceStats(0, None, None, scope="")
            gu_name = str(dong[1]).split()[-2] if len(str(dong[1]).split()) >= 2 else str(dong[1])
            return _stats_from_per_sqm_won(
                [float(r[0]) for r in gu_rows], len(gu_rows), f"{gu_name} 전체", [], "gu"
            )
    except Exception:
        return None
