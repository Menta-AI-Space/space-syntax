"""SBIZ upjong taxonomy endpoints for business picker UI."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import UpjongPresetOut, UpjongSearchResultOut, UpjongTreeResponse
from app.services.sbiz_upjong import QUICK_PRESETS, get_upjong_tree, search_upjong

router = APIRouter(prefix="/api/upjong", tags=["upjong"])


@router.get("/tree", response_model=UpjongTreeResponse)
async def upjong_tree():
    tree = await get_upjong_tree()
    return UpjongTreeResponse(large=tree, presets=[UpjongPresetOut(**p) for p in QUICK_PRESETS])


@router.get("/search", response_model=list[UpjongSearchResultOut])
async def upjong_search(q: str = Query("", min_length=1), limit: int = Query(30, ge=1, le=100)):
    tree = await get_upjong_tree()
    if not q.strip():
        raise HTTPException(status_code=422, detail="q is required")
    return search_upjong(tree, q, limit=limit)
