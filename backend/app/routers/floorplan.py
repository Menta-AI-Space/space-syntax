"""Floor-plan space-syntax analysis (실내 VGA) — stateless, no DB.

Two-step flow mirrors simulate.py's context/simulate split: extract first
(cheap, returns editable geometry so the frontend can render a wall editor),
then analyze once the user has corrected the walls in the browser.
"""

import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.models.schemas import (
    ExplanationOut,
    FloorplanAnalyzeRequest,
    FloorplanAnalyzeResponse,
    FloorplanCellOut,
    FloorplanChatRequest,
    FloorplanChatResponse,
    FloorplanCompareRequest,
    FloorplanCompareResponse,
    FloorplanDeltaOut,
    FloorplanEditIntentRequest,
    FloorplanEditIntentResponse,
    FloorplanEditStepOut,
    FloorplanExcludedCellOut,
    FloorplanExplainRequest,
    FloorplanExtractResponse,
    FloorplanIssueOut,
    FloorplanLabelOut,
    FloorplanLayerOut,
    FloorplanLineIn,
    FloorplanRoomOut,
    FloorplanSymbolOut,
    FloorplanXrefOut,
    FloorplanSpotOut,
    FloorplanSummaryOut,
)
from app.services.floorplan_extraction import (
    FloorplanExtractionError,
    extract_from_dxf,
)
from app.services.floorplan_compare import compare_plans, summarize_comparison
from app.services.floorplan_issues import detect_issues
from app.services.floorplan_edit_intent import plan_edit
from app.services.floorplan_chat import answer_floorplan_question
from app.services.floorplan_narrative import explain_floorplan
from app.services.floorplan_rooms import detect_rooms
from app.services.floorplan_vga import (
    MAX_GRID_CELLS,
    ROBOT_AISLE_MIN_M,
    FloorplanVGAError,
    WallSegment,
    compute_floorplan_vga,
    rule_based_floorplan_explanation,
)

router = APIRouter(prefix="/api/floorplan", tags=["floorplan"])
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def _spots_out(items):
    return [
        FloorplanSpotOut(x=s.x, y=s.y, value=s.value, percentile=s.percentile, x_m=s.x_m, y_m=s.y_m)
        for s in items
    ]


def _summary_out(result) -> FloorplanSummaryOut:
    return FloorplanSummaryOut(
        cols=result.cols,
        rows=result.rows,
        cell_size=result.cell_size,
        cell_count=len(result.cells),
        integration_mean=result.integration_mean,
        integration_max=result.integration_max,
        connectivity_mean=result.connectivity_mean,
        coverage_pct=result.coverage_pct,
        integration_local_mean=result.integration_local_mean,
        mean_depth_mean=result.mean_depth_mean,
        choice_max=result.choice_max,
        choice_sampled=result.choice_sampled,
        cell_size_m=result.cell_size_m,
        best_spots=_spots_out(result.best_spots),
        through_spots=_spots_out(result.through_spots),
        dead_spots=_spots_out(result.dead_spots),
        robot_passable_pct=result.robot_passable_pct,
        robot_aisle_min_m=ROBOT_AISLE_MIN_M,
        robot_dead_ends=_spots_out(result.robot_dead_ends),
    )


def _issue_out(issue) -> FloorplanIssueOut:
    return FloorplanIssueOut(
        severity=issue.severity, title=issue.title, detail=issue.detail, x=issue.x, y=issue.y,
        x_m=issue.x_m, y_m=issue.y_m, cell_count=issue.cell_count, area_m2=issue.area_m2,
        evidence=issue.evidence, cells=[list(c) for c in issue.cells], kind=issue.kind,
    )


def _segments(lines) -> list[WallSegment]:
    return [WallSegment(line.x1, line.y1, line.x2, line.y2) for line in lines]


