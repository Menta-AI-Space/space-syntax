import pytest

from pydantic import ValidationError

from app.models.schemas import SimulateRequest
from app.services import simulation
from app.services.simulation import (
    SimulationError,
    VirtualRoad,
    build_virtual_segments,
    rule_based_simulation_explanation,
    run_simulation,
    top_improved,
    top_worsened,
    _display_summary,
)
from app.services.spatial_query import RoadSegment


def _seg(osm_id, coords, highway="residential"):
    return RoadSegment(
        osm_id=osm_id,
        highway=highway,
        length_m=0.0,  # graph_builder falls back to geometric length
        coordinates=coords,
        walkable=True,
        drivable=True,
    )


def u_shaped_network():
    """Two parallel roads joined only at the left end — right ends are deep."""
    return [
        _seg(1, [[0.0, 0.0], [0.001, 0.0]]),
        _seg(2, [[0.001, 0.0], [0.002, 0.0]]),
        _seg(3, [[0.002, 0.0], [0.003, 0.0]]),
        _seg(4, [[0.0, 0.001], [0.001, 0.001]]),
        _seg(5, [[0.001, 0.001], [0.002, 0.001]]),
        _seg(6, [[0.002, 0.001], [0.003, 0.001]]),
        _seg(7, [[0.0, 0.0], [0.0, 0.001]]),
    ]


@pytest.fixture(autouse=True)
def force_python_engine(monkeypatch):
    monkeypatch.setattr(
        simulation,
        "compute_depthmapx_metrics",
        lambda G, segs, **kwargs: (None, "not configured"),
    )


class TestBuildVirtualSegments:
    def test_footway_is_walk_only(self):
        segs = build_virtual_segments(
            [VirtualRoad(coordinates=[[0.0, 0.0], [0.001, 0.0]], road_type="footway")]
        )
        assert segs[0].walkable is True
        assert segs[0].drivable is False
        assert segs[0].osm_id < 0
        assert segs[0].length_m == pytest.approx(111.32, rel=0.05)

    def test_residential_is_drivable(self):
        segs = build_virtual_segments(
            [VirtualRoad(coordinates=[[0.0, 0.0], [0.001, 0.0]], road_type="residential")]
        )
        assert segs[0].drivable is True

    def test_unique_negative_ids(self):
        segs = build_virtual_segments(
            [
                VirtualRoad(coordinates=[[0.0, 0.0], [0.001, 0.0]]),
                VirtualRoad(coordinates=[[0.0, 0.001], [0.001, 0.001]]),
            ]
        )
        assert segs[0].osm_id != segs[1].osm_id
        assert all(s.osm_id < 0 for s in segs)

    def test_too_few_coords_rejected(self):
        with pytest.raises(SimulationError):
            build_virtual_segments([VirtualRoad(coordinates=[[0.0, 0.0]])])

    def test_too_short_rejected(self):
        with pytest.raises(SimulationError):
            build_virtual_segments(
                [VirtualRoad(coordinates=[[0.0, 0.0], [0.00000001, 0.0]])]
            )

    def test_too_long_rejected(self):
        with pytest.raises(SimulationError):
            build_virtual_segments([VirtualRoad(coordinates=[[0.0, 0.0], [0.05, 0.0]])])


