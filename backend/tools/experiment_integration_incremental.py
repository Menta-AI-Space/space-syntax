"""Integration이 비준표 통제변수를 넣어도 공시지가를 설명하는가 — 스크리닝 실험.

**왜 이걸 먼저 하는가.** 개별공시지가는 비교표준지 × 토지가격비준표로 산정되고
비준표 항목에 **도로접면·도로거리**가 들어 있다. 그래서 "Integration이 높은 곳의
공시지가가 높다"는 상관은 상당 부분 행정 산식을 재현한 것일 수 있다. 높은 R²는
성과가 아니다. 통제 후에도 Integration이 살아남지 않으면 이 방향 전체를 접어야
하므로, 파이프라인을 다 만들기 전에 가장 값싼 이 검사를 먼저 돌린다
(`docs/LAND_VALUE_MODEL_RESEARCH.md`, CLAUDE.md "분석 대상은 토지 가격이다").

**설계**

    y            = ln(공시지가 원/㎡)
    관심변수     = 최근접 세그먼트의 Integration **타일 내 백분위** (0~1)
    통제변수     = 용도지역 · 도로접면 · 지목 더미, ln(면적), 동 고정효과

Integration을 raw로 쓰지 않는 이유: `compute_segment_structure.py`가
`betweenness_centrality(normalized=False)`를 쓰므로 raw 값이 타일 크기에 비례해
타일 간 비교가 불가능하다. 그래서 타일 안에서의 백분위를 쓴다.

모형 A(통제만) 대 모형 B(통제+Integration)의 R²를 비교하고, Integration 계수를
**동 단위 클러스터 보정 표준오차**와 함께 보고한다.

**판정은 계수 크기와 유의성으로 하고, 증분 R²는 합격 기준으로 쓰지 않는다.**
처음엔 `ΔR² < 0.002`면 실패로 뒀는데 같은 데이터에서 표본만 커지자 결론이 세 번
뒤집혔다(3.4만 ✗ → 7.6만 ○ → 70만 ✗). 공시지가는 행정적으로 산정되는 변수라
어떤 개별 설명변수도 큰 ΔR²를 가질 수 없다 — 자세한 것은 `_verdict` 주석 참고.

의존성은 numpy만 쓴다(statsmodels·pandas 미설치). OLS·클러스터 보정 모두 직접
계산한다.

Run:
  cd backend && .venv/Scripts/python -m tools.experiment_integration_incremental
  cd backend && .venv/Scripts/python -m tools.experiment_integration_incremental --sgg 11180
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import psycopg2

from app.config import settings

# 도로·하천 등 시장 거래 대상이 아닌 필지는 뺀다. compute_parcel_undervalued.py와
# 같은 기준을 쓴다 — 한쪽 컬럼이 비어 있는 필지가 있어 양쪽으로 거른다.
_EXCLUDE_SPECIAL = ("도로", "하천", "제방", "구거", "유지")
_EXCLUDE_CATEGORY = ("도로", "하천", "제방", "구거", "유지", "수도용지")

# 필지 → 최근접 세그먼트. KNN(`<->` ORDER BY LIMIT 1)은 **기저 테이블에 직접**
# 걸어야 GIST 인덱스를 탄다 — PERCENT_RANK를 CTE로 감싸 그 CTE에 KNN을 걸면
# 인덱스를 못 쓰고 필지마다 전체 스캔이 된다(실측: 10분 넘게 안 끝남).
# 그래서 백분위는 seg_key로 나중에 붙인다.
#
# 그리고 **세그먼트 쪽에서 출발해 후보 필지를 먼저 좁힌다.** 필지 88만 개 전부에
# KNN과 폴리곤 포함 판정을 돌리면 느리다 — 구조 배치가 아직 서울 일부만 덮으므로
# 후보는 수만 개뿐이고, ST_DWithin이 필지 GIST 인덱스를 탄다.
_DATA_SQL = """
WITH near AS (
    SELECT DISTINCT pc.pnu, pc.geom_5179
    FROM road_segment_structure s
    JOIN parcel_centroids pc
      ON ST_DWithin(pc.geom_5179, s.geom_5179, %(max_dist)s)
), pct AS (
    SELECT seg_key, tile_key, integration_raw,
           PERCENT_RANK() OVER (
               PARTITION BY tile_key ORDER BY integration_raw
           ) AS integ_pct
    FROM road_segment_structure
)
SELECT
    lp.price_per_sqm,
    b.adm_cd,
    COALESCE(NULLIF(ch.zone_name_1, ''), '(미상)')      AS zone_name,
    COALESCE(NULLIF(ch.road_side_name, ''), '(미상)')   AS road_side,
    COALESCE(NULLIF(ch.land_category, ''), '(미상)')    AS land_category,
    ch.area_sqm,
    pct.integ_pct,
    s.dist_m,
    pct.tile_key,
    pct.integration_raw
