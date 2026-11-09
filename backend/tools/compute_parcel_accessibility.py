"""필지별 교통 접근성 feature 배치 계산 (PPT Feature Pool 6·7·8).

지하철역·출입구·버스정류장·간선도로·drivable 도로까지의 최근접 거리와 반경 내
개수를 필지(PNU)마다 계산해 ``parcel_accessibility``에 넣는다. 스키마와 결측
규칙의 근거는 ``scripts/migrations/018_parcel_accessibility.sql`` 주석에 있다.

**왜 시설 좌표를 임시 표로 옮겨 담나** — ``planet_osm_point.way``는 EPSG:4326이고
필지는 5179다(2026-08-24 실측. 3857이 아니다). 4326의 GIST 인덱스는 있지만
``ST_Transform(way, 5179)``를 걸면 그 인덱스를 못 쓰고, 필지 5만 개마다 20만 행을
변환하면 KNN이 인덱스 없이 돈다. 그래서 필요한 시설만(역 470 · 출입구 2,217 ·
정류장 16,770) 세션 TEMP 표에 5179로 미리 옮기고 GIST를 새로 만든다. 원본 표를
건드리지 않고, 전체가 분 단위로 끝난다.

**왜 구(sgg) 단위로 도나** — 금천구 19,830 + 강남구 34,566필지다. 구 하나씩
가져오면 파이썬 메모리가 안전하고, 실패 시 그 구만 다시 돌리면 된다. 이 표는
필지 간 백분위 같은 계층 계산이 없어서 구를 잘라도 값이 달라지지 않는다
(``compute_parcel_undervalued.py``와 다른 점).

**결측을 0으로 채우지 않는다.** 거리가 없으면 NULL, 개수가 없으면 0이다.
0으로 채운 거리는 "정보 없음"을 "바로 옆"으로 학습시킨다. 서울 안에서는 최근접
시설이 항상 존재하므로 거리 NULL이 많이 나오면 좌표계가 틀렸다는 신호다.

Run:
  # 마이그레이션 먼저
  psql ... -f scripts/migrations/018_parcel_accessibility.sql
  # 한 구만 검산 (저장 안 함)
  cd backend && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe \
      -m tools.compute_parcel_accessibility --sgg 11545 --dry-run
  # 금천구 + 강남구 적재
  cd backend && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe \
      -m tools.compute_parcel_accessibility
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date
from statistics import median

import psycopg2.extras

from app.services.spatial_query import get_connection

logger = logging.getLogger(__name__)

# 계산 대상 기본값. 전체 서울(87만 필지)은 의도적으로 기본에서 뺐다 — 먼저 두 구로
# 검산하고 값이 맞는 걸 확인한 뒤 넓힌다. 금천구는 지하철이 드문 외곽,
# 강남구는 역·버스가 조밀한 도심이라 양극단 표본이 된다.
DEFAULT_SGG_CODES = ("11545", "11680")  # 금천구, 강남구

# 간선도로 등급. 근거는 018 마이그레이션 주석 참고 (한국 OSM 태깅 관행 +
# 이미 프로젝트가 쓰는 경계 + 램프(_link) 제외).
ARTERIAL_HIGHWAY_GRADES = ("motorway", "trunk", "primary", "secondary")

# 반경. 지하철 500/1000m는 국내 역세권 논의의 관행값(1차/2차 역세권)이고,
# 버스 300/500m는 도보 3~7분에 대응한다.
STATION_RADII_M = (500, 1000)
BUS_RADII_M = (300, 500)

# 최근접 탐색을 이 반경으로 자른다. KNN(`<->`)만으로도 인덱스를 타지만, 상한이
# 없으면 서울 밖 시설까지 훑는 경우가 생겨 느려진다. 서울 안에서 역까지 8km를
# 넘는 필지는 없으므로(실측 최대 약 2km) 넉넉한 상한이다.
MAX_SEARCH_M = 10_000

# 저장 컬럼 순서. UPSERT SQL과 build_row가 이 순서를 공유한다.
FEATURE_COLUMNS = (
    "nearest_station_m",
    "nearest_station_name",
    "nearest_entrance_m",
    "stations_within_500m",
    "stations_within_1000m",
    "nearest_bus_stop_m",
    "bus_stops_within_300m",
    "bus_stops_within_500m",
    "nearest_arterial_m",
    "nearest_road_m",
)
INSERT_COLUMNS = ("pnu", "sgg_code", *FEATURE_COLUMNS, "as_of")

# 거리 컬럼만 따로 — 결측률·분포 보고에서 개수 컬럼과 섞으면 안 된다
# (개수 0은 결측이 아니라 값이다).
DISTANCE_COLUMNS = tuple(c for c in FEATURE_COLUMNS if c.endswith("_m"))


# --- 시설 임시 표 ------------------------------------------------------------
# planet_osm_point.way는 4326이므로 여기서 5179로 옮긴다. ⚠️ ST_Transform을
# 빼먹으면 오류 없이 도(度) 단위 좌표가 m처럼 취급돼 거리가 전부 엉뚱해진다.
#
# 역은 point(462개)와 polygon(8개) 양쪽에 있다. 폴리곤은 중심점으로 바꿔 합치되,
# **같은 역이 노드로도 있으면 중복이므로** 100m 안에 노드 역이 있는 폴리곤은
# 버린다 (안 버리면 stations_within_500m가 부풀려진다).
_TEMP_SQL = f"""
CREATE TEMP TABLE acc_station ON COMMIT DROP AS
WITH pt AS (
    SELECT name, ST_Transform(way, 5179) AS geom
    FROM planet_osm_point
    WHERE railway = 'station'
), poly AS (
    SELECT name, ST_Centroid(ST_Transform(way, 5179)) AS geom
    FROM planet_osm_polygon
    WHERE railway = 'station'
)
SELECT name, geom FROM pt
UNION ALL
SELECT p.name, p.geom FROM poly p
WHERE NOT EXISTS (
    SELECT 1 FROM pt WHERE ST_DWithin(pt.geom, p.geom, 100)
);

