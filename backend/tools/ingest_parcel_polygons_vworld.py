"""V-World WFS에서 필지 경계를 받아 ``parcel_polygons``에 적재한다.

`ingest_parcel_polygons.py`는 국가공간정보포털에서 **사람이 내려받은 zip**을 읽는다.
그 방식은 한 번에 넓게 넣기 좋지만 다운로드에 로그인·약관 동의가 필요해서, 구를
하나 더 열려면 사람이 붙어야 한다. 이 도구는 이미 쓰고 있는 V-World 키로 같은
데이터를 받아 그 손길을 없앤다.

왜 필요했나: 도로 구조값(`road_segment_structure`)은 서울 전역에 있는데 필지 경계는
강남·금천·영등포 세 구뿐이었다. 그래서 반포역(서초구)에서 화면이 지도만 보이고
아무것도 칠해지지 않았다 — 계산이 안 되는 게 아니라 칠할 면이 없었던 것이다.

**타일로 쪼개 받는다.** WFS가 한 요청에 1,000건까지만 주므로, 정확히 1,000건이 오면
그 칸을 넷으로 갈라 다시 받는다(꽉 찼다는 건 잘렸다는 뜻이다). 빈 칸은 바로 버린다.

⚠️ 운영 DB에 쓴다. 추가(upsert)라 기존 행을 지우지 않지만, **좁은 범위로 먼저 돌려
화면에서 확인한 뒤 넓히라**(CLAUDE.md: 검증된 결과만 옮긴다). `--dry-run`은 받기만
하고 쓰지 않는다.

Run:
  cd backend && .venv/Scripts/python -m tools.ingest_parcel_polygons_vworld \\
      --bbox 127.000,37.500,127.020,37.515 --dry-run
  cd backend && .venv/Scripts/python -m tools.ingest_parcel_polygons_vworld \\
      --around 127.0110 37.5087 --radius-m 800
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx
import psycopg2
import psycopg2.extras

from app.config import settings

WFS_URL = "https://api.vworld.kr/req/wfs"
TYPENAME = "lp_pa_cbnd_bubun"
# WFS가 돌려주는 최대 건수. 이 수로 꽉 차면 잘린 것으로 본다.
PAGE_LIMIT = 1000
# 이보다 잘게 쪼개도 1,000건이 넘으면 포기한다 (그런 칸은 사실상 없다).
MIN_TILE_DEG = 0.00025
BATCH_SIZE = 500

_UPSERT_SQL = """
INSERT INTO parcel_polygons (pnu, geom_5179, area_sqm, src, ingested_at)
VALUES %s
ON CONFLICT (pnu) DO UPDATE SET
  geom_5179 = EXCLUDED.geom_5179,
  area_sqm = EXCLUDED.area_sqm,
  ingested_at = NOW()
