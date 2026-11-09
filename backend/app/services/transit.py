"""Nearest subway station via Kakao Local category search (SW8).

Uses the existing KAKAO_REST_KEY — no extra data loading needed for MVP 1.
Bus stops need the MOLIT bus-stop file import and are added in a later step.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import settings

KAKAO_CATEGORY_URL = "https://dapi.kakao.com/v2/local/search/category.json"
SUBWAY_CATEGORY = "SW8"


class TransitError(Exception):
    pass


@dataclass
class SubwayStation:
    name: str
    distance_m: float
    lat: float
    lng: float


# Callers wrap this in short timeouts (site-score uses 2.5s), so a slow Kakao
# response silently drops the transit subscore and *changes the computed site
# score* for the same coordinates. Caching the resolved station per point makes
# repeat lookups for one coordinate deterministic and free — subway stations
# don't move, so there is nothing to invalidate within a process lifetime.
_STATION_CACHE: dict[tuple[float, float, int], SubwayStation | None] = {}
_STATION_CACHE_MAX = 512


def _cache_key(lat: float, lng: float, radius_m: int) -> tuple[float, float, int]:
    # ~1m precision — finer than the station-distance resolution we report.
    return (round(lat, 5), round(lng, 5), radius_m)


async def find_nearest_subway(
    lat: float, lng: float, radius_m: int = 1500
) -> SubwayStation | None:
    """Return the nearest subway station within radius, or None."""
    if not settings.kakao_rest_key:
        raise TransitError("KAKAO_REST_KEY is not configured")

    key = _cache_key(lat, lng, radius_m)
    if key in _STATION_CACHE:
        return _STATION_CACHE[key]

    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_key}"}
    params = {
        "category_group_code": SUBWAY_CATEGORY,
        "x": lng,
        "y": lat,
        "radius": min(radius_m, 20000),
        "sort": "distance",
        "size": 5,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(KAKAO_CATEGORY_URL, headers=headers, params=params)

    if response.status_code == 401:
        raise TransitError("Invalid Kakao REST API key")
    if response.status_code != 200:
        raise TransitError(f"Kakao category search failed: HTTP {response.status_code}")

    documents = response.json().get("documents", [])
    station: SubwayStation | None = None
    if documents:
        doc = documents[0]
        station = SubwayStation(
            name=str(doc.get("place_name", "")),
            distance_m=float(doc.get("distance", 0)),
            lat=float(doc.get("y", 0)),
            lng=float(doc.get("x", 0)),
        )

    # Only successful lookups are cached; errors above propagate uncached so a
    # transient Kakao failure isn't remembered as "no station here".
    if len(_STATION_CACHE) >= _STATION_CACHE_MAX:
        _STATION_CACHE.clear()
    _STATION_CACHE[key] = station
    return station


async def find_subways_within(lat: float, lng: float, radius_m: int = 3000) -> list[SubwayStation]:
    """반경 안 지하철역 전부(최대 15곳). 계획도면 모델은 블록마다 「가장 가까운 역」을 따로 재므로
    중심 한 점의 가장 가까운 역 하나로는 모자란다(2026-10-27)."""
    if not settings.kakao_rest_key:
        raise TransitError("KAKAO_REST_KEY is not configured")
    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_key}"}
    params = {
        "category_group_code": SUBWAY_CATEGORY,
        "x": lng,
        "y": lat,
        "radius": min(radius_m, 20000),
        "sort": "distance",
        "size": 15,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(KAKAO_CATEGORY_URL, headers=headers, params=params)
    if response.status_code != 200:
        raise TransitError(f"Kakao category search failed: HTTP {response.status_code}")
    return [
        SubwayStation(
            name=str(doc.get("place_name", "")),
            distance_m=float(doc.get("distance", 0)),
            lat=float(doc.get("y", 0)),
            lng=float(doc.get("x", 0)),
        )
        for doc in response.json().get("documents", [])
    ]


# ---------------------------------------------------------------------------
# 역 규모(승하차 인원) · 버스 접근성
#
# "역이 가깝다"만으로는 강남역과 한적한 단일노선 역이 같은 점수가 된다.
# 실제 역세권 가치는 그 역으로 오가는 사람 수에서 갈리므로, 서울 지하철
# 일평균 승하차(tools/ingest_subway_ridership.py 적재)를 함께 본다.
# ---------------------------------------------------------------------------

import re


@dataclass
class StationScale:
    """역 규모 — 일평균 승하차 합계와 노선 수(환승 규모)."""

    station_nm: str
    daily_total: int
    line_count: int


def normalize_station_name(name: str) -> str:
    """카카오/OSM/서울열린데이터의 역 이름 표기 차이를 흡수한다.

    괄호 부기 제거("왕십리(성동구청)"→"왕십리"), 공백 제거, 접미 '역' 제거,
    노선 접미 제거("신촌역 2호선"→"신촌"). ingest 스크립트와 같은 규칙이어야
    조인이 성립한다.
    """
    n = re.sub(r"\(.*?\)", "", name or "").strip()
    # 카카오는 "신촌역 2호선"처럼 노선을 붙여 준다.
    n = re.sub(r"\s*\d*호선.*$", "", n)
    n = re.sub(r"\s*(경의중앙선|경춘선|수인분당선|신분당선|공항철도|우이신설선|김포골드라인).*$", "", n)
    n = n.replace(" ", "")
    if len(n) > 1 and n.endswith("역"):
        n = n[:-1]
    return n


_SCALE_SQL = """
SELECT station_nm, daily_total, line_count
FROM subway_ridership
WHERE station_key = %s
ORDER BY daily_total DESC
LIMIT 1
"""

_BUS_SQL = """
SELECT count(*) FROM planet_osm_point
WHERE highway = 'bus_stop'
  AND ST_DWithin(
        way::geography,
        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
        %s)
"""


def fetch_station_scale(station_name: str) -> StationScale | None:
    """역 이름으로 일평균 승하차·노선 수를 찾는다. 없으면 None."""
    key = normalize_station_name(station_name)
    if not key:
        return None
    try:
        from app.services.spatial_query import get_connection

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(_SCALE_SQL, (key,))
            row = cur.fetchone()
    except Exception:
        # 테이블 미적재·DB 오류는 점수를 죽이지 않는다 — 거리만으로 계산된다.
        return None
    if not row:
        return None
    return StationScale(station_nm=str(row[0]), daily_total=int(row[1]), line_count=int(row[2]))


def count_bus_stops(lat: float, lng: float, radius_m: int = 300) -> int:
    """반경 내 버스정류장 수 (OSM). 실패 시 0."""
    try:
        from app.services.spatial_query import get_connection

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(_BUS_SQL, (lng, lat, radius_m))
            row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
