"""제공자 중립 LLM 호출 레이어.

이 프로젝트는 나중에 OpenAI API 대신 **로컬에서 직접 돌리는 LLM**으로 옮길
예정이다. 그래서 서비스 코드는 절대 `AsyncOpenAI`를 직접 부르지 않고 여기의
`complete_json()` / `complete_text()`만 쓴다. 전환 시 바꾸는 곳은 이 파일 하나다.

로컬 LLM으로 갈 때 실제로 문제가 되는 것들을 미리 흡수해 둔다:

1. **JSON 강제 기능이 없다.** OpenAI의 `response_format={"type":"json_object"}`는
   대부분의 로컬 서버(llama.cpp, Ollama)에서 지원되지 않거나 무시된다. 그래서
   여기서는 그 옵션에 의존하지 않고, 응답 텍스트에서 JSON을 직접 파싱한다
   (```json 코드펜스, 앞뒤 잡담 제거 포함).
2. **느리다.** 7B~14B 로컬 모델은 첫 토큰까지 수 초가 걸린다. 타임아웃을
   호출부가 정하게 하고, 초과하면 예외 대신 None을 돌려 호출부의 규칙 기반
   폴백이 그대로 동작하게 한다.
3. **동시 요청에 약하다.** GPU 한 장에 요청이 몰리면 전부 느려진다. 세마포어로
   동시 호출 수를 제한한다.
4. **지시 이행률이 낮다.** 그래서 이 프로젝트의 원칙(숫자는 엔진이 계산하고
   LLM은 서술만)이 더 중요해진다 — LLM 출력이 비거나 깨져도 결과의 숫자는
   바뀌지 않는다.

설정(app/config.py):
    llm_provider: "openai" | "local" | "off"
    llm_base_url:  로컬 서버 주소 (예: http://localhost:11434/v1)
    llm_model:     로컬 모델 이름 (예: qwen2.5:14b-instruct)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.config import settings

# 로컬 GPU 한 장 기준 — 동시에 밀어 넣어도 총 처리량은 안 늘고 지연만 커진다.
_MAX_CONCURRENT = 2
logger = logging.getLogger(__name__)
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)


def _slot() -> contextlib.AbstractAsyncContextManager[Any]:
    """동시 호출 제한은 **로컬 제공자에만** 건다.

    2026-11-06: 이 세마포어(2)가 OpenAI 경로에도 걸려 있어서, 사용자 셋이 동시에
    물으면 셋째는 앞 사람의 서술(최대 25초)이 끝날 때까지 줄을 섰다. GPU 한 장을
    나눠 쓰는 로컬 서버를 위한 장치였고 OpenAI에는 필요 없다.
    """
    if _provider() == "local":
        return _semaphore
    return contextlib.nullcontext()


def llm_enabled() -> bool:
    """LLM 서술을 시도할 수 있는 설정인지."""
    provider = _provider()
    if provider == "off":
        return False
    if provider == "local":
        return bool(_local_base_url())
    return bool(settings.openai_api_key.strip())


def _provider() -> str:
    return (getattr(settings, "llm_provider", "") or "openai").strip().lower()


def _local_base_url() -> str:
    return (getattr(settings, "llm_base_url", "") or "").strip()


def _model_name() -> str:
    if _provider() == "local":
        return (getattr(settings, "llm_model", "") or "").strip() or "local-model"
    return settings.openai_model


def narrative_model_name() -> str:
    """서사(사용자에게 보이는 해석 문장)에 쓰는 모델.

    파싱(complete_json)과 서사(complete_text)를 다른 모델로 나눈다 — 파싱은
    5초 예산의 임계 경로라 빠른 mini가 맞고, 서사는 분석과 분리돼 나중에
    도착하므로 품질 좋은 모델을 쓸 여유가 있다(근거는 config.py 주석의 A/B).

    로컬 제공자면 그대로 로컬 모델을 쓴다 — 서버에 그 모델 하나뿐이다.
    """
    if _provider() == "local":
        return _model_name()
    return (getattr(settings, "openai_narrative_model", "") or "").strip() or _model_name()


def _extract_json(text: str) -> dict[str, Any] | None:
    """응답 텍스트에서 JSON 객체를 건져낸다.

    로컬 모델은 "물론이죠! ```json {...} ```" 처럼 잡담과 코드펜스를 붙이는
    경우가 흔해서, 순수 json.loads만으로는 실패한다.
    """
    if not text:
        return None
    candidates = [text.strip()]

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    # 가장 바깥 중괄호 구간 — 앞뒤 설명 문장을 잘라낸다.
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            continue
    return None


async def _chat(
    *, system: str, user: str, max_tokens: int, timeout_s: float, json_mode: bool,
    model: str | None = None, reasoning_effort: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> str | None:
    """한 번의 chat 호출. 실패·타임아웃은 None (예외를 밖으로 던지지 않는다).

    history(2026-11-09): 앞선 user/assistant 턴을 system 다음, 이번 user 앞에 그대로
    싣는다 — 도면 상담처럼 이어 묻는 대화에 쓴다. 비우면 종전과 같은 한 번 묻기다.
    """
    provider = _provider()
    if provider == "off":
        return None

    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None

    if provider == "local":
        base_url = _local_base_url()
        if not base_url:
            return None
        # 로컬 서버(Ollama/llama.cpp/vLLM)는 OpenAI 호환 엔드포인트를 제공하지만
        # 키 검증은 하지 않는다 — SDK가 빈 키를 거부하므로 자리표시자를 넣는다.
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=(getattr(settings, "llm_api_key", "") or "local"),
            timeout=timeout_s,
        )
    else:
        if not settings.openai_api_key.strip():
            return None
        client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=timeout_s)

    kwargs: dict[str, Any] = {
        "model": model or _model_name(),
        "max_completion_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            *(history or []),
            {"role": "user", "content": user},
        ],
    }
    # json_object 강제는 OpenAI에서만 쓴다. 로컬 서버에 보내면 400으로 죽는
    # 구현이 있어서, 로컬에서는 프롬프트 지시 + _extract_json으로 처리한다.
    if json_mode and provider != "local":
        kwargs["response_format"] = {"type": "json_object"}
    # 추론 모델은 답을 쓰기 전에 생각하는 데 시간을 쓴다. 짧은 서술에는 그 시간이 거의
    # 필요 없다. 모델이 이 값을 모르면 400이 나므로, 그때는 빼고 한 번 더 부른다.
    if reasoning_effort and provider != "local":
        kwargs["reasoning_effort"] = reasoning_effort

    try:
        async with _slot():
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(**kwargs), timeout=timeout_s
                )
            except Exception as exc:
                if "reasoning_effort" not in kwargs or "reasoning" not in str(exc).lower():
                    raise
                logger.warning("reasoning_effort 거부 (%s) — 빼고 다시 부른다: %s", kwargs["model"], exc)
                kwargs.pop("reasoning_effort")
                response = await asyncio.wait_for(
                    client.chat.completions.create(**kwargs), timeout=timeout_s
                )
    except Exception as exc:
        # 조용히 None을 돌려주면 호출부는 규칙 문장으로 잘 넘어가지만, **왜**
        # LLM이 안 붙는지 알 길이 없어진다 (2026-09-24 도면 해석이 라이브에서
        # source:"rule"만 내던 건이 그랬다). 서비스는 계속 살리고 이유만 남긴다.
        logger.warning("LLM 호출 실패 (%s): %s", kwargs["model"], exc)
        return None

    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    if not text:
        # 추론 모델은 max_completion_tokens를 추론에 다 쓰고 본문을 비워 보낼 수
        # 있다. finish_reason이 그걸 구분해 준다.
        logger.warning(
            "LLM 빈 응답 (%s, finish_reason=%s, max_completion_tokens=%s)",
            kwargs["model"], choice.finish_reason, max_tokens,
        )
        return None
    return text


async def complete_json(
    *,
    system: str,
    user: str,
    max_tokens: int = 900,
    timeout_s: float = 20.0,
) -> dict[str, Any] | None:
    """JSON 객체를 기대하는 호출. 실패하면 None — 호출부가 폴백을 쓴다."""
    if not llm_enabled():
        return None
    raw = await _chat(
        system=system, user=user, max_tokens=max_tokens, timeout_s=timeout_s, json_mode=True
    )
    if raw is None:
        return None
    return _extract_json(raw)


async def complete_text(
    *,
    system: str,
    user: str,
    max_tokens: int = 800,
    timeout_s: float = 20.0,
    fast: bool = False,
) -> str | None:
    """자유 서술 호출. 실패하면 None.

    서사 전용 모델을 쓴다(narrative_model_name) — complete_json(파싱)과
    일부러 다르다. fast=True(2026-10-26)면 짧은 서술용으로 파싱 모델(mini)과
    낮은 추론 강도를 쓴다 — 긴 서술을 큰 모델로 쓰면 14초였다.
    """
    if not llm_enabled():
        return None
    return await _chat(
        system=system, user=user, max_tokens=max_tokens, timeout_s=timeout_s,
        json_mode=False,
        model=_model_name() if fast else narrative_model_name(),
        reasoning_effort="low" if fast else None,
    )


async def complete_chat(
    *,
    system: str,
    history: list[dict[str, str]],
    user: str,
    max_tokens: int = 800,
    timeout_s: float = 20.0,
    fast: bool = False,
) -> str | None:
    """앞선 대화를 이어 받는 자유 서술 호출(2026-11-09, 도면 상담). 실패하면 None.

    complete_text와 같은 모델 선택(서사 모델, fast면 파싱 모델 + 낮은 추론)이고,
    다른 점은 history(user/assistant 턴 목록)를 system과 이번 질문 사이에 싣는 것뿐이다.
    """
    if not llm_enabled():
        return None
    return await _chat(
        system=system, user=user, max_tokens=max_tokens, timeout_s=timeout_s,
        json_mode=False,
        model=_model_name() if fast else narrative_model_name(),
        reasoning_effort="low" if fast else None,
        history=history,
    )


# ───────────────────────────── 도구 호출(loop) ─────────────────────────────
#
# 위의 complete_json/complete_text는 한 번 물어 한 번 받는다. 도구를 **루프로**
# 돌리려면 (1) 대화 메시지를 누적해 보내고 (2) 모델이 "이 도구를 이 인자로 불러
# 달라"고 답할 수 있어야 한다. 그 두 가지만 여기에 더한다.
#
# 로컬 LLM 주의: tools 파라미터는 llama.cpp/Ollama 구현에 따라 무시되거나 400이
# 된다. 그래서 실패는 예외가 아니라 None으로 돌려주고, 호출부는 종전 경로(규칙
# + 한 번의 파싱)로 그대로 돌아간다. 루프가 죽어도 서비스는 죽지 않는다.


@dataclass
class ToolCall:
    """모델이 요청한 도구 한 건."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolTurn:
    """도구 루프의 한 턴. tool_calls가 비어 있으면 text가 최종 답이다."""

    text: str | None
    tool_calls: list[ToolCall]
    # 다음 턴에 그대로 다시 실어 보내야 하는 assistant 메시지(OpenAI 규약).
    assistant_message: dict[str, Any]


