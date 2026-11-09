"""계획 단계 획지 등급 (B안) — 아직 없는 길 위에서 순위를 매긴다.

A안(`test_parcel_structure.py`)과 **같은 등급·배분 함수**를 쓰므로 여기서는
"계획 도로망이 실제로 구조 차이를 만들어 내는가"와 "없는 길/맹지를 조용히
넘기지 않는가"를 본다.
"""

import json
from pathlib import Path

import pytest

from app.services.planned_site import PlannedLot, analyze_planned_site
from app.services.simulation import SimulationError, VirtualRoad

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend-next" / "public" / "samples" / "planned-site.json"
)


def _load():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    roads = [VirtualRoad(coordinates=r["coordinates"], road_type=r["road_type"]) for r in data["roads"]]
    lots = [PlannedLot(lot_id=l["lot_id"], geometry=l["geometry"]) for l in data["lots"]]
    return roads, lots


def test_dead_end_block_ranks_below_the_arterial_block():
    """이 픽스처는 지선4가 막다른 길이 되도록 일부러 그렸다. 그 끝 블록이
    간선 접한 블록보다 낮게 나오지 않으면 계산이 뒤집힌 것이다."""
    roads, lots = _load()
    graded, _, _ = analyze_planned_site(roads, lots)
    by_id = {g.pnu: g for g in graded}
    dead_end = by_id["14블록"]
    arterial = by_id["12블록"]
    assert dead_end.percentile < arterial.percentile
    assert dead_end.grade > arterial.grade  # 5등급이 더 나쁘다


def test_every_lot_gets_a_grade_and_shares_sum_to_one():
    roads, lots = _load()
    graded, stats, _ = analyze_planned_site(roads, lots)
    assert stats["graded_count"] == len(lots)
    assert sum(g.allocation_share for g in graded) == pytest.approx(1.0)


def test_areas_are_metres_not_degrees():
    """WGS84 그대로 면적을 재면 0.0000x가 나온다 — 미터 좌표계로 옮겼는지 본다."""
    roads, lots = _load()
    graded, _, _ = analyze_planned_site(roads, lots)
    assert all(1_000 < g.area_sqm < 100_000 for g in graded)


def test_planning_only_run_says_so():
    roads, lots = _load()
    _, _, warnings = analyze_planned_site(roads, lots)
    assert any("계획 도로만으로" in w for w in warnings)


def test_lot_far_from_every_road_is_flagged_not_silently_zeroed():
    roads, lots = _load()
    far = PlannedLot(
        lot_id="외딴블록",
        geometry={
            "type": "Polygon",
            # 지구에서 수 km 떨어뜨린다.
            "coordinates": [[[127.30, 36.60], [127.301, 36.60], [127.301, 36.601], [127.30, 36.601], [127.30, 36.60]]],
        },
    )
    graded, stats, warnings = analyze_planned_site(roads, lots + [far])
    orphan = next(g for g in graded if g.pnu == "외딴블록")
    assert orphan.structure is None and orphan.grade is None
    assert stats["landlocked_count"] == 1
    assert any("닿지 않습니다" in w for w in warnings)


def test_empty_input_is_refused():
    roads, lots = _load()
    with pytest.raises(SimulationError):
        analyze_planned_site([], lots)
    with pytest.raises(SimulationError):
        analyze_planned_site(roads, [])


# ── 주변 실제 도로와 섞을 때 (2026-10-24 검증에서 나온 규칙) ──────────────────────
# 1e-5도 ≈ 위도 1.1m · 경도 0.88m (북위 37.5도)

from app.services import planned_site as ps  # noqa: E402
from app.services.planned_site_dxf import _join_close_ends  # noqa: E402
from app.services.spatial_query import RoadSegment  # noqa: E402
from shapely.geometry import LineString  # noqa: E402


def _seg(coords, osm_id=1, highway="residential"):
    return RoadSegment(osm_id=osm_id, highway=highway, length_m=100.0, coordinates=coords, walkable=True, drivable=True)


