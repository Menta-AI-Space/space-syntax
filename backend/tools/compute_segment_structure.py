"""1단계 배치 — 서울 전역 도로 세그먼트의 구조값을 타일로 나눠 한 번 계산한다.

필지 단위 판정(``compute_parcel_undervalued.py``)이 이 표를 읽는다. 필지가 약
87만 개인데 필지마다 반경 분석을 돌리면 며칠이 걸린다. 세그먼트는 그보다 훨씬
적고, 어차피 한 세그먼트에 접한 필지들은 같은 구조값을 갖는다 — 그래서 순서를
뒤집어 세그먼트를 먼저 계산하고 필지에 붙인다.

**타일 분할과 그 보장**

``fetch_roads_in_radius``는 반경 안에 걸친 ``roads_clean`` 행을 **자르지 않고
그대로** 준다. 그래서 같은 세그먼트는 어느 타일에서 봐도 같은 기하를 갖고, 중점도
같다. 타일마다 다음을 지킨다:

- 타일 중심에서 ``반경 = 코어 반대각선/2 + HALO_M`` 으로 도로를 가져온다.
- 계산은 그 전체 부분그래프에서 하되, **중점이 코어 정사각형 안에 있는 세그먼트만**
  저장한다.

이러면 (a) 세그먼트가 정확히 한 타일에서만 저장되므로 중복이 없고, (b) 저장되는
세그먼트는 자기 주변 ``HALO_M`` 만큼의 도로망이 계산에 전부 들어가 있다.
``HALO_M >= ANALYSIS_RADIUS_M`` 이므로 코어 세그먼트의 분석 반경은 온전하다.

⚠️ **컬럼 이름이 오해를 부른다 — ``choice_raw``는 raw가 아니라 NACH다.**
``nx.betweenness_centrality``를 ``normalized=False``로 부르는 것은 맞지만, 그 값은
저장 전에 ``space_syntax.py:_accumulate_segment_metrics``(86~94행)에서 정규화된다:
``NACH = log(choice+1) / log(total_depth+3)``, ``NAIN = node_count^1.2 / total_depth``.
depthmapX 경로(``tools/depthmapx_bridge.py``)도 같은 식을 쓴다. **NACH는 크기가 다른
체계 간 비교를 위해 설계된 지표이므로(Hillier 2012) 타일 간 비교가 가능하다.**

``structure_pct_in_tile``(타일 안 백분위)도 함께 저장하지만 **실험은 이 열을 쓰지
않는다** — ``experiment_mok_validity``·``experiment_sales_growth_backtest``·
``experiment_within_trdar``·``experiment_rent_within_dong`` 넷 다 ``choice_raw`` 계열을
직접 읽는다. (2026-09-10 실사: `DECISIONS_LAND_MODEL.md` §22)

⚠️ **다만 「반경 500m」가 아니다.** ``total_depth``와 betweenness는 타일 부분그래프
**전체**에서 계산되므로, 실제 측정치는 **코어 800m + 헤일로 500m ≈ 1.8km 창 안의
radius-n**이다. 서울 전역 위상구조가 아니라는 §14의 경고는 이 뜻에서 유효하다.

**엔진은 depthmapX(C++)를 먼저 쓴다.** ``compute_metrics``(NetworkX)는 타일마다
전지점쌍 최단경로를 두 번 돌려 세그먼트 수에 초선형으로 느려진다 — 마포구 실측에서
타일당 중위 **126초**가 나와 서울 전체가 60시간대였다. depthmapX로 바꾸면 같은
타일이 15배 빨라진다(627세그먼트 기준 32.2s → 2.1s). ``DEPTHMAPX_COMMAND``가
없거나 실패하면 NetworkX로 물러나되, 그 타일 수를 로그에 남긴다.

⚠️ **``--sgg``는 ``sgis_dong_boundary``의 코드 체계다** — 행자부 표준코드가 아니다.
마포구는 11440이 아니라 **11140**이고, 금천구는 11545가 아니라 **11180**이다.
확인은 ``SELECT DISTINCT left(adm_cd,5), min(adm_nm) FROM sgis_dong_boundary
GROUP BY 1``로 한다.

Run:
  # 먼저 몇 타일만 (DB 필요)
  cd backend && .venv/Scripts/python -m tools.compute_segment_structure --limit 5
  # 구 하나만 (마포구 = 11140, 타일 104개)
  cd backend && .venv/Scripts/python -m tools.compute_segment_structure --sgg 11140
  # 서울 전체 (타일 1,786개)
  cd backend && .venv/Scripts/python -m tools.compute_segment_structure
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import statistics
import sys
import time
from collections import Counter

import psycopg2.extras

from app.services.graph_builder import build_graph
from app.services.space_syntax import WALK, compute_metrics_preferred
from app.services.spatial_query import fetch_roads_in_radius, get_connection
from app.services.undervalued_areas import rank_percentile

logger = logging.getLogger(__name__)

# 분석 반경 — 동 단위 배치(STRUCTURE_RADIUS_M=500)와 같게 맞춘다. 다르게 하면
# 두 단위의 결과를 나란히 볼 수 없다.
ANALYSIS_RADIUS_M = 500
# 코어 타일 한 변. 작을수록 타일이 많아지지만 한 타일이 가벼워진다. 밀집 서울에서
# 반경 900m 남짓을 가져오면 세그먼트가 수천 개가 되어 각도 그래프가 무거워진다.
TILE_M = 800
# 코어 밖으로 더 가져오는 여유. 분석 반경보다 작으면 코어 세그먼트의 반경이
# 잘리므로 절대 줄이지 않는다.
HALO_M = ANALYSIS_RADIUS_M

# 타일 안 세그먼트가 이보다 적으면 백분위가 무의미하다(한두 개가 순위를 지배).
MIN_TILE_SEGMENTS = 10

_UPSERT_SQL = """
INSERT INTO road_segment_structure (
  seg_key, osm_id, highway, structure_raw, choice_raw, integration_raw,
  structure_pct_in_tile, tile_key, tile_segments, radius_m, geom_5179, computed_at
) VALUES %s
ON CONFLICT (seg_key) DO UPDATE SET
  structure_raw = EXCLUDED.structure_raw,
  choice_raw = EXCLUDED.choice_raw,
  integration_raw = EXCLUDED.integration_raw,
  structure_pct_in_tile = EXCLUDED.structure_pct_in_tile,
  tile_key = EXCLUDED.tile_key,
  tile_segments = EXCLUDED.tile_segments,
  radius_m = EXCLUDED.radius_m,
  geom_5179 = EXCLUDED.geom_5179,
  computed_at = NOW()