class TestRunSimulation:
    def test_loop_closing_road_improves_integration(self):
        segments = u_shaped_network()
        roads = [
            VirtualRoad(
                coordinates=[[0.003, 0.0], [0.003, 0.001]], road_type="footway"
            )
        ]
        result = run_simulation(segments, roads)

        assert result.integration.change_pct is not None
        assert result.integration.change_pct > 0
        assert result.improved_count >= 1
        assert len(result.segments) == len(segments)
        assert len(result.virtual_segments) == 1
        assert 0 <= result.integration.display_before <= 100
        assert 0 <= result.integration.display_after <= 100

    def test_no_roads_rejected(self):
        with pytest.raises(SimulationError):
            run_simulation(u_shaped_network(), [])

    def test_cached_before_metrics_skip_baseline_recomputation(self, monkeypatch):
        segments = u_shaped_network()
        baseline = {
            "integration": {i: 1.0 for i in range(len(segments))},
            "choice": {i: 1.0 for i in range(len(segments))},
            "mean_depth": {i: 1.0 for i in range(len(segments))},
            "connectivity": {i: 1.0 for i in range(len(segments))},
        }
        calls = []

        def fake_metrics(value, warnings, engine):
            calls.append((value, engine))
            return baseline, set(range(len(value)))

        monkeypatch.setattr(simulation, "_walk_metrics", fake_metrics)

        run_simulation(
            segments,
            [VirtualRoad(coordinates=[[0.003, 0.0], [0.003, 0.001]])],
            before_metrics=baseline,
        )

        assert len(calls) == 1

    def test_cached_depthmap_baseline_never_falls_back_for_after(self, monkeypatch):
        segments = u_shaped_network()
        baseline = {
            "integration": {i: 1.0 for i in range(len(segments))},
            "choice": {i: 1.0 for i in range(len(segments))},
            "mean_depth": {i: 1.0 for i in range(len(segments))},
            "connectivity": {i: 1.0 for i in range(len(segments))},
        }
        monkeypatch.setattr(
            simulation,
            "compute_depthmapx_metrics",
            lambda *_args, **_kwargs: (None, "engine unavailable"),
        )

        with pytest.raises(SimulationError, match="동일한 DepthmapX 엔진"):
            run_simulation(
                segments,
                [VirtualRoad(coordinates=[[0.003, 0.0], [0.003, 0.001]])],
                before_metrics=baseline,
                before_engine="depthmapx",
            )

    def test_unknown_closed_road_rejected(self):
        with pytest.raises(SimulationError, match="찾을 수 없습니다"):
            run_simulation(u_shaped_network(), closed_osm_ids=[999])

    def test_original_indices_stable(self):
        segments = u_shaped_network()
        result = run_simulation(
            segments,
            [VirtualRoad(coordinates=[[0.003, 0.0], [0.003, 0.001]])],
        )
        for d in result.segments:
            assert segments[d.index].osm_id == d.osm_id

    def test_closed_road_is_removed_from_after_network_and_counted_as_harmed(self):
        segments = u_shaped_network()

        result = run_simulation(
            segments,
            [VirtualRoad(coordinates=[[0.003, 0.0], [0.003, 0.001]])],
            closed_osm_ids=[3],
        )

        closed = next(d for d in result.segments if d.osm_id == 3)
        assert closed.integration_before > 0
        assert closed.integration_after == 0
        assert closed.choice_after == 0
        assert result.harmed_count >= 1

    def test_segment_vanished_from_after_graph_is_not_counted_as_harmed(self, monkeypatch):
        """A segment that graph noding merged away in the after run must not
        report a spurious -100% delta (its 0.0 is "not measured", not real)."""
        segments = u_shaped_network()
        n = len(segments)
        values = {i: 1.0 for i in range(n + 1)}  # +1 covers the virtual segment
        baseline = {m: dict(values) for m in ("integration", "choice", "mean_depth", "connectivity")}
        vanished_idx = 2

        def fake_metrics(segs, warnings, engine=None):
            covered = set(range(len(segs)))
            metrics = {m: {i: 1.0 for i in covered} for m in baseline}
            if len(segs) > n:  # after run: existing + virtual
                covered.discard(vanished_idx)
                for m in metrics:
                    metrics[m][vanished_idx] = 0.0
            return metrics, covered

        monkeypatch.setattr(simulation, "_walk_metrics", fake_metrics)

        result = run_simulation(
            segments,
            [VirtualRoad(coordinates=[[0.003, 0.0], [0.003, 0.001]])],
        )

        vanished = next(d for d in result.segments if d.index == vanished_idx)
        assert vanished.no_data is True
        assert vanished.integration_delta_pct is None
        assert vanished.integration_after == vanished.integration_before
        assert result.harmed_count == 0

    def test_local_summary_and_new_road_metrics_present(self):
        segments = u_shaped_network()
        result = run_simulation(
            segments,
            [VirtualRoad(coordinates=[[0.003, 0.0], [0.003, 0.001]])],
        )

        assert result.local_integration is not None
        assert result.local_segment_count > 0
        # Loop-closing road: nearby roads gain integration.
        assert result.local_integration.change_pct is not None
        assert result.local_integration.change_pct > 0
        assert len(result.new_roads) == 1
        new_road = result.new_roads[0]
        assert new_road.integration > 0
        assert new_road.integration_top_pct is not None
        assert 0 < new_road.integration_top_pct <= 100


class TestDisplaySummary:
    def test_shared_scale(self):
        summary = _display_summary([1.0, 1.0], [2.0, 2.0])
        assert summary.display_after > summary.display_before
        assert summary.change_pct == pytest.approx(100.0)

    def test_zero_baseline_has_no_pct(self):
        summary = _display_summary([0.0, 0.0], [1.0, 1.0])
        assert summary.change_pct is None

    def test_empty_values(self):
        summary = _display_summary([], [])
        assert summary.display_before == 0.0
        assert summary.change_pct is None


class TestTopImproved:
    def _result(self):
        segments = u_shaped_network()
        return run_simulation(
            segments,
            [VirtualRoad(coordinates=[[0.003, 0.0], [0.003, 0.001]])],
        )

    def test_uses_name_map(self):
        result = self._result()
        baseline = top_improved(result)
        assert baseline
        names = {baseline[0]["osm_id"]: "명물길"}
        top = top_improved(result, names=names)
        assert top[0]["label"] == "명물길"

    def test_fallback_label_without_name(self):
        top = top_improved(self._result())
        assert top
        assert all(t["label"] for t in top)

    def test_explanation_mentions_change(self):
        result = self._result()
        top = top_improved(result)
        text = rule_based_simulation_explanation(result, top)
        assert "%" in text
        assert "유동인구" in text  # honesty caveat present

    def test_explanation_states_before_after_values(self):
        result = self._result()
        text = rule_based_simulation_explanation(result, top_improved(result))
        assert "→" in text  # explicit before → after numbers
        assert "반경 전체 평균 접근성" in text

    def test_explanation_describes_harm_for_closure(self):
        result = run_simulation(u_shaped_network(), closed_osm_ids=[3])

        text = rule_based_simulation_explanation(result, top_improved(result))

        assert "피해" in text
        assert "도로 폐쇄" in text
        assert "큰 변화를 주지 않습니다" not in text


class TestTopWorsened:
    def test_reports_harmed_roads(self):
        result = run_simulation(
            u_shaped_network(),
            [VirtualRoad(coordinates=[[0.003, 0.0], [0.003, 0.001]])],
            closed_osm_ids=[3],
        )

        top = top_worsened(result, names={3: "폐쇄 후보"})

        assert top
        assert top[0]["label"] == "폐쇄 후보"
        assert top[0]["integration_delta_pct"] < 0


class TestSimulateRequest:
    def test_allows_road_closure_without_virtual_road(self):
        request = SimulateRequest(lat=37.56, lng=126.94, closed_osm_ids=[3])

        assert request.roads == []
        assert request.closed_osm_ids == [3]

    def test_requires_at_least_one_action(self):
        with pytest.raises(ValidationError):
            SimulateRequest(lat=37.56, lng=126.94)
