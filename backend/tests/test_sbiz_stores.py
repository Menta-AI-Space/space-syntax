import asyncio

import httpx
import pytest

from app.services import sbiz_stores
from app.services.sbiz_stores import SbizError, _fetch_store_page


VALID_PAYLOAD = {
    "header": {"resultCode": "00"},
    "body": {
        "totalCount": 1,
        "items": [
            {
                "bizesId": "B1",
                "bizesNm": "테스트카페",
                "indsLclsCd": "I2",
                "indsMclsCd": "I212",
                "indsSclsCd": "I21201",
                "lon": "126.9370",
                "lat": "37.5559",
            }
        ],
    },
}


class _FakeResponse:
    status_code = 200

    def json(self):
        return VALID_PAYLOAD


def _fake_client_class(fail_times: int, calls: list[int]):
    """AsyncClient stand-in whose GET raises ReadError for the first N attempts."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            calls.append(1)
            if len(calls) <= fail_times:
                raise httpx.ReadError("connection dropped mid-read")
            return _FakeResponse()

    return FakeClient


@pytest.fixture
def sbiz_env(monkeypatch):
    monkeypatch.setattr(sbiz_stores.settings, "data_go_api_key", "test-key")

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(sbiz_stores.asyncio, "sleep", _no_sleep)
    return monkeypatch


def test_fetch_store_page_retries_transient_transport_error(sbiz_env):
    calls: list[int] = []
    sbiz_env.setattr(sbiz_stores.httpx, "AsyncClient", _fake_client_class(1, calls))

    stores, total = asyncio.run(
        _fetch_store_page(cx=126.937, cy=37.556, radius_m=500, page_no=1, num_of_rows=100)
    )

    assert total == 1
    assert stores[0].bizes_nm == "테스트카페"
    assert len(calls) == 2  # first attempt dropped, second succeeded


def test_fetch_store_page_raises_sbiz_error_after_exhausted_retries(sbiz_env):
    calls: list[int] = []
    sbiz_env.setattr(sbiz_stores.httpx, "AsyncClient", _fake_client_class(99, calls))

    # Must surface as SbizError — the type routers already catch and degrade to a
    # warning — never as a raw transport error that 500s the whole request.
    with pytest.raises(SbizError, match="unreachable after 3 attempts"):
        asyncio.run(
            _fetch_store_page(
                cx=126.937, cy=37.556, radius_m=500, page_no=1, num_of_rows=100
            )
        )

    assert len(calls) == 3