CREATE TEMP TABLE acc_entrance ON COMMIT DROP AS
SELECT ST_Transform(way, 5179) AS geom
FROM planet_osm_point
WHERE railway = 'subway_entrance';

CREATE TEMP TABLE acc_bus ON COMMIT DROP AS
SELECT ST_Transform(way, 5179) AS geom
FROM planet_osm_point
WHERE highway = 'bus_stop';

-- 도로는 이미 5179 컬럼이 있고 GIST도 있지만, 간선만 골라 작은 표로 두면
-- KNN이 훨씬 짧게 끝난다 (203,983 → 약 13,437 세그먼트).
CREATE TEMP TABLE acc_arterial ON COMMIT DROP AS
SELECT geom_5179 AS geom
FROM roads_clean
WHERE highway = ANY(%(arterial)s);

CREATE INDEX ON acc_station USING GIST (geom);
CREATE INDEX ON acc_entrance USING GIST (geom);
CREATE INDEX ON acc_bus USING GIST (geom);
CREATE INDEX ON acc_arterial USING GIST (geom);
ANALYZE acc_station;
ANALYZE acc_entrance;
ANALYZE acc_bus;
ANALYZE acc_arterial;
"""

# 필지 하나당 최근접 5종 + 반경 개수 4종. 최근접은 LATERAL + `<->` + LIMIT 1
# (GIST index-scan), 개수는 ST_DWithin (같은 인덱스를 탄다).
#
# nearest_road_m만 임시 표 대신 roads_clean을 직접 본다 — drivable 도로가
# 120,000개가 넘어 복사 비용이 KNN 이득보다 크고, 부분 인덱스
# ``roads_clean_drivable_idx``가 이미 있다.
_PARCEL_SQL = f"""
SELECT
    pc.pnu,
    LEFT(pc.pnu, 5)                       AS sgg_code,
    st.dist_m                             AS nearest_station_m,
    st.name                               AS nearest_station_name,
    en.dist_m                             AS nearest_entrance_m,
    (SELECT count(*) FROM acc_station s
      WHERE ST_DWithin(s.geom, pc.geom_5179, {STATION_RADII_M[0]}))  AS stations_within_500m,
    (SELECT count(*) FROM acc_station s
      WHERE ST_DWithin(s.geom, pc.geom_5179, {STATION_RADII_M[1]}))  AS stations_within_1000m,
    bs.dist_m                             AS nearest_bus_stop_m,
    (SELECT count(*) FROM acc_bus b
      WHERE ST_DWithin(b.geom, pc.geom_5179, {BUS_RADII_M[0]}))      AS bus_stops_within_300m,
    (SELECT count(*) FROM acc_bus b
      WHERE ST_DWithin(b.geom, pc.geom_5179, {BUS_RADII_M[1]}))      AS bus_stops_within_500m,
    ar.dist_m                             AS nearest_arterial_m,
    rd.dist_m                             AS nearest_road_m
