"""Drive/moto reach uses a Euclidean disc approximation — no roads, no graph."""

import math

import pytest

from app.config import settings
from app.services.cannibalization import compute_cannibalization
from app.services.isochrone import (
    APPROX_REACH_WARNING_KO,
    approx_reach_radius_m,
    compute_isochrones,
    reach_cutoff_m,
    uses_reach_approximation,
)
from app.services.sbiz_stores import StoreRecord


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _store(name: str, lat: float, lng: float, scls_cd: str = "I21201") -> StoreRecord:
    return StoreRecord(
        bizes_id=name,
        bizes_nm=name,
        inds_lcls_cd="I2",
        inds_lcls_nm="음식",
        inds_mcls_cd="I212",
        inds_mcls_nm="비알코올",
        inds_scls_cd=scls_cd,
        inds_scls_nm="카페",
        lon=lng,
        lat=lat,
        bld_mng_no="",
        flr_no="1",
        rdnm_adr="테스트",
    )


def test_vehicular_modes_are_approximated():
    assert uses_reach_approximation("drive")
    assert uses_reach_approximation("moto")
    assert not uses_reach_approximation("walk")
    assert not uses_reach_approximation("bike")


def test_drive_isochrone_without_graph_is_disc():
    lat, lng = 37.5559, 126.9364
    result = compute_isochrones(
        G=None,
        segments=None,
        lat=lat,
        lng=lng,
        minutes_list=(3, 5, 10, 15),
        mode="drive",
    )
    assert result.start_connected
    assert APPROX_REACH_WARNING_KO in result.warnings
    assert len(result.bands) == 4
    for band in result.bands:
        cutoff = reach_cutoff_m("drive", band.minutes)
        radius = approx_reach_radius_m("drive", band.minutes)
        assert radius == pytest.approx(cutoff * settings.network_detour_factor)
        # schema semantics unchanged: max_distance_m is the network distance
        assert band.max_distance_m == pytest.approx(cutoff, abs=0.1)
        # valid GeoJSON polygon: closed ring, every vertex ~radius from center
        ring = band.polygon["coordinates"][0]
        assert ring[0] == ring[-1]
        assert len(ring) >= 33
        for vlng, vlat in ring[:-1]:
            assert _haversine_m(lat, lng, vlat, vlng) == pytest.approx(
                radius, rel=0.01
            )
        assert band.area_m2 == pytest.approx(math.pi * radius**2, rel=0.01)


def test_drive_band_counts_stores_within_effective_radius():
    lat, lng = 37.5559, 126.9364
    factor = settings.network_detour_factor
    cutoff = reach_cutoff_m("drive", 5)  # 2100m
    inside = cutoff * factor * 0.9
    outside = cutoff * factor * 1.1  # inside raw cutoff, outside the disc
    lat_scale = 111_320.0
    stores = [
        _store("안", lat + inside / lat_scale, lng),
        _store("밖", lat + outside / lat_scale, lng),
        _store("다른업종", lat + inside / lat_scale / 2, lng, scls_cd="G20405"),
    ]
    result = compute_isochrones(
        G=None,
        segments=None,
        lat=lat,
        lng=lng,
        minutes_list=(5,),
        mode="drive",
        stores=stores,
        inds_mcls_cd="I212",
        inds_scls_cd="I21201",
    )
    band = result.bands[0]
    assert band.store_count == 2
    assert band.same_category_count == 1


def test_walk_still_requires_graph():
    with pytest.raises(ValueError):
        compute_isochrones(
            G=None, segments=None, lat=37.5, lng=126.9, minutes_list=(5,), mode="walk"
        )


def test_cannibalization_drive_without_graph():
    lat, lng = 37.5559, 126.9364
    near = _store("메가커피", lat + 0.001, lng)
    result = compute_cannibalization(
        G=None,
        segments=None,
        lat=lat,
        lng=lng,
        inds_mcls_cd="I212",
        inds_scls_cd="I21201",
        stores=[near],
        radius_m=500,
        mode="drive",
        minutes=15,
    )
    assert result.mode == "drive"
    assert result.minutes == 15
    assert result.total_count == 1
    assert result.reach_polygon and result.reach_polygon.get("coordinates")
