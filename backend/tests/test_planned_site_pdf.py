"""계획 도면 벡터 PDF → 획지·도로 (2026-11-09).

픽스처는 `tools/make_planned_site_pdf_fixture.py`가 운정 샘플 DXF를 결정도 PDF처럼
그린 것이다(용도별 채움 + 도곽 TM 좌표 숫자 + 범례). 정답은 원래 DXF다.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pyproj import Geod
from shapely.geometry import shape

from app.main import app
from app.services.planned_site_dxf import PlanExtractionError, extract_plan
from app.services.planned_site_pdf import pdf_to_plan_dxf

pytest.importorskip("pdfplumber")

FIX = Path(__file__).parent / "fixtures" / "planned_site_pdf"
SAMPLE_DXF = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "planned-site-sample.dxf"
GEOD = Geod(ellps="WGS84")


def _lots(result):
    return {lot["lot_id"]: shape(lot["geometry"]) for lot in result.lots}


def test_pdf_lots_land_where_the_dxf_puts_them():
    pdf = pdf_to_plan_dxf((FIX / "with_coords.pdf").read_bytes())
    assert 1.70 < pdf.metres_per_pt < 1.83  # 1/5000
    got = _lots(extract_plan(pdf.dxf, "EPSG:5174"))
    want = _lots(extract_plan(SAMPLE_DXF.read_bytes(), "EPSG:5174"))
    shared = [name for name in want if name in got]
    assert len(shared) >= 20, sorted(got)
    for name in ("F10", "주복1", "A22"):
        a, b = got[name].centroid, want[name].centroid
        assert GEOD.inv(a.x, a.y, b.x, b.y)[2] < 5.0
        assert abs(got[name].area / want[name].area - 1) < 0.03


def test_pdf_reads_roads_too():
    result = extract_plan(pdf_to_plan_dxf((FIX / "with_coords.pdf").read_bytes()).dxf, "EPSG:5174")
    assert len(result.roads) >= 10


def test_scan_pdf_is_refused_with_what_to_do():
    with pytest.raises(PlanExtractionError, match="스캔"):
        pdf_to_plan_dxf((FIX / "scan.pdf").read_bytes())


def test_pdf_without_frame_coordinates_says_what_to_do():
    with pytest.raises(PlanExtractionError, match="좌표 숫자"):
        pdf_to_plan_dxf((FIX / "no_coords.pdf").read_bytes())


def test_broken_pdf_is_refused():
    with pytest.raises(PlanExtractionError):
        pdf_to_plan_dxf(b"%PDF-1.4 not really")


def test_extract_endpoint_takes_pdf():
    client = TestClient(app)
    with (FIX / "with_coords.pdf").open("rb") as fh:
        res = client.post(
            "/api/planned-site/extract",
            files={"file": ("결정도.pdf", fh, "application/pdf")},
            data={"crs": "EPSG:5174"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["crs"] == "EPSG:5174"
    assert len(body["lots"]) >= 20
    assert any("좌표 숫자" in note for note in body["notes"])


def test_extract_endpoint_explains_scan_pdf():
    client = TestClient(app)
    with (FIX / "scan.pdf").open("rb") as fh:
        res = client.post("/api/planned-site/extract", files={"file": ("스캔.pdf", fh, "application/pdf")})
    assert res.status_code == 400
    assert "스캔" in res.json()["detail"]
