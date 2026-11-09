"""주변 유사필지 가격 feature 배치 (PPT Feature Pool 12).

필지 × 연도마다 "그 해 이웃 필지들의 개별공시지가 분포"를 계산해
``parcel_neighbor_prices``에 넣는다. 스키마는
``scripts/migrations/021_parcel_neighbor_prices.sql``.

**왜 SQL이 아니라 파이썬인가.** ``ST_DWithin`` 자기조인으로 다 하면 계산은
DB에 남지만 검증할 수 있는 지점이 없다 — 이 feature의 사고 두 개(자기 가격
누출, 연도 교차 누출)는 조용히 R²만 올리고 눈에 안 보이므로 테스트로 못
잡으면 못 잡는다. 그래서 공간 후보 추출만 DB(GIST + bbox)에 맡기고, 이웃
선택·통계는 ``compute_neighbor_features()`` 순수 함수로 뺐다
(``tests/test_compute_parcel_neighbor_prices.py``가 그 함수를 때린다).
scipy cKDTree를 쓰므로 SQL 자기조인보다 오히려 빠르다 — 구·연도 하나(대상
3.3만 필지, 도너 9.2만)가 수 초다.

⚠️ 이 배치가 반드시 지키는 것 (자세한 근거는 마이그레이션 주석):

1. **대상 필지 자신을 이웃에서 뺀다.** 거리 0이 아니라 **PNU 동일성**으로
   뺀다 — 대표점이 같은 좌표로 찍힌 다른 필지를 함께 날리지 않기 위해서다.
2. **같은 base_year 안에서만 이웃을 찾는다.** 순수 함수가 연도 섞인 입력을
   받으면 ValueError로 죽는다.
3. **이웃이 없으면 count=0, 중위·평균·백분위는 NULL.** 0으로 채우지 않는다.
4. **경계 절단을 플래그로 남긴다.** 2018~2025는 금천·강남 두 구만 적재돼
   있어 구 경계에서 이웃이 잘리고, 2026만 전 서울이라 구 경계를 넘어 계산된다.

**도로·하천 필지는 이웃(도너)에서 뺀다** — 결정과 근거:
도로·하천·구거·제방·수도용지 지목의 공시지가는 대지와 성격이 다르고(도로는
인접 대지 가격의 1/3 수준으로 평가되는 관행), 금천·강남 2026년 실측에서
지목 '도로'가 7,004필지(13.2%)·'하천' 541필지다. 이만한 덩어리를 섞으면
이웃 중위가 "그 동네 땅값"이 아니라 "도로 필지 비율"을 따라 움직인다.
그래서 도너에서는 뺀다. 반대로 **대상(target)에서는 빼지 않는다** — 이 표는
feature 저장소이고, 어떤 필지를 학습에 쓸지는 학습 쪽에서 정하는 게 맞다
(``compute_parcel_undervalued``는 판정 결과 표라서 거기서 뺐다).

Run:
  # 한 구·한 해만, 저장 없이
  cd backend && .venv/Scripts/python -m tools.compute_parcel_neighbor_prices \
      --sgg 11680 --year 2026 --dry-run
  # 전체 (금천·강남 × 2018~2026)
  cd backend && .venv/Scripts/python -m tools.compute_parcel_neighbor_prices
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
import psycopg2.extras

from app.services.land_use_zones import normalize_zone_name
from app.services.spatial_query import get_connection

logger = logging.getLogger(__name__)

# 계산 대상. 공시지가가 9개 연도 완비된 두 구다(2026-08 실측).
TARGET_SGG = ("11545", "11680")   # 금천구, 강남구
# 2015~2017은 금천 일부(약 8.8천 필지)만 있어 이웃 통계가 심하게 잘린다 —
# "이웃이 적다"가 데이터 적재 상태를 반영하는 구간이라 아예 뺀다.
MIN_YEAR = 2018

# 반경 통계를 낼 거리. 넓히려면 여기만 고치면 되지만 컬럼은 300/500 고정이므로
# 업서트 쪽도 함께 봐야 한다.
RADII_M: tuple[int, ...] = (300, 500)
KNN_K = 20

# 도너를 얼마나 넓게 긁어올지. 최대 반경보다 넉넉해야 하는 이유는 knn20이다 —
# 밀도가 낮은 곳에서는 20번째 이웃이 500m 밖에 있을 수 있고, 그때 bbox가
# 딱 500m면 "이웃이 20개보다 적다"가 실제 밀도가 아니라 fetch 범위 탓이 된다.
FETCH_BUFFER_M = 1000

# 경계 절단 판정 거리. 300m 반경 통계가 잘리는지를 기준으로 잡았다.
EDGE_TRUNCATED_M = 300

# 이웃(도너)에서 뺄 지목. 위 docstring의 근거 참고.
EXCLUDED_DONOR_LAND_CATEGORIES: frozenset[str] = frozenset(
    {"도로", "하천", "구거", "제방", "수도용지"}
)


# ─────────────────────────────────────────────────────────────────────────
# 순수 계산 (DB 없이 돈다 — 테스트가 여기를 때린다)
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ParcelPoint:
    """이웃 계산의 입력 한 건. 대상이면서 도너일 수도 있다."""

    pnu: str
    base_year: int
    x: float
    y: float
    price_per_sqm: float
    zone_group: str | None      # 정규화된 대분류 (없으면 None)
    is_donor: bool              # 이웃 통계에 넣어도 되는 필지인가
    is_target: bool             # 이 필지의 feature를 계산할 것인가
    edge_dist_m: float | None = None


@dataclass(frozen=True)
class NeighborStats:
    """이웃 가격 분포. 이웃이 없으면 count=0이고 나머지는 전부 None이다."""

    count: int
    median: float | None
    mean: float | None
    p25: float | None
    p75: float | None


EMPTY_STATS = NeighborStats(count=0, median=None, mean=None, p25=None, p75=None)


@dataclass(frozen=True)
class NeighborFeatures:
    pnu: str
    base_year: int
    price_per_sqm: float
    zone_group: str | None
    by_radius: dict[int, NeighborStats]
    knn_median: float | None
    knn_count: int
    same_zone_median: float | None
    same_zone_count: int
    price_ratio: float | None
    edge_dist_m: float | None
    edge_truncated: bool


def _quantile_of_sorted(sorted_arr: np.ndarray, q: float) -> float:
    """정렬된 배열의 q분위. numpy 기본(``method="linear"``)과 같은 값을 준다.

    ``np.percentile``을 그대로 부르지 않는 이유는 속도다 — 호출 한 번에
    240µs가 드는데(실측, 원소 1,700개) 필지마다 반경 2개·동일용도지역·knn으로
    네 번 부르면 1ms가 되고, 필지 47.7만 개면 그것만으로 8분이다. 정렬은 어차피
    한 번 하면 되니 분위수는 인덱싱으로 뽑는다(같은 배열에서 3개를 뽑으므로
    3분의 1이 아니라 12분의 1로 준다).
    """
    n = sorted_arr.size
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    low = float(sorted_arr[lo])
    return low + (float(sorted_arr[hi]) - low) * frac


def summarize_prices(prices: Sequence[float] | np.ndarray) -> NeighborStats:
    """이웃 가격 배열 → 통계. **빈 배열이면 중위·평균이 NULL이다.**

    0으로 채우지 않는 것이 이 함수의 존재 이유다 — 0을 넣으면 "이웃 없음"이
    "이웃이 공짜"로 학습되고, 그 필지들은 하필 경계·산·공원 근처라 계통적으로
    한쪽에 몰려 있다. 백분위는 numpy 기본과 같은 선형 보간이다.
    """
    arr = np.asarray(prices, dtype=float)
    if arr.size == 0:
        return EMPTY_STATS
    s = np.sort(arr)
    return NeighborStats(
        count=int(arr.size),
        median=_quantile_of_sorted(s, 0.5),
        mean=float(arr.mean()),
        p25=_quantile_of_sorted(s, 0.25),
        p75=_quantile_of_sorted(s, 0.75),
    )


def safe_ratio(own: float | None, neighbor_median: float | None) -> float | None:
    """자기 가격 / 이웃 중위. 못 구하면 None.

    이웃이 없으면(None) 당연히 None이고, 이웃 중위가 0이어도 None이다 — 공시지가
    0은 정상값이 아니라 결측·특수필지의 흔적이라, 나눠서 inf를 만드는 쪽이
    더 나쁘다.
    """
    if own is None or neighbor_median is None:
        return None
    if neighbor_median <= 0:
        return None
    return float(own) / float(neighbor_median)


def _require_single_year(points: Sequence[ParcelPoint]) -> int | None:
    """연도가 섞였으면 죽는다.

    ⚠️ 이 배치에서 가장 조용한 사고가 "2026년 이웃 가격을 2018년 관측치에
    붙이는 것"이다. 값이 그럴듯해서 눈으로는 안 보이고 모델 성능만 올라간다.
    그래서 입력 단계에서 막는다 — 호출자가 연도별로 나눠 부르게 강제한다.
    """
    years = {p.base_year for p in points}
    if not years:
        return None
    if len(years) > 1:
        raise ValueError(
            f"이웃은 같은 연도 안에서만 계산한다 — 입력에 연도가 섞였다: "
            f"{sorted(years)}"
        )
    return years.pop()


def compute_neighbor_features(
    points: Sequence[ParcelPoint],
    *,
    radii: Iterable[int] = RADII_M,
    knn_k: int = KNN_K,
) -> list[NeighborFeatures]:
    """대상 필지들의 이웃 가격 feature. 좌표는 EPSG:5179 미터.

    자기 제외는 **PNU 동일성**으로 한다(거리 0 기준이 아니다). 도너 목록에서
    자기 자신의 인덱스를 찾아 후보에서 빼므로, 대표점이 겹친 옆 필지는 살아
    남고 자기 자신만 확실히 빠진다.
    """
    radii = tuple(sorted(radii))
    if not radii:
        raise ValueError("radii가 비어 있다")
    base_year = _require_single_year(points)
    targets = [p for p in points if p.is_target]
    if base_year is None or not targets:
        return []

    donors = [p for p in points if p.is_donor]
    if not donors:
        # 도너가 하나도 없으면 전부 결측이다. 0으로 채우지 않는다.
        return [_empty_features(t, radii) for t in targets]

    donor_xy = np.array([[p.x, p.y] for p in donors], dtype=float)
    donor_price = np.array([p.price_per_sqm for p in donors], dtype=float)
    # 문자열 비교를 피하려고 용도지역을 정수 코드로 바꿔 둔다. None은 -1이고
    # -1끼리는 같다고 보지 않는다("용도지역 모름"은 같은 계층이 아니다).
    zone_ids = {z: i for i, z in enumerate(sorted({p.zone_group for p in donors if p.zone_group}))}
    donor_zone = np.array(
        [zone_ids.get(p.zone_group, -1) if p.zone_group else -1 for p in donors],
        dtype=np.int32,
    )
    donor_index_by_pnu = {p.pnu: i for i, p in enumerate(donors)}

    from scipy.spatial import cKDTree  # 지연 import — DB 없는 테스트 환경 부담 최소화

    tree = cKDTree(donor_xy)
    max_r = float(radii[-1])
    target_xy = np.array([[t.x, t.y] for t in targets], dtype=float)

    # knn은 자기 자신이 후보에 들 수 있으니 k를 하나 더 뽑는다. 도너가 적으면
    # 그만큼만 뽑는다 (k > n이면 scipy가 무한거리·n 인덱스를 채워 준다).
    k = min(knn_k + 1, len(donors))
    # k=1이면 scipy가 1차원으로 돌려주므로 항상 (대상수, k)로 펴 둔다.
    knn_idx = np.asarray(tree.query(target_xy, k=k, workers=-1)[1]).reshape(
        len(targets), -1
    )

    out: list[NeighborFeatures] = []
    # 배치로 자른다 — 33,000개를 한 번에 query_ball_point하면 후보 인덱스가
    # 파이썬 리스트로 수천만 개 쌓여 메모리가 터진다.
    batch = 512
    for start in range(0, len(targets), batch):
        chunk = targets[start : start + batch]
        cand_lists = tree.query_ball_point(
            target_xy[start : start + len(chunk)], max_r, workers=-1, return_sorted=False
        )
        for offset, t in enumerate(chunk):
            self_i = donor_index_by_pnu.get(t.pnu)
            cand = np.asarray(cand_lists[offset], dtype=np.int64)
            if self_i is not None:
                cand = cand[cand != self_i]      # ← 자기 제외 (PNU 기준)

            if cand.size:
                d = np.hypot(
                    donor_xy[cand, 0] - t.x, donor_xy[cand, 1] - t.y
                )
                prices = donor_price[cand]
                zones = donor_zone[cand]
            else:
                d = prices = np.empty(0, dtype=float)
                zones = np.empty(0, dtype=np.int32)

            by_radius: dict[int, NeighborStats] = {}
            for r in radii:
                by_radius[r] = summarize_prices(prices[d <= r])

            # 같은 용도지역 이웃은 가장 좁은 반경에서 낸다(통제된 비교).
            inner = radii[0]
            if t.zone_group is None or t.zone_group not in zone_ids:
                same_zone = EMPTY_STATS
            else:
                mask = (d <= inner) & (zones == zone_ids[t.zone_group])
                same_zone = summarize_prices(prices[mask])

            row = knn_idx[start + offset]
            if self_i is not None:
                row = row[row != self_i]
            row = row[row < len(donors)]          # k > 도너 수일 때 채워진 자리
            row = row[:knn_k]
            knn_stats = summarize_prices(donor_price[row])

            inner_stats = by_radius[inner]
            out.append(
                NeighborFeatures(
                    pnu=t.pnu,
                    base_year=t.base_year,
                    price_per_sqm=t.price_per_sqm,
                    zone_group=t.zone_group,
                    by_radius=by_radius,
                    knn_median=knn_stats.median,
                    knn_count=knn_stats.count,
                    same_zone_median=same_zone.median,
                    same_zone_count=same_zone.count,
                    price_ratio=safe_ratio(t.price_per_sqm, inner_stats.median),
                    edge_dist_m=t.edge_dist_m,
                    edge_truncated=(
                        t.edge_dist_m is None or t.edge_dist_m < EDGE_TRUNCATED_M
                    ),
                )
            )
    return out


def _empty_features(t: ParcelPoint, radii: tuple[int, ...]) -> NeighborFeatures:
    return NeighborFeatures(
        pnu=t.pnu,
        base_year=t.base_year,
        price_per_sqm=t.price_per_sqm,
        zone_group=t.zone_group,
        by_radius={r: EMPTY_STATS for r in radii},
        knn_median=None,
        knn_count=0,
        same_zone_median=None,
        same_zone_count=0,
        price_ratio=None,
        edge_dist_m=t.edge_dist_m,
        edge_truncated=(t.edge_dist_m is None or t.edge_dist_m < EDGE_TRUNCATED_M),
    )


def build_points(
    rows: Sequence[dict], *, unknown_zones: Counter | None = None
) -> tuple[list[ParcelPoint], Counter]:
    """SQL 결과 → ParcelPoint. (입력, 집계) 를 돌려준다.

    도로·하천 지목은 도너에서 뺀다(모듈 docstring의 근거). 지목이 NULL인 필지는
    빼지 않는다 — 토지특성이 안 붙은 것을 "도로일지도 모른다"고 버리면
    이웃 표본이 이유 없이 줄어든다.
    """
    points: list[ParcelPoint] = []
    stats: Counter = Counter()
    for r in rows:
        zone_group = normalize_zone_name(r.get("zone_name"))
        if r.get("zone_name") and zone_group is None and unknown_zones is not None:
            unknown_zones[r["zone_name"]] += 1

        category = r.get("land_category")
        is_donor = category not in EXCLUDED_DONOR_LAND_CATEGORIES
        if not is_donor:
            stats["도너 제외(도로·하천 등 지목)"] += 1
        if zone_group is None:
            stats["용도지역 미확인"] += 1

        is_target = bool(r["is_target"])
        if is_target:
            stats["대상 필지"] += 1

        points.append(
            ParcelPoint(
                pnu=r["pnu"],
                base_year=int(r["base_year"]),
                x=float(r["x"]),
                y=float(r["y"]),
                price_per_sqm=float(r["price_per_sqm"]),
                zone_group=zone_group,
                is_donor=is_donor,
                is_target=is_target,
                edge_dist_m=(
                    None if r.get("edge_dist_m") is None else float(r["edge_dist_m"])
                ),
            )
        )
    return points, stats


# ─────────────────────────────────────────────────────────────────────────
# DB 입출력
# ─────────────────────────────────────────────────────────────────────────
_YEARS_SQL = """
SELECT DISTINCT base_year
FROM land_prices
WHERE LEFT(ldong_code, 5) = ANY(%s) AND base_year >= %s
ORDER BY base_year
"""

# 그 해 land_prices가 덮는 시군구 수. 경계 절단 규모를 판단하는 근거로 저장한다.
_DONOR_SGG_SQL = """
SELECT count(DISTINCT LEFT(ldong_code, 5)) FROM land_prices WHERE base_year = %s
"""

# 그 해 적재 범위의 경계선. land_prices의 법정동 코드와 sgis_dong_boundary의
# 행정동 코드는 체계가 달라(11545 vs 11170) 코드로 맞출 수 없다 — 그 해 필지가
# 하나라도 들어 있는 동을 공간으로 골라 합집합한 뒤 테두리만 남긴다.
_COVERAGE_RING_SQL = """
SELECT ST_AsBinary(ST_Boundary(ST_Union(b.geom))) AS ring
FROM sgis_dong_boundary b
WHERE b.adm_cd IN (
    SELECT DISTINCT b2.adm_cd
    FROM sgis_dong_boundary b2
    JOIN parcel_centroids pc ON ST_Contains(b2.geom, pc.geom_5179)
    JOIN land_prices lp ON lp.pnu = pc.pnu AND lp.base_year = %s
)
"""

# 대상 구의 필지 + 그 bbox를 FETCH_BUFFER_M 넓힌 범위의 도너를 한 번에 긁는다.
# bbox를 넓히는 덕에 **인접 구 필지가 그 해 적재돼 있으면 자동으로 이웃에
# 들어온다** (2026년) — 코드에 구 목록을 박지 않고 적재 범위가 결정하게 둔다.
_POINTS_SQL = """
WITH target AS (
    SELECT pc.geom_5179 AS geom
    FROM land_prices lp
    JOIN parcel_centroids pc ON pc.pnu = lp.pnu
    WHERE lp.base_year = %(year)s
      AND LEFT(lp.ldong_code, 5) = %(sgg)s
      AND lp.price_per_sqm > 0
), box AS (
    SELECT ST_Expand(ST_Extent(geom)::geometry, %(fetch_m)s) AS g FROM target
)
SELECT
    lp.pnu,
    lp.base_year,
    ST_X(pc.geom_5179) AS x,
    ST_Y(pc.geom_5179) AS y,
    lp.price_per_sqm,
    ch.zone_name_1 AS zone_name,
    ch.land_category,
    (LEFT(lp.ldong_code, 5) = %(sgg)s) AS is_target,
    CASE WHEN LEFT(lp.ldong_code, 5) = %(sgg)s AND %(ring)s IS NOT NULL
         THEN ST_Distance(pc.geom_5179, ST_GeomFromWKB(%(ring)s, 5179))
    END AS edge_dist_m
