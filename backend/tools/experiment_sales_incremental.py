"""목(Space Syntax)이 상권 매출을 **통제변수를 넣은 뒤에도** 설명하는가 — 검증 실험 1.

**왜 이 실험이 필요한가.** "목은 좋은데 매출이 낮다 → 저평가"는 위험한 추론이다.
매출이 낮은 이유는 목 말고도 많다 — 지하철 출입구가 길 반대편, 주차 불가, 앵커시설
부재, 이미 비싼 임대료, 프랜차이즈 비중. 이런 걸 안 빼고 잔차를 "저평가"라 부르면
사실은 **"우리가 아직 모르는 것"** 을 저평가라고 파는 것이 된다.

그래서 저평가는 "낮다"가 아니라 **"설명되지 않게 낮다"** 로 정의하고, 세 단계로 잰다.

    1단계  R²(목 단독)                     — 목이 애초에 신호이긴 한가
    2단계  ΔR² = R²(M0+목) − R²(M0)        — ← 이것만이 우리 기여분
    3단계  M0+목 모형의 잔차만 저평가 후보

⚠️ **높은 R²는 성과가 아니다.** M0에 이미 들어간 점포수·직장인구·유동인구만으로도
매출은 상당 부분 맞는다. 심사에서 답해야 하는 질문은 "얼마나 맞히나"가 아니라
**"남들이 이미 쓰는 정보로 설명 안 되는 몫이 있나"** 이고, 그 답이 ΔR²다.

⚠️ **ΔR²를 "그 변수의 기여"로 인용할 때는 투입 순서를 함께 밝힐 것.** 먼저 넣은
변수가 다른 변수와 공유하는 몫까지 가져간다 — 전 서울 공시지가 실측에서 같은 데이터
같은 최종 R²인데 도로접면 증분이 순서에 따라 +0.082 ~ +0.242로 3배 갈렸다
(CLAUDE.md "적정가 모형의 종속변수는 공시지가다"). 여기서는 **목 변수군을 항상
M0 위에 마지막으로** 올려 순서를 고정했다.

⚠️ **Connectivity는 단독으로 넣지 않는다.** road_segment_structure 테이블에 아직
connectivity 컬럼이 없어(choice_raw·integration_raw·structure_raw만 저장) 여기서는
**도로 밀도와 링크 밀도**를 대용으로 쓴다. 둘 다 "이 상권 안에 길이 얼마나 촘촘히
얽혀 있나"를 재는 값이라 connectivity와 같은 것을 다른 방식으로 본다. 진짜
connectivity가 필요해지면 compute_segment_structure.py에 컬럼을 추가해야 한다.

**집계 수준 — 생태학적 오류를 피한다.** 매출·유동인구는 상권·행정동 단위이고 목은
세그먼트 단위다. 그래서 이 실험은 **전부 상권 단위로만** 돌린다. 여기서 나온
계수를 필지 하나에 그대로 적용하면 안 된다("동 단위는 탐색용, 판정은 필지 단위").

**표준오차는 상권 단위 클러스터 보정.** 같은 상권의 여러 업종·분기 행은 독립이
아니다. 보정하지 않으면 t값이 부풀어 "유의하다"가 거짓이 된다.

**평가는 상권 홀드아웃 CV.** 같은 상권이 train과 test에 동시에 있으면 상권 고유
효과를 외운 것을 성능으로 착각한다. GroupKFold(group=trdar_cd)로 자른다.

의존성은 numpy + psycopg2만 쓴다(statsmodels·pandas 미설치). OLS·클러스터 보정
모두 직접 계산한다 — experiment_layer_ablation.py와 같은 방침이다.

Run:
  cd backend && .venv/Scripts/python -m tools.experiment_sales_incremental
  cd backend && .venv/Scripts/python -m tools.experiment_sales_incremental --se-nm 골목상권
  cd backend && .venv/Scripts/python -m tools.experiment_sales_incremental --by-svc
  cd backend && .venv/Scripts/python -m tools.experiment_sales_incremental --probe
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

import numpy as np
import psycopg2

from app.config import settings

# 점포수가 너무 적은 (상권 × 업종)은 점포당 매출이 한두 가게에 좌우된다.
# property_market.py의 MIN_SAMPLE=8과 같은 취지 — 표본이 얇으면 값이 아니라 잡음이다.
MIN_STORES = 5

# 상권 안에 도로 세그먼트가 이만큼도 없으면 목 지표를 신뢰할 수 없다.
MIN_SEGMENTS = 3


# 상권 단위 feature. 목 지표는 상권 폴리곤으로 클리핑한 세그먼트 길이로 가중평균한다
# (긴 간선 하나와 짧은 골목 하나를 같은 무게로 평균하면 골목상권이 왜곡된다).
#
# ⚠️ integration_raw·choice_raw는 타일 간 비교가 불가능하다 — space_syntax.py가
# betweenness_centrality(normalized=False)를 쓰므로 값이 타일 크기에 비례한다
# (013_parcel_undervalued.sql 주석). 그래서 타일 안 백분위로 바꿔서 쓴다.
FEATURE_SQL = """
WITH zone AS (
    SELECT trdar_cd,
           trdar_nm,
           NULLIF(trdar_se_nm, '')  AS trdar_se_nm,
           NULLIF(signgu_nm, '')    AS signgu_nm,
           COALESCE(NULLIF(area_m2, 0), ST_Area(ST_Transform(geom, 5179))) AS area_m2,
           ST_Transform(geom, 5179) AS g
    FROM trdar_area
    WHERE {se_filter}
),
seg AS (
    SELECT z.trdar_cd,
           COUNT(*)                                            AS n_seg,
           SUM(s.integ_pct  * c.len) / NULLIF(SUM(c.len), 0)   AS integration_pct,
           SUM(s.choice_pct * c.len) / NULLIF(SUM(c.len), 0)   AS choice_pct,
           SUM(s.structure_pct_in_tile * c.len)
               / NULLIF(SUM(c.len), 0)                         AS structure_pct
    FROM zone z
    JOIN (
        SELECT seg_key, geom_5179, structure_pct_in_tile,
               100 * PERCENT_RANK() OVER (
                   PARTITION BY tile_key ORDER BY integration_raw) AS integ_pct,
               100 * PERCENT_RANK() OVER (
                   PARTITION BY tile_key ORDER BY choice_raw)      AS choice_pct
        FROM road_segment_structure
        WHERE integration_raw IS NOT NULL AND choice_raw IS NOT NULL
    ) s ON ST_Intersects(s.geom_5179, z.g)
    CROSS JOIN LATERAL (
        SELECT ST_Length(ST_Intersection(s.geom_5179, z.g)) AS len
    ) c
    WHERE c.len > 0
    GROUP BY z.trdar_cd
),
-- Connectivity 대용. 단독으로 쓰지 않고 목 변수군 안에서만 쓴다.
netdens AS (
    SELECT z.trdar_cd,
           SUM(ST_Length(ST_Intersection(r.geom_5179, z.g)))       AS road_len_m,
           COUNT(DISTINCT r.osm_id)                                AS n_links
    FROM zone z
    JOIN roads_clean r ON ST_Intersects(r.geom_5179, z.g)
    WHERE r.walkable OR r.drivable
    GROUP BY z.trdar_cd
),
-- 지하철 출입구·버스·간선 거리. 상권 안 필지들의 중앙값을 쓴다.
-- ⚠️ 역 중심(nearest_station_m)보다 출입구(nearest_entrance_m)가 실제 도보
-- 접근점이다 — 큰 환승역은 둘이 300m 넘게 벌어진다 (018 마이그레이션 주석).
acc AS (
    SELECT z.trdar_cd,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY a.nearest_entrance_m) AS entrance_m,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY a.nearest_station_m)  AS station_m,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY a.nearest_bus_stop_m) AS bus_m,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY a.nearest_arterial_m) AS arterial_m,
           AVG(a.stations_within_500m::double precision)                     AS stations_500m,
           COUNT(*)                                                          AS n_parcels
    FROM zone z
    JOIN parcel_centroids p     ON ST_Intersects(z.g, p.geom_5179)
    JOIN parcel_accessibility a ON a.pnu = p.pnu
    GROUP BY z.trdar_cd
),
-- 상업지역 면적 비율. 가장 중요한 단일 통제변수다 — 상업지역이 애초에 간선도로변에
-- 지정되므로, 빼면 "목이 좋으면 매출이 높다"의 상당 부분이 실제로는
-- "상업지역이라 매출이 높다"가 된다.
zoning AS (
    SELECT z.trdar_cd,
           SUM(ST_Area(ST_Intersection(lu.geom_5179, z.g)))
               FILTER (WHERE lu.zone_group LIKE 'commercial%%')
               / NULLIF(z.area_m2, 0) AS commercial_share
    FROM zone z
    JOIN land_use_zones lu ON ST_Intersects(lu.geom_5179, z.g)
    GROUP BY z.trdar_cd, z.area_m2
)
SELECT s.yyq,
       s.trdar_cd,
       s.svc_cd,
       z.trdar_nm,
       z.trdar_se_nm,
       z.signgu_nm,
       s.month_sales_amt,
       st.store_count,
       st.franchise_count,
       z.area_m2,
       wp.total_workers,
       seg.n_seg,
       seg.integration_pct,
       seg.choice_pct,
       seg.structure_pct,
       nd.road_len_m,
       nd.n_links,
       acc.entrance_m,
       acc.station_m,
       acc.bus_m,
       acc.arterial_m,
       acc.stations_500m,
       COALESCE(zoning.commercial_share, 0) AS commercial_share
