from app.services.undervalued_areas import DongRawInput, compute_gap_scores, rank_percentile


def test_rank_percentile_highest_value_is_100():
    assert rank_percentile(10.0, [1.0, 2.0, 10.0]) == 100.0


def test_rank_percentile_lowest_value_is_0():
    assert rank_percentile(1.0, [1.0, 2.0, 10.0]) == 0.0


def test_rank_percentile_single_value_distribution_is_50():
    assert rank_percentile(5.0, [5.0]) == 50.0


def test_rank_percentile_ties_average_out():
    # 세 값이 모두 같으면 어느 걸 넣어도 중앙(50)으로 수렴한다
    assert rank_percentile(5.0, [5.0, 5.0, 5.0]) == 50.0


def test_gap_score_high_structure_low_price_is_positive():
    rows = [
        DongRawInput("A", "동A", structure_score=0.9, price_per_sqm_manwon=100.0, trade_sample_count=5),
        DongRawInput("B", "동B", structure_score=0.5, price_per_sqm_manwon=500.0, trade_sample_count=5),
        DongRawInput("C", "동C", structure_score=0.1, price_per_sqm_manwon=900.0, trade_sample_count=5),
    ]
    scores = {s.adm_cd: s for s in compute_gap_scores(rows)}
    # 동A: 구조 1등(백분위 100) + 가격 꼴찌(백분위 0) → 갭 = 100 (가장 저평가)
    assert scores["A"].gap_score == 100.0
    # 동C: 구조 꼴찌 + 가격 1등(가장 비쌈) → 갭 = -100
    assert scores["C"].gap_score == -100.0
    assert scores["A"].gap_score > scores["B"].gap_score > scores["C"].gap_score


def test_dong_without_road_network_is_excluded_not_zeroed():
    rows = [
        DongRawInput("A", "동A", structure_score=0.8, price_per_sqm_manwon=200.0, trade_sample_count=5),
        DongRawInput("B", "분석불가동", structure_score=None, price_per_sqm_manwon=None, trade_sample_count=0),
    ]
    scores = compute_gap_scores(rows)
    assert len(scores) == 1
    assert scores[0].adm_cd == "A"


def test_dong_without_price_sample_has_no_gap_score():
    rows = [
        DongRawInput("A", "동A", structure_score=0.8, price_per_sqm_manwon=None, trade_sample_count=1),
        DongRawInput("B", "동B", structure_score=0.5, price_per_sqm_manwon=300.0, trade_sample_count=5),
    ]
    scores = {s.adm_cd: s for s in compute_gap_scores(rows)}
    assert scores["A"].gap_score is None
    assert scores["A"].price_percentile is None
    assert scores["B"].gap_score is not None


# ── 스키마 미준비 vs 진짜 장애 ────────────────────────────────────────

def test_missing_table_returns_empty_list_not_500(monkeypatch):
    """배치를 안 돌린 환경에서 API가 500으로 나가면 '고장'과 구분이 안 된다."""
    import psycopg2.errors

    from app.services import undervalued_areas as mod

    def boom(_limit):
        raise psycopg2.errors.UndefinedTable("relation does not exist")

    monkeypatch.setattr(mod, "_query_top_undervalued", boom)
    assert mod.list_top_undervalued(5) == []


def test_missing_column_also_degrades():
    import psycopg2.errors

    from app.services import undervalued_areas as mod

    original = mod._query_top_undervalued
    try:
        mod._query_top_undervalued = lambda _l: (_ for _ in ()).throw(
            psycopg2.errors.UndefinedColumn("column does not exist")
        )
        assert mod.list_top_undervalued(5) == []
    finally:
        mod._query_top_undervalued = original


def test_connection_failure_still_raises():
    """DB가 죽은 것은 진짜 장애다 — 조용히 빈 목록으로 덮으면 안 된다."""
    import psycopg2

    import pytest

    from app.services import undervalued_areas as mod

    original = mod._query_top_undervalued
    try:
        mod._query_top_undervalued = lambda _l: (_ for _ in ()).throw(
            psycopg2.OperationalError("connection refused")
        )
        with pytest.raises(psycopg2.OperationalError):
            mod.list_top_undervalued(5)
    finally:
        mod._query_top_undervalued = original
