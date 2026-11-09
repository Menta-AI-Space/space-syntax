"""SBIZ official upjong (업종) taxonomy — large/middle/small lists + cache."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from app.config import settings
from app.services.sbiz_stores import SbizConfigError, SbizError, SBIZ_SERVICE_BASE, SBIZ_SERVICE_BASE_HTTPS

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FALLBACK_PATH = DATA_DIR / "sbiz_upjong.json"

_tree_cache: list[dict[str, Any]] | None = None
_tree_lock = asyncio.Lock()


@dataclass
class UpjongSelection:
    inds_lcls_cd: str | None = None
    inds_mcls_cd: str | None = None
    inds_scls_cd: str | None = None
    label: str = "일반"
    breadcrumb: str = "일반"
    archetype: str = "general"
    match_method: str = "disabled"  # scls | mcls | disabled


# Quick presets → SBIZ codes (중·소분류)
BUSINESS_TYPE_PRESETS: dict[str, dict[str, str | None]] = {
    "cafe": {
        "inds_lcls_cd": "I2",
        "inds_mcls_cd": "I212",
        "inds_scls_cd": "I21201",
        "label": "카페",
    },
    "convenience": {
        "inds_lcls_cd": "G2",
        "inds_mcls_cd": "G204",
        "inds_scls_cd": "G20405",
        "label": "편의점",
    },
    # "음식점"을 통칭하는 restaurant 프리셋에 특정 소분류(한식 I201 등)를
    # 박아 두면, 실제로는 치킨·샐러드·중식 등인 가게까지 전부 "한식"으로
    # 표시되고 동일업종 집계도 한식 매장 수만 세게 된다("홍제동 치킨집"이
    # "동일업종 한식 71곳"으로 나온 실사고, docs/agent/GOALS.md 2026-07-29
    # 오너 지시 1번). 동일업종 집계(store_matches_upjong)는 단일 코드만
    # 매칭하므로 I201~I211 여러 소분류를 하나로 묶어 셀 방법이 없다 — 세지
    # 못하는데 한식으로 세는 척하느니 집계를 끄고 라벨도 특정 종류를 단정하지
    # 않는다. 구체적인 메뉴어(치킨·중식·분식 등)는 이 프리셋이 아니라
    # search_upjong으로 정확한 소분류를 찾아 넘겨야 한다.
    "restaurant": {
        "inds_lcls_cd": "I2",
        "inds_mcls_cd": None,
        "inds_scls_cd": None,
        "label": "일반 음식점",
    },
    "clinic": {
        "inds_lcls_cd": "Q1",
        "inds_mcls_cd": "Q102",
        "inds_scls_cd": None,
        "label": "병의원",
    },
    "academy": {
        "inds_lcls_cd": "P1",
        "inds_mcls_cd": "P105",
        "inds_scls_cd": "P10501",
        "label": "학원",
    },
    "beauty": {
        "inds_lcls_cd": "S2",
        "inds_mcls_cd": "S207",
        "inds_scls_cd": None,
        "label": "미용실",
    },
    # 아래 넷은 프리셋이 없어 match_method가 'disabled'로 떨어졌고, 그러면 엔진이
    # 동일업종을 **아예 세지 않은 채** count=0을 준다. 그게 "이태원 노래방 0곳,
    # 경쟁 매우 낮아 진입 유리"라는 정반대 조언으로 이어졌다. 코드는 추측하지 않고
    # sbiz_stores에 실제로 들어 있는 값에서 골랐다(괄호 안은 서울 점포 수).
    "leisure": {
        # 유원지·오락 전체 — 노래방(4,898)·PC방(1,041)·게임장이 한 묶음이라
        # 소분류 하나로 좁히면 PC방 질의에 노래방만 세게 된다.
        "inds_lcls_cd": "R1",
        "inds_mcls_cd": "R104",
        "inds_scls_cd": None,
        "label": "오락·여가",
    },
    "fitness": {
        # 헬스장(4,478)으로 좁힌다 — 같은 중분류에 당구장·골프연습장·테니스장이
        # 섞여 있어 중분류로 잡으면 운동시설 경쟁이 부풀려진다.
        "inds_lcls_cd": "R1",
        "inds_mcls_cd": "R103",
        "inds_scls_cd": "R10307",
        "label": "헬스장",
    },
    "gas_station": {
        # 연료 소매 중 주유소(608)만 — 가정용 연료·가스충전소는 다른 장사다.
        "inds_lcls_cd": "G2",
        "inds_mcls_cd": "G214",
        "inds_scls_cd": "G21401",
        "label": "주유소",
    },
    "professional_office": {
        # 부동산 서비스 중분류에 중개/대리업(24,475) 하나뿐이다.
        "inds_lcls_cd": "L1",
        "inds_mcls_cd": "L102",
        "inds_scls_cd": "L10203",
        "label": "부동산 중개",
    },
    "lodging": {
        # 일반 숙박 — 호텔·여관/모텔·펜션이 한 묶음(6,047).
        "inds_lcls_cd": "I1",
        "inds_mcls_cd": "I101",
        "inds_scls_cd": None,
        "label": "숙박",
    },
    # ev_charging은 일부러 비워 둔다 — sbiz 업종에 전기차 충전소가 없다
    # (G21402는 LPG 가스충전소다). 없는 걸 있는 척 세는 것보다 "집계되지 않음"이 낫다.
}

QUICK_PRESETS: list[dict[str, str | None]] = [
    {
        "id": "cafe",
        "label": "카페",
        "inds_lcls_cd": "I2",
        "inds_mcls_cd": "I212",
        "inds_scls_cd": "I21201",
    },
    {
        "id": "convenience",
        "label": "편의점",
        "inds_lcls_cd": "G2",
        "inds_mcls_cd": "G204",
        "inds_scls_cd": "G20405",
    },
    {
        "id": "restaurant",
        "label": "한식",
        "inds_lcls_cd": "I2",
        "inds_mcls_cd": "I201",
        "inds_scls_cd": None,
    },
    {
        "id": "academy",
        "label": "학원",
        "inds_lcls_cd": "P1",
        "inds_mcls_cd": "P105",
        "inds_scls_cd": "P10501",
    },
    {
        "id": "clinic",
        "label": "병의원",
        "inds_lcls_cd": "Q1",
        "inds_mcls_cd": "Q102",
        "inds_scls_cd": None,
    },
    {
        "id": "beauty",
        "label": "미용실",
        "inds_lcls_cd": "S2",
        "inds_mcls_cd": "S207",
        "inds_scls_cd": None,
    },
]

# Explicit middle-code → scoring archetype (codes verified against the official
# 소상공인시장진흥공단 상가업소정보 247-category taxonomy — see backend/data/sbiz_upjong.json)
MCLS_ARCHETYPE: dict[str, str] = {
    "I201": "food_drink",  # 한식 음식점업
    "I202": "food_drink",  # 중식 음식점업
    "I203": "food_drink",  # 일식 음식점업
    "I204": "food_drink",  # 서양식 음식점업
    "I205": "food_drink",  # 동남아시아 음식점업
    "I206": "food_drink",  # 기타 외국식 음식점업
    "I207": "food_drink",  # 구내식당 및 뷔페
    "I210": "food_drink",  # 기타 간이 음식점업
    "I211": "food_drink",  # 주점업
    "I212": "food_drink",  # 비알코올 음료점업
    "G204": "daily_retail",  # 종합 소매업
    "G205": "daily_retail",  # 식료품 소매업
    "G206": "daily_retail",  # 음료 소매업
    "G215": "medical",  # 의약품/의료기기/화장품 소매업 (약국 등)
    "Q101": "medical",  # 병원
    "Q102": "medical",  # 의원
    "Q104": "medical",  # 기타 보건업
    "P105": "education",  # 일반 교육기관
    "P106": "education",  # 기타 교육기관
    "S207": "beauty_service",  # 이용 및 미용업
    "S208": "beauty_service",  # 욕탕, 마사지 및 기타 신체 관리 서비스업
}


def _service_key() -> str:
    key = settings.data_go_api_key.strip()
    if not key:
        raise SbizConfigError(
            "DATA_GO_API_KEY is not configured. Add it to .env (see .env.example)."
        )
    return unquote(key)


def _parse_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    header = payload.get("header") or {}
    result_code = str(header.get("resultCode", ""))
    if result_code and result_code != "00":
        msg = header.get("resultMsg", "Unknown API error")
        raise SbizError(f"SBIZ API error {result_code}: {msg}")
    body = payload.get("body") or {}
    items_raw = body.get("items") or []
    if isinstance(items_raw, dict):
        items_raw = [items_raw]
    return [item for item in items_raw if isinstance(item, dict)]


async def _fetch_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    params = {**params, "serviceKey": _service_key(), "type": "json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(f"{SBIZ_SERVICE_BASE_HTTPS}/{path}", params=params)
        if response.status_code >= 500 or response.status_code == 403:
            response = await client.get(f"{SBIZ_SERVICE_BASE}/{path}", params=params)
    if response.status_code != 200:
        raise SbizError(f"HTTP {response.status_code}: {response.text[:200]}")
    try:
        return response.json()
    except ValueError as e:
        raise SbizError(f"Invalid JSON from SBIZ: {response.text[:200]}") from e


async def _fetch_large_list() -> list[dict[str, str]]:
    payload = await _fetch_json("largeUpjongList", {"pageNo": 1, "numOfRows": 100})
    items = _parse_items(payload)
    return [
        {
            "code": str(item.get("indsLclsCd", "")).strip(),
            "name": str(item.get("indsLclsNm", "")).strip(),
        }
        for item in items
        if item.get("indsLclsCd")
    ]


async def _fetch_middle_list(lcls_cd: str) -> list[dict[str, str]]:
    payload = await _fetch_json(
        "middleUpjongList",
        {"indsLclsCd": lcls_cd, "pageNo": 1, "numOfRows": 500},
    )
    items = _parse_items(payload)
    return [
        {
            "code": str(item.get("indsMclsCd", "")).strip(),
            "name": str(item.get("indsMclsNm", "")).strip(),
        }
        for item in items
        if item.get("indsMclsCd")
    ]


async def _fetch_small_list(mcls_cd: str) -> list[dict[str, str]]:
    payload = await _fetch_json(
        "smallUpjongList",
        {"indsMclsCd": mcls_cd, "pageNo": 1, "numOfRows": 500},
    )
    items = _parse_items(payload)
    return [
        {
            "code": str(item.get("indsSclsCd", "")).strip(),
            "name": str(item.get("indsSclsNm", "")).strip(),
        }
        for item in items
        if item.get("indsSclsCd")
    ]


def _load_fallback_tree() -> list[dict[str, Any]]:
    if FALLBACK_PATH.is_file():
        with FALLBACK_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "large" in data:
            return data["large"]
    return []


async def fetch_upjong_tree(*, use_api: bool = True) -> list[dict[str, Any]]:
    """Build nested large → middle → small tree."""
    if use_api and settings.data_go_api_key.strip():
        try:
            large_list = await _fetch_large_list()
            tree: list[dict[str, Any]] = []
            for large in large_list:
                lcls_cd = large["code"]
                middle_list = await _fetch_middle_list(lcls_cd)
                middle_nodes: list[dict[str, Any]] = []
                for middle in middle_list:
                    mcls_cd = middle["code"]
                    small_list = await _fetch_small_list(mcls_cd)
                    middle_nodes.append(
                        {
                            "code": mcls_cd,
                            "name": middle["name"],
                            "small": small_list,
                        }
                    )
                    await asyncio.sleep(0.03)
                tree.append(
                    {
                        "code": lcls_cd,
                        "name": large["name"],
                        "middle": middle_nodes,
                    }
                )
                await asyncio.sleep(0.03)
            if tree:
                return tree
        except (SbizError, SbizConfigError):
            pass
    return _load_fallback_tree()


async def get_upjong_tree() -> list[dict[str, Any]]:
    """Return cached tree — fallback JSON first (fast), live API only if no fallback."""
    global _tree_cache
    async with _tree_lock:
        if _tree_cache is None:
            fb = _load_fallback_tree()
            if fb:
                _tree_cache = fb
            elif settings.data_go_api_key.strip():
                _tree_cache = await fetch_upjong_tree(use_api=True)
            else:
                _tree_cache = []
        return _tree_cache


def invalidate_upjong_cache() -> None:
    global _tree_cache
    _tree_cache = None


def _lookup_in_tree(
    tree: list[dict[str, Any]],
    *,
    inds_lcls_cd: str | None = None,
    inds_mcls_cd: str | None = None,
    inds_scls_cd: str | None = None,
) -> tuple[str, str, str | None, str | None, str | None]:
    """Return (label, breadcrumb, lcls, mcls, scls names/codes resolved)."""
    lcls_nm = mcls_nm = scls_nm = None
    for large in tree:
        if inds_lcls_cd and large.get("code") != inds_lcls_cd:
            continue
        if inds_lcls_cd:
            lcls_nm = large.get("name")
        for middle in large.get("middle") or []:
            if inds_mcls_cd and middle.get("code") != inds_mcls_cd:
                continue
            if inds_mcls_cd:
                mcls_nm = middle.get("name")
            for small in middle.get("small") or []:
                if inds_scls_cd and small.get("code") == inds_scls_cd:
                    scls_nm = small.get("name")
                    parts = [large.get("name"), middle.get("name"), scls_nm]
                    breadcrumb = " > ".join(p for p in parts if p)
                    return scls_nm or middle.get("name", ""), breadcrumb, inds_lcls_cd, inds_mcls_cd, inds_scls_cd
            if inds_mcls_cd and not inds_scls_cd:
                parts = [large.get("name"), mcls_nm]
                breadcrumb = " > ".join(p for p in parts if p)
                return mcls_nm or "", breadcrumb, inds_lcls_cd, inds_mcls_cd, None
    # Fallback labels from presets
    if inds_scls_cd:
        for preset in QUICK_PRESETS:
            if preset.get("inds_scls_cd") == inds_scls_cd:
                return preset["label"], preset["label"], inds_lcls_cd, inds_mcls_cd, inds_scls_cd
    if inds_mcls_cd:
        for preset in QUICK_PRESETS:
            if preset.get("inds_mcls_cd") == inds_mcls_cd:
                return preset["label"], preset["label"], inds_lcls_cd, inds_mcls_cd, None
    label = scls_nm or mcls_nm or lcls_nm or "선택 업종"
    parts = [p for p in (lcls_nm, mcls_nm, scls_nm) if p]
    return label, " > ".join(parts) if parts else label, inds_lcls_cd, inds_mcls_cd, inds_scls_cd


def archetype_for_mcls(inds_mcls_cd: str | None) -> str:
    if not inds_mcls_cd:
        return "general"
    if inds_mcls_cd in MCLS_ARCHETYPE:
        return MCLS_ARCHETYPE[inds_mcls_cd]
    prefix = inds_mcls_cd[:1]
    if prefix == "I":
        return "food_drink"
    if prefix == "G":
        return "daily_retail"
    if prefix == "Q":
        return "medical"
    if prefix == "P":
        return "education"
    if prefix == "S":
        return "beauty_service"
    return "general"


def _resolve_codes_from_tree(
    tree: list[dict[str, Any]],
    *,
    inds_lcls_cd: str | None,
    inds_mcls_cd: str | None,
    inds_scls_cd: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Fill missing lcls/mcls from scls lookup in the taxonomy tree."""
    lcls, mcls, scls = inds_lcls_cd, inds_mcls_cd, inds_scls_cd
    if not scls or (lcls and mcls):
        return lcls, mcls, scls
    for large in tree:
        for middle in large.get("middle") or []:
            for small in middle.get("small") or []:
                if small.get("code") == scls:
                    return large.get("code") or lcls, middle.get("code") or mcls, scls
    return lcls, mcls, scls


