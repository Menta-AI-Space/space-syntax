"""Extract candidate wall line segments from an uploaded floor plan.

Two input paths (raster image via OpenCV, DXF via ezdxf) converge on one
schema: ``{canvas_width, canvas_height, lines}``. Neither path is trusted
blindly — the frontend lets the user add/remove/move lines before analysis
runs, because CV wall detection and CAD layer conventions both vary too much
by source to compute space-syntax metrics on unconfirmed geometry (see
CLAUDE.md's cross-sectional-model honesty principle — a number is only
meaningful if what it's measuring is actually there).
"""

from __future__ import annotations

import io
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import ezdxf

from .floorplan_layer_advice import suggest_wall_layers
import ezdxf.recover
import numpy as np
from PIL import Image

# Downscale before CV processing — keeps wall detection inside the app's
# 5-second budget regardless of the phone-photo resolution a user uploads.
MAX_IMAGE_DIM = 1600

# Hough bridges gaps up to this many pixels. Kept small on purpose: a doorway is
# a *gap* in the wall, and a generous gap setting welds it shut, which silently
# seals every room (measured on real plans: gap=8 left only 29% of one apartment
# connected, gap=2 left 54%). Under-detected walls are recoverable by the user in
# the editor; a bridged doorway looks correct and is not.
_HOUGH_MAX_LINE_GAP = 2

_WALL_LAYER_NEEDLES = ("wall", "벽")
# 레이어명을 구분자로 쪼갰을 때 **토큰이 통째로** 이것이면 벽으로 본다.
# 실무 도면에서 받은 세 장이 전부 축약형을 썼다(2026-10-01): `WA1`(한국 사무소),
# `AA-WAXM-CONC`(AIA 변형), `A-WL-FUL`(AIA). 부분 문자열로 찾으면 `WIND`·`SWALE`
# 같은 것까지 걸리므로 토큰 단위로만 맞춘다.
_WALL_LAYER_TOKENS = ("wa", "wl", "wal", "walls", "cw", "벽체", "내벽", "외벽", "구조벽")
# 토큰 앞부분이 이것으로 시작하면 벽으로 본다 (`WA1`, `WAXM`, `WL2` 같은 번호·접미).
_WALL_TOKEN_PREFIXES = ("wa", "wl")
_LAYER_SPLIT = ("-", "_", ".", " ", "/")
# CAD convention (verified against real AIA-layered DXF): walls are drawn as
# continuous lines and doors/windows live on their own layers, so the wall layer
# alone has no openings and every room reads as sealed. These entities mark where
# to punch the openings back in.
_OPENING_LAYER_NEEDLES = ("door", "glaz", "window", "wind", "문", "창")
# 벽과 같은 이유로 문·창도 축약형을 쓴다. `A-DR-FUL`(문)·`A-WX-SIL`(창)이 실무
# 도면(`PLAN-1ST FLOOR.dwg`)에 그대로 있었는데 위 needle로는 하나도 안 걸려서
# **문이 0개**로 읽혔다 — 방이 44조각으로 끊기고 "문 자리를 찍어 주세요"가 떴다.
# 문은 도면에 있었고 우리가 이름을 못 알아본 것이다(2026-10-01).
_OPENING_TOKEN_PREFIXES = ("dr", "wx")


class FloorplanExtractionError(Exception):
    pass


@dataclass
class LayerInfo:
    """One DXF layer, with enough numbers for a person to say "that's the wall".

    Layer naming is the single biggest reason a real office drawing fails here:
    ``A-WALL-FULL`` matches our needles, ``구조체``·``칸막이``·``PARTITION`` do
    not, and when nothing matches the extractor falls back to treating *every*
    layer as wall — which also throws the door layer away, sealing every room
    (measured 2026-09-30 on the complex-mall fixture: 100% coverage → 31%).
    So instead of guessing harder in silence, we hand the frontend the list and
    let the user correct it.
    """

    name: str
    entity_count: int
    segment_count: int
    total_length: float
    # 이 레이어 선분 길이의 중앙값. 벽은 길고, 문·창은 짧고, 치수선·해치는
    # 아주 짧은 선이 많다 — 이름을 못 믿을 때 쓰는 두 번째 단서.
    median_length: float
    text_count: int
    block_count: int
    # wall | opening | text | symbol | other — 자동 추측 (사람이 고칠 수 있다)
    guess: str
    # 이 추측이 이름으로 맞춘 것인지(확실) 통계로 짐작한 것인지(불확실)
    guessed_by: str  # name | size | none


