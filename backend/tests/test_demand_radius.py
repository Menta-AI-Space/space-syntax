"""Unit tests for radius-scoped demand estimation."""

from app.services.demand_radius import (
    estimate_radius_population,
    estimate_radius_workers,
    radius_coverage_ratio,
    scale_demand_profile_to_radius,
)
from app.services import sgis_grid
from app.services.sgis import AgeBand, DemandProfile, GenderStats
from tests.test_sgis_grid import (
    FakeGridDB,
    _clear_grid_caches,
    _use_fake_db,
    _write_mini_grid_bundle,
    _korea_to_wgs84,
)


def _sample_profile(**overrides) -> DemandProfile:
    base = DemandProfile(
        adm_cd="11110101",
        adm_name="서울 종로구 청운효자동",
        population=20_000,
        households=8_000,
        companies=500,
        workers=10_000,
        avg_age=42.0,
        population_density=20_000.0,
        age_bands=[AgeBand("20대", 4000, 20.0)],
        gender=GenderStats(
            male_pct=48.0, female_pct=52.0, male_count=9600, female_count=10400
        ),
        stats_year="2024",
        data_scope="행정동 (총조사 2024)",
    )
    for key, val in overrides.items():
        setattr(base, key, val)
    return base


class TestEstimateRadiusPopulation:
    def test_density_caps_at_dong_total(self):
        # 20k/km² * π*(0.5km)² ≈ 15,708 < 20,000
        est = estimate_radius_population(
            radius_m=500,
            dong_population=20_000,
            population_density=20_000.0,
        )
        assert est == 15_707

    def test_large_radius_hits_dong_cap(self):
        est = estimate_radius_population(
            radius_m=1000,
            dong_population=20_000,
            population_density=20_000.0,
        )
        assert est == 20_000

    def test_fallback_without_density(self):
        ratio = radius_coverage_ratio(
            radius_m=500,
            dong_population=12_000,
            population_density=None,
        )
        assert 0 < ratio < 1.0
        est = estimate_radius_population(
            radius_m=500,
            dong_population=12_000,
            population_density=None,
        )
        assert est == int(round(12_000 * ratio))


class TestScaleDemandProfile:
    def test_scales_counts_and_updates_scope(self):
        profile = _sample_profile()
        scaled = scale_demand_profile_to_radius(profile, 500)
        assert scaled.population == 15_707
        assert scaled.workers == 7854
        assert scaled.households == 6283
        assert "반경 500m" in scaled.data_scope
        assert "밀도" in scaled.data_scope
        assert scaled.workers <= scaled.population

    def test_age_band_counts_scaled_pcts_preserved(self):
        profile = _sample_profile()
        scaled = scale_demand_profile_to_radius(profile, 500)
        assert len(scaled.age_bands) == 1
        assert scaled.age_bands[0].pct == 20.0
        assert scaled.age_bands[0].count < profile.age_bands[0].count

    def test_full_dong_when_radius_covers_all(self):
        profile = _sample_profile(population=5_000, workers=2_000, households=2_000)
        scaled = scale_demand_profile_to_radius(profile, 1000)
        assert scaled.population == 5_000
        assert scaled.workers == 2_000

    def test_sinchon_commercial_workers_may_exceed_population(self):
        """상권 행정동: 종사자>거주인구 허용 + 안내 문구."""
        profile = _sample_profile(
            adm_name="서울 서대문구 신촌동",
            population=18_000,
            workers=36_000,
            households=9_000,
            population_density=42_000.0,
        )
        scaled = scale_demand_profile_to_radius(profile, 500)
        assert scaled.population == 18_000  # capped at dong total
        assert scaled.workers == 36_000  # capped at dong total
        assert scaled.workers > scaled.population
        assert "종사자>거주인구" in scaled.data_scope

    def test_residential_workers_capped_at_population(self):
        """주거 중심 행정동: 반경 추정에서 종사자가 거주인구를 넘지 않음."""
        profile = _sample_profile(
            population=20_000,
            workers=18_000,
            population_density=20_000.0,
        )
        scaled = scale_demand_profile_to_radius(profile, 500)
        assert scaled.workers <= scaled.population

    def test_estimate_radius_workers_matches_density_proxy(self):
        est = estimate_radius_workers(
            radius_m=500,
            dong_workers=10_000,
            dong_population=20_000,
            population_density=20_000.0,
        )
        assert est == 7854


class TestGridDemandProfile:
    def setup_method(self) -> None:
        _clear_grid_caches()

    def teardown_method(self) -> None:
        _clear_grid_caches()

    def test_grid_overrides_density_proxy(self, monkeypatch):
        db = FakeGridDB()
        _write_mini_grid_bundle(db)
        _use_fake_db(monkeypatch, db)

        lng, lat = _korea_to_wgs84.transform(901_500, 1_901_500)
        profile = _sample_profile()
        scaled = scale_demand_profile_to_radius(profile, 500, lat=lat, lng=lng)

        assert scaled.population == 100
        assert scaled.workers == 300
        assert scaled.households == 50
        assert "격자 합산" in scaled.data_scope
        assert scaled.age_bands[0].band == "20대"
        assert scaled.age_bands[0].count == 50