@router.post("/extract", response_model=FloorplanExtractResponse)
async def extract(
    file: UploadFile = File(...),
    wall_layers: str = Form(""),
    opening_layers: str = Form(""),
):
    """``wall_layers``/``opening_layers``는 쉼표로 구분한 레이어 이름이다.

    비워 두면 자동으로 짐작하고, 사람이 레이어를 고쳐 다시 올리면 그 선택이
    이름 규칙을 이긴다. 파일을 서버에 두지 않기 때문에 다시 올리는 방식이다 —
    도면은 남의 건물 설계도라 보관하지 않는 편이 낫다.
    """
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="파일이 너무 큽니다 (최대 15MB)")
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다")

    filename = (file.filename or "").lower()
    is_dxf = filename.endswith(".dxf") or file.content_type == "application/dxf"
    if not is_dxf:
        raise HTTPException(
            status_code=400,
            detail="DXF 도면만 지원합니다. 사진·스캔 이미지는 벽 인식 정확도가 낮아 받지 않습니다.",
        )

    def split(value: str) -> list[str]:
        return [part.strip() for part in value.split(",") if part.strip()]

    try:
        result = await run_in_threadpool(
            extract_from_dxf, data, split(wall_layers), split(opening_layers)
        )
    except FloorplanExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    warnings: list[str] = []
    if not result.lines:
        warnings.append("벽을 자동으로 찾지 못했습니다 — 직접 선을 그려주세요.")

    # 문·창·실명을 읽은 것은 경고가 아니라 「이렇게 읽었습니다」다 — warnings에 넣으면
    # 화면이 주의 색(주황) 목록으로 그려 문제가 있는 것처럼 읽혔다 (2026-11-08).
    notes: list[str] = list(result.auto_notes)
    if result.openings:
        notes.append(
            f"문·창 {len(result.openings)}곳을 찾아 벽에서 개구부로 처리합니다."
        )
    if result.labels or result.symbols:
        counts = {}
        for sym in result.symbols:
            counts[sym.kind] = counts.get(sym.kind, 0) + 1
        ko = {"stair": "계단", "elevator": "엘리베이터", "entrance": "출입구", "exit": "비상구", "other": "기타 심볼"}
        parts = [f"실명 {len(result.labels)}개"] if result.labels else []
        parts += [f"{ko[k]} {n}개" for k, n in counts.items()]
        notes.append("도면에서 " + " · ".join(parts) + "를 읽었습니다.")
    if result.xrefs:
        warnings.append(
            f"이 도면은 외부 참조(XREF) {len(result.xrefs)}개를 씁니다 — 그 안의 벽은 이 파일에 없습니다. "
            "빠진 벽이 있으면 캐드에서 XREF를 BIND한 뒤 다시 저장해 주세요."
        )
    if result.suspect_wall_layers:
        names = " · ".join(result.suspect_wall_layers[:4])
        warnings.append(
            f"벽으로 쓰지 않은 레이어에도 선이 많습니다: {names}. 내벽·칸막이가 거기 있을 수 있으니 "
            "레이어 목록에서 확인해 주세요 — 빠뜨리면 방이 나뉘지 않습니다."
        )
    if result.layers_guessed:
        warnings.append(
            "레이어 이름으로 벽을 찾지 못해 선이 가장 많은 레이어를 벽으로 짐작했습니다 — "
            "아래 레이어 목록에서 확인해 주세요."
        )
    if result.meters_per_unit is None:
        warnings.append(
            "도면에 길이 단위가 없어 실제 거리(m)를 낼 수 없습니다 — 축척을 직접 입력하면 m 단위로 표시합니다."
        )

    return FloorplanExtractResponse(
        canvas_width=result.canvas_width,
        canvas_height=result.canvas_height,
        lines=[
            FloorplanLineIn(x1=line.x1, y1=line.y1, x2=line.x2, y2=line.y2)
            for line in result.lines
        ],
        source=result.source,
        openings=[
            FloorplanLineIn(x1=line.x1, y1=line.y1, x2=line.x2, y2=line.y2)
            for line in result.openings
        ],
        meters_per_unit=result.meters_per_unit,
        dxf_units=result.dxf_units,
        layers=[
            FloorplanLayerOut(
                name=l.name, entity_count=l.entity_count, segment_count=l.segment_count,
                total_length=l.total_length, median_length=l.median_length,
                text_count=l.text_count, block_count=l.block_count,
                guess=l.guess, guessed_by=l.guessed_by,
            )
            for l in result.layers
        ],
        xrefs=[FloorplanXrefOut(name=x.name, path=x.path) for x in result.xrefs],
        layers_guessed=result.layers_guessed,
        suspect_wall_layers=result.suspect_wall_layers,
        auto_notes=notes,
        labels=[FloorplanLabelOut(text=l.text, x=l.x, y=l.y) for l in result.labels],
        symbols=[FloorplanSymbolOut(kind=s.kind, name=s.name, x=s.x, y=s.y) for s in result.symbols],
        warnings=warnings,
    )


