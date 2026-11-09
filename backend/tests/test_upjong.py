"""Tests for SBIZ upjong taxonomy service."""

import asyncio

from app.services.sbiz_upjong import (
    QUICK_PRESETS,
    _load_fallback_tree,
    archetype_for_mcls,
    get_upjong_tree,
    invalidate_upjong_cache,
    resolve_upjong_selection,
    search_upjong,
)


SAMPLE_TREE = [
    {
        "code": "I2",
        "name": "음식",
        "middle": [
            {
                "code": "I212",
                "name": "비알코올·제과",
                "small": [
                    {"code": "I21201", "name": "카페"},
                    {"code": "I21202", "name": "제과·베이커리"},
                ],
            }
        ],
    }
]


class TestUpjongSelection:
    def test_preset_cafe(self):
        sel = resolve_upjong_selection(business_type="cafe", tree=SAMPLE_TREE)
        assert sel.inds_scls_cd == "I21201"
        assert sel.archetype == "food_drink"
        assert sel.match_method == "scls"

    def test_mcls_only(self):
        sel = resolve_upjong_selection(
            inds_mcls_cd="I212", inds_lcls_cd="I2", tree=SAMPLE_TREE
        )
        assert sel.label == "비알코올·제과"
        assert sel.match_method == "mcls"

    def test_general_disabled(self):
        sel = resolve_upjong_selection(business_type="general", tree=SAMPLE_TREE)
        assert sel.match_method == "disabled"

    def test_search_finds_cafe(self):
        hits = search_upjong(SAMPLE_TREE, "카페")
        assert any(h["inds_scls_cd"] == "I21201" for h in hits)

    def test_search_alias_orthopedics_and_jjimdak(self):
        tree = _load_fallback_tree()
        ortho = search_upjong(tree, "정형외과")
        assert any(h["inds_scls_cd"] == "Q10202" for h in ortho)
        assert any(h.get("matched_alias") == "정형외과" for h in ortho)
        jjim = search_upjong(tree, "찜닭")
        assert any(h["inds_scls_cd"] == "I20110" for h in jjim)
        assert any(h.get("matched_alias") == "찜닭전문점" for h in jjim)

    def test_search_salad_maps_to_food_snack_category(self):
        tree = _load_fallback_tree()
        hits = search_upjong(tree, "샐러드")
        assert any(h["inds_scls_cd"] == "I21005" for h in hits)
        sel = resolve_upjong_selection(
            inds_lcls_cd="I2",
            inds_mcls_cd="I210",
            inds_scls_cd="I21005",
            tree=tree,
        )
        assert sel.archetype == "food_drink"

    def test_resolve_scls_only_fills_parent_codes(self):
        tree = _load_fallback_tree()
        sel = resolve_upjong_selection(inds_scls_cd="I21005", tree=tree)
        assert sel.inds_mcls_cd == "I210"
        assert sel.inds_lcls_cd == "I2"
        assert sel.archetype == "food_drink"

    def test_archetype_mapping(self):
        assert archetype_for_mcls("I212") == "food_drink"
        assert archetype_for_mcls("G204") == "daily_retail"
        assert archetype_for_mcls("UNKNOWN") == "general"

    def test_quick_presets_have_codes(self):
        assert len(QUICK_PRESETS) >= 5
        for p in QUICK_PRESETS:
            assert p["inds_mcls_cd"]


class TestUpjongTree:
    def test_fallback_json_loads(self):
        tree = _load_fallback_tree()
        assert len(tree) >= 1
        assert "middle" in tree[0]

    def test_get_upjong_tree_uses_fallback_fast(self):
        invalidate_upjong_cache()
        tree = asyncio.run(get_upjong_tree())
        assert len(tree) >= 1


def test_presets_cover_the_business_types_the_chat_can_produce():
    # 프리셋이 없으면 match_method가 disabled로 떨어지고, 엔진은 동일업종을 아예
    # 세지 않은 채 count=0을 준다 — 그게 "이태원 노래방 0곳, 경쟁 유리"라는
    # 정반대 조언으로 이어졌다. 챗이 만들 수 있는 업종은 프리셋을 가져야 한다.
    from app.services.sbiz_upjong import BUSINESS_TYPE_PRESETS

    for bt in ("leisure", "fitness", "gas_station", "professional_office", "lodging"):
        preset = BUSINESS_TYPE_PRESETS[bt]
        assert preset.get("inds_mcls_cd") or preset.get("inds_scls_cd"), bt

    # 전기차 충전소는 sbiz 업종 자체가 없다(G21402는 LPG 가스충전소).
    # 없는 걸 있는 척 세느니 '집계되지 않음'이 낫다.
    assert "ev_charging" not in BUSINESS_TYPE_PRESETS


