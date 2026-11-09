"""AI 상담 답변 비교 — 「그냥 GPT」 대 「우리 앱」을 같은 질문으로 나란히 본다.

2026-11-06 소유자 지적: "그냥 GPT에 물어보는 것보다 답을 못한다, 정해진 틀로만 답한다."
고친 뒤에 실제로 나아졌는지 눈으로 견주려고 만든 도구다. 숫자를 매기지 않는다 —
사람이 읽고 판단한다.

    # 노트북에서(OPENAI_API_KEY가 .env에 있어야 한다)
    cd backend
    python tools/eval_agent_answers.py                      # 라이브 서버와 비교
    python tools/eval_agent_answers.py --base http://localhost:8000
    python tools/eval_agent_answers.py --model gpt-6.1-terra --only 3

결과: `tools/out/agent_eval_<시각>.md` — 질문마다 두 답과 걸린 시간을 나란히 적는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

# 윈도우 콘솔은 기본 코드페이지가 cp1252라 한글 질문을 print하면 UnicodeEncodeError로
# 죽는다(2026-11-06 실측). 진행 표시 한 줄 때문에 비교 전체를 날리지 않는다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

LIVE_BASE = "https://space-syntax-production.up.railway.app"

# 일부러 섞었다 — 입지 판단만이 아니라 개념·실무·후속 질문·서울 밖·조건부까지.
QUESTIONS: list[str] = [
    "신촌역 근처에 카페 열어도 괜찮을까?",
    "망원동에서 디저트 카페 하려는데 평일 낮 장사가 될까, 주말 위주일까?",
    "성수동 30평 월세 400이면 계약해도 될까?",
    "강남역이랑 역삼역 중에 샐러드 가게 차리기 어디가 나아?",
    "연남동 외국인 손님 많은 편이야? 외국인 상대로 뭘 팔면 좋을까?",
    "권리금 협상은 어떻게 해야 돼?",
    "상가 임대차보호법에서 계약갱신요구권이 뭐야?",
    "공시지가랑 실거래가 차이가 뭐야?",
    "편의점 창업할 때 본사 고를 때 뭘 봐야 해?",
    "부산 서면에 술집 차리면 어때?",
    "이 동네 밤 11시 이후에도 사람 다녀? 홍대입구역 근처 국밥집 생각 중이야",
    "건대입구역 근처 무인 아이스크림 가게 경쟁 심해?",
]

GPT_SYSTEM = "너는 친절한 한국어 어시스턴트다."


async def ask_plain_gpt(client: httpx.AsyncClient, model: str, question: str) -> tuple[str, float]:
    """도구 없이 같은 모델에 그대로 묻는다 — 「그냥 GPT」 기준선."""
    started = time.monotonic()
    res = await client.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={"model": model, "instructions": GPT_SYSTEM, "input": question, "max_output_tokens": 4000},
        timeout=120,
    )
    elapsed = time.monotonic() - started
    if res.status_code != 200:
        return f"(실패 {res.status_code}: {res.text[:300]})", elapsed
    data = res.json()
    texts = [
        part.get("text", "")
        for item in data.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ]
    return "".join(texts).strip() or "(빈 응답)", elapsed


async def ask_our_app(client: httpx.AsyncClient, base: str, question: str) -> tuple[str, float, float | None, list[str]]:
    """/api/agent/ask/stream으로 묻는다. (답, 전체 시간, 첫 글자까지 시간, 상태 줄들)."""
    started = time.monotonic()
    first: float | None = None
    text = ""
    statuses: list[str] = []
    final_answer = ""
    headers = {"Content-Type": "application/json"}
    if settings.api_access_token:
        headers["X-API-Key"] = settings.api_access_token
    async with client.stream(
        "POST", f"{base}/api/agent/ask/stream", json={"query": question}, headers=headers, timeout=120
    ) as res:
        if res.status_code != 200:
            body = await res.aread()
            return f"(실패 {res.status_code}: {body[:300]!r})", time.monotonic() - started, None, statuses
        async for line in res.aiter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            kind = event.get("type")
            if kind == "delta":
                if first is None:
                    first = time.monotonic() - started
                text += event.get("text", "")
            elif kind == "reset":
                text = ""
            elif kind == "status":
                statuses.append(event.get("text", ""))
            elif kind == "done":
                if event.get("fallback"):
                    statuses.append("⚠️ 스트림 경로 실패 — 화면은 종전 짧은 답으로 대신 씀")
                final_answer = ((event.get("response") or {}).get("answer") or "").strip()
    return (text.strip() or final_answer or "(빈 응답)"), time.monotonic() - started, first, statuses


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=LIVE_BASE, help="우리 백엔드 주소")
    parser.add_argument(
        "--model",
        default=(settings.openai_agent_model or "gpt-6.1-sol"),
        help="「그냥 GPT」에 쓸 모델(기본: 우리 상담 모델과 같게)",
    )
    parser.add_argument("--only", type=int, default=0, help="앞에서 N개만")
    args = parser.parse_args()
    if not settings.openai_api_key:
        sys.exit("OPENAI_API_KEY가 없습니다 — backend/.env를 확인하세요.")

    questions = QUESTIONS[: args.only] if args.only else QUESTIONS
    out_dir = Path(__file__).resolve().parent / "out"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"agent_eval_{datetime.now():%Y%m%d_%H%M}.md"
    lines = [
        f"# AI 상담 비교 — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- 그냥 GPT: `{args.model}` (도구 없음)",
        f"- 우리 앱: `{args.base}/api/agent/ask/stream`",
        "",
    ]
    async with httpx.AsyncClient() as client:
        for i, q in enumerate(questions, 1):
            print(f"[{i}/{len(questions)}] {q}")
            (gpt, gpt_s), (ours, ours_s, first_s, statuses) = await asyncio.gather(
                ask_plain_gpt(client, args.model, q), ask_our_app(client, args.base, q)
            )
            first_txt = f", 첫 글자 {first_s:.1f}초" if first_s is not None else ""
            lines += [
                f"## {i}. {q}",
                "",
                f"### 그냥 GPT ({gpt_s:.1f}초)",
                "",
                gpt,
                "",
                f"### 우리 앱 ({ours_s:.1f}초{first_txt})",
                "",
                *([f"> 진행: {' → '.join(s for s in statuses if s)}", ""] if statuses else []),
                ours,
                "",
                "---",
                "",
            ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {out}")


if __name__ == "__main__":
    asyncio.run(main())