FROM parcel_centroids pc
LEFT JOIN LATERAL (
    SELECT s.name, s.geom <-> pc.geom_5179 AS dist_m
    FROM acc_station s
    WHERE ST_DWithin(s.geom, pc.geom_5179, {MAX_SEARCH_M})
    ORDER BY s.geom <-> pc.geom_5179
    LIMIT 1
) st ON TRUE
LEFT JOIN LATERAL (
    SELECT e.geom <-> pc.geom_5179 AS dist_m
    FROM acc_entrance e
    WHERE ST_DWithin(e.geom, pc.geom_5179, {MAX_SEARCH_M})
    ORDER BY e.geom <-> pc.geom_5179
    LIMIT 1
) en ON TRUE
LEFT JOIN LATERAL (
    SELECT b.geom <-> pc.geom_5179 AS dist_m
    FROM acc_bus b
    WHERE ST_DWithin(b.geom, pc.geom_5179, {MAX_SEARCH_M})
    ORDER BY b.geom <-> pc.geom_5179
    LIMIT 1
) bs ON TRUE
LEFT JOIN LATERAL (
    SELECT a.geom <-> pc.geom_5179 AS dist_m
    FROM acc_arterial a
    WHERE ST_DWithin(a.geom, pc.geom_5179, {MAX_SEARCH_M})
    ORDER BY a.geom <-> pc.geom_5179
    LIMIT 1
) ar ON TRUE
LEFT JOIN LATERAL (
    SELECT r.geom_5179 <-> pc.geom_5179 AS dist_m
    FROM roads_clean r
    WHERE r.drivable
      AND ST_DWithin(r.geom_5179, pc.geom_5179, {MAX_SEARCH_M})
    ORDER BY r.geom_5179 <-> pc.geom_5179
    LIMIT 1
) rd ON TRUE
WHERE LEFT(pc.pnu, 5) = %(sgg)s
"""

# 재실행해도 행이 늘지 않게 upsert. 키는 (pnu) 하나다 — 이 표는 시불변이라
# 연도 축이 없다 (018 주석 참고).
_UPSERT_SQL = f"""
INSERT INTO parcel_accessibility ({", ".join(INSERT_COLUMNS)}, computed_at)
VALUES %s
ON CONFLICT (pnu) DO UPDATE SET
{",".join(f"  {c} = EXCLUDED.{c}" for c in ("sgg_code", *FEATURE_COLUMNS, "as_of"))},
  computed_at = NOW()