def test_preset_codes_match_the_store_data():
    # 코드는 추측이 아니라 sbiz_stores에 실제로 있는 값에서 골랐다 — 오타가 나면
    # 조용히 0곳이 되므로 값 자체를 고정한다.
    from app.services.sbiz_upjong import BUSINESS_TYPE_PRESETS as P

    assert P["leisure"]["inds_mcls_cd"] == "R104"        # 유원지·오락(노래방·PC방)
    assert P["fitness"]["inds_scls_cd"] == "R10307"      # 헬스장
    assert P["gas_station"]["inds_scls_cd"] == "G21401"  # 주유소
    assert P["professional_office"]["inds_scls_cd"] == "L10203"  # 부동산 중개
    assert P["lodging"]["inds_mcls_cd"] == "I101"        # 일반 숙박


def test_restaurant_preset_no_longer_locks_to_korean_food():
    """restaurant 프리셋이 I201(한식)에 고정돼 있으면 치킨집·중식당도 "한식"
    으로 표시되고 동일업종 집계는 한식 매장 수만 셌다 — 실제로 "홍제동
    치킨집"이 "동일업종 한식 71곳"으로 나온 사고였다(docs/agent/GOALS.md
    2026-07-29 오너 지시 1번). store_matches_upjong은 단일 코드만 매칭해
    한식·중식·양식을 하나로 묶어 셀 방법이 없으므로, 특정 소분류를 단정하는
    대신 동일업종 집계 자체를 끈다 — 세지 못하는 걸 한식으로 세는 척하지
    않는다. 대신 라벨과 archetype(가중치 프로필)은 그대로 정확히 잡는다.
    """
    from app.services.sbiz_stores import StoreRecord
    from app.services.site_score import match_same_category

    tree = asyncio.run(get_upjong_tree())
    sel = resolve_upjong_selection(business_type="restaurant", tree=tree)

    assert sel.inds_mcls_cd is None
    assert sel.inds_scls_cd is None
    assert sel.label != "한식"
    assert sel.archetype == "food_drink"

    def store(mcls: str, mcls_nm: str) -> StoreRecord:
        return StoreRecord(
            bizes_id="1",
            bizes_nm="테스트 식당",
            inds_lcls_cd="I2",
            inds_lcls_nm="음식점업",
            inds_mcls_cd=mcls,
            inds_mcls_nm=mcls_nm,
            inds_scls_cd="",
            inds_scls_nm="",
            lon=127.0,
            lat=37.5,
            bld_mng_no="",
            flr_no="",
            rdnm_adr="",
        )

    stores = [
        store("I201", "한식 음식점업"),
        store("I202", "중식 음식점업"),
        store("I204", "서양식 음식점업"),
    ]
    result = match_same_category(
        stores,
        inds_mcls_cd=sel.inds_mcls_cd,
        inds_scls_cd=sel.inds_scls_cd,
        match_method=sel.match_method,
    )
    # 한식 매장만 골라 세지 않는다 — 못 세면 0을 "한식 경쟁 없음"으로
    # 포장하는 대신 아예 집계를 끈다(match_same_category가 disabled면
    # compute_site_score는 이 subscore를 총점 계산에서 제외한다).
    assert result.match_method == "disabled"
    assert result.count == 0


def test_unknown_code_disables_counting_instead_of_reporting_zero():
    """트리에 없는 코드는 세지 말고 '집계 안 함'으로 둔다.

    그대로 세면 한 곳도 안 맞아 '동일업종 0곳 · 경쟁 매우 낮음'이 나온다 —
    ZZ99999로 조회하면 점수 73.4에 '매우 낮음'이 붙었다.
    """
    tree = asyncio.run(get_upjong_tree())
    assert resolve_upjong_selection(inds_scls_cd="ZZ99999", tree=tree).match_method == "disabled"
    # 실재하는 코드는 그대로 센다.
    assert resolve_upjong_selection(inds_scls_cd="I21201", tree=tree).match_method == "scls"
    assert resolve_upjong_selection(business_type="leisure", tree=tree).match_method == "mcls"


def test_code_exists_in_tree_checks_both_levels():
    from app.services.sbiz_upjong import code_exists_in_tree

    tree = asyncio.run(get_upjong_tree())
    assert code_exists_in_tree(tree, inds_mcls_cd=None, inds_scls_cd="I21201")
    assert not code_exists_in_tree(tree, inds_mcls_cd=None, inds_scls_cd="ZZ99999")
    assert code_exists_in_tree(tree, inds_mcls_cd="R104", inds_scls_cd=None)
    assert not code_exists_in_tree(tree, inds_mcls_cd="ZZ99", inds_scls_cd=None)


def test_user_facing_errors_are_korean_everywhere():
    """같은 상황을 라우터마다 따로 적었더니 문구가 어긋났다 — 입지 분석은
    한국어인데 신설도로 시뮬레이션은 영어가 그대로 떴다."""
    from app.errors import no_roads_error, road_fetch_error, segment_cap_warning

    assert "서울" in no_roads_error().detail
    assert no_roads_error().status_code == 404

    # 좌표가 지구 밖이면 사용자 입력 문제 → 400, 내부 예외 문구는 감춘다.
    bad = road_fetch_error(Exception("transform: latitude or longitude exceeded limits (-14)"))
    assert bad.status_code == 400 and "좌표" in bad.detail
    assert "transform" not in bad.detail

    other = road_fetch_error(Exception("connection reset"))
    assert other.status_code == 503 and "connection reset" not in other.detail

    assert "1,000개" in segment_cap_warning(1000, 1398)

    from app.errors import analysis_failed_error, too_many_segments_error

    many = too_many_segments_error(12345)
    assert many.status_code == 422 and "반경을 줄여" in many.detail

    # 계산 실패도 내부 예외를 감춘다 — 전에는 'Graph construction failed: <예외>'가
    # 그대로 화면에 나갔다.
    failed = analysis_failed_error(Exception("networkx blew up"), what="도로망 분석")
    assert failed.status_code == 503
    assert "networkx" not in failed.detail and "도로망 분석" in failed.detail


