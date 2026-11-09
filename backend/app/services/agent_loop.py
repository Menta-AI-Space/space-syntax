"""도구를 **루프로** 돌리는 상담 층.

기존 경로(`/api/agent/ask`)는 한 번에 결정한다: 질문을 받자마자 규칙이나 파서
LLM이 도구 목록을 정하고, 그 도구들을 병렬로 돌린 뒤 서술 모델은 이미 나온
숫자만 받는다. 서술 모델은 결과를 보고 "이건 X도 필요하다"고 말할 수 없다.

여기서는 반대로 한다:

    질문 → (모델) 어떤 도구를 어떤 인자로? → 실행 → 관찰을 모델에 되먹임
         → (모델) 더 필요하면 또 부르고, 됐으면 답을 쓴다

왜 이 구조가 필요한가(소유자 지적): "얘는 답변의 질이 떨어지고, 자기가 모르는
걸 못 말한다." 한 번에 결정하는 구조에서는 처음 고른 도구가 어긋나면 끝까지
어긋난 채 답이 나가고, 모델은 무엇이 없어서 못 답하는지도 알 수 없다. 결과를
보고 다시 고를 수 있으면 그 둘이 동시에 풀린다.

설계 원칙 셋:

1. **숫자는 여전히 엔진이 낸다.** 모델은 "어떤 도구를 부를지"와 "어떻게 설명할지"
   만 정한다. 관찰 텍스트는 엔진 결과를 그대로 옮긴 것이고, 모델이 새 숫자를
   지어내면 안 된다고 프롬프트에서 못 박는다.
2. **예산을 하드로 둔다.** 라운드 수와 벽시계 예산을 넘기면 그 자리에서 멈추고,
   그때까지 모은 관찰로 답을 쓰게 한다. 루프가 무한히 도는 일은 없다.
3. **실패하면 기존 경로로 돌아간다.** 모델이 없거나(로컬 LLM·키 없음) 도구
   호출을 지원하지 않으면 None을 돌려주고, 라우터는 종전 파이프라인을 그대로
   쓴다. 이 파일이 죽어도 서비스는 죽지 않는다.

라우터가 도구 실행 함수를 주입한다(순환 import 회피). 이 모듈은 어떤 도구가
있는지 목록으로만 알고, 실행 방법은 모른다.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from app.services.llm_client import AgentConversation, ToolCall, ToolTurn, llm_enabled

logger = logging.getLogger(__name__)

# 라운드 = 모델 호출 횟수. 2면 "도구 부르고 → 결과 보고 한 번 더 고칠 기회 →
# 답 쓰기"가 된다. 3 이상은 지연이 붙는 만큼의 이득이 관찰되지 않았다.
MAX_ROUNDS = 3
# 벽시계 예산. 서술은 이 안에서 끝나야 한다 — 넘으면 모은 관찰로 답을 쓴다.
BUDGET_S = 45.0
# 한 라운드에 실행할 도구 수 상한. 모델이 열 개를 부르면 예산이 통째로 날아간다.
MAX_CALLS_PER_ROUND = 3
# 한 턴의 출력 상한(추론 토큰 포함). 1800이면 추론 모델이 생각에 다 쓰고 답이 잘렸다.
TURN_MAX_TOKENS = 4000
TURN_TIMEOUT_S = 30.0


@dataclass
class Observation:
    """도구 실행 결과 한 건. facts는 모델에게 보여 줄 텍스트다."""

    facts: str
    # 라우터가 응답(site·tool_results)을 조립하는 데 쓰는 원본. 모델에게는 안 간다.
    payload: Any = None
    error: str = ""


@dataclass
class LoopResult:
    answer: str
    observations: list[Observation] = field(default_factory=list)
    rounds: int = 0
    # 도구를 한 번도 부르지 않고 답했으면 True — 라우터가 mode="talk"로 낸다.
    talk_only: bool = False


AnalyzeFn = Callable[[dict[str, Any]], Awaitable[Observation]]


def _tool_schema(tool_names: list[str], tool_labels: dict[str, str]) -> list[dict[str, Any]]:
    """모델에게 보여 줄 도구 정의.

    도구를 스무 개 낱개로 노출하지 않고 `analyze` 하나로 묶는다. 실제 도구들은
    전부 "이 지점 · 이 업종 · 이 값들"이라는 같은 입력을 받으므로, 낱개로 펼치면
    같은 인자를 스무 번 적는 스키마가 되고 모델이 고를 것도 많아진다. 어떤 판정을
    원하는지는 tools 배열로 고르게 한다.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "analyze",
                "description": (
                    "서울의 한 지점을 엔진으로 분석한다. 장소·업종을 주면 입지 점수를 "
                    "계산하고, tools에 적은 판정을 함께 돌려 결과를 텍스트로 돌려준다. "
                    "결과가 부족하면 다른 tools나 다른 장소로 한 번 더 부를 수 있다."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "place": {
                            "type": "string",
                            "description": (
                                "분석 중심 지점. **사용자가 부른 표현을 그대로** 넣는다. "
                                "지명·역·행정동·주소뿐 아니라 상호·지점명·건물명도 된다 — "
                                "예: '신촌역', '성수동', '서초구 반포동', '신촌 샐러디', "
                                "'강남역 스타벅스', '롯데월드타워'. "
                                "사용자가 특정 점포·건물을 기준으로 물었으면 그 이름을 "
                                "행정동·역 이름으로 바꾸지 마라 — 행정동을 넣으면 중심이 "
                                "그 동네의 대표점(상권 중심)으로 잡혀 사용자가 물은 자리에서 "
                                "수백 m 벗어난다."
                            ),
                        },
                        "business": {
                            "type": "string",
                            "description": "업종을 한국어 낱말로. 예: '카페', '편의점', '무인 아이스크림'. 모르면 빈 문자열",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string", "enum": tool_names},
                            "description": (
                                "함께 돌릴 판정. 사용 가능한 도구와 뜻:\n"
                                + "\n".join(f"- {name}: {tool_labels.get(name, '')}" for name in tool_names)
                            ),
                        },
                        "params": {
                            "type": "object",
                            "description": (
                                "판정에 필요한 수치(만원·평 단위). 예: "
                                '{"monthly_rent_man": 300, "area_pyeong": 30, "deposit_man": 5000}. '
                                "질문에 없는 값은 넣지 않는다 — 지어내면 판정이 틀린다."
                            ),
                            "additionalProperties": {"type": "number"},
                        },
                        "radius_m": {
                            "type": "integer",
                            "enum": [500, 1000],
                            "description": "분석 반경. 기본 500",
                        },
                    },
                    "required": ["place"],
                },
            },
        }
    ]


