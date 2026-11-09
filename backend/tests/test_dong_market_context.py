"""행정동 시장 맥락 — 절대수가 아니라 서울 대비 위치로 나오는지."""

import psycopg2
import pytest

from app.config import settings
from app.services.demand_benchmark import (
    SeoulRelative,
    _quarter_days,
    dong_market_context,
    dong_market_context_for_point,
)


def _db_available() -> bool:
    try:
        psycopg2.connect(settings.database_url, connect_timeout=2).close()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="local postgres not running")


def test_vs_median_pct_reads_as_percent_above_median():
    assert SeoulRelative(value=118.0, top_pct=10, seoul_median=100.0).vs_median_pct == 18.0
    assert SeoulRelative(value=50.0, top_pct=90, seoul_median=100.0).vs_median_pct == -50.0
    # 중앙값이 없으면 배수를 지어내지 않는다
    assert SeoulRelative(value=50.0, top_pct=None, seoul_median=None).vs_median_pct is None


def test_quarter_days_uses_real_day_counts():
    # 분기 합계를 일평균으로 환산할 때 '90일'로 뭉뚱그리면 분기마다 어긋난다
    assert _quarter_days("20251") == 90
    assert _quarter_days("20252") == 91
    assert _quarter_days("20254") == 92
    assert _quarter_days("20241") == 91  # 2024는 윤년


@requires_db
def test_market_context_is_always_relative_to_seoul():
    """모든 지표에 서울 상위 %가 붙어야 한다 — 절대수만 던지지 않는다."""
    context = dong_market_context_for_point(37.5563, 126.9236)  # 홍대 서교동
    assert context is not None
    assert context.dong_name
    assert context.quarter >= "20251"

    for metric in (context.footfall_daily, context.female_pct):
        assert metric.top_pct is not None and 1 <= metric.top_pct <= 100
        assert metric.seoul_median is not None
        assert metric.vs_median_pct is not None

    # 유동인구 구성은 6개 연령대가 다 나오고 합이 100%에 가까워야 한다
    assert set(context.age_pcts) == {"10대", "20대", "30대", "40대", "50대", "60대 이상"}
    assert 95.0 <= sum(r.value for r in context.age_pcts.values()) <= 105.0
    for band in context.age_pcts.values():
        assert band.top_pct is not None


@requires_db
def test_hongdae_is_young_and_gangnam_is_working_age():
    """상권 성격이 실제로 갈리는지 — 상수 없이 분포에서 나온 값인지 확인한다."""
    hongdae = dong_market_context_for_point(37.5563, 126.9236)
    gangnam = dong_market_context_for_point(37.4979, 127.0276)
    assert hongdae is not None and gangnam is not None

    # 홍대는 20대 비중이 서울 최상위권, 강남역 업무지구는 30–40대가 더 두껍다
    assert hongdae.age_pcts["20대"].top_pct <= 5
    assert hongdae.age_pcts["20대"].value > gangnam.age_pcts["20대"].value
    assert gangnam.age_pcts["40대"].value > hongdae.age_pcts["40대"].value


@requires_db
def test_outside_seoul_returns_none_instead_of_nearest_dong():
    assert dong_market_context_for_point(37.2636, 127.0286) is None  # 수원
    assert dong_market_context("99999999") is None


@requires_db
def test_youth_rank_comes_from_the_real_distribution():
    """예전에는 '서울 평균 28%'를 상수로 박고 임의의 식으로 순위를 만들었다.

    그 상수는 실측이 아니었고 결과도 35·15 같은 계단값으로 튀었다. 이제
    sgis_dong_stats의 426개 동 분포에서 잰다.
    """
    from app.services.demand_benchmark import _youth_pct_distribution

    distribution = _youth_pct_distribution()
    assert len(distribution) > 400
    # 계단값이 아니라 연속 분포여야 한다
    assert len(set(distribution)) > 50
