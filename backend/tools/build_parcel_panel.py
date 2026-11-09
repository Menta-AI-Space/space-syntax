"""학습용 통합 패널 ``parcel_panel`` 적재 배치.

스키마와 그 근거는 ``scripts/migrations/022_parcel_panel.sql``에 있다. 이 파일은
그 표의 **feature 컬럼만** 채운다 — 모델 출력 컬럼(``expected_price`` ~
``predicted_at``)은 INSERT에도 UPDATE에도 넣지 않는다. feature를 다시 적재했다고
G2가 만든 예측이 지워지면 안 되기 때문이다(마이그레이션 주석의 명시 요구).

⚠️ 이 모듈이 **Layer A / Layer B 구분의 정본**이다.
개별공시지가 = 비교표준지 × 비준표 배율이고 비준표 항목에 용도지역·지목·이용상황·
도로접면이 들어 있다. 그 변수들(Layer A)로 공시지가를 예측하면 모델은 행정 산식을
재현할 뿐이다 — 높은 R²는 성과가 아니다. 우리가 파는 것은 Layer A를 통제한 뒤
Layer B의 **증분 설명력**이므로, 어느 컬럼이 어느 층인지가 코드에 박혀 있어야 한다.
``tests/test_build_parcel_panel.py``가 이 목록과 실제 표 컬럼이 어긋나지 않게 고정한다.

⚠️ 절대 가져오지 않는 컬럼 (``FORBIDDEN_SOURCE_COLUMNS``):
``gap_score`` · ``price_percentile`` · ``structure_percentile``
(parcel_undervalued_scores) 와 ``price_ratio_to_neighbor_300m``
(parcel_neighbor_prices). 전자는 가격 백분위라 target 그 자체이고, 후자는 분자가
target이다. 진단·화면용으로 필요하면 원본 표를 pnu로 조인해 쓴다 — 복사할 이유가 없다.

조인 키가 컬럼마다 다르다 (마이그레이션이 정한 대로)
-----------------------------------------------------
- Layer A · 이웃 가격 · 종속변수: ``(pnu, base_year)`` — 같은 해 값만 붙인다.
- 접근성(``parcel_accessibility``): **pnu만.** 스냅샷이 하나뿐이라 연도별 값이
  존재하지 않는다. base_year 조인이 없는 것이 의도이고, ``access_as_of``가 그
  사실을 행마다 기록한다.
- 길 구조(``parcel_undervalued_scores.structure_score``): **pnu만**, 그 필지의
  가장 최근 base_year 한 건. ``structure_as_of``가 시점을 남긴다.
- 재개발: 공간 조인 + **고시일 < 1월 1일(base_year)** 필터. 공시지가 기준일이
  1월 1일이라 그해 중에 고시된 구역은 그해 가격에 반영될 수 없다.

기본 대상은 금천·강남 두 구다
------------------------------
``parcel_characteristics``는 2026년만 25개 구(88.9만 행)이고 2018~2025는 두
구뿐이다. 반면 Layer B의 접근성은 두 구(5.4만 행), 이웃 가격도 두 구만 계산돼
있다. 25개 구로 넓히면 2026년 행의 94%가 Layer B 전부 NULL이 되어 "증분 설명력"을
잴 수 없는 패널이 된다. 그래서 기본은 ``TARGET_SGG``이고, 넓히려면 ``--sgg``로
명시한다 — 그때 NULL률 로그가 무엇을 잃는지 알려 준다.

Run:
  # 한 해만, 저장 없이
  cd backend && .venv/Scripts/python -m tools.build_parcel_panel \
      --base-year 2026 --dry-run
  # 전체 (금천·강남 × 2018~2026)
  cd backend && .venv/Scripts/python -m tools.build_parcel_panel --base-year 2018-2026
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter

import psycopg2.extras

from app.services.land_use_zones import normalize_zone_name
from app.services.spatial_query import get_connection

logger = logging.getLogger(__name__)

# 패널 대상. 공시지가 9개 연도(2018~2026)가 완비되고 Layer B가 계산돼 있는 두 구다.
# compute_cv_folds.DEFAULT_SGG와 같은 목록이어야 한다 — 다르면 CV가 패널에 없는
# 필지를 배정한다.
TARGET_SGG: tuple[str, ...] = ("11545", "11680")   # 금천구, 강남구
DEFAULT_YEARS: tuple[int, int] = (2018, 2026)

# 성장률 지평. 컬럼이 1y/3y/5y로 고정이므로 여기만 고쳐도 되지 않는다.
GROWTH_LAGS: tuple[int, ...] = (1, 3, 5)
NEIGHBOR_GROWTH_LAG = 3


# ─────────────────────────────────────────────────────────────────────────
# 컬럼 군 — Layer A / Layer B 구분의 정본
# ─────────────────────────────────────────────────────────────────────────
IDENTITY_COLUMNS: tuple[str, ...] = ("pnu", "base_year", "sgg_code", "ldong_code")

# 종속변수. 개별공시지가(원/㎡)이고 시세·실거래가가 아니다 (CONTEXT.md의 세 구분).
TARGET_COLUMN = "official_price_per_sqm"

# Layer A — 토지가격비준표가 이미 쓰는 변수. 순환성의 원천이자 통제변수다.
# 연도별 원본(parcel_characteristics)을 그대로 조인한다. 현재값을 과거로 복사하면
# "2018년 필지가 2026년의 용도지역을 안다"가 되므로 절대 하지 않는다.
LAYER_A_COLUMNS: tuple[str, ...] = (
    "zone_name",          # 용도지역명1 (원문)
    "zone_group",         # normalize_zone_name() 대분류
    "land_category",      # 지목
    "use_situation",      # 이용상황
    "area_sqm",           # 토지면적
    "topograph_height",   # 지형높이
    "topograph_form",     # 지형형상
    "road_side_name",     # 도로접면 ← 순환성의 최대 원천
)

# Layer B — 우리 차별점. 아래 네 묶음이 Layer B의 전부다.
LAYER_B_ACCESS_COLUMNS: tuple[str, ...] = (
    "nearest_station_m",
    "nearest_entrance_m",
    "stations_within_500m",
    "stations_within_1000m",
    "nearest_bus_stop_m",
    "bus_stops_within_300m",
    "bus_stops_within_500m",
    "nearest_arterial_m",
    "nearest_road_m",
)
LAYER_B_STRUCTURE_COLUMNS: tuple[str, ...] = ("structure_score",)
LAYER_B_NEIGHBOR_COLUMNS: tuple[str, ...] = (
    "neighbor_median_300m",
    "neighbor_count_300m",
    "neighbor_median_500m",
    "neighbor_count_500m",
    "neighbor_knn20_median",
    "same_zone_median_300m",
    "same_zone_count_300m",
    # 아래 둘은 모델 입력이 아니라 진단 플래그다. 마이그레이션이 이웃 가격 묶음에
    # 넣어 뒀으므로 여기서도 같은 묶음에 둔다 — 학습 쪽에서 feature로 쓸지는
    # 학습 쪽이 정한다.
    "edge_truncated",
    "donor_sgg_count",
)
LAYER_B_REDEV_COLUMNS: tuple[str, ...] = (
    "in_redev_zone",
    "redev_zone_class",
    "redev_notice_year",
)
LAYER_B_COLUMNS: tuple[str, ...] = (
    LAYER_B_ACCESS_COLUMNS
    + LAYER_B_STRUCTURE_COLUMNS
    + LAYER_B_NEIGHBOR_COLUMNS
    + LAYER_B_REDEV_COLUMNS
)

# 성장 — **Layer A도 Layer B도 아니다.** 결정:
# 이 컬럼들은 target의 과거값이라(P(t)/P(t-k)-1) 누출은 아니지만 target의 사촌이다.
# Layer B에 넣으면 "우리 feature의 증분 설명력"이 자기회귀 성분으로 부풀고, Layer A에
# 섞으면 비준표 변수 목록이 오염된다. 그래서 별도 묶음으로 두고, **ablation에서는
# Layer A와 함께 통제군으로 들어간다**(마이그레이션 주석: "ablation에서 Layer A와
# 함께 통제군으로 다룬다"). 그 통제군이 ``ABLATION_CONTROL_COLUMNS``다.
GROWTH_COLUMNS: tuple[str, ...] = (
    "price_growth_1y",
    "price_growth_3y",
    "price_growth_5y",
    "neighbor_growth_3y",
)

# M0(통제만) 모형의 입력. M1은 여기에 LAYER_B_COLUMNS를 더한 것이고, 우리가 주장하는
# 것은 M1 − M0 증분뿐이다.
ABLATION_CONTROL_COLUMNS: tuple[str, ...] = LAYER_A_COLUMNS + GROWTH_COLUMNS

# 출처 시점. feature가 아니라 "이 값이 어느 시점 스냅샷인지"의 기록이다.
PROVENANCE_COLUMNS: tuple[str, ...] = ("access_as_of", "structure_as_of")

# 이 배치가 쓰는 컬럼 전부.
FEATURE_COLUMNS: tuple[str, ...] = LAYER_A_COLUMNS + LAYER_B_COLUMNS + GROWTH_COLUMNS
WRITTEN_COLUMNS: tuple[str, ...] = (
    IDENTITY_COLUMNS + (TARGET_COLUMN,) + FEATURE_COLUMNS + PROVENANCE_COLUMNS
)

# ⚠️ 이 배치가 **절대 건드리지 않는** 컬럼. G2 학습 배치가 채운다.
# UPSERT의 DO UPDATE SET에 하나라도 들어가면 feature 재적재가 예측을 지운다.
MODEL_OUTPUT_COLUMNS: tuple[str, ...] = (
    "expected_price",
    "price_gap",
    "growth_gap",
    "hot_prob",
    "model_version",
    "predicted_at",
)

BOOKKEEPING_COLUMNS: tuple[str, ...] = ("computed_at",)

# ⚠️ 원본 표에서 절대 가져오지 않는 컬럼 이름. 테스트가 이 이름들이 SQL과 컬럼
# 목록 어디에도 없는지 확인한다.
FORBIDDEN_SOURCE_COLUMNS: tuple[str, ...] = (
    "gap_score",                    # parcel_undervalued_scores — 가격 백분위 기반
    "price_percentile",             # parcel_undervalued_scores — target 그 자체
    "structure_percentile",         # parcel_undervalued_scores — 가격 층화가 섞여 있다
    "price_ratio_to_neighbor_300m", # parcel_neighbor_prices — 분자가 target이다
)


# ─────────────────────────────────────────────────────────────────────────
# 순수 계산 (DB 없이 돈다 — 테스트가 여기를 때린다)
# ─────────────────────────────────────────────────────────────────────────
def price_growth(current: float | None, past: float | None) -> float | None:
    """P(t)/P(t-k) - 1. 이력이 없으면 **None** — 0으로 채우지 않는다.

    0으로 채우면 "정보 없음"이 "성장 0%"으로 학습된다. 강남은 2018년 이전이
    미적재라 1y는 2019년부터, 3y는 2021년부터, 5y는 2023년부터만 값이 있다.
    """
    if current is None or past is None:
        return None
    if past <= 0 or current <= 0:
        return None
    return current / past - 1.0


def neighbor_growth(
    current_median: float | None,
    current_donor_sgg: int | None,
    past_median: float | None,
    past_donor_sgg: int | None,
) -> float | None:
    """이웃 중위의 3년 성장. **도너 범위가 같은 해끼리만** 계산한다.

    2026년은 이웃 도너가 2개 구 → 25개 구로 바뀌었다. 그냥 차분하면 나오는 값은
    "그 동네 값이 올랐다"가 아니라 "적재 범위가 넓어져 이웃 집합이 바뀌었다"다.
    부호까지 뒤집힐 수 있어 조용히 틀린 feature가 되므로, ``donor_sgg_count``가
    다르면 계산하지 않는다.
    """
    if current_donor_sgg is None or past_donor_sgg is None:
        return None
    if current_donor_sgg != past_donor_sgg:
        return None
    return price_growth(current_median, past_median)


def parse_years(values: list[str] | None) -> list[int]:
    """``--base-year`` 값들 → 연도 목록. 단일·범위(2018-2026)·콤마를 다 받는다."""
    if not values:
        return list(range(DEFAULT_YEARS[0], DEFAULT_YEARS[1] + 1))
    years: set[int] = set()
    for raw in values:
        for token in str(raw).split(","):
            token = token.strip()
            if not token:
                continue
            if "-" in token:
                lo, hi = (int(v) for v in token.split("-", 1))
                if hi < lo:
                    raise ValueError(f"연도 범위가 거꾸로다: {token}")
                years.update(range(lo, hi + 1))
            else:
                years.add(int(token))
    return sorted(years)


def build_rows(
    raw_rows,
    *,
    base_year: int,
    lag_prices: dict[tuple[str, int], float],
    lag_neighbors: dict[str, tuple[float | None, int | None]],
    redev: dict[str, tuple[str | None, int | None]],
    unknown_zones: Counter | None = None,
) -> list[dict]:
    """SELECT 결과 → 업서트용 dict 목록.

    파생값(zone_group·성장률·재개발 플래그)이 전부 여기서 만들어진다. DB를 타지
    않으므로 테스트가 합성 입력으로 규칙을 직접 때릴 수 있다.
    """
    unknown = unknown_zones if unknown_zones is not None else Counter()
    out: list[dict] = []
    for r in raw_rows:
        row = dict(r)
        zone_name = row.get("zone_name")
        group = normalize_zone_name(zone_name)
        if group is None and zone_name:
            unknown[zone_name] += 1
        row["zone_group"] = group

        price = row.get(TARGET_COLUMN)
        for lag in GROWTH_LAGS:
            row[f"price_growth_{lag}y"] = price_growth(
                price, lag_prices.get((row["pnu"], base_year - lag))
            )

        past_median, past_donor = lag_neighbors.get(row["pnu"], (None, None))
        row["neighbor_growth_3y"] = neighbor_growth(
            row.get("neighbor_median_300m"),
            row.get("donor_sgg_count"),
            past_median,
            past_donor,
        )

        zone_class, notice_year = redev.get(row["pnu"], (None, None))
        row["in_redev_zone"] = zone_class is not None or notice_year is not None
        row["redev_zone_class"] = zone_class
        row["redev_notice_year"] = notice_year

        out.append(row)
    return out


def null_rates(rows: list[dict]) -> dict[str, float]:
    """묶음별 NULL률(%). 결측을 채우지 않으므로 반드시 보고돼야 하는 값이다."""
    if not rows:
        return {}
    n = len(rows)
    groups = {
        "접근성": LAYER_B_ACCESS_COLUMNS[:1],          # nearest_station_m 하나로 대표
        "길구조": LAYER_B_STRUCTURE_COLUMNS,
        "이웃가격": ("neighbor_median_300m",),
        "성장1y": ("price_growth_1y",),
        "성장3y": ("price_growth_3y",),
        "성장5y": ("price_growth_5y",),
        "이웃성장3y": ("neighbor_growth_3y",),
        "용도지역군": ("zone_group",),
    }
    out: dict[str, float] = {}
    for label, cols in groups.items():
        missing = sum(1 for r in rows if all(r.get(c) is None for c in cols))
        out[label] = 100.0 * missing / n
    return out


# ─────────────────────────────────────────────────────────────────────────
# SQL — 컬럼 목록에서 만들어 낸다 (목록과 SQL이 어긋날 수 없게)
# ─────────────────────────────────────────────────────────────────────────
def build_upsert_sql() -> str:
    """INSERT ... ON CONFLICT DO UPDATE. **모델 출력 컬럼은 양쪽 다 없다.**

    UPDATE 대상은 feature 컬럼 + 식별 보조 컬럼 + ``computed_at``뿐이다. 여기에
    ``expected_price`` 같은 컬럼이 끼면 feature 재적재가 G2의 예측을 지운다.
    """
    cols = list(WRITTEN_COLUMNS) + ["computed_at"]
    updatable = [c for c in WRITTEN_COLUMNS if c not in ("pnu", "base_year")]
    sets = [f"  {c} = EXCLUDED.{c}" for c in updatable] + ["  computed_at = NOW()"]
    return (
        "INSERT INTO parcel_panel (\n  "
        + ", ".join(cols)
        + "\n) VALUES %s\nON CONFLICT (pnu, base_year) DO UPDATE SET\n"
        + ",\n".join(sets)
        + "\n"
    )


UPSERT_SQL = build_upsert_sql()
UPSERT_TEMPLATE = "(" + ", ".join(["%s"] * len(WRITTEN_COLUMNS)) + ", NOW())"

# 한 (구, 연도)의 feature를 한 번에 긁는다.
# ⚠️ 조인 키가 소스마다 다르다 — parcel_accessibility는 pnu만, parcel_neighbor_prices는
# (pnu, base_year), structure_score는 pnu + 가장 최근 base_year 한 건.
FEATURES_SQL = """
SELECT
    ch.pnu,
    ch.base_year,
    LEFT(ch.pnu, 5)                      AS sgg_code,
    ch.ldong_code,
    ch.price_per_sqm                     AS official_price_per_sqm,
    ch.zone_name_1                       AS zone_name,
    ch.land_category,
    ch.use_situation,
    ch.area_sqm,
    ch.topograph_height,
    ch.topograph_form,
    ch.road_side_name,
    ac.nearest_station_m,
    ac.nearest_entrance_m,
    ac.stations_within_500m,
    ac.stations_within_1000m,
    ac.nearest_bus_stop_m,
    ac.bus_stops_within_300m,
    ac.bus_stops_within_500m,
    ac.nearest_arterial_m,
    ac.nearest_road_m,
    ac.as_of                             AS access_as_of,
    st.structure_score,
    st.structure_as_of,
    np.neighbor_median_300m,
    np.neighbor_count_300m,
    np.neighbor_median_500m,
    np.neighbor_count_500m,
    np.neighbor_knn20_median,
    np.same_zone_median_300m,
    np.same_zone_count_300m,
    np.edge_truncated,
    np.donor_sgg_count