@dataclass
class WallLine:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class PlanLabel:
    """A room name (TEXT/MTEXT) with its anchor point, canvas coordinates."""

    text: str
    x: float
    y: float


@dataclass
class PlanSymbol:
    """A block reference (INSERT) classified by its block name — stairs,
    elevators, entrances/exits — plus doors found on the outer wall, which
    real exports rarely mark with a symbol but which we can infer."""

    kind: str  # stair | elevator | entrance | exit | other
    name: str
    x: float
    y: float


# 블록 이름으로 심볼 종류를 가른다. 사무소마다 이름이 달라 needle 포함 검사다 —
# 레이어명 분류(_layer_kind)와 같은 한계를 진다.
_SYMBOL_NEEDLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stair", ("stair", "step", "계단")),
    ("elevator", ("elev", "lift", "ev", "엘리베이터", "승강기")),
    ("exit", ("exit", "emergency", "비상")),
    ("entrance", ("entrance", "entry", "main", "출입구", "입구")),
)


def _symbol_kind(block_name: str) -> str:
    name = block_name.lower()
    for kind, needles in _SYMBOL_NEEDLES:
        if any(n in name for n in needles):
            return kind
    return "other"


# 벽으로 쓰지 않은 레이어의 선 길이가 벽 레이어의 이 비율을 넘으면, 그것도 벽일
# 수 있다고 알린다. 실무에서 `칸막이`·`PARTITION`처럼 이름 규칙에 안 걸리는
# 내벽이 따로 있는 일이 흔하고, 그걸 빠뜨리면 방이 나뉘지 않아 매장별 표가
# 통째로 틀린다 — 그런데 화면에는 아무 표시도 안 났다.
SUSPECT_WALL_RATIO = 0.15


@dataclass
class XrefWarning:
    """A block reference that points at *another file*.

    The geometry genuinely is not in the bytes we were given, so nothing on our
    side can recover it. Saying so precisely beats drawing an empty plan.
    """

    name: str
    path: str


@dataclass
class ExtractionResult:
    canvas_width: float
    canvas_height: float
    lines: list[WallLine]
    source: str
    # Door/window spans that should be carved back out of the walls. Only DXF
    # can supply these (raster plans carry no layer information).
    openings: list[WallLine] = field(default_factory=list)
    # Metres per canvas unit, when the DXF header declared a length unit
    # ($INSUNITS). None means the plan is unitless and the user must set the
    # scale (or accept results in canvas units only).
    meters_per_unit: float | None = None
    # Human-readable unit the DXF declared ("mm", "m", ...) or None.
    dxf_units: str | None = None
    # 사람이 고르지 않아도 프로그램이 알아서 한 일. 화면은 이걸 "이렇게 읽었습니다"
    # 한 줄로 보여 주고, 틀렸을 때만 사람이 손대면 된다.
    auto_notes: list[str] = field(default_factory=list)
    # Semantic elements (2026-09-18). Empty for plans that carry none.
    labels: list[PlanLabel] = field(default_factory=list)
    symbols: list[PlanSymbol] = field(default_factory=list)
    layers: list[LayerInfo] = field(default_factory=list)
    xrefs: list[XrefWarning] = field(default_factory=list)
    # 이름으로 벽 레이어를 못 찾아 통계로 짐작했는지 — 화면이 "확인해 주세요"를
    # 띄울지 정하는 기준.
    layers_guessed: bool = False
    # 벽으로 안 썼지만 벽일 수 있는 레이어 이름들 (선 길이가 상당한 것).
    suspect_wall_layers: list[str] = field(default_factory=list)


# DXF $INSUNITS codes → (label, metres per drawing unit). Only the ones that
# turn up in architectural exports; anything else is treated as unknown.
_DXF_UNITS: dict[int, tuple[str, float]] = {
    1: ("inch", 0.0254),
    2: ("ft", 0.3048),
    4: ("mm", 0.001),
    5: ("cm", 0.01),
    6: ("m", 1.0),
}


def _guess_meters_per_unit(span: float) -> float:
    """$INSUNITS가 없는 도면의 단위를 크기로 짚는다.

    정확할 필요는 없다 — 「도면 사이의 빈 공간」을 미터로 재는 데만 쓰고, 건물
    한 채는 보통 10~200m다. 틀려도 분석 결과의 치수에는 영향이 없다(치수는
    `meters_per_unit`이 따로 들고 있고, 단위가 없으면 그쪽은 None으로 남는다).
    """
    if span > 2_000:
        return 0.001  # mm로 그린 도면
    if span > 200:
        return 0.01  # cm
    return 1.0


