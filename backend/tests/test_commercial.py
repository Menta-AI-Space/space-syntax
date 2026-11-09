"""Unit tests for SBIZ store API parsing (no live API)."""

import asyncio
from unittest.mock import patch

import pytest

from app.services.sbiz_stores import (
    SbizConfigError,
    SbizError,
    StoreRecord,
    fetch_stores_in_radius,
    parse_store_list_json,
)

SAMPLE_JSON = {
    "header": {
        "resultCode": "00",
        "resultMsg": "NORMAL SERVICE",
    },
    "body": {
        "items": [
            {
                "bizesId": "MA001",
                "bizesNm": "카페A",
                "indsLclsCd": "I2",
                "indsLclsNm": "음식",
                "indsMclsCd": "I212",
                "indsMclsNm": "비알코올",
                "indsSclsCd": "I21201",
                "indsSclsNm": "카페",
                "lon": 126.936,
                "lat": 37.555,
                "bldMngNo": "BLD001",
                "flrNo": "1",
                "rdnmAdr": "서울특별시 서대문구 연세로 1",
            },
            {
                "bizesId": "MA002",
                "bizesNm": "카페B",
                "indsLclsCd": "I2",
                "indsLclsNm": "음식",
                "indsMclsCd": "I212",
                "indsMclsNm": "비알코올",
                "indsSclsCd": "I21201",
                "indsSclsNm": "카페",
                "lon": 126.937,
                "lat": 37.556,
                "bldMngNo": "BLD001",
                "flrNo": "2",
                "rdnmAdr": "서울특별시 서대문구 연세로 1",
            },
            {
                "bizesId": "MA003",
                "bizesNm": "약국C",
                "indsLclsCd": "G2",
                "indsLclsNm": "소매",
                "indsMclsCd": "G215",
                "indsMclsNm": "의약·화장품 소매",
                "indsSclsCd": "G21501",
                "indsSclsNm": "약국",
                "lon": 126.935,
                "lat": 37.554,
                "bldMngNo": "BLD002",
                "flrNo": "1",
                "rdnmAdr": "서울특별시 서대문구 연세로 2",
            },
        ],
        "numOfRows": 1000,
        "pageNo": 1,
        "totalCount": 3,
    },
}


def _stores_from_sample() -> list[StoreRecord]:
    stores, _ = parse_store_list_json(SAMPLE_JSON)
    return stores


class TestParseStoreListJson:
    def test_parses_items(self):
        stores, total = parse_store_list_json(SAMPLE_JSON)
        assert total == 3
        assert len(stores) == 3
        assert stores[0].bizes_nm == "카페A"
        assert stores[0].inds_mcls_cd == "I212"
        assert stores[0].lon == 126.936

    def test_api_error_raises(self):
        payload = {"header": {"resultCode": "03", "resultMsg": "NO DATA"}}
        with pytest.raises(SbizError, match="03"):
            parse_store_list_json(payload)


class TestFetchStoresInRadius:
    # fetch_stores_in_radius는 로컬 PostGIS(sbiz_stores 테이블) 우선이고,
    # 로컬이 비어 있을 때만 원격 API로 폴백한다 — API 경로 테스트는
    # 로컬 조회를 빈 결과로 패치해 폴백을 강제한다.
    def test_pagination_on_api_fallback(self):
        page1_stores, _ = parse_store_list_json(SAMPLE_JSON)
        page2_payload = {
            "header": {"resultCode": "00", "resultMsg": "OK"},
            "body": {"items": [], "totalCount": 3, "pageNo": 2, "numOfRows": 1000},
        }
        page2_stores, _ = parse_store_list_json(page2_payload)

        async def mock_fetch(**kwargs):
            if kwargs["page_no"] == 1:
                return page1_stores, 3
            return page2_stores, 3

        with patch("app.services.sbiz_stores._fetch_stores_from_db", return_value=[]):
            with patch("app.services.sbiz_stores._fetch_store_page", side_effect=mock_fetch):
                stores = asyncio.run(fetch_stores_in_radius(37.555, 126.936, 500))
        assert len(stores) == 3

    def test_missing_api_key_only_fatal_without_local_data(self):
        with patch("app.services.sbiz_stores._fetch_stores_from_db", return_value=[]):
            with patch("app.services.sbiz_stores._service_key", side_effect=SbizConfigError("missing")):
                with pytest.raises(SbizConfigError):
                    asyncio.run(fetch_stores_in_radius(37.555, 126.936, 500))

    def test_local_db_short_circuits_api(self):
        local_stores, _ = parse_store_list_json(SAMPLE_JSON)
        with patch("app.services.sbiz_stores._fetch_stores_from_db", return_value=local_stores):
            with patch("app.services.sbiz_stores._service_key", side_effect=SbizConfigError("missing")):
                stores = asyncio.run(fetch_stores_in_radius(37.555, 126.936, 500))
        assert stores == local_stores
