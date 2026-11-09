from app.services.graph_builder import build_graph
from app.services.isochrone import compute_isochrones, network_attr_for_mode, network_attr_for_mode
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


class TestIsochrone:
    def test_produces_bands(self):
        c = [126.90, 37.50]
        segments = [
            _seg(1, [c, [126.905, 37.50]]),
            _seg(2, [c, [126.895, 37.50]]),
            _seg(3, [c, [126.90, 37.505]]),
        ]
        G, _ = build_graph(segments)
        result = compute_isochrones(
            G=G,
            segments=segments,
            lat=37.50,
            lng=126.90,
            minutes_list=(5, 10),
            mode="walk",
        )
        assert result.start_connected
        assert len(result.bands) == 2
        assert result.bands[0].minutes == 5
        assert result.bands[0].polygon.get("coordinates")

    def test_drive_mode_uses_drivable_graph(self):
        c = [126.90, 37.50]
        segments = [
            _seg(1, [c, [126.905, 37.50]]),
            _seg(2, [c, [126.895, 37.50]]),
        ]
        G, _ = build_graph(segments)
        walk = compute_isochrones(
            G=G, segments=segments, lat=37.50, lng=126.90,
            minutes_list=(5,), mode="walk",
        )
        drive = compute_isochrones(
            G=G, segments=segments, lat=37.50, lng=126.90,
            minutes_list=(5,), mode="drive",
        )
        assert walk.mode == "walk"
        assert drive.mode == "drive"
        assert drive.bands[0].max_distance_m > walk.bands[0].max_distance_m

    def test_bike_uses_walkable_network(self):
        assert network_attr_for_mode("bike") == "walkable"
        assert network_attr_for_mode("moto") == "drivable"

    def test_moto_slower_than_drive_same_graph(self):
        c = [126.90, 37.50]
        segments = [
            _seg(1, [c, [126.905, 37.50]]),
            _seg(2, [c, [126.895, 37.50]]),
        ]
        G, _ = build_graph(segments)
        moto = compute_isochrones(
            G=G, segments=segments, lat=37.50, lng=126.90,
            minutes_list=(5,), mode="moto",
        )
        drive = compute_isochrones(
            G=G, segments=segments, lat=37.50, lng=126.90,
            minutes_list=(5,), mode="drive",
        )
        assert moto.bands[0].max_distance_m < drive.bands[0].max_distance_m

    def test_no_warning_when_point_on_main_network(self):
        main = [126.90, 37.50]
        island = [126.92, 37.52]
        segments = [
            _seg(1, [main, [126.905, 37.50]]),
            _seg(2, [main, [126.895, 37.50]]),
            _seg(3, [island, [126.921, 37.52]]),
        ]
        G, _ = build_graph(segments)
        result = compute_isochrones(
            G=G,
            segments=segments,
            lat=37.50,
            lng=126.90,
            minutes_list=(5,),
            mode="walk",
        )
        assert result.start_connected
        assert result.warnings == []

    def test_warns_when_point_on_isolated_fragment(self):
        main = [126.90, 37.50]
        island = [126.92, 37.52]
        segments = [
            _seg(1, [main, [126.905, 37.50]]),
            _seg(2, [main, [126.895, 37.50]]),
            _seg(3, [island, [126.921, 37.52]]),
        ]
        G, _ = build_graph(segments)
        result = compute_isochrones(
            G=G,
            segments=segments,
            lat=37.52,
            lng=126.92,
            minutes_list=(5,),
            mode="walk",
        )
        assert result.start_connected
        assert any("고립 구간" in warning for warning in result.warnings)