def _dxf_length_unit(doc) -> tuple[str, float] | None:
    try:
        code = int(doc.header.get("$INSUNITS", 0))
    except Exception:
        return None
    return _DXF_UNITS.get(code)


def extract_from_image(data: bytes) -> ExtractionResult:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise FloorplanExtractionError(f"이미지를 열 수 없습니다: {exc}") from exc

    image = image.convert("L")
    width, height = image.size
    scale = min(1.0, MAX_IMAGE_DIM / max(width, height))
    if scale < 1.0:
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
    arr = np.array(image)

    edges = cv2.Canny(arr, 50, 150)
    raw_lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=40,
        minLineLength=25,
        maxLineGap=_HOUGH_MAX_LINE_GAP,
    )
    lines: list[WallLine] = []
    if raw_lines is not None:
        for x1, y1, x2, y2 in raw_lines[:, 0]:
            lines.append(WallLine(float(x1), float(y1), float(x2), float(y2)))

    out_width, out_height = image.size
    return ExtractionResult(
        canvas_width=float(out_width), canvas_height=float(out_height), lines=lines, source="image"
    )


def _layer_tokens(layer: str) -> list[str]:
    name = layer.lower()
    for sep in _LAYER_SPLIT:
        name = name.replace(sep, "|")
    return [t for t in name.split("|") if t]


def _looks_like_opening_token(token: str) -> bool:
    # 벽 토큰과 같은 규칙(접두 + 두 글자 이내). `drain`·`drive`는 길어서 안 걸린다.
    # `gls`는 일부러 넣지 않는다 — `A-WL-GLS`는 유리벽이지 개구부가 아니다.
    return any(
        token.startswith(prefix) and len(token) <= len(prefix) + 2
        for prefix in _OPENING_TOKEN_PREFIXES
    )


def _looks_like_wall_token(token: str) -> bool:
    if token in _WALL_LAYER_TOKENS:
        return True
    # `WA1`·`WAXM`·`WL2` — 접두가 맞고 **두 글자 이내**만 더 붙었으면 벽 계열로
    # 본다. 한 글자만 더 허용하면 `WAXM`을 놓치고, 세 글자까지 열면 `WATER`가
    # 걸린다(실제로 걸렸다).
    return any(
        token.startswith(prefix) and len(token) <= len(prefix) + 2
        for prefix in _WALL_TOKEN_PREFIXES
    )


def _layer_kind(layer: str) -> str:
    """Classify a DXF layer as wall, opening, or other by its name."""
    name = layer.lower()
    if any(needle in name for needle in _OPENING_LAYER_NEEDLES):
        return "opening"
    tokens = _layer_tokens(layer)
    if any(_looks_like_opening_token(t) for t in tokens):
        return "opening"
    if any(needle in name for needle in _WALL_LAYER_NEEDLES):
        return "wall"
    if any(_looks_like_wall_token(t) for t in tokens):
        return "wall"
    return "other"


def _read_dxf(data: bytes):
    """Parse DXF bytes, retrying with ezdxf's recovery mode for sloppy files."""
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            return ezdxf.readfile(str(tmp_path))
        except Exception:
            # Plenty of real exports are non-conformant (wrong version tag,
            # missing subclass markers). Recovery salvages most of them.
            doc, _auditor = ezdxf.recover.readfile(str(tmp_path))
            return doc
    except Exception as exc:
        # 2026-11-07 UX 루프: 예전엔 ezdxf 영어 원문(「Invalid group code "…" at line 1.」)을
        # 그대로 붙여 화면에 보였다 — 사용자가 할 일만 한국어로 말한다(원문은 __cause__에 남는다).
        raise FloorplanExtractionError(
            "DXF 파일을 읽을 수 없습니다 — 파일이 깨졌거나 DXF 형식이 아닙니다. "
            "CAD 프로그램에서 「다른 이름으로 저장 → DXF」로 다시 저장해 올려 주세요."
        ) from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


# 곡선을 직선으로 펼 때 허용하는 최대 벗어남(sagitta)을 반지름의 몇 분의 1로 둘지.
# 격자 한 칸이 보통 수십 cm~수 m라 이보다 곱게 펴도 결과가 달라지지 않는다 —
# 벽이 막혔나 뚫렸나만 결정하면 된다.
_FLATTEN_RATIO = 25.0
_MAX_FLATTEN_POINTS = 64
# SPLINE·ELLIPSE는 반지름이 없어 상대 기준을 못 쓴다. 도면 단위로 준 값이고,
# 격자 한 칸(보통 수십 cm~수 m)보다 훨씬 곱다.
_SPLINE_SAGITTA = 0.05


