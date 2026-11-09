"""Tests for real competition bucket classification."""

from app.services.real_competition import compute_real_competition, resolve_vertical
from app.services.sbiz_stores import StoreRecord


def _store(
    name: str,
    mcls: str,
    mcls_nm: str,
    scls: str = "",
    scls_nm: str = "",
    lat: float = 37.56,
    lon: float = 126.94,
) -> StoreRecord:
    return StoreRecord(
        bizes_id=name,
        bizes_nm=name,
        inds_lcls_cd="I2",
        inds_lcls_nm="음식",
        inds_mcls_cd=mcls,
        inds_mcls_nm=mcls_nm,
        inds_scls_cd=scls or mcls,
        inds_scls_nm=scls_nm or mcls_nm,
        lon=lon,
        lat=lat,
        bld_mng_no="",
        flr_no="",
        rdnm_adr="",
    )


def test_restaurant_direct_and_lunch_buckets():
    stores = [
        _store("한식당", "I201", "한식 음식점업"),
        _store("김밥천국", "I210", "기타 간이 음식점업", scls_nm="김밥/만두/분식"),
        _store("메가엠지씨커피", "I212", "비알코올 음료점업", scls="I21201", scls_nm="카페"),
    ]
    result = compute_real_competition(
        lat=37.56,
        lng=126.94,
        stores=stores,
        vertical="restaurant",
        radius_m=500,
    )
    direct = next(b for b in result.buckets if b.key == "direct")
    lunch = next(b for b in result.buckets if b.key == "lunch_rotation")
    assert direct.count >= 1
    assert lunch.count >= 1
    assert result.total_weighted_score > 0


def test_resolve_vertical_clinic_academy_bakery():
    assert resolve_vertical(archetype="medical", inds_mcls_cd="Q102", inds_scls_cd="Q10210") == "clinic"
    assert resolve_vertical(archetype="education", inds_mcls_cd="P105", inds_scls_cd="P10501") == "academy"
    assert resolve_vertical(archetype="food_drink", inds_mcls_cd="I210", inds_scls_cd="I21001") == "bakery"
    assert resolve_vertical(archetype="food_drink", inds_mcls_cd="I210", inds_scls_cd="I21005") == "restaurant"


def test_clinic_pharmacy_and_direct_buckets():
    stores = [
        _store("행복약국", "G215", "의약품 소매업", scls_nm="약국"),
        _store("마포내과", "Q102", "의원", scls_nm="내과/소아과 의원"),
        _store("피부관리실", "S207", "미용", scls_nm="피부관리실"),
    ]
    result = compute_real_competition(
        lat=37.56,
        lng=126.94,
        stores=stores,
        vertical="clinic",
        radius_m=500,
    )
    direct = next(b for b in result.buckets if b.key == "direct")
    pharm = next(b for b in result.buckets if b.key == "substitute_pharmacy")
    assert direct.count >= 1
    assert pharm.count >= 1


def test_academy_direct_and_study_substitute():
    stores = [
        _store("영어학원", "P105", "일반 교육기관", scls_nm="외국어학원"),
        _store("스터디카페", "I212", "비알코올 음료점업", scls_nm="카페"),
    ]
    result = compute_real_competition(
        lat=37.56,
        lng=126.94,
        stores=stores,
        vertical="academy",
        radius_m=500,
    )
    direct = next(b for b in result.buckets if b.key == "direct")
    assert direct.count >= 1


def test_cafe_low_price_brand_bucket():
    stores = [
        _store("메가엠지씨커피 마포점", "I212", "비알코올 음료점업"),
        _store("더벤티", "I212", "비알코올 음료점업"),
    ]
    result = compute_real_competition(
        lat=37.56,
        lng=126.94,
        stores=stores,
        vertical="cafe",
        radius_m=500,
    )
    low = next(b for b in result.buckets if b.key == "low_price_brand")
    assert low.count == 2
    assert low.weighted_score >= 2.0