"""
_VALUES_TEMPLATE = (
    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
    " ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 5179), NOW())"
)


def seg_key(osm_id: int, coordinates: list[list[float]]) -> str:
    """세그먼트 안정 식별자. roads_clean에 PK가 없고 osm_id는 분할되면 겹친다.

    좌표열을 그대로 해시한다 — fetch가 기하를 자르지 않으므로 어느 타일에서
    봐도 같은 값이 나온다. 소수점은 7자리(약 1cm)로 잘라 부동소수점 표기 차이를
    없앤다.
    """
    rounded = [[round(x, 7), round(y, 7)] for x, y in coordinates]
    digest = hashlib.sha1(
        json.dumps(rounded, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return f"{osm_id}:{digest}"


def midpoint(coordinates: list[list[float]]) -> tuple[float, float]:
    """좌표열의 중앙 정점(길이 기준 중점이 아니라 정점 기준).

    타일 배정에만 쓰므로 정확한 선형 중점일 필요는 없다 — 중요한 건 어느 타일에서
    보든 **같은 값**이 나오는 결정론성이다.
    """
    mid = len(coordinates) // 2
    if len(coordinates) % 2 == 1:
        x, y = coordinates[mid]
        return float(x), float(y)
    (x1, y1), (x2, y2) = coordinates[mid - 1], coordinates[mid]
    return (float(x1) + float(x2)) / 2.0, (float(y1) + float(y2)) / 2.0


def area_bbox(conn, sgg: str | None = None) -> tuple[float, float, float, float]:
    """분석할 범위의 WGS84 bbox (min_lng, min_lat, max_lng, max_lat).

    ``sgg``(시군구코드 5자리)를 주면 그 구만, 없으면 서울 전체.

    구 하나만 돌리는 길을 열어 둔 이유: 서울 전체는 타일 1,786개다. 발표·검증처럼
    "실제 숫자 하나"가 급할 때는 구 하나(마포구 기준 104개)면 충분하고, 그게
    "전체 준비 중"보다 훨씬 쓸모 있다.

    ⚠️ 구 경계로 자르면 그 구 **바깥 도로가 계산에 안 들어오는 게 아니다** —
    타일마다 halo만큼 더 가져오므로 경계 근처 세그먼트도 이웃 구의 도로망을
    함께 본다. 다만 이웃 구 쪽 세그먼트는 저장되지 않는다(코어 밖이므로).
    """
    where = "WHERE adm_cd LIKE %s" if sgg else ""
    params = (sgg + "%",) if sgg else None
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)
            FROM (SELECT ST_Extent(ST_Transform(geom, 4326)) AS e
                  FROM sgis_dong_boundary {where}) s
            """,
            params,
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        raise SystemExit(
            f"경계를 찾지 못했다 (sgg={sgg or '전체'}) — sgis_dong_boundary를 확인할 것"
        )
    return float(row[0]), float(row[1]), float(row[2]), float(row[3])