def _points_to_segments(points) -> list[tuple[float, float, float, float]]:
    out: list[tuple[float, float, float, float]] = []
    for a, b in zip(points, points[1:]):
        out.append((float(a[0]), float(a[1]), float(b[0]), float(b[1])))
    return out


def _flatten_curved(entity) -> list[tuple[float, float, float, float]]:
    """ARC·CIRCLE을 직선 조각으로. **각도 해석은 ezdxf에 맡긴다.**

    처음엔 호의 시작·끝 각을 직접 돌려 폈는데, DXF의 회전 방향 규약(양수 bulge가
    어느 쪽으로 부푸는가)을 손으로 다루다 반원이 반대로 뒤집혔다. ezdxf의
    ``flattening()``은 그 규약을 이미 알고 실제 곡선 위의 점을 준다.
    """
    radius = float(getattr(entity.dxf, "radius", 0.0) or 0.0)
    if radius <= 0:
        return []
    try:
        points = list(entity.flattening(radius / _FLATTEN_RATIO))
    except Exception:
        return []
    if len(points) > _MAX_FLATTEN_POINTS:
        step = len(points) // _MAX_FLATTEN_POINTS + 1
        points = points[::step] + [points[-1]]
    return _points_to_segments(points)


# 블록 안에 블록이 든 도면이 있다. 끝없이 파고들지 않도록 깊이를 막는다.
_MAX_BLOCK_DEPTH = 4


def _explode_insert(entity, depth: int = 0):
    """INSERT 안의 실제 도형을 (엔티티, 레이어) 쌍으로 펼친다.

    사무소 도면은 평면 전체를 블록 하나로 묶어 꽂아 두는 일이 흔하다. 그러면
    모델스페이스에는 INSERT 하나뿐이고 **선이 한 줄도 없어** 지금까지 "DXF에서
    선 엔티티를 찾지 못했습니다"로 끝났다 — 화면에는 도면이 멀쩡히 보이는데.

    블록 안 도형의 레이어가 ``0``이면 CAD 규약상 꽂은 쪽 레이어를 따른다.
    그 규약을 지켜야 벽·문 구분이 살아난다.
    """
    if depth >= _MAX_BLOCK_DEPTH:
        return []
    host_layer = str(getattr(entity.dxf, "layer", "") or "0")
    try:
        children = list(entity.virtual_entities())
    except Exception:
        return []
    out = []
    for child in children:
        child_layer = str(getattr(child.dxf, "layer", "") or "0")
        effective = host_layer if child_layer in ("0", "") else child_layer
        if child.dxftype() == "INSERT":
            out.extend(_explode_insert(child, depth + 1))
        else:
            out.append((child, effective))
    return out


def _entity_segments(entity) -> list[tuple[float, float, float, float]]:
    """DXF 엔티티 하나를 직선 조각들로.

    LINE·POLYLINE 외에 **ARC·CIRCLE과 곡선 폴리라인(bulge)**도 편다. 합성 도면은
    전부 직선이라 없어도 됐지만, 실제 도면은 둥근 벽·문 심볼·곡선 통로를 이걸로
    그린다. 빠뜨리면 그 자리가 뚫린 채로 분석돼 바깥 공기가 새어 들어온다.
    """
    dxftype = entity.dxftype()
    if dxftype == "LINE":
        start, end = entity.dxf.start, entity.dxf.end
        return [(float(start.x), float(start.y), float(end.x), float(end.y))]
    if dxftype in ("ARC", "CIRCLE"):
        return _flatten_curved(entity)
    if dxftype in ("SPLINE", "ELLIPSE"):
        # 곡선 통로·라운드 코너에 쓰인다. 반지름이 없으므로 도면 크기를 모른 채
        # 펴야 하는데, ezdxf가 sagitta를 받아 알아서 조각 수를 정한다.
        try:
            points = list(entity.flattening(_SPLINE_SAGITTA))
        except Exception:
            return []
        if len(points) > _MAX_FLATTEN_POINTS:
            step = len(points) // _MAX_FLATTEN_POINTS + 1
            points = points[::step] + [points[-1]]
        return _points_to_segments(points)
    if dxftype == "MLINE":
        # 다중선 — 벽을 두 줄로 한 번에 긋는 기능이다. 어떤 캐드는 벽을 전부
        # 이걸로 그린다. ezdxf가 실제 선들로 쪼개 준다.
        try:
            return [seg for part in entity.virtual_entities() for seg in _entity_segments(part)]
        except Exception:
            return []
    if dxftype not in ("LWPOLYLINE", "POLYLINE"):
        return []

    # 폴리라인은 ezdxf가 LINE·ARC로 쪼개 준다. 직접 bulge를 풀지 않는 이유는
    # 위 _flatten_curved의 주석과 같다.
    try:
        exploded = list(entity.virtual_entities())
    except Exception:
        exploded = []
    if exploded:
        segments: list[tuple[float, float, float, float]] = []
        for part in exploded:
            segments.extend(_entity_segments(part))
        if segments:
            return segments

    # 폴백: 꼭짓점만 잇는다 (곡선 정보는 잃지만 벽의 대체적 위치는 남는다).
    if dxftype == "LWPOLYLINE":
        points = [(p[0], p[1]) for p in entity.get_points()]
    else:
        points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
    segments = _points_to_segments(points)
    if getattr(entity, "closed", False) and len(points) > 2:
        (x1, y1), (x2, y2) = points[-1], points[0]
        segments.append((float(x1), float(y1), float(x2), float(y2)))
    return segments


