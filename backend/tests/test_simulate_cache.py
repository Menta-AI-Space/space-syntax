import asyncio

import pytest

from app.models.schemas import SimulateRequest, VirtualRoadIn
from app.routers import simulate as simulate_router


class ProbeComplete(Exception):
    pass


class FakeCache:
    def __init__(self, payload):
        self.payload = payload

    def get(self, _key):
        return self.payload


def baseline_payload():
    return {
        "_cache": {
            "mode": "walk",
            "engine": "depthmapx",
            "analysis_version": "a1",
            "segment_limit": 1000,
            "baseline_metrics": {
                "integration": [2.0],
                "choice": [3.0],
                "mean_depth": [1.0],
                "connectivity": [1.0],
            },
        },
        "segments": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "osm_id": 10,
                        "highway": "residential",
                        "length_m": 100.0,
                        "walkable": True,
                        "drivable": True,
                        "layer": 0,
                        "bridge": False,
                        "tunnel": False,
                        "integration_walk": 2.0,
                        "choice_walk": 3.0,
                        "mean_depth_walk": 1.0,
                        "connectivity_walk": 1.0,
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[126.9, 37.5], [126.901, 37.5]],
                    },
                }
            ],
        }
    }


def test_simulation_reuses_cached_segments_and_before_metrics(monkeypatch):
    monkeypatch.setattr(simulate_router, "analysis_cache", FakeCache(baseline_payload()))
    monkeypatch.setattr(simulate_router.settings, "analysis_cache_enabled", True)
    monkeypatch.setattr(simulate_router.settings, "analysis_cache_network_version", "v1")
    monkeypatch.setattr(simulate_router.settings, "analysis_cache_version", "a1")
    monkeypatch.setattr(simulate_router.settings, "depthmapx_enabled", True)
    monkeypatch.setattr(
        simulate_router,
        "fetch_roads_in_radius",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cached simulation queried roads")),
    )

    def probe(segments, roads, closed_osm_ids, before_metrics, before_engine):
        assert segments[0].osm_id == 10
        assert before_metrics["integration"][0] == 2.0
        assert before_engine == "depthmapx"
        raise ProbeComplete

    monkeypatch.setattr(simulate_router, "run_simulation", probe)

    with pytest.raises(ProbeComplete):
        asyncio.run(
            simulate_router.simulate(
                SimulateRequest(
                    lat=37.5,
                    lng=126.9,
                    radius_m=500,
                    roads=[
                        VirtualRoadIn(
                            coordinates=[[126.9, 37.5], [126.901, 37.501]],
                            road_type="footway",
                        )
                    ],
                )
            )
        )
