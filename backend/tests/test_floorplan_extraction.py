import io
from pathlib import Path

import ezdxf
import pytest
from PIL import Image, ImageDraw

from app.services.floorplan_extraction import (
    FloorplanExtractionError,
    extract_from_dxf,
    extract_from_image,
)


def _synthetic_dxf_bytes(wall_layer: str | None) -> bytes:
    doc = ezdxf.new()
    msp = doc.modelspace()
    layer = wall_layer or "0"
    if wall_layer:
        doc.layers.add(name=wall_layer)
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": layer})
    msp.add_line((100, 0), (100, 100), dxfattribs={"layer": layer})
    msp.add_line((100, 100), (0, 100), dxfattribs={"layer": layer})
    msp.add_line((0, 100), (0, 0), dxfattribs={"layer": layer})
    buf = io.StringIO()
    doc.write(buf, fmt="asc")
    return buf.getvalue().encode("utf-8")


def _synthetic_floorplan_image_bytes() -> bytes:
    image = Image.new("L", (300, 200), color=255)
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 280, 180], outline=0, width=4)
    draw.line([(150, 20), (150, 180)], fill=0, width=4)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_extract_from_dxf_finds_wall_layer_lines():
    result = extract_from_dxf(_synthetic_dxf_bytes("WALL"))
    assert result.source == "dxf"
    assert len(result.lines) == 4
    assert result.canvas_width > 0
    assert result.canvas_height > 0


def test_extract_from_dxf_falls_back_to_all_lines_without_wall_layer():
    result = extract_from_dxf(_synthetic_dxf_bytes(None))
    assert len(result.lines) == 4


def _aia_dxf_bytes() -> bytes:
    """Mimics real CAD output: AIA layer names, continuous walls, doors separate."""
    doc = ezdxf.new()
    for layer in ("A-WALL", "A-DOOR", "A-GLAZ", "A-FURN"):
        doc.layers.add(name=layer)
    msp = doc.modelspace()
    # Walls run unbroken through the door positions, as real drawings do.
    msp.add_line((0, 0), (12, 0), dxfattribs={"layer": "A-WALL"})
    msp.add_line((0, 9), (12, 9), dxfattribs={"layer": "A-WALL"})
    msp.add_line((0, 0), (0, 9), dxfattribs={"layer": "A-WALL"})
    msp.add_line((12, 0), (12, 9), dxfattribs={"layer": "A-WALL"})
    msp.add_line((5, 0), (5, 9), dxfattribs={"layer": "A-WALL"})
    msp.add_line((5, 4.5), (5, 5.5), dxfattribs={"layer": "A-DOOR"})
    msp.add_line((0, 2), (0, 3), dxfattribs={"layer": "A-GLAZ"})
    msp.add_lwpolyline([(1, 1), (2, 1), (2, 2)], dxfattribs={"layer": "A-FURN"})
    buf = io.StringIO()
    doc.write(buf, fmt="asc")
    return buf.getvalue().encode("utf-8")


def test_dxf_separates_door_and_window_layers_from_walls():
    """Doors/windows must not be treated as walls — they mark openings to carve.
    Without this every room reads as sealed (measured on a real DXF: 36% coverage)."""
    result = extract_from_dxf(_aia_dxf_bytes())

    assert len(result.lines) == 5  # A-WALL only; A-FURN excluded by the wall filter
    assert len(result.openings) == 2  # A-DOOR + A-GLAZ


def test_dxf_recognises_abbreviated_door_and_window_layers():
    """실무 도면은 `A-DR-FUL`(문)·`A-WX-SIL`(창)처럼 두 글자로 줄여 쓴다.

    `door`·`window`만 찾던 때는 `PLAN-1ST FLOOR.dwg`에서 **문이 0개**로 읽혀
    방이 44조각으로 끊기고 "문 자리를 찍어 주세요"가 떴다 — 문은 도면에 있었고
    우리가 이름을 못 알아본 것이다(2026-10-01).
    """
    from app.services.floorplan_extraction import _layer_kind

    assert _layer_kind("A-DR-FUL") == "opening"
    assert _layer_kind("A-DR-REV") == "opening"
    assert _layer_kind("A-WX-SIL") == "opening"
    assert _layer_kind("A-EX-000-001-0$0$A-DR-FUL") == "opening"
    # 유리벽은 개구부가 아니라 벽이다 — `gls`를 개구부 토큰에 넣지 않은 이유.
    assert _layer_kind("A-WL-GLS") == "wall"
    # 길어지면 안 걸린다 — `drain`·`drive`가 문이 되면 안 된다.
    assert _layer_kind("A-DRAIN") != "opening"