FROM trdar_sales   s
JOIN zone          z   ON z.trdar_cd   = s.trdar_cd
JOIN trdar_stores  st  ON st.yyq = s.yyq AND st.trdar_cd = s.trdar_cd
                      AND st.svc_cd = s.svc_cd
JOIN seg           seg ON seg.trdar_cd = s.trdar_cd
LEFT JOIN netdens  nd  ON nd.trdar_cd  = s.trdar_cd
LEFT JOIN acc      acc ON acc.trdar_cd = s.trdar_cd
LEFT JOIN zoning   zoning ON zoning.trdar_cd = s.trdar_cd
LEFT JOIN trdar_workpop wp ON wp.yyq = s.yyq AND wp.trdar_cd = s.trdar_cd
WHERE s.month_sales_amt > 0
  AND st.store_count >= %(min_stores)s
  AND seg.n_seg      >= %(min_segments)s
"""


# ── feature 구성 ────────────────────────────────────────────────────────
#
# M0(통제) 안에서도 하위군을 나눠 둔다. "목을 넣기 전에 무엇을 이미 넣었는지"를
# 밝히지 않으면 ΔR² 숫자를 해석할 수 없기 때문이다.
#
# ⚠️ 임대료는 M0에 없다. reb_rent_stats·rent_listings가 있지만 집계 단위가
# 상권이 아니라 REB 상권/개별 매물이라 1:1로 붙지 않는다. 그래서 "임대료를
# 통제했다"고 말하면 안 된다 — 미통제 항목으로 보고한다.
M0_SCALE = {
    "규모":     ["ln_store_count", "ln_area_m2"],
    "수요":     ["ln_workers"],
    "임차인":   ["franchise_share"],
    "접근성":   ["ln_entrance_m", "ln_bus_m", "ln_arterial_m", "stations_500m"],
    "제도":     ["commercial_share"],
}
M0_NUMERIC = [c for cols in M0_SCALE.values() for c in cols]
M0_DUMMY = ["svc_cd", "yyq", "signgu_nm", "trdar_se_nm"]

# 목 변수군. Connectivity 자리에 밀도 두 개가 들어가 있다(모듈 docstring 참고).
SYNTAX_NUMERIC = [
    "integration_pct",
    "choice_pct",
    "ln_road_density",   # 도로연장 / 면적  — Connectivity 대용
    "ln_link_density",   # 링크수  / 면적  — Connectivity 대용
]

# 통제 없이 목만 넣은 1단계 모형. 업종·분기는 뺄 수 없다 — 업종마다 매출 규모
# 자체가 자릿수로 다르고 분기마다 물가가 다르므로, 이걸 안 빼면 1단계 R²가
# "목의 설명력"이 아니라 "업종 구성"이 된다.
STAGE1_DUMMY = ["svc_cd", "yyq"]


@dataclass
class Rows:
    """DB에서 읽은 상권 × 업종 × 분기 관측치."""

    y: np.ndarray                      # ln(점포당 매출)
    numeric: dict[str, np.ndarray]
    categorical: dict[str, np.ndarray]
    groups: np.ndarray                 # trdar_cd — 클러스터 · CV 그룹
    labels: list[str] = field(default_factory=list)   # 사람이 읽을 상권명
    dropped: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.y.shape[0])


def _ln(x: np.ndarray, floor: float = 1.0) -> np.ndarray:
    """로그. 0·음수·NULL은 floor로 눌러 로그를 정의되게 만든다.

    거리 변수에 floor=1.0을 쓰는 건 "1m보다 가까움"과 "1m"를 구분하지 않겠다는
    뜻이다. 상권 중앙값 거리라 실제로 1m 미만이 나올 일은 없다.
    """
    return np.log(np.maximum(x, floor))


def load_rows(dsn: str, se_nm: str | None) -> Rows:
    """DB에서 상권 × 업종 × 분기 관측치를 읽어 feature를 만든다."""
    se_filter = "TRUE" if se_nm is None else "trdar_se_nm = %(se_nm)s"
    sql = FEATURE_SQL.format(se_filter=se_filter)
    params = {"min_stores": MIN_STORES, "min_segments": MIN_SEGMENTS, "se_nm": se_nm}

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        raw = cur.fetchall()

    if not raw:
        raise SystemExit("관측치가 0건이다 — 필터나 적재 상태를 확인할 것.")

    idx = {c: i for i, c in enumerate(cols)}

    def col(name: str, default: float = np.nan) -> np.ndarray:
        j = idx[name]
        return np.array(
            [default if r[j] is None else float(r[j]) for r in raw], dtype=float
        )

    def cat(name: str) -> np.ndarray:
        j = idx[name]
        return np.array([("" if r[j] is None else str(r[j])) for r in raw], dtype=object)

    sales = col("month_sales_amt")
    stores = col("store_count")
    area = col("area_m2")
    road_len = col("road_len_m", 0.0)
    links = col("n_links", 0.0)
    franchise = col("franchise_count", 0.0)

    # 점포당 매출. 상권 크기 효과가 자동으로 빠져 "목이 좋으면 한 가게가 더
    # 번다"를 직접 본다. 그래도 ln_store_count를 M0에 남기는 건 경쟁 밀도
    # (같은 업종이 많으면 나눠 먹는다)가 별개의 경로이기 때문이다.
    y = np.log(sales / np.maximum(stores, 1.0))

    numeric = {
        "ln_store_count":   _ln(stores),
        "ln_area_m2":       _ln(area, 1.0),
        "ln_workers":       _ln(col("total_workers", 0.0), 1.0),
        "franchise_share":  np.clip(franchise / np.maximum(stores, 1.0), 0.0, 1.0),
        "ln_entrance_m":    _ln(col("entrance_m")),
        "ln_bus_m":         _ln(col("bus_m")),
        "ln_arterial_m":    _ln(col("arterial_m")),
        "stations_500m":    col("stations_500m", 0.0),
        "commercial_share": col("commercial_share", 0.0),
        "integration_pct":  col("integration_pct"),
        "choice_pct":       col("choice_pct"),
        # 밀도는 면적으로 나눈 뒤 로그. 1e6을 곱해 km² 단위로 읽히게 한다.
        "ln_road_density":  _ln(road_len / np.maximum(area, 1.0) * 1e6, 1e-6),
        "ln_link_density":  _ln(links    / np.maximum(area, 1.0) * 1e6, 1e-6),
    }
    categorical = {name: cat(name) for name in ("svc_cd", "yyq", "signgu_nm", "trdar_se_nm")}
    groups = cat("trdar_cd")
    names = cat("trdar_nm")

    # 목 지표나 종속변수가 NaN인 행은 버린다. **몇 건을 왜 버렸는지 반드시
    # 보고한다** — 조용히 떨어뜨리면 "표본이 원래 그랬다"로 읽힌다.
    keep = np.isfinite(y)
    dropped = {"ln_sales_nan": int((~keep).sum())}
    for name, v in numeric.items():
        bad = ~np.isfinite(v)
        if bad.any():
            dropped[name] = int(bad.sum())
        keep &= ~bad

    return Rows(
        y=y[keep],
        numeric={k: v[keep] for k, v in numeric.items()},
        categorical={k: v[keep] for k, v in categorical.items()},
        groups=groups[keep],
        labels=[str(n) for n in names[keep]],
        dropped=dropped,
    )


# ── 설계행렬 · OLS ──────────────────────────────────────────────────────


def build_design(
    rows: Rows,
    numeric: list[str],
    dummies: list[str],
    levels: dict[str, list[str]] | None = None,
) -> tuple[np.ndarray, list[str], dict[str, list[str]]]:
    """상수항 + 수치변수 + 더미(첫 수준 제외)로 설계행렬을 만든다.

    ``levels``를 주면 그 수준 목록을 그대로 쓴다 — CV에서 train의 수준을 test에
    적용하기 위한 것이다. test에만 있는 수준은 모든 더미가 0이 되고(=기준 수준으로
    취급) train에만 있는 수준은 test에서 전부 0인 열이 된다. 둘 다 의도된 동작이다.
    """
    n = len(rows)
    out_levels: dict[str, list[str]] = {}
    blocks = [np.ones((n, 1))]
    names = ["const"]

    for name in numeric:
        blocks.append(rows.numeric[name].reshape(-1, 1))
        names.append(name)

    for name in dummies:
        v = rows.categorical[name]
        if levels is not None:
            lv = levels[name]
        else:
            lv = sorted(set(v.tolist()))
        out_levels[name] = lv
        # 첫 수준을 기준으로 잡아 뺀다(더미 함정 회피).
        for level in lv[1:]:
            blocks.append((v == level).astype(float).reshape(-1, 1))
            names.append(f"{name}={level}")

    return np.hstack(blocks), names, out_levels


def solve_ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """정규방정식 대신 lstsq. 더미가 많아 X가 계급 결손이 되기 쉽다.

    ``rcond=None``은 numpy 기본 컷오프를 쓰겠다는 뜻으로, 완전 공선인 열의
    계수를 0으로 눌러 준다 — 어느 수준이 다른 수준의 선형결합인 경우
    (예: 한 상권에 업종이 하나뿐) 예외로 죽지 않고 넘어간다.
    """
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def r2(y: np.ndarray, pred: np.ndarray) -> float:
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def cluster_se(
    X: np.ndarray, y: np.ndarray, beta: np.ndarray, groups: np.ndarray
) -> np.ndarray:
    """상권 단위 클러스터 보정 표준오차 (CR0 + 소표본 보정).

    같은 상권의 여러 업종·분기 행은 독립이 아니다 — 그 상권만의 임대료·주차·
    앵커시설이 모든 행에 같이 실린다. 보정하지 않으면 표준오차가 작아져 t값이
    부풀고, "유의하다"가 거짓이 된다. 클러스터 수가 상권 수(≈1,000)라 점근
    근사는 무리 없이 성립한다.
    """
    n, k = X.shape
    resid = y - X @ beta
    xtx_inv = np.linalg.pinv(X.T @ X)

    meat = np.zeros((k, k))
    uniq = np.unique(groups)
    for g in uniq:
        m = groups == g
        xg_u = X[m].T @ resid[m]          # (k,)
        meat += np.outer(xg_u, xg_u)

    n_clusters = uniq.size
    # Stata와 같은 소표본 보정.
    dof = (n_clusters / max(n_clusters - 1, 1)) * ((n - 1) / max(n - k, 1))
    cov = dof * (xtx_inv @ meat @ xtx_inv)
    return np.sqrt(np.maximum(np.diag(cov), 0.0))


def group_kfold(groups: np.ndarray, k: int, seed: int = 20260825) -> list[np.ndarray]:
    """상권 홀드아웃 fold. **같은 상권은 통째로 한 fold에만 들어간다.**

    상권을 쪼개면 같은 상권의 다른 분기가 train에 남아, 상권 고유효과를 외운
    것을 성능으로 착각한다. 매출은 상권 단위라 공간 홀드아웃이 곧 상권 홀드아웃이다.
    """
    uniq = np.unique(groups)
    rng = np.random.default_rng(seed)
    rng.shuffle(uniq)
    buckets = np.array_split(uniq, k)
    return [np.isin(groups, b) for b in buckets]


# ── 3단계 실험 ─────────────────────────────────────────────────────────


@dataclass
class ModelFit:
    label: str
    numeric: list[str]
    dummies: list[str]
    r2_in: float
    r2_cv: float
    n_params: int


def fit_model(
    rows: Rows, label: str, numeric: list[str], dummies: list[str], folds: int
) -> ModelFit:
    X, _, _ = build_design(rows, numeric, dummies)
    beta = solve_ols(X, rows.y)
    r2_in = r2(rows.y, X @ beta)

    # 상권 홀드아웃 CV. 표본 내 R²는 더미가 많을수록 무조건 오르므로, 목을
    # 넣어서 오른 게 진짜인지 판별하려면 표본 밖 성능을 함께 봐야 한다.
    preds = np.empty_like(rows.y)
    preds[:] = np.nan
    for test_mask in group_kfold(rows.groups, folds):
        train_mask = ~test_mask
        if train_mask.sum() < X.shape[1] * 2 or test_mask.sum() == 0:
            continue
        tr = _subset(rows, train_mask)
        te = _subset(rows, test_mask)
        Xtr, _, levels = build_design(tr, numeric, dummies)
        Xte, _, _ = build_design(te, numeric, dummies, levels=levels)
        b = solve_ols(Xtr, tr.y)
        preds[test_mask] = Xte @ b

    ok = np.isfinite(preds)
    r2_cv = r2(rows.y[ok], preds[ok]) if ok.sum() > 0 else float("nan")
    return ModelFit(label, numeric, dummies, r2_in, r2_cv, X.shape[1])


def _subset(rows: Rows, mask: np.ndarray) -> Rows:
    return Rows(
        y=rows.y[mask],
        numeric={k: v[mask] for k, v in rows.numeric.items()},
        categorical={k: v[mask] for k, v in rows.categorical.items()},
        groups=rows.groups[mask],
    )


def run(rows: Rows, folds: int) -> dict:
    """1단계 단독 → 2단계 증분 → 3단계 잔차."""
    m_syntax = fit_model(rows, "S  목 단독",   SYNTAX_NUMERIC, STAGE1_DUMMY, folds)
    m0       = fit_model(rows, "M0 통제만",    M0_NUMERIC,     M0_DUMMY,     folds)
    m1       = fit_model(rows, "M1 통제+목",   M0_NUMERIC + SYNTAX_NUMERIC, M0_DUMMY, folds)

    # 통제 하위군을 하나씩 빼서 "무엇이 목의 몫을 가져가고 있나"를 본다.
    # ⚠️ 이 숫자들은 서로 더해지지 않는다 — 각각 M0 전체에서 그 군만 뺀 것이다.
    leave_out = {}
    for name, cols in M0_SCALE.items():
        rest = [c for c in M0_NUMERIC if c not in cols]
        f = fit_model(rows, f"M1−{name}", rest + SYNTAX_NUMERIC, M0_DUMMY, folds)
        leave_out[name] = round(m1.r2_in - f.r2_in, 5)

    # 목 계수 + 상권 클러스터 표준오차. 계수는 M1(통제 포함)에서 읽는다.
    X, names, _ = build_design(rows, M0_NUMERIC + SYNTAX_NUMERIC, M0_DUMMY)
    beta = solve_ols(X, rows.y)
    se = cluster_se(X, rows.y, beta, rows.groups)
    coefs = {}
    for name in SYNTAX_NUMERIC:
        j = names.index(name)
        b, s = float(beta[j]), float(se[j])
        coefs[name] = {
            "coef": round(b, 5),
            "cluster_se": round(s, 5),
            "t": round(b / s, 2) if s > 0 else None,
        }

    # 3단계 — 저평가 후보는 **M1의 잔차**다. M0나 목 단독의 잔차가 아니다.
    resid = rows.y - X @ beta

    return {
        "n_obs": len(rows),
        "n_trdar": int(np.unique(rows.groups).size),
        "n_svc": int(np.unique(rows.categorical["svc_cd"]).size),
        "dropped": rows.dropped,
        "stage1_syntax_only": {"r2_in": round(m_syntax.r2_in, 4),
                               "r2_cv": round(m_syntax.r2_cv, 4)},
        "stage2": {
            "M0_r2_in": round(m0.r2_in, 4), "M0_r2_cv": round(m0.r2_cv, 4),
            "M1_r2_in": round(m1.r2_in, 4), "M1_r2_cv": round(m1.r2_cv, 4),
            "delta_r2_in": round(m1.r2_in - m0.r2_in, 5),
            "delta_r2_cv": round(m1.r2_cv - m0.r2_cv, 5),
            "n_params_M0": m0.n_params, "n_params_M1": m1.n_params,
        },
        "syntax_coefs_in_M1": coefs,
        "m0_leave_one_group_out": leave_out,
        "stage3_residual": {
            "sd": round(float(resid.std()), 4),
            "note": "저평가 후보는 이 잔차 하위 꼬리다. M0나 목 단독 잔차를 쓰면 안 된다.",
        },
    }


PROBE_TABLES = (
    "trdar_sales", "trdar_stores", "trdar_workpop", "trdar_area",
    "road_segment_structure", "roads_clean", "parcel_centroids",
    "parcel_accessibility", "land_use_zones",
)


def probe(dsn: str) -> int:
    """필요한 표가 다 있고 비어 있지 않은지만 확인한다 (무거운 공간조인 없이).

    운영 DB는 토지가격 분석에 필요한 최소 집합만 담고 있을 수 있으므로, 본
    실험을 돌리기 전에 이걸 먼저 돌려 무엇이 없는지 확인한다.
    """
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        missing = []
        for t in PROBE_TABLES:
            cur.execute("SELECT to_regclass(%s)", (t,))
            if cur.fetchone()[0] is None:
                print(f"  ✗ {t:<24} 없음")
                missing.append(t)
                continue
            cur.execute(f"SELECT count(*) FROM {t}")  # noqa: S608 - 고정 목록
            n = cur.fetchone()[0]
            mark = "✓" if n else "✗"
            print(f"  {mark} {t:<24} {n:>10,} 행")
            if not n:
                missing.append(t)
        if missing:
            print(f"\n비어 있거나 없는 표: {', '.join(missing)}")
            print("적재가 안 된 DB다 — 다른 DB에 붙거나 해당 ingest 도구를 먼저 돌릴 것.")
            return 1
    print("\n전부 준비됨.")
    return 0


def report(res: dict) -> None:
    s2 = res["stage2"]
    print(f"\n관측치 {res['n_obs']:,}건 · 상권 {res['n_trdar']:,}개 · 업종 {res['n_svc']}개")
    if res["dropped"]:
        print(f"제외: {res['dropped']}")

    print("\n── 1단계. 목만으로 얼마나 맞추나 ─────────────────────────")
    s1 = res["stage1_syntax_only"]
    print(f"  R² (표본내) {s1['r2_in']:.4f}   R² (상권 홀드아웃) {s1['r2_cv']:.4f}")
    print("  ※ 업종·분기는 통제로 들어가 있다 — 안 빼면 '업종 구성'을 목의 설명력으로 읽게 된다.")

    print("\n── 2단계. 통제 뒤 목이 더 설명하는 몫 (← 진짜 기여분) ────")
    print(f"  M0 통제만    R² {s2['M0_r2_in']:.4f} (CV {s2['M0_r2_cv']:.4f})  변수 {s2['n_params_M0']}개")
    print(f"  M1 통제+목   R² {s2['M1_r2_in']:.4f} (CV {s2['M1_r2_cv']:.4f})  변수 {s2['n_params_M1']}개")
    print(f"  ΔR² 표본내   {s2['delta_r2_in']*100:+.2f}%p")
    print(f"  ΔR² 홀드아웃 {s2['delta_r2_cv']*100:+.2f}%p   ← 심사에서 말할 숫자")
    print("  ※ 목 변수군은 항상 M0 위에 마지막으로 올렸다. 순서를 바꾸면 숫자가 달라진다.")

    print("\n── 목 계수 (M1 안, 상권 클러스터 보정 SE) ────────────────")
    for name, c in res["syntax_coefs_in_M1"].items():
        t = c["t"]
        sig = "" if t is None else ("  ***" if abs(t) > 2.58 else "  **" if abs(t) > 1.96 else "")
        print(f"  {name:<18} {c['coef']:+.4f}  (SE {c['cluster_se']:.4f}, t {t}){sig}")

    print("\n── M0 하위군을 하나씩 뺐을 때 R² 손실 ────────────────────")
    print("  (무엇이 목의 몫을 가져가고 있는지 본다 — 서로 더해지지 않는다)")
    for name, d in sorted(res["m0_leave_one_group_out"].items(), key=lambda kv: -kv[1]):
        print(f"  {name:<10} {d*100:+.2f}%p")

    print("\n── 3단계 ────────────────────────────────────────────────")
    print(f"  M1 잔차 표준편차 {res['stage3_residual']['sd']:.4f} (로그 단위)")
    print(f"  {res['stage3_residual']['note']}")
    print("\n⚠️ 미통제: 임대료·주차·앵커시설. reb_rent_stats는 집계 단위가 상권과")
    print("   맞지 않아 붙이지 않았다 — '임대료를 통제했다'고 말하면 안 된다.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--se-nm", default=None,
                    help="상권 유형 필터 (골목상권·발달상권·전통시장·관광특구). 기본은 전체")
    ap.add_argument("--folds", type=int, default=5, help="상권 홀드아웃 fold 수")
    ap.add_argument("--by-svc", action="store_true",
                    help="업종별로 따로 돌린다 (표본 200건 이상인 업종만)")
    ap.add_argument("--probe", action="store_true", help="필요한 표가 있는지만 확인")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로만 출력")
    args = ap.parse_args()

    dsn = settings.database_url
    if args.probe:
        return probe(dsn)

    rows = load_rows(dsn, args.se_nm)

    if args.by_svc:
        out = {}
        for svc in sorted(set(rows.categorical["svc_cd"].tolist())):
            mask = rows.categorical["svc_cd"] == svc
            if mask.sum() < 200:
                continue
            sub = _subset(rows, mask)
            sub.dropped = {}
            # 업종을 하나로 고정했으므로 업종 더미는 뺀다.
            res = run_single_svc(sub, args.folds)
            out[svc] = res
            print(f"\n===== {svc}  (n={mask.sum():,}) =====")
            print(f"  ΔR² 홀드아웃 {res['stage2']['delta_r2_cv']*100:+.2f}%p")
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    res = run(rows, args.folds)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        report(res)
    return 0


def run_single_svc(rows: Rows, folds: int) -> dict:
    """업종을 하나로 고정한 경우 — 업종 더미를 빼고 같은 3단계를 돌린다."""
    global M0_DUMMY, STAGE1_DUMMY  # noqa: PLW0603 - 스크립트 한정
    saved_m0, saved_s1 = M0_DUMMY, STAGE1_DUMMY
    M0_DUMMY = [d for d in saved_m0 if d != "svc_cd"]
    STAGE1_DUMMY = [d for d in saved_s1 if d != "svc_cd"]
    try:
        return run(rows, folds)
    finally:
        M0_DUMMY, STAGE1_DUMMY = saved_m0, saved_s1


if __name__ == "__main__":
    sys.exit(main())
