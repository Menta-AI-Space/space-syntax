"""행안부 행정표준코드(법정동코드) 전국 → legal_dong_codes 테이블 적재.

두 가지 소스 지원 (현행 코드 ~2만 행, 재실행 안전 upsert):
1. --txt: code.go.kr「법정동코드 전체자료」 다운로드 파일 (EUC-KR, 탭 구분,
   법정동코드/법정동명/폐지여부). 데이터포털 활용신청 불필요 — 기본 권장.
2. 인자 없음: StanReginCd API (DATA_GO_API_KEY에 해당 서비스 활용신청 필요;
   미신청 시 403).

PNU 생성용: 실거래의 (시군구코드 5자리, 법정동명) → region_cd 10자리 매핑.

Run: cd backend && .venv/Scripts/python -m tools.ingest_legal_dong_codes --txt "법정동코드 전체자료.txt"
"""

from __future__ import annotations

import argparse
import asyncio
import time
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import httpx
import psycopg2
import psycopg2.extras

from app.config import settings

API_URL = "https://apis.data.go.kr/1741000/StanReginCd/getStanReginCdList"
PAGE_SIZE = 1000
REQUEST_DELAY_SEC = 0.05

_UPSERT_SQL = """
INSERT INTO legal_dong_codes (region_cd, lawd_cd, addr_name, lowest_name, ingested_at)
VALUES %s
ON CONFLICT (region_cd) DO UPDATE SET
  addr_name = EXCLUDED.addr_name,
  lowest_name = EXCLUDED.lowest_name,
  ingested_at = NOW()
"""
_VALUES_TEMPLATE = "(%s, %s, %s, %s, NOW())"


class DongCodeError(Exception):
    pass


def _parse_page(xml_text: str) -> tuple[list[tuple[str, str, str]], int]:
    """Return ([(region_cd, addr_name, lowest_name)], total_count)."""
    root = ET.fromstring(xml_text)

    result_code = root.findtext(".//RESULT/resultCode") or root.findtext(
        ".//head/RESULT/resultCode"
    )
    if result_code and result_code not in ("INFO-0", "INFO-00", "00", "000"):
        msg = root.findtext(".//RESULT/resultMsg") or "unknown"
        raise DongCodeError(f"StanReginCd error {result_code}: {msg}")

    total = int(root.findtext(".//totalCount") or "0")
    rows: list[tuple[str, str, str]] = []
    for row in root.iter("row"):
        region_cd = (row.findtext("region_cd") or "").strip()
        addr_name = (row.findtext("locatadd_nm") or "").strip()
        lowest_name = (row.findtext("locallow_nm") or "").strip()
        if len(region_cd) == 10 and addr_name:
            if not lowest_name:
                lowest_name = addr_name.split()[-1]
            rows.append((region_cd, addr_name, lowest_name))
    return rows, total


async def _fetch_page(client: httpx.AsyncClient, page: int) -> str:
    params = {
        "serviceKey": unquote(settings.data_go_api_key.strip()),
        "pageNo": page,
        "numOfRows": PAGE_SIZE,
        "type": "xml",
        "flag": "Y",
    }
    resp = await client.get(API_URL, params=params)
    if resp.status_code != 200:
        raise DongCodeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.text


def _rows_from_txt(path: str) -> list[tuple[str, str, str]]:
    """code.go.kr 전체자료 txt → [(region_cd, addr_name, lowest_name)] (현존만)."""
    rows: list[tuple[str, str, str]] = []
    with open(path, encoding="cp949") as f:
        for i, line in enumerate(f):
            parts = line.rstrip("\n").split("\t")
            if i == 0 or len(parts) < 3:
                continue
            region_cd, addr_name, status = (p.strip() for p in parts[:3])
            if status != "존재" or len(region_cd) != 10 or not region_cd.isdigit():
                continue
            rows.append((region_cd, addr_name, addr_name.split()[-1]))
    if not rows:
        raise SystemExit(f"현존 코드가 없습니다 — 파일 형식 확인: {path}")
    return rows


def _upsert(rows: list[tuple[str, str, str]]) -> None:
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    values = [(r[0], r[0][:5], r[1], r[2]) for r in rows]
    psycopg2.extras.execute_values(
        cur, _UPSERT_SQL, values, template=_VALUES_TEMPLATE, page_size=1000
    )
    conn.commit()
    cur.execute("SELECT count(*), count(DISTINCT lawd_cd) FROM legal_dong_codes")
    n, n_lawd = cur.fetchone()
    conn.close()
    print(f"upserted {len(values):,}; DB: {n:,} codes, {n_lawd} lawd_cd")


def ingest_txt(path: str) -> None:
    t0 = time.perf_counter()
    rows = _rows_from_txt(path)
    print(f"{path}: {len(rows):,} 현존 codes")
    _upsert(rows)
    print(f"DONE in {time.perf_counter() - t0:.0f}s")


async def ingest() -> None:
    t0 = time.perf_counter()
    if not settings.data_go_api_key.strip():
        raise SystemExit("DATA_GO_API_KEY is not configured")

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()

    total_rows = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        first = await _fetch_page(client, 1)
        rows, total = _parse_page(first)
        if total <= 0:
            raise DongCodeError("totalCount=0 — API 응답 형식을 확인하세요")
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        print(f"total {total:,} codes, {pages} pages")

        batch = list(rows)
        for page in range(2, pages + 1):
            await asyncio.sleep(REQUEST_DELAY_SEC)
            page_rows, _ = _parse_page(await _fetch_page(client, page))
            batch.extend(page_rows)
            if page % 10 == 0:
                print(f"  page {page}/{pages} ({len(batch):,} rows buffered)")

    values = [(r[0], r[0][:5], r[1], r[2]) for r in batch]
    psycopg2.extras.execute_values(
        cur, _UPSERT_SQL, values, template=_VALUES_TEMPLATE, page_size=1000
    )
    conn.commit()
    total_rows = len(values)

    cur.execute("SELECT count(*), count(DISTINCT lawd_cd) FROM legal_dong_codes")
    n, n_lawd = cur.fetchone()
    conn.close()
    print(
        f"DONE in {time.perf_counter() - t0:.0f}s: upserted {total_rows:,}; "
        f"DB: {n:,} codes, {n_lawd} lawd_cd"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--txt", help="code.go.kr 법정동코드 전체자료 txt 경로 (미지정 시 API)"
    )
    args = parser.parse_args()
    if args.txt:
        ingest_txt(args.txt)
    else:
        asyncio.run(ingest())
