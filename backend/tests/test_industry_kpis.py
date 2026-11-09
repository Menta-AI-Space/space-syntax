from app.services.industry_kpis import (
    KpiInputs,
    compute_industry_kpis,
)
from app.services.industry_profiles import load_all_profiles
from app.services.sbiz_stores import StoreRecord
from app.services.site_score import nearest_same_category_distance_m


def make_store(mcls_cd: str, lat: float | None, lon: float | None) -> StoreRecord:
    return StoreRecord(
        bizes_id="1",
        bizes_nm="테스트",
        inds_lcls_cd="I2",
        inds_lcls_nm="",
        inds_mcls_cd=mcls_cd,
        inds_mcls_nm="",
        inds_scls_cd="",
        inds_scls_nm="",
        lon=lon,
        lat=lat,
        bld_mng_no="",
        flr_no="",
        rdnm_adr="",
    )


def test_nearest_same_category_distance_picks_closest_matching_store():
    base_lat, base_lng = 37.5559, 126.9370
    stores = [
        make_store("I212", base_lat + 0.002, base_lng),  # ~222m, matches
        make_store("I212", base_lat + 0.0005, base_lng),  # ~56m, matches
        make_store("G204", base_lat + 0.0001, base_lng),  # closer but wrong category
        make_store("I212", None, None),  # no coordinates
    ]
    d = nearest_same_category_distance_m(
        stores, lat=base_lat, lng=base_lng, inds_mcls_cd="I212", match_method="mcls"
    )
    assert d is not None
    assert 50 < d < 65


def test_nearest_same_category_distance_none_when_disabled_or_no_match():
    stores = [make_store("I212", 37.556, 126.937)]
    assert (
        nearest_same_category_distance_m(
            stores, lat=37.5559, lng=126.9370, inds_mcls_cd="I212", match_method="disabled"
        )
        is None
    )
    assert (
        nearest_same_category_distance_m(
            stores, lat=37.5559, lng=126.9370, inds_mcls_cd="Q101", match_method="mcls"
        )
        is None
    )


def full_inputs() -> KpiInputs:
    return KpiInputs(
        population=15_000,
        workers=8_000,
        households=6_000,
        female_pct=51.0,
        age_band_pcts={"20대": 18.0, "30대": 16.0, "40대": 14.0},
        integration_top_pct=12.0,
        choice_top_pct=20.0,
        connectivity_top_pct=15.0,
        junction_degree=4,
        is_corner=True,
        nearest_station_m=250.0,
        nearest_school_m=400.0,
        total_store_count=350,
        same_category_count=14,
        competition_area_m2=180_000.0,
        radius_m=500,
        match_method="scls",
    )


def test_competition_kpis_none_when_store_fetch_failed():
    # SBIZ fetch failure produces an empty stores list — must read as
    # "no data", never as "no competitors" (100점 경쟁 없음).
    inputs = full_inputs()
    inputs.total_store_count = 0
    inputs.same_category_count = 0
    inputs.min_same_category_distance_m = None
    cafe = kpi_map(inputs)["cafe_saturation"]
    assert cafe.score is None
    assert "조회 실패" in cafe.detail
    conv = profile_kpi_map("convenience", inputs)["competition_distance"]
    assert conv.score is None
    assert "조회 실패" in conv.detail


def cafe_profile() -> dict:
    return load_all_profiles()["cafe"]


def kpi_map(inputs: KpiInputs) -> dict:
    return {k.key: k for k in compute_industry_kpis(cafe_profile(), inputs)}


def test_cafe_entrepreneur_kpis_all_computed_with_full_inputs():
    kpis = kpi_map(full_inputs())
    for key in ("takeout_demand", "stay_demand", "cafe_saturation", "visibility"):
        assert key in kpis, f"cafe profile should include {key}"
        kpi = kpis[key]
        assert kpi.score is not None
        assert 0 <= kpi.score <= 100
        assert kpi.display not in ("", "—")
        assert kpi.detail


def test_takeout_demand_favors_worker_heavy_areas():
    office = kpi_map(full_inputs())["takeout_demand"]
    inputs = full_inputs()
    inputs.workers = 300
    residential = kpi_map(inputs)["takeout_demand"]
    assert office.score > residential.score


def test_stay_demand_uses_population_and_young_share():
    base = kpi_map(full_inputs())["stay_demand"]
    inputs = full_inputs()
    inputs.population = 1_000
    inputs.age_band_pcts = {"60대": 40.0}
    weak = kpi_map(inputs)["stay_demand"]
    assert base.score > weak.score


