"""Ingest E-Gen (국립중앙의료원) hospital duty hours into PostGIS.

Source: data.go.kr B552657 HsptlAsembySearchService — full weekday/weekend
duty hours + WGS84 coords for nearly every institution (vs HIRA 세부정보's
~7% coverage, Seoul 0%).

Usage:
    python tools/ingest_egen_hospitals.py --sido 서울특별시
    python tools/ingest_egen_hospitals.py --all   # 17개 시도 전부 (API 호출 많음)

Upserts by hpid; re-run quarterly. dutyTime6 = 토요일, dutyTime7 = 일요일.
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

BASE = "https://apis.data.go.kr/B552657/HsptlAsembySearchService/getHsptlMdcncListInfoInqire"
SIDOS = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
    "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도",
    "경상남도", "제주특별자치도",
]
PAGE_SIZE = 1000


def _text(item: ET.Element, tag: str) -> str | None:
    el = item.find(tag)
    if el is None or el.text is None:
        return None
    value = el.text.strip()
    return value or None


def _hhmm(value: str | None) -> str | None:
    if not value:
        return None
    digits = value.replace(":", "").strip()
    if len(digits) != 4 or not digits.isdigit() or not 0 <= int(digits[:2]) < 24:
        return None
    return digits


def fetch_sido(client: httpx.Client, sido: str) -> list[tuple]:
    rows: list[tuple] = []
    page = 1
    total = None
    while True:
        resp = client.get(BASE, params={
            "serviceKey": settings.data_go_api_key,
            "Q0": sido,
            "pageNo": page,
            "numOfRows": PAGE_SIZE,
        })
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        code = root.findtext(".//resultCode")
        if code != "00":
            raise RuntimeError(f"{sido} p{page}: API error {code} {root.findtext('.//resultMsg')}")
        if total is None:
            total = int(root.findtext(".//totalCount") or 0)
        items = root.findall(".//item")
        for item in items:
            lat, lon = _text(item, "wgs84Lat"), _text(item, "wgs84Lon")
            hpid = _text(item, "hpid")
            if not hpid or not lat or not lon:
                continue
            sat_s, sat_e = _hhmm(_text(item, "dutyTime6s")), _hhmm(_text(item, "dutyTime6c"))
            sun_s, sun_e = _hhmm(_text(item, "dutyTime7s")), _hhmm(_text(item, "dutyTime7c"))
            flon, flat = float(lon), float(lat)
            rows.append((
                hpid,
                _text(item, "dutyName"),
                _text(item, "dutyDivNam"),
                _text(item, "dutyAddr"),
                flon, flat,
                sat_s, sat_e, sun_s, sun_e,
                bool(sat_s and sat_e and sat_s != sat_e),
                bool(sun_s and sun_e and sun_s != sun_e),
                flon, flat, flon, flat,
            ))
        print(f"  {sido} p{page}: {len(items)} items (total {total})")
        if page * PAGE_SIZE >= (total or 0) or not items:
            break
        page += 1
        time.sleep(0.3)  # be polite; daily limit 10k calls
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sido", help="시도 이름 (예: 서울특별시)")
    p.add_argument("--all", action="store_true", help="전국 17개 시도")
    args = p.parse_args()
    targets = SIDOS if args.all else [args.sido] if args.sido else None
    if not targets:
        p.print_help()
        return 1

    conn = psycopg2.connect(settings.database_url)
    total = 0
    with httpx.Client(timeout=30) as client:
        for sido in targets:
            rows = fetch_sido(client, sido)
            with conn:
                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """
                        INSERT INTO egen_hospitals
                          (hpid, name, div_name, addr, lon, lat,
                           sat_start, sat_end, sun_start, sun_end,
                           open_sat, open_sun, geom, geom_5179)
                        VALUES %s
                        ON CONFLICT (hpid) DO UPDATE SET
                          name = EXCLUDED.name, div_name = EXCLUDED.div_name,
                          addr = EXCLUDED.addr, lon = EXCLUDED.lon, lat = EXCLUDED.lat,
                          sat_start = EXCLUDED.sat_start, sat_end = EXCLUDED.sat_end,
                          sun_start = EXCLUDED.sun_start, sun_end = EXCLUDED.sun_end,
                          open_sat = EXCLUDED.open_sat, open_sun = EXCLUDED.open_sun,
                          geom = EXCLUDED.geom, geom_5179 = EXCLUDED.geom_5179,
                          updated_at = NOW()
                        """,
                        rows,
                        template=(
                            "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                            " ST_SetSRID(ST_MakePoint(%s,%s),4326),"
                            " ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s),4326),5179))"
                        ),
                        page_size=2000,
                    )
            total += len(rows)
            print(f"{sido}: {len(rows):,} upserted")
    conn.close()
    print(f"DONE {total:,} hospitals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
