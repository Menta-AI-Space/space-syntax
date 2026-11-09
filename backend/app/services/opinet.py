"""오피넷(한국석유공사) 주유소 유가 — gas_station 프로파일 전용.

Runtime API (일 1,500회 무료 키) + 1h TTL 캐시. 유가는 일 단위로 변해
로컬 적재 대신 조회 시점 값을 쓴다 — gas_station 분석 요청에만 호출되므로
쿼터 부담 없음. 실패 시 None (KPI는 정직한 null).

aroundAll.do는 KATEC(TM128) 좌표계를 요구 — pyproj로 WGS84에서 변환.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from pyproj import Transformer

from app.config import settings

_KATEC = (
    "+proj=tmerc +lat_0=38 +lon_0=128 +k=0.9999 +x_0=400000 +y_0=600000"
    " +ellps=bessel +units=m +towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43"
)
_transformer = Transformer.from_crs("EPSG:4326", _KATEC, always_xy=True)

GASOLINE = "B027"  # 보통휘발유
SEARCH_RADIUS_M = 3000  # 차량 상권 반경 (오피넷 최대 5000)

_CACHE_TTL_S = 3600
_sido_cache: dict[str, tuple[float, float]] = {}
_around_cache: dict[tuple[float, float], tuple[float, "FuelPosition"]] = {}


@dataclass
class FuelPosition:
    local_avg_won: float
    sido_avg_won: float
    station_count: int
    sido_name: str


def _sido_avg(client: httpx.Client, sido_cd: str = "01") -> float | None:
    now = time.monotonic()
    cached = _sido_cache.get(sido_cd)
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1]
    resp = client.get(
        "https://www.opinet.co.kr/api/avgSidoPrice.do",
        params={"out": "json", "code": settings.opinet_api_key, "sido": sido_cd},
    )
    resp.raise_for_status()
    for oil in resp.json().get("RESULT", {}).get("OIL", []):
        if oil.get("PRODCD") == GASOLINE:
            price = float(oil["PRICE"])
            _sido_cache[sido_cd] = (now, price)
            return price
    return None


def fuel_position_for_point(lat: float, lng: float) -> FuelPosition | None:
    """반경 내 주유소 휘발유 평균가 vs 시도 평균가. 실패/무설정 시 None."""
    if not settings.opinet_api_key:
        return None
    key = (round(lat, 3), round(lng, 3))
    now = time.monotonic()
    cached = _around_cache.get(key)
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1]
    try:
        x, y = _transformer.transform(lng, lat)
        with httpx.Client(timeout=8) as client:
            resp = client.get(
                "https://www.opinet.co.kr/api/aroundAll.do",
                params={
                    "out": "json", "code": settings.opinet_api_key,
                    "x": round(x, 2), "y": round(y, 2),
                    "radius": SEARCH_RADIUS_M, "sort": 1, "prodcd": GASOLINE,
                },
            )
            resp.raise_for_status()
            stations = resp.json().get("RESULT", {}).get("OIL", [])
            prices = [float(s["PRICE"]) for s in stations if float(s.get("PRICE") or 0) > 0]
            if not prices:
                return None
            # NOTE: 시도 판별은 v1에서 서울 고정 — 전국 확장 시 좌표→시도 매핑 필요
            sido_avg = _sido_avg(client, "01")
        if sido_avg is None:
            return None
        pos = FuelPosition(
            local_avg_won=round(sum(prices) / len(prices), 1),
            sido_avg_won=sido_avg,
            station_count=len(prices),
            sido_name="서울",
        )
    except Exception:
        return None
    _around_cache[key] = (now, pos)
    return pos
