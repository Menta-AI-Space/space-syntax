"""서울 대비 아파트 시세 상대% 계산 (순수 함수, DB 불필요)."""

from app.services.housing_market import pct_vs_seoul


def test_pct_vs_seoul_higher_and_lower():
    # 지역 2000, 서울 중앙값 1000 → +100%
    assert pct_vs_seoul(2000.0, 1000.0) == 100
    # 지역이 서울보다 낮으면 음수
    assert pct_vs_seoul(800.0, 1000.0) == -20
    # 비슷하면 0 근처
    assert pct_vs_seoul(1010.0, 1000.0) == 1


def test_pct_vs_seoul_handles_missing():
    assert pct_vs_seoul(None, 1000.0) is None
    assert pct_vs_seoul(1500.0, None) is None
    assert pct_vs_seoul(1500.0, 0) is None
