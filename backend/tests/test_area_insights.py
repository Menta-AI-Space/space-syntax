"""동네 시세 블록의 숫자와 그 숫자의 출처 표기."""

from app.services.area_insights import ApartmentPriceStats, _stats_from_per_sqm_won


def test_per_sqm_won_becomes_manwon_per_pyeong():
    """real_estate_trades.price_per_sqm은 원/㎡다(만원 아님).

    단위를 한 번 헷갈리면 평당 1,650만원이 1,650억으로 나가는데, 화면에는
    그냥 큰 숫자로 보여서 눈으로는 안 걸린다.
    """
    # ㎡당 500만원(=5,000,000원) 짜리 거래 세 건.
    stats = _stats_from_per_sqm_won([5_000_000.0] * 3, 3, "반경 500m", [], "radius")

    assert stats.median_per_sqm_manwon == 500
    # 1평 = 3.3058㎡ → 평당 약 1,653만원
    assert stats.median_per_pyeong_manwon == 1653
    assert stats.trade_count == 3


def test_scope_kind_says_where_the_number_came_from():
    """반경 안에 실거래가 없으면 구 전체 중앙값으로 물러난다.

    화면이 "주변 아파트 시세"라고만 적으면 이 동네 값으로 읽힌다 — 같은 구
    안에서도 동네별 평당가가 배로 갈린다. 문구를 파싱하는 대신 종류를 따로
    실어 보내, scope 문자열을 손봐도 화면 판단이 안 깨지게 한다.
    """
    radius = _stats_from_per_sqm_won([4_000_000.0], 1, "반경 500m", [], "radius")
    gu = _stats_from_per_sqm_won([4_000_000.0] * 9, 9, "강남구 전체", [], "gu")

    assert radius.scope_kind == "radius"
    assert gu.scope_kind == "gu"
    # 데이터가 아예 없을 때는 어느 쪽도 주장하지 않는다.
    assert ApartmentPriceStats(0, None, None).scope_kind == ""


def test_no_trades_leaves_the_price_empty_not_zero():
    """거래가 없으면 '평당 0만원'이 아니라 값 없음이어야 한다."""
    stats = _stats_from_per_sqm_won([], 0, "", [], "")

    assert stats.median_per_sqm_manwon is None
    assert stats.median_per_pyeong_manwon is None
