"""2단계 배치 — 필지에 세그먼트 구조·공시지가·용도지역을 붙여 갭 점수를 저장한다.

선행: ``compute_segment_structure.py``(1단계)로 ``road_segment_structure``가 채워져
있어야 하고, ``land_prices``(개별공시지가)와 ``parcel_characteristics``(용도지역·
지목·면적·도로접면)가 적재돼 있어야 한다. 뒤의 둘은 같은 V-World 키로 받는다
(``ingest_land_prices_api.py``, ``ingest_parcel_characteristics.py``). 계산 규칙은 ``app/services/parcel_undervalued.py``에 있고 여기서는
데이터를 모아 넣고 결과를 저장하는 일만 한다.

**시군구 단위로 나눠 처리한다** — 이유가 둘이다:

1. 서울 필지가 약 87만 개다. 한 번에 파이썬으로 올리면 메모리가 위험하다.
2. 그런데 계층(stratum)은 함께 있어야 백분위가 나온다. 시군구로 자르면
   ``dong_zone``과 ``sgg_zone``은 한 덩어리 안에 온전히 들어간다. 가장 넓은
   ``zone`` 단계만 "서울 전체의 용도지역"이 아니라 "그 시군구의 용도지역"이 되는데,
   이건 통제가 **더 강해지는** 방향이라 문제가 아니다 (강남과 도봉을 한 분포에
   두지 않는 것이 원래 목적이었다).

토지특성은 2015년부터라 그 이전 연도는 용도지역이 붙지 않아 판정에서 빠진다.
공시지가는 2009년부터 있으므로 두 표의 연도 범위가 다르다는 점에 주의.

**구조값으로 raw가 아니라 타일 내 백분위를 쓴다.** raw Choice는 그래프 크기에
대략 비례해서(``normalized=False``) 타일 간 비교가 불가능하다 — 1단계 docstring 참고.

Run:
  # 최신 연도, 한 구만 먼저
  cd backend && .venv/Scripts/python -m tools.compute_parcel_undervalued --sgg 11680 --dry-run
  # 전체
  cd backend && .venv/Scripts/python -m tools.compute_parcel_undervalued
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter

import psycopg2.extras

from app.services.land_use_zones import is_buildable, normalize_zone_name
from app.services.parcel_undervalued import (
    ParcelRawInput,
    compute_parcel_gaps,
)
from app.services.spatial_query import get_connection

logger = logging.getLogger(__name__)

# 필지 대표점에서 이보다 먼 세그먼트는 "접면 도로"로 보지 않는다. 그보다 멀면
# 그 세그먼트의 구조가 이 필지의 접근성이라고 말하기 어렵다 — 값을 비우고 제외한다.
MAX_SEGMENT_DISTANCE_M = 50

# 특수지구분에서 제외할 것. 도로·하천 필지는 매매 대상이 아니라 판정 의미가 없다.
EXCLUDED_SPECIAL_NAMES = ("도로", "하천", "구거", "제방", "수도용지")
# 지목(토지특성 land_category)으로도 같은 것을 거른다.
EXCLUDED_LAND_CATEGORIES = ("도로", "하천", "구거", "제방", "수도용지", "공원")

_SGG_SQL = """
SELECT DISTINCT LEFT(adm_cd, 5) AS sgg
FROM sgis_dong_boundary
ORDER BY sgg
"""

_LATEST_YEAR_SQL = "SELECT MAX(base_year) FROM land_prices"

# 필지 하나당 최근접 세그먼트 1개를 LATERAL KNN으로 뽑는다 (GIST 인덱스 사용).
_PARCEL_SQL = """
SELECT
    lp.pnu,
    lp.price_per_sqm,
    b.adm_cd,
    ch.zone_name_1 AS zone_name,
    ch.road_side_name,
    ch.area_sqm,
    seg.seg_key,
    seg.structure_pct_in_tile,
    seg.dist_m