def _text_of(entity) -> str:
    if entity.dxftype() == "MTEXT":
        return entity.plain_text().strip()
    return str(entity.dxf.text).strip()


def _text_anchor(entity) -> tuple[float, float]:
    if entity.dxftype() == "MTEXT":
        p = entity.dxf.insert
        return float(p.x), float(p.y)
    # TEXT: prefer the alignment point when the text is aligned, else insert.
    p = entity.dxf.align_point if entity.dxf.hasattr("align_point") and entity.dxf.halign else entity.dxf.insert
    return float(p.x), float(p.y)


# 치수·해치·중심선처럼 벽일 수 없는 레이어. 이름에 이게 들어가면 후보에서 뺀다.
_NOISE_LAYER_NEEDLES = (
    "dim", "치수", "hatch", "해치", "text", "문자", "grid", "그리드",
    "center", "중심", "axis", "축", "title", "표제", "note", "주기",
    # 캐드가 만드는 특수 레이어와 배치·대지 레이어. 실무 도면에서 이것들이
    # 「벽 후보」로 올라와 건물 대신 **땅을 감쌌다**(2026-10-01 실측).
    "defpoints", "viewport", "a-sp-", "대지", "배치", "경계", "lot", "site",
)


def _is_noise_layer(name: str) -> bool:
    lowered = name.lower()
    return any(needle in lowered for needle in _NOISE_LAYER_NEEDLES)