FROM parcel_characteristics ch
LEFT JOIN parcel_accessibility ac
       ON ac.pnu = ch.pnu
LEFT JOIN parcel_neighbor_prices np
       ON np.pnu = ch.pnu AND np.base_year = ch.base_year
LEFT JOIN LATERAL (
    SELECT us.structure_score, us.base_year AS structure_as_of
    FROM parcel_undervalued_scores us
    WHERE us.pnu = ch.pnu
    ORDER BY us.base_year DESC
    LIMIT 1
) st ON TRUE
WHERE ch.base_year = %(year)s
  AND LEFT(ch.pnu, 5) = %(sgg)s
  AND ch.price_per_sqm > 0
ORDER BY ch.pnu
"""

# 성장률용 과거 가격. 같은 필지의 t-1 / t-3 / t-5만 본다.
LAG_PRICES_SQL = """
SELECT pnu, base_year, price_per_sqm
FROM parcel_characteristics
WHERE base_year = ANY(%(years)s)
  AND LEFT(pnu, 5) = %(sgg)s
  AND price_per_sqm > 0
"""

# 이웃 성장률용 3년 전 이웃 중위 + **그때의 도너 범위**. 도너 수를 함께 가져오는
# 것이 요점이다 — 범위가 다르면 성장률을 계산하지 않기 때문이다.
LAG_NEIGHBORS_SQL = """
SELECT pnu, neighbor_median_300m, donor_sgg_count
FROM parcel_neighbor_prices
WHERE base_year = %(year)s
  AND LEFT(pnu, 5) = %(sgg)s
