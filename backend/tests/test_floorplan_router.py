import io

import ezdxf
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app

client = TestClient(app)


def _synthetic_dxf_bytes() -> bytes:
    doc = ezdxf.new()
    doc.layers.add(name="WALL")
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "WALL"})
    msp.add_line((100, 0), (100, 100), dxfattribs={"layer": "WALL"})
    msp.add_line((100, 100), (0, 100), dxfattribs={"layer": "WALL"})
    msp.add_line((0, 100), (0, 0), dxfattribs={"layer": "WALL"})
    buf = io.StringIO()
    doc.write(buf, fmt="asc")
    return buf.getvalue().encode("utf-8")


def _synthetic_image_bytes() -> bytes:
    image = Image.new("L", (300, 200), color=255)
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 280, 180], outline=0, width=4)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_extract_from_image_rejected():
    """Photos/scans are rejected outright — wall detection on them is unreliable
    (see CLAUDE.md's DXF-only decision)."""
    response = client.post(
        "/api/floorplan/extract",
        files={"file": ("plan.png", _synthetic_image_bytes(), "image/png")},
    )
    assert response.status_code == 400


def test_extract_from_dxf_endpoint():
    response = client.post(
        "/api/floorplan/extract",
        files={"file": ("plan.dxf", _synthetic_dxf_bytes(), "application/dxf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "dxf"
    assert len(body["lines"]) == 4


def test_extract_read_summary_is_note_not_warning():
    """실명·문을 읽은 것은 「이렇게 읽었습니다」(auto_notes)다 — warnings에 넣으면
    화면이 주의 색으로 그려 문제가 있는 것처럼 읽힌다."""
    doc = ezdxf.new()
    doc.layers.add(name="WALL")
    msp = doc.modelspace()
    for a, b in [((0, 0), (100, 0)), ((100, 0), (100, 100)), ((100, 100), (0, 100)), ((0, 100), (0, 0))]:
        msp.add_line(a, b, dxfattribs={"layer": "WALL"})
    msp.add_text("매장 01", dxfattribs={"layer": "ROOM", "height": 3}).set_placement((40, 50))
    buf = io.StringIO()
    doc.write(buf, fmt="asc")
    response = client.post(
        "/api/floorplan/extract",
        files={"file": ("plan.dxf", buf.getvalue().encode("utf-8"), "application/dxf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert any("를 읽었습니다" in note for note in body["auto_notes"])
    assert not any("를 읽었습니다" in w for w in body["warnings"])


def test_extract_invalid_dxf_returns_400():
    response = client.post(
        "/api/floorplan/extract",
        files={"file": ("plan.dxf", b"not a dxf", "application/dxf")},
    )
    assert response.status_code == 400


def test_analyze_endpoint_returns_cells_and_explanation():
    lines = [
        {"x1": 0, "y1": 0, "x2": 60, "y2": 0},
        {"x1": 60, "y1": 0, "x2": 60, "y2": 30},
        {"x1": 60, "y1": 30, "x2": 0, "y2": 30},
        {"x1": 0, "y1": 30, "x2": 0, "y2": 0},
    ]
    response = client.post(
        "/api/floorplan/analyze",
        json={"canvas_width": 60, "canvas_height": 30, "lines": lines},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["cell_count"] > 0
    assert len(body["cells"]) == body["summary"]["cell_count"]
    assert body["explanation"]["source"] == "rule"


def test_analyze_endpoint_no_lines_rejected_by_schema():
    response = client.post(
        "/api/floorplan/analyze",
        json={"canvas_width": 60, "canvas_height": 30, "lines": []},
    )
    assert response.status_code == 422