def test_dxf_without_wall_layer_reports_no_openings():
    """With no wall layer we fall back to every line, so nothing can be
    classified as an opening — guessing there would punch holes at random."""
    result = extract_from_dxf(_synthetic_dxf_bytes(None))
    assert result.openings == []


def test_extract_from_dxf_invalid_bytes_raises():
    with pytest.raises(FloorplanExtractionError):
        extract_from_dxf(b"not a dxf file")


def test_unreadable_dxf_message_is_korean_without_parser_text():
    with pytest.raises(FloorplanExtractionError) as info:
        extract_from_dxf(b"hello not a dxf")
    msg = str(info.value)
    assert "DXF 형식이 아닙니다" in msg
    assert "Invalid" not in msg and "hello" not in msg


def test_dxf_without_any_lines_says_what_to_do_in_plain_korean():
    """빈 DXF — 「선 엔티티」 같은 캐드 용어 대신 할 일을 말한다 (2026-11-07 UX 자동 개선)."""
    stream = io.StringIO()
    ezdxf.new().write(stream)
    with pytest.raises(FloorplanExtractionError) as info:
        extract_from_dxf(stream.getvalue().encode())
    msg = str(info.value)
    assert "엔티티" not in msg
    assert "선을 하나도 찾지 못했습니다" in msg and "다시 저장해 주세요" in msg


def test_extract_from_image_detects_some_lines():
    result = extract_from_image(_synthetic_floorplan_image_bytes())
    assert result.source == "image"
    assert result.canvas_width > 0 and result.canvas_height > 0
    assert len(result.lines) > 0


def test_extract_from_image_invalid_bytes_raises():
    with pytest.raises(FloorplanExtractionError):
        extract_from_image(b"not an image")


# ── 의미 요소 (2026-09-18) ────────────────────────────────────────────────

LABELED = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "synthetic-mall-labeled.dxf"
PLAIN = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "synthetic-large-mall.dxf"


def test_reads_room_names_and_symbols_from_labeled_sample():
    result = extract_from_dxf(LABELED.read_bytes())
    texts = {label.text for label in result.labels}
    assert {"앵커매장 A", "앵커매장 B", "중앙통로", "매장 01"} <= texts
    kinds = sorted(s.kind for s in result.symbols)
    assert kinds == ["elevator", "entrance", "entrance", "exit", "stair", "stair"]
    # 중앙통로 라벨은 통로 한가운데(y = 60/120 → 캔버스 세로 절반)에 있어야 한다.
    corridor = next(l for l in result.labels if l.text == "중앙통로")
    assert abs(corridor.y - result.canvas_height / 2) < 5


def test_outer_wall_doors_become_entrances_without_symbols():
    """실제 도면은 출입구 심볼을 잘 안 넣는다 — 바깥벽 위의 문은 기하로 출입구가 된다."""
    result = extract_from_dxf(PLAIN.read_bytes())
    assert result.labels == []
    entrances = [s for s in result.symbols if s.kind == "entrance"]
    assert len(entrances) == 2
    ys = sorted(round(s.y) for s in entrances)
    assert ys == [0, round(result.canvas_height)]


def test_symbol_kind_needles():
    from app.services.floorplan_extraction import _symbol_kind

    assert _symbol_kind("A-STAIR-01") == "stair"
    assert _symbol_kind("계단_블록") == "stair"
    assert _symbol_kind("EV_CAR") == "elevator"
    assert _symbol_kind("EMERGENCY_EXIT") == "exit"
    assert _symbol_kind("MAIN_ENTRANCE") == "entrance"
    assert _symbol_kind("CHAIR") == "other"


# ── 레이어 목록·선택 (2026-09-30) ─────────────────────────────────────────

def _relayer(source: Path, mapping: dict[str, str]) -> bytes:
    """샘플 도면의 레이어 이름만 바꿔 임시 DXF 바이트를 만든다."""
    import tempfile

    doc = ezdxf.readfile(str(source))
    for entity in doc.modelspace():
        try:
            entity.dxf.layer = mapping.get(entity.dxf.layer, entity.dxf.layer)
        except Exception:
            pass
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
    tmp.close()
    doc.saveas(tmp.name)
    data = Path(tmp.name).read_bytes()
    Path(tmp.name).unlink(missing_ok=True)
    return data


