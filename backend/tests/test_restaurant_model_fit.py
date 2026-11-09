"""Tests for restaurant model fit scoring."""

from app.services.real_competition import compute_real_competition
from app.services.restaurant_model_fit import SiteMetrics, compute_restaurant_model_fit
from app.services.sbiz_stores import StoreRecord


def test_lunch_office_scores_higher_with_workers():
    site = SiteMetrics(workers=12000, choice_top_pct=25, transit_distance_m=250)
    result = compute_restaurant_model_fit(site, None)
    lunch = next(m for m in result.models if m.model_id == "lunch_office")
    bbq = next(m for m in result.models if m.model_id == "bbq_gathering")
    assert lunch.score > bbq.score


def test_bbq_penalized_without_exhaust():
    site = SiteMetrics(workers=8000, area_pyeong=12, has_exhaust=False)
    result = compute_restaurant_model_fit(site, None)
    bbq = next(m for m in result.models if m.model_id == "bbq_gathering")
    assert bbq.verdict in ("낮음", "보통")
