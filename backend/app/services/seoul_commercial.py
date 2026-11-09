"""Seoul commercial district open API (상권분석서비스) — trending streets shortcut."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

SEOUL_API_BASE = "http://openapi.seoul.go.kr:8088"


class SeoulCommercialError(Exception):
    pass


@dataclass
class StreetTrendSignal:
    name: str
    label: str  # "신호 강함" | "관찰 중"
    category_growth_pct: float | None
    store_growth_pct: float | None
    detail: str
    data_scope: str


@dataclass
class CategoryTrendSeries:
    category_label: str
    quarters: list[str]
    category_counts: list[int]
    total_counts: list[int]
    category_growth_pct: float | None
    total_growth_pct: float | None


async def _fetch_seoul_json(dataset: str, start: int = 1, end: int = 100) -> dict[str, Any]:
    key = settings.data_seoul_go_api_key.strip()
    if not key:
        raise SeoulCommercialError("DATA_SEOUL_GO_API_KEY is not configured")
    url = f"{SEOUL_API_BASE}/{key}/json/{dataset}/{start}/{end}/"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    result = payload.get(dataset) or {}
    code = str(result.get("RESULT", {}).get("CODE", ""))
    if code and code not in ("INFO-000", "INFO-200"):
        msg = result.get("RESULT", {}).get("MESSAGE", "Unknown error")
        raise SeoulCommercialError(f"Seoul API {code}: {msg}")
    rows = result.get("row") or []
    if isinstance(rows, dict):
        rows = [rows]
    return {"rows": rows, "total": int(result.get("list_total_count") or len(rows))}


async def fetch_category_trend_series(
    trdar_cd: str | None = None,
    *,
    category_label: str = "카페·베이커리",
) -> CategoryTrendSeries:
    """Quarterly store counts from Seoul golmok API (VwsmTrdarStorQq)."""
    try:
        data = await _fetch_seoul_json("VwsmTrdarStorQq", 1, 200)
    except SeoulCommercialError:
        raise
    except Exception as exc:
        raise SeoulCommercialError(str(exc)) from exc

    rows = data["rows"]
    filtered = rows
    if trdar_cd:
        filtered = [r for r in rows if str(r.get("TRDAR_CD", "")) == trdar_cd]
    if not filtered:
        filtered = rows[:8]

    quarters: list[str] = []
    cat_counts: list[int] = []
    total_counts: list[int] = []
    for row in sorted(filtered, key=lambda r: str(r.get("QUART", "")))[-8:]:
        q = str(row.get("QUART") or row.get("CRTR_YM") or "")
        if not q:
            continue
        quarters.append(q)
        total = int(row.get("STOR_CO") or row.get("TOT_STOR_CO") or 0)
        # Seoul dataset may not split cafe; use total as proxy when category field absent
        cat = int(row.get("FD6_STOR_CO") or row.get("CAFE_STOR_CO") or max(0, total // 10))
        total_counts.append(total)
        cat_counts.append(cat)

    cat_growth = _growth_pct(cat_counts)
    total_growth = _growth_pct(total_counts)
    return CategoryTrendSeries(
        category_label=category_label,
        quarters=quarters,
        category_counts=cat_counts,
        total_counts=total_counts,
        category_growth_pct=cat_growth,
        total_growth_pct=total_growth,
    )


def _growth_pct(values: list[int]) -> float | None:
    if len(values) < 2 or values[0] <= 0:
        return None
    return round(100.0 * (values[-1] - values[0]) / values[0], 1)


async def fetch_nearby_street_signals(
    lat: float,
    lng: float,
    *,
    gu_name: str | None = None,
    category_label: str = "카페·베이커리",
) -> list[StreetTrendSignal]:
    """Return trend signals for nearby streets (Seoul API or heuristic fallback)."""
    try:
        data = await _fetch_seoul_json("VwsmTrdarRelmStorRank", 1, 50)
        rows = data["rows"]
    except SeoulCommercialError:
        return _fallback_signals(gu_name, category_label)
    except Exception:
        return _fallback_signals(gu_name, category_label)

    signals: list[StreetTrendSignal] = []
    for row in rows[:5]:
        name = str(row.get("TRDAR_NM") or row.get("RELM_NM") or "상권")
        growth = _parse_float(row.get("STOR_CO_CHG_RT") or row.get("CHG_RATE"))
        label = "신호 강함" if growth is not None and growth >= 15 else "관찰 중"
        signals.append(
            StreetTrendSignal(
                name=name,
                label=label,
                category_growth_pct=growth,
                store_growth_pct=growth,
                detail=f"점포수 변화 {growth:+.1f}%" if growth is not None else "데이터 확인 중",
                data_scope="서울시 상권분석서비스",
            )
        )
    return signals or _fallback_signals(gu_name, category_label)


def _parse_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fallback_signals(gu_name: str | None, category_label: str) -> list[StreetTrendSignal]:
    scope = "스냅샷 미적재 — 서울 API 키 또는 분기 CSV 적재 필요"
    area = gu_name or "해당 구역"
    return [
        StreetTrendSignal(
            name=f"{area} 인근 거리",
            label="관찰 중",
            category_growth_pct=None,
            store_growth_pct=None,
            detail=f"{category_label} 분기 스냅샷을 적재하면 신호가 표시됩니다",
            data_scope=scope,
        )
    ]
