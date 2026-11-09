"""biz_licenses 업소 → 가장 가까운 도로 세그먼트(road_segment_structure) 연결.

모든 후속 실험(동네 안 배분·생존분석·사건 연구)이 「업소 ↔ 골목」조인 위에서 돈다.
매번 KNN 을 다시 하지 않도록 `seg_key`·`seg_dist_m` 을 표에 박아 둔다.

- 최대 거리 기본 60m. 그 밖이면 NULL 로 두고 실험에서 제외한다(주소 좌표가 건물 중심이라
  이면도로 안쪽 필지는 30~40m 가 흔하다; 60m 를 넘으면 좌표 오류나 대형 필지다).
- 세그먼트 구조 지표(`choice_raw`·`integration_raw`)는 여기서 복사하지 않는다 —
  구조 지표가 재계산되면 seg_key 로 다시 조인하면 된다.

Run:
  cd backend && .venv/Scripts/python -m tools.link_biz_licenses_segments
  cd backend && .venv/Scripts/python -m tools.link_biz_licenses_segments --max-dist 100 --redo
"""

from __future__ import annotations

import argparse
import sys
import time

import psycopg2

from app.config import settings

ALTER = """
ALTER TABLE biz_licenses ADD COLUMN IF NOT EXISTS seg_key text;
ALTER TABLE biz_licenses ADD COLUMN IF NOT EXISTS seg_dist_m double precision;
CREATE INDEX IF NOT EXISTS biz_licenses_seg_idx ON biz_licenses (seg_key);
"""

LINK = """
WITH todo AS (
    SELECT mgt_no, geom_5179
    FROM biz_licenses
    WHERE geom_5179 IS NOT NULL AND ({redo} OR seg_key IS NULL AND seg_dist_m IS NULL)
),
nn AS (
    SELECT t.mgt_no, s.seg_key, ST_Distance(t.geom_5179, s.geom_5179) AS d
    FROM todo t
    CROSS JOIN LATERAL (
        SELECT seg_key, geom_5179
        FROM road_segment_structure r
        ORDER BY r.geom_5179 <-> t.geom_5179
        LIMIT 1
    ) s
)
UPDATE biz_licenses b
SET seg_key = CASE WHEN nn.d <= %(max_dist)s THEN nn.seg_key ELSE NULL END,
    seg_dist_m = nn.d
FROM nn
WHERE b.mgt_no = nn.mgt_no
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-dist", type=float, default=60.0)
    ap.add_argument("--redo", action="store_true", help="이미 연결된 행도 다시 계산")
    args = ap.parse_args()

    conn = psycopg2.connect(settings.database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(ALTER)
            conn.commit()
            t0 = time.time()
            cur.execute(LINK.format(redo="TRUE" if args.redo else "FALSE"), {"max_dist": args.max_dist})
            print(f"연결 갱신 {cur.rowcount:,}행 ({time.time() - t0:.0f}s)", flush=True)
            conn.commit()
            cur.execute(
                """
                SELECT biz_type,
                       COUNT(*) AS n,
                       COUNT(geom_5179) AS with_geom,
                       COUNT(seg_key) AS linked,
                       ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY seg_dist_m)::numeric, 1) AS med_dist,
                       ROUND(percentile_cont(0.9) WITHIN GROUP (ORDER BY seg_dist_m)::numeric, 1) AS p90_dist
                FROM biz_licenses GROUP BY biz_type ORDER BY n DESC
                """
            )
            for row in cur.fetchall():
                print("  ", row)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