def test_stay_demand_none_when_no_demand_data():
    inputs = full_inputs()
    inputs.population = None
    inputs.age_band_pcts = {}
    inputs.integration_top_pct = None
    kpi = kpi_map(inputs)["stay_demand"]
    assert kpi.score is None
    assert kpi.display == "—"


def test_takeout_demand_degrades_but_survives_missing_workers():
    inputs = full_inputs()
    inputs.workers = None
    kpi = kpi_map(inputs)["takeout_demand"]
    # choice + transit components remain
    assert kpi.score is not None
    assert "종사자" not in kpi.detail


def test_cafe_saturation_uses_isochrone_density_and_intensity_label():
    kpi = kpi_map(full_inputs())["cafe_saturation"]
    # 14 stores / 0.18 km² ≈ 77.8/km² → 매우 높음, score capped at 100
    assert kpi.score == 97.2
    assert kpi.display == "매우 높음"
    assert "14곳" in kpi.detail


def test_cafe_saturation_disabled_without_upjong():
    inputs = full_inputs()
    inputs.match_method = "disabled"
    kpi = kpi_map(inputs)["cafe_saturation"]
    assert kpi.score is None
    assert "업종 미선택" in kpi.detail


def test_visibility_corner_beats_midblock():
    corner = kpi_map(full_inputs())["visibility"]
    inputs = full_inputs()
    inputs.is_corner = False
    inputs.junction_degree = 2
    midblock = kpi_map(inputs)["visibility"]
    assert corner.score > midblock.score
    assert "코너 입지" in corner.detail
    assert "일반 입지" in midblock.detail


def test_franchise_override_keys_present_and_gated_on_brand():
    kpis = kpi_map(full_inputs())
    # userOverrides.franchise keys are always present; without a brand input
    # they stay honest None (never a fake zero) with an actionable reason.
    assert "cannibalization_risk" in kpis
    kpi = kpis["cannibalization_risk"]
    assert kpi.score is None
    assert "브랜드명 입력 필요" in kpi.detail


def profile_kpi_map(profile_id: str, inputs: KpiInputs) -> dict:
    profile = load_all_profiles()[profile_id]
    return {k.key: k for k in compute_industry_kpis(profile, inputs)}


def test_all_vertical_entrepreneur_kpis_have_implementations_or_honest_none():
    implemented_per_vertical = {
        "restaurant": {"lunch_demand", "dinner_demand", "delivery_fit", "competition_pressure"},
        "clinic": {"care_population", "same_specialty_supply", "medical_gap"},
        "academy": {"school_age_population", "same_subject_competition", "commute_access", "school_access"},
        "beauty": {"female_population", "repeat_demand"},
        "convenience": {"walk_population", "corner_visibility", "competition_distance"},
    }
    for vertical, implemented in implemented_per_vertical.items():
        kpis = profile_kpi_map(vertical, full_inputs())
        for key in implemented:
            assert kpis[key].score is not None, f"{vertical}.{key} should compute"
            assert 0 <= kpis[key].score <= 100
            assert kpis[key].detail
        for key, kpi in kpis.items():
            if kpi.score is None:
                assert kpi.detail, f"{vertical}.{key} None-score needs a reason"


def test_takeout_demand_blends_morning_presence_when_available():
    with_slot = full_inputs()
    with_slot.morning_pop = 45_000.0
    kpi = kpi_map(with_slot)["takeout_demand"]
    assert "출근시간(7–9시) 체류 45,000명" in kpi.detail
    weak_slot = full_inputs()
    weak_slot.morning_pop = 500.0
    assert kpi.score > kpi_map(weak_slot)["takeout_demand"].score
    # 슬롯 데이터가 없으면 기존 프록시 컴포넌트만으로 계산 (None 아님)
    without = kpi_map(full_inputs())["takeout_demand"]
    assert without.score is not None
    assert "출근시간" not in without.detail


def test_restaurant_timeslot_presence_blended_when_available():
    inputs = full_inputs()
    inputs.lunch_pop = 80_000.0
    inputs.dinner_pop = 60_000.0
    kpis = profile_kpi_map("restaurant", inputs)
    assert "점심(11–14시) 체류 80,000명" in kpis["lunch_demand"].detail
    assert "저녁(17–21시) 체류 60,000명" in kpis["dinner_demand"].detail
    quiet = full_inputs()
    quiet.lunch_pop = 300.0
    quiet.dinner_pop = 300.0
    quiet_kpis = profile_kpi_map("restaurant", quiet)
    assert kpis["lunch_demand"].score > quiet_kpis["lunch_demand"].score
    assert kpis["dinner_demand"].score > quiet_kpis["dinner_demand"].score