@router.post("/analyze", response_model=FloorplanAnalyzeResponse)
async def analyze(request: FloorplanAnalyzeRequest):
    walls = [WallSegment(line.x1, line.y1, line.x2, line.y2) for line in request.lines]
    openings = [
        WallSegment(line.x1, line.y1, line.x2, line.y2) for line in request.openings
    ]
    try:
        result = await run_in_threadpool(
            compute_floorplan_vga,
            walls,
            request.canvas_width,
            request.canvas_height,
            MAX_GRID_CELLS,
            openings,
            request.meters_per_unit,
        )
    except FloorplanVGAError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    label_points = [(l.text, l.x, l.y) for l in request.labels]
    issues = detect_issues(result, label_points)
    explanation = rule_based_floorplan_explanation(result, issues)
    rooms = (
        detect_rooms(
            blocked_no_openings=result.blocked_no_openings,
            exterior=result.exterior_mask,
            analyzed_mask=result.analyzed_mask,
            cell_size=result.cell_size,
            cell_size_m=result.cell_size_m,
            integration=result.integration_grid,
            choice=result.choice_grid,
            connectivity=result.connectivity_grid,
            labels_xy=label_points,
        )
        if result.blocked_no_openings is not None
        else []
    )

    return FloorplanAnalyzeResponse(
        summary=_summary_out(result),
        issues=[_issue_out(i) for i in issues],
        rooms=[
            FloorplanRoomOut(
                index=r.index, name=r.name, cell_count=r.cell_count, area_m2=r.area_m2,
                x=r.x, y=r.y, x_m=r.x_m, y_m=r.y_m,
                integration_mean=r.integration_mean, choice_mean=r.choice_mean,
                connectivity_mean=r.connectivity_mean,
                integration_percentile=r.integration_percentile, analyzed_pct=r.analyzed_pct,
            )
            for r in rooms
        ],
        cells=[
            FloorplanCellOut(
                row=cell.row,
                col=cell.col,
                x=cell.x,
                y=cell.y,
                integration=cell.integration,
                connectivity=cell.connectivity,
                integration_local=cell.integration_local,
                mean_depth=cell.mean_depth,
                choice=cell.choice,
                aisle_width_m=cell.aisle_width if result.cell_size_m else None,
            )
            for cell in result.cells
        ],
        excluded_cells=[
            FloorplanExcludedCellOut(row=cell.row, col=cell.col)
            for cell in result.excluded_cells
        ],
        explanation=ExplanationOut(text=explanation, source="rule"),
        warnings=result.warnings,
        notes=result.notes,
    )


@router.post("/explain", response_model=ExplanationOut)
async def explain(request: FloorplanExplainRequest):
    """LLM 해석 — 분석 뒤에 따로 부른다. LLM이 꺼져 있거나 실패하면 규칙 문장을
    그대로 돌려주므로 프론트는 source로 구분한다."""
    text, source = await explain_floorplan(request.summary, request.issues, request.rule_text, lang=request.lang)
    return ExplanationOut(text=text, source=source)


@router.post("/edit-intent", response_model=FloorplanEditIntentResponse)
async def edit_intent(request: FloorplanEditIntentRequest):
    """말로 고치기 — 고른 자리 + 한 문장 지시를 벽·문 편집으로 바꾼다 (2026-11-09).

    GPT는 주변 벽·문 번호 중에서 명령만 고르고 선 계산은 서버가 한다. GPT가 꺼져
    있거나 실패하면 낱말 규칙으로 만든다(source="rule"). 분석은 돌리지 않는다 —
    사용자가 미리보기를 보고 적용한 뒤 「바꾼 것과 비교하기」로 잰다.
    """
    seg = lambda l: (l.x1, l.y1, l.x2, l.y2)  # noqa: E731
    plan = await plan_edit(
        instruction=request.instruction,
        point=(request.point.x, request.point.y),
        box=seg(request.box) if request.box else None,
        lines=[seg(l) for l in request.lines],
        openings=[seg(l) for l in request.openings],
        labels=[(l.text, l.x, l.y) for l in request.labels],
        meters_per_unit=request.meters_per_unit,
        canvas_width=request.canvas_width,
        canvas_height=request.canvas_height,
        previous_instruction=request.previous_instruction,
        lang=request.lang,
    )
    line = lambda s: FloorplanLineIn(x1=s[0], y1=s[1], x2=s[2], y2=s[3])  # noqa: E731
    return FloorplanEditIntentResponse(
        steps=[FloorplanEditStepOut(op=op, text=text) for op, text in plan.steps],
        add_lines=[line(s) for s in plan.add_lines],
        remove_line_indices=plan.remove_lines,
        add_openings=[line(s) for s in plan.add_openings],
        remove_opening_indices=plan.remove_openings,
        message=plan.message,
        question=plan.question,
        source=plan.source,  # type: ignore[arg-type]
        warnings=plan.warnings,
    )