def test_unreliable_reach_falls_back_to_the_radius_count():
    """도달권이 못 믿을 때는 0곳이 아니라 반경 기준 실측값으로 채점한다.

    홍대입구 정육점: 도보 5분 도달권 0곳 · 반경 4곳. 라벨만 비우고 0곳을
    그대로 두니 경쟁 점수는 만점(경쟁 없음)으로 총점에 들어가고, 설명에는
    "도보 5분 내 동일업종 0곳"이 사실처럼 적혔다 — 바로 아래 경고가 그 0곳을
    못 믿는다고 말하는데도. 값과 문구가 함께 반경 기준으로 바뀌어야 한다.
    """
    from app.services.site_score import compute_site_score

    base = dict(
        archetype="food_drink",
        business_label="카페",
        business_type="cafe",
        inds_lcls_cd="I2",
        inds_mcls_cd="I212",
        inds_scls_cd="I21201",
        match_method="scls",
        integration_top_pct=10.0,
        choice_top_pct=50.0,
        mean_depth_top_pct=30.0,
        connectivity_top_pct=20.0,
        connectivity_degree=4,
        nearest_station_m=300.0,
        station_name="신촌역",
        total_store_count=100,
        population=15000,
        workers=8000,
        adm_name="서울 마포구 서교동",
        radius_m=500,
    )

    def competition(**over):
        result = compute_site_score(**{**base, **over})
        comp = next(s for s in result.subscores if s.key == "competition")
        return result, comp

    zero_result, zero = competition(same_category_count=0)
    real_result, real = competition(same_category_count=40, competition_scope="radius")

    # 문구가 어디서 센 값인지 밝힌다 — 반경 기준인데 "도보 5분"이라고 적으면 안 된다.
    assert "도보 5분" in zero.detail
    assert "반경 500m 내 동일업종 40곳" in real.detail
    assert "도보 5분" not in real.detail

    # 경쟁 밀도 점수는 높을수록 경쟁이 심하다. 0곳이면 바닥, 실측 40곳이면 확실히 높다.
    assert zero.score == 0
    assert real.score is not None and real.score > 50

    # 그리고 그 차이가 총점에 반영돼야 한다 — 0곳으로 두면 경쟁 만점이 얹힌다.
    assert real_result.total_score < zero_result.total_score

    # 강도 라벨도 비우지 않는다. 근거 있는 값이므로 매길 수 있다.
    assert real.display and real.display != "—"


def test_market_context_facts_always_carry_a_seoul_position():
    """유동인구·소비는 절대수만 내보내지 않는다 — 서울 상위 %가 함께 가야 한다.

    '유동인구 11만명'만 보면 좋은지 나쁜지 알 수 없다는 사용자 지적을 반영한 것.
    성별×연령 교차(20대 여성)는 공개 데이터에 없어 각각 따로 낸다.
    """
    from types import SimpleNamespace

    from app.models.schemas import DongMarketContextOut, SeoulRelativeOut
    from app.routers.site_score import _demand_facts_for_llm, _market_context_out

    ctx = SimpleNamespace(
        dong_name="신촌동", quarter="20254",
        footfall_daily=SimpleNamespace(value=114181.6, top_pct=8, seoul_median=40000.0, vs_median_pct=185.5),
        female_pct=SimpleNamespace(value=58.9, top_pct=1, seoul_median=50.0, vs_median_pct=17.8),
        age_pcts={"20대": SimpleNamespace(value=45.8, top_pct=1, seoul_median=18.0, vs_median_pct=154.4),
                  "60대": SimpleNamespace(value=5.0, top_pct=90, seoul_median=14.0, vs_median_pct=-64.3)},
        local_spend_monthly=None,
    )
    out = _market_context_out(ctx)
    assert isinstance(out, DongMarketContextOut)
    assert isinstance(out.footfall_daily, SeoulRelativeOut)

    demand = SimpleNamespace(
        data_scope="행정동", adm_name="신촌동", radius_m=500, population=1, workers=1,
        households=1, avg_age=None, gender=None, age_bands=[], market_context=out,
        seoul_rank=None, apartment_market=None, demographic_fit=None,
    )
    facts = _demand_facts_for_llm(demand)
    assert "서울 상위 8%" in facts and "여성 59%" in facts
    # 상위 30% 밖 연령대는 나열하지 않는다 — 서술이 표가 되지 않게.
    assert "20대" in facts and "60대" not in facts