COMPLEX = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "complex-mall.dxf"


def test_layer_inventory_reports_every_layer_with_stats():
    result = extract_from_dxf(COMPLEX.read_bytes())
    names = {layer.name for layer in result.layers}
    assert {"WALL", "DOOR", "ROOM_NAME", "SYMBOL"} <= names
    wall = next(l for l in result.layers if l.name == "WALL")
    door = next(l for l in result.layers if l.name == "DOOR")
    assert wall.guess == "wall" and wall.guessed_by == "name"
    assert door.guess == "opening"
    # 벽은 길고 문은 짧다 — 이름을 못 믿을 때 쓰는 단서가 실제로 갈리는지.
    assert wall.median_length > door.median_length * 3
    assert not result.layers_guessed


def test_unknown_layer_names_guess_the_wall_and_keep_the_doors():
    """실무 도면은 `구조체`·`칸막이문` 같은 이름을 쓴다. 예전에는 이럴 때 모든
    레이어를 벽으로 합쳐 문이 통째로 사라졌다 (반영률 100% → 31%)."""
    data = _relayer(COMPLEX, {"WALL": "구조체", "DOOR": "칸막이문"})
    result = extract_from_dxf(data)
    assert result.layers_guessed, "짐작했으면 화면이 확인을 요청해야 한다"
    assert len(result.lines) == 102
    assert len(result.openings) == 25, "문 레이어를 이름으로 찾았으면 살려야 한다"


def test_user_chosen_layers_beat_the_name_rules():
    data = _relayer(COMPLEX, {"WALL": "구조체", "DOOR": "칸막이문"})
    result = extract_from_dxf(data, wall_layers=["구조체"], opening_layers=["칸막이문"])
    assert len(result.lines) == 102 and len(result.openings) == 25
    assert not result.layers_guessed, "사람이 고른 뒤에는 짐작이라고 하면 안 된다"

    # 문을 쓰지 않기로 하면 그대로 반영된다.
    only_walls = extract_from_dxf(data, wall_layers=["구조체"], opening_layers=[])
    assert len(only_walls.openings) == 0


def test_choosing_an_empty_layer_fails_loudly():
    data = _relayer(COMPLEX, {"WALL": "구조체"})
    with pytest.raises(FloorplanExtractionError, match="선이 없습니다"):
        extract_from_dxf(data, wall_layers=["ROOM_NAME"], opening_layers=[])


def test_dimension_layers_are_not_mistaken_for_walls():
    """치수 레이어만 남으면 벽이 없는 게 맞다. 그래도 **실패로 끝내지 않고**
    레이어 목록을 돌려줘야 사람이 '이게 벽이다'라고 고칠 수 있다."""
    data = _relayer(COMPLEX, {"WALL": "A-DIM-치수"})
    result = extract_from_dxf(data)
    dim = next(l for l in result.layers if l.name == "A-DIM-치수")
    assert dim.guess != "wall", "치수 레이어를 벽으로 짚으면 도면이 통째로 어긋난다"
    assert result.lines == []
    assert result.layers_guessed
    assert result.canvas_width > 0, "도면 범위는 잡혀 있어야 화면에 그릴 수 있다"

    # 사람이 그 레이어를 벽이라고 지정하면 그대로 쓴다.
    fixed = extract_from_dxf(data, wall_layers=["A-DIM-치수"], opening_layers=["DOOR"])
    assert len(fixed.lines) == 102 and len(fixed.openings) == 25


# ── 곡선 엔티티 (2026-09-30) ──────────────────────────────────────────────

def _dxf_bytes(build) -> bytes:
    import tempfile

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    doc.layers.add("WALL")
    build(doc.modelspace())
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
    tmp.close()
    doc.saveas(tmp.name)
    data = Path(tmp.name).read_bytes()
    Path(tmp.name).unlink(missing_ok=True)
    return data


def test_arcs_become_wall_segments():
    """실제 도면은 둥근 벽·문 심볼을 호로 그린다. 호를 버리면 그 자리가 뚫린
    채로 분석돼 바깥 공기가 새어 들어온다."""
    def build(msp):
        msp.add_line((0, 0), (20, 0), dxfattribs={"layer": "WALL"})
        msp.add_arc(center=(20, 10), radius=10, start_angle=-90, end_angle=90,
                    dxfattribs={"layer": "WALL"})

    result = extract_from_dxf(_dxf_bytes(build))
    # 직선 1 + 호를 편 여러 조각
    assert len(result.lines) > 4, "호가 통째로 버려졌다"


