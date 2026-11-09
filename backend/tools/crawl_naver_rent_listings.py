"""네이버부동산 임대 매물 호가 수집 → rent_listings (내부 컨설팅 분석 전용).

⚠ 주의
- 호가(asking price) 데이터이며 실거래가 아니다. 렌트프리 미반영·호가 편향이
  있으므로 분석 산출물에는 반드시 "호가 기준, 실거래 아님"을 표기한다.
- 플랫폼 API·comparable 서비스에서 이 테이블을 조회하지 않는다 (서비스 서빙 금지,
  개별 컨설팅 내부 분석 용도로만 사용).
- 소규모·저빈도로만 실행한다: 요청 간 딜레이를 지키고, 필요한 상권 반경만 수집.

Run: cd backend && .venv/Scripts/python -m tools.crawl_naver_rent_listings \
       --lat 37.5551 --lng 126.9368 --radius 500 --kind SG --trade B2
     (kind: SG 상가 | OPST 오피스텔, trade: B2 월세 | B1 전세)
"""

from __future__ import annotations

import argparse
import asyncio
import math
import time

import httpx
import psycopg2
import psycopg2.extras

from app.config import settings

API_URL = "https://m.land.naver.com/cluster/ajax/articleList"
REQUEST_DELAY_SEC = 1.5  # 저빈도 유지
MAX_PAGES = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://m.land.naver.com/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

_UPSERT_SQL = """
INSERT INTO rent_listings (
  source, article_no, property_kind, trade_kind, deposit_manwon, rent_manwon,
  area_contract_sqm, area_exclusive_sqm, floor_info, name, description,
  cortar_no, lat, lon, geom_5179, first_seen_at, last_seen_at
)
VALUES %s
ON CONFLICT (source, article_no) DO UPDATE SET
  deposit_manwon = EXCLUDED.deposit_manwon,
  rent_manwon = EXCLUDED.rent_manwon,
  last_seen_at = NOW()
"""
_VALUES_TEMPLATE = (
    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
    " ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179), NOW(), NOW())"
)


def _parse_manwon(value) -> int | None:
    """네이버 가격 문자열(예: '1억 2,000', '3,000') → 만원."""
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if not s:
        return None
    total = 0
    if "억" in s:
        eok, _, rest = s.partition("억")
        try:
            total += int(eok.strip()) * 10_000
        except ValueError:
            return None
        s = rest.strip()
    if s:
        try:
            total += int(float(s))
        except ValueError:
            return None if total == 0 else total
    return total


def _bbox(lat: float, lng: float, radius_m: float) -> tuple[float, float, float, float]:
    dlat = radius_m / 111_320
    dlng = radius_m / (111_320 * math.cos(math.radians(lat)))
    return lat - dlat, lng - dlng, lat + dlat, lng + dlng


async def fetch_listings(
    lat: float, lng: float, radius_m: float, kind: str, trade: str
) -> list[dict]:
    btm, lft, top, rgt = _bbox(lat, lng, radius_m)
    articles: list[dict] = []
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
        for page in range(1, MAX_PAGES + 1):
            params = {
                "rletTpCd": kind,
                "tradTpCd": trade,
                "z": 16,
                "lat": lat,
                "lon": lng,
                "btm": btm,
                "lft": lft,
                "top": top,
                "rgt": rgt,
                "page": page,
            }
            resp = await client.get(API_URL, params=params)
            if resp.status_code != 200:
                raise SystemExit(
                    f"HTTP {resp.status_code} (page {page}) — 차단됐거나 엔드포인트가 "
                    f"바뀌었을 수 있습니다: {resp.text[:200]}"
                )
            data = resp.json()
            body = data.get("body") or []
            articles.extend(body)
            print(f"  page {page}: {len(body)} listings (more={data.get('more')})")
            if not data.get("more") or not body:
                break
            await asyncio.sleep(REQUEST_DELAY_SEC)
    return articles


def store(articles: list[dict], kind: str, trade: str) -> None:
    rows = []
    for a in articles:
        article_no = str(a.get("atclNo") or "").strip()
        lat, lng = a.get("lat"), a.get("lng")
        if not article_no or lat is None or lng is None:
            continue
        rows.append(
            (
                "naver", article_no, kind, trade,
                _parse_manwon(a.get("prc")), _parse_manwon(a.get("rentPrc")),
                a.get("spc1"), a.get("spc2"),
                str(a.get("flrInfo") or ""), str(a.get("atclNm") or ""),
                str(a.get("atclFetrDesc") or ""), str(a.get("cortarNo") or ""),
                lat, lng, lng, lat,
            )
        )
    if not rows:
        print("no rows to store")
        return
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    psycopg2.extras.execute_values(
        cur, _UPSERT_SQL, rows, template=_VALUES_TEMPLATE, page_size=500
    )
    conn.commit()
    cur.execute("SELECT count(*) FROM rent_listings")
    n = cur.fetchone()[0]
    conn.close()
    print(f"stored {len(rows)} listings; DB total {n:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lng", type=float, required=True)
    parser.add_argument("--radius", type=float, default=500)
    parser.add_argument("--kind", default="SG", choices=["SG", "OPST"])
    parser.add_argument("--trade", default="B2", choices=["B2", "B1", "A1"])
    args = parser.parse_args()

    t0 = time.perf_counter()
    listings = asyncio.run(
        fetch_listings(args.lat, args.lng, args.radius, args.kind, args.trade)
    )
    store(listings, args.kind, args.trade)
    print(f"DONE in {time.perf_counter() - t0:.0f}s")
