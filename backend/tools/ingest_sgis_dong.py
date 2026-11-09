"""서울 행정동 경계 + 총조사 통계를 SGIS API에서 받아 PostGIS에 적재.

연 1회(총조사 통계 갱신 시) 재실행하면 된다. 단계별로 커밋하므로 도중에
실패해도 이미 적재된 단계는 유지되고, 재실행하면 나머지가 채워진다.

  1단계: 행정동 경계 426 + 인구·가구·사업체·종사자 핵심 통계 (호출 ~51회)
  2단계: 업종 대분류별 사업체/종사자 (구×14 = 350회)
  3단계: 동별 연령/성별/주택 믹스 (426×3 = 1,278회)

SGIS는 과도한 동시 요청 시 연결을 끊으므로(ReadError) 동시성 3 + 재시도.

Run: cd backend && .venv/Scripts/python -m tools.ingest_sgis_dong
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
import psycopg2
import psycopg2.extras

from app.config import settings
from app.services.sgis import (
    MAJOR_INDUSTRY_CODES,
    SGIS_BASE,
    STATS_YEARS,
    SgisError,
    _access_token,
    _get,
    _parse_age_bands,
    _parse_float,
    _parse_int,
)

SEOUL_SIDO_CD = "11"
_CONCURRENCY = 3
_RETRIES = 4


async def _get_retry(
    client: httpx.AsyncClient, path: str, params: dict, context: str
):
    """_get + 네트워크 오류 재시도 (SGIS는 부하 시 연결을 끊는다)."""
    for attempt in range(_RETRIES):
        try:
            return await _get(client, path, params, context)
        except httpx.HTTPError:
            if attempt == _RETRIES - 1:
                raise
            await asyncio.sleep(0.5 * (2**attempt))


async def _fetch_boundaries(
    client: httpx.AsyncClient, token: str
) -> tuple[str, list[dict]]:
    """서울 전체 행정동 경계 GeoJSON (adm_cd 8자리, EPSG:5179 좌표)."""
    last_error: Exception | None = None
    for year in STATS_YEARS:
        try:
            response = await client.get(
                f"{SGIS_BASE}/boundary/hadmarea.geojson",
                params={
                    "accessToken": token,
                    "adm_cd": SEOUL_SIDO_CD,
                    "low_search": "2",
                    "year": year,
                },
            )
            if response.status_code != 200:
                raise SgisError(f"boundary {year}: HTTP {response.status_code}")
            features = response.json().get("features") or []
            if features:
                return year, features
        except (SgisError, ValueError, httpx.HTTPError) as e:
            last_error = e
    raise SgisError(f"boundary: no features for any of {STATS_YEARS}: {last_error}")


async def _stats_rows_for_gu(
    client: httpx.AsyncClient,
    token: str,
    path: str,
    gu_cd: str,
    context: str,
    *,
    extra_params: dict | None = None,
) -> tuple[dict[str, dict], str | None]:
    """구 코드 하나로 소속 동 전체의 통계 행을 받는다 (low_search=1)."""
    for year in STATS_YEARS:
        try:
            params = {
                "accessToken": token,
                "adm_cd": gu_cd,
                "low_search": "1",
                "year": year,
            }
            if extra_params:
                params.update(extra_params)
            result = await _get_retry(client, path, params, context)
            rows = result if isinstance(result, list) else []
            if rows:
                return {str(r.get("adm_cd", "")).strip(): r for r in rows}, year
        except SgisError:
            continue
    return {}, None


async def _startupbiz_row(
    client: httpx.AsyncClient, token: str, path: str, adm_cd: str
) -> dict | None:
    try:
        result = await _get_retry(
            client, path, {"accessToken": token, "adm_cd": adm_cd}, path
        )
        if isinstance(result, list):
            for row in result:
                if str((row or {}).get("adm_cd", "")) == adm_cd:
                    return row
            return result[0] if result else None
        return result if isinstance(result, dict) else None
    except (SgisError, httpx.HTTPError):
        return None


def _gender_from_row(row: dict | None) -> dict | None:
    if not row:
        return None
    male_pct = _parse_float(row.get("m_per"))
    female_pct = _parse_float(row.get("f_per"))
    if male_pct is None or female_pct is None:
        return None
    return {
        "male_pct": male_pct,
        "female_pct": female_pct,
        "male_count": _parse_int(row.get("m_ppl")),
        "female_count": _parse_int(row.get("f_ppl")),
    }


def _housing_from_row(row: dict | None) -> dict | None:
    if not row:
        return None
    apt_pct = _parse_float(row.get("apart_per"))
    off_pct = _parse_float(row.get("officetel_per"))
    row_pct = _parse_float(row.get("row_house_per") or row.get("row_multi_house_per"))
    det_pct = _parse_float(row.get("detach_house_per"))
    if all(v is None for v in (apt_pct, off_pct, row_pct, det_pct)):
        return None
    return {
        "apartment_pct": apt_pct or 0.0,
        "officetel_pct": off_pct or 0.0,
        "row_house_pct": row_pct or 0.0,
        "detached_pct": det_pct or 0.0,
        "apartment_count": _parse_int(row.get("apart_cnt")),
        "officetel_count": _parse_int(row.get("officetel_cnt")),
    }


def _connect():
    """쓰기 직전에 새로 연결한다 — 수집 단계가 수 분 걸리는 동안 열어둔
    커넥션은 서버 재시작/유휴 종료로 끊길 수 있다."""
    return psycopg2.connect(settings.database_url)


def _upsert_core(
    features: list[dict],
    dongs: dict[str, str],
    boundary_year: str,
    pop_by_dong: dict[str, dict],
    comp_by_dong: dict[str, dict],
    stats_year: str | None,
) -> None:
    conn = _connect()
    cur = conn.cursor()
    boundary_rows = [
        (
            str((f.get("properties") or {}).get("adm_cd", "")).strip(),
            dongs[str((f.get("properties") or {}).get("adm_cd", "")).strip()],
            boundary_year,
            json.dumps(f.get("geometry")),
        )
        for f in features
        if str((f.get("properties") or {}).get("adm_cd", "")).strip()
    ]
    psycopg2.extras.execute_batch(
        cur,
        """
        INSERT INTO sgis_dong_boundary (adm_cd, adm_nm, boundary_year, geom, ingested_at)
        VALUES (%s, %s, %s, ST_SetSRID(ST_Multi(ST_GeomFromGeoJSON(%s)), 5179), NOW())
        ON CONFLICT (adm_cd) DO UPDATE SET
          adm_nm = EXCLUDED.adm_nm,
          boundary_year = EXCLUDED.boundary_year,
          geom = EXCLUDED.geom,
          ingested_at = NOW()
        """,
        boundary_rows,
        page_size=100,
    )

    stats_rows = []
    for adm_cd, adm_nm in dongs.items():
        pop_row = pop_by_dong.get(adm_cd, {})
        comp_row = comp_by_dong.get(adm_cd, {})
        stats_rows.append(
            (
                adm_cd,
                adm_nm,
                stats_year,
                _parse_int(pop_row.get("tot_ppltn")),
                _parse_int(pop_row.get("tot_house")),
                _parse_int(comp_row.get("corp_cnt")),
                _parse_int(comp_row.get("tot_worker")),
                _parse_float(pop_row.get("avg_age")),
                _parse_float(pop_row.get("ppltn_dnsty")),
            )
        )
    psycopg2.extras.execute_batch(
        cur,
        """
        INSERT INTO sgis_dong_stats (
          adm_cd, adm_nm, stats_year, population, households, companies, workers,
          avg_age, population_density, ingested_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (adm_cd) DO UPDATE SET
          adm_nm = EXCLUDED.adm_nm,
          stats_year = EXCLUDED.stats_year,
          population = EXCLUDED.population,
          households = EXCLUDED.households,
          companies = EXCLUDED.companies,
          workers = EXCLUDED.workers,
          avg_age = EXCLUDED.avg_age,
          population_density = EXCLUDED.population_density,
          ingested_at = NOW()
        """,
        stats_rows,
        page_size=100,
    )
    conn.commit()
    conn.close()


async def ingest() -> None:
    t0 = time.perf_counter()

    async with httpx.AsyncClient(timeout=60.0) as client:
        token = await _access_token(client)
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def limited(coro):
            async with sem:
                return await coro

        # ---- 1단계: 경계 + 핵심 통계 ----
        boundary_year, features = await _fetch_boundaries(client, token)
        dongs: dict[str, str] = {}
        for feat in features:
            props = feat.get("properties") or {}
            adm_cd = str(props.get("adm_cd", "")).strip()
            adm_nm = str(props.get("adm_nm", "")).strip()
            if adm_cd:
                dongs[adm_cd] = adm_nm
        gu_codes = sorted({cd[:5] for cd in dongs})
        print(f"phase1: boundaries {len(dongs)} dongs / {len(gu_codes)} gus (year {boundary_year})")

        pop_results = await asyncio.gather(
            *(
                limited(
                    _stats_rows_for_gu(client, token, "/stats/population.json", gu, f"pop-{gu}")
                )
                for gu in gu_codes
            )
        )
        comp_results = await asyncio.gather(
            *(
                limited(
                    _stats_rows_for_gu(client, token, "/stats/company.json", gu, f"comp-{gu}")
                )
                for gu in gu_codes
            )
        )
        pop_by_dong: dict[str, dict] = {}
        comp_by_dong: dict[str, dict] = {}
        stats_year: str | None = None
        for rows, year in pop_results:
            pop_by_dong.update(rows)
            stats_year = stats_year or year
        for rows, year in comp_results:
            comp_by_dong.update(rows)
            stats_year = stats_year or year

        _upsert_core(features, dongs, boundary_year, pop_by_dong, comp_by_dong, stats_year)
        print(
            f"phase1: committed core stats — population {len(pop_by_dong)}, "
            f"company {len(comp_by_dong)} (year {stats_year})"
        )

        # ---- 2단계: 업종 대분류 (알파벳 class_code만 동작) ----
        async def industry_for_gu(gu: str, code: str, name: str):
            rows, _ = await _stats_rows_for_gu(
                client,
                token,
                "/stats/company.json",
                gu,
                f"ind-{gu}-{code}",
                extra_params={"class_code": code},
            )
            return code, name, rows

        industry_results = await asyncio.gather(
            *(
                limited(industry_for_gu(gu, code, name))
                for gu in gu_codes
                for code, name in MAJOR_INDUSTRY_CODES
            )
        )
        industries_by_dong: dict[str, list[dict]] = {}
        for code, name, rows in industry_results:
            for adm_cd, row in rows.items():
                corp = _parse_int(row.get("corp_cnt"))
                wrk = _parse_int(row.get("tot_worker"))
                if (corp or 0) <= 0 and (wrk or 0) <= 0:
                    continue
                industries_by_dong.setdefault(adm_cd, []).append(
                    {"code": code, "name": name, "companies": corp or 0, "workers": wrk or 0}
                )
        ind_rows = []
        for adm_cd, items in industries_by_dong.items():
            items.sort(key=lambda i: i["workers"], reverse=True)
            ind_rows.append((json.dumps(items[:8], ensure_ascii=False), adm_cd))
        conn = _connect()
        psycopg2.extras.execute_batch(
            conn.cursor(),
            "UPDATE sgis_dong_stats SET industries = %s WHERE adm_cd = %s",
            ind_rows,
            page_size=100,
        )
        conn.commit()
        conn.close()
        print(f"phase2: committed industries for {len(ind_rows)} dongs")

        # ---- 3단계: 동별 연령/성별/주택 ----
        async def startupbiz_for_dong(adm_cd: str):
            ppl = await _startupbiz_row(client, token, "/startupbiz/pplsummary.json", adm_cd)
            mf = await _startupbiz_row(client, token, "/startupbiz/mfratiosummary.json", adm_cd)
            house = await _startupbiz_row(client, token, "/startupbiz/housesummary.json", adm_cd)
            return adm_cd, ppl, mf, house

        sb_results = await asyncio.gather(
            *(limited(startupbiz_for_dong(cd)) for cd in dongs)
        )

    sb_rows = []
    hits = 0
    for adm_cd, ppl, mf, house in sb_results:
        population = _parse_int((pop_by_dong.get(adm_cd) or {}).get("tot_ppltn"))
        age_bands = (
            [
                {"band": b.band, "count": b.count, "pct": b.pct}
                for b in _parse_age_bands(ppl, population)
            ]
            if ppl
            else []
        )
        if ppl or mf or house:
            hits += 1
        sb_rows.append(
            (
                json.dumps(age_bands, ensure_ascii=False),
                json.dumps(_gender_from_row(mf), ensure_ascii=False),
                json.dumps(_housing_from_row(house), ensure_ascii=False),
                adm_cd,
            )
        )
    conn = _connect()
    cur = conn.cursor()
    psycopg2.extras.execute_batch(
        cur,
        "UPDATE sgis_dong_stats SET age_bands = %s, gender = %s, housing = %s WHERE adm_cd = %s",
        sb_rows,
        page_size=100,
    )
    conn.commit()
    print(f"phase3: committed age/gender/housing — startupbiz hits {hits}/{len(sb_rows)}")

    cur.execute("SELECT count(*) FROM sgis_dong_boundary")
    n_boundary = cur.fetchone()[0]
    cur.execute(
        """
        SELECT count(*), count(workers),
               count(*) FILTER (WHERE age_bands IS NOT NULL AND age_bands != '[]'::jsonb),
               count(*) FILTER (WHERE industries IS NOT NULL AND industries != '[]'::jsonb)
        FROM sgis_dong_stats
        """
    )
    n_stats, n_workers, n_age, n_ind = cur.fetchone()
    conn.close()

    dt = time.perf_counter() - t0
    print(
        f"DONE in {dt:.0f}s: boundaries={n_boundary}, stats={n_stats} "
        f"(workers {n_workers}, age_bands {n_age}, industries {n_ind})"
    )


if __name__ == "__main__":
    try:
        asyncio.run(ingest())
    except SgisError as e:
        print(f"INGEST FAILED: {e}", file=sys.stderr)
        sys.exit(1)