FROM parcel_centroids pc
JOIN land_prices lp ON lp.pnu = pc.pnu AND lp.base_year = %(year)s
LEFT JOIN parcel_characteristics ch
       ON ch.pnu = lp.pnu AND ch.base_year = lp.base_year
CROSS JOIN box
WHERE pc.geom_5179 && box.g
  AND lp.price_per_sqm > 0
"""

_UPSERT_SQL = """
INSERT INTO parcel_neighbor_prices (
  pnu, base_year, price_per_sqm, zone_group,
  neighbor_count_300m, neighbor_median_300m, neighbor_mean_300m,
  neighbor_p25_300m, neighbor_p75_300m,
  neighbor_count_500m, neighbor_median_500m, neighbor_mean_500m,
  neighbor_p25_500m, neighbor_p75_500m,
  neighbor_knn20_median, neighbor_knn20_count,
  same_zone_median_300m, same_zone_count_300m,
  price_ratio_to_neighbor_300m,
  edge_dist_m, edge_truncated, donor_sgg_count, computed_at
) VALUES %s
ON CONFLICT (pnu, base_year) DO UPDATE SET
  price_per_sqm = EXCLUDED.price_per_sqm,
  zone_group = EXCLUDED.zone_group,
  neighbor_count_300m = EXCLUDED.neighbor_count_300m,
  neighbor_median_300m = EXCLUDED.neighbor_median_300m,
  neighbor_mean_300m = EXCLUDED.neighbor_mean_300m,
  neighbor_p25_300m = EXCLUDED.neighbor_p25_300m,
  neighbor_p75_300m = EXCLUDED.neighbor_p75_300m,
  neighbor_count_500m = EXCLUDED.neighbor_count_500m,
  neighbor_median_500m = EXCLUDED.neighbor_median_500m,
  neighbor_mean_500m = EXCLUDED.neighbor_mean_500m,
  neighbor_p25_500m = EXCLUDED.neighbor_p25_500m,
  neighbor_p75_500m = EXCLUDED.neighbor_p75_500m,
  neighbor_knn20_median = EXCLUDED.neighbor_knn20_median,
  neighbor_knn20_count = EXCLUDED.neighbor_knn20_count,
  same_zone_median_300m = EXCLUDED.same_zone_median_300m,
  same_zone_count_300m = EXCLUDED.same_zone_count_300m,
  price_ratio_to_neighbor_300m = EXCLUDED.price_ratio_to_neighbor_300m,
  edge_dist_m = EXCLUDED.edge_dist_m,
  edge_truncated = EXCLUDED.edge_truncated,
  donor_sgg_count = EXCLUDED.donor_sgg_count,
  computed_at = NOW()