def _collect_layers(stats: dict[str, dict]) -> tuple[list[LayerInfo], bool]:
    """Turn per-layer tallies into LayerInfo, guessing the wall layer.

    Name match wins. When nothing matches we pick the layer with the greatest
    total line length as the wall — on a floor plan that is nearly always the
    walls — and mark it ``guessed_by="size"`` so the screen can ask.
    """
    named_wall = [n for n in stats if _layer_kind(n) == "wall" and not _is_noise_layer(n)]
    guessed = False
    fallback_wall: str | None = None
    if not named_wall:
        candidates = [
            (data["total_length"], name)
            for name, data in stats.items()
            if data["segment_count"] and not _is_noise_layer(name)
        ]
        if candidates:
            fallback_wall = max(candidates)[1]
            guessed = True

    layers: list[LayerInfo] = []
    for name, data in sorted(stats.items(), key=lambda kv: -kv[1]["total_length"]):
        kind = _layer_kind(name)
        by = "name" if kind in ("wall", "opening") else "none"
        if kind == "other":
            if name == fallback_wall:
                kind, by = "wall", "size"
            elif data["text_count"] and not data["segment_count"]:
                kind = "text"
            elif data["block_count"] and not data["segment_count"]:
                kind = "symbol"
        lengths = data["lengths"]
        lengths.sort()
        layers.append(
            LayerInfo(
                name=name,
                entity_count=data["entity_count"],
                segment_count=data["segment_count"],
                total_length=data["total_length"],
                median_length=lengths[len(lengths) // 2] if lengths else 0.0,
                text_count=data["text_count"],
                block_count=data["block_count"],
                guess=kind,
                guessed_by=by,
            )
        )
    return layers, guessed


def _find_xrefs(doc) -> list[XrefWarning]:
    out: list[XrefWarning] = []
    try:
        for block in doc.blocks:
            record = getattr(block, "block_record", None)
            path = ""
            if record is not None:
                path = str(getattr(record.dxf, "xref_path", "") or "")
            if not path:
                path = str(getattr(block.block_record.dxf, "xref_path", "") or "") if record else ""
            if path:
                out.append(XrefWarning(name=str(block.name), path=path))
    except Exception:
        return out
    return out


def extract_from_dxf(
    data: bytes,
    wall_layers: list[str] | None = None,
    opening_layers: list[str] | None = None,
) -> ExtractionResult:
    """``wall_layers``/``opening_layers`` override the automatic guess — the
    frontend sends them back after the user corrects the layer list."""
    doc = _read_dxf(data)

    wall_override = {n.lower() for n in (wall_layers or [])}
    opening_override = {n.lower() for n in (opening_layers or [])}

    all_lines: list[WallLine] = []
    wall_layer_lines: list[WallLine] = []
    opening_lines: list[WallLine] = []
    lines_by_layer: dict[str, list[WallLine]] = {}
    raw_labels: list[tuple[str, float, float]] = []
    raw_symbols: list[tuple[str, str, float, float]] = []
    stats: dict[str, dict] = {}

    def tally(layer: str) -> dict:
        return stats.setdefault(
            layer,
            {"entity_count": 0, "segment_count": 0, "total_length": 0.0,
             "lengths": [], "text_count": 0, "block_count": 0},
        )

    # (엔티티, 레이어) 대기열. 모델스페이스를 먼저 훑고, 그때 만난 INSERT의
    # 내용물이 여기 쌓여 같은 방식으로 처리된다.
    pending: list = []

    def walk():
        for item in doc.modelspace():
            yield item, str(getattr(item.dxf, "layer", ""))
        while pending:
            yield pending.pop()

    for entity, layer in walk():
        dxftype = entity.dxftype()
        bucket = tally(layer)
        bucket["entity_count"] += 1
        if dxftype in ("TEXT", "MTEXT"):
            bucket["text_count"] += 1
            text = _text_of(entity)
            if text:
                x, y = _text_anchor(entity)
                raw_labels.append((text, x, y))
            continue
        if dxftype == "INSERT":
            bucket["block_count"] += 1
            name = str(entity.dxf.name)
            # 블록은 심볼이기도 하고 도형 덩어리이기도 하다. **이름으로 알아본
            # 심볼(계단·EV·출입구)은 표식이지 벽이 아니므로 펼치지 않는다** —
            # 펼치면 계단 그림의 선들이 벽 후보가 되어 방을 막아 버린다.
            # 나머지 블록(평면 전체를 묶어 꽂은 경우)만 안을 들여다본다.
            if _symbol_kind(name) == "other":
                pending.extend(_explode_insert(entity))
            p = entity.dxf.insert
            raw_symbols.append((_symbol_kind(name), name, float(p.x), float(p.y)))
            continue
        lowered = layer.lower()
        if wall_override or opening_override:
            # 사람이 고른 것이 이름 규칙을 이긴다.
            if lowered in opening_override:
                kind = "opening"
            elif lowered in wall_override:
                kind = "wall"
            else:
                kind = "ignore"
        else:
            kind = _layer_kind(layer)
            if kind == "other" and _is_noise_layer(layer):
                kind = "ignore"

        for x1, y1, x2, y2 in _entity_segments(entity):
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            bucket["segment_count"] += 1
            bucket["total_length"] += length
            bucket["lengths"].append(length)
            line = WallLine(float(x1), float(y1), float(x2), float(y2))
            lines_by_layer.setdefault(layer, []).append(line)
            if kind == "ignore":
                continue
            if kind == "opening":
                opening_lines.append(line)
                continue
            all_lines.append(line)
            if kind == "wall":
                wall_layer_lines.append(line)

    unit = _dxf_length_unit(doc)
    layers, layers_guessed = _collect_layers(stats)
    if wall_override:
        # 사람이 직접 고른 뒤에는 "짐작했습니다"라고 말하면 안 된다.
        layers_guessed = False
        for layer in layers:
            lowered = layer.name.lower()
            if lowered in wall_override:
                layer.guess, layer.guessed_by = "wall", "name"
            elif lowered in opening_override:
                layer.guess, layer.guessed_by = "opening", "name"
            elif layer.guess in ("wall", "opening"):
                layer.guess, layer.guessed_by = "other", "none"
    xrefs = _find_xrefs(doc)

    # ── 도면 정규화 ────────────────────────────────────────────────────────
    # 손님은 건축사사무소에서 받은 파일을 그대로 넣는다. "한 평면만 남기고 잘라서
    # 올려 주세요"·"어느 레이어가 벽입니까"는 **우리가 할 일을 손님에게 넘기는
    # 것**이다(2026-10-01 결정). 그래서 여기서 둘 다 프로그램이 한다:
    #   1. 한 장에 도면이 여럿이면 **벽이 가장 많이 든 도면 한 장**을 골라 자른다.
    #   2. 이름으로 찾은 벽만으로 건물이 안 닫히면 **닫히는 레이어를 찾아 더한다.**
    # 사람이 레이어를 직접 고른 뒤에는 건드리지 않는다 — 사람 선택이 늘 이긴다.
    auto_notes: list[str] = []
    if not wall_override and not opening_override and lines_by_layer:
        every = [ln for group in lines_by_layer.values() for ln in group]
        axs = [v for ln in every for v in (ln.x1, ln.x2)]
        ays = [v for ln in every for v in (ln.y1, ln.y2)]
        ox, oy = min(axs), min(ays)
        span_u = max(max(axs) - ox, max(ays) - oy)
        mpu = unit[1] if unit else _guess_meters_per_unit(span_u)
        scaled = {
            name: [
                ((ln.x1 - ox) * mpu, (ln.y1 - oy) * mpu,
                 (ln.x2 - ox) * mpu, (ln.y2 - oy) * mpu)
                for ln in group
            ]
            for name, group in lines_by_layer.items()
        }
        named_wall_layers = [
            layer.name for layer in layers
            if layer.guess == "wall" and layer.guessed_by == "name"
        ]
        skip_layers = {
            name for name in lines_by_layer
            if _layer_kind(name) == "opening" or _is_noise_layer(name)
        }
        try:
            advice = suggest_wall_layers(
                scaled, named_wall_layers,
                (max(axs) - ox) * mpu, (max(ays) - oy) * mpu,
                skip=skip_layers,
            )
        except Exception:
            # 추천이 실패해도 분석 자체는 계속된다 — 이름으로 짚은 벽으로 간다.
            advice = None

        if advice and advice.core_box:
            bx0, by0, bx1, by1 = advice.core_box
            box = (bx0 / mpu + ox, by0 / mpu + oy, bx1 / mpu + ox, by1 / mpu + oy)

            def inside(x: float, y: float) -> bool:
                return box[0] <= x <= box[2] and box[1] <= y <= box[3]

            def on_sheet(ln: WallLine) -> bool:
                return inside(ln.x1, ln.y1) or inside(ln.x2, ln.y2)

            if advice.drawing_count > 1:
                auto_notes.append(
                    f"한 장에 도면이 {advice.drawing_count}개 있어 그중 벽이 가장 많이 "
                    "그려진 도면 하나만 분석했습니다."
                )
                all_lines = [ln for ln in all_lines if on_sheet(ln)]
                wall_layer_lines = [ln for ln in wall_layer_lines if on_sheet(ln)]
                opening_lines = [ln for ln in opening_lines if on_sheet(ln)]
                raw_labels = [t for t in raw_labels if inside(t[1], t[2])]
                raw_symbols = [t for t in raw_symbols if inside(t[2], t[3])]
                lines_by_layer = {
                    name: kept for name, group in lines_by_layer.items()
                    if (kept := [ln for ln in group if on_sheet(ln)])
                }

            if advice.added:
                auto_notes.append(
                    f"벽 레이어 이름만으로는 건물 외곽이 닫히지 않아 "
                    f"{' · '.join(advice.added)}을(를) 벽에 포함했습니다."
                )
            if advice.wall_layers:
                picked = {name.lower() for name in advice.wall_layers}
                wall_layer_lines = [
                    ln for name, group in lines_by_layer.items()
                    if name.lower() in picked for ln in group
                ]
                for layer in layers:
                    if layer.name.lower() in picked and layer.guess != "wall":
                        layer.guess, layer.guessed_by = "wall", "size"

    if wall_layer_lines:
        lines = wall_layer_lines
    elif wall_override:
        # 사람이 고른 레이어에 선이 없다 — 잘못 골랐다는 뜻이니 조용히 다른 걸
        # 쓰지 말고 그대로 알려야 한다.
        raise FloorplanExtractionError(
            "고르신 레이어에 선이 없습니다. 벽 레이어를 다시 선택해 주세요."
        )
    else:
        # 이름으로 못 찾았으면 크기로 짚은 레이어 **하나만** 벽으로 쓴다.
        # 예전엔 모든 레이어를 합쳤는데, 그러면 문 레이어까지 벽이 되어 방이
        # 전부 밀폐됐다 (2026-09-30 실측: 반영률 100% → 31%).
        sized = next((l.name for l in layers if l.guess == "wall" and l.guessed_by == "size"), None)
        lines = lines_by_layer.get(sized, []) if sized else all_lines
        if not lines:
            lines = all_lines
        layers_guessed = True

    if not lines:
        if xrefs:
            raise FloorplanExtractionError(
                f"이 도면은 외부 참조(XREF) {len(xrefs)}개를 쓰고 있어 벽이 이 파일 안에 없습니다. "
                "캐드에서 XREF를 BIND한 뒤 다시 저장해 주세요."
            )
        if not any(layer.segment_count for layer in layers):
            # 「선 엔티티」는 캐드 용어라 무엇을 하라는 건지 안 읽혔다 (2026-11-07 UX 자동 개선).
            raise FloorplanExtractionError(
                "이 DXF에서 선을 하나도 찾지 못했습니다. 빈 도면이거나 벽이 선이 아닌 이미지·글자로만 "
                "들어 있을 수 있습니다 — 캐드에서 벽과 문이 선으로 보이는지 확인하고 다시 저장해 주세요."
            )
        # 선은 있는데 전부 치수·해치 같은 레이어였다. 여기서 실패로 끝내면
        # 사람이 고칠 기회가 없다 — 빈 도면과 레이어 목록을 함께 돌려주고
        # 화면이 "어느 레이어가 벽입니까"를 묻게 한다.
        layers_guessed = True

    # 문 레이어를 이름으로 찾았으면, 벽을 크기로 짚었더라도 문은 살려 둔다.
    # 벽 후보를 "전부 합치기"로 떨어뜨렸을 때만 문을 버린다 — 그때는 어느 선이
    # 문인지 구분할 근거가 없기 때문이다.
    named_openings = any(l.guess == "opening" and l.guessed_by == "name" for l in layers)
    if not wall_layer_lines and not wall_override and not named_openings:
        opening_lines = []

    extent_lines = lines or [ln for group in lines_by_layer.values() for ln in group]
    xs = [v for line in extent_lines for v in (line.x1, line.x2)]
    ys = [v for line in extent_lines for v in (line.y1, line.y2)]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)
    scale = MAX_IMAGE_DIM / max(span_x, span_y)

    def project(x: float, y: float) -> tuple[float, float]:
        # DXF is y-up; canvas/image coordinates are y-down.
        return (x - min_x) * scale, (max_y - y) * scale

    def normalize(source_lines: list[WallLine]) -> list[WallLine]:
        out: list[WallLine] = []
        for line in source_lines:
            nx1, ny1 = project(line.x1, line.y1)
            nx2, ny2 = project(line.x2, line.y2)
            out.append(WallLine(nx1, ny1, nx2, ny2))
        return out

    openings = normalize(opening_lines)

    labels = [PlanLabel(text, *project(x, y)) for text, x, y in raw_labels]
    symbols = [PlanSymbol(kind, name, *project(x, y)) for kind, name, x, y in raw_symbols]

    # 바깥 벽 위에 놓인 문은 출입구다. 실제 도면은 출입구 심볼을 잘 안 넣으므로
    # 기하로 추정한다 — 문 선분의 중점이 전체 외곽선(경계 상자)에 붙어 있으면.
    canvas_w, canvas_h = span_x * scale, span_y * scale
    tol = max(canvas_w, canvas_h) * 0.005
    symbol_pts = [(s.x, s.y) for s in symbols if s.kind in ("entrance", "exit")]
    for op in openings:
        mx, my = (op.x1 + op.x2) / 2, (op.y1 + op.y2) / 2
        on_edge = min(mx, canvas_w - mx, my, canvas_h - my) <= tol
        if not on_edge:
            continue
        near_symbol = any(abs(mx - sx) <= tol * 4 and abs(my - sy) <= tol * 4 for sx, sy in symbol_pts)
        if not near_symbol:
            symbols.append(PlanSymbol("entrance", "(outer-wall door)", mx, my))

    used_wall = {l.name for l in layers if l.guess == "wall"}
    used_opening = {l.name for l in layers if l.guess == "opening"}
    wall_length = sum(l.total_length for l in layers if l.name in used_wall) or 1.0
    suspects = [
        l.name
        for l in layers
        if l.name not in used_wall
        and l.name not in used_opening
        and l.segment_count
        and not _is_noise_layer(l.name)
        and l.total_length >= wall_length * SUSPECT_WALL_RATIO
    ]

    return ExtractionResult(
        canvas_width=canvas_w,
        canvas_height=canvas_h,
        lines=normalize(lines),
        source="dxf",
        openings=openings,
        auto_notes=auto_notes,
        meters_per_unit=(unit[1] / scale) if unit else None,
        dxf_units=unit[0] if unit else None,
        labels=labels,
        symbols=symbols,
        layers=layers,
        xrefs=xrefs,
        layers_guessed=layers_guessed,
        suspect_wall_layers=suspects,
    )
