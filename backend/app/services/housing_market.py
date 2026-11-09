"""Apartment sale price benchmark from RTMS (proxy for housing market level)."""

from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass

from app.services.geocoding import REGION_LAWD_CD, infer_lawd_cd_from_b_code
from app.services.real_estate import (
    ApartmentTrade,
    RealEstateConfigError,
    RealEstateError,
    fetch_recent_apartment_trades,
)

_TRADES_TTL_S = 6 * 3600
_trades_cache: dict[str, tuple[float, list[ApartmentTrade]]] = {}
_trades_inflight: dict[str, asyncio.Task] = {}


@dataclass
class _DbTradeRow:
    """real_estate_trades에서 읽은 최소 필드 — 중앙값 계산에 쓰는 것만."""

    umd_nm: str
    price_per_sqm: float | None


_DB_TRADES_SQL = """
SELECT umd_nm, price_per_sqm
FROM real_estate_trades
WHERE property_type = 'apartment'
  AND lawd_cd = %s
  AND deal_date >= (CURRENT_DATE - (%s || ' months')::interval)
  AND cdeal_type != 'O'
  AND price_per_sqm IS NOT NULL
"""


def _trades_from_db(lawd_cd: str, months_back: int) -> list[_DbTradeRow]:
    """로컬 real_estate_trades 조회 — 미적재/오류 시 빈 리스트 (API 폴백)."""
    from app.services.spatial_query import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_DB_TRADES_SQL, (lawd_cd, months_back))
                rows = cur.fetchall()
    except Exception:
        return []
    return [_DbTradeRow(umd_nm=r[0], price_per_sqm=r[1]) for r in rows]


async def _trades_for_lawd(lawd_cd: str, months_back: int):
    """아파트 실거래 — 로컬 real_estate_trades 우선, 없으면 RTMS API + 캐시.

    로컬 테이블은 tools/ingest_real_estate_trades.py --types apartment가 채운다.
    API 폴백은 RTMS가 12개월 구 조회에 30s+ 걸리는 반면 site-score는 8s만
    기다리므로, shield된 태스크로 돌려 첫 요청이 타임아웃돼도 다운로드는
    완주해 캐시를 채운다 — 같은 구의 다음 요청부터 즉시 응답.
    """
    db_rows = await asyncio.to_thread(_trades_from_db, lawd_cd, months_back)
    if db_rows:
        return db_rows

    key = f"{lawd_cd}:{months_back}"
    cached = _trades_cache.get(key)
    if cached and time.monotonic() - cached[0] < _TRADES_TTL_S:
        return cached[1]

    task = _trades_inflight.get(key)
    if task is None or task.done():
        task = asyncio.create_task(
            fetch_recent_apartment_trades(lawd_cd=lawd_cd, months_back=months_back)
        )
        _trades_inflight[key] = task

        def _store(t: asyncio.Task, key: str = key) -> None:
            _trades_inflight.pop(key, None)
            if not t.cancelled() and t.exception() is None:
                _trades_cache[key] = (time.monotonic(), t.result())

        task.add_done_callback(_store)
    return await asyncio.shield(task)


@dataclass
class ApartmentMarket:
    median_price_per_sqm: float | None
    sample_count: int
    scope: str
    # 절대 시세(만원/㎡)만 주면 일반인이 높은지 낮은지 감을 못 잡는다. 서울 전체
    # 중앙값 대비 몇 %인지를 함께 줘 소비 여력을 직관적으로 읽게 한다.
    vs_seoul_pct: int | None = None
    seoul_median_per_sqm: float | None = None


def pct_vs_seoul(local_per_sqm_manwon: float | None, seoul_median_manwon: float | None) -> int | None:
    """지역 아파트 시세가 서울 중앙값 대비 몇 % 높은지(음수면 낮음). 표본 없으면 None."""
    if not local_per_sqm_manwon or not seoul_median_manwon or seoul_median_manwon <= 0:
        return None
    return round((local_per_sqm_manwon / seoul_median_manwon - 1) * 100)


_SEOUL_MEDIAN_TTL_S = 24 * 3600
_seoul_median_cache: dict[str, tuple[float, float | None]] = {}
_seoul_median_inflight: dict[str, asyncio.Task] = {}

_SEOUL_MEDIAN_SQL = """
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_sqm)
FROM real_estate_trades
WHERE property_type = 'apartment'
  AND lawd_cd LIKE '11%%'
  AND deal_date >= (CURRENT_DATE - (%s || ' months')::interval)
  AND cdeal_type != 'O'
  AND price_per_sqm IS NOT NULL
"""


def _seoul_median_from_db(months_back: int) -> float | None:
    """서울 전체 아파트 실거래 중앙값(만원/㎡). 미적재/오류 시 None."""
    from app.services.spatial_query import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_SEOUL_MEDIAN_SQL, (months_back,))
                row = cur.fetchone()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    return round(float(row[0]) / 10_000, 1)  # 원/㎡ → 만원/㎡


