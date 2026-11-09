"""한국 밖 계획도면(두바이·미국 샘플, 2026-11-09) — 지정한 좌표계를 받는지, 미터 좌표계를
자리에 맞게 고르는지, 라우터가 한국 DB를 묻지 않고 도면 안 길만으로 계산하는지."""

import io

import ezdxf
from fastapi.testclient import TestClient

from app.main import app
from app.services import planned_site
from app.services.planned_site_dxf import extract_plan

client = TestClient(app)


def _grid_dxf(x0: float, y0: float) -> bytes:
    """UTM 미터 좌표에 그린 2×2 블록 격자(도로 중심선 + 안쪽으로 들인 획지)."""
    doc = ezdxf.new("R2010")
    for layer in ("00_계획선-도로", "00_계획선-획지선", "00_계획선-획지명"):
        doc.layers.add(name=layer)
    msp = doc.modelspace()
    step = 120.0
    for i in range(3):
        msp.add_lwpolyline([(x0, y0 + i * step), (x0 + 2 * step, y0 + i * step)], dxfattribs={"layer": "00_계획선-도로"})
        msp.add_lwpolyline([(x0 + i * step, y0), (x0 + i * step, y0 + 2 * step)], dxfattribs={"layer": "00_계획선-도로"})
    n = 0
    for i in range(2):
        for j in range(2):
            n += 1
            ax, ay = x0 + i * step + 3, y0 + j * step + 3
            bx, by = x0 + (i + 1) * step - 3, y0 + (j + 1) * step - 3
            msp.add_lwpolyline([(ax, ay), (bx, ay), (bx, by), (ax, by)], close=True, dxfattribs={"layer": "00_계획선-획지선"})
            msp.add_text(f"C{n}", dxfattribs={"layer": "00_계획선-획지명", "height": 5, "insert": ((ax + bx) / 2, (ay + by) / 2)})
    buf = io.StringIO()
    doc.write(buf, fmt="asc")
    return buf.getvalue().encode("utf-8")


# 두바이 다운타운 부근 UTM 40N 좌표
DUBAI = (326_000.0, 2_787_000.0)


def test_metric_epsg_is_korea_or_utm():
    assert planned_site.metric_epsg_for(126.9, 37.5) == "EPSG:5179"
    assert planned_site.metric_epsg_for(55.27, 25.19) == "EPSG:32640"   # 두바이 UTM 40N
    assert planned_site.metric_epsg_for(-81.54, 28.32) == "EPSG:32617"  # 플로리다 UTM 17N
    assert planned_site.metric_epsg_for(151.2, -33.9) == "EPSG:32756"   # 남반구는 327xx


def test_extract_accepts_an_explicit_foreign_crs():
    res = extract_plan(_grid_dxf(*DUBAI), "EPSG:32640")
    assert res.crs == "EPSG:32640"
    assert res.centre_lat is not None and 25.0 < res.centre_lat < 25.3
    assert res.centre_lng is not None and 55.0 < res.centre_lng < 55.5
    assert len(res.roads) >= 6 and len(res.lots) == 4
    assert any("한국 밖" in note for note in res.notes)
    # 이름표가 획지에 붙는다
    assert {l["lot_id"] for l in res.lots} == {"C1", "C2", "C3", "C4"}


def test_extract_without_crs_still_asks_when_nothing_fits_korea():
    res = extract_plan(_grid_dxf(*DUBAI), None)
    assert res.crs == "" and not res.roads
    assert any("좌표계를 직접 지정" in note for note in res.notes)


def test_analysis_abroad_measures_in_metres(monkeypatch):
    """두바이 도면을 EPSG:5179로 재면 길이가 틀어진다 — UTM으로 재야 120m 변이 120m로 나온다."""
    res = extract_plan(_grid_dxf(*DUBAI), "EPSG:32640")
    roads = [planned_site.VirtualRoad(coordinates=r["coordinates"], road_type=r["road_type"]) for r in res.roads]
    lots = [planned_site.PlannedLot(lot_id=l["lot_id"], geometry=l["geometry"]) for l in res.lots]
    graded, stats, warnings, detail = planned_site.analyze_planned_site_detail(roads, lots, None)
    assert len(graded) == 4
    for g in graded:
        assert 10_000 < g.area_sqm < 14_000, g.area_sqm  # (120-6)² = 12,996㎡
        sides = detail[g.pnu].sides
        assert sides, "변이 하나도 길에 안 닿았다"
    # 분석이 끝나면 미터 좌표계는 한국 기본값으로 돌아온다
    assert planned_site._metric_pair.get() is planned_site._DEFAULT_PAIR


def test_router_skips_korean_db_for_plans_abroad(monkeypatch):
    """한국 밖 중심이면 기존 도로·아파트·역을 묻지 않는다 — DB에 그 나라 데이터가 없다."""
    import app.services.spatial_query as sq

    def boom(*_a, **_k):
        raise AssertionError("한국 밖 도면인데 DB를 물었다")

    monkeypatch.setattr(sq, "fetch_roads_in_radius", boom)
    monkeypatch.setattr(sq, "fetch_apartment_points", boom)
    res = extract_plan(_grid_dxf(*DUBAI), "EPSG:32640")
    body = {
        "roads": res.roads, "lots": res.lots,
        "include_existing_roads": True,
        "centre_lat": res.centre_lat, "centre_lng": res.centre_lng,
    }
    r = client.post("/api/planned-site", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["lots"]) == 4
    assert any("한국 밖 도면" in w for w in data["warnings"])
