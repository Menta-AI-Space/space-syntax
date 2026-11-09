"""SGIS (통계청 통계지리정보서비스) client — 행정동 인구·가구·사업체·종사자.

Flow: authentication → reverse geocode (WGS84 → 행정동 코드) → stats lookup.
Requires SGIS_CONSUMER_KEY / SGIS_CONSUMER_SECRET from sgis.kostat.go.kr.
All failures raise SgisError so the caller can degrade gracefully.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

SGIS_BASE = "https://sgisapi.mods.go.kr/OpenAPI3"

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}

# KSIC 10차 대분류 — company.json class_code는 알파벳 대분류만 인식한다
# (숫자 중분류 코드를 넘기면 조용히 0행이 반환된다).
MAJOR_INDUSTRY_CODES = [
    ("C", "제조업"),
    ("F", "건설업"),
    ("G", "도매·소매"),
    ("H", "운수·창고"),
    ("I", "숙박·음식점"),
    ("J", "정보통신"),
    ("K", "금융·보험"),
    ("L", "부동산"),
    ("M", "전문·과학·기술"),
    ("N", "사업시설·지원"),
    ("P", "교육서비스"),
    ("Q", "보건·사회복지"),
    ("R", "예술·스포츠·여가"),
    ("S", "기타 서비스"),
]


class SgisError(Exception):
    pass


class SgisConfigError(SgisError):
    pass


@dataclass
class GenderStats:
    male_pct: float
    female_pct: float
    male_count: int | None = None
    female_count: int | None = None


@dataclass
class HousingMix:
    apartment_pct: float
    officetel_pct: float
    row_house_pct: float
    detached_pct: float
    apartment_count: int | None = None
    officetel_count: int | None = None


@dataclass
class IndustryStats:
    code: str
    name: str
    companies: int
    workers: int


@dataclass
class AgeBand:
    band: str
    count: int
    pct: float


@dataclass
class DemandStats:
    """Backward-compatible subset."""

    adm_cd: str
    adm_name: str
    population: int | None
    households: int | None
    companies: int | None
    workers: int | None


@dataclass
class DemandProfile:
    adm_cd: str
    adm_name: str
    population: int | None
    households: int | None
    companies: int | None
    workers: int | None
    avg_age: float | None = None
    population_density: float | None = None
    age_bands: list[AgeBand] = field(default_factory=list)
    gender: GenderStats | None = None
    housing: HousingMix | None = None
    industries: list[IndustryStats] = field(default_factory=list)
    stats_year: str | None = None
    data_scope: str = "행정동 (총조사)"


def _parse_int(value: Any) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _check_result(payload: dict, context: str) -> Any:
    err_cd = str(payload.get("errCd", payload.get("errcd", "0")))
    if err_cd not in ("0", "00"):
        msg = payload.get("errMsg", payload.get("errmsg", "unknown error"))
        raise SgisError(f"SGIS {context} error {err_cd}: {msg}")
    return payload.get("result")


async def _get(client: httpx.AsyncClient, path: str, params: dict, context: str) -> Any:
    response = await client.get(f"{SGIS_BASE}{path}", params=params)
    if response.status_code != 200:
        raise SgisError(f"SGIS {context} failed: HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as e:
        raise SgisError(f"SGIS {context}: invalid JSON") from e
    return _check_result(payload, context)


async def _access_token(client: httpx.AsyncClient) -> str:
    key = settings.sgis_consumer_key.strip()
    secret = settings.sgis_consumer_secret.strip()
    if not key or not secret:
        raise SgisConfigError(
            "SGIS_CONSUMER_KEY/SGIS_CONSUMER_SECRET not configured. "
            "Register at sgis.kostat.go.kr and add them to .env."
        )

    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    result = await _get(
        client,
        "/auth/authentication.json",
        {"consumer_key": key, "consumer_secret": secret},
        "auth",
    )
    token = (result or {}).get("accessToken")
    if not token:
        raise SgisError("SGIS auth returned no accessToken")
    timeout_ms = _parse_int((result or {}).get("accessTimeout"))
    expires_at = (timeout_ms / 1000 - 60) if timeout_ms else (now + 3 * 3600)
    _token_cache["token"] = token
    _token_cache["expires_at"] = expires_at
    return token


async def get_access_token(client: httpx.AsyncClient) -> str:
    """Public wrapper for benchmark module."""
    return await _access_token(client)


async def _adm_dong_from_point(
    client: httpx.AsyncClient, token: str, lat: float, lng: float
) -> tuple[str, str]:
    result = await _get(
        client,
        "/addr/rgeocodewgs84.json",
        {"accessToken": token, "x_coor": lng, "y_coor": lat, "addr_type": 20},
        "rgeocode",
    )
    items = result if isinstance(result, list) else [result] if result else []
    if not items:
        raise SgisError("SGIS rgeocode returned no result for this point")
    item = items[0] or {}
    sido_cd = str(item.get("sido_cd", "")).strip()
    sgg_cd = str(item.get("sgg_cd", "")).strip()
    emdong_cd = str(item.get("emdong_cd", "")).strip()
    adm_cd = f"{sido_cd}{sgg_cd}{emdong_cd}"
    if not (sido_cd and sgg_cd and emdong_cd):
        raise SgisError("SGIS rgeocode result is missing sido_cd/sgg_cd/emdong_cd")
    parts = [item.get("sido_nm", ""), item.get("sgg_nm", ""), item.get("emdong_nm", "")]
    adm_name = " ".join(p for p in parts if p).strip() or adm_cd
    return adm_cd, adm_name


def _first_row(result: Any, adm_cd: str | None = None) -> dict:
    if isinstance(result, list):
        if adm_cd:
            for row in result:
                if str((row or {}).get("adm_cd", "")) == adm_cd:
                    return row or {}
        if result:
            return result[0] or {}
        return {}
    if isinstance(result, dict):
        return result
    return {}


STATS_YEARS = ["2024", "2023", "2022"]


async def _get_stats_with_year_fallback(
    client: httpx.AsyncClient,
    token: str,
    path: str,
    adm_cd: str,
    context: str,
    *,
    extra_params: dict | None = None,
) -> tuple[dict, str]:
    last_error: SgisError | None = None
    for year in STATS_YEARS:
        try:
            params = {
                "accessToken": token,
                "adm_cd": adm_cd,
                "low_search": "0",
                "year": year,
            }
            if extra_params:
                params.update(extra_params)
            result = await _get(client, path, params, context)
            row = _first_row(result, adm_cd)
            if row:
                return row, year
        except SgisError as e:
            last_error = e
    if last_error is not None:
        raise last_error
    raise SgisError(f"SGIS {context}: no data for any of {STATS_YEARS}")


async def _startupbiz_row(
    client: httpx.AsyncClient, token: str, path: str, adm_cd: str, context: str
) -> dict:
    result = await _get(
        client,
        path,
        {"accessToken": token, "adm_cd": adm_cd},
        context,
    )
    row = _first_row(result, adm_cd)
    if not row:
        raise SgisError(f"SGIS {context}: empty result for adm_cd={adm_cd}")
    return row


def _parse_age_bands(row: dict, total_pop: int | None) -> list[AgeBand]:
    """Parse pplsummary.json age fields into AgeBand list."""
    specs = [
        ("10세 미만", "teenage_less_than_cnt", "teenage_less_than_per"),
        ("10대", "teenage_cnt", "teenage_per"),
        ("20대", "twenty_cnt", "twenty_per"),
        ("30대", "thirty_cnt", "thirty_per"),
        ("40대", "forty_cnt", "forty_per"),
        ("50대", "fifty_cnt", "fifty_per"),
        ("60대", "sixty_cnt", "sixty_per"),
        ("70세+", "seventy_more_than_cnt", "seventy_more_than_per"),
    ]
    bands: list[AgeBand] = []
    for label, cnt_key, pct_key in specs:
        count = _parse_int(row.get(cnt_key))
        pct = _parse_float(row.get(pct_key))
        if count is None and pct is None:
            continue
        if pct is None and count is not None and total_pop and total_pop > 0:
            pct = round(100.0 * count / total_pop, 1)
        bands.append(
            AgeBand(
                band=label,
                count=count or 0,
                pct=round(pct or 0.0, 1),
            )
        )
    return bands


async def _fetch_industries(
    client: httpx.AsyncClient, token: str, adm_cd: str, year: str
) -> list[IndustryStats]:
    async def _one(code: str, name: str) -> IndustryStats | None:
        try:
            result = await _get(
                client,
                "/stats/company.json",
                {
                    "accessToken": token,
                    "adm_cd": adm_cd,
                    "low_search": "0",
                    "year": year,
                    "class_code": code,
                },
                f"company-{code}",
            )
            row = _first_row(result, adm_cd)
            corp = _parse_int(row.get("corp_cnt"))
            wrk = _parse_int(row.get("tot_worker"))
            if corp is None and wrk is None:
                return None
            return IndustryStats(
                code=code,
                name=name,
                companies=corp or 0,
                workers=wrk or 0,
            )
        except SgisError:
            return None

    tasks = [_one(code, name) for code, name in MAJOR_INDUSTRY_CODES]
    results = await asyncio.gather(*tasks)
    industries = [r for r in results if r is not None and (r.companies > 0 or r.workers > 0)]
    industries.sort(key=lambda i: i.workers, reverse=True)
    return industries[:8]


async def fetch_demand_profile(lat: float, lng: float) -> DemandProfile:
    """행정동 단위 배후 수요 프로필 — 로컬 PostGIS 우선, 원격 SGIS API 폴백.

    로컬 스토어는 tools/ingest_sgis_dong.py가 적재한다. DB에 해당 지점의
    행정동이 없거나 DB가 죽어 있으면 기존 원격 API 경로로 내려간다.
    """
    from app.services.sgis_local import load_demand_profile_from_db

    profile = await asyncio.to_thread(load_demand_profile_from_db, lat, lng)
    if profile is not None:
        return profile
    return await _fetch_demand_profile_api(lat, lng)


async def _fetch_demand_profile_api(lat: float, lng: float) -> DemandProfile:
    """원격 SGIS API 경로 (요청당 HTTP 7~20회 — 로컬 미적재 지역 전용)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _access_token(client)
        adm_cd, adm_name = await _adm_dong_from_point(client, token, lat, lng)

        population: int | None = None
        households: int | None = None
        companies: int | None = None
        workers: int | None = None
        avg_age: float | None = None
        population_density: float | None = None
        stats_year: str | None = None

        try:
            row, stats_year = await _get_stats_with_year_fallback(
                client, token, "/stats/population.json", adm_cd, "population"
            )
            population = _parse_int(row.get("tot_ppltn"))
            households = _parse_int(row.get("tot_house"))
            avg_age = _parse_float(row.get("avg_age"))
            population_density = _parse_float(row.get("ppltn_dnsty"))
        except SgisError:
            pass

        try:
            row, year = await _get_stats_with_year_fallback(
                client, token, "/stats/company.json", adm_cd, "company"
            )
            stats_year = stats_year or year
            companies = _parse_int(row.get("corp_cnt"))
            workers = _parse_int(row.get("tot_worker"))
        except SgisError:
            pass

        age_bands: list[AgeBand] = []
        try:
            ppl_row = await _startupbiz_row(
                client, token, "/startupbiz/pplsummary.json", adm_cd, "pplsummary"
            )
            age_bands = _parse_age_bands(ppl_row, population)
        except SgisError:
            pass

        gender: GenderStats | None = None
        try:
            g_row = await _startupbiz_row(
                client, token, "/startupbiz/mfratiosummary.json", adm_cd, "mfratio"
            )
            male_pct = _parse_float(g_row.get("m_per"))
            female_pct = _parse_float(g_row.get("f_per"))
            if male_pct is not None and female_pct is not None:
                gender = GenderStats(
                    male_pct=male_pct,
                    female_pct=female_pct,
                    male_count=_parse_int(g_row.get("m_ppl")),
                    female_count=_parse_int(g_row.get("f_ppl")),
                )
        except SgisError:
            pass

        housing: HousingMix | None = None
        try:
            h_row = await _startupbiz_row(
                client, token, "/startupbiz/housesummary.json", adm_cd, "housesummary"
            )
            apt_pct = _parse_float(h_row.get("apart_per"))
            off_pct = _parse_float(h_row.get("officetel_per"))
            row_pct = _parse_float(
                h_row.get("row_house_per") or h_row.get("row_multi_house_per")
            )
            det_pct = _parse_float(h_row.get("detach_house_per"))
            if any(v is not None for v in (apt_pct, off_pct, row_pct, det_pct)):
                housing = HousingMix(
                    apartment_pct=apt_pct or 0.0,
                    officetel_pct=off_pct or 0.0,
                    row_house_pct=row_pct or 0.0,
                    detached_pct=det_pct or 0.0,
                    apartment_count=_parse_int(h_row.get("apart_cnt")),
                    officetel_count=_parse_int(h_row.get("officetel_cnt")),
                )
        except SgisError:
            pass

        industries: list[IndustryStats] = []
        if stats_year:
            industries = await _fetch_industries(client, token, adm_cd, stats_year)

        if population is None and workers is None:
            raise SgisError("SGIS stats returned no population or worker data")

        scope_year = stats_year or "최근"
        return DemandProfile(
            adm_cd=adm_cd,
            adm_name=adm_name,
            population=population,
            households=households,
            companies=companies,
            workers=workers,
            avg_age=avg_age,
            population_density=population_density,
            age_bands=age_bands,
            gender=gender,
            housing=housing,
            industries=industries,
            stats_year=stats_year,
            data_scope=f"행정동 (총조사 {scope_year})",
        )


