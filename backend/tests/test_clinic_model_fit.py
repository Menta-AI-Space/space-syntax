"""Tests for clinic model fit."""

from app.services.clinic_model_fit import compute_clinic_model_fit
from app.services.restaurant_model_fit import SiteMetrics


def test_derma_fits_transit_and_female():
    site = SiteMetrics(
        workers=8000,
        female_pct=55,
        parent_pct=35,
        transit_distance_m=300,
        choice_top_pct=30,
    )
    result = compute_clinic_model_fit(site, None)
    derma = next(m for m in result.models if m.model_id == "derma_aesthetic")
    ortho = next(m for m in result.models if m.model_id == "ortho_pain")
    assert derma.score > ortho.score


def test_ortho_needs_parking_and_seniors():
    good = SiteMetrics(senior_pct=28, has_parking=True, area_pyeong=25, has_elevator=True)
    poor = SiteMetrics(senior_pct=10, has_parking=False, area_pyeong=12)
    good_score = next(
        m for m in compute_clinic_model_fit(good, None).models if m.model_id == "ortho_pain"
    ).score
    poor_score = next(
        m for m in compute_clinic_model_fit(poor, None).models if m.model_id == "ortho_pain"
    ).score
    assert good_score > poor_score