def _system_prompt(capabilities: str) -> str:
    """상담 모델의 지시문.

    2026-11-06 개편(소유자 지적: "그냥 GPT에 물어보는 것보다 못하고, 정해진 양식대로만
    답한다"). 종전 지시는 세 가지로 답을 묶어 두고 있었다.

    - 형식 고정: 「결론 / 왜 / 할 일」, 1,200자 — 무엇을 물어도 같은 틀.
    - 일반 지식 금지: "도구가 낸 숫자만 쓴다", "자료에 없으면 확인되지 않는다" — 모델이
      제일 잘하는 창업 실무·업계 관행·체크리스트가 전부 꺼져 있었다.
    - 질문의 종류를 보지 않음: 장소가 있으면 무조건 입지 점수 보고서.

    지금 원칙은 하나만 남긴다: **특정 장소의 숫자는 엔진(analyze) 값만 쓴다.** 나머지는
    좋은 상담가처럼 질문에 맞춰 답하고, 일반 지식은 일반 지식이라고 밝혀서 쓴다.
    """
    return (
        "너는 한국의 상권·창업 입지 상담가다. 소상공인 창업, 상가 임대·매입, 상권 분석 "
        "실무를 오래 해 온 사람처럼 답한다. 이 서비스는 서울의 길 구조(공간구문론)·"
        "생활인구·매출·경쟁 데이터를 계산하는 분석 엔진을 갖고 있고, 너는 그 엔진을 "
        "도구(analyze)로 부를 수 있다.\n\n"
        f"{capabilities}\n\n"
        "## 무엇을 하는가\n"
        "- 사용자가 실제로 알고 싶은 것을 먼저 파악하고 그것에 답한다. 질문이 입지 판단이 "
        "아니면(인테리어, 권리금 협상, 업종 선택, 상권 개념, 창업 절차, 마케팅 등) 그 "
        "질문에 맞게 답한다. 모든 질문을 입지 점수 보고서로 바꾸지 않는다.\n"
        "- 너의 일반 지식(창업 실무, 업계 관행, 상가임대차 제도의 일반론, 업종별 특성, "
        "체크리스트, 사례)은 적극적으로 쓴다. 다만 '일반적으로', '업계에서는'처럼 일반론이라는 "
        "게 드러나게 쓴다.\n"
        "- 서울의 특정 장소·업종에 대한 판단이 필요하면 analyze를 부른다. 질문이 요구하는 "
        "판정을 tools에 함께 적는다(월세를 말했으면 lease_verdict, 매입가면 purchase_verdict, "
        "경쟁을 물으면 real_competition, 뜨는지 물으면 trends, 시간대·유동인구·생활인구·"
        "점심/밤 장사를 물으면 area_insights). 결과가 부족하거나 장소가 "
        "엉뚱하게 잡혔으면 고쳐서 한 번 더 부른다.\n"
        "- **place는 사용자가 부른 그대로 넣는다.** 상호·지점명·건물명을 기준으로 물었으면"
        "('신촌 샐러디집에 카페 차려도 될까?') 그 이름을 그대로 넣는다('신촌 샐러디'). "
        "행정동이나 역 이름으로 바꾸면 분석 중심이 그 자리가 아니라 동네 대표점이 된다. "
        "관찰의 '분석 중심'이 행정동 대표점인데 사용자는 특정 점포를 물었다면, 그 이름으로 "
        "다시 부른다.\n"
        "- 입지 판단에는 길 구조만이 아니라 결과에 있는 유동인구·거주인구·직장인·"
        "연령·외국인·매출·경쟁·시세, 그리고 '주변 상권 동태'(개업·폐업, 상권변화지표, "
        "집객시설, 공실률, 대규모점포, 최근 건축허가, 정비구역, 역 승하차)를 함께 근거로 "
        "쓴다. 질문과 관련 있는 데이터를 골라 쓴다. 이 자리가 정비구역 안이면 꼭 알린다.\n"
        "- 앞 대화가 있으면 이어서 답한다. 같은 장소·업종 소개를 되풀이하지 않고 이번에 "
        "새로 물은 것에 답한다.\n\n"
        "## 숫자와 사실\n"
        "- 특정 장소의 수치(점수·인구·매출·경쟁 점포 수·임대료·지가)는 analyze 결과에 "
        "있는 것만 쓴다. 지어내지 않는다. 엔진을 부르지 않았으면 그 장소의 수치를 말하지 "
        "않는다.\n"
        "- 널리 알려진 일반 기준(예: 매출 대비 임대료 비중의 통상 범위)은 일반론으로 밝혀서 "
        "쓸 수 있다. 특정 장소의 값처럼 쓰지 않는다.\n"
        "- 결과에 '추정치'·'행정동 평균'·'표본' 같은 정밀도 표시가 있으면 문장에 남긴다. "
        "'경쟁 수준 판단 불가'라고 적혀 있으면 경쟁이 낮다고 쓰지 않는다. 내부 점수(N점)는 "
        "인용하지 않고 '상위 N%' 같은 비교로 말한다.\n"
        "- 엔진으로 확인되지 않는 것은 짧게 그렇다고 밝히고, 거기서 멈추지 말고 일반 지식으로 "
        "도울 수 있는 만큼 돕고 어떻게 확인하면 되는지 알려 준다.\n"
        "- 서울 밖 지역은 엔진에 데이터가 없다. 그 사실을 밝히고 일반 지식으로 답한다.\n"
        "- 투자·계약을 확정적으로 권하지 않는다. 판단 근거와 위험을 함께 준다.\n\n"
        "## 어떻게 쓰는가\n"
        "- 한국어 존댓말, 자연스러운 대화체. 첫 문장에서 바로 답한다(서론·질문 반복 없이).\n"
        "- 형식은 질문에 맞춘다. 간단한 질문은 몇 문장으로, 비교는 표로, 절차는 번호 목록으로, "
        "깊은 분석 요청은 소제목을 나눠 충분히. 정해진 틀을 매번 쓰지 않는다.\n"
        "- 소제목이 필요하면 한 줄을 **굵게** 쓴다(# 제목은 쓰지 않는다). 문단 사이는 빈 줄.\n"
        "- 핵심 정보가 빠져 답이 크게 달라지면, 합리적인 가정을 밝히고 답한 뒤 마지막에 "
        "꼭 필요한 것 하나만 되묻는다."
    )


