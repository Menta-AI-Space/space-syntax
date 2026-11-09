"""서울 전역을 «한 그래프»로 놓고 목 지표를 다중 반경으로 계산한다.

**왜 새 도구인가** — `compute_segment_structure.py`는 서울을 800m 타일로 나눠
타일마다 따로 계산한다. 헤일로 덕에 경계 절단은 없지만, 한 세그먼트의 `total_depth`와
betweenness는 **그 타일 부분그래프(코어 800m + 헤일로 500m ≈ 1.8km 창) 안에서만**
계산된다. 즉 기존 `road_segment_structure`는 「반경 500m」가 아니라 **「1.8km 창 안의
radius-n」**이다 (`DECISIONS_LAND_MODEL.md` §22).

§12~18·§21이 기각한 것이 **이론의 한계인지 이 창 크기의 한계인지** 가르려면 서울
전역을 한 판에 놓고 반경을 바꿔 가며 재야 한다. 그게 이 도구다.

**반경은 metric을 쓴다.** 실측(강남역 3,339세그먼트, 2026-09-10):

| 설정 | 초 |
| --- | --- |
| n (전역) | 44.5 |
| 2000m metric | 12.9 |
| 1000m metric | 4.5 |
| 500m metric | 2.8 |

전역(n)은 세그먼트 수에 대해 k≈1.75로 늘어나 서울 20만이면 **약 10시간**이다.
metric 반경은 탐색을 잘라내므로 훨씬 싸고, 무엇보다 **어느 스케일이 매출을
설명하는지**가 우리가 답해야 할 질문이라 여러 반경이 필요하다.

**DB에 쓰지 않는다.** 운영 DB는 읽기 전용이 원칙이고(CLAUDE.md §1) 이 랩탑엔 로컬
PostGIS가 없다. 결과는 CSV로 떨어뜨리고, 검증한 뒤에 별도 단계로 적재한다.

Run:
  # 한 구만 (빠른 검증). sgis_dong_boundary 코드 체계다 — 마포구는 11140
  cd backend && .venv/Scripts/python -m tools.compute_global_segment_structure \
      --sgg 11140 --radii 500,1000 --out C:/dev/tools/scratch/mapo.csv

  # 서울 전역
  cd backend && .venv/Scripts/python -m tools.compute_global_segment_structure \
      --radii 500,1000,2000 --out C:/dev/tools/scratch/seoul_global.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import sys
import time

import psycopg2.extras

from app.services.depthmapx_engine import compute_depthmapx_metrics
from app.services.graph_builder import build_graph
from app.services.spatial_query import RoadSegment, get_connection

logger = logging.getLogger(__name__)

# 기존 타일 배치와 같은 식별자 — 나중에 road_segment_structure 와 조인하려면 같아야 한다.
def seg_key(osm_id: int, coordinates: list[list[float]]) -> str:
    rounded = [[round(x, 7), round(y, 7)] for x, y in coordinates]
    digest = hashlib.sha1(
        json.dumps(rounded, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return f"{osm_id}:{digest}"


_ALL_ROADS_SQL = """
SELECT
    osm_id, highway, length_m, walkable, drivable, layer, bridge, tunnel,
    ST_AsGeoJSON(geom)::json AS geom
FROM roads_clean
WHERE (walkable OR drivable)
"""

# 자치구 한정 — sgis_dong_boundary 는 행자부 표준코드가 아니다(마포 11140).
# ⚠️ 컬럼은 `geom`이고 SRID 5179다. 서브쿼리에서 `geom_5179`를 쓰면 바깥
# roads_clean 컬럼으로 해석돼 "aggregate functions are not allowed in WHERE"가 난다.
# EXISTS로 쓰면 집계 자체가 없어져 그 함정을 피하고 인덱스도 탄다.
_SGG_CLAUSE = """
  AND EXISTS (
        SELECT 1 FROM sgis_dong_boundary b
        WHERE left(b.adm_cd, 5) = ANY(%s)
          AND ST_Intersects(roads_clean.geom_5179, b.geom)
      )
