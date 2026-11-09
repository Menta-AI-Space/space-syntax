"""Tests for bakery model fit."""

from app.services.bakery_model_fit import compute_bakery_model_fit
from app.services.restaurant_model_fit import SiteMetrics


def test_morning_sandwich_fits_commute_corridor():
    site = SiteMetrics(workers=7000, transit_distance_m=300, choice_top_pct=28)
    result = compute_bakery_model_fit(site, None)
    morning = next(m for m in result.models if m.model_id == "morning_sandwich")
    premium = next(m for m in result.models if m.model_id == "premium")
    assert morning.score > premium.score


def test_neighborhood_bakery_fits_residential():
    site = SiteMetrics(population=15000, households=8000, apartment_pct=50)
    result = compute_bakery_model_fit(site, None)
    neighborhood = next(m for m in result.models if m.model_id == "neighborhood")
    assert neighborhood.verdict in ("좋음", "보통")