"""


def to_upsert_row(f: NeighborFeatures, donor_sgg_count: int) -> tuple:
    """feature → 업서트 튜플. 컬럼 순서가 ``_UPSERT_SQL``과 맞아야 한다."""
    r300 = f.by_radius[300]
    r500 = f.by_radius[500]
    return (
        f.pnu, f.base_year, f.price_per_sqm, f.zone_group,
        r300.count, r300.median, r300.mean, r300.p25, r300.p75,
        r500.count, r500.median, r500.mean, r500.p25, r500.p75,
        f.knn_median, f.knn_count,
        f.same_zone_median, f.same_zone_count,
        f.price_ratio,
        f.edge_dist_m, f.edge_truncated, donor_sgg_count,
    )


def process(
    conn,
    sgg: str,
    year: int,
    *,
    ring: memoryview | bytes | None,
    donor_sgg_count: int,
    unknown_zones: Counter,
    dry_run: bool,
) -> tuple[int, int, Counter]:
    """(구, 연도) 하나 → (도너 후보 수, 저장한 대상 수, 집계)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            _POINTS_SQL,
            {"year": year, "sgg": sgg, "fetch_m": FETCH_BUFFER_M, "ring": ring},
        )
        rows = cur.fetchall()
    if not rows:
        return 0, 0, Counter()

    points, stats = build_points(rows, unknown_zones=unknown_zones)
    features = compute_neighbor_features(points)
    _log_missing(features, f"{sgg}/{year}")
    if not features or dry_run:
        return len(rows), len(features), stats

    values = [to_upsert_row(f, donor_sgg_count) for f in features]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur, _UPSERT_SQL, values,
            template=(
                "(" + ", ".join(["%s"] * 22) + ", NOW())"
            ),
            page_size=2000,
        )
    conn.commit()
    return len(rows), len(features), stats


