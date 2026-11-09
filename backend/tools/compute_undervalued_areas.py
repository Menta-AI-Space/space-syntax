""""뜰 자리" 배치 — 서울 426개 행정동의 구조 점수·실거래가를 계산해 저장한다.

각 동 중심점에서 site_score/analyze와 같은 엔진으로 500m 도보 반경을 분석하고
(choice_walk+integration_walk)/2의 평균을 그 동의 구조 점수로 쓴다. 가격은 동
폴리곤 안 최근 24개월 실거래(``real_estate_trades``) ㎡당가 중앙값이다. 두 값을
서울 426개 동 분포에서의 백분위로 바꾼 뒤 갭 점수(구조 백분위-가격 백분위)를
계산해 ``undervalued_area_scores``에 저장한다 (undervalued_areas.py 참고).

동 하나당 도로망 분석 1회(수 초)가 걸리므로 전체는 온라인 요청이 아니라
오프라인 배치다 — precompute_analysis_cache.py와 같은 원칙.

Run: cd backend && .venv/Scripts/python -m tools.compute_undervalued_areas
     --limit 20 (동 개수를 제한해 먼저 검증하고 싶을 때)
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time

import psycopg2.extras

from app.services.graph_builder import build_graph
from app.services.space_syntax import WALK, compute_metrics_preferred
from app.services.spatial_query import fetch_roads_in_radius, get_connection
from app.services.undervalued_areas import DongRawInput, compute_gap_scores

logger = logging.getLogger(__name__)

STRUCTURE_RADIUS_M = 500
PRICE_LOOKBACK_MONTHS = 24
MIN_TRADE_SAMPLE = 3  # 이보다 적으면 중앙값을 신뢰하지 않고 가격을 비운다


def fetch_dong_centroids(conn, limit: int | None = None) -> list[tuple[str, str, float, float]]:
    """(adm_cd, adm_nm, lng, lat) — 경계 폴리곤 중심점을 WGS84로 변환해 받는다."""
    sql = """
        SELECT adm_cd, adm_nm,
               ST_X(ST_Transform(ST_Centroid(geom), 4326)) AS lng,
               ST_Y(ST_Transform(ST_Centroid(geom), 4326)) AS lat
        FROM sgis_dong_boundary
        ORDER BY adm_cd
    """
    if limit:
        sql += " LIMIT %s"
    with conn.cursor() as cur:
        cur.execute(sql, (limit,) if limit else None)
        return cur.fetchall()


def fetch_price_median(conn, adm_cd: str) -> tuple[float | None, int]:
    """동 폴리곤 안, 최근 24개월, 정상거래(해제 제외)의 ㎡당가 중앙값 + 표본 수."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.price_per_sqm
            FROM real_estate_trades t
            JOIN sgis_dong_boundary b ON b.adm_cd = %s
            WHERE ST_Contains(b.geom, t.geom_5179)
              AND t.deal_date >= (CURRENT_DATE - (%s || ' months')::interval)
              AND t.cdeal_type != 'O'
              AND t.price_per_sqm IS NOT NULL
            """,
            (adm_cd, PRICE_LOOKBACK_MONTHS),
        )
        values = [row[0] for row in cur.fetchall() if row[0] is not None]
    if len(values) < MIN_TRADE_SAMPLE:
        return None, len(values)
    return round(statistics.median(values), 1), len(values)


def compute_structure_score(lng: float, lat: float) -> float | None:
    """500m 도보 반경 (choice_walk+integration_walk) 평균. 도로망이 없으면 None."""
    segments = fetch_roads_in_radius(lng, lat, STRUCTURE_RADIUS_M, mode="walk")
    if not segments:
        return None
    graph, _ = build_graph(segments)
    if graph.number_of_edges() == 0:
        return None
    # depthmapX(C++) 우선, 실패하면 NetworkX — 라우터·세그먼트 배치와 같은 순서다.
    # 여기도 동 426개마다 반경 분석을 돌리므로 엔진 차이가 그대로 배치 시간이 된다.
    metrics, _engine = compute_metrics_preferred(graph, segments, modes=(WALK,))
    choice = metrics[WALK]["choice"]
    integration = metrics[WALK]["integration"]
    if not choice or not integration:
        return None
    combined = [
        (choice.get(i, 0.0) + integration.get(i, 0.0)) / 2.0
        for i in range(len(segments))
    ]
    return round(statistics.mean(combined), 4) if combined else None


def upsert_scores(conn, scores) -> None:
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO undervalued_area_scores (
                adm_cd, adm_nm, structure_score, structure_percentile,
                price_per_sqm_manwon, price_percentile, trade_sample_count,
                gap_score, computed_at
            ) VALUES %s
            ON CONFLICT (adm_cd) DO UPDATE SET
                adm_nm = EXCLUDED.adm_nm,
                structure_score = EXCLUDED.structure_score,
                structure_percentile = EXCLUDED.structure_percentile,
                price_per_sqm_manwon = EXCLUDED.price_per_sqm_manwon,
                price_percentile = EXCLUDED.price_percentile,
                trade_sample_count = EXCLUDED.trade_sample_count,
                gap_score = EXCLUDED.gap_score,
                computed_at = EXCLUDED.computed_at
            """,
            # computed_at은 template의 NOW() 리터럴이 채운다 — 튜플에 넣으면
            # 플레이스홀더(8개)보다 값이 하나 많아 psycopg2가 죽는다.
            [
                (
                    s.adm_cd, s.adm_nm, s.structure_score, s.structure_percentile,
                    s.price_per_sqm_manwon, s.price_percentile, s.trade_sample_count,
                    s.gap_score,
                )
                for s in scores
            ],
            template="(%s, %s, %s, %s, %s, %s, %s, %s, NOW())",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="처리할 동 개수 제한 (검증용)")
    parser.add_argument("--dry-run", action="store_true", help="계산만 하고 저장하지 않음")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    with get_connection() as conn:
        centroids = fetch_dong_centroids(conn, args.limit)
        logger.info("동 %d개 처리 시작", len(centroids))

        raw: list[DongRawInput] = []
        for i, (adm_cd, adm_nm, lng, lat) in enumerate(centroids, 1):
            t0 = time.time()
            structure = compute_structure_score(lng, lat)
            price, sample_n = fetch_price_median(conn, adm_cd)
            raw.append(
                DongRawInput(
                    adm_cd=adm_cd, adm_nm=adm_nm,
                    structure_score=structure,
                    price_per_sqm_manwon=price,
                    trade_sample_count=sample_n,
                )
            )
            logger.info(
                "[%d/%d] %s 구조=%s 가격=%s(n=%d) %.1fs",
                i, len(centroids), adm_nm, structure, price, sample_n, time.time() - t0,
            )

        scores = compute_gap_scores(raw)
        skipped = len(raw) - len(scores)
        if skipped:
            logger.warning("도로망을 찾지 못해 제외된 동 %d개", skipped)

        if args.dry_run:
            top5 = sorted(
                (s for s in scores if s.gap_score is not None),
                key=lambda s: s.gap_score,
                reverse=True,
            )[:5]
            for s in top5:
                logger.info(
                    "dry-run 상위: %s 구조%.0f%% 가격%.0f%% 갭%.1f",
                    s.adm_nm, s.structure_percentile, s.price_percentile, s.gap_score,
                )
            return 0

        upsert_scores(conn, scores)
        logger.info("완료 — %d개 동 저장", len(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())
