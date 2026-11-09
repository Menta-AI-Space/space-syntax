"""파싱과 서사의 모델 분리 — 파싱은 mini, 서사는 상위 모델.

파싱(complete_json)은 응답 5초 예산의 임계 경로라 빠른 모델이 맞고, 서사
(complete_text)는 분석과 분리돼 나중에 도착하므로 품질 좋은 모델을 쓸 여유가
있다. A/B 실측(2026-08-08): mini의 결론은 하나 마나 한 말이었고 gpt-5.4는
판단과 근거 있는 행동 제안을 냈다. 여기서는 그 분리가 실제 호출에 반영되는
지를 고정한다 — 한쪽만 바꾸고 다른 쪽을 빼먹는 회귀를 막는다.
"""

import asyncio

import pytest

from app.config import settings
from app.services import llm_client


@pytest.fixture
def openai_ready(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "openai_model", "gpt-5.4-mini")
    monkeypatch.setattr(settings, "openai_narrative_model", "gpt-5.4")


def test_narrative_model_defaults_to_the_bigger_one(openai_ready):
    assert llm_client.narrative_model_name() == "gpt-5.4"


def test_empty_narrative_setting_falls_back_to_the_parsing_model(openai_ready, monkeypatch):
    """변수를 비우면 종전처럼 한 모델로 돈다 — 빈 설정이 죽은 설정이면 안 된다."""
    monkeypatch.setattr(settings, "openai_narrative_model", "")
    assert llm_client.narrative_model_name() == "gpt-5.4-mini"


def test_local_provider_ignores_the_narrative_split(openai_ready, monkeypatch):
    """로컬 서버에는 모델이 하나뿐이다 — 서사라고 다른 이름을 보내면 404다."""
    monkeypatch.setattr(settings, "llm_provider", "local")
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5:14b-instruct")
    assert llm_client.narrative_model_name() == "qwen2.5:14b-instruct"


def test_parsing_and_narrative_actually_hit_different_models(openai_ready, monkeypatch):
    """complete_json은 파싱 모델, complete_text는 서사 모델로 간다."""
    used: list[str | None] = []

    async def fake_chat(*, system, user, max_tokens, timeout_s, json_mode, model=None, reasoning_effort=None):
        used.append(model)
        return '{"ok": true}' if json_mode else "서술"

    monkeypatch.setattr(llm_client, "_chat", fake_chat)

    asyncio.run(llm_client.complete_json(system="s", user="u"))
    asyncio.run(llm_client.complete_text(system="s", user="u"))

    # 파싱은 model 미지정(_chat이 기본 파싱 모델을 쓴다), 서사는 명시된 상위 모델.
    assert used == [None, "gpt-5.4"]