def _log_missing(features: Sequence[NeighborFeatures], label: str) -> None:
    """결측률을 남긴다 — 0으로 채우지 않았으므로 결측은 반드시 보고돼야 한다."""
    if not features:
        return
    n = len(features)
    miss300 = sum(1 for f in features if f.by_radius[300].median is None)
    miss_zone = sum(1 for f in features if f.same_zone_median is None)
    edge = sum(1 for f in features if f.edge_truncated)
    logger.info(
        "  %s 결측 · 300m중위 %d/%d (%.2f%%), 동일용도지역중위 %.2f%%, 경계절단 %.2f%%",
        label, miss300, n, 100 * miss300 / n, 100 * miss_zone / n, 100 * edge / n,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, action="append", default=None,
                        help="기준연도. 여러 번 줄 수 있다. 생략하면 land_prices의 "
                             f"{MIN_YEAR}년 이후 전부")
    parser.add_argument("--sgg", action="append", default=None,
                        help=f"시군구코드 5자리. 생략하면 {', '.join(TARGET_SGG)}")
    parser.add_argument("--dry-run", action="store_true",
                        help="계산만 하고 저장하지 않음")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sggs = args.sgg or list(TARGET_SGG)

    with get_connection() as conn:
        if args.year:
            years = sorted(set(args.year))
        else:
            with conn.cursor() as cur:
                cur.execute(_YEARS_SQL, (sggs, MIN_YEAR))
                years = [r[0] for r in cur.fetchall()]
        if not years:
            raise SystemExit("계산할 연도가 없다 — land_prices를 먼저 적재할 것")
        logger.info("연도 %s × 시군구 %s", years, sggs)

        unknown_zones: Counter = Counter()
        total_stats: Counter = Counter()
        total_saved = 0
        for year in years:
            t0 = time.time()
            with conn.cursor() as cur:
                cur.execute(_DONOR_SGG_SQL, (year,))
                donor_sgg_count = int(cur.fetchone()[0])
                cur.execute(_COVERAGE_RING_SQL, (year,))
                row = cur.fetchone()
                ring = row[0] if row else None
            if ring is None:
                # 경계를 못 구하면 절단 여부를 알 수 없다 → 전부 truncated로 둔다
                # (모르는 것을 "안전하다"고 표시하지 않는다).
                logger.warning("%d년 적재 범위 경계를 못 구했다 — 전부 경계절단 표시", year)
            logger.info(
                "%d년 — 적재 시군구 %d개, 경계선 %s (%.1fs)",
                year, donor_sgg_count,
                "있음" if ring is not None else "없음", time.time() - t0,
            )

            for sgg in sggs:
                t1 = time.time()
                n_rows, n_saved, stats = process(
                    conn, sgg, year,
                    ring=ring, donor_sgg_count=donor_sgg_count,
                    unknown_zones=unknown_zones, dry_run=args.dry_run,
                )
                total_saved += n_saved
                total_stats.update(stats)
                logger.info(
                    "  %s/%d — 후보 %d개 중 대상 %d개 계산 (%.1fs)%s",
                    sgg, year, n_rows, n_saved, time.time() - t1,
                    " [dry-run]" if args.dry_run else "",
                )

        logger.info(
            "완료 — %d행%s", total_saved, " (dry-run, 저장 안 함)" if args.dry_run else ""
        )
        for k, v in total_stats.most_common():
            logger.info("  %s: %d", k, v)
        if unknown_zones:
            logger.warning("미인식 용도지역 명칭 %d종:", len(unknown_zones))
            for name, n in unknown_zones.most_common(10):
                logger.warning("  %r: %d개", name, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
