"""Dashboard bundle should not inflate core road fetch for delivery territory."""

from app.models.schemas import DashboardPointRequest
from app.routers.dashboard import _dashboard_core_fetch_radius, _isochrone_fetch_radius
from app.services.isochrone import competition_fetch_radius_m
from app.services.territory_cannibalization import competition_fetch_radius_for_territory


def test_core_fetch_ignores_delivery_territory_radius():
    request = DashboardPointRequest(
        lat=37.5,
        lng=127.0,
        radius_m=500,
        mode="walk",
        minutes=[5, 10, 15],
        competitor_minutes=5,
    )
    core = _dashboard_core_fetch_radius(request)
    territory = competition_fetch_radius_for_territory(500)

    assert core == competition_fetch_radius_m(500, "walk", 15)
    assert core == 1350
    # moto territory reach is disc-approximated: 250×15×0.75×1.05
    assert territory == 2953
    assert territory > core


def test_core_fetch_grows_with_competitor_band():
    request = DashboardPointRequest(
        lat=37.5,
        lng=127.0,
        radius_m=500,
        mode="moto",
        minutes=[5, 10, 15],
        competitor_minutes=25,
    )
    core = _dashboard_core_fetch_radius(request)
    assert core == competition_fetch_radius_m(500, "moto", 25)
    # disc-approximated moto reach: 250×25×0.75×1.05, not the 1.2 hull margin
    assert core == 4921


def test_isochrone_fetch_expands_for_long_bands():
    request = DashboardPointRequest(
        lat=37.5,
        lng=127.0,
        radius_m=500,
        mode="walk",
        minutes=[5, 10, 15],
    )
    fetch = _isochrone_fetch_radius(request)
    assert fetch == competition_fetch_radius_m(500, "walk", 15)
    assert fetch == 1350
    assert fetch > request.radius_m