FROM near pc
JOIN land_prices lp ON lp.pnu = pc.pnu
JOIN sgis_dong_boundary b ON ST_Contains(b.geom, pc.geom_5179)
JOIN LATERAL (
    SELECT g.seg_key, g.geom_5179 <-> pc.geom_5179 AS dist_m
    FROM road_segment_structure g
    ORDER BY g.geom_5179 <-> pc.geom_5179
    LIMIT 1
) s ON TRUE
JOIN pct ON pct.seg_key = s.seg_key
LEFT JOIN parcel_characteristics ch
       ON ch.pnu = lp.pnu AND ch.base_year = lp.base_year
WHERE lp.base_year = %(year)s
  AND lp.price_per_sqm > 0
  AND s.dist_m <= %(max_dist)s
  AND ch.area_sqm > 0
  AND (lp.special_name IS NULL OR lp.special_name <> ALL(%(excl_special)s))
  AND (ch.land_category IS NULL OR ch.land_category <> ALL(%(excl_category)s))
  AND (%(sgg)s IS NULL OR LEFT(b.adm_cd, 5) = %(sgg)s)
"""


def _dummies(values: list[str], *, min_count: int) -> tuple[np.ndarray, list[str]]:
    """One-hot, 최빈 수준을 기준(drop)으로 삼고 희소 수준은 '(기타)'로 묶는다.

    희소 수준을 그대로 두면 관측치 1~2개짜리 더미가 그 필지의 잔차를 통째로
    흡수해 R²를 부풀린다.
    """
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    kept = {v for v, n in counts.items() if n >= min_count}
    folded = [v if v in kept else "(기타)" for v in values]

    levels = sorted(set(folded), key=lambda v: -counts.get(v, 0))
    base = levels[0]  # 최빈 수준을 기준으로 뺀다
    others = [lv for lv in levels[1:]]
    if not others:
        return np.zeros((len(values), 0)), []
    idx = {lv: i for i, lv in enumerate(others)}
    out = np.zeros((len(values), len(others)))
    for r, v in enumerate(folded):
        j = idx.get(v)
        if j is not None:
            out[r, j] = 1.0
    return out, [f"{base}→{lv}" for lv in others]


def _demean(A: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """집단 내 평균을 뺀다 — 고정효과 흡수(within 변환).

    더미를 명시적으로 만들면 전 서울(70만 필지 × 945 타일)에서 행렬이 4.4GiB로
    터진다. Frisch-Waugh-Lovell 정리에 따라 within 변환은 더미를 넣은 회귀와
    **계수·잔차가 정확히 같다** — 메모리만 아끼는 것이고 결과는 동일하다.
    """
    out = A.astype(float, copy=True)
    order = np.argsort(groups, kind="stable")
    g_sorted = groups[order]
    # 같은 값이 연속으로 모이므로 경계만 찾으면 집단별 슬라이스가 된다.
    bounds = np.flatnonzero(np.r_[True, g_sorted[1:] != g_sorted[:-1], True])
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        idx = order[lo:hi]
        out[idx] -= out[idx].mean(axis=0)
    return out


def _ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """(beta, resid, R²). 순위 결손에 안전하도록 lstsq를 쓴다."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return beta, resid, 1.0 - ss_res / ss_tot