async def complete_with_tools(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int = 1200,
    timeout_s: float = 25.0,
    model: str | None = None,
) -> ToolTurn | None:
    """도구를 쓸 수 있는 한 턴. 실패·타임아웃·미지원은 None."""
    if not llm_enabled():
        return None
    provider = _provider()
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None

    if provider == "local":
        base_url = _local_base_url()
        if not base_url:
            return None
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=(getattr(settings, "llm_api_key", "") or "local"),
            timeout=timeout_s,
        )
    else:
        if not settings.openai_api_key.strip():
            return None
        client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=timeout_s)

    try:
        async with _slot():
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model or narrative_model_name(),
                    max_completion_tokens=max_tokens,
                    messages=[{"role": "system", "content": system}, *messages],
                    tools=tools,
                    tool_choice="auto",
                ),
                timeout=timeout_s,
            )
    except Exception:
        return None

    choice = response.choices[0].message
    calls: list[ToolCall] = []
    for call in getattr(choice, "tool_calls", None) or []:
        try:
            args = json.loads(call.function.arguments or "{}")
        except (json.JSONDecodeError, ValueError):
            args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append(ToolCall(id=call.id, name=call.function.name, arguments=args))

    assistant_message: dict[str, Any] = {"role": "assistant", "content": choice.content or ""}
    if calls:
        assistant_message["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
            }
            for c in calls
        ]
    return ToolTurn(
        text=(choice.content or "").strip() or None,
        tool_calls=calls,
        assistant_message=assistant_message,
    )


