"""소상공인시장진흥공단 상가(상권)정보 — 로컬 PostGIS 우선, Open API 폴백.

로컬: sbiz_stores 테이블 (분기 전체 덤프, tools/ingest_sbiz_stores.py로 적재).
원격: storeListInRadius (반경내 상가업소 조회),
      https://apis.data.go.kr/B553077/api/open/sdsc2/ (DATA_GO_API_KEY).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SBIZ_SERVICE_BASE = "http://apis.data.go.kr/B553077/api/open/sdsc2"
SBIZ_SERVICE_BASE_HTTPS = "https://apis.data.go.kr/B553077/api/open/sdsc2"

DEFAULT_PAGE_SIZE = 1000
REQUEST_DELAY_SEC = 0.05


class SbizError(Exception):
    pass


class SbizConfigError(SbizError):
    pass


@dataclass
class StoreRecord:
    bizes_id: str
    bizes_nm: str
    inds_lcls_cd: str
    inds_lcls_nm: str
    inds_mcls_cd: str
    inds_mcls_nm: str
    inds_scls_cd: str
    inds_scls_nm: str
    lon: float | None
    lat: float | None
    bld_mng_no: str
    flr_no: str
    rdnm_adr: str


def _service_key() -> str:
    key = settings.data_go_api_key.strip()
    if not key:
        raise SbizConfigError(
            "DATA_GO_API_KEY is not configured. Add it to .env (see .env.example)."
        )
    return unquote(key)


def _parse_store_item(raw: dict[str, Any]) -> StoreRecord:
    def _float(v: Any) -> float | None:
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return StoreRecord(
        bizes_id=str(raw.get("bizesId", "")),
        bizes_nm=str(raw.get("bizesNm", "")),
        inds_lcls_cd=str(raw.get("indsLclsCd", "")),
        inds_lcls_nm=str(raw.get("indsLclsNm", "")).strip(),
        inds_mcls_cd=str(raw.get("indsMclsCd", "")),
        inds_mcls_nm=str(raw.get("indsMclsNm", "")).strip(),
        inds_scls_cd=str(raw.get("indsSclsCd", "")),
        inds_scls_nm=str(raw.get("indsSclsNm", "")).strip(),
        lon=_float(raw.get("lon")),
        lat=_float(raw.get("lat")),
        bld_mng_no=str(raw.get("bldMngNo", "")).strip(),
        flr_no=str(raw.get("flrNo", "")).strip(),
        rdnm_adr=str(raw.get("rdnmAdr", "")).strip(),
    )


def parse_store_list_json(payload: dict[str, Any]) -> tuple[list[StoreRecord], int]:
    """Parse storeListInRadius JSON response."""
    header = payload.get("header") or {}
    result_code = str(header.get("resultCode", ""))
    if result_code and result_code != "00":
        msg = header.get("resultMsg", "Unknown API error")
        raise SbizError(f"SBIZ API error {result_code}: {msg}")

    body = payload.get("body") or {}
    total_count = int(body.get("totalCount") or 0)
    items_raw = body.get("items") or []

    if isinstance(items_raw, dict):
        items_raw = [items_raw]

    stores = [_parse_store_item(item) for item in items_raw if isinstance(item, dict)]
    return stores, total_count


async def _fetch_store_page(
    *,
    cx: float,
    cy: float,
    radius_m: int,
    page_no: int,
    num_of_rows: int,
) -> tuple[list[StoreRecord], int]:
    params = {
        "serviceKey": _service_key(),
        "radius": radius_m,
        "cx": cx,
        "cy": cy,
        "type": "json",
        "pageNo": page_no,
        "numOfRows": num_of_rows,
    }

    # data.go.kr drops connections mid-read often enough that one flaky page
    # out of a 20-page radius fetch used to 500 the whole request. Retry
    # transient transport errors, then surface the failure as SbizError so
    # callers degrade the same way they do for API-level errors.
    last_transport_error: httpx.TransportError | None = None
    response = None
    for attempt in range(3):
        if attempt:
            await asyncio.sleep(float(attempt))
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(
                    f"{SBIZ_SERVICE_BASE_HTTPS}/storeListInRadius", params=params
                )
                if response.status_code >= 500 or response.status_code == 403:
                    response = await client.get(
                        f"{SBIZ_SERVICE_BASE}/storeListInRadius", params=params
                    )
            break
        except httpx.TransportError as e:
            last_transport_error = e
    if response is None:
        raise SbizError(
            f"SBIZ API unreachable after 3 attempts: {last_transport_error}"
        ) from last_transport_error

    if response.status_code == 403:
        raise SbizError(
            "data.go.kr returned 403 Forbidden. Verify DATA_GO_API_KEY is valid and "
            "your portal account is subscribed to 소상공인시장진흥공단_상가(상권)정보_API."
        )
    if response.status_code != 200:
        raise SbizError(f"HTTP {response.status_code}: {response.text[:200]}")

    try:
        payload = response.json()
    except ValueError as e:
        raise SbizError(f"Invalid JSON from SBIZ API: {response.text[:200]}") from e

    return parse_store_list_json(payload)


_STORES_IN_RADIUS_SQL = """
SELECT bizes_id, bizes_nm, inds_lcls_cd, inds_lcls_nm, inds_mcls_cd, inds_mcls_nm,
       inds_scls_cd, inds_scls_nm, lon, lat, bld_mng_no, flr_no, rdnm_adr