def _cluster_se(X: np.ndarray, resid: np.ndarray, groups: np.ndarray,
                *, extra_params: int = 0) -> np.ndarray:
    """동 단위 클러스터 보정 표준오차 (Liang-Zeger, 소표본 보정 포함).

    ``extra_params``는 within 변환으로 흡수해 X에 열로 남지 않은 모수 개수다
    (고정효과 수준 수). 이걸 안 세면 자유도가 과대평가돼 표준오차가 작게 나온다.
    """
    n, k = X.shape
    df_params = k + extra_params  # 자유도 보정에만 쓴다 (행렬 크기는 k 그대로)
    xtx_inv = np.linalg.pinv(X.T @ X)
    meat = np.zeros((k, k))
    order = np.argsort(groups, kind="stable")
    g_sorted = groups[order]
    bounds = np.flatnonzero(np.r_[True, g_sorted[1:] != g_sorted[:-1], True])
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        idx = order[lo:hi]
        s = X[idx].T @ resid[idx]
        meat += np.outer(s, s)
    ng = len(bounds) - 1
    correction = (ng / max(ng - 1, 1)) * ((n - 1) / max(n - df_params, 1))
    vcov = xtx_inv @ meat @ xtx_inv * correction
    return np.sqrt(np.maximum(np.diag(vcov), 0.0))


# CLAUDE.md의 sanity check 범위(국내 실측): 지하철 발표 +2.7~3.6%, 개통 후 600m 내
# 최대 +12.7%. 이 상한을 크게 넘으면 성능이 아니라 편향의 증거로 본다.
_LITERATURE_CEILING_PCT = 12.7


def _verdict(coef: float, tstat: float, delta_r2: float) -> str:
    """판정. **증분 R²를 합격 기준으로 쓰지 않는다.**

    처음엔 `ΔR² < 0.002`면 ✗로 뒀는데, 같은 데이터에서 표본만 커지자 결론이 세 번
    뒤집혔다 — 3.4만 ✗ → 7.6만 ○ → 70만 ✗. 공시지가는 행정적으로 산정되는 변수라
    어떤 개별 설명변수도 큰 ΔR²를 가질 수 없고, 표본이 커지면 ΔR²는 작은 참값으로
    수렴하는 반면 t값은 계속 커진다. 그래서 판정은 **계수 크기와 유의성**으로 하고
    ΔR²는 효과 크기의 참고치로만 보고한다.
    """
    pct = 100 * (np.exp(coef) - 1)
    lines = [f"\n판정 근거: 계수 {pct:+.1f}% · t {tstat:+.2f} · ΔR² {delta_r2:+.4f}"]

    if coef <= 0:
        lines.append("판정: ✗ 부호가 예상과 반대다. 길 구조가 좋을수록 지가가 낮다는"
                     "\n      뜻이므로 변수 구성이나 통제 설계를 다시 봐야 한다.")
    elif abs(tstat) < 2:
        lines.append("판정: △ 부호는 맞지만 동 클러스터 보정 후 유의하지 않다."
                     "\n      표본(세그먼트 커버리지)을 늘려 다시 볼 것.")
    elif pct > _LITERATURE_CEILING_PCT:
        lines.append(f"판정: ⚠ 유의하지만 계수가 문헌 상한({_LITERATURE_CEILING_PCT}%)을"
                     " 넘는다."
                     "\n      성능이 아니라 편향(미관측 입지질·측정오차)의 증거로 본다.")
    else:
        lines.append("판정: ○ 부호가 맞고, 문헌 범위 안이며, 클러스터 보정 후 유의하다.")
        lines.append(f"      단 ΔR² {delta_r2:.4f}는 작다 — 통제변수가 지가 변동의"
                     "\n      대부분을 이미 설명하므로, 이 변수 단독으로 저평가를"
                     "\n      판정하기엔 근거가 약하다는 뜻이다.")
    return "\n".join(lines)


