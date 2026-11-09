"""Ingest 공정거래위원회 가맹사업(franchise) 정보공개서 brand stats into Postgres.

Sources (data.go.kr, org 1130000 = 공정거래위원회):
- FftcBrandFrcsStatsService/getBrandFrcsStats   브랜드별 가맹점 현황
    (data.go.kr dataset 15110241) — brandNm, corpNm, indutyLclasNm,
    indutyMlsfcNm, frcsCnt, newFrcsRgsCnt, ctrtEndCnt, ctrtCncltnCnt
- FftcBrandFntnStatsService/getBrandFntnStats   브랜드별 창업금액 현황
    (data.go.kr dataset 15110265) — smtnAmt (창업비용 합계, 천원 단위),
    joined onto the same (yr, brandNm, corpNm) key.

NOTE: like every data.go.kr open API these require a per-API 활용신청
(instant auto-approval) on the portal for the account that owns
DATA_GO_API_KEY. Without it the gateway answers a bare HTTP 403
"Forbidden" — apply at:
    https://www.data.go.kr/data/15110241/openapi.do  (가맹점 현황)
    https://www.data.go.kr/data/15110265/openapi.do  (창업 금액)

Usage:
    python -m tools.ingest_franchise_brands            # latest year, 전국
    python -m tools.ingest_franchise_brands --yr 2024  # pin a year

Re-ingesting upserts on (yr, brand_nm, hq_conm).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx
import psycopg2
from psycopg2.extras import execute_values

# Windows console defaults to cp1252 — Korean brand names crash print()
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

BASE = "https://apis.data.go.kr/1130000"
FRCS_STATS = f"{BASE}/FftcBrandFrcsStatsService/getBrandFrcsStats"
FNTN_STATS = f"{BASE}/FftcBrandFntnStatsService/getBrandFntnStats"
PAGE_SIZE = 1000
# Registry publishes yearly snapshots with a lag; probe newest-first.
CANDIDATE_YEARS = [str(y) for y in range(2026, 2016, -1)]

DDL = """
CREATE TABLE IF NOT EXISTS franchise_brands (
    id serial PRIMARY KEY,
    yr text NOT NULL,
    brand_nm text NOT NULL,
    industry_large text,
    industry_mid text,
    hq_conm text NOT NULL DEFAULT '',
    frcs_cnt int,
    new_open_cnt int,
    contract_end_cnt int,
    contract_cancel_cnt int,
    startup_cost_total_manwon numeric,
    ingested_at timestamptz DEFAULT now(),
    UNIQUE (yr, brand_nm, hq_conm)
);
CREATE INDEX IF NOT EXISTS franchise_brands_brand_nm_idx
    ON franchise_brands (brand_nm text_pattern_ops);
