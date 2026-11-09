"""국토부 RTMS 매매 실거래 → real_estate_trades PostGIS 적재.

상업업무용(NrgTrade)·오피스텔(OffiTrade)·아파트(AptTrade)를 시군구×월 단위로
수집하고 Kakao geocoding으로 좌표를 붙여 적재한다. 재실행 안전(upsert):
같은 거래는 해제여부(cdealType)만 갱신되므로 최근 몇 달을 주기적으로
다시 돌리면 해제·지연신고가 반영된다.

좌표는 ① 법정동코드+지번 → PNU → parcel_centroids(연속지적도) 로컬 조인,
② 실패 시 Kakao geocoding fallback 순으로 붙인다. 지적도가 적재돼 있으면
외부 API 호출 없이 전국 단위도 빠르게 처리된다.

Run: cd backend && .venv/Scripts/python -m tools.ingest_real_estate_trades
     (기본: 서울 25개 구, 최근 12개월, commercial+officetel)
     예: ... --lawd 11410 --months-back 3 --types commercial officetel
     전국: ... --nationwide  (legal_dong_codes 적재 선행 필요)
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time

import psycopg2
import psycopg2.extras

from app.config import settings
from app.services.geocoding import GeocodingError, geocode_address_detail
from app.services.real_estate import (
    ApartmentTrade,
    CommercialTrade,
    OfficetelTrade,
    RealEstateError,
    TradeRecord,
    _format_seoul_address,
    _recent_deal_months,
    fetch_apartment_trades_for_month,
    fetch_officetel_trades_for_month,
    fetch_trades_for_month,
)

SEOUL_LAWD_CDS: dict[str, str] = {
    "11110": "종로구", "11140": "중구", "11170": "용산구", "11200": "성동구",
    "11215": "광진구", "11230": "동대문구", "11260": "중랑구", "11290": "성북구",
    "11305": "강북구", "11320": "도봉구", "11350": "노원구", "11380": "은평구",
    "11410": "서대문구", "11440": "마포구", "11470": "양천구", "11500": "강서구",
    "11530": "구로구", "11545": "금천구", "11560": "영등포구", "11590": "동작구",
    "11620": "관악구", "11650": "서초구", "11680": "강남구", "11710": "송파구",
    "11740": "강동구",
}

_FETCHERS = {
    "commercial": fetch_trades_for_month,
    "apartment": fetch_apartment_trades_for_month,
    "officetel": fetch_officetel_trades_for_month,
}

GEOCODE_DELAY_SEC = 0.04  # Kakao Local QPS 여유
# 한 구의 월별 RTMS fetch를 동시에(각 fetch는 내부적으로 순차 페이지네이션).
# RTMS 30 TPS 제한을 넘지 않도록 동시 요청 수를 제한한다.
MONTH_FETCH_CONCURRENCY = 6

_INSERT_SQL = """
INSERT INTO real_estate_trades (
  property_type, lawd_cd, sgg_nm, umd_nm, jibun, bldg_nm,
  deal_date, deal_amount_manwon, area_sqm, plottage_ar, floor, build_year,
  building_use, building_type, land_use, share_dealing_type,
  cdeal_type, cdeal_day, dealing_gbn, price_per_sqm, dup_seq,
  geocode_query, geo_precision, lon, lat, geom_5179
)
VALUES %s
ON CONFLICT ON CONSTRAINT real_estate_trades_dedup_key DO UPDATE SET
  cdeal_type = EXCLUDED.cdeal_type,
  cdeal_day = EXCLUDED.cdeal_day,
  dealing_gbn = EXCLUDED.dealing_gbn,
  -- 지적도 조인 성공(parcel)은 기존 Kakao 좌표보다 정밀 → 재실행 시 승급
  geocode_query = CASE
    WHEN EXCLUDED.geo_precision = 'parcel'
     AND real_estate_trades.geo_precision <> 'parcel'
    THEN EXCLUDED.geocode_query ELSE real_estate_trades.geocode_query END,
  lon = CASE
    WHEN EXCLUDED.geo_precision = 'parcel'
     AND real_estate_trades.geo_precision <> 'parcel'
    THEN EXCLUDED.lon ELSE real_estate_trades.lon END,
  lat = CASE
    WHEN EXCLUDED.geo_precision = 'parcel'
     AND real_estate_trades.geo_precision <> 'parcel'
    THEN EXCLUDED.lat ELSE real_estate_trades.lat END,
  geom_5179 = CASE
    WHEN EXCLUDED.geo_precision = 'parcel'
     AND real_estate_trades.geo_precision <> 'parcel'
    THEN EXCLUDED.geom_5179 ELSE real_estate_trades.geom_5179 END,
  geo_precision = CASE
    WHEN EXCLUDED.geo_precision = 'parcel'
     AND real_estate_trades.geo_precision <> 'parcel'
    THEN EXCLUDED.geo_precision ELSE real_estate_trades.geo_precision END,
  ingested_at = NOW()