def test_restaurant_lunch_favors_workers_dinner_favors_residents():
    office = full_inputs()
    office.workers = 30_000
    office.population = 2_000
    residential = full_inputs()
    residential.workers = 500
    residential.population = 30_000
    office_kpis = profile_kpi_map("restaurant", office)
    resi_kpis = profile_kpi_map("restaurant", residential)
    assert office_kpis["lunch_demand"].score > resi_kpis["lunch_demand"].score
    assert resi_kpis["dinner_demand"].score > office_kpis["dinner_demand"].score


def test_clinic_medical_gap_high_when_demand_high_supply_low():
    underserved = full_inputs()
    underserved.same_category_count = 0
    saturated = full_inputs()
    saturated.same_category_count = 40
    gap_open = profile_kpi_map("clinic", underserved)["medical_gap"]
    gap_closed = profile_kpi_map("clinic", saturated)["medical_gap"]
    assert gap_open.score > gap_closed.score


def test_clinic_weekend_gap_honest_none():
    kpi = profile_kpi_map("clinic", full_inputs())["weekend_gap"]
    assert kpi.score is None
    assert "진료시간" in kpi.detail


def test_ev_charger_whitespace_scores_by_station_density():
    from app.services.industry_kpis import _kpi_charger_whitespace

    empty = KpiInputs(ev_charger_count=0, ev_fast_charger_count=0, radius_m=500)
    score, disp, detail = _kpi_charger_whitespace(empty)
    assert score == 100.0
    assert disp == "충전 공백"

    sparse = KpiInputs(ev_charger_count=10, ev_fast_charger_count=0, radius_m=500)
    dense = KpiInputs(ev_charger_count=67, ev_fast_charger_count=4, radius_m=500)
    sparse_score = _kpi_charger_whitespace(sparse)[0]
    dense_score = _kpi_charger_whitespace(dense)[0]
    assert sparse_score > dense_score  # 충전소 적을수록 공백 기회 큼
    assert "충전소 10곳" in _kpi_charger_whitespace(sparse)[2]

    unknown = KpiInputs(ev_charger_count=None, radius_m=500)
    kpi = _kpi_charger_whitespace(unknown)
    assert kpi[0] is None
    assert "미적재" in kpi[2]


def test_clinic_weekend_gap_computes_with_demand_and_hira_supply():
    open_gap = full_inputs()
    open_gap.weekend_prime_pop = 40_000.0
    open_gap.clinic_count_hira = 25
    open_gap.clinic_sat_open = 0
    open_gap.clinic_sun_open = 0
    covered = full_inputs()
    covered.weekend_prime_pop = 40_000.0
    covered.clinic_count_hira = 25
    covered.clinic_sat_open = 6
    covered.clinic_sun_open = 2
    kpi_open = profile_kpi_map("clinic", open_gap)["weekend_gap"]
    kpi_covered = profile_kpi_map("clinic", covered)["weekend_gap"]
    assert kpi_open.score is not None and kpi_covered.score is not None
    assert kpi_open.score > kpi_covered.score
    assert "주말진료 확인 토 6/일 2곳" in kpi_covered.detail
    assert "신고 기준" in kpi_covered.detail


def test_academy_school_age_reacts_to_age_mix():
    school_zone = full_inputs()
    school_zone.age_band_pcts = {"10세 미만": 12.0, "10대": 15.0}
    office_zone = full_inputs()
    office_zone.age_band_pcts = {"30대": 30.0, "40대": 20.0}
    strong = profile_kpi_map("academy", school_zone)["school_age_population"]
    weak = profile_kpi_map("academy", office_zone)["school_age_population"]
    assert strong.score > weak.score
    assert "학령" in strong.detail


def test_beauty_price_positioning_from_per_store_sales():
    premium = full_inputs()
    premium.trdar_per_store_won = 36_000_000
    premium.citywide_per_store_won = 18_000_000
    budget = full_inputs()
    budget.trdar_per_store_won = 9_000_000
    budget.citywide_per_store_won = 18_000_000
    kpi_premium = profile_kpi_map("beauty", premium)["price_gap"]
    kpi_budget = profile_kpi_map("beauty", budget)["price_gap"]
    assert kpi_premium.score == 100.0
    assert kpi_premium.display == "서울 중앙값의 2.0배"
    assert "프리미엄 여력" in kpi_premium.detail
    assert kpi_budget.score == 25.0
    assert "저가 경쟁 시장" in kpi_budget.detail
    # 상권 데이터 없으면 정직한 None
    kpi_none = profile_kpi_map("beauty", full_inputs())["price_gap"]
    assert kpi_none.score is None
    assert "상권" in kpi_none.detail


