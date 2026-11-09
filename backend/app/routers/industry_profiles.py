"""Configuration-driven industry analysis and adaptive UI profiles."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.services.industry_profiles import get_adaptive_profile, list_profile_summaries

router = APIRouter(prefix="/api/industry-profiles", tags=["industry-profiles"])


class AdaptiveProfileOut(BaseModel):
    """get_adaptive_profile()의 dict를 그대로 감싼다.

    HTTP 응답은 dict든 모델이든 같은 JSON을 낸다. 이 모델이 필요한 이유는
    agent.py의 도구 파이프라인이 모든 도구 결과를 ``result.model_dump()``로
    다루기 때문이다(dict에는 그 메서드가 없다). extra="allow"로 원본 dict의
    필드를 하나도 잘라내지 않고 그대로 통과시킨다.
    """

    model_config = ConfigDict(extra="allow")

    industryId: str
    userType: str
    ui: dict[str, Any]


_MENU_PATH = Path(__file__).resolve().parents[2] / "data" / "industry_menu.json"


@lru_cache(maxsize=1)
def _load_menu() -> dict:
    with open(_MENU_PATH, encoding="utf-8") as f:
        return json.load(f)


@router.get("/menu")
def industry_menu():
    """대분류 → 세부 업종 2단 메뉴 (업종별 분석 진입점).

    각 item의 profile이 null이면 아직 전용 KPI 프로파일이 없어
    범용 분석만 제공된다는 뜻 — 프론트는 그대로 노출하되 표시만 다르게.
    """
    return _load_menu()


@router.get("")
def profile_catalog():
    return list_profile_summaries()


@router.get("/{industry_id}")
def adaptive_profile(
    industry_id: str,
    user_type: str = Query("entrepreneur", pattern="^(entrepreneur|franchise)$"),
) -> AdaptiveProfileOut:
    try:
        profile = get_adaptive_profile(industry_id, user_type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown industry profile: {industry_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AdaptiveProfileOut(**profile)