@dataclass
class LoopEvent:
    """스트리밍 상담의 이벤트.

    type:
      - "status": 지금 하는 일(text) — 예: "신촌역 · 카페 분석 중"
      - "delta": 답 글자 조각(text)
      - "reset": 지금까지 흘린 글을 지운다(모델이 말을 꺼냈다가 도구를 부르기로 바꿨을 때)
      - "done": 끝. result가 None이면 이 경로를 쓸 수 없었다(호출부가 폴백).
    """

    type: str
    text: str = ""
    result: LoopResult | None = None


def _status_text(args: dict[str, Any]) -> str:
    place = str(args.get("place") or "").strip()
    business = str(args.get("business") or "").strip()
    target = " · ".join(p for p in (place, business) if p)
    return f"{target} 분석 중" if target else "분석 중"


async def stream_agent_loop(
    question: str,
    *,
    analyze: AnalyzeFn,
    tool_names: list[str],
    tool_labels: dict[str, str],
    capabilities: str,
    history: list[dict[str, str]] | None = None,
    history_summary: str = "",
    max_rounds: int = MAX_ROUNDS,
    budget_s: float = BUDGET_S,
) -> AsyncIterator[LoopEvent]:
    """모델이 도구를 골라 돌리고 답을 글자 단위로 흘린다.

    history는 앞 대화를 {"role", "content"}로 오래된 순으로 담는다(질문과 답 본문).
    history_summary는 history가 없을 때만 쓰는 종전 요약(장소·업종)이다.
    """
    q = (question or "").strip()
    if not q or not llm_enabled():
        yield LoopEvent(type="done", result=None)
        return

    started = time.monotonic()
    schema = _tool_schema(tool_names, tool_labels)
    conversation = AgentConversation(system=_system_prompt(capabilities), history=list(history or []))
    if history or not history_summary:
        conversation.add_user(q)
    else:
        conversation.add_user(f"앞 대화: {history_summary}\n\n질문: {q}")
    observations: list[Observation] = []
    rounds = 0
    streamed = False

    while rounds < max_rounds:
        left = budget_s - (time.monotonic() - started)
        if left <= 5.0:
            break
        turn: ToolTurn | None = None
        failed = False
        round_text: list[str] = []
        async for event in conversation.stream_turn(
            tools=schema, max_tokens=TURN_MAX_TOKENS, timeout_s=min(TURN_TIMEOUT_S, left)
        ):
            if event.kind == "delta":
                streamed = True
                round_text.append(event.text)
                yield LoopEvent(type="delta", text=event.text)
            elif event.kind == "turn":
                turn = event.turn
            elif event.kind == "error":
                failed = True
        rounds += 1
        if failed or turn is None:
            # 글을 이미 흘렸으면 그걸 답으로 살린다. 아니면 이 경로를 포기하거나,
            # 도구를 이미 돌렸다면 그 관찰로 마지막 답을 쓰러 나간다.
            if streamed:
                yield LoopEvent(type="done", result=_finish("".join(round_text).strip(), observations, rounds))
                return
            if not observations:
                yield LoopEvent(type="done", result=None)
                return
            break
        if not turn.tool_calls:
            text = (turn.text or "").strip()
            if not text and not observations:
                yield LoopEvent(type="done", result=None)
                return
            yield LoopEvent(type="done", result=_finish(text, observations, rounds))
            return

        if streamed:
            # 도구를 부르기 전에 꺼낸 말("확인해 볼게요")은 답이 아니다.
            streamed = False
            yield LoopEvent(type="reset")
        outputs: list[tuple[str, str]] = []
        for call in turn.tool_calls[:MAX_CALLS_PER_ROUND]:
            yield LoopEvent(type="status", text=_status_text(call.arguments))
            obs = await _run_call(call, analyze)
            observations.append(obs)
            outputs.append((call.id, obs.facts if not obs.error else f"실행 실패: {obs.error}"))
        for call in turn.tool_calls[MAX_CALLS_PER_ROUND:]:
            outputs.append((call.id, "한 번에 돌릴 수 있는 도구 수를 넘겨 실행하지 않았다."))
        conversation.add_tool_outputs(turn, outputs)

    # 라운드나 예산이 끝났다 — 도구 없이 마지막으로 답만 쓰게 한다.
    if not observations:
        yield LoopEvent(type="done", result=None)
        return
    yield LoopEvent(type="status", text="답변 쓰는 중")
    conversation.add_user("위 결과로 답을 써라. 도구는 더 부르지 않는다.")
    final_text: list[str] = []
    async for event in conversation.stream_turn(
        tools=[],
        max_tokens=TURN_MAX_TOKENS,
        timeout_s=max(10.0, min(TURN_TIMEOUT_S, budget_s - (time.monotonic() - started))),
    ):
        if event.kind == "delta":
            final_text.append(event.text)
            yield LoopEvent(type="delta", text=event.text)
        elif event.kind == "turn" and event.turn and event.turn.text and not final_text:
            final_text.append(event.turn.text)
    yield LoopEvent(type="done", result=_finish("".join(final_text).strip(), observations, rounds))


