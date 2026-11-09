"""국토부 오피스텔 전월세 실거래(OffiRent) → officetel_rents PostGIS 적재.

매매 적재(ingest_real_estate_trades)와 동일한 geocoding 파이프라인을 재사용한다:
PNU→parcel_centroids 로컬 조인 우선, Kakao fallback, 마스킹 지번은 동 중심(approx).
재실행 안전(upsert). OffiRent 활용신청 승인 후 실행 가능(미승인 시 403).

Run: cd backend && .venv/Scripts/python -m tools.ingest_officetel_rents
     (기본: 서울 25개 구, 최근 12개월)
     예: ... --lawd 11410 --months-back 3
     전국: ... --nationwide  (legal_dong_codes 적재 선행 필요)
"""

from __future__ import annotations

import argparse
import asyncio
import time

import psycopg2
import psycopg2.extras

from app.config import settings
from app.services.real_estate import (
    OfficetelRent,
    RealEstateError,
    fetch_officetel_rents_for_month,
)

# 매매 적재기의 geocoding·법정동·PNU 헬퍼를 그대로 재사용
from tools.ingest_real_estate_trades import (
    MONTH_FETCH_CONCURRENCY,
    SEOUL_LAWD_CDS,
    _geocode_trade,
    _nationwide_lawd_cds,
    _preload_dong_lookup,
    _preload_geo_cache,
    _geo_cache,
)
from app.services.real_estate import _recent_deal_months

_INSERT_SQL = """
INSERT INTO officetel_rents (
  lawd_cd, sgg_nm, umd_nm, jibun, bldg_nm, deal_date,
  deposit_manwon, monthly_rent_manwon, is_jeonse, area_sqm, floor, build_year,
  contract_type, contract_term, use_rr_right,
  pre_deposit_manwon, pre_monthly_rent_manwon, monthly_rent_per_sqm,
  geocode_query, geo_precision, lon, lat, dup_seq, geom_5179
)
VALUES %s
ON CONFLICT ON CONSTRAINT officetel_rents_dedup_key DO UPDATE SET
  contract_type = EXCLUDED.contract_type,
  use_rr_right = EXCLUDED.use_rr_right,
  ingested_at = NOW()
"""
_VALUES_TEMPLATE = (
    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
    " %s, %s, %s, %s, %s,"
    " ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179))"
)


def _dedup_key(r: OfficetelRent, lawd_cd: str) -> tuple:
    return (
        lawd_cd, r.umd_nm, r.jibun, r.offi_nm, r.deal_date,
        r.deposit, r.monthly_rent, r.floor, r.exclu_use_ar,
    )


def _row_for(r: OfficetelRent, lawd_cd, lon, lat, precision, query, dup_seq) -> tuple:
    return (
        lawd_cd, r.sgg_nm, r.umd_nm, r.jibun, r.offi_nm, r.deal_date,
        r.deposit, r.monthly_rent, r.is_jeonse, r.exclu_use_ar, r.floor,
        r.build_year, r.contract_type, r.contract_term, r.use_rr_right,
        r.pre_deposit, r.pre_monthly_rent, r.monthly_rent_per_sqm,
        query, precision, lon, lat, dup_seq, lon, lat,
    )


async def ingest(
    lawd_cds: list[str] | None, months_back: int
) -> None:
    t0 = time.perf_counter()
    months = _recent_deal_months(months_back)

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    _preload_geo_cache(cur)  # 매매 좌표 캐시 재사용 (같은 건물 다수 공유)
    _preload_dong_lookup(cur)
    cur.execute("SELECT count(*) FROM parcel_centroids")
    use_parcels = cur.fetchone()[0] > 0
    if lawd_cds is None:
        lawd_cds = _nationwide_lawd_cds(cur)
        if not lawd_cds:
            raise SystemExit(
                "--nationwide에는 legal_dong_codes가 필요합니다: "
                "python -m tools.ingest_legal_dong_codes --txt 먼저 실행"
            )
    print(
        f"geo cache {len(_geo_cache):,} | parcels "
        f"{'ON' if use_parcels else 'OFF'} | {len(lawd_cds)} 시군구 × {months_back}개월"
    )

    total = geo_fail = fetch_fail = jeonse = 0
    fetch_sem = asyncio.Semaphore(MONTH_FETCH_CONCURRENCY)
    for lawd_cd in lawd_cds:
        gu = SEOUL_LAWD_CDS.get(lawd_cd, lawd_cd)

        async def _fetch_month(ymd: str, lawd_cd=lawd_cd, gu=gu):
            async with fetch_sem:
                try:
                    return await fetch_officetel_rents_for_month(lawd_cd, ymd)
                except RealEstateError as e:
                    print(f"  WARN {gu} {ymd}: {e}")
                    return None

        # 월별 fetch 동시 실행 (순차 대비 대폭 단축)
        month_results = await asyncio.gather(*(_fetch_month(y) for y in months))
        rents: list[OfficetelRent] = []
        for r in month_results:
            if r is None:
                fetch_fail += 1
            else:
                rents.extend(r)

        batch: list[tuple] = []
        seq_counter: dict[tuple, int] = {}
        gu_geo_fail = 0
        for r in rents:
            lon, lat, precision, query = await _geocode_trade(
                cur, r, lawd_cd, use_parcels
            )
            if lon is None:
                gu_geo_fail += 1
            if r.is_jeonse:
                jeonse += 1
            key = _dedup_key(r, lawd_cd)
            dup_seq = seq_counter.get(key, 0)
            seq_counter[key] = dup_seq + 1
            batch.append(_row_for(r, lawd_cd, lon, lat, precision, query, dup_seq))
        if batch:
            psycopg2.extras.execute_values(
                cur, _INSERT_SQL, batch, template=_VALUES_TEMPLATE, page_size=500
            )
            conn.commit()
        total += len(batch)
        geo_fail += gu_geo_fail
        print(f"  {gu}: {len(batch):,} rents (geo fail {gu_geo_fail})")

    cur.execute("SELECT count(*), count(*) FILTER (WHERE is_jeonse) FROM officetel_rents")
    n, n_jeonse = cur.fetchone()
    conn.close()
    print(
        f"DONE in {time.perf_counter() - t0:.0f}s: upserted {total:,} "
        f"(전세 {jeonse}, geo fail {geo_fail}, fetch fail {fetch_fail}); "
        f"DB total {n:,} ({n_jeonse} 전세)"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lawd", nargs="*", default=list(SEOUL_LAWD_CDS))
    parser.add_argument("--nationwide", action="store_true")
    parser.add_argument("--months-back", type=int, default=12)
    args = parser.parse_args()
    asyncio.run(ingest(None if args.nationwide else args.lawd, args.months_back))
