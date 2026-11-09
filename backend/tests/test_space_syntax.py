from app.services.graph_builder import build_graph
from app.services.space_syntax import WALK, compute_metrics, extend_unique_warnings
from app.services.spatial_query import RoadSegment


def _seg(osm_id: int, coords: list[list[float]]) -> RoadSegment:
    return RoadSegment(
        osm_id=osm_id,
        highway="residential",
        length_m=100.0,
        coordinates=coords,
        walkable=True,
        drivable=True,
    )


class TestSpaceSyntaxMetrics:
    def test_segment_angular_four_metrics(self):
        # Cross: four arms meet at center — angular dual graph with connectivity > 1
        c = [126.90, 37.50]
        segments = [
            _seg(1, [c, [126.905, 37.50]]),
            _seg(2, [c, [126.895, 37.50]]),
            _seg(3, [c, [126.90, 37.505]]),
            _seg(4, [c, [126.90, 37.495]]),
        ]
        G, _ = build_graph(segments)
        metrics, _ = compute_metrics(G, segments, modes=(WALK,))
        walk = metrics[WALK]
        for key in ("choice", "integration", "mean_depth", "connectivity"):
            assert key in walk
            assert len(walk[key]) == len(segments)
        # Center arms should have non-zero connectivity at junction
        assert max(walk["connectivity"].values()) >= 2.0


def test_extend_unique_warnings_dedupes_messages():
    warnings = ["Walkable network has 5 disconnected components"]
    extend_unique_warnings(
        warnings,
        [
            "Walkable network has 5 disconnected components",
            "DepthmapX unavailable: missing binary",
        ],
    )
    assert warnings == [
        "Walkable network has 5 disconnected components",
        "DepthmapX unavailable: missing binary",
    ]