def code_exists_in_tree(
    tree: list[dict[str, Any]],
    *,
    inds_mcls_cd: str | None,
    inds_scls_cd: str | None,
) -> bool:
    """분류 트리에 실제로 있는 코드인지."""
    for large in tree or []:
        for middle in large.get("middle") or []:
            if inds_scls_cd:
                for small in middle.get("small") or []:
                    if small.get("code") == inds_scls_cd:
                        return True
            elif inds_mcls_cd and middle.get("code") == inds_mcls_cd:
                return True
    return False


def resolve_upjong_selection(
    *,
    inds_lcls_cd: str | None = None,
    inds_mcls_cd: str | None = None,
    inds_scls_cd: str | None = None,
    business_type: str | None = None,
    tree: list[dict[str, Any]] | None = None,
) -> UpjongSelection:
    """Resolve request fields to a single upjong selection."""
    lcls = inds_lcls_cd
    mcls = inds_mcls_cd
    scls = inds_scls_cd

    preset_label: str | None = None
    if business_type and business_type in BUSINESS_TYPE_PRESETS:
        preset = BUSINESS_TYPE_PRESETS[business_type]
        lcls = lcls or preset.get("inds_lcls_cd")
        mcls = mcls or preset.get("inds_mcls_cd")
        scls = scls or preset.get("inds_scls_cd")
        preset_label = preset.get("label")

    if not mcls and not scls:
        if not lcls:
            return UpjongSelection(label="", breadcrumb="", match_method="disabled")
        # 대분류(예: "I2" 음식점업)만 있고 소분류가 없는 경우 — restaurant
        # 프리셋처럼 여러 소분류(한식·중식·분식…)를 아우르는 업종이라 하나의
        # 코드로 좁힐 수 없다. store_matches_upjong은 단일 코드만 매칭하므로
        # 동일업종 집계는 여기서 켤 수 없다(세지 못하는 걸 특정 소분류로
        # 세는 척하지 않는다). 다만 라벨과 archetype(가중치 프로필)은 대분류
        # 만으로도 정확히 정할 수 있으니 비워 두지 않는다.
        label = preset_label or "선택 업종"
        if tree:
            for large in tree:
                if large.get("code") == lcls:
                    label = large.get("name") or label
                    break
        return UpjongSelection(
            inds_lcls_cd=lcls,
            label=label,
            breadcrumb=label,
            archetype=archetype_for_mcls(lcls),
            match_method="disabled",
        )

    if tree:
        lcls, mcls, scls = _resolve_codes_from_tree(
            tree,
            inds_lcls_cd=lcls,
            inds_mcls_cd=mcls,
            inds_scls_cd=scls,
        )
        label, breadcrumb, _, _, _ = _lookup_in_tree(
            tree, inds_lcls_cd=lcls, inds_mcls_cd=mcls, inds_scls_cd=scls
        )
    else:
        label = "선택 업종"
        breadcrumb = label
        if scls:
            for preset in QUICK_PRESETS:
                if preset.get("inds_scls_cd") == scls:
                    label = preset["label"]
                    breadcrumb = label
                    break
        elif mcls:
            for preset in QUICK_PRESETS:
                if preset.get("inds_mcls_cd") == mcls:
                    label = preset["label"]
                    breadcrumb = label
                    break

    match_method = "scls" if scls else "mcls"
    # 트리에 없는 코드(오타·폐지된 코드)로는 점포를 셀 수 없다. 그런데 그대로
    # 세면 한 곳도 안 맞아 '동일업종 0곳 · 경쟁 매우 낮음'이 나온다 — 실제로
    # ZZ99999로 조회하면 점수 73.4에 '매우 낮음'이 붙었다. 세지 않은 것을 0으로
    # 말하지 않도록 집계를 끈다(그러면 '아직 집계되지 않음'으로 안내된다).
    if tree and not code_exists_in_tree(tree, inds_mcls_cd=mcls, inds_scls_cd=scls):
        match_method = "disabled"
    return UpjongSelection(
        inds_lcls_cd=lcls,
        inds_mcls_cd=mcls,
        inds_scls_cd=scls,
        label=label,
        breadcrumb=breadcrumb,
        archetype=archetype_for_mcls(mcls),
        match_method=match_method,
    )