"""

# 재개발·재건축 — **고시일이 있는 구역만**, 그리고 고시일 < 1월 1일(base_year)만.
# 공시지가 기준일이 1월 1일이라 그해 중 고시된 구역은 그해 가격에 못 들어간다.
# 겹치면 가장 최근 고시 구역을 쓴다. 구역(2,157건)을 driving side로 두고 필지
# 대표점의 GIST 인덱스를 타게 한다 — 반대로 하면 110만 필지를 훑는다.
REDEV_SQL = """
SELECT DISTINCT ON (pc.pnu)
       pc.pnu,
       z.zone_class                          AS redev_zone_class,
       EXTRACT(YEAR FROM z.notice_date)::int  AS redev_notice_year
FROM redevelopment_zones_real z
JOIN parcel_centroids pc ON ST_Intersects(z.geom_5179, pc.geom_5179)
WHERE z.notice_date IS NOT NULL
  AND z.notice_date < make_date(%(year)s, 1, 1)
  AND LEFT(pc.pnu, 5) = ANY(%(sgg)s)
ORDER BY pc.pnu, z.notice_date DESC
"""


# ─────────────────────────────────────────────────────────────────────────
# DB 입출력
# ─────────────────────────────────────────────────────────────────────────
def fetch_lag_prices(conn, sgg: str, base_year: int) -> dict[tuple[str, int], float]:
    years = [base_year - lag for lag in GROWTH_LAGS]
    with conn.cursor() as cur:
        cur.execute(LAG_PRICES_SQL, {"years": years, "sgg": sgg})
        return {(r[0], r[1]): r[2] for r in cur.fetchall()}


def fetch_lag_neighbors(
    conn, sgg: str, base_year: int
) -> dict[str, tuple[float | None, int | None]]:
    with conn.cursor() as cur:
        cur.execute(
            LAG_NEIGHBORS_SQL, {"year": base_year - NEIGHBOR_GROWTH_LAG, "sgg": sgg}
        )
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def fetch_redev(
    conn, sgg: list[str], base_year: int
) -> dict[str, tuple[str | None, int | None]]:
    with conn.cursor() as cur:
        cur.execute(REDEV_SQL, {"year": base_year, "sgg": list(sgg)})
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def fetch_features(conn, sgg: str, base_year: int) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(FEATURES_SQL, {"year": base_year, "sgg": sgg})
        return cur.fetchall()


def to_tuple(row: dict) -> tuple:
    """dict → 업서트 튜플. 순서는 ``WRITTEN_COLUMNS``가 정한다."""
    return tuple(row.get(c) for c in WRITTEN_COLUMNS)


def store(conn, rows: list[dict], *, page_size: int = 5000) -> int:
    if not rows:
        return 0
    values = [to_tuple(r) for r in rows]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur, UPSERT_SQL, values, template=UPSERT_TEMPLATE, page_size=page_size
        )
    conn.commit()
    return len(values)


def process(
    conn,
    sgg: str,
    base_year: int,
    *,
    redev: dict[str, tuple[str | None, int | None]],
    unknown_zones: Counter,
    dry_run: bool,
    page_size: int,
) -> tuple[int, dict[str, float]]:
    """(구, 연도) 하나 → (저장한 행 수, 묶음별 NULL률)."""
    raw = fetch_features(conn, sgg, base_year)
    if not raw:
        return 0, {}
    rows = build_rows(
        raw,
        base_year=base_year,
        lag_prices=fetch_lag_prices(conn, sgg, base_year),
        lag_neighbors=fetch_lag_neighbors(conn, sgg, base_year),
        redev=redev,
        unknown_zones=unknown_zones,
    )
    rates = null_rates(rows)
    if dry_run:
        return len(rows), rates
    return store(conn, rows, page_size=page_size), rates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-year", action="append", default=None,
        help="기준연도. 여러 번 줄 수 있고 범위도 된다 (예: --base-year 2018-2026). "
             f"생략하면 {DEFAULT_YEARS[0]}~{DEFAULT_YEARS[1]}",
    )
    parser.add_argument(
        "--sgg", action="append", default=None,
        help=f"시군구코드 5자리. 여러 번 줄 수 있다. 생략하면 {', '.join(TARGET_SGG)}",
    )
    parser.add_argument("--page-size", type=int, default=5000, help="업서트 배치 크기")
    parser.add_argument("--dry-run", action="store_true", help="계산만 하고 저장하지 않음")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    years = parse_years(args.base_year)
    sggs = args.sgg or list(TARGET_SGG)
    logger.info("연도 %s × 시군구 %s%s", years, sggs,
                " [dry-run]" if args.dry_run else "")
    if args.sgg and set(args.sgg) - set(TARGET_SGG):
        logger.warning(
            "기본 대상(%s) 밖의 구가 있다 — Layer B(접근성·이웃 가격)가 계산돼 있지 "
            "않으면 그 행은 Layer B 전부 NULL이 된다. 아래 NULL률을 확인할 것",
            ", ".join(TARGET_SGG),
        )

    unknown_zones: Counter = Counter()
    total = 0
    with get_connection() as conn:
        for year in years:
            t0 = time.time()
            redev = fetch_redev(conn, sggs, year)
            logger.info(
                "%d년 — 고시일 < %d-01-01 재개발구역에 든 필지 %d개 (%.1fs)",
                year, year, len(redev), time.time() - t0,
            )
            for sgg in sggs:
                t1 = time.time()
                n, rates = process(
                    conn, sgg, year,
                    redev=redev, unknown_zones=unknown_zones,
                    dry_run=args.dry_run, page_size=args.page_size,
                )
                total += n
                logger.info(
                    "  %s/%d — %d행 (%.1fs)%s", sgg, year, n, time.time() - t1,
                    " [dry-run]" if args.dry_run else "",
                )
                if rates:
                    logger.info(
                        "    NULL률 · %s",
                        " · ".join(f"{k} {v:.1f}%" for k, v in rates.items()),
                    )

    logger.info("완료 — %d행%s", total,
                " (dry-run, 저장 안 함)" if args.dry_run else "")
    if unknown_zones:
        logger.warning("미인식 용도지역 명칭 %d종:", len(unknown_zones))
        for name, n in unknown_zones.most_common(10):
            logger.warning("  %r: %d개", name, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
