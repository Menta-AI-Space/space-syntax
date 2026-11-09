"""계획 도면 DXF → 획지 등급 (2026-10-22).

시연 샘플 `planned-site-sample.dxf`는 파주 운정3지구 운정중앙역 일대 LH 결정도에서
뽑았다(`tools/make_unjeong_sample_dxf.py`, 2026-10-24). LH 도면처럼 **도로를 양쪽
가장자리 선으로** 그렸다. 그 선을 그대로 길로 쓰면 블록마다
고리가 생겨 망이 섬으로 쪼개지고 (예전 가상 지구에서) 획지 129개 중 98개가 「길에 안 닿음」으로
빠졌다. 가운데 선을 뽑아야 끝까지 돈다 — 그걸 지킨다.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.planned_site import PlannedLot, analyze_planned_site, analyze_planned_site_detail
from app.services.planned_site_dxf import extract_plan
from app.services.simulation import VirtualRoad

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "frontend-next" / "public" / "samples" / "planned-site-sample.dxf"
)

client = TestClient(app)


def test_curb_drawn_roads_become_one_network_and_every_lot_is_graded():
    plan = extract_plan(SAMPLE.read_bytes())
    assert any("가운데 선" in note for note in plan.notes)
    assert len(plan.lots) >= 20
    roads = [VirtualRoad(coordinates=r["coordinates"], road_type=r["road_type"]) for r in plan.roads]
    lots = [PlannedLot(lot_id=l["lot_id"], geometry=l["geometry"]) for l in plan.lots]
    graded, stats, _ = analyze_planned_site(roads, lots)
    assert stats["landlocked_count"] == 0
    assert stats["graded_count"] == len(lots)
    assert len({g.grade for g in graded}) == 5


def test_lot_names_on_the_drawing_become_lot_ids():
    """손님은 「획지 37」이 아니라 공고문의 「F10」을 찾는다."""
    plan = extract_plan(SAMPLE.read_bytes(), crs="EPSG:5174")
    ids = {lot["lot_id"] for lot in plan.lots}
    assert {"F10", "주복1", "F1-1", "A22"} <= ids
    assert len(ids) == len(plan.lots)


def test_frame_and_legend_layers_are_not_read_as_plan():
    plan = extract_plan(SAMPLE.read_bytes())
    kinds = {layer.name: layer.guess for layer in plan.layers}
    assert kinds["00_계획선-도로"] == "road"
    assert kinds["00_계획선-획지선"] == "lot"
    assert kinds["00_도면틀"] == "other"


def test_both_5174_and_5186_are_offered_because_both_land_in_korea():
    plan = extract_plan(SAMPLE.read_bytes())
    epsgs = [c.epsg for c in plan.crs_candidates]
    assert "EPSG:5174" in epsgs and "EPSG:5186" in epsgs
    moved = extract_plan(SAMPLE.read_bytes(), crs="EPSG:5186")
    assert moved.crs == "EPSG:5186"
    assert abs(moved.centre_lat - plan.centre_lat) > 0.5  # 100km 단위로 어긋난다


def test_extract_endpoint_returns_what_planned_site_accepts():
    res = client.post(
        "/api/planned-site/extract",
        files={"file": ("plan.dxf", SAMPLE.read_bytes(), "application/dxf")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["crs"] == "EPSG:5174"
    assert len(body["crs_candidates"]) >= 2
    graded = client.post(
        "/api/planned-site", json={"roads": body["roads"], "lots": body["lots"]}
    )
    assert graded.status_code == 200, graded.text
    assert graded.json()["summary"]["graded_count"] == len(body["lots"])


def test_extract_endpoint_refuses_dwg_with_a_way_out():
    res = client.post(
        "/api/planned-site/extract",
        files={"file": ("plan.dwg", b"AC1032", "application/octet-stream")},
    )
    assert res.status_code == 400
    assert "DXF" in res.json()["detail"]


def test_f10_sides_and_neighbours_explain_the_comparison():
    """화면은 등급 대신 「어느 변이 왜 좋은가」와 「옆 땅과 견주면」을 보여 준다.
    (어느 변이 가장 좋은지는 주변 실제 도로를 넣느냐에 달려 여기서는 묻지 않는다 —
    도면 길만이면 남쪽, 주변 도로를 넣으면 동쪽(역·간선 쪽)이다. 2026-10-24 검증.)"""
    plan = extract_plan(SAMPLE.read_bytes(), crs="EPSG:5174")
    roads = [VirtualRoad(coordinates=r["coordinates"], road_type=r["road_type"]) for r in plan.roads]
    lots = [PlannedLot(lot_id=l["lot_id"], geometry=l["geometry"]) for l in plan.lots]
    _, _, _, detail = analyze_planned_site_detail(roads, lots)
    f10 = detail["F10"]
    assert len(f10.sides) >= 2  # 모퉁이 땅
    best = f10.sides[0]
    assert best.score >= 50  # 도면 길만으로도 약한 변은 아니다
    assert best.edge and best.edge["type"] in ("LineString", "MultiLineString")
    assert best.reasons
    assert [s.score for s in f10.sides] == sorted((s.score for s in f10.sides), reverse=True)
    assert f10.neighbors and all(n.lot_id != "F10" for n in f10.neighbors)
    assert {n.relation for n in f10.neighbors} <= {"맞닿음", "길 건너"}
    assert all(d.sides for d in detail.values())  # 길에 안 닿은 획지가 없다


def test_sample_plan_roads_are_one_network():
    """광장 둘레에서 길이 끊기면 북쪽 획지가 9~19점으로 떨어졌다(2026-10-24). 도면 길은 한 덩어리여야 한다."""
    import networkx as nx

    from app.services.graph_builder import build_graph
    from app.services.simulation import build_virtual_segments

    plan = extract_plan(SAMPLE.read_bytes(), crs="EPSG:5174")
    segs = build_virtual_segments(
        [VirtualRoad(coordinates=r["coordinates"], road_type=r["road_type"]) for r in plan.roads]
    )
    G, _ = build_graph(segs)
    assert nx.number_connected_components(G) == 1


def test_schools_and_housing_are_left_out_of_comparison_but_shown_as_nearby():
    """학교·아파트 땅은 상가를 낼 수 없다 — 견줄 이웃에서 빼고, 옆 상가 땅의 「근처」로만 둔다."""
    plan = extract_plan(SAMPLE.read_bytes(), crs="EPSG:5174")
    roads = [VirtualRoad(coordinates=r["coordinates"], road_type=r["road_type"]) for r in plan.roads]
    lots = [PlannedLot(lot_id=l["lot_id"], geometry=l["geometry"]) for l in plan.lots]
    _, _, _, detail = analyze_planned_site_detail(roads, lots)
    assert detail["고2"].kind == "school" and detail["A22"].kind == "housing"
    assert detail["F10"].kind == "candidate" and detail["주복2"].kind == "candidate"
    for d in detail.values():
        assert all(detail[n.lot_id].kind == "candidate" for n in d.neighbors)
    near = {n.lot_id: n.kind for n in detail["주복2"].nearby}
    assert near.get("A46") == "housing" and near.get("초4") == "school"


def test_plan_model_scores_candidate_lots_from_drawing_factors():
    """2026-10-27: 신도시 10곳에서 배운 모델이 도면 블록 요인(넓이·도로 폭·아파트 용지·역)으로 점수를 낸다.
    역 바로 앞 F1·F2 블록이 역에서 먼 「획지 5」보다 높아야 상식에 맞는다."""
    plan = extract_plan(SAMPLE.read_bytes())
    assert all(r["width_m"] for r in plan.roads), "가운데 선으로 읽은 길은 폭이 있어야 한다"
    roads = [VirtualRoad(coordinates=r["coordinates"], road_type=r["road_type"]) for r in plan.roads]
    lots = [PlannedLot(lot_id=l["lot_id"], geometry=l["geometry"]) for l in plan.lots]
    _, _, warnings, detail = analyze_planned_site_detail(
        roads, lots, None,
        road_widths=[r["width_m"] for r in plan.roads],
        stations=[(126.7282857, 37.7161461)],  # 운정중앙역
    )
    scored = {k: v.plan for k, v in detail.items() if v.kind == "candidate"}
    assert all(p is not None for p in scored.values())
    assert min(scored["F2-2"].score, scored["F1-2"].score) > scored["획지 5"].score
    assert not any("가능성" in w for w in warnings)


def test_broken_dxf_message_is_korean_without_parser_text():
    """깨진 DXF에 ezdxf 영어 원문(「Invalid group code …」)이 화면에 나가지 않는다 (2026-11-07 UX 루프)."""
    import pytest

    from app.services.planned_site_dxf import PlanExtractionError

    with pytest.raises(PlanExtractionError) as info:
        extract_plan(b"hello world\nnot a dxf\n")
    msg = str(info.value)
    assert "DXF 파일을 읽을 수 없습니다" in msg
    assert "Invalid" not in msg and "line" not in msg


def test_unrecognised_layers_return_layer_list_instead_of_dead_end():
    """레이어 이름을 못 알아본 도면은 오류 대신 레이어 표를 돌려줘 사람이 고르게 한다 (2026-11-07)."""
    mall = SAMPLE.parent / "showcase-mall.dxf"
    plan = extract_plan(mall.read_bytes())
    assert plan.layers and not plan.roads and not plan.lots
    assert any("다시 읽기" in note for note in plan.notes)
    res = client.post(
        "/api/planned-site/extract",
        files={"file": ("mall.dxf", mall.read_bytes(), "application/dxf")},
    )
    assert res.status_code == 200
    assert res.json()["layers"]


def test_single_line_road_layer_does_not_crash():
    """선 하나뿐인 레이어를 「계획 도로」로 골라 다시 읽어도 500이 아니다 (2026-11-08 UX 루프)."""
    import io

    import ezdxf

    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (200, 0))
    buf = io.StringIO()
    doc.write(buf)
    plan = extract_plan(buf.getvalue().encode(), road_layers=["0"])
    assert len(plan.roads) >= 1
    assert not plan.lots


def test_lot_note_names_only_the_lines_it_used():
    """도로 레이어를 못 고른 채 획지만 만들었는데 「도로선과 획지선을 이어」라 하면 화면의 「길 0개」와
    어긋난다 (2026-11-08 UX 자동 개선)."""
    both = extract_plan(SAMPLE.read_bytes())
    assert any(note.startswith("도로선과 획지선을 이어 획지") for note in both.notes)
    lots_only = extract_plan(SAMPLE.read_bytes(), road_layers=["없는 레이어"], lot_layers=["00_계획선-획지선"])
    assert not lots_only.roads
    lot_notes = [note for note in lots_only.notes if "이어 획지" in note]
    assert lot_notes and all(note.startswith("획지선만 이어 획지") for note in lot_notes)
