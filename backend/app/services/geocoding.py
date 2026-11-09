import httpx
from dataclasses import dataclass

from app.config import settings
KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_COORD2ADDRESS_URL = "https://dapi.kakao.com/v2/local/geo/coord2address.json"

# 지명 별칭 표는 2026-08-01에 걷어냈다. 다시 만들지 말 것.
#
# "신촌역 카페 자리 어때?"가 2호선이 아니라 경의중앙선 신촌역으로 분석되던
# 버그의 원인이었다. 표에 있던 10개 항목이 **전부** 역·학교가 아니라 그 근처
# "길"을 가리켰고, 길은 몇백 미터에 걸쳐 있어 좌표가 어디로 떨어질지 알 수
# 없다. 게다가 이 표는 키워드검색보다 **먼저** 걸려서, 정확한 답을 아는
# 카카오 검색까지 가지도 못하게 막았다.
#
# 실측(2026-08-01, 카카오 키워드검색 1순위):
#   신촌역   → 표: 서대문구 신촌로 / 실제: 신촌역 2호선, 마포구 신촌로 지하 90
#   홍제역   → 표: 서대문구 가좌로 / 실제: 홍제역 3호선, 통일로 지하 440-1 (다른 길)
#   연세대   → 표: 서대문구 연세로 / 실제: 연세대학교 신촌캠퍼스, 연세로 50
#   이화여대 → 표: 이화여자대길   / 실제: 이화여자대학교, 이화여대길 52 (철자도 틀림)
#
# 카카오 키워드검색은 이 10개를 전부 정확히 찾는다. 손으로 적은 표는 검색보다
# 나을 수 없고, 틀려도 아무도 모른다 — 하드코딩 상수를 분석 결과처럼 쓰지
# 않는다는 CLAUDE.md 원칙이 걸리는 자리다.
#
# 지명이 주소검색에서 안 잡히면 _geocode_keyword_detail로 떨어지게 두면 된다.


class GeocodingError(Exception):
    pass


@dataclass
class GeocodedAddress:
    lat: float
    lng: float
    lawd_cd: str | None = None
    region_name: str | None = None


# Seoul district name → RTMS lawd_cd (시군구 5-digit code)
# RTMS LAWD_CD (5자리 시군구) — 서울 25개 구 전체.
# tools/ingest_real_estate_trades.py 의 SEOUL_LAWD_CDS 와 동일한 코드표의 역방향.
REGION_LAWD_CD: dict[str, str] = {
    "종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200",
    "광진구": "11215", "동대문구": "11230", "중랑구": "11260", "성북구": "11290",
    "강북구": "11305", "도봉구": "11320", "노원구": "11350", "은평구": "11380",
    "서대문구": "11410", "마포구": "11440", "양천구": "11470", "강서구": "11500",
    "구로구": "11530", "금천구": "11545", "영등포구": "11560", "동작구": "11590",
    "관악구": "11620", "서초구": "11650", "강남구": "11680", "송파구": "11710",
    "강동구": "11740",
}


def infer_lawd_cd_from_b_code(b_code: str) -> str | None:
    """Extract 5-digit 시군구 code from Kakao 10-digit 법정동 code."""
    code = b_code.strip()
    if len(code) >= 5 and code[:5].isdigit():
        return code[:5]
    return None


def strip_place_label_prefix(address: str) -> str:
    """Extract road address from autocomplete labels like '가게명 · 도로명주소'."""
    if " · " in address:
        tail = address.split(" · ", 1)[1].strip()
        if tail:
            return tail
    return address


# 지명 뒤에 붙는 군더더기. 카카오는 "서강대역 근처"를 장소로 못 찾지만
# "서강대역"은 찾는다 — 이 토큰들을 걷어내 지명만 남긴다.
_LOCATION_NOISE_WORDS = {"근처", "주변", "인근", "근방", "부근", "앞", "옆", "쪽", "일대"}


def strip_location_noise(address: str) -> str:
    """Drop filler tokens like '근처'/'주변' so '서강대역 근처' also tries '서강대역'."""
    tokens = [t for t in address.split() if t not in _LOCATION_NOISE_WORDS]
    return " ".join(tokens)


def expand_geocode_queries(address: str) -> list[str]:
    """Return deduplicated geocoding query variants for a user or trade address."""
    q = address.strip()
    if not q:
        return []

    queries: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        v = value.strip()
        if v and v not in seen:
            seen.add(v)
            queries.append(v)

    add(q)
    add(strip_place_label_prefix(q))
    add(strip_location_noise(q))
    if q.startswith("서울특별시"):
        add(q.replace("서울특별시", "서울", 1))
    elif not q.startswith("서울") and "구 " in q:
        add(f"서울 {q}")
        add(f"서울특별시 {q}")
    return queries