"""


def fetch_all_roads(sgg: str | None) -> list[RoadSegment]:
    """sgg 는 쉼표로 여러 자치구를 받는다. 붙어 있는 구를 묶어야 그래프가 한 덩어리가 된다 —
    space_syntax._prepare_angular_main 이 «가장 큰 연결요소»만 남기므로 떨어진 구를
    섞으면 조용히 버려진다."""
    codes = [c.strip() for c in sgg.split(",") if c.strip()] if sgg else []
    query = _ALL_ROADS_SQL + (_SGG_CLAUSE if codes else "")
    params = (codes,) if codes else ()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    segments: list[RoadSegment] = []
    for row in rows:
        geom = row["geom"]
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        segments.append(
            RoadSegment(
                osm_id=int(row["osm_id"]),
                highway=str(row["highway"]),
                length_m=float(row["length_m"] or 0),
                coordinates=coords,
                walkable=bool(row["walkable"]),
                drivable=bool(row["drivable"]),
                layer=int(row["layer"] or 0),
                bridge=bool(row["bridge"]),
                tunnel=bool(row["tunnel"]),
            )
        )
    return segments


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sgg", default=None,
                        help="자치구 코드 5자리, 쉼표로 복수 가능 (sgis 체계). 생략하면 서울 전역")
    parser.add_argument("--radii", default="500,1000,2000",
                        help="쉼표로 구분. 'n'이면 전역(느리다 — 서울 전체 약 10시간)")
    parser.add_argument("--mode", default="walk", choices=("walk", "drive"))
    parser.add_argument("--out", required=True, help="결과 CSV 경로")
    parser.add_argument("--timeout", type=int, default=86_400, help="depthmapX 한 반경당 상한(초)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    radii = [r.strip() for r in args.radii.split(",") if r.strip()]

    t0 = time.perf_counter()
    segments = fetch_all_roads(args.sgg)
    logger.info("도로 %s개 적재 (%.1f초)", f"{len(segments):,}", time.perf_counter() - t0)
    if not segments:
        logger.error("세그먼트가 없다 — --sgg 코드를 확인하라")
        return 1

    t0 = time.perf_counter()
    graph, _ = build_graph(segments)
    logger.info(
        "그래프 노드 %s · 엣지 %s (%.1f초)",
        f"{graph.number_of_nodes():,}", f"{graph.number_of_edges():,}",
        time.perf_counter() - t0,
    )

    columns: dict[str, dict[int, float]] = {}
    for radius in radii:
        radius_type = "steps" if radius == "n" else "metric"
        t0 = time.perf_counter()
        logger.info("depthmapX 시작 — 반경 %s (%s)", radius, radius_type)
        result, err = compute_depthmapx_metrics(
            graph, segments, modes=(args.mode,),
            radius=radius, radius_type=radius_type, timeout_seconds=args.timeout,
        )
        secs = time.perf_counter() - t0
        if err:
            logger.error("반경 %s 실패 (%.1f초): %s", radius, secs, err)
            continue
        columns[f"nach_r{radius}"] = result[args.mode]["choice"]
        columns[f"nain_r{radius}"] = result[args.mode]["integration"]
        logger.info("반경 %s 완료 — %.1f초", radius, secs)

    if not columns:
        logger.error("성공한 반경이 하나도 없다")
        return 1

    metric_names = list(columns)
    with open(args.out, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["seg_key", "osm_id", "highway", "length_m", *metric_names])
        for i, seg in enumerate(segments):
            writer.writerow([
                seg_key(seg.osm_id, seg.coordinates),
                seg.osm_id, seg.highway, round(seg.length_m, 2),
                *(round(columns[name].get(i, 0.0), 6) for name in metric_names),
            ])
    logger.info("기록 완료 — %s (%s행 · 지표 %d개)", args.out, f"{len(segments):,}", len(metric_names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
