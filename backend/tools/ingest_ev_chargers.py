"""Ingest 환경부 전기차 충전소 위치 into PostGIS (API-only).

Source: data.go.kr B552584 EvCharger getChargerInfo — per-charger rows with
WGS84 coords, 급속/완속 type, operator. The 2022 CSV download has NO coords
and is 4 years stale — never use it; this API is the sole source.

Usage:
    python tools/ingest_ev_chargers.py --zcode 11    # 서울
    python tools/ingest_ev_chargers.py --all         # 전국 (호출 많음)

Upserts by (stat_id, chger_id); re-run monthly~quarterly.
"""

from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

BASE = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
ZCODES = ["11", "26", "27", "28", "29", "30", "31", "36",
          "41", "43", "44", "46", "47", "48", "50", "51", "52"]
# 기본지연 ~30s + 행당 ~0.03s: 2000이면 ~90s (client timeout 120s 내). 9999는 게이트웨이 504.
PAGE_SIZE = 2000
MAX_RETRIES = 5


def _text(item: ET.Element, tag: str) -> str | None:
    el = item.find(tag)
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _insert(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO ev_chargers
                  (stat_id, chger_id, name, addr, lon, lat,
                   charger_type, output_kw, busi_name, zcode, geom, geom_5179)
                VALUES %s
                ON CONFLICT (stat_id, chger_id) DO UPDATE SET
                  name = EXCLUDED.name, addr = EXCLUDED.addr,
                  lon = EXCLUDED.lon, lat = EXCLUDED.lat,
                  charger_type = EXCLUDED.charger_type,
                  output_kw = EXCLUDED.output_kw,
                  busi_name = EXCLUDED.busi_name, zcode = EXCLUDED.zcode,
                  geom = EXCLUDED.geom, geom_5179 = EXCLUDED.geom_5179,
                  updated_at = NOW()
                """,
                rows,
                template=(
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    " ST_SetSRID(ST_MakePoint(%s,%s),4326),"
                    " ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s),4326),5179))"
                ),
                page_size=2000,
            )


def fetch_zcode(client: httpx.Client, zcode: str, conn) -> int:
    """페이지 단위로 즉시 커밋 — 중간 실패해도 진행분 보존 (upsert라 재실행 안전)."""
    inserted = 0
    page = 1
    total = None
    while True:
        resp = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = client.get(BASE, params={
                    "serviceKey": settings.data_go_api_key,
                    "pageNo": page,
                    "numOfRows": PAGE_SIZE,
                    "zcode": zcode,
                })
                resp.raise_for_status()
                break
            except (httpx.HTTPStatusError, httpx.TimeoutException) as e:
                if attempt == MAX_RETRIES:
                    raise
                print(f"  zcode {zcode} p{page}: retry {attempt} ({type(e).__name__})", flush=True)
                time.sleep(5 * attempt)
        if "Forbidden" in resp.text[:200]:
            raise RuntimeError("API Forbidden — data.go.kr 활용신청 승인/전파 대기 중")
        root = ET.fromstring(resp.text)
        code = root.findtext(".//resultCode")
        if code not in ("00", None):
            raise RuntimeError(f"zcode {zcode} p{page}: {code} {root.findtext('.//resultMsg')}")
        if total is None:
            total = int(root.findtext(".//totalCount") or 0)
        items = root.findall(".//item")
        rows: list[tuple] = []
        for item in items:
            lat, lon = _text(item, "lat"), _text(item, "lng")
            stat_id, chger_id = _text(item, "statId"), _text(item, "chgerId")
            if not stat_id or not chger_id or not lat or not lon:
                continue
            flon, flat = float(lon), float(lat)
            rows.append((
                stat_id, chger_id,
                _text(item, "statNm"), _text(item, "addr"),
                flon, flat,
                _text(item, "chgerType"), _text(item, "output"),
                _text(item, "busiNm"), zcode,
                flon, flat, flon, flat,
            ))
        _insert(conn, rows)
        inserted += len(rows)
        print(f"  zcode {zcode} p{page}: {len(items)} fetched / {inserted:,} committed (total {total})", flush=True)
        if page * PAGE_SIZE >= (total or 0) or not items:
            break
        page += 1
        time.sleep(0.3)
    return inserted


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--zcode", help="시도 코드 (서울=11)")
    p.add_argument("--all", action="store_true")
    args = p.parse_args()
    targets = ZCODES if args.all else [args.zcode] if args.zcode else None
    if not targets:
        p.print_help()
        return 1

    conn = psycopg2.connect(settings.database_url)
    total = 0
    with httpx.Client(timeout=120) as client:
        for zcode in targets:
            inserted = fetch_zcode(client, zcode, conn)
            total += inserted
            print(f"zcode {zcode}: {inserted:,} upserted", flush=True)
    conn.close()
    print(f"DONE {total:,} chargers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
