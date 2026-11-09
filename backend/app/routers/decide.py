"""POST /api/decide — 후보지 순위 결정 + 적정 월세 산출.

응답의 모든 숫자·순위·판정은 app.services.decision(파이썬)이 결정론적으로
계산한다. LLM은 headline/comparison_note 서술만 담당한다.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services.decision import CandidateInput, compare_candidates
from app.services.decision_narrative import narrate_decision

router = APIRouter(prefix="/api", tags=["decide"])

NARRATIVE_TIMEOUT_S = 12.0


class CandidateIn(BaseModel):
    id: str = Field(..., min_length=1, max_length=32)
    label: str = Field("", max_length=120)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    monthly_rent_man: float | None = Field(None, ge=0, le=100_000)
    deposit_man: float | None = Field(None, ge=0)
    area_pyeong: float | None = Field(None, gt=0, le=10_000)
    floor: int | None = None


class DecideConstraints(BaseModel):
    max_rent_man: float | None = Field(None, ge=0, le=100_000)


class DecideRequest(BaseModel):
    goal: Literal["compare_candidates"] = "compare_candidates"
    inds_lcls_cd: str | None = None
    inds_mcls_cd: str | None = None
    inds_scls_cd: str | None = None
    radius_m: int = Field(500, description="분석 반경 (500 또는 1000)")
    priority: Literal["revenue", "stability", "balanced"] = "balanced"
    brand: str | None = Field(None, max_length=40)
    candidates: list[CandidateIn] = Field(..., min_length=2, max_length=5)
    constraints: DecideConstraints | None = None

    @field_validator("radius_m")
    @classmethod
    def _validate_radius(cls, v: int) -> int:
        if v not in (500, 1000):
            raise ValueError("radius_m must be 500 or 1000")
        return v

    @field_validator("candidates")
    @classmethod
    def _unique_ids(cls, v: list[CandidateIn]) -> list[CandidateIn]:
        ids = [c.id for c in v]
        if len(set(ids)) != len(ids):
            raise ValueError("candidate id must be unique")
        return v


class RankedOut(BaseModel):
    id: str
    label: str
    rank: int
    recommend_score: float
    verdict: str
    verdict_label: str
    expected_monthly_sales_man: float | None = None
    expected_sales_basis: str = ""
    max_acceptable_rent_man: float | None = None
    current_rent_man: float | None = None
    rent_gap_man: float | None = None
    strengths: list[str] = []
    risks: list[str] = []
    actions: list[str] = []
    evidence: dict[str, Any] = {}


class DecideResponse(BaseModel):
    ranked: list[RankedOut]
    headline: str = ""
    comparison_note: str = ""
    narrative_source: str = "rule"
    warnings: list[str] = []


@router.post("/decide", response_model=DecideResponse)
async def decide(request: DecideRequest) -> DecideResponse:
    if request.goal != "compare_candidates":
        raise HTTPException(status_code=400, detail="지원하지 않는 goal 입니다.")

    result = await compare_candidates(
        [
            CandidateInput(
                id=c.id,
                label=c.label or c.id,
                lat=c.lat,
                lng=c.lng,
                monthly_rent_man=c.monthly_rent_man,
                deposit_man=c.deposit_man,
                area_pyeong=c.area_pyeong,
                floor=c.floor,
            )
            for c in request.candidates
        ],
        inds_lcls_cd=request.inds_lcls_cd,
        inds_mcls_cd=request.inds_mcls_cd,
        inds_scls_cd=request.inds_scls_cd,
        radius_m=request.radius_m,
        priority=request.priority,
        brand=request.brand,
        budget_max_rent_man=(
            request.constraints.max_rent_man if request.constraints else None
        ),
    )

    warnings = list(result.warnings)
    try:
        headline, note, source = await asyncio.wait_for(
            narrate_decision(result.ranked, priority=request.priority),
            timeout=NARRATIVE_TIMEOUT_S,
        )
    except TimeoutError:
        from app.services.decision_narrative import rule_based_narrative

        headline, note = rule_based_narrative(result.ranked, request.priority)
        source = "rule"
        warnings.append("AI 서술 응답이 지연되어 규칙 기반 요약으로 대체했습니다.")

    return DecideResponse(
        ranked=[
            RankedOut(
                id=r.id,
                label=r.label,
                rank=r.rank,
                recommend_score=r.recommend_score,
                verdict=r.verdict,
                verdict_label=r.verdict_label,
                expected_monthly_sales_man=r.expected_monthly_sales_man,
                expected_sales_basis=r.expected_sales_basis,
                max_acceptable_rent_man=r.max_acceptable_rent_man,
                current_rent_man=r.current_rent_man,
                rent_gap_man=r.rent_gap_man,
                strengths=r.strengths,
                risks=r.risks,
                actions=r.actions,
                evidence=r.evidence,
            )
            for r in result.ranked
        ],
        headline=headline,
        comparison_note=note,
        narrative_source=source,
        warnings=warnings,
    )
