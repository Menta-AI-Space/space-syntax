"""화면 글자 영어 번역 (2026-11-09) — 영어 기본 화면의 자동 번역층.

소유자: 「영어가 기본」. 화면 코드·서버 문장·AI 답이 모두 한국어로 쓰여 있어서, 문장마다 손으로 영어판을
붙이면 몇 시간이 걸린다. 그래서 영어 모드에선 프론트(`lib/autoTranslate.ts`)가 화면에 뜬 한국어 글 조각을
모아 여기로 보내고, 여기서 GPT로 옮겨 돌려준다. 한 번 옮긴 문장은 서버 메모리에 남겨 다음부터는 바로 준다
(프론트도 브라우저에 따로 남긴다). 한국어 모드(`?lang=ko`)는 이 길을 전혀 안 탄다.

숫자·단위·기호는 그대로 두고, 이 서비스 고유 낱말은 아래 용어집으로 고정한다 — 같은 말이 화면마다 다르게
옮겨지면 사용자가 다른 것으로 읽는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict

from app.services.llm_client import complete_json

logger = logging.getLogger(__name__)

MAX_CACHE = 50_000
CHUNK = 40
_cache: "OrderedDict[str, str]" = OrderedDict()

SYSTEM_PROMPT = """You translate Korean UI text of a web app into natural, concise English for an international (YC-style) audience.
The app analyzes building floor plans, new-town site plans and city streets with Space Syntax to find where people naturally gather.
Rules:
- Return JSON: {"translations": [...]} with exactly one English string per input, same order.
- Keep numbers, units (m, ㎡→m², %), symbols (· — → ✓ ★), emoji, punctuation style, and leading/trailing spaces as in the source. Keep line breaks.
- Korean quotation marks 「」 become “ ”. Keep it short — this is UI copy, not prose. Use sentence case.
- Proper nouns of places: romanize (신촌역 → Sinchon Station, 운정신도시 → Unjeong New Town, 강남구 → Gangnam-gu).
- If a piece is already English or is only a name/code, return it unchanged.
Glossary (always use these):
목좋은곳 (product name) → Prime Spot
목 좋은 자리 / 목 좋은 곳 / 목이 좋은 → high-footfall location (short form: high-footfall spot) — owner's choice 2026-11-09, never "prime spot" except in the product name; 목 (as a score) → footfall potential
통합도 → Integration; 국소 통합도 → Local integration; 선택도 → Choice; 연결도 → Connectivity; 공간구문론 → Space Syntax
고른 자리 / 고른 곳 → selected spot (고른 = "selected/picked", never "accessible"); 지나가는 길목 → pass-through spot; 외진 → secluded; 막다른 → dead-end; 시야 → visibility
건축 평면도 → floor plan; 신도시 계획도면 / 계획도면 → new town plan; 도면 → plan; 획지 / 필지 → lot / parcel; 지적도 → cadastral map
매장 N → Store N; N번 매장 → Store N; 호실 → unit; 광장 → plaza; 통로 → corridor; 출입구 → entrance; 비상구 → emergency exit; 계단 → stairs
벽 → wall; 문 → door; 문 내기 / 문 뚫기 → add a door; 벽 그리기 → draw wall; 지우개 → eraser; 되돌리기 → undo; 말로 고치기 → Edit by voice
AI 입지분석 → AI Site Analysis; 업종 → business type; 상권 → trade area; 공시지가 → official land price
"""


def _remember(src: str, dst: str) -> None:
    _cache[src] = dst
    _cache.move_to_end(src)
    while len(_cache) > MAX_CACHE:
        _cache.popitem(last=False)


async def _translate_chunk(chunk: list[str]) -> dict[str, str]:
    raw = await complete_json(
        system=SYSTEM_PROMPT,
        user=json.dumps({"texts": chunk}, ensure_ascii=False),
        max_tokens=6000,
        timeout_s=30.0,
    )
    got: dict[str, str] = {}
    items = raw.get("translations") if isinstance(raw, dict) else None
    if not isinstance(items, list) or len(items) != len(chunk):
        logger.warning("화면 번역: 응답 개수 불일치 (%s / %s)", len(items) if isinstance(items, list) else None, len(chunk))
        return got
    for src, dst in zip(chunk, items):
        if isinstance(dst, str) and dst.strip():
            got[src] = dst
    return got


async def translate_texts(texts: list[str]) -> list[str | None]:
    """한국어 조각 → 영어. 옮기지 못한 자리는 None(프론트가 한국어로 두고 나중에 다시 묻는다)."""
    todo: list[str] = []
    seen: set[str] = set()
    for t in texts:
        if t not in _cache and t not in seen:
            seen.add(t)
            todo.append(t)
    if todo:
        chunks = [todo[i : i + CHUNK] for i in range(0, len(todo), CHUNK)]
        results = await asyncio.gather(*(_translate_chunk(c) for c in chunks), return_exceptions=True)
        for res in results:
            if isinstance(res, dict):
                for src, dst in res.items():
                    _remember(src, dst)
            else:
                logger.warning("화면 번역 실패: %s", res)
    out: list[str | None] = []
    for t in texts:
        dst = _cache.get(t)
        if dst is not None:
            _cache.move_to_end(t)
        out.append(dst)
    return out