def test_beauty_female_population_uses_gender_split():
    kpi = profile_kpi_map("beauty", full_inputs())["female_population"]
    assert kpi.score is not None
    assert "여성 51%" in kpi.detail
    no_gender = full_inputs()
    no_gender.female_pct = None
    degraded = profile_kpi_map("beauty", no_gender)["female_population"]
    assert degraded.score is not None
    assert "여성" not in degraded.detail


def test_convenience_competition_distance_scales_with_proximity():
    near = full_inputs()
    near.min_same_category_distance_m = 60.0
    far = full_inputs()
    far.min_same_category_distance_m = 450.0
    none_nearby = full_inputs()
    none_nearby.min_same_category_distance_m = None
    kpis_near = profile_kpi_map("convenience", near)["competition_distance"]
    kpis_far = profile_kpi_map("convenience", far)["competition_distance"]
    kpis_none = profile_kpi_map("convenience", none_nearby)["competition_distance"]
    assert kpis_near.score == 12.0
    assert kpis_far.score == 90.0
    assert kpis_none.score == 100.0
    assert kpis_none.display == "경쟁 없음"
    assert "최근접 60m" == kpis_near.display


def test_unknown_profile_kpi_key_yields_none_score():
    profile = {
        "ui": {"summaryKpis": [{"key": "made_up_metric", "label": "가상 지표"}]},
    }
    kpis = compute_industry_kpis(profile, full_inputs())
    assert len(kpis) == 1
    assert kpis[0].score is None
    assert kpis[0].label == "가상 지표"


def test_foreign_share_annotates_timeslot_kpis():
    inputs = full_inputs()
    inputs.late_night_pop = 8_000.0
    inputs.weekend_prime_pop = 25_000.0
    inputs.living_weekend_days = 26
    inputs.foreign_share_pct = 18.4
    conv = profile_kpi_map("convenience", inputs)["late_night_demand"]
    assert "외국인 18%" in conv.detail
    beauty = profile_kpi_map("beauty", inputs)["weekend_demand"]
    assert "외국인 18%" in beauty.detail
    inputs.foreign_share_pct = 2.0  # 5% 미만이면 표기 생략
    conv2 = profile_kpi_map("convenience", inputs)["late_night_demand"]
    assert "외국인" not in conv2.detail


def franchise_inputs(**overrides) -> KpiInputs:
    inputs = full_inputs()
    inputs.brand_label = "메가커피"
    inputs.own_brand_count_in_radius = 1
    inputs.nearest_own_brand_m = 350.0
    inputs.own_brand_seoul_count = 120
    inputs.trdar_store_count = 263
    inputs.trdar_franchise_count = 31
    for key, value in overrides.items():
        setattr(inputs, key, value)
    return inputs


def test_franchise_kpis_require_brand():
    inputs = full_inputs()  # brand_label 없음
    kpis = kpi_map(inputs)
    for key in ("cannibalization_risk", "new_coverage", "network_efficiency"):
        assert kpis[key].score is None
        assert "브랜드명 입력 필요" in kpis[key].detail
    # brand_whitespace는 브랜드 없이도 상권 점포 데이터로 계산
    inputs.trdar_store_count = 263
    inputs.trdar_franchise_count = 31
    ws = kpi_map(inputs)["brand_whitespace"]
    assert ws.score == 88.2
    assert "프랜차이즈 31곳" in ws.detail


def test_cannibalization_risk_scales_with_proximity():
    close = kpi_map(franchise_inputs(nearest_own_brand_m=80.0, own_brand_count_in_radius=2))["cannibalization_risk"]
    far = kpi_map(franchise_inputs(nearest_own_brand_m=900.0, own_brand_count_in_radius=1))["cannibalization_risk"]
    none_near = kpi_map(franchise_inputs(own_brand_count_in_radius=0, nearest_own_brand_m=None))["cannibalization_risk"]
    assert close.score > far.score
    assert close.display == "위험 높음"
    assert none_near.score == 0.0
    assert none_near.display == "위험 없음"
    assert "서울 전체 120곳" in none_near.detail


def test_new_coverage_inverse_of_cannibalization():
    virgin = kpi_map(franchise_inputs(own_brand_count_in_radius=0, nearest_own_brand_m=None))["new_coverage"]
    assert virgin.score == 100.0
    assert virgin.display == "미개척 상권"
    near = kpi_map(franchise_inputs(nearest_own_brand_m=200.0))["new_coverage"]
    assert near.score == 20.0


def test_network_efficiency_blends_scale_and_spacing():
    kpi = kpi_map(franchise_inputs())["network_efficiency"]
    assert kpi.score is not None
    assert "서울 자사 120곳" in kpi.detail
    first_store = kpi_map(franchise_inputs(own_brand_seoul_count=0))["network_efficiency"]
    assert first_store.score is None
    assert "첫 출점" in first_store.detail