def test_circle_closes_on_itself():
    def build(msp):
        msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "WALL"})
        msp.add_circle(center=(50, 50), radius=5, dxfattribs={"layer": "WALL"})

    result = extract_from_dxf(_dxf_bytes(build))
    assert len(result.lines) > 5


def test_curved_polyline_bulge_is_flattened_not_straightened():
    """bulge는 '이 변을 호로 그려라'다. 무시하면 곡선 벽이 직선이 되어 실제와
    다른 자리를 막거나 연다."""
    def build(msp):
        msp.add_lwpolyline(
            [(0, 0, 0, 0, 1.0), (10, 0, 0, 0, 0)], format="xyseb",
            dxfattribs={"layer": "WALL"},
        )

    result = extract_from_dxf(_dxf_bytes(build))
    assert len(result.lines) > 1, "bulge를 무시하고 직선 하나로 폈다"
    # 반원이므로 시작·끝을 잇는 직선에서 반지름만큼 벗어난 점이 있어야 한다.
    ys = [v for line in result.lines for v in (line.y1, line.y2)]
    assert max(ys) - min(ys) > result.canvas_height * 0.3


def test_straight_only_plan_is_unchanged():
    """합성 몰은 전부 직선이다 — 곡선 지원이 기존 결과를 흔들면 안 된다."""
    result = extract_from_dxf(COMPLEX.read_bytes())
    assert len(result.lines) == 102
    assert len(result.openings) == 25


# ── 블록 안에 든 도형 (2026-09-30) ────────────────────────────────────────

def _block_plan(nested: bool = False) -> bytes:
    """평면 전체를 블록 하나로 묶어 꽂은 도면 — 사무소에서 흔한 형태."""
    import tempfile

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    doc.layers.add("WALL")
    doc.layers.add("DOOR")
    block = doc.blocks.new(name="PLAN")
    for a, b in (((0, 0), (20, 0)), ((20, 0), (20, 20)), ((20, 20), (0, 20)), ((0, 20), (0, 0))):
        block.add_line(a, b, dxfattribs={"layer": "WALL"})
    block.add_line((8, 0), (12, 0), dxfattribs={"layer": "DOOR"})
    if nested:
        inner = doc.blocks.new(name="INNER")
        inner.add_line((5, 5), (15, 5), dxfattribs={"layer": "WALL"})
        block.add_blockref("INNER", (0, 0))
    doc.modelspace().add_blockref("PLAN", (0, 0))
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
    tmp.close()
    doc.saveas(tmp.name)
    data = Path(tmp.name).read_bytes()
    Path(tmp.name).unlink(missing_ok=True)
    return data


def test_walls_inside_a_block_are_found():
    """모델스페이스에 INSERT 하나뿐이면 예전엔 '선을 찾지 못했습니다'로 끝났다 —
    화면에는 도면이 멀쩡히 보이는데."""
    result = extract_from_dxf(_block_plan())
    assert len(result.lines) == 4
    assert len(result.openings) == 1, "블록 안의 문 레이어도 살아야 한다"


def test_nested_blocks_are_walked():
    result = extract_from_dxf(_block_plan(nested=True))
    assert len(result.lines) == 5


def test_block_contents_keep_their_own_layers():
    result = extract_from_dxf(_block_plan())
    names = {layer.name for layer in result.layers}
    assert "WALL" in names and "DOOR" in names
    wall = next(l for l in result.layers if l.name == "WALL")
    assert wall.segment_count == 4 and wall.guess == "wall"


def test_symbol_blocks_are_not_exploded_into_walls():
    """계단·EV 블록은 표식이지 벽이 아니다. 펼치면 그 그림의 선들이 벽 후보가
    되어 방을 막아 버린다 (2026-09-30에 실제로 그랬다)."""
    import tempfile

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    doc.layers.add("WALL")
    doc.layers.add("SYMBOL")
    stair = doc.blocks.new(name="STAIR")
    for i in range(6):  # 계단 디딤판 — 펼치면 선 6개가 생긴다
        stair.add_line((0, i), (4, i))
    msp = doc.modelspace()
    for a, b in (((0, 0), (20, 0)), ((20, 0), (20, 20)), ((20, 20), (0, 20)), ((0, 20), (0, 0))):
        msp.add_line(a, b, dxfattribs={"layer": "WALL"})
    msp.add_blockref("STAIR", (8, 8), dxfattribs={"layer": "SYMBOL"})
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
    tmp.close()
    doc.saveas(tmp.name)
    data = Path(tmp.name).read_bytes()
    Path(tmp.name).unlink(missing_ok=True)

    result = extract_from_dxf(data)
    assert len(result.lines) == 4, "계단 그림이 벽으로 섞였다"
    assert any(s.kind == "stair" for s in result.symbols), "계단은 심볼로는 남아야 한다"


