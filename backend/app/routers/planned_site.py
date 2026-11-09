"""계획 단계 택지의 획지 등급 — 아직 없는 길 위에서 분양가 배분 근거를 낸다.

`/api/parcel-structure`(A안)가 이미 있는 동네를 다루는 것과 달리, 여기는 도면
위에만 있는 도로망과 획지를 받아 그 자리에서 공간구문론을 돌린다. 등급·배분
규칙은 A안과 **같은 함수**를 쓴다 — 두 화면이 다른 점수를 내면 "계획일 때와
지어진 뒤"를 견줄 수 없다.
"""

import logging

from dataclasses import asdict
from functools import partial

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.models.schemas import (
    LotNearbyOut,
    LotNeighborOut,
    LotSideOut,
    PlannedLotOut,
    PlanScoreOut,
    ParcelStructureSummaryOut,
    PlannedSiteExtractResponse,
    PlannedSiteRequest,
    PlannedSiteResponse,
)
from app.services.planned_site import PlannedLot, analyze_planned_site_detail, metric_epsg_for
from app.services.planned_site_dxf import PlanExtractionError, extract_plan
from app.services.planned_site_pdf import pdf_to_plan_dxf
from app.services.simulation import SimulationError, VirtualRoad

router = APIRouter(prefix="/api", tags=["planned-site"])
logger = logging.getLogger(__name__)

# 기존 도로를 함께 볼 때의 반경. 지구 하나를 덮고도 주변 간선이 들어오는 크기다.
EXISTING_RADIUS_M = 1200
# 아파트를 세는 반경 — 블록에서 800m까지 보므로 기존 도로 반경 + 800
MODEL_RADIUS_M = EXISTING_RADIUS_M + 800
# 계획 도면은 지구 전체라 건축 평면도(15MB)보다 크다. 성남낙생 DXF가 2만 엔티티다.
MAX_PLAN_UPLOAD_BYTES = 30 * 1024 * 1024