async def run_agent_loop(
    question: str,
    *,
    analyze: AnalyzeFn,
    tool_names: list[str],
    tool_labels: dict[str, str],
    capabilities: str,
    history: list[dict[str, str]] | None = None,
    history_summary: str = "",
    max_rounds: int = MAX_ROUNDS,
    budget_s: float = BUDGET_S,
) -> LoopResult | None:
    """모델이 도구를 골라 돌리고 답을 쓴다. 쓸 수 없으면 None(라우터가 폴백).

    stream_agent_loop을 끝까지 받아 한 번에 돌려준다(비스트리밍 /api/agent/ask용).
    """
    text: list[str] = []
    result: LoopResult | None = None
    async for event in stream_agent_loop(
        question,
        analyze=analyze,
        tool_names=tool_names,
        tool_labels=tool_labels,
        capabilities=capabilities,
        history=history,
        history_summary=history_summary,
        max_rounds=max_rounds,
        budget_s=budget_s,
    ):
        if event.type == "delta":
            text.append(event.text)
        elif event.type == "reset":
            text.clear()
        elif event.type == "done":
            result = event.result
    if result is None:
        joined = "".join(text).strip()
        # 글은 나왔는데 턴이 깨끗이 안 끝났다 — 쓴 데까지 살린다.
        return LoopResult(answer=joined, talk_only=True) if joined else None
    if not result.answer:
        result.answer = "".join(text).strip()
    return result


def _finish(text: str, observations: list[Observation], rounds: int) -> LoopResult:
    used = [o for o in observations if not o.error]
    return LoopResult(
        answer=text,
        observations=observations,
        rounds=rounds,
        talk_only=not used,
    )


async def _run_call(call: ToolCall, analyze: AnalyzeFn) -> Observation:
    if call.name != "analyze":
        return Observation(facts="", error=f"모르는 도구: {call.name}")
    try:
        return await analyze(call.arguments)
    except Exception as exc:  # noqa: BLE001 - 도구 실패가 루프를 죽이면 안 된다
        logger.warning("agent_loop: analyze 실패 args=%s — %s", json.dumps(call.arguments, ensure_ascii=False), exc)
        return Observation(facts="", error=str(exc)[:200])
