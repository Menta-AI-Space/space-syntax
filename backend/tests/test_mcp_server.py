"""MCP 노출 — 도구 등록과 인증 가드를 고정한다.

프로토콜 왕복(initialize→tools/call)은 dev 서버에서 실측했다(2026-08-09,
신촌역 86.2점). 여기서는 회귀가 잦은 두 가지만 고정한다: 도구 목록이
비거나 이름이 바뀌는 것, 그리고 REST에 자물쇠를 채워 놓고 MCP 문을 열어
두는 것.
"""

import asyncio

import pytest

from app.config import settings
from app.mcp_server import _ApiKeyGuard, server


def test_the_four_tools_are_registered():
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools.tools} if hasattr(tools, "tools") else {t.name for t in tools} == {
        "score_site", "judge_lease", "compare_candidates", "find_similar_areas",
    }


class _Recorder:
    def __init__(self):
        self.messages = []
        self.called_inner = False

    async def send(self, msg):
        self.messages.append(msg)

    async def inner_app(self, scope, receive, send):
        self.called_inner = True


@pytest.mark.parametrize("supplied,expect_inner", [
    (None, False),         # 키 없음 → 401
    ("wrong", False),      # 틀린 키 → 401
    ("secret-token", True) # 맞는 키 → 통과
])
def test_mcp_requires_the_same_key_as_rest(monkeypatch, supplied, expect_inner):
    """REST에 API 키가 걸려 있으면 MCP도 같은 키를 요구한다.

    FastAPI 의존성은 mount된 ASGI 앱을 지나치므로, 이 가드가 없으면 키를
    설정해 둔 배포에서 MCP만 무인증으로 열린다.
    """
    monkeypatch.setattr(settings, "api_access_token", "secret-token")
    rec = _Recorder()
    guard = _ApiKeyGuard(rec.inner_app)
    headers = [(b"x-api-key", supplied.encode())] if supplied else []
    scope = {"type": "http", "headers": headers}

    asyncio.run(guard(scope, None, rec.send))

    assert rec.called_inner is expect_inner
    if not expect_inner:
        assert rec.messages[0]["status"] == 401


def test_mcp_is_open_when_rest_is_open(monkeypatch):
    """키가 설정돼 있지 않으면 REST처럼 MCP도 열린다 — 정책 동일성."""
    monkeypatch.setattr(settings, "api_access_token", "")
    rec = _Recorder()
    guard = _ApiKeyGuard(rec.inner_app)
    asyncio.run(guard({"type": "http", "headers": []}, None, rec.send))
    assert rec.called_inner is True