def search_upjong(tree: list[dict[str, Any]], query: str, *, limit: int = 30) -> list[dict[str, Any]]:
    """Search small(scls)-level categories only, so every result is precise enough
    for exact-code competitor matching (see store_matches_upjong). Matches against
    the official name first, then the parent middle-category name, then legacy
    aliases (old 837-code taxonomy terms) so approximate/colloquial input like
    "닭갈비" still finds "닭/오리고기 구이/찜".
    """
    q = query.strip().lower()
    if not q:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for large in tree:
        lcls_cd = large.get("code", "")
        lcls_nm = large.get("name", "")
        for middle in large.get("middle") or []:
            mcls_cd = middle.get("code", "")
            mcls_nm = middle.get("name", "")
            mcls_match = q in mcls_nm.lower()
            for small in middle.get("small") or []:
                scls_cd = small.get("code", "")
                scls_nm = small.get("name", "")
                scls_lower = scls_nm.lower()
                matched_alias: str | None = None

                if scls_lower == q:
                    score = 0
                elif scls_lower.startswith(q):
                    score = 1
                elif q in scls_lower:
                    score = 2
                elif mcls_match:
                    score = 3
                else:
                    matched_alias = next(
                        (a for a in (small.get("aliases") or []) if q in a.lower()), None
                    )
                    if matched_alias is None:
                        continue
                    score = 4

                item: dict[str, Any] = {
                    "type": "small",
                    "inds_lcls_cd": lcls_cd,
                    "inds_mcls_cd": mcls_cd,
                    "inds_scls_cd": scls_cd,
                    "label": scls_nm,
                    "breadcrumb": f"{lcls_nm} > {mcls_nm} > {scls_nm}",
                }
                if matched_alias:
                    item["matched_alias"] = matched_alias
                scored.append((score, item))

    scored.sort(key=lambda pair: pair[0])
    return [item for _, item in scored[:limit]]