def tile_grid(bbox: tuple[float, float, float, float], tile_m: int) -> list[dict]:
    """bbox를 tile_m 정사각형으로 덮는 타일 목록.

    위도 1도는 약 111.32km로 두고, 경도 1도는 위도에 따라 줄어드는 것을 반영한다
            (cos(lat)). 서울 범위에서는 이 근사로 충분하다 — 타일이 정확한
    정사각형이어야 할 이유가 없고, 코어 배정만 결정론적이면 된다.
    """
    min_lng, min_lat, max_lng, max_lat = bbox
    deg_lat = tile_m / 111_320.0
    mid_lat = (min_lat + max_lat) / 2.0
    deg_lng = tile_m / (111_320.0 * math.cos(math.radians(mid_lat)))

    tiles: list[dict] = []
    n_rows = int(math.ceil((max_lat - min_lat) / deg_lat))
    n_cols = int(math.ceil((max_lng - min_lng) / deg_lng))
    for r in range(n_rows):
        for c in range(n_cols):
            lat0 = min_lat + r * deg_lat
            lng0 = min_lng + c * deg_lng
            tiles.append(
                {
                    "key": f"{r}_{c}",
                    "lng0": lng0,
                    "lat0": lat0,
                    "lng1": lng0 + deg_lng,
                    "lat1": lat0 + deg_lat,
                    "center_lng": lng0 + deg_lng / 2.0,
                    "center_lat": lat0 + deg_lat / 2.0,
                }
            )
    return tiles


def fetch_radius_for(tile_m: int) -> int:
    """코어 어디에 있든 주변 HALO_M이 들어오도록 하는 fetch 반경."""
    half_diagonal = tile_m * math.sqrt(2) / 2.0
    return int(math.ceil(half_diagonal + HALO_M))


def in_core(tile: dict, lng: float, lat: float) -> bool:
    """중점이 이 타일의 코어 안인가. 경계는 아래·왼쪽 포함(겹침 없이 정확히 하나)."""
    return tile["lng0"] <= lng < tile["lng1"] and tile["lat0"] <= lat < tile["lat1"]