def _box(lng0, lat0, lng1, lat1, lot_id="L"):
    ring = [[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]
    return PlannedLot(lot_id=lot_id, geometry={"type": "Polygon", "coordinates": [ring]})


def test_close_centreline_ends_become_one_junction():
    """보로노이가 낸 4m 간격 교차점 두 개는 한 점이어야 그래프에서 안 끊긴다."""
    a = LineString([(0, 0), (100, 0)])
    b = LineString([(104, 0), (104, 80)])
    c = LineString([(300, 0), (400, 0)])
    joined = _join_close_ends([a, b, c])
    assert joined[0].coords[-1] == joined[1].coords[0]
    assert joined[2].coords[0] == (300.0, 0.0)  # 먼 끝은 그대로


def test_existing_roads_under_the_plan_are_dropped():
    """도면 길을 따라가는 기존 길(같은 길이 지도 데이터에 먼저 든 것)과 획지 안의 길은 뺀다."""
    planned = [_seg([[127.0, 37.5], [127.003, 37.5]], osm_id=-1)]
    along = _seg([[127.0, 37.50005], [127.003, 37.50005]], osm_id=10)  # 5m 옆을 나란히
    inside = _seg([[127.0012, 37.5012], [127.0018, 37.5018]], osm_id=11)  # 획지 한가운데
    far = _seg([[127.0, 37.505], [127.003, 37.505]], osm_id=12)  # 550m 북쪽
    lot = _box(127.001, 37.501, 127.002, 37.502)
    kept = ps._drop_existing_inside_plan([along, inside, far], planned, [lot])
    assert [s.osm_id for s in kept] == [12]


def test_cut_plan_road_end_is_linked_to_nearby_existing_road_unless_a_lot_is_in_between():
    planned = ps._segments_from_roads([VirtualRoad(coordinates=[[127.0, 37.5], [127.003, 37.5]])])
    existing = [_seg([[127.0034, 37.4995], [127.0034, 37.5005]], osm_id=20)]  # 끝에서 35m 동쪽
    links = ps._connect_to_existing(planned, existing, [])
    assert len(links) == 1
    assert 30 < links[0].length_m < 40
    blocker = _box(127.0031, 37.4997, 127.0033, 37.5003)  # 그 사이 획지
    assert ps._connect_to_existing(planned, existing, [blocker]) == []


def test_fragmented_existing_data_does_not_raise_the_disconnection_warning():
    """「도로망이 n개로 끊겨」는 주변 실제 도로의 조각 때문에 어디서나 난다 — 도면 길이 다 이어져
    있으면 계획도면 화면에는 띄우지 않는다."""
    roads, lots = _load()
    lng, lat = roads[0].coordinates[0]
    islands = [
        _seg([[lng + 0.05 + i * 0.002, lat + 0.05], [lng + 0.0505 + i * 0.002, lat + 0.05]], osm_id=100 + i)
        for i in range(12)
    ]
    _, _, warnings, _ = ps.analyze_planned_site_detail(roads, lots, islands)
    assert not any("끊겨 있어" in w for w in warnings)
    assert not any("계산에서 빠졌습니다" in w for w in warnings)


def test_failed_existing_roads_warning_is_said_once(monkeypatch):
    """기존 도로를 못 불러오면 「…없이 계산했습니다」와 「…불러오지 못해…」가 두 줄로 떴다 —
    한 줄로, 까닭(불러오지 못해)을 앞에 둔다 (2026-11-06 UX 자동 개선)."""
    from fastapi.testclient import TestClient

    import app.services.spatial_query as sq
    import app.services.transit as transit
    from app.main import app as fastapi_app

    def boom(*_a, **_k):
        raise RuntimeError("db down")

    async def no_stations(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(sq, "fetch_roads_in_radius", boom)
    monkeypatch.setattr(sq, "fetch_apartment_points", lambda *_a, **_k: [])
    monkeypatch.setattr(transit, "find_subways_within", no_stations)
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    body = {
        "roads": [{"coordinates": r["coordinates"], "road_type": r["road_type"]} for r in data["roads"]],
        "lots": [{"lot_id": l["lot_id"], "geometry": l["geometry"]} for l in data["lots"]],
        "include_existing_roads": True,
        "centre_lat": 36.53,
        "centre_lng": 127.234,
    }
    res = TestClient(fastapi_app).post("/api/planned-site", json=body)
    assert res.status_code == 200, res.text
    road_lines = [w for w in res.json()["warnings"] if w.startswith("주변 기존 도로")]
    assert len(road_lines) == 1
    assert road_lines[0].startswith("주변 기존 도로를 불러오지 못해 계획 도로만으로 계산했습니다 — ")


def test_lot_kind_greys_out_land_where_no_shop_can_open():
    """2026-11-06 소유자: 상권 분석이니 상업용지 이외는 회색 — 학교·아파트 말고도 공공 땅을 가른다."""
    from app.services.planned_site import lot_kind

    for name in ("F10", "F1-2", "주복3", "근생2", "점포겸용단독1", "업2", "C3", "상1", "획지 5", "복7"):
        assert lot_kind(name) == "candidate", name
    for name in ("A21", "단독12", "B3"):
        assert lot_kind(name) == "housing", name
    for name in ("초5", "고2", "유7"):
        assert lot_kind(name) == "school", name
    for name in ("문2", "P28", "공원3", "근린공원1", "주차2", "종교1", "녹지4", "주유1", "의료1"):
        assert lot_kind(name) == "public", name


def test_road_scores_are_returned_per_segment():
    """블록 안 길마다 값이 나와야 한다(소유자) — road_scores_out에 구간별 0~100 값이 담긴다."""
    from app.services.planned_site import PlannedLot, VirtualRoad, analyze_planned_site_detail

    lng0, lat0 = 126.7700, 37.7150
    d = 0.0009  # 약 80~100m
    def road(*pts):
        return VirtualRoad(coordinates=[[x, y] for x, y in pts], road_type="residential")

    roads = [
        road((lng0, lat0), (lng0 + 3 * d, lat0)),
        road((lng0, lat0 + d), (lng0 + 3 * d, lat0 + d)),
        road((lng0, lat0), (lng0, lat0 + d)),
        road((lng0 + 1.5 * d, lat0), (lng0 + 1.5 * d, lat0 + d)),
        road((lng0 + 3 * d, lat0), (lng0 + 3 * d, lat0 + d)),
    ]
    e = d * 0.1
    lots = [
        PlannedLot("F1", {"type": "Polygon", "coordinates": [[[lng0 + e, lat0 + e], [lng0 + 1.5 * d - e, lat0 + e], [lng0 + 1.5 * d - e, lat0 + d - e], [lng0 + e, lat0 + d - e], [lng0 + e, lat0 + e]]]}),
        PlannedLot("공원1", {"type": "Polygon", "coordinates": [[[lng0 + 1.5 * d + e, lat0 + e], [lng0 + 3 * d - e, lat0 + e], [lng0 + 3 * d - e, lat0 + d - e], [lng0 + 1.5 * d + e, lat0 + d - e], [lng0 + 1.5 * d + e, lat0 + e]]]}),
    ]
    out: list[dict] = []
    _, _, _, detail = analyze_planned_site_detail(roads, lots, road_scores_out=out)
    assert len(out) >= 5
    assert all(0 <= r["score"] <= 100 and len(r["coordinates"]) >= 2 and r["planned"] for r in out)
    assert len({r["score"] for r in out}) > 1  # 길마다 값이 다르다
    assert detail["공원1"].kind == "public"