FROM land_prices lp
JOIN parcel_centroids pc ON pc.pnu = lp.pnu
JOIN sgis_dong_boundary b ON ST_Contains(b.geom, pc.geom_5179)
LEFT JOIN LATERAL (
    SELECT s.seg_key, s.structure_pct_in_tile,
           s.geom_5179 <-> pc.geom_5179 AS dist_m
    FROM road_segment_structure s
    ORDER BY s.geom_5179 <-> pc.geom_5179
    LIMIT 1
) seg ON TRUE
-- 용도지역은 폴리곤 공간조인이 아니라 토지특성(같은 PNU·연도)에서 온다.
-- V-World getLandCharacteristics가 용도지역·지목·면적·도로접면을 한꺼번에 주므로
-- 용도지역지구도 WFS를 적재해 공간조인할 필요가 없다(2026-08-22 확인).
LEFT JOIN parcel_characteristics ch
       ON ch.pnu = lp.pnu AND ch.base_year = lp.base_year
WHERE lp.base_year = %s
  AND LEFT(b.adm_cd, 5) = %s
  AND lp.price_per_sqm > 0
  -- 도로·하천 필지 제외. 특수지구분과 지목 양쪽으로 거른다 — 한쪽이 비어 있는
  -- 필지가 있어서(토지특성 미적재분 등) 둘 다 봐야 새는 걸 막는다.
  AND (lp.special_name IS NULL OR lp.special_name <> ALL(%s))
  AND (ch.land_category IS NULL OR ch.land_category <> ALL(%s))
"""

_UPSERT_SQL = """
INSERT INTO parcel_undervalued_scores (
  pnu, base_year, adm_cd, zone_group, structure_score, price_per_sqm,
  structure_percentile, price_percentile, gap_score,
  stratum_level, stratum_key, stratum_size, seg_key, computed_at
) VALUES %s
ON CONFLICT (pnu, base_year) DO UPDATE SET
  adm_cd = EXCLUDED.adm_cd,
  zone_group = EXCLUDED.zone_group,
  structure_score = EXCLUDED.structure_score,
  price_per_sqm = EXCLUDED.price_per_sqm,
  structure_percentile = EXCLUDED.structure_percentile,
  price_percentile = EXCLUDED.price_percentile,
  gap_score = EXCLUDED.gap_score,
  stratum_level = EXCLUDED.stratum_level,
  stratum_key = EXCLUDED.stratum_key,
  stratum_size = EXCLUDED.stratum_size,
  seg_key = EXCLUDED.seg_key,
  computed_at = NOW()
