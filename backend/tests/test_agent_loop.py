"""도구를 루프로 돌리는 경로(services/agent_loop.py) 단위 테스트.

로컬 개발기의 OPENAI 키가 만료돼 있어 실제 모델로는 end-to-end 확인이 안 된다.
그래서 모델 자리에 대본(stub)을 넣어 **루프의 기계적 동작**을 고정한다: 도구를
고르고, 결과를 보고 다시 고르고, 예산·라운드 상한에서 멈추고, 실패하면 None으로
빠져 라우터가 기존 경로로 돌아가는 것.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import agent_loop, llm_client
from app.services.agent_loop import Observation, run_agent_loop
from app.services.llm_client import ToolCall, ToolTurn


def _turn_with_call(call_id: str, args: dict) -> ToolTurn:
    return ToolTurn(
        text=None,
        tool_calls=[ToolCall(id=call_id, name="analyze", arguments=args)],
        assistant_message={"role": "assistant", "content": ""},
    )


def _turn_with_text(text: str) -> ToolTurn:
    return ToolTurn(text=text, tool_calls=[], assistant_message={"role": "assistant", "content": text})


def _script(monkeypatch, turns: list[ToolTurn | None]) -> list[list[dict]]:
    """모델 대본을 깔고, 매 호출에 실려 온 messages를 기록해 돌려준다."""
    seen: list[list[dict]] = []
    calls = iter(turns)

    async def _fake(*, system, messages, tools, max_tokens=0, timeout_s=0, model=None):
        seen.append(list(messages))
        try:
            return next(calls)
        except StopIteration:
            return _turn_with_text("대본 끝")

    # 2026-11-06: 루프는 llm_client.AgentConversation을 거친다. 로컬 제공자 경로는
    # complete_with_tools를 그대로 부르므로, 그 자리에 대본을 깔면 기계적 동작이 같다.
    monkeypatch.setattr(llm_client, "complete_with_tools", _fake)
    monkeypatch.setattr(llm_client, "_provider", lambda: "local")
    monkeypatch.setattr(llm_client, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent_loop, "llm_enabled", lambda: True)
    return seen


def _loop(question: str, analyze, **kwargs):
    return asyncio.run(
        run_agent_loop(
            question,
            analyze=analyze,
            tool_names=["lease_verdict", "trends"],
            tool_labels={"lease_verdict": "임차 계약 판정", "trends": "성장 신호"},
            capabilities="가진 것: 서울 도로망",
            **kwargs,
        )
    )


def test_loop_runs_the_tool_the_model_picked_then_writes_the_answer(monkeypatch):
    seen = _script(
        monkeypatch,
        [
            _turn_with_call("c1", {"place": "신촌역", "business": "카페", "tools": ["trends"]}),
            _turn_with_text("**한 줄 결론** — 유입은 좋지만 경쟁이 강합니다."),
        ],
    )
    got_args: list[dict] = []

    async def _analyze(args):
        got_args.append(args)
        return Observation(facts="입지 점수 78점 · 동일업종 12곳", payload={"site": "SITE"})

    result = _loop("신촌역 카페 자리 어때?", _analyze)

    assert result is not None
    assert got_args == [{"place": "신촌역", "business": "카페", "tools": ["trends"]}]
    assert result.answer.startswith("**한 줄 결론**")
    assert result.talk_only is False
    assert [o.facts for o in result.observations] == ["입지 점수 78점 · 동일업종 12곳"]
    # 두 번째 호출에는 도구 결과가 tool 메시지로 실려 있어야 한다 — 이게 없으면
    # 모델은 자기가 뭘 받았는지 모른 채 답을 쓴다.
    tool_messages = [m for m in seen[1] if m.get("role") == "tool"]
    assert tool_messages and tool_messages[0]["content"] == "입지 점수 78점 · 동일업종 12곳"
    assert tool_messages[0]["tool_call_id"] == "c1"


def test_loop_lets_the_model_fix_a_wrong_place_after_seeing_the_error(monkeypatch):
    """첫 시도가 실패하면 모델이 고쳐서 다시 부를 수 있다 — 한 번에 결정하는
    종전 구조에서는 불가능했던 것."""
    _script(
        monkeypatch,
        [
            _turn_with_call("c1", {"place": "부산 서면", "business": "카페"}),
            _turn_with_call("c2", {"place": "서면역", "business": "카페"}),
            _turn_with_text("서울 밖은 확인되지 않습니다."),
        ],
    )
    seen_places: list[str] = []

    async def _analyze(args):
        seen_places.append(args["place"])
        if args["place"] == "부산 서면":
            return Observation(facts="", error="'부산 서면'의 좌표를 찾지 못했다. 서울만 된다.")
        return Observation(facts="입지 점수 55점", payload={"site": "SITE"})

    result = _loop("부산 서면 카페 어때?", _analyze)

    assert result is not None
    assert seen_places == ["부산 서면", "서면역"]
    assert result.rounds == 3
    assert [bool(o.error) for o in result.observations] == [True, False]


def test_loop_answers_without_calling_any_tool(monkeypatch):
    """개념 질문 — 도구를 부르지 않고 답한다(talk_only)."""
    _script(monkeypatch, [_turn_with_text("공시지가는 정부가 매기는 가격입니다.")])

    async def _analyze(_args):  # 불려서는 안 된다
        raise AssertionError("도구를 부르지 않아야 한다")

    result = _loop("공시지가랑 실거래가 차이가 뭐야?", _analyze)

    assert result is not None
    assert result.talk_only is True
    assert result.observations == []
    assert "공시지가" in result.answer


def test_loop_gives_up_to_the_old_path_when_the_model_is_unavailable(monkeypatch):
    """모델이 없거나 도구 호출을 지원하지 않으면 None — 라우터가 기존 경로로 간다."""
    _script(monkeypatch, [None])

    async def _analyze(_args):
        raise AssertionError("모델이 없으면 도구도 부르지 않는다")

    assert _loop("신촌역 카페 어때?", _analyze) is None


def test_loop_is_disabled_when_the_llm_is_off(monkeypatch):
    monkeypatch.setattr(agent_loop, "llm_enabled", lambda: False)
    assert asyncio.run(
        run_agent_loop("아무 질문", analyze=None, tool_names=[], tool_labels={}, capabilities="")
    ) is None


def test_loop_stops_at_the_round_cap_and_still_answers(monkeypatch):
    """모델이 계속 도구만 부르면 상한에서 멈추고, 모은 관찰로 답을 쓴다."""
    _script(
        monkeypatch,
        [
            _turn_with_call("c1", {"place": "신촌역"}),
            _turn_with_call("c2", {"place": "신촌역"}),
            _turn_with_text("마지막 정리"),
        ],
    )

    async def _analyze(_args):
        return Observation(facts="점수 78점", payload={"site": "SITE"})

    result = _loop("신촌역 어때?", _analyze, max_rounds=2)

    assert result is not None
    assert result.rounds == 2, "상한을 넘겨 돌지 않는다"
    assert len(result.observations) == 2
    assert result.answer == "마지막 정리", "상한 뒤에는 도구 없이 답만 쓰게 한다"


def test_loop_caps_how_many_tools_run_in_one_round(monkeypatch):
    """한 라운드에 부르는 도구 수 상한 — 열 개를 부르면 예산이 통째로 날아간다."""
    many = ToolTurn(
        text=None,
        tool_calls=[ToolCall(id=f"c{i}", name="analyze", arguments={"place": f"장소{i}"}) for i in range(5)],
        assistant_message={"role": "assistant", "content": ""},
    )
    seen = _script(monkeypatch, [many, _turn_with_text("정리")])
    ran: list[str] = []

    async def _analyze(args):
        ran.append(args["place"])
        return Observation(facts=f"{args['place']} 결과", payload={"site": "SITE"})

    result = _loop("여러 곳 비교", _analyze)

    assert result is not None
    assert len(ran) == agent_loop.MAX_CALLS_PER_ROUND
    # 자르고 넘어간 호출도 tool 메시지로 알려야 한다 — 안 그러면 모델이 돌지도
    # 않은 도구의 결과를 인용한다.
    skipped = [m for m in seen[1] if m.get("role") == "tool" and "넘겨 실행하지 않았다" in m["content"]]
    assert len(skipped) == 5 - agent_loop.MAX_CALLS_PER_ROUND


def test_loop_survives_a_tool_that_raises(monkeypatch):
    _script(monkeypatch, [_turn_with_call("c1", {"place": "신촌역"}), _turn_with_text("확인되지 않았습니다")])

    async def _analyze(_args):
        raise RuntimeError("엔진 폭발")

    result = _loop("신촌역 어때?", _analyze)

    assert result is not None
    assert result.talk_only is True, "쓸 수 있는 관찰이 없으면 보고서 없이 글만 낸다"
    assert "엔진 폭발" in result.observations[0].error


@pytest.mark.parametrize("question", ["", "   "])
def test_loop_ignores_empty_questions(monkeypatch, question):
    monkeypatch.setattr(agent_loop, "llm_enabled", lambda: True)
    assert _loop(question, None) is None


# ─────────────────── 라우터 배선 (settings.agent_loop_enabled) ───────────────────

from types import SimpleNamespace  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from app.routers import agent  # noqa: E402


def _full_site():
    """AgentAskResponse.site를 채우는 최소 유효 SiteScoreResponse."""
    from app.models.schemas import ExplanationOut, NearestSegmentOut, SiteScoreResponse

    return SiteScoreResponse(
        lat=37.5,
        lng=127.0,
        radius_m=500,
        business_type="cafe",
        business_label="카페",
        total_score=78.0,
        grade="B",
        verdict_one_liner="",
        segment=NearestSegmentOut(
            osm_id=1,
            highway="residential",
            distance_m=10.0,
            integration_top_pct=50.0,
            choice_top_pct=50.0,
            mean_depth_top_pct=50.0,
            connectivity_top_pct=50.0,
            connectivity_degree=3,
        ),
        subscores=[],
        explanation=ExplanationOut(text="", source="rule"),
    )


def test_agent_ask_uses_the_loop_result_when_the_flag_is_on(monkeypatch):
    async def _loop(*_a, **_k):
        return agent_loop.LoopResult(
            answer="**한 줄 결론** — 경쟁이 강합니다.",
            observations=[
                Observation(
                    facts="점수 78점",
                    payload={
                        "site": _full_site(),
                        "tool_results": {"trends": {"verdict": "성장"}},
                        "address": "신촌역",
                        "business_type": "cafe",
                        "business_label": "카페",
                        "upjong": None,
                        "params": {"monthly_rent_man": 300.0},
                    },
                )
            ],
            rounds=2,
        )

    monkeypatch.setattr(agent.settings, "agent_loop_enabled", True, raising=False)
    monkeypatch.setattr(agent, "run_agent_loop", _loop)
    # 루프가 답을 냈으면 기존 파이프라인은 아예 돌지 않아야 한다.
    async def _boom(*_a, **_k):
        raise AssertionError("기존 경로가 돌면 안 된다")

    monkeypatch.setattr(agent, "_extract_intent", _boom)

    resp = asyncio.run(agent.agent_ask(agent.AgentAskRequest(query="신촌역 카페 월세 300 계약해도 될까?")))

    assert resp.mode == "analyze"
    assert resp.site is not None and resp.site.total_score == 78.0
    assert resp.address == "신촌역"
    assert resp.tools == ["trends"]
    assert resp.tool_results == {"trends": {"verdict": "성장"}}
    assert resp.parameters == {"monthly_rent_man": 300.0}
    assert resp.answer_source == "llm"


def test_agent_ask_falls_back_to_the_old_pipeline_when_the_loop_returns_none(monkeypatch):
    async def _no_loop(*_a, **_k):
        return None

    called: list[str] = []

    async def _intent(query, _tree=None):
        called.append(query)
        # 기존 경로가 실제로 돌았다는 것만 확인하고 즉시 빠져나온다.
        raise HTTPException(status_code=400, detail="테스트용 중단")

    monkeypatch.setattr(agent.settings, "agent_loop_enabled", True, raising=False)
    monkeypatch.setattr(agent, "run_agent_loop", _no_loop)
    monkeypatch.setattr(agent, "_extract_intent", _intent)

    with pytest.raises(HTTPException):
        asyncio.run(agent.agent_ask(agent.AgentAskRequest(query="신촌역 카페 어때?")))
    assert called == ["신촌역 카페 어때?"], "루프가 못 하면 기존 경로로 내려간다"


def test_loop_analyze_reports_an_unresolvable_place_instead_of_raising(monkeypatch):
    """좌표를 못 찾으면 예외가 아니라 관찰의 error로 — 모델이 고쳐서 다시 부른다."""
    async def _none(_place):
        return None

    monkeypatch.setattr(agent, "_resolve_point", _none)
    obs = asyncio.run(agent._loop_analyze({"place": "부산 서면", "business": "카페"}, question="q"))
    assert obs.facts == ""
    assert "좌표를 찾지 못했다" in obs.error


def test_loop_analyze_skips_a_tool_whose_values_are_missing(monkeypatch):
    """월세 없이 lease_verdict를 부르면 실행하지 않고, 무엇이 없어서 못 돌았는지 남긴다."""
    async def _point(_place):
        return agent._Point(lat=37.5, lng=127.0, label="신촌역")

    async def _site(_req):
        return SimpleNamespace(total_score=78.0)

    monkeypatch.setattr(agent, "_resolve_point", _point)
    monkeypatch.setattr(agent, "run_site_score", _site)
    monkeypatch.setattr(agent, "_facts_for_answer", lambda *_a, **_k: "점수 78점")

    async def _never(*_a, **_k):
        raise AssertionError("값이 없으면 도구를 부르지 않는다")

    monkeypatch.setattr(agent, "_run_tool", _never)

    obs = asyncio.run(
        agent._loop_analyze(
            {"place": "신촌역", "business": "카페", "tools": ["lease_verdict"]}, question="q"
        )
    )
    assert "monthly_rent_man" in obs.facts
    assert "실행하지 못한 것" in obs.facts
    assert obs.payload["tool_results"] == {}


# ───────────────────── 2026-11-06: 스트리밍 상담 ─────────────────────


def _stream(question: str, analyze, **kwargs) -> list[agent_loop.LoopEvent]:
    async def _collect():
        return [
            e
            async for e in agent_loop.stream_agent_loop(
                question,
                analyze=analyze,
                tool_names=["trends"],
                tool_labels={"trends": "성장 신호"},
                capabilities="가진 것: 서울 도로망",
                **kwargs,
            )
        ]

    return asyncio.run(_collect())


def test_stream_reports_status_then_streams_the_answer(monkeypatch):
    _script(
        monkeypatch,
        [
            _turn_with_call("c1", {"place": "신촌역", "business": "카페", "tools": ["trends"]}),
            _turn_with_text("신촌역 카페는 유입은 좋지만 경쟁이 강합니다."),
        ],
    )

    async def _analyze(_args):
        return Observation(facts="입지 점수 상위 10%", payload={"site": "SITE"})

    events = _stream("신촌역 카페 자리 어때?", _analyze)

    assert [e.type for e in events] == ["status", "delta", "done"]
    assert events[0].text == "신촌역 · 카페 분석 중"
    assert events[1].text.startswith("신촌역 카페는")
    assert events[-1].result is not None and events[-1].result.talk_only is False


def test_stream_clears_text_the_model_spoke_before_deciding_to_call_a_tool(monkeypatch):
    """도구를 부르기 전에 꺼낸 말("확인해 볼게요")은 답이 아니다 — reset으로 지운다."""
    preamble = ToolTurn(
        text="확인해 볼게요.",
        tool_calls=[ToolCall(id="c1", name="analyze", arguments={"place": "성수동"})],
        assistant_message={"role": "assistant", "content": "확인해 볼게요."},
    )

    async def _fake_stream(self, *, tools, max_tokens=0, timeout_s=0):
        if not hasattr(self, "_n"):
            self._n = 0
        self._n += 1
        if self._n == 1:
            yield llm_client.StreamEvent(kind="delta", text="확인해 볼게요.")
            yield llm_client.StreamEvent(kind="turn", turn=preamble)
        else:
            yield llm_client.StreamEvent(kind="delta", text="성수동은 괜찮습니다.")
            yield llm_client.StreamEvent(kind="turn", turn=_turn_with_text("성수동은 괜찮습니다."))

    monkeypatch.setattr(llm_client.AgentConversation, "stream_turn", _fake_stream)
    monkeypatch.setattr(agent_loop, "llm_enabled", lambda: True)

    async def _analyze(_args):
        return Observation(facts="점수", payload={"site": "S"})

    events = _stream("성수동 어때?", _analyze)
    assert [e.type for e in events] == ["delta", "reset", "status", "delta", "done"]
    result = asyncio.run(
        agent_loop.run_agent_loop(
            "성수동 어때?", analyze=_analyze, tool_names=["trends"], tool_labels={}, capabilities=""
        )
    )
    assert result is not None and result.answer == "성수동은 괜찮습니다."


def test_stream_puts_the_previous_turns_before_the_question(monkeypatch):
    """후속 질문은 앞 질문과 **앞 답 본문**을 함께 본다 — 종전엔 장소·업종만 넘겼다."""
    seen = _script(monkeypatch, [_turn_with_text("디저트 카페도 비슷합니다.")])

    async def _analyze(_args):
        raise AssertionError

    history = [
        {"role": "user", "content": "신촌역 카페 어때?"},
        {"role": "assistant", "content": "유입은 좋지만 경쟁이 강합니다."},
    ]
    _stream("그럼 디저트 카페는?", _analyze, history=history)

    assert [m["role"] for m in seen[0]] == ["user", "assistant", "user"]
    assert seen[0][1]["content"] == "유입은 좋지만 경쟁이 강합니다."
    assert seen[0][2]["content"] == "그럼 디저트 카페는?"


def test_stream_keeps_text_already_shown_when_the_turn_breaks(monkeypatch):
    async def _fake_stream(self, *, tools, max_tokens=0, timeout_s=0):
        yield llm_client.StreamEvent(kind="delta", text="절반까지 쓴 답")
        yield llm_client.StreamEvent(kind="error", text="timeout")

    monkeypatch.setattr(llm_client.AgentConversation, "stream_turn", _fake_stream)
    monkeypatch.setattr(agent_loop, "llm_enabled", lambda: True)

    async def _analyze(_args):
        raise AssertionError

    events = _stream("권리금은 어떻게 협상해?", _analyze)
    assert events[-1].type == "done"
    assert events[-1].result is not None and events[-1].result.answer == "절반까지 쓴 답"


def test_system_prompt_no_longer_forces_a_fixed_template():
    """소유자 지적(정해진 양식으로만 답한다)의 회귀 방지."""
    prompt = agent_loop._system_prompt("가진 것")
    assert "한 줄 결론" not in prompt
    assert "1,200자" not in prompt
    assert "일반 지식" in prompt
    assert "지어내지 않는다" in prompt


# ───────────────────── Responses API 백엔드 (gpt-6.1-sol) ─────────────────────


class _Ev:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeStream:
    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


def _fake_openai(monkeypatch, scripts, *, reject_reasoning=False):
    """responses.create 호출 kwargs를 기록하고 대본의 이벤트를 흘리는 가짜 클라이언트."""
    calls: list[dict] = []
    scripts = list(scripts)

    class _Responses:
        async def create(self, **kwargs):
            calls.append(dict(kwargs))
            if reject_reasoning and "reasoning" in kwargs:
                raise RuntimeError("Unsupported parameter: reasoning")
            return _FakeStream(scripts.pop(0))

    class _Client:
        def __init__(self, **_kw):
            self.responses = _Responses()

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)
    monkeypatch.setattr(llm_client, "_provider", lambda: "openai")
    monkeypatch.setattr(llm_client, "llm_enabled", lambda: True)
    monkeypatch.setattr(llm_client.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(llm_client.settings, "openai_agent_model", "gpt-6.1-sol")
    monkeypatch.setattr(llm_client.settings, "openai_agent_reasoning_effort", "low")
    return calls


def _drain(conv, tools):
    async def _go():
        return [e async for e in conv.stream_turn(tools=tools)]

    return asyncio.run(_go())


_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {"name": "analyze", "description": "분석", "parameters": {"type": "object", "properties": {}}},
    }
]


def test_responses_backend_streams_text_and_chains_tool_outputs(monkeypatch):
    calls = _fake_openai(
        monkeypatch,
        [
            [
                _Ev("response.created", response=_Obj(id="resp_1")),
                _Ev(
                    "response.output_item.done",
                    item=_Obj(type="function_call", call_id="call_1", name="analyze", arguments='{"place": "신촌역"}'),
                ),
                _Ev("response.completed", response=_Obj(id="resp_1")),
            ],
            [
                _Ev("response.created", response=_Obj(id="resp_2")),
                _Ev("response.output_text.delta", delta="신촌역은 "),
                _Ev("response.output_text.delta", delta="괜찮습니다."),
                _Ev("response.completed", response=_Obj(id="resp_2")),
            ],
        ],
    )
    conv = llm_client.AgentConversation(
        system="지시문", history=[{"role": "user", "content": "앞 질문"}, {"role": "assistant", "content": "앞 답"}]
    )
    conv.add_user("신촌역 카페 어때?")

    first = _drain(conv, _TOOLS_SCHEMA)
    assert [e.kind for e in first] == ["turn"]
    turn = first[0].turn
    assert turn.tool_calls[0].id == "call_1" and turn.tool_calls[0].arguments == {"place": "신촌역"}
    # 첫 요청: 지시문은 instructions로, 도구는 Responses 형식(평평한 name)으로, 앞 대화 포함.
    assert calls[0]["model"] == "gpt-6.1-sol"
    assert calls[0]["instructions"] == "지시문"
    assert calls[0]["tools"][0]["name"] == "analyze"
    assert calls[0]["reasoning"] == {"effort": "low"}
    assert [m["content"] for m in calls[0]["input"]] == ["앞 질문", "앞 답", "신촌역 카페 어때?"]
    assert "previous_response_id" not in calls[0]

    conv.add_tool_outputs(turn, [("call_1", "점수 상위 10%")])
    second = _drain(conv, _TOOLS_SCHEMA)
    assert [e.kind for e in second] == ["delta", "delta", "turn"]
    assert second[-1].turn.text == "신촌역은 괜찮습니다."
    # 둘째 요청: 직전 응답에 이어 붙이고, 입력은 도구 결과만.
    assert calls[1]["previous_response_id"] == "resp_1"
    assert calls[1]["input"] == [{"type": "function_call_output", "call_id": "call_1", "output": "점수 상위 10%"}]


def test_responses_backend_retries_without_reasoning_when_rejected(monkeypatch):
    calls = _fake_openai(
        monkeypatch,
        [[_Ev("response.output_text.delta", delta="답"), _Ev("response.completed", response=_Obj(id="r"))]],
        reject_reasoning=True,
    )
    conv = llm_client.AgentConversation(system="s")
    conv.add_user("질문")
    events = _drain(conv, [])
    assert [e.kind for e in events] == ["delta", "turn"]
    assert "reasoning" in calls[0] and "reasoning" not in calls[1]
    assert "tools" not in calls[1]


def test_responses_backend_reports_failure_as_an_event(monkeypatch):
    _fake_openai(monkeypatch, [[_Ev("response.failed", response=_Obj(id="r", error="server_error"))]])
    conv = llm_client.AgentConversation(system="s")
    conv.add_user("질문")
    events = _drain(conv, [])
    assert [e.kind for e in events] == ["error"]


# ───────────────────── 라우터: 대화 기록·스트림 엔드포인트 ─────────────────────

import json as _json  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_history_messages_carry_answers_and_trim_old_turns():
    turns = [agent.AgentHistoryTurn(query=f"질문{i}", answer=f"답{i}") for i in range(6)]
    turns.append(agent.AgentHistoryTurn(query="긴 답", answer="가" * 3000))
    turns.append(agent.AgentHistoryTurn(query="답 없는 턴", address="성수동", business_label="카페"))
    msgs = agent._history_messages(turns)

    assert len(msgs) == 2 * agent._HISTORY_TURNS
    assert msgs[0] == {"role": "user", "content": "질문4"}
    long_answer = next(m["content"] for m in msgs if m["content"].startswith("가"))
    assert len(long_answer) < 1600 and long_answer.endswith("(이하 생략)")
    # 답 본문이 없는 턴은 무엇을 분석했는지만 남긴다.
    assert msgs[-1]["content"] == "(성수동 · 카페 기준으로 분석 결과를 보여 드렸습니다.)"


def test_talk_response_skips_the_llm_when_the_stream_writes_the_answer(monkeypatch):
    async def _never(*_a, **_k):
        raise AssertionError("talk=False면 LLM을 부르지 않는다")

    monkeypatch.setattr(agent, "talk_answer", _never)
    req = agent.AgentAskRequest(query="권리금은 어떻게 협상해?", talk=False)
    out = asyncio.run(agent._talk_response(req, fallback_detail="x"))
    assert out.mode == "talk" and out.answer == "" and out.site is None


def _sse_events(body: str) -> list[dict]:
    return [_json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def _stream_client() -> TestClient:
    app = FastAPI()
    app.include_router(agent.router)
    return TestClient(app)


def test_stream_endpoint_emits_deltas_and_a_final_response(monkeypatch):
    seen: dict = {}

    async def _fake_stream(question, **kwargs):
        seen["history"] = kwargs["history"]
        yield agent_loop.LoopEvent(type="status", text="신촌역 · 카페 분석 중")
        yield agent_loop.LoopEvent(type="delta", text="괜찮은 ")
        yield agent_loop.LoopEvent(type="delta", text="자리입니다.")
        yield agent_loop.LoopEvent(
            type="done", result=agent_loop.LoopResult(answer="괜찮은 자리입니다.", observations=[], rounds=1)
        )

    monkeypatch.setattr(agent, "stream_agent_loop", _fake_stream)
    res = _stream_client().post(
        "/api/agent/ask/stream",
        json={"query": "그럼 신촌역은?", "history": [{"query": "성수동 카페", "answer": "경쟁이 셉니다."}]},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(res.text)
    assert [e["type"] for e in events] == ["status", "delta", "delta", "done"]
    assert events[-1]["fallback"] is False
    assert events[-1]["response"]["answer"] == "괜찮은 자리입니다."
    assert seen["history"][1] == {"role": "assistant", "content": "경쟁이 셉니다."}


def test_stream_endpoint_tells_the_screen_to_fall_back(monkeypatch):
    async def _fake_stream(question, **kwargs):
        yield agent_loop.LoopEvent(type="done", result=None)

    monkeypatch.setattr(agent, "stream_agent_loop", _fake_stream)
    events = _sse_events(_stream_client().post("/api/agent/ask/stream", json={"query": "신촌역 카페"}).text)
    assert events == [{"type": "done", "response": None, "fallback": True}]