def _parse_kakao_document(doc: dict) -> GeocodedAddress:
    lat = float(doc["y"])
    lng = float(doc["x"])
    lawd_cd: str | None = None
    region_name: str | None = None

    address = doc.get("address") or {}
    road = doc.get("road_address") or {}
    b_code = address.get("b_code") or road.get("b_code") or ""
    lawd_cd = infer_lawd_cd_from_b_code(b_code)
    region_name = address.get("region_2depth_name") or road.get("region_2depth_name")

    return GeocodedAddress(lat=lat, lng=lng, lawd_cd=lawd_cd, region_name=region_name)


async def _geocode_single_query_detail(address: str) -> GeocodedAddress:
    if not settings.kakao_rest_key:
        raise GeocodingError("KAKAO_REST_KEY is not configured")

    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_key}"}
    params = {"query": address}

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(KAKAO_ADDRESS_URL, headers=headers, params=params)

    if response.status_code == 401:
        raise GeocodingError("Invalid Kakao REST API key")
    if response.status_code != 200:
        raise GeocodingError(f"Kakao geocoding failed: HTTP {response.status_code}")

    data = response.json()
    documents = data.get("documents", [])
    if not documents:
        raise GeocodingError(f"No results found for address: {address}")

    return _parse_kakao_document(documents[0])


async def geocode_with_fallbacks(queries: list[str]) -> tuple[float, float]:
    """Try each query in order until Kakao returns a match."""
    result = await geocode_detail_with_fallbacks(queries)
    return result.lat, result.lng


async def geocode_detail_with_fallbacks(queries: list[str]) -> GeocodedAddress:
    """Try each query in order; return coordinates and inferred lawd_cd when available."""
    last_error: GeocodingError | None = None
    for query in queries:
        try:
            return await _geocode_single_query_detail(query)
        except GeocodingError as e:
            last_error = e
    if last_error is not None:
        raise last_error
    raise GeocodingError("No geocoding queries provided")


async def _geocode_keyword_detail(query: str) -> GeocodedAddress:
    if not settings.kakao_rest_key:
        raise GeocodingError("KAKAO_REST_KEY is not configured")

    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_key}"}
    params = {"query": query.strip(), "size": 1}

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(KAKAO_KEYWORD_URL, headers=headers, params=params)

    if response.status_code == 401:
        raise GeocodingError("Invalid Kakao REST API key")
    if response.status_code != 200:
        raise GeocodingError(f"Kakao keyword search failed: HTTP {response.status_code}")

    documents = response.json().get("documents", [])
    if not documents:
        raise GeocodingError(f"No place found for: {query}")

    doc = documents[0]
    return GeocodedAddress(
        lat=float(doc["y"]),
        lng=float(doc["x"]),
        lawd_cd=None,
        region_name=doc.get("region_2depth_name"),
    )


async def geocode_address_detail(address: str) -> GeocodedAddress:
    """Return coordinates and optional lawd_cd for a Korean address or place name."""
    queries = expand_geocode_queries(address)
    if not queries:
        raise GeocodingError("Empty address")
    try:
        return await geocode_detail_with_fallbacks(queries)
    except GeocodingError:
        last_error: GeocodingError | None = None
        for keyword_q in (address, strip_place_label_prefix(address), strip_location_noise(address)):
            q = keyword_q.strip()
            if not q:
                continue
            try:
                return await _geocode_keyword_detail(q)
            except GeocodingError as e:
                last_error = e
        if last_error is not None:
            raise last_error
        raise GeocodingError(f"No place or address found for: {address}")


async def geocode_address(address: str) -> tuple[float, float]:
    """Return (lat, lng) for a Korean address or place keyword via Kakao Local API."""
    result = await geocode_address_detail(address)
    return result.lat, result.lng


def _pick_reverse_address(doc: dict) -> str:
    """Prefer the road address; fall back to the 지번 address."""
    road = doc.get("road_address") or {}
    jibun = doc.get("address") or {}
    name = (road.get("address_name") or "").strip()
    if name:
        building = (road.get("building_name") or "").strip()
        return f"{name} {building}".strip() if building else name
    return (jibun.get("address_name") or "").strip()


async def reverse_geocode(lat: float, lng: float) -> str | None:
    """Coordinates → Korean address string, or None when unavailable.

    Used to label machine-generated candidate spots (/api/area-search) with a
    real address instead of an invented place name. Never raises: a missing
    label must not fail an analysis that is otherwise complete.
    """
    if not settings.kakao_rest_key:
        return None
    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_key}"}
    params = {"x": f"{lng:.7f}", "y": f"{lat:.7f}"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                KAKAO_COORD2ADDRESS_URL, headers=headers, params=params
            )
        if response.status_code != 200:
            return None
        documents = response.json().get("documents", [])
    except Exception:
        return None
    if not documents:
        return None
    return _pick_reverse_address(documents[0]) or None