"""


class FtcApiForbidden(RuntimeError):
    pass


def _to_int(val) -> int | None:
    if val in (None, ""):
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _to_float(val) -> float | None:
    if val in (None, ""):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _items(payload: dict) -> list[dict]:
    """items comes back as {"item": [...]} or a bare list depending on count."""
    items = payload.get("items")
    if items is None:
        return []
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict)]
    inner = items.get("item")
    if inner is None:
        return []
    return inner if isinstance(inner, list) else [inner]


def fetch_page(client: httpx.Client, url: str, yr: str, page: int, rows: int) -> dict:
    resp = client.get(url, params={
        "serviceKey": settings.data_go_api_key.strip(),
        "pageNo": page,
        "numOfRows": rows,
        "resultType": "json",
        "yr": yr,
    })
    if resp.status_code == 403:
        raise FtcApiForbidden(
            f"HTTP 403 Forbidden from {url}. The DATA_GO_API_KEY works for other "
            "data.go.kr orgs but is not yet approved for 공정거래위원회(1130000) "
            "services — log in to data.go.kr and click 활용신청 on "
            "https://www.data.go.kr/data/15110241/openapi.do and "
            "https://www.data.go.kr/data/15110265/openapi.do (auto-approved), "
            "then re-run."
        )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("resultCode") not in ("00", None):
        raise RuntimeError(
            f"{url} yr={yr} p{page}: API error "
            f"{payload.get('resultCode')} {payload.get('resultMsg')}"
        )
    return payload


def fetch_all(client: httpx.Client, url: str, yr: str, label: str) -> list[dict]:
    first = fetch_page(client, url, yr, 1, PAGE_SIZE)
    total = _to_int(first.get("totalCount")) or 0
    items = _items(first)
    print(f"  {label} yr={yr}: total {total:,}")
    page = 2
    while len(items) < total:
        time.sleep(0.2)  # be polite; daily quota applies
        payload = fetch_page(client, url, yr, page, PAGE_SIZE)
        batch = _items(payload)
        if not batch:
            break
        items.extend(batch)
        print(f"  {label} p{page}: {len(items):,}/{total:,}")
        page += 1
    return items


def resolve_latest_year(client: httpx.Client) -> str:
    """Newest year for which the 가맹점 현황 service actually has rows."""
    for yr in CANDIDATE_YEARS:
        payload = fetch_page(client, FRCS_STATS, yr, 1, 1)
        if (_to_int(payload.get("totalCount")) or 0) > 0:
            return yr
        time.sleep(0.2)
    raise RuntimeError(f"no year in {CANDIDATE_YEARS[-1]}..{CANDIDATE_YEARS[0]} has data")


def build_rows(yr: str, stats: list[dict], costs: list[dict]) -> list[tuple]:
    # 창업비용 합계(smtnAmt)는 천원 단위 -> 만원으로 환산(/10).
    cost_by_key: dict[tuple[str, str], float] = {}
    for item in costs:
        brand = (item.get("brandNm") or "").strip()
        corp = (item.get("corpNm") or "").strip()
        total = _to_float(item.get("smtnAmt"))
        if brand and total is not None:
            cost_by_key[(brand, corp)] = round(total / 10, 1)

    dedup: dict[tuple[str, str, str], tuple] = {}
    for item in stats:
        brand = (item.get("brandNm") or "").strip()
        if not brand:
            continue
        corp = (item.get("corpNm") or "").strip()
        dedup[(yr, brand, corp)] = (
            yr,
            brand,
            (item.get("indutyLclasNm") or "").strip() or None,
            (item.get("indutyMlsfcNm") or "").strip() or None,
            corp,
            _to_int(item.get("frcsCnt")),
            _to_int(item.get("newFrcsRgsCnt")),
            _to_int(item.get("ctrtEndCnt")),
            _to_int(item.get("ctrtCncltnCnt")),
            cost_by_key.get((brand, corp)),
        )
    return list(dedup.values())


def upsert(conn, rows: list[tuple]) -> None:
    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO franchise_brands
                  (yr, brand_nm, industry_large, industry_mid, hq_conm,
                   frcs_cnt, new_open_cnt, contract_end_cnt, contract_cancel_cnt,
                   startup_cost_total_manwon)
                VALUES %s
                ON CONFLICT (yr, brand_nm, hq_conm) DO UPDATE SET
                  industry_large = EXCLUDED.industry_large,
                  industry_mid = EXCLUDED.industry_mid,
                  frcs_cnt = EXCLUDED.frcs_cnt,
                  new_open_cnt = EXCLUDED.new_open_cnt,
                  contract_end_cnt = EXCLUDED.contract_end_cnt,
                  contract_cancel_cnt = EXCLUDED.contract_cancel_cnt,
                  startup_cost_total_manwon = EXCLUDED.startup_cost_total_manwon,
                  ingested_at = now()
                """,
                rows,
                page_size=2000,
            )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--yr", help="기준년도 (default: newest year with data)")
    args = p.parse_args()

    if not settings.data_go_api_key.strip():
        print("DATA_GO_API_KEY is not configured (.env)")
        return 1

    conn = psycopg2.connect(settings.database_url)
    with conn:
        with conn.cursor() as cur:
            cur.execute(DDL)

    try:
        with httpx.Client(timeout=60) as client:
            yr = args.yr or resolve_latest_year(client)
            print(f"target year: {yr}")
            stats = fetch_all(client, FRCS_STATS, yr, "가맹점현황")
            time.sleep(0.3)
            costs = fetch_all(client, FNTN_STATS, yr, "창업금액")
    except FtcApiForbidden as exc:
        print(f"ABORT: {exc}")
        conn.close()
        return 2

    rows = build_rows(yr, stats, costs)
    upsert(conn, rows)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM franchise_brands WHERE yr = %s", (yr,))
        count = cur.fetchone()[0]
    conn.close()
    print(f"DONE yr={yr}: {len(rows):,} rows upserted ({count:,} in table for that year)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
