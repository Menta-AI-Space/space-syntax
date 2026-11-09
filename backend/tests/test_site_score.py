import asyncio

from app.services.graph_builder import build_graph
from app.services.llm_explain import explain_site_score, rule_based_explanation
from app.services.sbiz_stores import StoreRecord
from app.services.site_score import (
    build_verdict_one_liner,
    compute_site_score,
    competition_advantage_score,
    competition_density_display_score,
    competition_density_per_km2,
    competition_intensity_label,
    competition_score,
    count_same_category,
    match_same_category,
    demand_score,
    demographic_fit_score,
    format_rank_display,
    nearest_segment,
    top_percent,
    bottom_percent,
    transit_score,
)
from app.services.spatial_query import RoadSegment


def make_segment(osm_id: int, coords: list[list[float]]) -> RoadSegment:
    return RoadSegment(
        osm_id=osm_id,
        highway="residential",
        length_m=100.0,
        coordinates=coords,
        walkable=True,
        drivable=True,
    )


def make_store(
    mcls_nm: str,
    scls_nm: str = "",
    *,
    mcls_cd: str = "I212",
    scls_cd: str = "I21201",
) -> StoreRecord:
    return StoreRecord(
        bizes_id="1",
        bizes_nm="테스트상점",
        inds_lcls_cd="I2",
        inds_lcls_nm="음식",
        inds_mcls_cd=mcls_cd,
        inds_mcls_nm=mcls_nm,
        inds_scls_cd=scls_cd,
        inds_scls_nm=scls_nm or mcls_nm,
        lon=126.9,
        lat=37.5,
        bld_mng_no="",
        flr_no="1",
        rdnm_adr="서울 서대문구",
    )


class TestNearestSegment:
    def test_picks_closest(self):
        segments = [
            make_segment(1, [[126.90, 37.50], [126.91, 37.50]]),
            make_segment(2, [[126.90, 37.60], [126.91, 37.60]]),
        ]
        result = nearest_segment(segments, 37.501, 126.905)
        assert result is not None
        assert result.osm_id == 1
        assert result.distance_m > 0

    def test_empty(self):
        assert nearest_segment([], 37.5, 126.9) is None


