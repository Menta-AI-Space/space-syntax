"""POST /api/purchase-verdict — market_band이 화면에 캡션을 그릴 수 있을 만큼
실제 표본 상태(표본 부족·완화·출처)를 그대로 실어 나르는지 검증한다.

입지 분석(analyze_site)·실거래 조회(fetch_comparables)는 DB·외부 API가 필요한
I/O라 여기서는 결과를 고정해 둔 페이크로 대체하고, 그 이후 라우터가 응답을
조립하는 로직(순수 계산)만 확인한다.
"""

from __future__ import annotations

import asyncio

from app.routers import goals
from app.services.property_market import MarketBand
from app.services.verdicts import SiteAnalysis


def _fake_site_analysis() -> SiteAnalysis:
    return SiteAnalysis(
        row={
            "max_acceptable_rent_man": 300.0,
            "expected_sales_basis": "테스트용 매출 근거",
            "evidence": {},
        },
        upjong=None,
        warnings=[],
    )


def _request() -> goals.PurchaseVerdictRequest:
    return goals.PurchaseVerdictRequest(
        lat=37.5, lng=127.0, price_manwon=50_000, area_sqm=66.0
    )


def test_purchase_verdict_market_band_flags_insufficient_sample(monkeypatch):
    async def _fake_analyze_site(**_kwargs):
        return _fake_site_analysis()

    band = MarketBand(
        property_type="commercial",
        building_type="집합",
        n=1,
        radius_m=500,
        months_back=12,
        p25=1000.0,
        p50=1200.0,
        p75=1400.0,
        min_deal_date="2026-01-01",
        max_deal_date="2026-06-01",
        relaxations=["면적 ±20% 필터 해제"],
        insufficient_sample=True,
        subject_price_per_sqm=1100.0,
    )

    async def _fake_fetch_comparables(**_kwargs):
        return band

    monkeypatch.setattr(goals, "analyze_site", _fake_analyze_site)
    monkeypatch.setattr(goals, "fetch_comparables", _fake_fetch_comparables)

    response = asyncio.run(goals.purchase_verdict(_request()))

    assert response.market_band.n == 1
    assert response.market_band.insufficient_sample is True
    assert response.market_band.relaxations == ["면적 ±20% 필터 해제"]
    assert response.market_band.data_source == band.data_source
    assert response.market_band.data_source != ""


def test_purchase_verdict_market_band_reports_sufficient_sample(monkeypatch):
    async def _fake_analyze_site(**_kwargs):
        return _fake_site_analysis()

    band = MarketBand(
        property_type="commercial",
        building_type="집합",
        n=20,
        radius_m=500,
        months_back=12,
        p25=1000.0,
        p50=1200.0,
        p75=1400.0,
        min_deal_date="2026-01-01",
        max_deal_date="2026-06-01",
        insufficient_sample=False,
        subject_price_per_sqm=1100.0,
    )

    async def _fake_fetch_comparables(**_kwargs):
        return band

    monkeypatch.setattr(goals, "analyze_site", _fake_analyze_site)
    monkeypatch.setattr(goals, "fetch_comparables", _fake_fetch_comparables)

    response = asyncio.run(goals.purchase_verdict(_request()))

    assert response.market_band.n == 20
    assert response.market_band.insufficient_sample is False
    assert response.market_band.relaxations == []


def test_purchase_verdict_market_band_defaults_insufficient_without_a_band(monkeypatch):
    # 실거래 비교가 아예 실패하면(예외) band 자체가 없다 — 이때도 화면이
    # "표본 충분"으로 오인하지 않도록 insufficient_sample은 True로 남아야 한다.
    async def _fake_analyze_site(**_kwargs):
        return _fake_site_analysis()

    async def _fake_fetch_comparables(**_kwargs):
        raise RuntimeError("주변 실거래 조회 실패(테스트)")

    monkeypatch.setattr(goals, "analyze_site", _fake_analyze_site)
    monkeypatch.setattr(goals, "fetch_comparables", _fake_fetch_comparables)

    response = asyncio.run(goals.purchase_verdict(_request()))

    assert response.market_band.n == 0
    assert response.market_band.insufficient_sample is True
    assert response.market_band.relaxations == []
    assert response.market_band.data_source == ""