@router.post("/chat", response_model=FloorplanChatResponse)
async def chat(request: FloorplanChatRequest):
    """도면 상담 — 분석 결과를 두고 일반인이 되묻는 말에 쉬운 말로 답한다(2026-11-09).
    `/explain`처럼 분석 뒤에 따로 부르므로 5초 룰 밖이다. LLM이 없으면 카드 목록에서
    고른 규칙 답(source:"rule")을 돌려준다. 답이 카드 하나를 가리키면 card에 번호."""
    if request.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="마지막 메시지는 사용자의 질문이어야 합니다.")
    text, card, source = await answer_floorplan_question(
        summary=request.summary,
        issues=request.issues,
        rooms=request.rooms,
        rule_text=request.rule_text,
        narrative=request.narrative,
        messages=[{"role": m.role, "content": m.content} for m in request.messages],
    )
    return FloorplanChatResponse(text=text, source=source, card=card)


@router.post("/compare", response_model=FloorplanCompareResponse)
async def compare(request: FloorplanCompareRequest):
    """A/B — 같은 도면의 원본과 고친 판을 각각 돌려 무엇이 좋아지고 무엇이
    나빠졌는지 낸다. 한 줄 점수로 합치지 않는다 (SPEC_FLOORPLAN_TABS_UI 결정 3).

    두 번 계산하므로 분석 하나의 두 배가 든다. 실측 1.1초 × 2 = 2.4초로 5초 룰
    안이지만, 여기에 LLM을 얹지 않는 이유이기도 하다.
    """
    label_points = [(l.text, l.x, l.y) for l in request.labels]

    async def run(lines, openings):
        try:
            result = await run_in_threadpool(
                compute_floorplan_vga,
                _segments(lines),
                request.canvas_width,
                request.canvas_height,
                MAX_GRID_CELLS,
                _segments(openings),
                request.meters_per_unit,
            )
        except FloorplanVGAError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return result, detect_issues(result, label_points)

    before, before_issues = await run(request.before_lines, request.before_openings)
    after, after_issues = await run(request.after_lines, request.after_openings)

    deltas, appeared, resolved = compare_plans(before, after, before_issues, after_issues)
    warnings: list[str] = []
    if before.cell_size != after.cell_size:
        # 벽을 크게 옮겨 도면 크기가 달라지면 격자도 달라져 지표를 직접 못 비교한다.
        warnings.append(
            "두 판의 격자 크기가 달라 수치를 직접 비교하기 어렵습니다 — 벽을 옮겨 도면 범위가 바뀐 경우입니다."
        )
    if before.choice_sampled or after.choice_sampled:
        warnings.append(
            "도면이 커서 통행량은 표본으로 계산했습니다. 작은 차이는 계산 오차일 수 있습니다."
        )

    return FloorplanCompareResponse(
        deltas=[
            FloorplanDeltaOut(
                key=d.key, label=d.label, before=d.before, after=d.after,
                change_pct=d.change_pct, direction=d.direction, unit=d.unit, note=d.note,
            )
            for d in deltas
        ],
        appeared=[_issue_out(i) for i in appeared],
        resolved=[_issue_out(i) for i in resolved],
        summary=summarize_comparison(deltas, appeared, resolved),
        before_summary=_summary_out(before),
        after_summary=_summary_out(after),
        warnings=warnings,
    )