def process_tile(tile: dict, radius_m: int) -> tuple[list[tuple], str | None]:
    """타일 하나 → (upsert할 행들, 쓴 엔진). 도로가 없거나 코어가 비면 빈 리스트."""
    segments = fetch_roads_in_radius(
        tile["center_lng"], tile["center_lat"], radius_m, mode="walk"
    )
    if not segments:
        return [], None

    # 코어에 속하는 세그먼트가 하나도 없으면 계산 자체를 건너뛴다 (서울 밖 타일).
    core_idx = [
        i for i, s in enumerate(segments) if in_core(tile, *midpoint(s.coordinates))
    ]
    if not core_idx:
        return [], None

    graph, _ = build_graph(segments)
    if graph.number_of_edges() == 0:
        return [], None
    metrics, engine = compute_metrics_preferred(graph, segments, modes=(WALK,))
    choice = metrics[WALK]["choice"]
    integration = metrics[WALK]["integration"]
    if not choice or not integration:
        return [], engine

    # 백분위는 **코어 세그먼트들 사이에서** 매긴다. halo 세그먼트를 분포에 넣으면
    # 타일마다 halo 비중이 달라 백분위가 흔들린다.
    combined = {
        i: (choice.get(i, 0.0) + integration.get(i, 0.0)) / 2.0 for i in core_idx
    }
    dist = list(combined.values())
    if len(dist) < MIN_TILE_SEGMENTS:
        logger.debug("타일 %s 코어 세그먼트 %d개 — 건너뜀", tile["key"], len(dist))
        return [], engine

    rows: list[tuple] = []
    for i in core_idx:
        s = segments[i]
        rows.append(
            (
                seg_key(s.osm_id, s.coordinates),
                s.osm_id,
                s.highway,
                round(combined[i], 6),
                round(choice.get(i, 0.0), 6),
                round(integration.get(i, 0.0), 6),
                rank_percentile(combined[i], dist),
                tile["key"],
                len(dist),
                ANALYSIS_RADIUS_M,
                json.dumps({"type": "LineString", "coordinates": s.coordinates}),
            )
        )
    return rows, engine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tile-m", type=int, default=TILE_M, help="코어 타일 한 변(m)")
    parser.add_argument("--sgg", default=None,
                        help="시군구코드 5자리 하나만 (예: 11440 마포구). 생략하면 서울 전체")
    parser.add_argument("--limit", type=int, default=None, help="처리할 타일 수 제한(검증용)")
    parser.add_argument("--dry-run", action="store_true", help="계산만 하고 저장하지 않음")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    radius_m = fetch_radius_for(args.tile_m)
    logger.info(
        "타일 %dm, fetch 반경 %dm, 분석 반경 %dm", args.tile_m, radius_m, ANALYSIS_RADIUS_M
    )

    with get_connection() as conn:
        tiles = tile_grid(area_bbox(conn, args.sgg), args.tile_m)
        if args.limit:
            # 가운데부터 — 바깥 타일은 대부분 서울 밖이라 검증에 쓸모가 없다.
            mid = len(tiles) // 2
            tiles = tiles[mid : mid + args.limit]
        logger.info("범위 %s — 타일 %d개 처리 시작", args.sgg or "서울 전체", len(tiles))

        total = kept = empty = 0
        durations: list[float] = []
        engines: Counter[str] = Counter()
        for i, tile in enumerate(tiles, 1):
            t0 = time.time()
            try:
                rows, engine = process_tile(tile, radius_m)
            except Exception as exc:  # 타일 하나가 죽어도 배치는 계속한다
                logger.warning("타일 %s 실패: %s: %s", tile["key"], type(exc).__name__, exc)
                continue
            if engine:
                engines[engine] += 1
            dt = time.time() - t0
            durations.append(dt)
            if not rows:
                empty += 1
            else:
                kept += len(rows)
                if not args.dry_run:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(
                            cur, _UPSERT_SQL, rows, template=_VALUES_TEMPLATE
                        )
                    conn.commit()
            total += 1
            if i % 20 == 0 or i == len(tiles):
                logger.info(
                    "[%d/%d] 세그먼트 %d개 저장, 빈 타일 %d, 중위 %.1fs, 엔진 %s",
                    i, len(tiles), kept, empty, statistics.median(durations),
                    dict(engines),
                )

        logger.info(
            "완료 — 타일 %d개(빈 타일 %d), 세그먼트 %d개, 엔진 %s%s",
            total, empty, kept, dict(engines),
            " (dry-run, 저장 안 함)" if args.dry_run else "",
        )
        # NetworkX로 물러난 타일이 있으면 조용히 넘어가지 않는다 — 그 타일만
        # 수십 배 느리고, 두 엔진의 Choice 순위상관이 1.0이 아니라(실측 0.88)
        # 섞이면 타일 간 백분위에 미세한 계통차가 생긴다.
        if engines.get("networkx") and engines.get("depthmapx"):
            logger.warning(
                "타일 %d개가 NetworkX로 물러났다 — depthmapX 실패 로그를 확인할 것",
                engines["networkx"],
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
