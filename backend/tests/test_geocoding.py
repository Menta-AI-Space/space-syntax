"""Tests for Kakao geocoding with keyword fallback."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.services.geocoding import (
    GeocodingError,
    expand_geocode_queries,
    geocode_address,
    strip_location_noise,
    strip_place_label_prefix,
)


def test_strip_place_label_prefix():
    assert (
        strip_place_label_prefix("샐러디 신촌역점 · 서울 서대문구 신촌로 107")
        == "서울 서대문구 신촌로 107"
    )
    assert strip_place_label_prefix("서울 서대문구 신촌로 107") == "서울 서대문구 신촌로 107"


def test_expand_geocode_queries_includes_stripped_address():
    queries = expand_geocode_queries("샐러디 신촌역점 · 서울 서대문구 신촌로 107")
    assert "서울 서대문구 신촌로 107" in queries


def test_strip_location_noise_drops_filler():
    assert strip_location_noise("서강대역 근처") == "서강대역"
    assert strip_location_noise("강남역 주변") == "강남역"
    assert strip_location_noise("서강대역") == "서강대역"


def test_expand_geocode_queries_drops_location_noise():
    # "서강대역 근처"는 카카오가 못 찾으므로 "서강대역"으로도 시도해야 한다.
    queries = expand_geocode_queries("서강대역 근처")
    assert "서강대역" in queries


def test_geocode_address_falls_back_to_keyword():
    async def run():
        with (
            patch(
                "app.services.geocoding.geocode_detail_with_fallbacks",
                new_callable=AsyncMock,
                side_effect=GeocodingError("No results found for address: 신촌 샐러디"),
            ),
            patch(
                "app.services.geocoding._geocode_keyword_detail",
                new_callable=AsyncMock,
                return_value=type(
                    "Geo",
                    (),
                    {
                        "lat": 37.555,
                        "lng": 126.936,
                        "lawd_cd": None,
                        "region_name": "서대문구",
                    },
                )(),
            ),
        ):
            return await geocode_address("신촌 샐러디")

    lat, lng = asyncio.run(run())
    assert abs(lat - 37.555) < 0.001
    assert abs(lng - 126.936) < 0.001


def test_station_name_does_not_expand_into_a_road_address():
    """지명을 "근처 길"로 바꿔치기하지 않는다.

    2026-08-01까지 ADDRESS_ALIASES가 "신촌역"을 "서울 서대문구 신촌로"로
    바꿔 넣었다. 길은 몇백 미터에 걸쳐 있어 좌표가 어디로 떨어질지 알 수 없고,
    실제로 2호선 신촌역(마포구)이 아니라 경의중앙선 쪽으로 찍혔다. 게다가 이
    치환이 키워드검색보다 먼저 걸려서, 정확한 답을 아는 카카오 검색까지 가지
    못했다.

    지명은 그대로 두고, 주소검색이 못 찾으면 키워드검색으로 떨어져야 한다.
    """
    for name in ("신촌역", "홍제역", "연세대", "이화여대"):
        queries = expand_geocode_queries(name)
        assert queries == [name], f"{name}이 다른 것으로 확장됐다: {queries}"
        assert not any("로" == q[-1:] for q in queries[1:]), f"{name}이 길 이름으로 치환됐다"


def test_expand_keeps_working_for_real_addresses():
    """별칭을 걷어내도 진짜 주소 처리는 그대로다."""
    queries = expand_geocode_queries("서울특별시 서대문구 신촌로 107")
    assert "서울특별시 서대문구 신촌로 107" in queries
    assert "서울 서대문구 신촌로 107" in queries