@router.post("/planned-site/extract", response_model=PlannedSiteExtractResponse)
async def planned_site_extract(
    file: UploadFile = File(...),
    crs: str = Form(""),
    road_layers: str = Form(""),
    lot_layers: str = Form(""),
):
    """계획 도면 DXF → `{roads, lots}`. 결과를 그대로 `/api/planned-site`에 넣는다.

    ``crs``를 비우면 첫 후보로 놓고 후보 전부를 돌려준다 — 화면이 지도에 얹어
    사람에게 확인받는다(5174와 5186은 둘 다 한국 땅에 떨어진다). 사람이 고르면
    그 좌표계로 다시 올린다. 도면은 보관하지 않으므로 다시 올리는 방식이다.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다")
    if len(data) > MAX_PLAN_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="파일이 너무 큽니다 (최대 30MB)")
    name = (file.filename or "").lower()
    is_pdf = name.endswith(".pdf")
    if not (name.endswith(".dxf") or is_pdf):
        raise HTTPException(
            status_code=400,
            detail="DXF 또는 CAD에서 뽑은 PDF 도면만 받습니다. DWG는 CAD에서 「다른 이름으로 저장 → DXF」로 바꿔 주세요.",
        )

    def split(value: str) -> list[str]:
        return [part.strip() for part in value.split(",") if part.strip()]

    def run():
        # 벡터 PDF는 도곽 좌표 숫자로 위치를 맞춰 DXF로 바꾼 뒤 같은 길로 읽는다 (2026-11-09).
        pdf_notes: list[str] = []
        dxf = data
        if is_pdf:
            converted = pdf_to_plan_dxf(data)
            dxf, pdf_notes = converted.dxf, converted.notes
        out = extract_plan(dxf, crs.strip() or None, split(road_layers), split(lot_layers))
        out.notes = pdf_notes + out.notes
        return out

    try:
        result = await run_in_threadpool(run)
    except PlanExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PlannedSiteExtractResponse(
        roads=result.roads,
        lots=result.lots,
        layers=[asdict(layer) for layer in result.layers],
        crs=result.crs,
        crs_candidates=[asdict(c) for c in result.crs_candidates],
        centre_lat=result.centre_lat,
        centre_lng=result.centre_lng,
        notes=result.notes,
    )


@router.post("/planned-site", response_model=PlannedSiteResponse)
async def planned_site(request: PlannedSiteRequest):
    roads = [
        VirtualRoad(coordinates=r.coordinates, road_type=r.road_type) for r in request.roads
    ]
    lots = [PlannedLot(lot_id=l.lot_id, geometry=l.geometry) for l in request.lots]

    existing = None
    warnings_pre: list[str] = []
    apartments: list[tuple[float, float]] = []
    stations: list[tuple[float, float]] | None = None  # None = 역을 모름(조회 못 함) → 역 없이 배운 모델
    include_existing = request.include_existing_roads
    if include_existing and request.centre_lat is not None and request.centre_lng is not None:
        # 한국 밖 도면(두바이·미국 샘플, 2026-11-09): 기존 도로·아파트·역 데이터가 한국만 있어
        # 묻지 않고 도면 안 길만으로 계산한다. 경고 한 줄로 알린다.
        if metric_epsg_for(request.centre_lng, request.centre_lat) != "EPSG:5179":
            include_existing = False
            warnings_pre.append(
                "한국 밖 도면이라 주변 기존 도로·아파트·역 데이터 없이 도면 안 길만으로 계산했습니다 — "
                "값은 이 지구 안에서의 상대값입니다."
            )
    if include_existing:
        if request.centre_lat is None or request.centre_lng is None:
            raise HTTPException(
                status_code=400,
                detail="기존 도로를 함께 보려면 중심 좌표(centre_lat·centre_lng)가 필요합니다",
            )
        from app.services.spatial_query import fetch_roads_in_radius

        try:
            existing = await run_in_threadpool(
                fetch_roads_in_radius, request.centre_lng, request.centre_lat, EXISTING_RADIUS_M
            )
        except Exception as exc:
            logger.warning("기존 도로 조회 실패: %s", exc)
            existing = None
        # 계획도면 모델 요인 — 주변 아파트(도면 밖)와 역. 못 받아도 계산은 계속한다.
        from app.services.spatial_query import fetch_apartment_points
        from app.services.transit import find_subways_within

        try:
            apartments = await run_in_threadpool(
                fetch_apartment_points, request.centre_lng, request.centre_lat, MODEL_RADIUS_M
            )
        except Exception as exc:
            logger.warning("아파트 조회 실패: %s", exc)
        try:
            stations = [
                (s.lng, s.lat)
                for s in await find_subways_within(request.centre_lat, request.centre_lng, MODEL_RADIUS_M + 1500)
            ]
        except Exception as exc:
            logger.warning("역 조회 실패: %s", exc)
            warnings_pre.append("지하철역을 불러오지 못해 「가게가 들어설 가능성」을 역 거리 없이 계산했습니다.")

    road_scores: list[dict] = []
    try:
        graded, stats, warnings, detail = await run_in_threadpool(
            partial(
                analyze_planned_site_detail,
                roads,
                lots,
                existing,
                road_widths=[r.width_m for r in request.roads],
                apartments=apartments,
                stations=stations,
                road_scores_out=road_scores,
            )
        )
    except SimulationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    warnings.extend(warnings_pre)
    if include_existing and existing is None:
        # 계산 쪽이 이미 「주변 기존 도로 없이 계획 도로만으로…」를 넣었다 — 같은 말을 두 번
        # 보이지 않고, 그 문장 앞머리를 「불러오지 못해」로 바꿔 까닭을 한 줄에 말한다
        # (2026-11-06 UX 자동 개선: 경고 목록에 같은 뜻이 두 줄로 떴다).
        failed = "주변 기존 도로를 불러오지 못해 계획 도로만으로 계산했습니다"
        merged = False
        for i, w in enumerate(warnings):
            if w.startswith("주변 기존 도로 없이 계획 도로만으로 계산했습니다"):
                warnings[i] = failed + w[len("주변 기존 도로 없이 계획 도로만으로 계산했습니다"):]
                merged = True
                break
        if not merged:
            warnings.append(failed + ".")

    total = request.total_amount_manwon
    # 2026-10-26 사용자: 「쉽고 정확하게」 — 무엇을 보고 낸 값인지, 실제와 얼마나 맞았는지를 그대로 적는다.
    caveats = [
        "길 모양만 보고 낸 예상입니다. 실제로 사람 수를 센 유동인구가 아니고, 감정평가나 분양가도 아닙니다.",
        "색과 순위는 이 도면 안의 땅끼리 견준 것입니다. 다른 신도시 결과와 바로 견주면 안 되고, "
        "바로 옆 땅끼리 견줄 때 가장 믿을 만합니다.",
        # 2026-10-27: 숫자는 tools/validation/train_plan_model.py 채점(도시 하나 빼고 배우기, 물리 단위 GBM)과
        # evaluate_plan.py의 「지금 화면 공식」(r2000) — 바꾸면 여기도 고친다.
        "「가게가 들어설 가능성」은 이미 지어진 신도시 10곳의 실제 가게 자리에서 배운 예상입니다. 처음 보는 신도시에서 "
        "가게가 많이 들어선 블록을 맞게 고른 비율이 약 84%였습니다(아무렇게나 고르면 50%, 앞길 유동인구 색만으로는 약 68%). "
        "블록 넓이·둘레 길의 크기·주변 아파트 땅·역까지 거리를 함께 봅니다.",
        "역 거리는 지금 있는 지하철역 기준입니다 — 아직 없는 계획 역은 들어가지 않습니다.",
    ]

    return PlannedSiteResponse(
        roads=road_scores,
        summary=ParcelStructureSummaryOut(**stats),
        lots=[
            PlannedLotOut(
                pnu=g.pnu, area_sqm=g.area_sqm, lat=g.lat, lng=g.lng, geometry=g.geometry,
                structure=g.structure, integration=g.integration, choice=g.choice,
                road_distance_m=g.road_distance_m, frontage_count=g.frontage_count,
                percentile=g.percentile, grade=g.grade, allocation_share=g.allocation_share,
                allocation_manwon=(
                    round(total * g.allocation_share, 1)
                    if total and g.allocation_share is not None
                    else None
                ),
                sides=[LotSideOut(**asdict(s)) for s in detail[g.pnu].sides],
                neighbors=[LotNeighborOut(**asdict(n)) for n in detail[g.pnu].neighbors],
                best_side_score=detail[g.pnu].best_score,
                kind=detail[g.pnu].kind,
                nearby=[LotNearbyOut(**asdict(n)) for n in detail[g.pnu].nearby],
                plan=PlanScoreOut(**asdict(detail[g.pnu].plan)) if detail[g.pnu].plan else None,
            )
            for g in graded
        ],
        warnings=warnings,
        caveats=caveats,
    )
