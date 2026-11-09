"""Tests for the generic complementary-business synergy engine (food_drink archetype)."""

from app.services.sbiz_stores import StoreRecord
from app.services.synergy_analysis import (
    classify_store,
    compute_synergy_cluster,
    resolve_relation,
)


def _store(
    name: str,
    mcls: str,
    mcls_nm: str,
    scls: str = "",
    scls_nm: str = "",
    lat: float = 37.5601,
    lon: float = 126.9401,
    bld_mng_no: str = "",
) -> StoreRecord:
    return StoreRecord(
        bizes_id=name,
        bizes_nm=name,
        inds_lcls_cd=mcls[0] if mcls else "I",
        inds_lcls_nm="",
        inds_mcls_cd=mcls,
        inds_mcls_nm=mcls_nm,
        inds_scls_cd=scls or mcls,
        inds_scls_nm=scls_nm or mcls_nm,
        lon=lon,
        lat=lat,
        bld_mng_no=bld_mng_no,
        flr_no="",
        rdnm_adr="",
    )


def test_classify_office_fitness_healthcare():
    office = _store("한빛법률사무소", "M103", "법무관련 서비스업", scls="M10301", scls_nm="변호사")
    gym = _store("스트롱헬스장", "R103", "스포츠 서비스업", scls="R10307", scls_nm="헬스장")
    clinic = _store("서울내과의원", "Q102", "의원", scls="Q10201", scls_nm="내과/소아과 의원")
    cafe = _store("옆집카페", "I212", "비알코올 음료점업", scls="I21201", scls_nm="카페")

    assert classify_store(office) == "office_business"
    assert classify_store(gym) == "fitness"
    assert classify_store(clinic) == "healthcare"
    assert classify_store(cafe) == "cafe"


def test_cafe_target_office_and_gym_are_complementary_not_competitor():
    assert resolve_relation("cafe", "cafe")[0] == "direct_competitor"
    assert resolve_relation("cafe", "office_business")[0] == "synergy"
    assert resolve_relation("cafe", "fitness")[0] == "arts_synergy"
    assert resolve_relation("cafe", "healthcare")[0] == "synergy"


def test_competitor_raises_pressure_and_complements_raise_synergy():
    stores = [
        _store("옆집카페", "I212", "비알코올 음료점업", scls="I21201", scls_nm="카페", lat=37.5602, lon=126.9402),
        _store(
            "한빛법률사무소",
            "M103",
            "법무관련 서비스업",
            scls="M10301",
            scls_nm="변호사",
            lat=37.5603,
            lon=126.9403,
        ),
        _store(
            "스트롱헬스장",
            "R103",
            "스포츠 서비스업",
            scls="R10307",
            scls_nm="헬스장",
            lat=37.5604,
            lon=126.9404,
        ),
    ]
    result = compute_synergy_cluster(
        lat=37.56, lng=126.94, stores=stores, target="cafe", radius_m=500
    )
    direct = next(c for c in result.clusters if c.key == "direct")
    synergy = next(c for c in result.clusters if c.key == "synergy")
    arts = next(c for c in result.clusters if c.key == "arts_synergy")

    assert direct.count == 1
    assert synergy.count == 1
    assert arts.count == 1
    assert result.scores.direct_competition_pressure > 0
    assert result.scores.complementary_synergy > 0

    # Removing the competing cafe should drop pressure to zero but keep synergy positive.
    result_no_competitor = compute_synergy_cluster(
        lat=37.56, lng=126.94, stores=stores[1:], target="cafe", radius_m=500
    )
    assert result_no_competitor.scores.direct_competition_pressure == 0.0
    assert result_no_competitor.scores.complementary_synergy > 0


def test_same_building_office_weighted_more_than_far_office():
    site_bld = "BLD123"
    stores = [
        _store(
            "건물내법률사무소",
            "M103",
            "법무관련 서비스업",
            scls="M10301",
            scls_nm="변호사",
            lat=37.56005,
            lon=126.94005,
            bld_mng_no=site_bld,
        ),
        _store(
            "먼법률사무소",
            "M103",
            "법무관련 서비스업",
            scls="M10301",
            scls_nm="변호사",
            lat=37.5615,
            lon=126.942,
            bld_mng_no="OTHER",
        ),
    ]
    result = compute_synergy_cluster(
        lat=37.56,
        lng=126.94,
        stores=stores,
        target="cafe",
        radius_m=500,
        bld_mng_no=site_bld,
    )
    synergy = next(c for c in result.clusters if c.key == "synergy")
    near = next(s for s in synergy.stores if s.name == "건물내법률사무소")
    far = next(s for s in synergy.stores if s.name == "먼법률사무소")
    assert near.same_building is True
    assert near.weight > far.weight