FROM sbiz_stores
WHERE ST_DWithin(
    geom_5179,
    ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179),
    %s
)
"""


def _fetch_stores_from_db(lat: float, lng: float, radius_m: int) -> list[StoreRecord]:
    """sbiz_stores 로컬 스토어 반경 조회 — 미적재/오류 시 빈 리스트 (API 폴백)."""
    from app.services.spatial_query import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_STORES_IN_RADIUS_SQL, (lng, lat, radius_m))
                rows = cur.fetchall()
    except Exception as e:
        logger.warning("sbiz_stores DB lookup failed (falling back to API): %s", e)
        return []
    return [
        StoreRecord(
            bizes_id=r[0],
            bizes_nm=r[1],
            inds_lcls_cd=r[2],
            inds_lcls_nm=r[3],
            inds_mcls_cd=r[4],
            inds_mcls_nm=r[5],
            inds_scls_cd=r[6],
            inds_scls_nm=r[7],
            lon=r[8],
            lat=r[9],
            bld_mng_no=r[10],
            flr_no=r[11],
            rdnm_adr=r[12],
        )
        for r in rows
    ]


async def fetch_stores_in_radius(
    lat: float,
    lng: float,
    radius_m: int,
    *,
    max_pages: int = 20,
) -> list[StoreRecord]:
    """반경 내 상가업소 — 로컬 PostGIS 우선, 빈 결과면 원격 API 폴백."""
    local = await asyncio.to_thread(_fetch_stores_from_db, lat, lng, radius_m)
    if local:
        return local
    return await _fetch_stores_in_radius_api(lat, lng, radius_m, max_pages=max_pages)


async def _fetch_stores_in_radius_api(
    lat: float,
    lng: float,
    radius_m: int,
    *,
    max_pages: int = 20,
) -> list[StoreRecord]:
    """원격 storeListInRadius (paginated). cx=longitude, cy=latitude."""
    all_stores: list[StoreRecord] = []
    page_no = 1
    total_count = 0

    while page_no <= max_pages:
        stores, total_count = await _fetch_store_page(
            cx=lng,
            cy=lat,
            radius_m=radius_m,
            page_no=page_no,
            num_of_rows=DEFAULT_PAGE_SIZE,
        )
        all_stores.extend(stores)

        if len(all_stores) >= total_count or not stores:
            break

        page_no += 1
        await asyncio.sleep(REQUEST_DELAY_SEC)

    return all_stores