async def seoul_apartment_median_per_sqm(months_back: int = 12) -> float | None:
    """서울 전체 아파트 중앙 시세(만원/㎡), 24시간 캐시. 상대% 기준값.

    캐시가 찰 때까지 기다리지 않는다 — site-score는 8초 예산 안에 응답해야
    하므로(_trades_for_lawd와 같은 이유), 캐시 미스면 백그라운드로 채우고
    이번 요청엔 즉시 None을 반환한다. 다음 요청부터 채워진 값이 나온다.
    """
    key = str(months_back)
    cached = _seoul_median_cache.get(key)
    if cached and time.monotonic() - cached[0] < _SEOUL_MEDIAN_TTL_S:
        return cached[1]

    task = _seoul_median_inflight.get(key)
    if task is None or task.done():
        task = asyncio.create_task(asyncio.to_thread(_seoul_median_from_db, months_back))
        _seoul_median_inflight[key] = task

        def _store(t: asyncio.Task, key: str = key) -> None:
            _seoul_median_inflight.pop(key, None)
            if not t.cancelled() and t.exception() is None:
                _seoul_median_cache[key] = (time.monotonic(), t.result())

        task.add_done_callback(_store)
    return None


def _emdong_name(adm_name: str) -> str:
    parts = adm_name.strip().split()
    return parts[-1] if parts else ""


def _lawd_cd_from_adm(adm_cd: str, adm_name: str) -> str | None:
    """Map SGIS dong to RTMS 5-digit 시군구 code.

    SGIS adm_cd prefix (e.g. 11080…) does not match RTMS LAWD_CD (11410 for 서대문구).
    Prefer gu name lookup, then 10-digit b-code style prefix when valid.
    """
    for gu, lawd in REGION_LAWD_CD.items():
        if gu in adm_name:
            return lawd
    if len(adm_cd) >= 10 and adm_cd[:5].isdigit():
        return infer_lawd_cd_from_b_code(adm_cd[:10])
    if len(adm_cd) >= 5 and adm_cd[:5].isdigit():
        prefix = adm_cd[:5]
        # RTMS 서울 시군구 codes are 11xxx; reject obvious SGIS-only prefixes.
        if prefix.startswith("11") and prefix not in ("11080", "11070"):
            return prefix
    return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


async def fetch_apartment_market(
    adm_cd: str,
    adm_name: str,
    *,
    months_back: int = 12,
) -> ApartmentMarket | None:
    """Median apartment sale price per sqm for the emdong (fallback: gu)."""
    lawd_cd = _lawd_cd_from_adm(adm_cd, adm_name)
    if not lawd_cd:
        return ApartmentMarket(
            median_price_per_sqm=None,
            sample_count=0,
            scope=f"{adm_name} — 시군구 코드 매핑 실패",
        )

    emdong = _emdong_name(adm_name)
    try:
        trades = await _trades_for_lawd(lawd_cd, months_back)
    except RealEstateConfigError:
        return ApartmentMarket(
            median_price_per_sqm=None,
            sample_count=0,
            scope="RTMS API 키 미설정 (DATA_GO_API_KEY)",
        )
    except RealEstateError as exc:
        return ApartmentMarket(
            median_price_per_sqm=None,
            sample_count=0,
            scope=f"RTMS 조회 실패: {exc}",
        )

    # 서울 전체 중앙값 — 지역 시세를 상대적으로 읽기 위한 기준(캐시라 저렴).
    seoul_med = await seoul_apartment_median_per_sqm(months_back)

    dong_prices = [
        t.price_per_sqm
        for t in trades
        if t.price_per_sqm is not None
        and emdong
        and (emdong in t.umd_nm or t.umd_nm in emdong)
    ]
    if len(dong_prices) >= 3:
        med = _median(dong_prices)
        med_manwon = round(med / 10_000, 1) if med is not None else None
        return ApartmentMarket(
            median_price_per_sqm=med_manwon,
            sample_count=len(dong_prices),
            scope=f"{emdong} (최근 {months_back}개월 매매)",
            vs_seoul_pct=pct_vs_seoul(med_manwon, seoul_med),
            seoul_median_per_sqm=seoul_med,
        )

    gu_prices = [
        t.price_per_sqm for t in trades if t.price_per_sqm is not None
    ]
    if not gu_prices:
        return ApartmentMarket(
            median_price_per_sqm=None,
            sample_count=0,
            scope=f"{adm_name} — 거래 표본 없음",
        )

    med = _median(gu_prices)
    med_manwon = round(med / 10_000, 1) if med is not None else None
    return ApartmentMarket(
        median_price_per_sqm=med_manwon,
        sample_count=len(gu_prices),
        scope=f"구 단위 ({lawd_cd}, 최근 {months_back}개월 매매)",
        vs_seoul_pct=pct_vs_seoul(med_manwon, seoul_med),
        seoul_median_per_sqm=seoul_med,
    )
