"""Tests for the RTMS apartment-market cache (housing_market)."""

from __future__ import annotations

import asyncio

import pytest

from app.services import housing_market as hm
from app.services.real_estate import ApartmentTrade


def _trade(price_per_sqm: float, umd_nm: str = "신촌동") -> ApartmentTrade:
    return ApartmentTrade(
        deal_amount=100_000,
        exclu_use_ar=84.0,
        apt_nm="테스트",
        umd_nm=umd_nm,
        jibun="1",
        sgg_nm="서대문구",
        sgg_cd="11410",
        deal_year="2026",
        deal_month="6",
        deal_day="1",
        floor="5",
        build_year="2010",
        dealing_gbn="중개거래",
        price_per_sqm=price_per_sqm,
    )


@pytest.fixture(autouse=True)
def _clear_trades_cache(monkeypatch):
    # 로컬 real_estate_trades가 비어 있는 환경을 기본으로 — API 폴백 경로를 검증.
    monkeypatch.setattr(hm, "_trades_from_db", lambda lawd_cd, months_back: [])
    hm._trades_cache.clear()
    hm._trades_inflight.clear()
    yield
    hm._trades_cache.clear()
    hm._trades_inflight.clear()


class TestTradesCache:
    def test_fetch_survives_caller_timeout_and_fills_cache(self, monkeypatch):
        """site-score's 8s wait_for must not kill the slow RTMS download —
        it finishes in the background so the next request hits the cache."""
        release = asyncio.Event()
        calls = 0

        async def fake_fetch(*, lawd_cd, months_back):
            nonlocal calls
            calls += 1
            await release.wait()
            return [_trade(12_000_000.0) for _ in range(3)]

        monkeypatch.setattr(hm, "fetch_recent_apartment_trades", fake_fetch)

        async def scenario():
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    hm.fetch_apartment_market("1141068", "서울특별시 서대문구 신촌동"),
                    timeout=0.05,
                )
            task = hm._trades_inflight.get("11410:12")
            assert task is not None and not task.cancelled()
            release.set()
            await task
            await asyncio.sleep(0)  # let the done callback run
            assert "11410:12" in hm._trades_cache

            result = await asyncio.wait_for(
                hm.fetch_apartment_market("1141068", "서울특별시 서대문구 신촌동"),
                timeout=0.05,
            )
            assert result is not None
            assert result.sample_count == 3
            assert result.median_price_per_sqm == 1200.0
            assert calls == 1  # second call served from cache

        asyncio.run(scenario())

    def test_db_rows_bypass_api(self, monkeypatch):
        """로컬 real_estate_trades에 데이터가 있으면 RTMS API를 아예 안 탄다."""
        monkeypatch.setattr(
            hm,
            "_trades_from_db",
            lambda lawd_cd, months_back: [
                hm._DbTradeRow(umd_nm="신촌동", price_per_sqm=11_000_000.0),
                hm._DbTradeRow(umd_nm="신촌동", price_per_sqm=12_000_000.0),
                hm._DbTradeRow(umd_nm="신촌동", price_per_sqm=13_000_000.0),
            ],
        )

        async def fail_fetch(*, lawd_cd, months_back):
            raise AssertionError("API must not be called when DB has rows")

        monkeypatch.setattr(hm, "fetch_recent_apartment_trades", fail_fetch)

        result = asyncio.run(
            hm.fetch_apartment_market("1141068", "서울특별시 서대문구 신촌동")
        )
        assert result.sample_count == 3
        assert result.median_price_per_sqm == 1200.0
        assert "신촌동" in result.scope

    def test_failed_fetch_is_not_cached_and_retried(self, monkeypatch):
        calls = 0

        async def fake_fetch(*, lawd_cd, months_back):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise hm.RealEstateError("boom")
            return [_trade(10_000_000.0)]

        monkeypatch.setattr(hm, "fetch_recent_apartment_trades", fake_fetch)

        async def scenario():
            first = await hm.fetch_apartment_market(
                "1141068", "서울특별시 서대문구 신촌동"
            )
            assert first.sample_count == 0
            assert "RTMS 조회 실패" in first.scope
            assert not hm._trades_cache

            second = await hm.fetch_apartment_market(
                "1141068", "서울특별시 서대문구 신촌동"
            )
            assert second.sample_count == 1
            assert calls == 2

        asyncio.run(scenario())
