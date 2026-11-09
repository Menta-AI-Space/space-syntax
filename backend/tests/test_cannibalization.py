from app.services.cannibalization import compute_cannibalization
from app.services.graph_builder import build_graph
from app.services.sbiz_stores import StoreRecord
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


def _store(name: str, lat: float, lng: float, mcls_cd: str, scls_cd: str) -> StoreRecord:
    return StoreRecord(
        bizes_id="1",
        bizes_nm=name,
        inds_lcls_cd="I2",
        inds_lcls_nm="음식",
        inds_mcls_cd=mcls_cd,
        inds_mcls_nm="비알코올",
        inds_scls_cd=scls_cd,
        inds_scls_nm="카페",
        lon=lng,
        lat=lat,
        bld_mng_no="",
        flr_no="1",
        rdnm_adr="테스트",
    )


class TestCannibalization:
    def test_finds_competitors(self):
        c = [126.90, 37.50]
        segments = [
            _seg(1, [c, [126.905, 37.50]]),
            _seg(2, [c, [126.895, 37.50]]),
            _seg(3, [c, [126.90, 37.505]]),
        ]
        G, _ = build_graph(segments)
        stores = [
            _store("메가커피", 37.5001, 126.9002, "I212", "I21201"),
            _store("편의점", 37.5002, 126.9003, "G204", "G20405"),
        ]
        result = compute_cannibalization(
            G=G,
            segments=segments,
            lat=37.50,
            lng=126.90,
            inds_mcls_cd="I212",
            inds_scls_cd="I21201",
            stores=stores,
            radius_m=500,
        )
        assert len(result.competitors) == 1
        assert result.competitors[0].name == "메가커피"
        assert result.mode == "walk"
        assert result.minutes == 5
        assert result.reach_polygon is not None
        assert result.reach_polygon.get("coordinates")
        assert 0 <= result.risk_score <= 100

    def test_longer_reach_band_has_polygon(self):
        c = [126.90, 37.50]
        segments = [
            _seg(1, [c, [126.905, 37.50]]),
            _seg(2, [c, [126.895, 37.50]]),
            _seg(3, [c, [126.90, 37.505]]),
        ]
        G, _ = build_graph(segments)
        stores = [
            _store("메가커피", 37.5001, 126.9002, "I212", "I21201"),
        ]
        for minutes in (5, 10, 15):
            result = compute_cannibalization(
                G=G,
                segments=segments,
                lat=37.50,
                lng=126.90,
                inds_mcls_cd="I212",
                inds_scls_cd="I21201",
                stores=stores,
                radius_m=500,
                minutes=minutes,
            )
            assert result.minutes == minutes
            assert result.reach_polygon is not None
            assert result.reach_polygon.get("coordinates")

    def test_no_warnings_on_well_connected_network(self):
        c = [126.90, 37.50]
        segments = [
            _seg(1, [c, [126.905, 37.50]]),
            _seg(2, [c, [126.895, 37.50]]),
            _seg(3, [c, [126.90, 37.505]]),
        ]
        G, _ = build_graph(segments)
        result = compute_cannibalization(
            G=G,
            segments=segments,
            lat=37.50,
            lng=126.90,
            inds_mcls_cd="I212",
            inds_scls_cd="I21201",
            stores=[],
            radius_m=500,
        )
        assert result.warnings == []
        assert "고립" not in result.detail
        assert "근사" not in result.detail

    def test_warns_when_site_is_on_isolated_network_fragment(self):
        main = [126.90, 37.50]
        island = [126.92, 37.52]
        segments = [
            _seg(1, [main, [126.905, 37.50]]),
            _seg(2, [main, [126.895, 37.50]]),
            _seg(3, [island, [126.921, 37.52]]),
        ]
        G, _ = build_graph(segments)
        result = compute_cannibalization(
            G=G,
            segments=segments,
            lat=37.52,
            lng=126.92,
            inds_mcls_cd="I212",
            inds_scls_cd="I21201",
            stores=[],
            radius_m=500,
        )
        assert any("고립 구간" in w for w in result.warnings)
        # The caveat must reach the same text field the dashboard renders
        # (CannibalizationResponse only forwards `detail`), not just an
        # internal field nobody reads.
        assert any("고립 구간" in part for part in result.detail.split(" · "))

    def test_warns_when_drive_mode_uses_reach_approximation(self):
        result = compute_cannibalization(
            G=None,
            segments=None,
            lat=37.50,
            lng=126.90,
            inds_mcls_cd="I212",
            inds_scls_cd="I21201",
            stores=[],
            radius_m=500,
            mode="drive",
        )
        assert any("근사" in w for w in result.warnings)
        assert any("근사" in part for part in result.detail.split(" · "))

    def test_overlap_rival_warning_is_attributed_to_the_rival_not_the_site(self):
        # Both the subject site and the rival competitor are on drive-mode
        # (disc-approximated) reach, so each gets its own approximation
        # caveat. The rival's must name the rival so it isn't misread as
        # being about the subject site's own reach band.
        stores = [_store("경쟁카페", 37.501, 126.901, "I212", "I21201")]
        result = compute_cannibalization(
            G=None,
            segments=None,
            lat=37.50,
            lng=126.90,
            inds_mcls_cd="I212",
            inds_scls_cd="I21201",
            stores=stores,
            radius_m=2000,
            mode="drive",
        )
        assert len(result.competitors) == 1
        site_warning = "차량 도달권은 직선거리 기반 근사입니다."
        rival_warning = "경쟁카페 도달권 계산: 차량 도달권은 직선거리 기반 근사입니다."
        assert site_warning in result.warnings
        assert rival_warning in result.warnings