# ─────────────────────────── 상담 대화(스트리밍) ───────────────────────────
#
# 2026-11-06: AI 상담 탭의 답을 "엔진 결과를 정해진 칸에 채우는 문장"이 아니라 모델이
# 대화를 이끌고 엔진을 도구로 부르는 상담으로 바꿨다(services/agent_loop.py). 그 대화를
# 여기서 들고 있는다. 두 가지가 위 complete_with_tools와 다르다.
#
# 1. **스트리밍.** 도구를 두세 번 돌리고 답을 쓰면 10~20초다. 글자가 나오는 대로 화면에
#    흘려야 기다림이 견딜 만해진다.
# 2. **Responses API.** gpt-6.1-sol은 Chat Completions에서 함수 호출을 받지 않는다.
#    OpenAI 제공자면 Responses API로, 로컬 제공자면 종전 Chat Completions(비스트리밍)로
#    부른다 — 로컬 서버 대부분은 Responses API가 없다.
#
# 실패는 예외가 아니라 `error` 이벤트로 끝난다. 호출부(agent_loop)가 폴백을 정한다.


def agent_model_name() -> str:
    """AI 상담(도구 루프)에 쓰는 모델."""
    if _provider() == "local":
        return _model_name()
    return (getattr(settings, "openai_agent_model", "") or "").strip() or narrative_model_name()


