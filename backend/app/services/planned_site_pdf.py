"""계획 도면 **벡터 PDF** → 계획 도로선·획지선 DXF (그다음은 `planned_site_dxf.extract_plan`).

LH 토지 공고 중에는 CAD 원본(DWG) 없이 PDF만 붙은 것이 있다(부천대장, 운정3).
CAD에서 바로 뽑은 PDF라 선과 채움이 벡터로 살아 있다 — 운정 샘플을 그렇게
손으로 만들었다(`tools/make_unjeong_sample_dxf.py`). 이 모듈은 그 손일을 자동으로 한다.

소유자 결정(2026-11-09, `SPEC_SPACE_ANALYSIS_MODULES.md`): **사람 손은 최대한 안 가게,
가더라도 무엇을 해야 하는지 알게.**

1. **스캔·사진 PDF는 막는다.** 선이 없고 그림만 있으면 없는 선을 찾은 척하게 된다.
2. **땅 = 채움색이 있는 면.** 결정도는 용도별로 땅을 칠하고 도로는 비워 둔다. 한 블록이
   수십 조각으로 쪼개져 있어 색별로 합친다. 합친 땅들을 메워(닫기) 땅을 빼면 도로 면이다.
3. **위치 = 도곽에 찍힌 좌표 숫자.** 결정도 테두리에는 TM 좌표 눈금(예: 「209,500」)이
   찍힌다. 가로줄의 숫자는 x에 따라, 세로줄의 숫자는 y에 따라 늘어난다 — 그 관계를
   직선으로 맞추면 PDF pt → TM m 변환이 나온다(북쪽이 위인 도면만, 회전 없음).
   못 읽으면 할 일을 말하고 멈춘다.

좌표계(5174/5186 …)는 여기서 고르지 않는다 — 숫자는 TM 값일 뿐이고, 어느 원점인지는
`extract_plan`이 후보를 내고 사람이 지도에서 확인하는 기존 흐름을 그대로 쓴다.

⚠️ PyMuPDF는 AGPL이라 쓰지 않는다. pdfplumber(pdfminer.six, MIT).
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field

import ezdxf
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from app.services.planned_site_dxf import _LOT_NAME, PlanExtractionError

logger = logging.getLogger(__name__)

MAX_PAGES = 30
# 그림이 페이지를 덮고 벡터 조각이 이보다 적으면 스캔으로 본다(스캔 PDF에도 도장·테두리 몇 개는 있다).
SCAN_MAX_VECTORS = 200
# 이보다 적으면 도면이 아니다(글자만 있는 문서).
MIN_VECTORS = 10
SCAN_IMAGE_COVER = 0.5
# 채움 조각 최소 넓이(pt²). 글자 테두리·심볼 자투리를 거른다.
MIN_PIECE_PT2 = 0.6
# 땅 사이 틈(도로)을 메우는 닫기 반경(m). 운정: 6pt×7.14m ≈ 43m — 녹지 낀 대로까지 메운다.
CLOSE_M = 40.0
# 본체에서 떨어진 채움 덩어리(범례 상자 등)를 거르는 기준: 가장 큰 덩어리 대비 넓이.
BODY_MIN_SHARE = 0.15
# TM 좌표로 볼 수 있는 숫자 범위(m). 5174/5186 동서 10만~40만, 남북 20만~80만, 5179는 더 크다.
COORD_MIN, COORD_MAX = 50_000, 2_500_000
# 같은 줄로 칠 거리(pt)와, 숫자 옆 눈금선을 찾을 거리(pt).
ROW_TOL = 4.0
TICK_TOL = 8.0
# 가로·세로 축척이 이만큼 넘게 다르면 좌표 숫자로 보지 않는다.
SCALE_AGREE = 0.03
ROAD_LAYER = "계획선-도로"
LOT_LAYER = "계획선-획지선"

_NUM = re.compile(r"^(?:[XYENxyen]\s*[=:]?\s*)?(\d{1,3}(?:,\d{3})+|\d{5,7})(?:\.\d+)?(?:m)?$")


@dataclass
class PdfPlan:
    dxf: bytes
    notes: list[str] = field(default_factory=list)
    metres_per_pt: float = 0.0


@dataclass
class _Label:
    value: float
    x: float  # 글자 가운데 (pt, 아래가 0)
    y: float


def _is_white(color) -> bool:
    if color is None:
        return True
    try:
        vals = [float(v) for v in (color if isinstance(color, (list, tuple)) else [color])]
    except (TypeError, ValueError):
        return False  # 패턴 등 — 칠한 것으로 본다
    if len(vals) == 4:  # CMYK
        return all(v <= 0.02 for v in vals)
    return all(v >= 0.98 for v in vals)


def _color_key(color) -> tuple:
    try:
        return tuple(round(float(v), 2) for v in (color if isinstance(color, (list, tuple)) else [color]))
    except (TypeError, ValueError):
        return ("pattern",)


def _pick_page(pdf):
    best, best_n = None, -1
    for page in pdf.pages[:MAX_PAGES]:
        n = len(page.curves) + len(page.rects) + len(page.lines)
        if n > best_n:
            best, best_n = page, n
    return best, best_n


def _image_cover(page) -> float:
    area = float(page.width * page.height) or 1.0
    covered = 0.0
    for im in page.images:
        w = max(0.0, min(im["x1"], page.width) - max(im["x0"], 0))
        h = max(0.0, min(im["bottom"], page.height) - max(im["top"], 0))
        covered += w * h
    return covered / area


def _filled_pieces(page) -> dict[tuple, list[Polygon]]:
    """채움색이 있는 면을 색별로. 좌표는 pt, 아래가 0(y 위로)."""
    H = float(page.height)
    by_color: dict[tuple, list[Polygon]] = {}
    for obj in list(page.curves) + list(page.rects):
        if not obj.get("fill") or _is_white(obj.get("non_stroking_color")):
            continue
        if "pts" in obj and obj["pts"]:
            pts = [(float(x), H - float(t)) for x, t in obj["pts"]]
        else:
            x0, x1, top, bottom = (float(obj[k]) for k in ("x0", "x1", "top", "bottom"))
            pts = [(x0, H - bottom), (x1, H - bottom), (x1, H - top), (x0, H - top)]
        if len(pts) < 3:
            continue
        try:
            poly = make_valid(Polygon(pts))
        except Exception:
            continue
        if poly.area < MIN_PIECE_PT2:
            continue
        by_color.setdefault(_color_key(obj.get("non_stroking_color")), []).append(poly)
    return by_color


def _stroke_lines(page) -> list[LineString]:
    """칠하지 않은 선(획지선·도로선 후보). pt, 아래가 0."""
    H = float(page.height)
    out: list[LineString] = []
    for obj in list(page.lines) + list(page.curves):
        if obj.get("fill"):
            continue
        pts = obj.get("pts") or [(obj["x0"], obj["top"]), (obj["x1"], obj["bottom"])]
        pts = [(float(x), H - float(t)) for x, t in pts]
        if len(pts) >= 2:
            try:
                line = LineString(pts)
            except Exception:
                continue
            if line.length > 0.5:
                out.append(line)
    return out


def _labels(page) -> list[_Label]:
    H = float(page.height)
    out: list[_Label] = []
    for w in page.extract_words(keep_blank_chars=False):
        if w["x0"] < 0 or w["x1"] > page.width or w["top"] < 0 or w["bottom"] > page.height:
            continue  # 페이지 밖에 걸친 글자는 잘려 보이지 않는다
        m = _NUM.match(w["text"].strip())
        if not m:
            continue
        value = float(m.group(1).replace(",", ""))
        if COORD_MIN <= value <= COORD_MAX:
            out.append(_Label(value, (w["x0"] + w["x1"]) / 2, H - (w["top"] + w["bottom"]) / 2))
    return out


def _ticks(page) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """짧은 세로선(x, y0, y1)과 가로선(y, x0, x1) — 좌표 숫자 옆 눈금."""
    H = float(page.height)
    vert, horiz = [], []
    for ln in page.lines:
        x0, x1 = float(ln["x0"]), float(ln["x1"])
        y0, y1 = H - float(ln["bottom"]), H - float(ln["top"])
        if abs(x1 - x0) < 0.3 and abs(y1 - y0) > 1:
            vert.append((x0, y0, y1))
        elif abs(y1 - y0) < 0.3 and abs(x1 - x0) > 1:
            horiz.append((y0, x0, x1))
    return vert, horiz


def _fit(pairs: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    """value = a·pos + b 최소제곱. (a, b, 최대 잔차 m)."""
    if len({v for _, v in pairs}) < 2:
        return None
    n = len(pairs)
    mp = sum(p for p, _ in pairs) / n
    mv = sum(v for _, v in pairs) / n
    sxx = sum((p - mp) ** 2 for p, _ in pairs)
    if sxx <= 0:
        return None
    a = sum((p - mp) * (v - mv) for p, v in pairs) / sxx
    b = mv - a * mp
    resid = max(abs(a * p + b - v) for p, v in pairs)
    return a, b, resid


def _axis(labels: list[_Label], along: str, ticks) -> tuple[float, float] | None:
    """한 축(along='x'면 동서 숫자)의 pt→m 직선. 같은 줄에 늘어선 숫자들로 맞춘다."""
    rows: dict[int, list[_Label]] = {}
    for lab in labels:
        across = lab.y if along == "x" else lab.x
        key = round(across / ROW_TOL)
        rows.setdefault(key, []).append(lab)
    pairs: list[tuple[float, float]] = []
    for row in rows.values():
        pos = [(lab.x if along == "x" else lab.y) for lab in row]
        if len({lab.value for lab in row}) < 2 or max(pos) - min(pos) < 20:
            continue
        for lab in row:
            p = lab.x if along == "x" else lab.y
            # 숫자 바로 옆 눈금선이 있으면 그 자리를 쓴다(글자 가운데보다 정확하다).
            near = [
                t[0] for t in ticks
                if abs(t[0] - p) <= TICK_TOL
                and min(abs(t[1] - (lab.y if along == "x" else lab.x)),
                        abs(t[2] - (lab.y if along == "x" else lab.x))) <= 20
            ]
            if near:
                p = min(near, key=lambda q: abs(q - p))
            pairs.append((p, lab.value))
    fit = _fit(pairs)
    if fit is None:
        return None
    a, b, resid = fit
    if a <= 0:
        return None
    spacing = (max(v for _, v in pairs) - min(v for _, v in pairs)) or 1.0
    if resid > max(5.0, spacing * 0.01):
        return None
    return a, b


def _georef(page) -> tuple[float, float, float, float] | None:
    """(aE, bE, aN, bN): E = aE·x + bE, N = aN·y + bN. 못 읽으면 None."""
    labels = _labels(page)
    if len(labels) < 4:
        return None
    vert, horiz = _ticks(page)
    # 동서·남북 숫자는 크기대가 다를 수 있지만 섞여 있으니 줄 방향으로 가른다.
    ex = _axis(labels, "x", vert)
    ny = _axis(labels, "y", horiz)
    if not ex or not ny:
        return None
    if abs(ex[0] - ny[0]) / max(ex[0], ny[0]) > SCALE_AGREE:
        return None
    return ex[0], ex[1], ny[0], ny[1]


def pdf_to_plan_dxf(data: bytes) -> PdfPlan:
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        raise PlanExtractionError(
            "PDF 파일을 열 수 없습니다 — 파일이 깨졌거나 암호가 걸려 있을 수 있습니다."
        ) from exc
    with pdf:
        if not pdf.pages:
            raise PlanExtractionError("PDF에 페이지가 없습니다.")
        page, n_vectors = _pick_page(pdf)
        if n_vectors < SCAN_MAX_VECTORS and page.images and _image_cover(page) >= SCAN_IMAGE_COVER:
            raise PlanExtractionError(
                "스캔하거나 사진으로 찍은 PDF라 선을 읽을 수 없습니다. CAD에서 바로 뽑은 "
                "도면 PDF(공고 첨부 「결정도」 원본)나 DXF를 올려 주세요."
            )
        if n_vectors < MIN_VECTORS:
            raise PlanExtractionError(
                "PDF에서 도면 선을 찾지 못했습니다. 글자만 있는 문서라면 도면이 든 PDF를 올려 주세요."
            )

        geo = _georef(page)
        if geo is None:
            raise PlanExtractionError(
                "이 PDF에서 도면 테두리의 좌표 숫자(예: 「209,500」)를 찾지 못해 지도 위치를 "
                "정할 수 없습니다. 같은 공고에 CAD 도면(DWG)이 있으면 DXF로 저장해 올려 주세요. "
                "PDF를 지도에 직접 맞추는 기능은 준비 중입니다."
            )
        aE, bE, aN, bN = geo
        mpp = (aE + aN) / 2

        by_color = _filled_pieces(page)
        if not by_color:
            raise PlanExtractionError(
                "PDF에서 색칠된 땅을 찾지 못했습니다 — 용도별로 땅을 칠한 「결정도」 PDF를 올려 주세요."
            )
        merged = [make_valid(unary_union(pieces)) for pieces in by_color.values()]
        land = make_valid(unary_union(merged))

        # 본체만: 메운 덩어리 중 큰 것. 범례 상자·삽도는 따로 떨어져 있다.
        r = CLOSE_M / mpp
        filled = land.buffer(r, join_style="mitre").buffer(-r, join_style="mitre")
        blobs = list(getattr(filled, "geoms", [filled]))
        biggest = max(b.area for b in blobs)
        body = unary_union([b for b in blobs if b.area >= biggest * BODY_MIN_SHARE])
        lots_pt = []
        for poly in merged:
            part = make_valid(poly.intersection(body))
            for g in getattr(part, "geoms", [part]):
                if isinstance(g, Polygon) and g.area >= MIN_PIECE_PT2 * 50:
                    lots_pt.append(g)
        if not lots_pt:
            raise PlanExtractionError("PDF에서 땅으로 볼 면을 찾지 못했습니다.")
        land = unary_union(lots_pt)
        road_face = make_valid(body.difference(land))

        dividers = [
            ln for ln in _stroke_lines(page)
            if ln.within(land.buffer(0.5)) and ln.length * mpp >= 5
        ]

        def tm(x: float, y: float) -> tuple[float, float]:
            return aE * x + bE, aN * y + bN

        doc = ezdxf.new()
        msp = doc.modelspace()
        doc.layers.add(ROAD_LAYER)
        doc.layers.add(LOT_LAYER)
        for poly in lots_pt:
            for ring in [poly.exterior, *poly.interiors]:
                msp.add_lwpolyline([tm(x, y) for x, y in ring.coords], close=True, dxfattribs={"layer": LOT_LAYER})
        for ln in dividers:
            msp.add_lwpolyline([tm(x, y) for x, y in ln.coords], dxfattribs={"layer": LOT_LAYER})
        for g in getattr(road_face, "geoms", [road_face]):
            if isinstance(g, Polygon) and g.area > 0:
                for ring in [g.exterior, *g.interiors]:
                    msp.add_lwpolyline([tm(x, y) for x, y in ring.coords], close=True, dxfattribs={"layer": ROAD_LAYER})
        names = 0
        H = float(page.height)
        for w in page.extract_words():
            text = w["text"].strip()
            if _LOT_NAME.match(text):
                x, y = (w["x0"] + w["x1"]) / 2, H - (w["top"] + w["bottom"]) / 2
                msp.add_text(text, dxfattribs={"layer": LOT_LAYER, "insert": tm(x, y), "height": 5})
                names += 1

    buf = io.StringIO()
    doc.write(buf)
    notes = [
        f"PDF 도면 테두리의 좌표 숫자로 위치를 맞췄습니다(축척 약 1/{round(mpp * 72 / 0.0254, -2):,.0f}).",
        f"용도별로 칠한 땅 {len(by_color)}가지 색을 읽었습니다"
        + (f", 획지 이름 {names}개." if names else "."),
    ]
    return PdfPlan(dxf=buf.getvalue().encode("utf-8"), notes=notes, metres_per_pt=mpp)