def run(year: int | None, sgg: str | None, max_dist: float, min_count: int,
        fe: str, integ_kind: str) -> int:
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    if year is None:
        cur.execute("SELECT MAX(base_year) FROM land_prices")
        year = cur.fetchone()[0]
    print(f"기준연도 {year}" + (f", 자치구 {sgg}" if sgg else ", 서울(계산된 영역 전체)"))

    cur.execute(_DATA_SQL, {
        "year": year, "max_dist": max_dist, "sgg": sgg,
        "excl_special": list(_EXCLUDE_SPECIAL),
        "excl_category": list(_EXCLUDE_CATEGORY),
    })
    rows = cur.fetchall()
    conn.close()
    if len(rows) < 200:
        print(f"표본이 {len(rows)}개뿐이다 — 세그먼트 구조 배치를 더 돌려야 한다.")
        return 1

    price = np.array([r[0] for r in rows], dtype=float)
    dong = np.array([r[1] for r in rows])
    zone = [r[2] for r in rows]
    road_side = [r[3] for r in rows]
    category = [r[4] for r in rows]
    area = np.array([r[5] for r in rows], dtype=float)
    integ_pct = np.array([r[6] for r in rows], dtype=float)
    tile = np.array([r[8] for r in rows])
    integ_raw = np.array([r[9] for r in rows], dtype=float)

    y = np.log(price)
    ln_area = np.log(area)

    # 관심변수. pct는 타일 내 백분위(타일 간 비교 불가 문제를 우회하지만 대규모
    # 공간 변동이 사라진다), raw는 타일 안에서만 비교 가능하므로 반드시 타일
    # 고정효과와 함께 써야 한다.
    if integ_kind == "raw":
        # 타일마다 스케일이 달라 그대로 넣으면 계수 해석이 불가능하다 — 타일 내
        # 표준화로 스케일을 맞춘다(타일 FE와 함께 쓰면 within 변동만 남는다).
        integ = np.zeros_like(integ_raw)
        for t in np.unique(tile):
            m = tile == t
            v = integ_raw[m]
            sd = v.std()
            integ[m] = (v - v.mean()) / sd if sd > 0 else 0.0
        integ_label = "Integration (타일 내 표준화, 1 s.d.)"
    else:
        integ = integ_pct
        integ_label = "Integration 백분위 (0→1)"

    d_zone, _ = _dummies(zone, min_count=min_count)
    d_side, _ = _dummies(road_side, min_count=min_count)
    d_cat, _ = _dummies(category, min_count=min_count)
    fe_groups = np.array(tile if fe == "tile" else dong)
    n_fe = len(np.unique(fe_groups))

    # 고정효과는 더미가 아니라 within 변환으로 흡수한다(위 _demean 주석 참고).
    # 그래서 절편도 필요 없다 — 집단 내 평균을 뺀 뒤에는 상수항이 0이 된다.
    sst = float(((y - y.mean()) ** 2).sum())
    yd = _demean(y, fe_groups)
    other = np.hstack([ln_area[:, None], d_zone, d_side, d_cat])
    n_controls = other.shape[1] + n_fe

    print(f"\n스펙: {integ_label} · 고정효과 {fe} ({n_fe}개 수준, within 변환으로 흡수)")
    print(f"표본 {len(y):,} 필지 · 동 {len(np.unique(dong))}개 · 통제변수 {n_controls}개")
    print(f"  ln(지가) 평균 {y.mean():.3f} (지가 중앙값 {np.median(price):,.0f} 원/㎡)")
    print(f"  Integration 백분위 평균 {integ.mean():.3f}")
    unknown_side = sum(1 for v in road_side if v in ("(미상)", "지정되지않음"))
    print(f"  ⚠️ 도로접면 미상/지정되지않음 {unknown_side / len(rows):.0%}"
          " — 순환성 방어가 이만큼 약하다")

    # 통제변수 R²의 출처를 쪼갠다. 통제만으로 R²가 0.87쯤 나오는데, 그게
    # **지리**(동/타일 고정효과)에서 오는 것이면 정당한 통제이고, **비준표
    # 입력변수**(용도지역·도로접면)에서 오는 것이면 종속변수가 순환적이라는
    # 뜻이다. 둘은 처방이 다르므로 반드시 구분해서 봐야 한다.
    def r2_of(cols: np.ndarray | None) -> tuple[np.ndarray, np.ndarray, float]:
        """within 변환 후 회귀. R²는 원래 SST 기준이라 더미 방식과 같은 값이다."""
        if cols is None or cols.shape[1] == 0:
            resid = yd
            return np.zeros(0), resid, 1.0 - float(resid @ resid) / sst
        Xd = _demean(cols, fe_groups)
        beta, *_ = np.linalg.lstsq(Xd, yd, rcond=None)
        resid = yd - Xd @ beta
        return beta, resid, 1.0 - float(resid @ resid) / sst

    # ⚠️ 이 분해는 **투입 순서에 의존한다.** 전 서울 실측에서 도로접면 기여가
    # 마지막에 넣으면 +0.082, 먼저 넣으면 +0.242로 3배 달라졌다(최종 R²는 동일).
    # 먼저 넣은 변수가 다른 변수와 공유하는 몫까지 가져가기 때문이다. 개별 수치를
    # "이 변수의 기여"로 인용하면 다른 순서로 계산한 문서와 어긋난다.
    print("\n통제변수 R² 분해 (누적 · 아래 순서 기준 — 순서를 바꾸면 값이 달라진다):")
    steps = [
        ("고정효과만", None),
        ("+ 용도지역", d_zone),
        ("+ 지목·면적", np.hstack([d_zone, d_cat, ln_area[:, None]])),
        ("+ 도로접면(=통제 전체)", other),
    ]
    prev = 0.0
    for label, cols in steps:
        _, _, r2 = r2_of(cols)
        print(f"  {label:24s} R² = {r2:.4f}  (+{r2 - prev:.4f})")
        prev = r2

    # 모형 0: Integration만 (고정효과·통제 없음) — 통제 전 상관의 크기
    x_only = np.hstack([np.ones((len(y), 1)), integ[:, None]])
    _, _, r2_only = _ols(y, x_only)

    # 모형 A: 통제만
    _, _, r2_a = r2_of(other)

    # 모형 B: 통제 + Integration
    full_cols = np.hstack([other, integ[:, None]])
    beta_b, resid_b, r2_b = r2_of(full_cols)
    # 클러스터 보정은 within 변환된 설계행렬로 한다. df 보정에는 흡수된
    # 고정효과 개수를 함께 센다 — 안 세면 표준오차가 과소평가된다.
    Xd_full = _demean(full_cols, fe_groups)
    se = _cluster_se(Xd_full, resid_b, dong, extra_params=n_fe)
    coef = beta_b[-1]
    coef_se = se[-1]
    tstat = coef / coef_se if coef_se > 0 else float("nan")

    print("\n" + "=" * 62)
    print(f"모형 0  Integration만          R² = {r2_only:.4f}")
    print(f"모형 A  통제변수만            R² = {r2_a:.4f}")
    print(f"모형 B  통제 + Integration    R² = {r2_b:.4f}")
    print(f"        증분 R² (B − A)       ΔR² = {r2_b - r2_a:+.4f}")
    print("-" * 62)
    print(f"{integ_label} 계수 = {coef:+.4f}  (동 클러스터 SE {coef_se:.4f}, t = {tstat:+.2f})")
    print(f"  해석: 1 단위 증가 시 ln(지가) {coef:+.4f}"
          f" ≈ 지가 {100 * (np.exp(coef) - 1):+.1f}%")
    print("=" * 62)

    print(_verdict(coef, tstat, r2_b - r2_a))
    print("\n주의: 이건 횡단면 상관이고 인과 추정치가 아니다. 계수를 '도로가 생기면"
          "\n오르는 폭'으로 읽지 말 것 (CLAUDE.md 결과 표기 규칙).")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, default=None, help="기준연도 (기본: land_prices 최신)")
    p.add_argument("--sgg", default=None, help="자치구 5자리 코드 (기본: 계산된 영역 전체)")
    p.add_argument("--max-dist", type=float, default=50.0,
                   help="최근접 세그먼트까지 허용 거리 m (기본 50)")
    p.add_argument("--min-count", type=int, default=30,
                   help="더미 수준 최소 관측치 — 그 미만은 '(기타)'로 묶는다")
    p.add_argument("--fe", choices=("dong", "tile"), default="dong",
                   help="고정효과 단위. raw를 쓸 때는 tile이어야 한다")
    p.add_argument("--integ", choices=("pct", "raw"), default="pct",
                   help="pct=타일 내 백분위(대규모 변동 소실), raw=타일 내 표준화")
    a = p.parse_args()
    if a.integ == "raw" and a.fe != "tile":
        p.error("--integ raw 는 --fe tile 과 함께 써야 한다 (타일 간 비교 불가)")
    sys.exit(run(a.year, a.sgg, a.max_dist, a.min_count, a.fe, a.integ))