"""
# 지오메트리는 GeoJSON 문자열로 넣고 PostGIS가 5179로 옮긴다. 면적도 거기서 잰다 —
# 파이썬에서 도(degree) 면적을 재면 0.0000x가 나온다.
_VALUES_TEMPLATE = """(
  %s,
  ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 5179),
  ST_Area(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 5179)),
  %s, NOW()
)"""


def fetch_tile(client: httpx.Client, bbox: tuple[float, float, float, float]) -> list[dict]:
    params = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typename": TYPENAME,
        "srsname": "EPSG:4326",
        "bbox": ",".join(f"{v:.7f}" for v in bbox),
        "key": settings.vworld_api_key,
        "domain": settings.vworld_domain or "http://localhost:8000",
        "output": "application/json",
        "maxFeatures": str(PAGE_LIMIT),
    }
    response = client.get(WFS_URL, params=params, timeout=40)
    if response.status_code != 200:
        raise RuntimeError(f"WFS HTTP {response.status_code}")
    text = response.text
    if not text.lstrip().startswith("{"):
        # 빈 결과는 XML 예외로 온다 — 실패가 아니라 "여긴 필지가 없다"다.
        return []
    return response.json().get("features", [])


def collect(bbox: tuple[float, float, float, float], *, verbose: bool = True) -> dict[str, dict]:
    """bbox 안의 필지를 pnu→feature로 모은다. 꽉 찬 칸은 넷으로 쪼갠다."""
    found: dict[str, dict] = {}
    queue = [bbox]
    requests = 0
    with httpx.Client() as client:
        while queue:
            tile = queue.pop()
            features = fetch_tile(client, tile)
            requests += 1
            if len(features) >= PAGE_LIMIT and (tile[2] - tile[0]) > MIN_TILE_DEG:
                mx = (tile[0] + tile[2]) / 2
                my = (tile[1] + tile[3]) / 2
                queue.extend([
                    (tile[0], tile[1], mx, my), (mx, tile[1], tile[2], my),
                    (tile[0], my, mx, tile[3]), (mx, my, tile[2], tile[3]),
                ])
                continue
            for feature in features:
                pnu = (feature.get("properties") or {}).get("pnu")
                if pnu and feature.get("geometry"):
                    found[pnu] = feature
            if verbose and requests % 20 == 0:
                print(f"  요청 {requests}회 · 필지 {len(found):,}개 · 남은 칸 {len(queue)}")
            # V-World에 너무 몰아치지 않는다.
            time.sleep(0.05)
    if verbose:
        print(f"  요청 {requests}회로 필지 {len(found):,}개 수집")
    return found


def store(found: dict[str, dict]) -> int:
    if not found:
        return 0
    rows = []
    for pnu, feature in found.items():
        geojson = json.dumps(feature["geometry"])
        rows.append((pnu, geojson, geojson, "vworld"))
    written = 0
    with psycopg2.connect(settings.database_url) as conn, conn.cursor() as cur:
        for start in range(0, len(rows), BATCH_SIZE):
            chunk = rows[start:start + BATCH_SIZE]
            psycopg2.extras.execute_values(cur, _UPSERT_SQL, chunk, template=_VALUES_TEMPLATE)
            written += len(chunk)
        conn.commit()
    return written


def _bbox_around(lng: float, lat: float, radius_m: float) -> tuple[float, float, float, float]:
    deg_lat = radius_m / 110_574
    deg_lng = radius_m / (111_320 * max(0.1, abs(__import__("math").cos(__import__("math").radians(lat)))))
    return (lng - deg_lng, lat - deg_lat, lng + deg_lng, lat + deg_lat)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", help="minLng,minLat,maxLng,maxLat")
    parser.add_argument("--around", nargs=2, type=float, metavar=("LNG", "LAT"))
    parser.add_argument("--radius-m", type=float, default=600)
    parser.add_argument("--dry-run", action="store_true", help="받기만 하고 쓰지 않는다")
    args = parser.parse_args()

    if args.bbox:
        parts = [float(v) for v in args.bbox.split(",")]
        if len(parts) != 4:
            sys.exit("--bbox는 minLng,minLat,maxLng,maxLat 네 값입니다")
        bbox = (parts[0], parts[1], parts[2], parts[3])
    elif args.around:
        bbox = _bbox_around(args.around[0], args.around[1], args.radius_m)
    else:
        sys.exit("--bbox 또는 --around 중 하나가 필요합니다")

    if not settings.vworld_api_key:
        sys.exit("VWORLD_API_KEY가 없습니다")

    print(f"범위 {bbox[0]:.5f},{bbox[1]:.5f} ~ {bbox[2]:.5f},{bbox[3]:.5f}")
    started = time.time()
    found = collect(bbox)
    if not found:
        print("필지를 찾지 못했습니다.")
        return
    sample = list(found)[:3]
    print("표본 PNU:", sample)
    by_sgg: dict[str, int] = {}
    for pnu in found:
        by_sgg[pnu[:5]] = by_sgg.get(pnu[:5], 0) + 1
    print("자치구별:", dict(sorted(by_sgg.items(), key=lambda kv: -kv[1])))

    if args.dry_run:
        print(f"(dry-run) {len(found):,}개를 받았고 쓰지 않았습니다. {time.time() - started:.0f}초")
        return

    written = store(found)
    print(f"적재 {written:,}개 · {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