def test_restaurant_and_bakery_targets_also_work():
    stores = [
        _store("옆집식당", "I201", "한식 음식점업", lat=37.5602, lon=126.9402),
        _store("옆집빵집", "I210", "기타 간이 음식점업", scls="I21001", scls_nm="제과점", lat=37.5603, lon=126.9403),
        _store(
            "대학기숙사",
            "I102",
            "기타 숙박업",
            scls="I10201",
            scls_nm="기숙사/고시원",
            lat=37.5604,
            lon=126.9404,
        ),
    ]
    for target in ("restaurant", "bakery"):
        result = compute_synergy_cluster(
            lat=37.56, lng=126.94, stores=stores, target=target, radius_m=500
        )
        direct = next(c for c in result.clusters if c.key == "direct")
        assert direct.count == 1
        assert result.scores.complementary_synergy > 0


def test_invalid_target_raises():
    import pytest

    with pytest.raises(ValueError):
        compute_synergy_cluster(lat=37.56, lng=126.94, stores=[], target="academy", radius_m=500)


# ─────── site_bld_mng_no 추정(최근접 점포 건물번호로 대체)이 warnings에 안 남는 버그 ───────
#
# 후보지의 건물관리번호(bld_mng_no)를 입력하지 않으면 엔진은 반경 내 최근접 점포의
# 건물번호를 후보지 건물번호로 대체 추정한다. 이 추정치로 "동일건물" 여부를 판정해
# 경쟁·시너지 가중치를 계산하는데, 추정을 썼다는 사실이 warnings에 전혀 남지 않으면
# 화면에는 확정치처럼 보인다.


def test_missing_bld_mng_no_is_estimated_and_warned():
    site_bld = "BLD123"
    stores = [
        _store(
            "건물내법률사무소",
            "M103",
            "법무관련 서비스업",
            scls="M10301",
            scls_nm="변호사",
            lat=37.56005,
            lon=126.94005,
            bld_mng_no=site_bld,
        ),
    ]
    result = compute_synergy_cluster(
        lat=37.56,
        lng=126.94,
        stores=stores,
        target="cafe",
        radius_m=500,
        # bld_mng_no is omitted -> falls back to nearest-store inference.
    )
    synergy = next(c for c in result.clusters if c.key == "synergy")
    assert synergy.stores[0].same_building is True
    assert any("추정" in w and ("건물" in w) for w in result.warnings)


def test_explicit_bld_mng_no_produces_no_estimation_warning():
    site_bld = "BLD123"
    stores = [
        _store(
            "건물내법률사무소",
            "M103",
            "법무관련 서비스업",
            scls="M10301",
            scls_nm="변호사",
            lat=37.56005,
            lon=126.94005,
            bld_mng_no=site_bld,
        ),
    ]
    result = compute_synergy_cluster(
        lat=37.56,
        lng=126.94,
        stores=stores,
        target="cafe",
        radius_m=500,
        bld_mng_no=site_bld,
    )
    synergy = next(c for c in result.clusters if c.key == "synergy")
    assert synergy.stores[0].same_building is True
    assert result.warnings == []


def test_missing_bld_mng_no_with_no_candidate_produces_no_estimation_warning():
    stores = [
        _store(
            "건물내법률사무소",
            "M103",
            "법무관련 서비스업",
            scls="M10301",
            scls_nm="변호사",
            lat=37.56005,
            lon=126.94005,
            bld_mng_no="",
        ),
    ]
    result = compute_synergy_cluster(
        lat=37.56,
        lng=126.94,
        stores=stores,
        target="cafe",
        radius_m=500,
    )
    synergy = next(c for c in result.clusters if c.key == "synergy")
    assert synergy.stores[0].same_building is False
    assert result.warnings == []