"""


def build_inputs(
    rows: list[dict], base_year: int, *, unknown_zones: Counter
) -> tuple[list[ParcelRawInput], dict[str, str], Counter]:
    """SQL 결과 → ParcelRawInput. (입력, pnu→seg_key, 제외 사유 집계)

    값이 없는 항목은 None으로 둔다 — 0으로 채우면 "구조가 나쁜 필지"와 "값을 못
    붙인 필지"가 섞인다. 실제 제외는 ``compute_parcel_gaps``가 한다.
    """
    inputs: list[ParcelRawInput] = []
    seg_by_pnu: dict[str, str] = {}
    reasons: Counter = Counter()

    for r in rows:
        zone_group = normalize_zone_name(r["zone_name"])
        if r["zone_name"] and zone_group is None:
            unknown_zones[r["zone_name"]] += 1

        dist = r["dist_m"]
        too_far = dist is None or dist > MAX_SEGMENT_DISTANCE_M
        structure = None if too_far else r["structure_pct_in_tile"]

        if structure is None:
            reasons["접면 도로 없음/너무 멂"] += 1
        if zone_group is None:
            reasons["용도지역 미확인"] += 1
        elif not is_buildable(zone_group):
            reasons["개발 제한 용도지역"] += 1

        if r["seg_key"] and not too_far:
            seg_by_pnu[r["pnu"]] = r["seg_key"]

        inputs.append(
            ParcelRawInput(
                pnu=r["pnu"],
                adm_cd=r["adm_cd"],
                zone_code=zone_group,
                structure_score=structure,
                price_per_sqm=float(r["price_per_sqm"]),
                base_year=base_year,
                buildable=is_buildable(zone_group),
            )
        )
    return inputs, seg_by_pnu, reasons


def process_sgg(
    conn, sgg: str, base_year: int, *, unknown_zones: Counter, dry_run: bool
) -> tuple[int, int, Counter]:
    """시군구 하나 → (조회 필지 수, 판정된 필지 수, 제외 사유)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_PARCEL_SQL, (base_year, sgg, list(EXCLUDED_SPECIAL_NAMES),
                                  list(EXCLUDED_LAND_CATEGORIES)))
        rows = cur.fetchall()
    if not rows:
        return 0, 0, Counter()

    inputs, seg_by_pnu, reasons = build_inputs(
        rows, base_year, unknown_zones=unknown_zones
    )
    scores = compute_parcel_gaps(inputs)
    if not scores or dry_run:
        return len(rows), len(scores), reasons

    values = [
        (
            s.pnu, s.base_year, s.adm_cd, s.zone_code, s.structure_score,
            s.price_per_sqm, s.structure_percentile, s.price_percentile,
            s.gap_score, s.stratum_level, s.stratum_key, s.stratum_size,
            seg_by_pnu.get(s.pnu),
        )
        for s in scores
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur, _UPSERT_SQL, values,
            template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
            page_size=2000,
        )
    conn.commit()
    return len(rows), len(scores), reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=None,
                       help="기준연도. 생략하면 land_prices의 최신 연도")
    parser.add_argument("--sgg", default=None, help="시군구코드 5자리 하나만 (검증용)")
    parser.add_argument("--dry-run", action="store_true", help="계산만 하고 저장하지 않음")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    with get_connection() as conn:
        base_year = args.year
        if base_year is None:
            with conn.cursor() as cur:
                cur.execute(_LATEST_YEAR_SQL)
                row = cur.fetchone()
            base_year = row[0] if row else None
        if base_year is None:
            raise SystemExit("land_prices가 비어 있다 — 개별공시지가를 먼저 적재할 것")
        logger.info("기준연도 %d", base_year)

        if args.sgg:
            sggs = [args.sgg]
        else:
            with conn.cursor() as cur:
                cur.execute(_SGG_SQL)
                sggs = [r[0] for r in cur.fetchall()]
        logger.info("시군구 %d개 처리", len(sggs))

        unknown_zones: Counter = Counter()
        total_reasons: Counter = Counter()
        total_rows = total_scored = 0
        for i, sgg in enumerate(sggs, 1):
            t0 = time.time()
            n_rows, n_scored, reasons = process_sgg(
                conn, sgg, base_year, unknown_zones=unknown_zones, dry_run=args.dry_run
            )
            total_rows += n_rows
            total_scored += n_scored
            total_reasons.update(reasons)
            logger.info(
                "[%d/%d] %s 필지 %d개 중 %d개 판정 (%.1fs)",
                i, len(sggs), sgg, n_rows, n_scored, time.time() - t0,
            )

        logger.info(
            "완료 — 필지 %d개 중 %d개 판정%s",
            total_rows, total_scored, " (dry-run, 저장 안 함)" if args.dry_run else "",
        )
        for reason, n in total_reasons.most_common():
            logger.info("  제외 사유 · %s: %d", reason, n)
        if unknown_zones:
            # 미인식 명칭은 정규화 패턴에 추가할 후보다. 조용히 버리지 않는다.
            logger.warning("미인식 용도지역 명칭 %d종:", len(unknown_zones))
            for name, n in unknown_zones.most_common(20):
                logger.warning("  %r: %d개", name, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
