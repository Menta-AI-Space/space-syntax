"""Best-effort Kakao Map enrichment for SBIZ competitor rows."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.config import settings

KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_PLACE_PANEL_URL = "https://place-api.map.kakao.com/places/panel3/{place_id}"

ENRICH_LIMIT = 6
SEARCH_RADIUS_M = 200

ALLOWED_PHOTO_HOST_SUFFIXES = (
    ".kakaocdn.net",
    ".daumcdn.net",
    ".kakao.com",
)

_PHOTO_TYPE_PRIORITY = {
    "POI": 100,
    "MYSTORE": 95,
    "VENDOR": 90,
    "FOOD": 85,
    "MENU": 80,
    "INDOOR": 75,
    "OUTDOOR": 70,
    "KMAPREVIEW": 50,
}
_MEDIA_TYPE_PRIORITY = {
    "PHOTO": 10,
    "VOD": 0,
}


@dataclass
class CompetitorEnrichment:
    kakao_place_id: str | None = None
    place_url: str | None = None
    photo_url: str | None = None
    rating: float | None = None
    review_count: int | None = None
    tagline: str | None = None
    phone: str | None = None
    kakao_category: str | None = None


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").lower())


def _category_tail(category_name: str) -> str:
    parts = [p.strip() for p in (category_name or "").split(">") if p.strip()]
    return parts[-1] if parts else ""


def _build_tagline(
    *,
    kakao_category: str | None,
    sbiz_category: str | None,
    panel: dict[str, Any] | None,
    phone: str | None,
) -> str | None:
    parts: list[str] = []

    summary = (panel or {}).get("summary") or {}
    if isinstance(summary, dict):
        intro = str(summary.get("intro") or summary.get("description") or "").strip()
        if intro:
            parts.append(intro.split("\n")[0][:80])
        tags = summary.get("tags") or summary.get("tag_list") or []
        if isinstance(tags, list) and tags:
            tag = str(tags[0]).strip()
            if tag and tag not in parts:
                parts.append(tag)
        menu = (panel or {}).get("menu") or {}
        if isinstance(menu, dict):
            menus = menu.get("menus") or menu.get("menu_list") or []
            if isinstance(menus, list) and menus:
                first = menus[0]
                if isinstance(first, dict):
                    menu_name = str(first.get("name") or first.get("menu_name") or "").strip()
                    if menu_name:
                        parts.append(f"대표메뉴 {menu_name}")

    if not parts:
        kakao_tail = _category_tail(kakao_category or "")
        if kakao_tail:
            parts.append(kakao_tail)
        elif sbiz_category:
            parts.append(sbiz_category)

    if phone:
        parts.append(f"☎ {phone}")

    if not parts:
        return None
    return " · ".join(parts[:3])


def _normalize_photo_url(url: str) -> str:
    cleaned = url.strip()
    if cleaned.startswith("http://"):
        return "https://" + cleaned[len("http://") :]
    return cleaned


def _photo_item_url(item: dict[str, Any]) -> str | None:
    for key in (
        "url",
        "originurl",
        "origin_url",
        "photo_url",
        "thumbnail_url",
        "thumb_url",
    ):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            return _normalize_photo_url(raw)
    return None


def _score_photo_item(item: dict[str, Any]) -> int:
    photo_type = str(item.get("type") or "").upper()
    media_type = str(item.get("media_type") or "").upper()
    return _PHOTO_TYPE_PRIORITY.get(photo_type, 40) + _MEDIA_TYPE_PRIORITY.get(
        media_type, 5
    )


def _collect_photo_candidates(container: dict[str, Any]) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []

    for key in (
        "main_photo_url",
        "mainphotourl",
        "main_photo",
        "photo_url",
        "thumbnail_url",
    ):
        raw = container.get(key)
        if isinstance(raw, str) and raw.strip():
            candidates.append((200, _normalize_photo_url(raw)))

    photos = container.get("photos") or {}
    if isinstance(photos, dict):
        for list_key in ("photos", "photosList", "list", "photo_list"):
            photo_list = photos.get(list_key)
            if not isinstance(photo_list, list):
                continue
            for item in photo_list:
                if isinstance(item, dict):
                    url = _photo_item_url(item)
                    if url:
                        candidates.append((_score_photo_item(item), url))
                elif isinstance(item, str) and item.strip():
                    candidates.append((40, _normalize_photo_url(item)))

    review = container.get("kakaomap_review") or {}
    if isinstance(review, dict):
        for list_key in ("photos", "photo_list", "list"):
            photo_list = review.get(list_key)
            if not isinstance(photo_list, list):
                continue
            for item in photo_list:
                if isinstance(item, dict):
                    url = _photo_item_url(item)
                    if url:
                        candidates.append((_score_photo_item(item), url))
                elif isinstance(item, str) and item.strip():
                    candidates.append((45, _normalize_photo_url(item)))

    summary = container.get("summary") or {}
    if isinstance(summary, dict):
        road_view = summary.get("road_view") or {}
        if isinstance(road_view, dict):
            url = road_view.get("url")
            if isinstance(url, str) and url.strip():
                candidates.append((10, _normalize_photo_url(url)))

    return candidates


def _extract_photo_url(data: dict[str, Any]) -> str | None:
    candidates: list[tuple[int, str]] = []
    candidates.extend(_collect_photo_candidates(data))
    my_store = data.get("my_store") or {}
    if isinstance(my_store, dict):
        candidates.extend(_collect_photo_candidates(my_store))
    summary = data.get("summary") or {}
    if isinstance(summary, dict):
        candidates.extend(_collect_photo_candidates(summary))

    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row[0], row[1]))
    return candidates[0][1]


def is_allowed_photo_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == suffix[1:] or host.endswith(suffix) for suffix in ALLOWED_PHOTO_HOST_SUFFIXES)


def proxied_photo_url(url: str | None) -> str | None:
    if not url or not is_allowed_photo_url(url):
        return None
    return f"/api/place-photo?url={quote(url, safe='')}"


async def fetch_kakao_photo(url: str) -> tuple[bytes, str]:
    if not is_allowed_photo_url(url):
        raise ValueError("Photo URL host is not allowed")

    headers = {
        "Accept": "image/*,*/*",
        "Referer": "https://map.kakao.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get("content-type") or "image/jpeg"
        if not content_type.startswith("image/"):
            raise ValueError("Upstream response is not an image")
        return response.content, content_type


def _parse_panel(data: dict[str, Any]) -> tuple[float | None, int | None, str | None]:
    rating: float | None = None
    review_count: int | None = None

    review = data.get("kakaomap_review") or data.get("review") or {}
    if isinstance(review, dict):
        for key in ("score", "average_score", "rating", "star_rating"):
            raw = review.get(key)
            if raw is not None:
                try:
                    rating = round(float(raw), 1)
                    break
                except (TypeError, ValueError):
                    pass
        for key in ("review_count", "count", "total_count"):
            raw = review.get(key)
            if raw is not None:
                try:
                    review_count = int(raw)
                    break
                except (TypeError, ValueError):
                    pass

    summary = data.get("summary") or {}
    if rating is None and isinstance(summary, dict):
        for key in ("rating", "average_score", "score"):
            raw = summary.get(key)
            if raw is not None:
                try:
                    rating = round(float(raw), 1)
                    break
                except (TypeError, ValueError):
                    pass

    photo_url = _extract_photo_url(data)
    return rating, review_count, photo_url


def _pick_best_match(documents: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    if not documents:
        return None
    target = _normalize_name(name)
    scored: list[tuple[int, float, dict[str, Any]]] = []
    for doc in documents:
        place_name = str(doc.get("place_name") or "")
        norm = _normalize_name(place_name)
        score = 0
        if norm == target:
            score = 100
        elif target in norm or norm in target:
            score = 70
        elif any(tok in norm for tok in re.split(r"[\s()]+", name) if len(tok) >= 2):
            score = 40
        try:
            distance = float(doc.get("distance") or 9999)
        except (TypeError, ValueError):
            distance = 9999.0
        scored.append((score, distance, doc))
    scored.sort(key=lambda row: (-row[0], row[1]))
    best_score = scored[0][0]
    if best_score < 40:
        return scored[0][2]
    return scored[0][2]


async def _search_kakao_place(
    client: httpx.AsyncClient,
    *,
    name: str,
    lat: float,
    lng: float,
) -> dict[str, Any] | None:
    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_key}"}
    params = {
        "query": name,
        "x": lng,
        "y": lat,
        "radius": SEARCH_RADIUS_M,
        "sort": "distance",
        "size": 10,
    }
    response = await client.get(KAKAO_KEYWORD_URL, headers=headers, params=params)
    if response.status_code != 200:
        return None
    documents = response.json().get("documents") or []
    return _pick_best_match(documents, name)


async def _fetch_place_panel(
    client: httpx.AsyncClient,
    place_id: str,
) -> dict[str, Any] | None:
    headers = {
        "Accept": "application/json",
        "Referer": "https://map.kakao.com/",
    }
    response = await client.get(
        KAKAO_PLACE_PANEL_URL.format(place_id=place_id),
        headers=headers,
    )
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def enrich_competitor(
    *,
    name: str,
    category: str,
    lat: float | None,
    lng: float | None,
    client: httpx.AsyncClient | None = None,
) -> CompetitorEnrichment:
    empty = CompetitorEnrichment()
    if not settings.kakao_rest_key or lat is None or lng is None:
        return empty

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=12.0)

    try:
        doc = await _search_kakao_place(client, name=name, lat=lat, lng=lng)
        if not doc:
            return empty

        place_id = str(doc.get("id") or "") or None
        place_url = str(doc.get("place_url") or "") or None
        phone = str(doc.get("phone") or "") or None
        kakao_category = str(doc.get("category_name") or "") or None

        rating: float | None = None
        review_count: int | None = None
        photo_url: str | None = None
        panel: dict[str, Any] | None = None

        if place_id:
            panel = await _fetch_place_panel(client, place_id)
            if panel:
                rating, review_count, photo_url = _parse_panel(panel)

        tagline = _build_tagline(
            kakao_category=kakao_category,
            sbiz_category=category,
            panel=panel,
            phone=phone,
        )
        if not tagline and kakao_category:
            tagline = _category_tail(kakao_category)

        return CompetitorEnrichment(
            kakao_place_id=place_id,
            place_url=place_url,
            photo_url=photo_url,
            rating=rating,
            review_count=review_count,
            tagline=tagline,
            phone=phone or None,
            kakao_category=kakao_category,
        )
    finally:
        if owns_client and client is not None:
            await client.aclose()


async def enrich_competitors(
    competitors: list[Any],
    *,
    limit: int = ENRICH_LIMIT,
) -> list[CompetitorEnrichment]:
    if not settings.kakao_rest_key or not competitors:
        return [CompetitorEnrichment() for _ in competitors]

    targets = competitors[:limit]
    rest = len(competitors) - len(targets)

    async with httpx.AsyncClient(timeout=12.0) as client:
        enriched = await asyncio.gather(
            *[
                enrich_competitor(
                    name=c.name,
                    category=c.category,
                    lat=getattr(c, "lat", None),
                    lng=getattr(c, "lng", None),
                    client=client,
                )
                for c in targets
            ]
        )

    return list(enriched) + [CompetitorEnrichment() for _ in range(rest)]