"""
_VALUES_TEMPLATE = (
    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
    " %s, %s, %s, %s, %s,"
    " ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179))"
)

# 질의 문자열 → (lon, lat) 성공 / None 실패. 실행 간 재사용은 DB preload로.
_geo_cache: dict[str, tuple[float, float] | None] = {}

# (lawd_cd, 법정동명) → 법정동코드 10자리. PNU 생성용.
_dong_lookup: dict[tuple[str, str], str] = {}

_JIBUN_RE = re.compile(r"(\d{1,4})(?:-(\d{1,4}))?")


def _preload_geo_cache(cur) -> None:
    cur.execute(
        "SELECT DISTINCT geocode_query, lon, lat FROM real_estate_trades "
        "WHERE lon IS NOT NULL AND geocode_query <> ''"
    )
    for query, lon, lat in cur.fetchall():
        _geo_cache.setdefault(query, (lon, lat))


def _preload_dong_lookup(cur) -> None:
    cur.execute("SELECT lawd_cd, lowest_name, region_cd FROM legal_dong_codes")
    for lawd, lowest, code in cur.fetchall():
        _dong_lookup.setdefault((lawd, lowest), code)


def _build_pnu(lawd_cd: str, umd_nm: str, jibun: str) -> str | None:
    """실거래 (시군구코드, 법정동명, 지번) → PNU 19자리. 마스킹/불일치는 None."""
    code10 = _dong_lookup.get((lawd_cd, umd_nm.strip()))
    if not code10:
        return None
    j = jibun.strip()
    if not j or "*" in j:
        return None
    san = "1"
    if j.startswith("산"):
        san = "2"
        j = j[1:].strip()
    m = _JIBUN_RE.fullmatch(j)
    if not m:
        return None
    bun, ji = int(m.group(1)), int(m.group(2) or 0)
    return f"{code10}{san}{bun:04d}{ji:04d}"


def _parcel_lonlat(cur, pnu: str) -> tuple[float, float] | None:
    cur.execute(
        "SELECT ST_X(ST_Transform(geom_5179, 4326)), "
        "ST_Y(ST_Transform(geom_5179, 4326)) "
        "FROM parcel_centroids WHERE pnu = %s",
        (pnu,),
    )
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def _nationwide_lawd_cds(cur) -> list[str]:
    """동/리 레벨 코드가 존재하는 모든 시군구 5자리 (시도·시군구 자체 행 제외)."""
    cur.execute(
        "SELECT DISTINCT lawd_cd FROM legal_dong_codes "
        "WHERE substr(region_cd, 6, 5) <> '00000' ORDER BY 1"
    )
    return [r[0] for r in cur.fetchall()]


async def _geocode_with_retry(query: str, retries: int = 3):
    """Kakao geocoding with retry on transient network errors.

    GeocodingError(결과 없음)는 즉시 전파. 그 외(네트워크/DNS/httpx)는 백오프 재시도하고,
    소진되면 GeocodingError로 변환해 배치가 크래시하지 않게 한다.
    """
    for attempt in range(retries):
        try:
            return await geocode_address_detail(query)
        except GeocodingError:
            raise
        except Exception as exc:  # noqa: BLE001 — 네트워크 등 일시 오류
            if attempt == retries - 1:
                raise GeocodingError(f"network fail after {retries}x: {exc}") from exc
            await asyncio.sleep(1.0 * (attempt + 1))
    raise GeocodingError("unreachable")


async def _geocode_trade(
    cur, trade: TradeRecord, lawd_cd: str, use_parcels: bool
) -> tuple[float | None, float | None, str, str]:
    """Return (lon, lat, geo_precision, matched_query).

    1순위: PNU → parcel_centroids 로컬 조인 (precision='parcel')
    2순위: Kakao geocoding (exact/approx)
    """
    if use_parcels:
        pnu = _build_pnu(lawd_cd, trade.umd_nm, trade.jibun)
        if pnu:
            hit = _parcel_lonlat(cur, pnu)
            if hit:
                return hit[0], hit[1], "parcel", f"pnu:{pnu}"

    umd_level = _format_seoul_address(trade.sgg_nm, trade.umd_nm)
    for query in trade.geocode_fallback_queries():
        precision = "approx" if query == umd_level else "exact"
        cached = _geo_cache.get(query)
        if cached is not None:
            return cached[0], cached[1], precision, query
        if query in _geo_cache:  # 이전에 실패한 질의
            continue
        try:
            result = await _geocode_with_retry(query)
        except GeocodingError:
            # no-result 또는 재시도 소진된 네트워크 오류 → 이 질의는 건너뜀(크래시 방지).
            _geo_cache[query] = None
            await asyncio.sleep(GEOCODE_DELAY_SEC)
            continue
        _geo_cache[query] = (result.lng, result.lat)
        await asyncio.sleep(GEOCODE_DELAY_SEC)
        return result.lng, result.lat, precision, query
    return None, None, "none", ""


def _dedup_key(trade: TradeRecord, lawd_cd: str) -> tuple:
    if isinstance(trade, CommercialTrade):
        bldg_nm, area = "", trade.building_ar
    else:
        bldg_nm = trade.apt_nm if isinstance(trade, ApartmentTrade) else trade.offi_nm
        area = trade.exclu_use_ar
    return (
        trade.property_type, lawd_cd, trade.umd_nm, trade.jibun, bldg_nm,
        trade.deal_date, trade.deal_amount, trade.floor, area,
    )


def _row_for(
    trade: TradeRecord, lawd_cd: str, lon, lat, precision: str, query: str, dup_seq: int
) -> tuple:
    if isinstance(trade, CommercialTrade):
        bldg_nm, area, plottage = "", trade.building_ar, trade.plottage_ar
        building_type, land_use = trade.building_type, trade.land_use
        share = trade.share_dealing_type
        building_use = trade.building_use
    else:
        assert isinstance(trade, (ApartmentTrade, OfficetelTrade))
        bldg_nm = trade.apt_nm if isinstance(trade, ApartmentTrade) else trade.offi_nm
        area, plottage = trade.exclu_use_ar, None
        building_type, land_use, share, building_use = "집합", "", "", ""
    return (
        trade.property_type, lawd_cd, trade.sgg_nm, trade.umd_nm, trade.jibun,
        bldg_nm, trade.deal_date, trade.deal_amount, area, plottage,
        trade.floor, trade.build_year, building_use, building_type, land_use,
        share, trade.cdeal_type, trade.cdeal_day, trade.dealing_gbn,
        trade.price_per_sqm, dup_seq, query, precision, lon, lat, lon, lat,
    )


async def ingest(
    lawd_cds: list[str] | None,
    months_back: int,
    property_types: list[str],
    *,
    skip_geocode: bool = False,
) -> None:
    t0 = time.perf_counter()
    months = _recent_deal_months(months_back)

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    if skip_geocode:
        use_parcels = False
    else:
        _preload_geo_cache(cur)
        _preload_dong_lookup(cur)
        cur.execute("SELECT count(*) FROM parcel_centroids")
        use_parcels = cur.fetchone()[0] > 0
    if lawd_cds is None:
        lawd_cds = _nationwide_lawd_cds(cur)
        if not lawd_cds:
            raise SystemExit(
                "--nationwide에는 legal_dong_codes가 필요합니다: "
                "python -m tools.ingest_legal_dong_codes 먼저 실행"
            )
    if skip_geocode:
        print(
            f"geocoding SKIPPED (--skip-geocode) | "
            f"{len(lawd_cds)} 시군구 × {months_back}개월"
        )
    else:
        print(
            f"geo cache {len(_geo_cache):,} | dong codes {len(_dong_lookup):,} | "
            f"parcels {'ON' if use_parcels else 'OFF (Kakao fallback only)'} | "
            f"{len(lawd_cds)} 시군구 × {months_back}개월"
        )

    total = geo_fail = fetch_fail = 0
    fetch_sem = asyncio.Semaphore(MONTH_FETCH_CONCURRENCY)
    for lawd_cd in lawd_cds:
        gu = SEOUL_LAWD_CDS.get(lawd_cd, lawd_cd)
        for ptype in property_types:
            fetcher = _FETCHERS[ptype]

            async def _fetch_month(
                ymd: str, fetcher=fetcher, lawd_cd=lawd_cd, gu=gu, ptype=ptype
            ) -> list[TradeRecord] | None:
                async with fetch_sem:
                    try:
                        return await fetcher(lawd_cd, ymd)
                    except RealEstateError as e:
                        print(f"  WARN {gu} {ptype} {ymd}: {e}")
                        return None  # 실패 표시 (fetch_fail 집계용)

            # 월별 fetch를 동시에 (순차 300회 → 구별 동시 fetch로 대폭 단축)
            month_results = await asyncio.gather(*(_fetch_month(y) for y in months))
            trades: list[TradeRecord] = []
            for r in month_results:
                if r is None:
                    fetch_fail += 1
                else:
                    trades.extend(r)

            batch: list[tuple] = []
            gu_geo_fail = 0
            seq_counter: dict[tuple, int] = {}
            for trade in trades:
                if skip_geocode:
                    lon, lat, precision, query = None, None, "none", ""
                else:
                    lon, lat, precision, query = await _geocode_trade(
                        cur, trade, lawd_cd, use_parcels
                    )
                if lon is None:
                    gu_geo_fail += 1
                key = _dedup_key(trade, lawd_cd)
                dup_seq = seq_counter.get(key, 0)
                seq_counter[key] = dup_seq + 1
                batch.append(
                    _row_for(trade, lawd_cd, lon, lat, precision, query, dup_seq)
                )
            if batch:
                psycopg2.extras.execute_values(
                    cur, _INSERT_SQL, batch, template=_VALUES_TEMPLATE, page_size=500
                )
                conn.commit()
            total += len(batch)
            geo_fail += gu_geo_fail
            print(f"  {gu} {ptype}: {len(batch):,} trades (geo fail {gu_geo_fail})")

    cur.execute("SELECT property_type, count(*) FROM real_estate_trades GROUP BY 1")
    counts = dict(cur.fetchall())
    conn.close()
    dt = time.perf_counter() - t0
    print(
        f"DONE in {dt:.0f}s: upserted {total:,} rows "
        f"(geo fail {geo_fail}, fetch fail {fetch_fail}); DB totals: {counts}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lawd", nargs="*", default=list(SEOUL_LAWD_CDS),
        help="시군구 코드 목록 (기본: 서울 25개 구)",
    )
    parser.add_argument(
        "--nationwide", action="store_true",
        help="전국 모든 시군구 (legal_dong_codes 기반; --lawd 무시)",
    )
    parser.add_argument("--months-back", type=int, default=12)
    parser.add_argument(
        "--types", nargs="*", default=["commercial", "officetel"],
        choices=list(_FETCHERS),
    )
    parser.add_argument(
        "--skip-geocode", action="store_true",
        help=(
            "지오코딩(카카오/지적도) 생략 — lon/lat/geom_5179는 NULL. "
            "동 단위 집계만 쓰는 소비자(예: housing_market 시세 벤치마크)는 "
            "좌표가 필요 없어 API 왕복만으로 즉시 끝난다. 지도 표시용 "
            "property_market은 좌표가 필요하므로 이 플래그를 쓰지 말 것."
        ),
    )
    args = parser.parse_args()
    asyncio.run(
        ingest(
            None if args.nationwide else args.lawd,
            args.months_back,
            args.types,
            skip_geocode=args.skip_geocode,
        )
    )
