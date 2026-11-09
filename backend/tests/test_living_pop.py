from app.services.industry_kpis import KpiInputs, compute_industry_kpis
from app.services.industry_profiles import load_all_profiles
from app.services.living_pop import (
    LATE_NIGHT_HOURS,
    LivingPopProfile,
    late_night_average,
    slot_average,
    weekend_prime_average,
)


def hourly(value: float) -> dict[int, float]:
    return {h: value for h in range(24)}


def test_slot_average_ignores_missing_hours():
    partial = {22: 100.0, 23: 200.0}  # 00–05 absent
    assert slot_average(partial, LATE_NIGHT_HOURS) == 150.0
    assert slot_average({}, LATE_NIGHT_HOURS) is None


def test_late_night_average_combines_day_types():
    profile = LivingPopProfile(
        weekday_hourly=hourly(1000.0),
        weekend_hourly=hourly(3000.0),
        weekday_sample_days=5,
        weekend_sample_days=2,
    )
    assert late_night_average(profile) == 2000.0


def test_weekend_prime_none_without_weekend_days():
    profile = LivingPopProfile(weekday_hourly=hourly(1000.0), weekday_sample_days=5)
    assert weekend_prime_average(profile) is None
    assert late_night_average(profile) == 1000.0  # weekday-only still computes


def kpi(profile_id: str, inputs: KpiInputs) -> dict:
    profile = load_all_profiles()[profile_id]
    return {k.key: k for k in compute_industry_kpis(profile, inputs)}


def test_late_night_demand_kpi_computes_from_living_pop():
    inputs = KpiInputs(late_night_pop=16_000.0, living_weekday_days=1)
    k = kpi("convenience", inputs)["late_night_demand"]
    assert k.score is not None
    assert 0 < k.score < 100
    assert "16,000명" in k.detail
    assert "표본 1일" in k.detail


def test_weekend_demand_kpi_honest_none_without_weekend_sample():
    inputs = KpiInputs(late_night_pop=16_000.0, weekend_prime_pop=None)
    k = kpi("beauty", inputs)["weekend_demand"]
    assert k.score is None
    assert "주말" in k.detail


def test_weekend_demand_kpi_computes_when_weekend_data_present():
    inputs = KpiInputs(weekend_prime_pop=34_000.0, living_weekend_days=8)
    k = kpi("beauty", inputs)["weekend_demand"]
    assert k.score is not None
    assert "34,000명" in k.detail
    assert "표본 8일" in k.detail