class TestTopPercent:
    def test_highest_value_is_top(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert top_percent(values, 5.0) == 20.0  # 1 of 5

    def test_lowest_value_is_bottom(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert top_percent(values, 1.0) == 100.0

    def test_empty_values(self):
        assert top_percent([], 1.0) == 100.0


class TestBottomPercent:
    def test_lowest_value_is_top(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert bottom_percent(values, 1.0) == 20.0

    def test_highest_value_is_bottom(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert bottom_percent(values, 5.0) == 100.0


class TestCornerInfo:
    def test_junction_detected(self):
        # 십자 교차로: 세그먼트 4개가 한 점에서 만남 → degree 4
        center = [126.90, 37.50]
        segments = [
            make_segment(1, [center, [126.91, 37.50]]),
            make_segment(2, [center, [126.89, 37.50]]),
            make_segment(3, [center, [126.90, 37.51]]),
            make_segment(4, [center, [126.90, 37.49]]),
        ]
        G, edge_map = build_graph(segments)
        from app.services.site_score import corner_info

        info = corner_info(G, edge_map, 0)
        assert info.is_corner
        assert info.junction_degree >= 3


class TestHeuristicScores:
    def test_transit_score_bounds(self):
        assert transit_score(2000) == 0.0
        assert transit_score(None) is None
        assert 0 < transit_score(800) < 100
        # 거리만 알 때는 중립 규모로 계산한다 — 만점을 주지 않는다.
        assert 60 < transit_score(100) < 90

    def test_transit_score_separates_stations_of_different_size(self):
        """같은 거리라면 이용객이 많은 역이 더 높아야 한다.

        예전 공식은 거리만 봐서 강남역과 한적한 단일노선 역이 똑같이
        100점이었다. 그 회귀를 막는 테스트다.
        """
        gangnam = transit_score(40, 156_554)
        sinchon = transit_score(40, 78_699)
        small = transit_score(40, 5_000)
        assert gangnam > sinchon > small
        assert gangnam - small > 30  # 유의미하게 갈려야 한다

    def test_transit_score_distance_decays(self):
        near = transit_score(100, 78_699)
        mid = transit_score(600, 78_699)
        far = transit_score(1_200, 78_699)
        assert near > mid > far
        assert transit_score(1_600, 78_699) == 0.0

    def test_bus_fills_only_where_subway_is_weak(self):
        """버스 보정이 역세권 점수를 상한으로 밀어붙이면 안 된다."""
        strong_no_bus = transit_score(40, 156_554, 0)
        strong_with_bus = transit_score(40, 156_554, 20)
        weak_no_bus = transit_score(1_200, 20_000, 0)
        weak_with_bus = transit_score(1_200, 20_000, 20)
        # 지하철이 강하면 버스가 거의 안 움직인다
        assert strong_with_bus - strong_no_bus < 5
        # 지하철이 약하면 버스가 실질적으로 메운다
        assert weak_with_bus - weak_no_bus > 8
        assert strong_with_bus < 100.0

    def test_bus_only_location_still_scores(self):
        """지하철이 아예 없어도 버스만으로 점수가 나와야 한다."""
        assert transit_score(None, None, 12) > 0
        assert transit_score(None, None, 0) is None

    def test_demand_score(self):
        assert demand_score(None, None) is None
        low = demand_score(1000, 500)
        high = demand_score(30000, 20000)
        assert low < high <= 100.0
        with_fit = demand_score(15000, 8000, demographic_fit=90.0)
        assert with_fit >= demand_score(15000, 8000)

    def test_demographic_fit_cafe(self):
        age = {"20대": 34.0, "30대": 18.0, "40대": 12.0}
        score, label = demographic_fit_score("food_drink", age)
        assert score is not None
        assert score > 50
        assert "20–30대" in label

    def test_competition_density_display_increases_with_count(self):
        empty = competition_density_display_score(competition_density_per_km2(0, 500))
        crowded = competition_density_display_score(competition_density_per_km2(50, 500))
        assert empty == 0.0
        assert crowded > empty

    def test_competition_advantage_inverse_of_display(self):
        assert competition_advantage_score(80.0) == 20.0

    def test_competition_score_advantage_decreases_when_crowded(self):
        empty = competition_score(0, 500)
        crowded = competition_score(50, 500)
        assert empty == 100.0
        assert crowded < empty

    def test_competition_intensity_label_bands(self):
        assert competition_intensity_label(60) == "매우 높음"
        assert competition_intensity_label(30) == "높음"
        assert competition_intensity_label(15) == "보통"
        assert competition_intensity_label(5) == "낮음"
        assert competition_intensity_label(1) == "매우 낮음"

    def test_format_rank_display(self):
        assert format_rank_display(8.5) == "상위 8.5%"

    def test_verdict_access_good_comp_high(self):
        v = build_verdict_one_liner(
            integration_top_pct=10,
            choice_top_pct=15,
            competition_intensity="매우 높음",
        )
        assert "유입 조건은 좋지만" in v
        assert "경쟁" in v


class TestSameCategory:
    def test_scls_code_matching(self):
        stores = [
            make_store("비알코올", "카페", scls_cd="I21201"),
            make_store("비알코올", "제과", scls_cd="I21202"),
            make_store("한식", "한식", mcls_cd="I210", scls_cd="I21001"),
        ]
        assert (
            count_same_category(stores, inds_mcls_cd="I212", inds_scls_cd="I21201") == 1
        )
        assert count_same_category(stores, inds_mcls_cd="I212") == 2

    def test_disabled_without_codes(self):
        stores = [make_store("카페", "카페")]
        assert count_same_category(stores, inds_mcls_cd=None) == 0
        assert match_same_category(stores, inds_mcls_cd=None).match_method == "disabled"


class TestComputeSiteScore:
    def _base_kwargs(self, **overrides):
        kwargs = dict(
            archetype="food_drink",
            business_label="카페",
            business_type="cafe",
            inds_lcls_cd="I2",
            inds_mcls_cd="I212",
            inds_scls_cd="I21201",
            upjong_breadcrumb="음식 > 비알코올·제과 > 카페",
            match_method="scls",
            integration_top_pct=10.0,
            choice_top_pct=50.0,
            mean_depth_top_pct=30.0,
            connectivity_top_pct=20.0,
            connectivity_degree=4,
            nearest_station_m=300.0,
            station_name="신촌역",
            same_category_count=5,
            total_store_count=100,
            population=15000,
            workers=8000,
            adm_name="서울 서대문구 신촌동",
            radius_m=500,
        )
        kwargs.update(overrides)
        return kwargs

    def test_full_data(self):
        result = compute_site_score(**self._base_kwargs())
        assert 0 <= result.total_score <= 100
        assert result.business_label == "카페"
        assert len(result.subscores) == 7
        comp = next(s for s in result.subscores if s.key == "competition")
        assert comp.score is not None
        assert comp.label == "경쟁 밀도"
        assert comp.display in ("낮음", "보통", "높음", "매우 높음", "매우 낮음")
        assert "도보 5분 내 동일업종 5곳" in comp.detail
        assert "전체 점포 100곳 (분석 반경 500m)" in comp.detail
        assert result.verdict_one_liner
        integration = next(s for s in result.subscores if s.key == "integration")
        assert integration.display == "상위 10.0%"
        assert integration.rank_top_pct == 10.0

    def test_general_disables_competition_score(self):
        result = compute_site_score(
            **self._base_kwargs(
                archetype="general",
                business_label="일반",
                business_type="general",
                inds_lcls_cd=None,
                inds_mcls_cd=None,
                inds_scls_cd=None,
                match_method="disabled",
                same_category_count=0,
            )
        )
        comp = next(s for s in result.subscores if s.key == "competition")
        assert comp.score is None
        assert "업종 미선택" in comp.detail

    def test_missing_demand_renormalizes(self):
        full = compute_site_score(**self._base_kwargs())
        partial = compute_site_score(
            **self._base_kwargs(population=None, workers=None, adm_name=None)
        )
        demand = next(s for s in partial.subscores if s.key == "demand")
        assert demand.score is None
        # 총점은 여전히 0-100 범위 내에서 계산됨
        assert 0 <= partial.total_score <= 100
        assert partial.total_score != 0

    def test_unknown_archetype_falls_back(self):
        result = compute_site_score(
            **self._base_kwargs(archetype="unknown_type", business_label="기타")
        )
        assert result.business_label == "기타"
        assert 0 <= result.total_score <= 100


class TestExplanation:
    def test_rule_based_mentions_score(self):
        result = compute_site_score(
            archetype="food_drink",
            business_label="카페",
            business_type="cafe",
            inds_mcls_cd="I212",
            inds_scls_cd="I21201",
            match_method="scls",
            integration_top_pct=10.0,
            choice_top_pct=50.0,
            mean_depth_top_pct=30.0,
            connectivity_top_pct=20.0,
            connectivity_degree=3,
            nearest_station_m=300.0,
            station_name="신촌역",
            same_category_count=5,
            total_store_count=100,
            population=15000,
            workers=8000,
            adm_name="신촌동",
            radius_m=500,
        )
        text = rule_based_explanation(result, None)
        assert "**한 줄 결론**" in text
        assert result.verdict_one_liner in text
        assert "**추천 전략**" in text

    def test_explain_falls_back_without_key(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "openai_api_key", "")
        result = compute_site_score(
            archetype="general",
            business_label="일반",
            business_type="general",
            match_method="disabled",
            integration_top_pct=20.0,
            choice_top_pct=30.0,
            mean_depth_top_pct=40.0,
            connectivity_top_pct=50.0,
            connectivity_degree=2,
            nearest_station_m=None,
            station_name=None,
            same_category_count=0,
            total_store_count=10,
            population=None,
            workers=None,
            adm_name=None,
            radius_m=500,
        )
        text, source = asyncio.run(explain_site_score(result, demand_facts=None))
        assert source == "rule"
        assert text


def test_depth_display_says_which_way_is_good():
    """이름과 숫자가 반대 방향을 가리키던 것을 문구로 막는다.

    "큰길에서 꺾어 들어오는 정도"는 깊을수록 나쁜 쪽을 가리키는데,
    상위%는 낮을수록 좋다(중심에 가깝다). 그냥 붙이면 "상위 16.3%"가 정반대로
    읽힌다 — 어젯밤 네 화면에서 같은 함정을 고쳤다.
    """
    from app.services.site_score import METRIC_LABELS, depth_display

    # 이름은 문장이 아니라 명사구여야 "상위 N%"와 붙여 읽힌다.
    assert METRIC_LABELS["mean_depth"] == "큰길에서 꺾어 들어오는 정도"

    shallow = depth_display(8.1)
    deep = depth_display(82.0)

    assert "얕은 편" in shallow and "깊은 편" in deep
    # 방향을 매번 적는다. 얕고 깊은 게 뭐가 좋은지는 자명하지 않다.
    assert "낮을수록 좋음" in shallow
    assert "낮을수록 좋음" in deep
    # 가운데를 한쪽으로 단정하지 않는다.
    assert "중간" in depth_display(50.0)
