"""화면 글자 영어 번역 — 영어 기본 화면용 (2026-11-09, services/ui_translate.py)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.ui_translate import translate_texts

router = APIRouter(prefix="/api", tags=["translate"])


class TranslateRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=200)


class TranslateResponse(BaseModel):
    translations: list[str | None]


@router.post("/translate", response_model=TranslateResponse)
async def translate(request: TranslateRequest):
    # 한 조각이 아주 길면(붙여 넣은 글 등) 자른다 — 화면 글 조각은 대개 수십 자다.
    texts = [t[:4000] for t in request.texts]
    return TranslateResponse(translations=await translate_texts(texts))