def test_partition_layer_is_flagged_not_silently_dropped():
    """`외벽`은 이름 규칙에 걸리고 `칸막이`는 안 걸린다. 그러면 내벽이 통째로
    빠져 방이 나뉘지 않는데, 예전엔 화면에 아무 표시도 없었다."""
    import tempfile

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    for name in ("외벽", "칸막이", "DOOR"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for a, b in (((0, 0), (40, 0)), ((40, 0), (40, 20)), ((40, 20), (0, 20)), ((0, 20), (0, 0))):
        msp.add_line(a, b, dxfattribs={"layer": "외벽"})
    for x in (10, 20, 30):
        msp.add_line((x, 0), (x, 20), dxfattribs={"layer": "칸막이"})
    msp.add_line((5, 0), (7, 0), dxfattribs={"layer": "DOOR"})
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
    tmp.close()
    doc.saveas(tmp.name)
    data = Path(tmp.name).read_bytes()
    Path(tmp.name).unlink(missing_ok=True)

    result = extract_from_dxf(data)
    assert result.suspect_wall_layers == ["칸막이"]
    assert len(result.lines) == 4  # 아직은 외벽만

    fixed = extract_from_dxf(data, wall_layers=["외벽", "칸막이"], opening_layers=["DOOR"])
    assert len(fixed.lines) == 7
    assert fixed.suspect_wall_layers == [], "고른 뒤에는 의심할 게 없다"


def test_used_layers_are_not_listed_as_suspects():
    result = extract_from_dxf(COMPLEX.read_bytes())
    assert "WALL" not in result.suspect_wall_layers
    assert "DOOR" not in result.suspect_wall_layers


def _plan_with(extra) -> bytes:
    """사각 외벽 4줄 + 추가 엔티티 하나."""
    import tempfile

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 6
    doc.layers.add("WALL")
    msp = doc.modelspace()
    for a, b in (((0, 0), (30, 0)), ((30, 0), (30, 20)), ((30, 20), (0, 20)), ((0, 20), (0, 0))):
        msp.add_line(a, b, dxfattribs={"layer": "WALL"})
    extra(msp)
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
    tmp.close()
    doc.saveas(tmp.name)
    data = Path(tmp.name).read_bytes()
    Path(tmp.name).unlink(missing_ok=True)
    return data


def test_mline_walls_are_read():
    """다중선은 벽을 두 줄로 한 번에 긋는 기능이다. 어떤 캐드는 벽을 전부
    이걸로 그리는데, 버리면 내벽이 통째로 사라진다."""
    data = _plan_with(lambda msp: msp.add_mline([(5, 5), (25, 5)], dxfattribs={"layer": "WALL"}))
    assert len(extract_from_dxf(data).lines) > 4


def test_spline_and_ellipse_are_flattened():
    spline = _plan_with(
        lambda msp: msp.add_spline([(5, 5), (15, 12), (25, 5)], dxfattribs={"layer": "WALL"})
    )
    assert len(extract_from_dxf(spline).lines) > 6

    ellipse = _plan_with(
        lambda msp: msp.add_ellipse((15, 10), major_axis=(8, 0), ratio=0.5, dxfattribs={"layer": "WALL"})
    )
    assert len(extract_from_dxf(ellipse).lines) > 6


SHOWCASE = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "showcase-mall.dxf"


def test_island_blocks_inside_one_mall_are_not_counted_as_other_drawings():
    """통로가 넓으면 가운데 매장 블록이 떨어진 덩어리로 보인다. 그걸 다른 도면으로
    세면 몰 하나에 「한 장에 도면이 3개」라고 말했다(2026-10-22)."""
    result = extract_from_dxf(SHOWCASE.read_bytes())
    assert not any("도면이" in note and "개 있어" in note for note in result.auto_notes)
    assert len(result.lines) > 500