async def fetch_demand_stats(lat: float, lng: float) -> DemandStats:
    """Legacy wrapper — returns basic DemandStats from DemandProfile."""
    profile = await fetch_demand_profile(lat, lng)
    return DemandStats(
        adm_cd=profile.adm_cd,
        adm_name=profile.adm_name,
        population=profile.population,
        households=profile.households,
        companies=profile.companies,
        workers=profile.workers,
    )


async def fetch_seoul_dong_population_rows(
    client: httpx.AsyncClient, token: str
) -> list[dict]:
    """All Seoul emdongs with population (for benchmark cache)."""
    last_error: SgisError | None = None
    for year in STATS_YEARS:
        try:
            result = await _get(
                client,
                "/stats/population.json",
                {
                    "accessToken": token,
                    "adm_cd": "11",
                    "low_search": "1",
                    "year": year,
                },
                "seoul-population",
            )
            if isinstance(result, list) and result:
                return result
        except SgisError as e:
            last_error = e
    if last_error:
        raise last_error
    raise SgisError("SGIS seoul population: no data")


async def fetch_seoul_dong_company_rows(
    client: httpx.AsyncClient, token: str
) -> list[dict]:
    """All Seoul emdongs with workers (for benchmark cache)."""
    last_error: SgisError | None = None
    for year in STATS_YEARS:
        try:
            result = await _get(
                client,
                "/stats/company.json",
                {
                    "accessToken": token,
                    "adm_cd": "11",
                    "low_search": "1",
                    "year": year,
                },
                "seoul-company",
            )
            if isinstance(result, list) and result:
                return result
        except SgisError as e:
            last_error = e
    if last_error:
        raise last_error
    raise SgisError("SGIS seoul company: no data")
