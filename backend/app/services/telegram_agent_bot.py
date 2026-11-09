"""Telegram interface for the customer-facing commercial-area analysis Agent.

This is deliberately separate from ``tools/agent/telegram_bot.py``.  That bot
controls the autonomous *development runner*; this module lets an end user ask
the already-built ``/api/agent/ask`` product questions without opening the web
UI.  It runs as a daemon thread in the existing Railway backend, so it does not
need another container or another LLM call path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
HISTORY_LIMIT = 5
API = "https://api.telegram.org/bot{token}/{method}"

HELP = """상권분석 AI Agent입니다.

지역과 업종을 평소 말하듯 물어보세요.
예) 신촌 카페 상권 분석해줘
예) 여기 월세 300이면 계약해도 될까?
예) 홍대와 합정 중 어디가 나아?

앞 대화를 이어서 이해합니다.
/new  새 분석 시작
/help  사용법"""

AskCallback = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass
class ChatState:
    """Small per-chat context needed by ``AgentAskRequest`` follow-up turns."""

    history: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] | None = None


def allowed_ids(raw: str | None) -> set[int]:
    """Parse stable Telegram numeric IDs. Empty means nobody, not everybody."""

    out: set[int] = set()
    for chunk in (raw or "").replace(";", ",").split(","):
        value = chunk.strip()
        if value.lstrip("-").isdigit():
            out.add(int(value))
    return out


def is_authorized(user_id: Any, allowed: set[int]) -> bool:
    if not allowed:
        return False
    try:
        return int(user_id) in allowed
    except (TypeError, ValueError):
        return False


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError("Agent response is not serializable")


def analysis_request(query: str, state: ChatState) -> dict[str, Any]:
    request: dict[str, Any] = {
        "query": query,
        "include_answer": True,
        "history": state.history[-HISTORY_LIMIT:],
    }
    if state.context:
        request["context"] = state.context
    return request


def remember_response(state: ChatState, response: Any) -> None:
    data = _as_dict(response)
    state.history.append(
        {
            "query": data.get("query", ""),
            "address": data.get("address", ""),
            "business_type": data.get("business_type", "general"),
            "upjong": data.get("upjong"),
            "params": data.get("parameters") or {},
            # The backend model calls this field ``params``. Keep the exact API
            # contract rather than copying the frontend's older type alias.
        }
    )
    state.history = state.history[-HISTORY_LIMIT:]
    state.context = data.get("pending")


def _number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    return f"{value:,.1f}".rstrip("0").rstrip(".")


def _fact_line(item: dict[str, Any]) -> str:
    label = str(item.get("label") or item.get("name") or "지표")
    facts: list[str] = []
    score = _number(item.get("score"))
    if score:
        facts.append(f"{score}점")
    display = str(item.get("display") or "").strip()
    if display:
        facts.append(display)
    detail = str(item.get("detail") or "").strip()
    if detail:
        facts.append(detail)
    return label + (": " + " · ".join(facts) if facts else "")


def _list_lines(label: str, values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    clean = [str(value).strip() for value in values if str(value).strip()]
    if not clean:
        return []
    return [f"- {label}: {value}" for value in clean]


def _tool_report(tool: str, result: Any) -> list[str]:
    """Render the same primary tool card the web Agent displays, as text."""

    if not tool or tool == "site_score" or not isinstance(result, dict):
        return []
    lines = ["🔎 실행 도구 결과"]
    headline = str(result.get("headline") or result.get("summary") or "").strip()
    if headline:
        lines.append(headline)
    verdict = str(result.get("verdict_label") or result.get("verdict") or "").strip()
    if verdict:
        lines.append(f"판정: {verdict}")

    scalar_fields = (
        ("recommend_score", "추천도", "점"),
        ("expected_monthly_sales_man", "예상 월매출", "만원"),
        ("max_acceptable_rent_man", "적정 월세 상한", "만원"),
        ("rent_gap_man", "월세 차이", "만원"),
        ("break_even_monthly_man", "손익분기 월매출", "만원"),
        ("monthly_operating_profit_man", "월 영업이익", "만원"),
        ("cap_rate_pct", "연 수익률", "%"),
        ("dscr", "대출 감당력", "배"),
        ("payback_years", "원금 회수", "년"),
        ("price_premium_pct", "시세 대비 가격", "%"),
    )
    for key, label, unit in scalar_fields:
        value = _number(result.get(key))
        if value:
            lines.append(f"{label}: {value}{unit}")

    rent_low = _number(result.get("appropriate_rent_low_man"))
    rent_high = _number(result.get("appropriate_rent_high_man"))
    if rent_low or rent_high:
        lines.append(f"적정 월세: {rent_low or '?'}~{rent_high or '?'}만원")

    ranked = result.get("ranked")
    if isinstance(ranked, list):
        for index, item in enumerate(ranked[:5], 1):
            if not isinstance(item, dict):
                continue
            name = item.get("label") or item.get("name") or item.get("address") or f"후보 {index}"
            rank = item.get("rank") or index
            score = _number(item.get("recommend_score") or item.get("score"))
            decision = item.get("verdict_label") or item.get("verdict") or ""
            tail = " · ".join(part for part in (f"{score}점" if score else "", str(decision)) if part)
            lines.append(f"{rank}위 {name}" + (f" — {tail}" if tail else ""))

    candidates = result.get("candidates")
    if isinstance(candidates, list):
        for index, item in enumerate(candidates[:5], 1):
            if not isinstance(item, dict):
                continue
            name = item.get("label") or item.get("address") or f"후보 {index}"
            score = _number(item.get("recommend_score"))
            decision = str(item.get("verdict_label") or item.get("verdict") or "").strip()
            tail = " · ".join(part for part in (f"{score}점" if score else "", decision) if part)
            lines.append(f"{index}. {name}" + (f" — {tail}" if tail else ""))
            sales = _number(item.get("expected_monthly_sales_man"))
            rent = _number(item.get("max_acceptable_rent_man"))
            if sales:
                lines.append(f"   예상 월매출 {sales}만원")
            if rent:
                lines.append(f"   적정 월세 상한 {rent}만원")
            lines += [f"   {line}" for line in _list_lines("강점", item.get("strengths"))[:2]]
            lines += [f"   {line}" for line in _list_lines("위험", item.get("risks"))[:2]]

    lines += _list_lines("강점", result.get("strengths"))
    lines += _list_lines("위험", result.get("risks"))
    lines += _list_lines("다음 행동", result.get("actions"))
    return lines if len(lines) > 1 else []


def format_analysis(response: Any) -> str:
    """Render the web Agent's answer, tool card, evidence, and follow-ups."""

    data = _as_dict(response)
    site = data.get("site") or {}
    place = str(data.get("address") or "분석 지역")
    business = str(data.get("business_label") or "상권")
    lines = [f"📍 {place} · {business}"]

    steps = [str(step.get("label") or "").strip() for step in data.get("steps") or [] if isinstance(step, dict)]
    steps = [step for step in steps if step]
    if steps:
        lines.append("실행한 분석: " + " → ".join(steps))

    score = _number(site.get("total_score"))
    grade = str(site.get("grade") or "").strip()
    if score or grade:
        score_line = " · ".join(part for part in (f"{score}점" if score else "", grade) if part)
        lines.append(f"종합 평가: {score_line}")

    notice = str(data.get("notice") or "").strip()
    if notice:
        lines.extend(["", f"⚠️ {notice}"])

    answer = str(data.get("answer") or "").strip()
    verdict = str(site.get("verdict_one_liner") or "").strip()
    if verdict and verdict not in answer:
        lines.extend(["", verdict])
    if answer:
        lines.extend(["", answer])

    tool_lines = _tool_report(str(data.get("tool") or ""), data.get("tool_result"))
    if tool_lines:
        lines.extend([""] + tool_lines)

    subscores = site.get("subscores")
    if isinstance(subscores, list) and subscores:
        lines.extend(["", "📊 세부 점수"])
        lines += [f"- {_fact_line(item)}" for item in subscores if isinstance(item, dict)]

    kpis = site.get("industry_kpis")
    visible_kpis = [item for item in (kpis or []) if isinstance(item, dict) and item.get("score") is not None]
    if visible_kpis:
        lines.extend(["", "🏪 업종 핵심 지표"])
        lines += [f"- {_fact_line(item)}" for item in visible_kpis]

    warnings: list[str] = []
    for source in (site.get("warnings"), (data.get("tool_result") or {}).get("warnings") if isinstance(data.get("tool_result"), dict) else None):
        if isinstance(source, list):
            for warning in source:
                text = str(warning).strip()
                if text and text not in warnings:
                    warnings.append(text)
    if warnings:
        lines.extend(["", "⚠️ 데이터·분석 주의"])
        lines += [f"- {warning}" for warning in warnings]

    if data.get("pending"):
        lines.extend(["", "필요한 값을 이어서 답해 주시면 같은 분석을 계속합니다."])

    suggestions = [str(q).strip() for q in data.get("suggested_questions") or [] if str(q).strip()]
    if suggestions:
        lines.extend(["", "💬 이어서 물어보기"])
        lines += [f"- {question}" for question in suggestions]

    lines.extend(
        [
            "",
            "🏢 네이버 지도에서 상가·매물 확인",
            "https://map.naver.com/p/search/" + urllib.parse.quote(f"{place} 상가임대"),
        ]
    )
    return "\n".join(lines).strip()


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split without dropping characters; prefer human-readable boundaries."""

    if limit < 1:
        raise ValueError("limit must be positive")
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = max(rest.rfind("\n\n", 0, limit + 1), rest.rfind("\n", 0, limit + 1))
        if cut <= 0:
            cut = rest.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        else:
            # Preserve the boundary in the preceding chunk so concatenation is
            # byte-for-byte identical and no sentence is silently joined.
            boundary = 2 if rest[cut:cut + 2] == "\n\n" else 1
            cut += boundary
        chunks.append(rest[:cut])
        rest = rest[cut:]
    if rest or not chunks:
        chunks.append(rest)
    return chunks


async def handle_text(text: str, state: ChatState, ask: AskCallback) -> str:
    stripped = (text or "").strip()
    command = stripped.partition(" ")[0].split("@")[0].lower()
    if command in {"/start", "/help"}:
        return HELP
    if command in {"/new", "/reset"}:
        state.history.clear()
        state.context = None
        return "새 대화를 시작했습니다. 지역과 업종을 알려주세요."
    if not stripped:
        return "지역과 업종을 함께 적어 주세요. 예: 신촌 카페 상권 분석해줘"

    response = await ask(analysis_request(stripped, state))
    remember_response(state, response)
    return format_analysis(response)


def _api(token: str, method: str, params: dict[str, Any], *, timeout: int = 40) -> dict[str, Any] | None:
    body = urllib.parse.urlencode(
        {
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            for key, value in params.items()
            if value is not None
        }
    ).encode()
    try:
        with urllib.request.urlopen(API.format(token=token, method=method), data=body, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode())
        except (ValueError, OSError):
            payload = {}
        logger.warning("Telegram %s rejected: %s", method, payload.get("description") or exc)
        return payload or None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Telegram %s failed: %s", method, exc)
        return None


def _send(token: str, chat_id: int, text: str) -> None:
    for chunk in split_message(text):
        _api(
            token,
            "sendMessage",
            {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
            timeout=20,
        )


def start_if_configured(
    ask: AskCallback,
    *,
    thread_factory: Callable[..., Any] = threading.Thread,
) -> bool:
    """Start the poller beside FastAPI only when its dedicated token exists."""

    if not os.getenv("TELEGRAM_AGENT_BOT_TOKEN", "").strip():
        logger.info("Telegram analysis Agent disabled (TELEGRAM_AGENT_BOT_TOKEN unset)")
        return False
    thread = thread_factory(target=run_bot, args=(ask,), daemon=True, name="telegram-analysis-agent")
    thread.start()
    return True


def run_bot(ask: AskCallback) -> None:
    """Long-poll Telegram and pass authorized text into the product Agent."""

    token = os.getenv("TELEGRAM_AGENT_BOT_TOKEN", "").strip()
    allowed = allowed_ids(os.getenv("TELEGRAM_AGENT_ALLOWED_IDS"))
    if not token:
        logger.info("Telegram analysis Agent disabled (TELEGRAM_AGENT_BOT_TOKEN unset)")
        return
    if not allowed:
        logger.error("Telegram analysis Agent has no TELEGRAM_AGENT_ALLOWED_IDS; refusing all users")

    states: dict[int, ChatState] = {}
    offset: int | None = None
    logger.info("Telegram analysis Agent started for %d allowed user(s)", len(allowed))

    while True:
        updates = _api(token, "getUpdates", {"offset": offset, "timeout": 25}, timeout=40)
        if not updates or not updates.get("ok"):
            retry_after = ((updates or {}).get("parameters") or {}).get("retry_after")
            time.sleep(max(5, int(retry_after or 5)))
            continue

        for update in updates.get("result", []):
            offset = int(update["update_id"]) + 1
            message = update.get("message") or update.get("edited_message") or {}
            chat_id = (message.get("chat") or {}).get("id")
            user_id = (message.get("from") or {}).get("id")
            text = message.get("text")
            if chat_id is None or text is None or not is_authorized(user_id, allowed):
                continue

            _api(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
            try:
                reply = asyncio.run(handle_text(text, states.setdefault(int(chat_id), ChatState()), ask))
            except Exception as exc:  # one request must never kill the bot loop
                detail = getattr(exc, "detail", None)
                logger.exception("Telegram analysis request failed")
                reply = f"분석하지 못했습니다: {detail or str(exc) or '잠시 후 다시 시도해 주세요.'}"
            _send(token, int(chat_id), reply)
