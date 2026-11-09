from types import SimpleNamespace

import app.services.territory_cannibalization as tc
from app.services.graph_builder import build_graph
from app.services.sbiz_stores import StoreRecord
from app.services.spatial_query import RoadSegment
from app.services.territory_cannibalization import (
    MODEL_FIT_REACH_MINUTES,
    MODEL_FIT_REACH_MODE,
    compute_territory_analysis,
    normalize_brand,
)


def _seg(osm_id: int, coords: list[list[float]]) -> RoadSegment:
    return RoadSegment(
        osm_id=osm_id,
        highway="residential",
        length_m=100.0,
        coordinates=coords,
        walkable=True,
        drivable=True,
    )


def _store(name: str, lat: float, lng: float) -> StoreRecord:
    return StoreRecord(
        bizes_id=name,
        bizes_nm=name,
        inds_lcls_cd="I2",
        inds_lcls_nm="음식",
        inds_mcls_cd="I210",
        inds_mcls_nm="기타 간이 음식점업",
        inds_scls_cd="I21001",
        inds_scls_nm="샐러드",
        lon=lng,
        lat=lat,
        bld_mng_no="",
        flr_no="",
        rdnm_adr="테스트",
    )


def _square(min_lng: float, min_lat: float, max_lng: float, max_lat: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [min_lng, min_lat],
            [max_lng, min_lat],
            [max_lng, max_lat],
            [min_lng, max_lat],
            [min_lng, min_lat],
        ]],
    }


def test_normalize_brand_strips_branch_suffix():
    assert normalize_brand("샐러디 신촌역점") == "샐러디"


def test_model_fit_constants_walk_15():
    assert MODEL_FIT_REACH_MODE == "walk"
    assert MODEL_FIT_REACH_MINUTES == 15


def test_territory_analysis_includes_before_and_after():
    c = [126.90, 37.50]
    segments = [
        _seg(1, [c, [126.905, 37.50]]),
        _seg(2, [c, [126.895, 37.50]]),
        _seg(3, [c, [126.90, 37.505]]),
    ]
    G, _ = build_graph(segments)
    stores = [_store("샐러디 신촌역점", 37.5001, 126.9002)]
    result = compute_territory_analysis(
        G=G,
        segments=segments,
        lat=37.50,
        lng=126.90,
        stores=stores,
        inds_mcls_cd="I210",
        inds_scls_cd="I21001",
        archetype="food_drink",
        brand_label="샐러디",
    )
    assert result.model_fit_minutes == 15
    assert len(result.after_territories) >= len(result.before_territories)
    assert any(t.is_new_entrant for t in result.after_territories)
    assert result.narrative


def test_overlap_area_excludes_existing_brand_only_overlap(monkeypatch):
    """headline·narrative가 보는 overlap_area는 후보 권역과 실제로 겹치는
    부분만 반영해야 한다. 후보와 무관하게 기존 브랜드끼리만 겹치는 구간(예:
    버거킹과 맥도날드가 후보에서 먼 곳에서 서로 겹치는 경우)이 합산되어
    "경쟁 과밀"로 판정이 뒤집히면 안 된다."""

    candidate_lat, candidate_lng = 37.50, 126.90
    candidate_poly = _square(126.895, 37.495, 126.905, 37.505)

    store_a = _store("맘스터치 신촌점", 37.5001, 126.9002)
    a_poly = _square(126.898, 37.498, 126.902, 37.503)  # 후보 권역 안에 완전히 포함

    store_b = _store("버거킹 신촌점", 37.503, 126.903)
    b_poly = _square(130.00, 40.00, 130.05, 40.05)  # 후보와 전혀 겹치지 않는 먼 곳

    store_c = _store("맥도날드 신촌점", 37.502, 126.904)
    c_poly = _square(130.02, 40.00, 130.07, 40.05)  # b_poly와만 크게 겹침(후보 무관)

    polygons = {
        (candidate_lat, candidate_lng): candidate_poly,
        (store_a.lat, store_a.lon): a_poly,
        (store_b.lat, store_b.lon): b_poly,
        (store_c.lat, store_c.lon): c_poly,
    }

    def fake_compute_isochrones(*, G=None, segments=None, lat, lng, minutes_list, mode):
        poly = polygons.get((lat, lng))
        return SimpleNamespace(bands=[SimpleNamespace(polygon=poly)] if poly else [])

    monkeypatch.setattr(tc, "compute_isochrones", fake_compute_isochrones)

    result = tc.compute_territory_analysis(
        G=None,
        segments=None,
        lat=candidate_lat,
        lng=candidate_lng,
        stores=[store_a, store_b, store_c],
        inds_mcls_cd="I210",
        inds_scls_cd="I21001",
        archetype="food_drink",
        brand_label="테스트버거",
    )

    # 버거킹 vs 맥도날드(후보와 무관)의 큰 겹침이 실제로 잡혔는지 확인 —
    # 이게 없으면 애초에 이 테스트가 버그를 재현하지 못한다.
    existing_only = [
        z for z in result.zones
        if z.zone_type == "overlap" and z.label == "기존 브랜드 간 겹침"
    ]
    assert existing_only
    assert existing_only[0].area_m2 > 5_000_000

    candidate_overlap = [
        z for z in result.zones
        if z.zone_type == "overlap" and z.label == "과밀 경쟁 권역"
    ]
    gap = [z for z in result.zones if z.zone_type == "gap"]
    assert candidate_overlap and gap

    # 후보 권역 기준 실제 겹침(맘스터치만)은 공백보다 훨씬 작아야 한다.
    assert candidate_overlap[0].area_m2 < gap[0].area_m2
    # 반면 기존 브랜드끼리의 겹침은 공백보다 훨씬 크다 — 합산했다면 판정이 뒤집혔을 규모.
    assert existing_only[0].area_m2 > gap[0].area_m2

    # headline/narrative는 후보 기준 겹침 vs 공백(위 비교)을 따라야지, 후보와
    # 무관한 기존 브랜드끼리의 겹침에 휘둘려 "경쟁 과밀"로 뒤집히면 안 된다.
    assert "공백 권역을 노릴 수 있는 편" in result.brand_fit_headline
    assert any("선점 전략이 유효" in n for n in result.narrative)
    assert not any("과밀 구간이 넓어" in n for n in result.narrative)
