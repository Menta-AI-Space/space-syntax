"""시험용 계획 결정도 PDF — 운정 샘플 DXF를 「CAD에서 뽑은 결정도 PDF」처럼 그린다.

용도별 채움 + 도곽 TM 좌표 숫자 + 범례·제목. `tests/test_planned_site_pdf.py`가 쓴다.
reportlab이 필요하다(앱 의존성 아님):

    python tools/make_planned_site_pdf_fixture.py tests/fixtures/planned_site_pdf/with_coords.pdf 5000
    python tools/make_planned_site_pdf_fixture.py tests/fixtures/planned_site_pdf/no_coords.pdf 5000 --no-coords
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyproj import Transformer
from shapely.geometry import shape
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from app.services.planned_site_dxf import extract_plan
pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
data = (Path(__file__).resolve().parents[2] / "frontend-next/public/samples/planned-site-sample.dxf").read_bytes()
res = extract_plan(data, "EPSG:5174")
to_tm = Transformer.from_crs("EPSG:4326", "EPSG:5174", always_xy=True)
lots = []
for l in res.lots:
    ring = [to_tm.transform(x, y) for x, y in shape(l["geometry"]).exterior.coords]
    lots.append((l["lot_id"], ring))
xs = [p[0] for _, r in lots for p in r]; ys = [p[1] for _, r in lots for p in r]
x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
scale = float(sys.argv[2]) if len(sys.argv) > 2 else 5000  # 1:scale
NO_COORDS = "--no-coords" in sys.argv
mpp = scale * 0.0254 / 72  # m per pt
W, H = 842, 1191  # A3 세로
ox, oy = 200, 200  # 도면 왼아래 pt
def P(x, y): return ox + (x - x0) / mpp, oy + (y - y0) / mpp
c = canvas.Canvas(sys.argv[1], pagesize=(W, H))
c.setFont("HYSMyeongJo-Medium", 18); c.drawString(60, H - 50, "획지 및 건축물 등에 관한 도시관리계획 결정도 (시험용)")
palette = {"F": Color(.55,.55,.85), "A": Color(1,.85,.55), "주": Color(.95,.6,.6), "문": Color(.6,.85,.6), "초": Color(.7,.9,1)}
for name, ring in lots:
    col = next((v for k, v in palette.items() if name.startswith(k)), Color(.95,.95,.6))
    c.setFillColor(col); c.setStrokeColor(Color(0,0,0)); c.setLineWidth(.4)
    p = c.beginPath(); pts = [P(*q) for q in ring]; p.moveTo(*pts[0])
    for q in pts[1:]: p.lineTo(*q)
    p.close(); c.drawPath(p, fill=1, stroke=1)
    from shapely.geometry import Polygon
    cx, cy = Polygon(ring).representative_point().coords[0]
    c.setFillColor(Color(0,0,0)); c.setFont("HYSMyeongJo-Medium", 6); c.drawCentredString(*P(cx, cy), name)
# 도곽 + 좌표 숫자 (500m 간격)
fx0, fy0 = P(x0 - 200, y0 - 200); fx1, fy1 = P(x1 + 200, y1 + 200)
c.setStrokeColor(Color(0,0,0)); c.setLineWidth(1); c.rect(fx0, fy0, fx1 - fx0, fy1 - fy0, fill=0)
c.setFont("Helvetica", 7)
import math
for gx in ([] if NO_COORDS else range(int(math.ceil((x0-200)/500))*500, int(x1+200), 500)):
    px, _ = P(gx, 0); c.line(px, fy0, px, fy0 - 5); c.drawCentredString(px, fy0 - 14, f"{gx:,}")
    c.line(px, fy1, px, fy1 + 5); c.drawCentredString(px, fy1 + 8, f"{gx:,}")
for gy in ([] if NO_COORDS else range(int(math.ceil((y0-200)/500))*500, int(y1+200), 500)):
    _, py = P(0, gy); c.line(fx0, py, fx0 - 5, py); c.drawRightString(fx0 - 7, py - 2, f"{gy:,}")
    c.line(fx1, py, fx1 + 5, py); c.drawString(fx1 + 7, py - 2, f"{gy:,}")
# 범례 (채움 상자 — 본체에서 걸러야 한다)
c.setFont("HYSMyeongJo-Medium", 9)
for i, (k, col) in enumerate(palette.items()):
    c.setFillColor(col); c.rect(W - 160, 120 + i * 18, 20, 12, fill=1); c.setFillColor(Color(0,0,0)); c.drawString(W - 132, 122 + i * 18, {"F":"업무","A":"공동주택","주":"주상복합","문":"문화","초":"학교"}[k])
c.drawString(60, 60, f"축척 1/{int(scale)}   좌표계: 중부원점(TM)")
c.showPage(); c.save()
print("lots", len(lots), "extent m", round(x1-x0), round(y1-y0), "mpp", round(mpp,3), "TM centre", round((x0+x1)/2), round((y0+y1)/2))