def test_bakery_franchise_and_cafe_substitute():
    stores = [
        _store("파리바게뜨", "I210", "기타 간이 음식점업", scls="I21001", scls_nm="제과점"),
        _store("스타벅스", "I212", "비알코올 음료점업", scls="I21201", scls_nm="카페"),
    ]
    result = compute_real_competition(
        lat=37.56,
        lng=126.94,
        stores=stores,
        vertical="bakery",
        radius_m=500,
    )
    direct = next(b for b in result.buckets if b.key == "direct")
    franchise = next(b for b in result.buckets if b.key == "franchise_bakery")
    cafe = next(b for b in result.buckets if b.key == "substitute_cafe")
    assert direct.count >= 1
    assert franchise.count >= 1
    assert cafe.count >= 1


def test_polygon_reach_filters_stores():
    inside = _store("한식당", "I201", "한식 음식점업", lat=37.56, lon=126.94)
    outside = _store("먼식당", "I201", "한식 음식점업", lat=37.60, lon=127.00)
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [126.93, 37.55],
                [126.95, 37.55],
                [126.95, 37.57],
                [126.93, 37.57],
                [126.93, 37.55],
            ]
        ],
    }
    result = compute_real_competition(
        lat=37.56,
        lng=126.94,
        stores=[inside, outside],
        vertical="restaurant",
        reachable_polygon=polygon,
        reach_mode="walk",
        reach_minutes=10,
    )
    direct = next(b for b in result.buckets if b.key == "direct")
    assert direct.count == 1
    assert result.reach_label == "도보 10분"


class TestResolveVerticalProfileBacked:
    """resolve_vertical이 industry-profile matchRules를 단일 기준으로 쓴다."""

    def test_cafe_by_scls_and_mcls(self):
        assert resolve_vertical(
            archetype="food_drink", inds_mcls_cd="I212", inds_scls_cd="I21201"
        ) == "cafe"
        assert resolve_vertical(
            archetype="food_drink", inds_mcls_cd="I212", inds_scls_cd=None
        ) == "cafe"

    def test_bakery_scls_override(self):
        assert resolve_vertical(
            archetype="food_drink", inds_mcls_cd="I210", inds_scls_cd="I21001"
        ) == "bakery"

    def test_beauty_now_resolves(self):
        assert resolve_vertical(
            archetype="beauty_service", inds_mcls_cd="S207", inds_scls_cd=None
        ) == "beauty"
        assert resolve_vertical(
            archetype="beauty_service", inds_mcls_cd=None, inds_scls_cd=None
        ) == "beauty"

    def test_convenience_now_resolves(self):
        assert resolve_vertical(
            archetype="daily_retail", inds_mcls_cd="G204", inds_scls_cd="G20405"
        ) == "convenience"

    def test_restaurant_archetype_fallback(self):
        assert resolve_vertical(
            archetype="food_drink", inds_mcls_cd="I201", inds_scls_cd=None
        ) == "restaurant"

    def test_clinic_mcls_prefix_fallback(self):
        # 프로필 mcls 목록(Q101/Q102/Q104) 밖의 Q1xx도 구 하드코딩처럼 clinic
        assert resolve_vertical(
            archetype="medical", inds_mcls_cd="Q103", inds_scls_cd=None
        ) == "clinic"

    def test_general_stays_none(self):
        assert resolve_vertical(
            archetype="general", inds_mcls_cd=None, inds_scls_cd=None
        ) is None


def test_every_resolvable_vertical_is_fit_capable_or_known_degraded():
    """경쟁규칙 프로필이 있는 모든 vertical은 모델 적합도가 구현됐거나(라우터
    MODEL_FIT_VERTICALS), academy 전용 경로거나, 명시적 강등 목록에 있어야 한다.
    beauty가 이 셋 어디에도 없어서 _compute_model_fit의 ValueError가 500으로
    새어나간 회귀 방지. 새 competitor_rules 프로필을 추가하면 이 테스트가 깨지며
    적합도 구현 또는 강등 목록 갱신을 요구한다."""
    from app.routers.real_competition import MODEL_FIT_VERTICALS
    from app.services.real_competition import _available_verticals

    resolvable = set(_available_verticals())
    known_degraded = {"beauty", "convenience"}  # 경쟁 분석만 제공, 적합도 없음
    assert MODEL_FIT_VERTICALS <= resolvable
    assert resolvable - MODEL_FIT_VERTICALS - {"academy"} == known_degraded
