import asyncio
from types import SimpleNamespace

import pytest

from app.models.schemas import SiteScoreRequest
from app.routers import site_score as site_score_router
from app.services.space_syntax import WALK


class ProbeComplete(Exception):
    pass


def test_site_score_requests_only_walk_depthmap_metrics(monkeypatch):
    """Site score consumes walk metrics only; drive analysis must not be launched."""

    async def fake_tree():
        return {}

    # Probe the live compute path, not the persistent cache: with grid-snapped
    # keys this coordinate can hit a warmed 상권 entry in the real DB otherwise.
    monkeypatch.setattr(site_score_router.settings, "analysis_cache_enabled", False)
    monkeypatch.setattr(site_score_router, "get_upjong_tree", fake_tree)
    monkeypatch.setattr(
        site_score_router,
        "resolve_upjong_selection",
        lambda **kwargs: SimpleNamespace(
            archetype="general",
            label="일반",
            inds_lcls_cd=None,
            inds_mcls_cd=None,
            inds_scls_cd=None,
            breadcrumb="일반",
            match_method="disabled",
        ),
    )
    segments = [SimpleNamespace()]
    monkeypatch.setattr(site_score_router, "fetch_roads_in_radius", lambda *args: segments)
    monkeypatch.setattr(site_score_router, "build_graph", lambda value: (object(), {}))
    monkeypatch.setattr(site_score_router, "analysis_point_on_primary_network", lambda *args, **kwargs: True)

    def fake_depthmap(graph, value, modes=("walk", "drive")):
        assert modes == (WALK,)
        raise ProbeComplete

    monkeypatch.setattr(site_score_router, "compute_depthmapx_metrics", fake_depthmap)

    with pytest.raises(ProbeComplete):
        asyncio.run(
            site_score_router.site_score(
                SiteScoreRequest(lat=37.555134, lng=126.936893, radius_m=500)
            )
        )