"""

# OSM 스냅샷 기준일. 값이 아니라 **표 전체의 시점**이므로 행마다 같은 값이 들어간다.
_AS_OF_SQL = """
SELECT value::timestamptz::date
FROM osm2pgsql_properties
WHERE property = 'import_timestamp'
"""
# osm2pgsql_properties가 없는 DB(운영 최소 집합 등)를 위한 폴백.
FALLBACK_AS_OF = date(2026, 6, 30)


@dataclass(frozen=True)
class AccessibilityRow:
    """저장 직전 한 행. 거리는 None을 그대로 들고 있는다 (0으로 채우지 않는다)."""

    pnu: str
    sgg_code: str
    nearest_station_m: float | None
    nearest_station_name: str | None
    nearest_entrance_m: float | None
    stations_within_500m: int
    stations_within_1000m: int
    nearest_bus_stop_m: float | None
    bus_stops_within_300m: int
    bus_stops_within_500m: int
    nearest_arterial_m: float | None
    nearest_road_m: float | None
    as_of: date

    def as_tuple(self) -> tuple:
        """INSERT_COLUMNS 순서 그대로."""
        return tuple(getattr(self, c) for c in INSERT_COLUMNS)


def clean_distance(value) -> float | None:
    """거리 정리 — **결측은 None으로 남긴다.**

    0으로 채우면 "반경 안에 아무것도 없다"가 "바로 옆에 있다"로 학습된다.
    음수는 있을 수 없으므로(``<->``는 항상 ≥0) 나오면 데이터 이상으로 보고 버린다.
    """
    if value is None:
        return None
    v = float(value)
    if v < 0:
        return None
    return v


def clean_count(value) -> int:
    """개수 정리 — 여기서는 **0이 정답이다**.

    ``count(*)``는 NULL을 내지 않지만, 방어적으로 None을 0으로 본다. 거리와 달리
    "반경 안에 아무것도 없음"이 0이라는 정보 자체이므로 결측과 구분할 필요가 없다.
    """
    if value is None:
        return 0
    return int(value)


def build_row(raw: dict, as_of: date) -> AccessibilityRow:
    """SQL 한 행 → AccessibilityRow. 거리/개수 결측 규칙이 여기 한 곳에 모인다."""
    return AccessibilityRow(
        pnu=raw["pnu"],
        sgg_code=raw["sgg_code"],
        nearest_station_m=clean_distance(raw["nearest_station_m"]),
        nearest_station_name=raw.get("nearest_station_name") or None,
        nearest_entrance_m=clean_distance(raw["nearest_entrance_m"]),
        stations_within_500m=clean_count(raw["stations_within_500m"]),
        stations_within_1000m=clean_count(raw["stations_within_1000m"]),
        nearest_bus_stop_m=clean_distance(raw["nearest_bus_stop_m"]),
        bus_stops_within_300m=clean_count(raw["bus_stops_within_300m"]),
        bus_stops_within_500m=clean_count(raw["bus_stops_within_500m"]),
        nearest_arterial_m=clean_distance(raw["nearest_arterial_m"]),
        nearest_road_m=clean_distance(raw["nearest_road_m"]),
        as_of=as_of,
    )


def summarize_distances(rows: list[AccessibilityRow]) -> dict[str, dict]:
    """거리 컬럼별 결측률·중위·최대. 검산 보고에 쓴다.

    결측률을 함께 내는 이유: 좌표계가 틀리면 오류 없이 "전부 NULL" 또는 "전부
    똑같이 거대한 값"이 나온다. 결측률과 중위값이 유일한 조기 경보다.
    """
    out: dict[str, dict] = {}
    for col in DISTANCE_COLUMNS:
        values = [getattr(r, col) for r in rows]
        present = [v for v in values if v is not None]
        out[col] = {
            "n": len(values),
            "missing": len(values) - len(present),
            "missing_pct": (
                100.0 * (len(values) - len(present)) / len(values) if values else 0.0
            ),
            "median": median(present) if present else None,
            "max": max(present) if present else None,
        }
    return out


def create_facility_temps(conn) -> dict[str, int]:
    """시설 임시 표 4개를 만들고 각 행 수를 돌려준다.

    ⚠️ ``ON COMMIT DROP``이므로 **같은 트랜잭션 안에서** 필지 질의까지 끝내야
    한다. 중간에 commit하면 표가 사라진다 — 그래서 upsert는 구별로 하지 않고
    맨 끝에 한 번만 commit한다.
    """
    with conn.cursor() as cur:
        cur.execute(_TEMP_SQL, {"arterial": list(ARTERIAL_HIGHWAY_GRADES)})
        counts = {}
        for name in ("acc_station", "acc_entrance", "acc_bus", "acc_arterial"):
            cur.execute(f"SELECT count(*) FROM {name}")  # noqa: S608 — 상수 이름
            counts[name] = cur.fetchone()[0]
    return counts


def read_as_of(conn) -> date:
    """OSM 스냅샷 기준일을 DB에서 읽는다. 없으면 알려진 값으로 폴백."""
    with conn.cursor() as cur:
        try:
            cur.execute(_AS_OF_SQL)
            row = cur.fetchone()
        except psycopg2.Error:
            conn.rollback()
            return FALLBACK_AS_OF
    if row and row[0]:
        return row[0]
    return FALLBACK_AS_OF


def process_sgg(conn, sgg: str, as_of: date, *, dry_run: bool) -> list[AccessibilityRow]:
    """구 하나를 계산하고 (dry-run이 아니면) upsert한다."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_PARCEL_SQL, {"sgg": sgg})
        raw_rows = cur.fetchall()

    rows = [build_row(r, as_of) for r in raw_rows]
    if not rows or dry_run:
        return rows

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            _UPSERT_SQL,
            [r.as_tuple() for r in rows],
            template="(" + ", ".join(["%s"] * len(INSERT_COLUMNS)) + ", NOW())",
            page_size=2000,
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sgg",
        action="append",
        default=None,
        help=f"시군구코드 5자리. 반복 지정 가능. 생략하면 {DEFAULT_SGG_CODES}",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="계산만 하고 저장하지 않음"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sggs = args.sgg or list(DEFAULT_SGG_CODES)

    with get_connection() as conn:
        as_of = read_as_of(conn)
        logger.info("OSM 스냅샷 기준일(as_of) %s — 이 표는 시불변이다", as_of)

        t0 = time.time()
        counts = create_facility_temps(conn)
        logger.info(
            "시설 임시 표 준비 (%.1fs): 역 %d · 출입구 %d · 정류장 %d · 간선 %d",
            time.time() - t0,
            counts["acc_station"],
            counts["acc_entrance"],
            counts["acc_bus"],
            counts["acc_arterial"],
        )
        if counts["acc_station"] == 0 or counts["acc_bus"] == 0:
            raise SystemExit("시설이 0건이다 — planet_osm_* 적재 상태를 확인할 것")

        all_rows: list[AccessibilityRow] = []
        for i, sgg in enumerate(sggs, 1):
            t1 = time.time()
            rows = process_sgg(conn, sgg, as_of, dry_run=args.dry_run)
            all_rows.extend(rows)
            logger.info(
                "[%d/%d] %s 필지 %d개 (%.1fs)", i, len(sggs), sgg, len(rows),
                time.time() - t1,
            )

        if not args.dry_run:
            # 임시 표가 ON COMMIT DROP이라 여기서 한 번만 commit한다.
            conn.commit()

        logger.info(
            "완료 — 필지 %d개%s",
            len(all_rows),
            " (dry-run, 저장 안 함)" if args.dry_run else "",
        )
        for col, s in summarize_distances(all_rows).items():
            logger.info(
                "  %s: 결측 %d (%.2f%%) · 중위 %s · 최대 %s",
                col,
                s["missing"],
                s["missing_pct"],
                "-" if s["median"] is None else f"{s['median']:.0f}m",
                "-" if s["max"] is None else f"{s['max']:.0f}m",
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
