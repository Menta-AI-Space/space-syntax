"""화면 번역 (2026-11-09) — GPT는 가짜로, 캐시·개수 불일치·라우터만 본다."""

import asyncio

from fastapi.testclient import TestClient

from app.main import app
from app.services import ui_translate as ut


def test_cache_and_dedupe(monkeypatch):
    calls = []

    async def fake(**kwargs):
        import json
        texts = json.loads(kwargs["user"])["texts"]
        calls.append(texts)
        return {"translations": [f"EN:{t}" for t in texts]}

    ut._cache.clear()
    monkeypatch.setattr(ut, "complete_json", fake)
    out = asyncio.run(ut.translate_texts(["벽 그리기", "문 뚫기", "벽 그리기"]))
    assert out == ["EN:벽 그리기", "EN:문 뚫기", "EN:벽 그리기"]
    assert calls == [["벽 그리기", "문 뚫기"]]
    asyncio.run(ut.translate_texts(["문 뚫기"]))
    assert len(calls) == 1  # 두 번째는 캐시


def test_count_mismatch_returns_none(monkeypatch):
    async def fake(**kwargs):
        return {"translations": ["only one"]}

    ut._cache.clear()
    monkeypatch.setattr(ut, "complete_json", fake)
    assert asyncio.run(ut.translate_texts(["가", "나"])) == [None, None]


def test_router(monkeypatch):
    async def fake(**kwargs):
        return {"translations": ["Undo"]}

    ut._cache.clear()
    monkeypatch.setattr(ut, "complete_json", fake)
    res = TestClient(app).post("/api/translate", json={"texts": ["되돌리기"]})
    assert res.status_code == 200 and res.json() == {"translations": ["Undo"]}