def _agent_reasoning_effort() -> str | None:
    if _provider() == "local":
        return None
    effort = (getattr(settings, "openai_agent_reasoning_effort", "") or "").strip()
    return effort or None


@dataclass
class StreamEvent:
    """상담 한 턴의 스트림 이벤트.

    kind:
      - "delta": 답 글자 조각(text)
      - "turn": 턴이 끝났다(turn). tool_calls가 있으면 도구를 돌려 이어 가야 한다.
      - "error": 이 턴을 못 끝냈다(text = 사유). 그 뒤로 이벤트는 없다.
    """

    kind: str
    text: str = ""
    turn: "ToolTurn | None" = None


def _responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat Completions 형식의 도구 정의를 Responses API 형식으로 편다."""
    out: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function") or {}
        out.append(
            {
                "type": "function",
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                "strict": False,
            }
        )
    return out


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    try:
        args = json.loads(raw or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return args if isinstance(args, dict) else {}


@dataclass
class AgentConversation:
    """도구를 쓰는 상담 대화 한 건의 상태.

    history는 앞 대화를 {"role": "user"|"assistant", "content": str}로 오래된 순으로
    담는다. 이번 질문은 add_user로 넣는다.
    """

    system: str
    history: list[dict[str, str]] = field(default_factory=list)
    model: str | None = None
    # Responses API 상태: 직전 응답 id와 다음 요청에 실을 입력.
    _previous_id: str | None = None
    _pending: list[dict[str, Any]] = field(default_factory=list)
    # Chat Completions(로컬) 상태: 누적 메시지.
    _messages: list[dict[str, Any]] = field(default_factory=list)
    _started: bool = False

    def __post_init__(self) -> None:
        for turn in self.history:
            role = turn.get("role")
            content = (turn.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                self._pending.append({"role": role, "content": content})
                self._messages.append({"role": role, "content": content})

    def add_user(self, text: str) -> None:
        self._pending.append({"role": "user", "content": text})
        self._messages.append({"role": "user", "content": text})

    def add_tool_outputs(self, turn: "ToolTurn", outputs: list[tuple[str, str]]) -> None:
        """도구 실행 결과를 다음 요청에 싣는다. outputs = [(call_id, 텍스트)]."""
        for call_id, text in outputs:
            self._pending.append({"type": "function_call_output", "call_id": call_id, "output": text})
        self._messages.append(turn.assistant_message)
        for call_id, text in outputs:
            self._messages.append({"role": "tool", "tool_call_id": call_id, "content": text})

    async def stream_turn(
        self,
        *,
        tools: list[dict[str, Any]],
        max_tokens: int = 4000,
        timeout_s: float = 30.0,
    ) -> AsyncIterator[StreamEvent]:
        if not llm_enabled():
            yield StreamEvent(kind="error", text="LLM 비활성")
            return
        if _provider() == "local":
            async for event in self._stream_local(tools=tools, max_tokens=max_tokens, timeout_s=timeout_s):
                yield event
            return
        async for event in self._stream_responses(tools=tools, max_tokens=max_tokens, timeout_s=timeout_s):
            yield event

    async def _stream_local(
        self, *, tools: list[dict[str, Any]], max_tokens: int, timeout_s: float
    ) -> AsyncIterator[StreamEvent]:
        turn = await complete_with_tools(
            system=self.system,
            messages=self._messages,
            tools=tools,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            model=self.model or agent_model_name(),
        )
        if turn is None:
            yield StreamEvent(kind="error", text="도구 호출 실패")
            return
        if turn.text and not turn.tool_calls:
            yield StreamEvent(kind="delta", text=turn.text)
        yield StreamEvent(kind="turn", turn=turn)

    async def _stream_responses(
        self, *, tools: list[dict[str, Any]], max_tokens: int, timeout_s: float
    ) -> AsyncIterator[StreamEvent]:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            yield StreamEvent(kind="error", text="openai 패키지 없음")
            return
        if not settings.openai_api_key.strip():
            yield StreamEvent(kind="error", text="OPENAI_API_KEY 없음")
            return
        client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=timeout_s)
        model = self.model or agent_model_name()
        kwargs: dict[str, Any] = {
            "model": model,
            "instructions": self.system,
            "input": list(self._pending),
            "max_output_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = _responses_tools(tools)
            kwargs["tool_choice"] = "auto"
        if self._previous_id:
            kwargs["previous_response_id"] = self._previous_id
        effort = _agent_reasoning_effort()
        if effort:
            kwargs["reasoning"] = {"effort": effort}

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        response_id: str | None = None
        incomplete = ""
        try:
            async with asyncio.timeout(timeout_s):
                try:
                    stream = await client.responses.create(**kwargs)
                except Exception as exc:
                    # 추론 모델이 아니면 reasoning을 거부한다 — 빼고 한 번 더.
                    if "reasoning" not in kwargs or "reasoning" not in str(exc).lower():
                        raise
                    logger.warning("reasoning 거부 (%s) — 빼고 다시 부른다: %s", model, exc)
                    kwargs.pop("reasoning")
                    stream = await client.responses.create(**kwargs)
                async for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "response.created":
                        response_id = getattr(getattr(event, "response", None), "id", None) or response_id
                    elif etype == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            text_parts.append(delta)
                            yield StreamEvent(kind="delta", text=delta)
                    elif etype == "response.output_item.done":
                        item = getattr(event, "item", None)
                        if getattr(item, "type", "") == "function_call":
                            calls.append(
                                ToolCall(
                                    id=getattr(item, "call_id", "") or getattr(item, "id", ""),
                                    name=getattr(item, "name", ""),
                                    arguments=_parse_arguments(getattr(item, "arguments", "")),
                                )
                            )
                    elif etype in ("response.completed", "response.incomplete"):
                        response = getattr(event, "response", None)
                        response_id = getattr(response, "id", None) or response_id
                        if etype == "response.incomplete":
                            details = getattr(response, "incomplete_details", None)
                            incomplete = str(getattr(details, "reason", "") or "incomplete")
                    elif etype in ("response.failed", "error"):
                        message = getattr(event, "message", "") or str(
                            getattr(getattr(event, "response", None), "error", "") or etype
                        )
                        logger.warning("상담 스트림 실패 (%s): %s", model, message)
                        yield StreamEvent(kind="error", text=str(message)[:200])
                        return
        except Exception as exc:  # noqa: BLE001 - 실패는 이벤트로 알린다
            logger.warning("상담 호출 실패 (%s): %s", model, exc)
            yield StreamEvent(kind="error", text=str(exc)[:200])
            return

        if incomplete:
            # 추론에 토큰을 다 쓰고 답이 잘렸을 수 있다. 쓴 데까지는 살린다.
            logger.warning("상담 응답 미완 (%s): %s", model, incomplete)
        self._previous_id = response_id
        self._pending = []
        text = "".join(text_parts).strip()
        if not text and not calls:
            yield StreamEvent(kind="error", text=f"빈 응답 ({incomplete or 'no output'})")
            return
        assistant_message: dict[str, Any] = {"role": "assistant", "content": text}
        if calls:
            assistant_message["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
                }
                for c in calls
            ]
        yield StreamEvent(
            kind="turn",
            turn=ToolTurn(text=text or None, tool_calls=calls, assistant_message=assistant_message),
        )
