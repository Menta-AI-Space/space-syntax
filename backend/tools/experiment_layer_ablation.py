"""Layer A를 통제한 뒤 Layer B가 더 설명하는 몫을 잰다 (방법론 A의 근거 실험).

이 실험이 답하는 질문은 "공시지가를 얼마나 잘 맞추나"가 **아니다**. 개별공시지가 =
비교표준지 × 토지가격비준표 배율이고 비준표 항목에 용도지역·도로접면·지목·이용상황이
들어 있다. 그 변수들(Layer A)로 공시지가를 예측하면 모델은 행정 산식을 재현할 뿐이라
R²가 높은 건 성과가 아니다 (docs/DECISIONS_LAND_MODEL.md). 그래서 재는 것은

    ΔR² = R²(Layer A 통제 + Layer B) − R²(Layer A 통제)

하나다.

⚠️ Layer B를 한 덩어리로 보고하지 않는다. 이웃 가격(neighbor_median_*)은 Layer B로
분류돼 있지만 "옆 필지에 매겨진 공시지가"이므로 같은 행정 산식의 다른 통로다. 그걸
합쳐 ΔR²를 말하면 순환성을 우리 기여로 파는 것이 된다. 그래서 하위군을 갈라 각각
M0 위에 따로 올린다. **우리가 주장할 숫자는 접근성+길구조(M1a)의 증분이다.**

⚠️ R² 분해를 "그 변수의 기여"로 인용하지 마라 — 투입 순서에 따라 달라진다. 여기서는
모든 하위군을 M0 **위에 각각 단독으로** 올려 순서를 동일하게 고정했고, 출력에도 그
순서를 함께 적는다.

모형: 로그선형 헤도닉(로그 종속변수, OLS). GBM을 쓰지 않은 건 의존성 문제가 아니라
결정 문서가 한계효과·외삽이 필요한 쪽에 GBM을 금지했기 때문이다. 증분 설명력은
계수 해석이 가능한 선형 모형에서 재야 순서 효과를 통제했다고 말할 수 있다.

평가: cv_folds의 spatial_temporal(h=1) 12 fold. train은 컷 이전 연도, test는 컷 이후
연도이고 공간은 동 홀드아웃 + 500m 격자 버퍼라 옆 필지 답을 보지 않는다.

⚠️ 연도 처리: test 연도(2024~2026)는 train(2018~2022)에 없다. 연도 더미를 쓰면 test에서
전부 0이 되어 수준을 못 맞춘다. 그래서 연도는 **선형 추세항**으로 넣어 외삽 가능하게
했다. M0·M1이 같은 처리를 공유하므로 ΔR² 비교는 공정하다. 수준 성분을 뺀
cross-sectional 설명력도 함께 보고한다(within-year 편차 기준).

Run:
    cd backend && ./.venv/Scripts/python.exe -m tools.experiment_layer_ablation
    ./.venv/Scripts/python.exe -m tools.experiment_layer_ablation --json out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field

import numpy as np

try:  # 저장소 관례 — tools/ 는 backend/ 를 루트로 보고 돈다.
    import psycopg2
except ImportError:  # pragma: no cover
    sys.exit("psycopg2가 없다. backend/.venv 로 실행할 것.")

from dotenv import load_dotenv
from scipy.linalg import lstsq as scipy_lstsq

from tools.build_parcel_panel import (
    GROWTH_COLUMNS,
    LAYER_A_COLUMNS,
    LAYER_B_ACCESS_COLUMNS,
    LAYER_B_NEIGHBOR_COLUMNS,
    LAYER_B_REDEV_COLUMNS,
    LAYER_B_STRUCTURE_COLUMNS,
)

# ── Layer A 안에서 범주형과 수치형을 가른다 ──────────────────────────────────
# build_parcel_panel 의 LAYER_A_COLUMNS 가 정본이고, 여기서는 그 목록을 형에 따라
# 나누기만 한다. 목록이 늘면 아래 assert 가 먼저 터진다.
_A_CATEGORICAL = (
    "zone_group",
    "land_category",
    "use_situation",
    "road_side_name",
    "topograph_height",
    "topograph_form",
)
# zone_name 은 원문이라 zone_group 과 같은 정보를 카디널리티만 높여 담는다 — 뺀다.
_A_DROPPED = ("zone_name",)
_A_NUMERIC_LOG = ("area_sqm",)  # 면적은 로그로 넣는다 (규모 수익 비선형)

# ── Layer B 하위군. 각각 M0 위에 단독으로 올린다 ─────────────────────────────
# as_of 컬럼들은 전 행 상수라 feature 가 아니다 (스냅샷 하나라는 사실의 기록).
_B_DROPPED = (
    "access_as_of",
    "structure_as_of",
    # ⚠️ redev_zone_class 의 one-hot 과 정확히 선형종속이다. in_redev_zone 이 false 인
    # 행이 redev_zone_class 의 "<NA>" 레벨과 정확히 같은 집합이라, 기준 범주를 뺀
    # 나머지 더미들의 합이 in_redev_zone 그 자체가 된다. 랭크가 매번 딱 한 열
    # 부족하게 나오고(실측: 97/98, 101/102, 115/116, 118/119), 선형 모형에서
    # 계수가 발산한다(다른 세션 실측: M1 R² 가 -7.3e21). 조건수 문제가 아니라
    # 구조적 중복이므로 더 좋은 최소자승 드라이버로는 못 고친다 — 열을 뺀다.
    # 정보는 redev_zone_class 쪽이 더 많으므로(구역 분류까지 들고 있다) 이쪽을 뺀다.
    # GBM 은 이 중복에 영향받지 않지만, 세 모형에 같은 feature 를 줘야 비교가 되므로
    # 여기서 한 번에 뺀다.
    "in_redev_zone",
)
_B_CATEGORICAL = ("redev_zone_class",)


def _split_layer_a() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """LAYER_A_COLUMNS 를 (범주형, 수치형) 으로 가른다."""
    cats = tuple(c for c in LAYER_A_COLUMNS if c in _A_CATEGORICAL)
    nums = tuple(
        c for c in LAYER_A_COLUMNS if c not in _A_CATEGORICAL and c not in _A_DROPPED
    )
    unknown = set(LAYER_A_COLUMNS) - set(cats) - set(nums) - set(_A_DROPPED)
    assert not unknown, f"Layer A 에 분류 안 된 컬럼이 있다: {sorted(unknown)}"
    return cats, nums


A_CATEGORICAL, A_NUMERIC = _split_layer_a()

# 하위군 이름 → 그 군의 컬럼들. 보고서의 행 순서이기도 하다.
B_GROUPS: dict[str, tuple[str, ...]] = {
    "접근성": LAYER_B_ACCESS_COLUMNS,
    "길구조": LAYER_B_STRUCTURE_COLUMNS,
    "이웃가격": LAYER_B_NEIGHBOR_COLUMNS,
    "재개발": LAYER_B_REDEV_COLUMNS,
}

# 우리가 주장할 증분. 이웃 가격은 여기 없다 — 그게 이 실험의 요지다.
OUR_GROUPS = ("접근성", "길구조")


def _b_columns(names: tuple[str, ...] | list[str]) -> tuple[list[str], list[str]]:
    """하위군 이름들 → (수치형 컬럼, 범주형 컬럼)."""
    nums: list[str] = []
    cats: list[str] = []
    for name in names:
        for col in B_GROUPS[name]:
            if col in _B_DROPPED:
                continue
            (cats if col in _B_CATEGORICAL else nums).append(col)
    return nums, cats


# ── 데이터 적재 ───────────────────────────────────────────────────────────────

TARGET = "official_price_per_sqm"
FOLD_SCHEME = "spatial_temporal"
FOLD_HORIZON = 1


@dataclass
class Panel:
    """패널 한 덩어리. 컬럼별 배열로 들고 있는다.

    ⚠️ fold 배정은 여기 붙이지 않는다. cv_folds 와 조인해서 읽으면 한 행이 자기가
    속한 fold 수만큼 복제돼 476,786행이 2,423,240행이 되고, 범주형 object 배열까지
    같이 불어나 postgres 가 "out of memory for query result" 로 죽는다. 패널은 한 번만
    읽고, fold 별로 (pnu, base_year) → 행 번호만 따로 받아 온다.
    """

    base_year: np.ndarray
    y: np.ndarray  # log(공시지가)
    key_index: dict[tuple[str, int], int] = field(default_factory=dict)
    numeric: dict[str, np.ndarray] = field(default_factory=dict)
    # ⚠️ 범주형은 문자열이 아니라 **정수 코드**로 들고 있는다. object 배열로 두면
    # 부분집합을 뜰 때마다 파이썬 문자열 포인터 배열을 새로 할당해서, fold 12개 ×
    # 모형 7개 × 범주 수십 개를 돌리는 중 9번째 fold 에서 MemoryError 로 죽었다.
    categorical: dict[str, np.ndarray] = field(default_factory=dict)
    cat_levels: dict[str, list[str]] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.y)


def _feature_names() -> tuple[list[str], list[str]]:
    all_numeric = list(A_NUMERIC) + list(GROWTH_COLUMNS)
    all_categorical = list(A_CATEGORICAL)
    for name in B_GROUPS:
        nums, cats = _b_columns([name])
        all_numeric += nums
        all_categorical += cats
    return all_numeric, all_categorical


def load_panel(dsn: str) -> Panel:
    all_numeric, all_categorical = _feature_names()
    cols = [TARGET] + all_numeric + all_categorical
    select = ", ".join(cols)
    sql = f"""
        SELECT pnu, base_year, {select}
        FROM parcel_panel
        WHERE {TARGET} > 0
        ORDER BY pnu, base_year
    """
    print("[load] parcel_panel 을 한 번 읽는다...")
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    print(f"[load] {len(rows):,} 행")

    n_meta = 2  # pnu, base_year
    base_year = np.array([r[1] for r in rows], dtype=np.int32)
    target = np.array([r[n_meta] for r in rows], dtype=np.float64)
    panel = Panel(base_year=base_year, y=np.log(target))
    panel.key_index = {(r[0], r[1]): i for i, r in enumerate(rows)}

    off = n_meta + 1
    for i, name in enumerate(all_numeric):
        panel.numeric[name] = np.array(
            [np.nan if r[off + i] is None else float(r[off + i]) for r in rows],
            dtype=np.float64,
        )
    off += len(all_numeric)
    for i, name in enumerate(all_categorical):
        raw = ["<NA>" if r[off + i] is None else str(r[off + i]) for r in rows]
        levels = sorted(set(raw))
        code_of = {lvl: c for c, lvl in enumerate(levels)}
        panel.cat_levels[name] = levels
        panel.categorical[name] = np.fromiter(
            (code_of[v] for v in raw), dtype=np.int32, count=len(raw)
        )
    return panel


def load_fold_ids(dsn: str) -> list[str]:
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT fold_id FROM cv_folds WHERE scheme=%s AND horizon=%s"
            " ORDER BY fold_id",
            (FOLD_SCHEME, FOLD_HORIZON),
        )
        return [r[0] for r in cur.fetchall()]


def load_fold(
    dsn: str, panel: Panel, fold_id: str
) -> tuple[np.ndarray, np.ndarray]:
    """fold 하나의 (train 행번호, test 행번호). 패널에 없는 키는 버린다."""
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pnu, base_year, split_role FROM cv_folds"
            " WHERE scheme=%s AND horizon=%s AND fold_id=%s",
            (FOLD_SCHEME, FOLD_HORIZON, fold_id),
        )
        train: list[int] = []
        test: list[int] = []
        for pnu, year, role in cur:
            i = panel.key_index.get((pnu, year))
            if i is None:
                continue
            (train if role == "train" else test).append(i)
    return (
        np.array(sorted(train), dtype=np.int64),
        np.array(sorted(test), dtype=np.int64),
    )


# ── 설계행렬 ─────────────────────────────────────────────────────────────────


def _as_index(rows: np.ndarray) -> np.ndarray:
    """boolean mask 든 인덱스 배열이든 인덱스 배열로 통일한다.

    mask 를 그대로 두고 ``len(mask)`` 를 행 수로 쓰면 전체 행 수가 나온다 —
    설계행렬의 절편 열만 2,423,240행이 되고 나머지는 fold 크기라 hstack 이 터진다.
    """
    return np.flatnonzero(rows) if rows.dtype == bool else rows


def _logged(panel: Panel, name: str) -> tuple[np.ndarray, str]:
    """수치 컬럼을 (필요하면 로그를 씌워) 돌려준다."""
    col = panel.numeric[name]
    if name in _A_NUMERIC_LOG:
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(col > 0, np.log(col), np.nan), f"log({name})"
    return col, name


@dataclass
class DesignSpec:
    """어떤 열을 만들지의 확정 목록. **train 에서만** 정하고 test 에 그대로 쓴다.

    ⚠️ 이 클래스가 있는 이유: 열 포함 여부를 각 부분집합에서 따로 정하면 (상수열
    제외·결측률 0/1 제외 판정이 train 과 test 에서 갈려서) 설계행렬의 열 수가
    어긋난다. 실제로 train 88열 / test 95열이 나와 matmul 이 터졌다. 열 목록은
    train 이 정하고 test 는 따르기만 한다.
    """

    # (컬럼명, 표시라벨, 중위값, 값열을 낼지, 결측표시열을 낼지)
    numeric: list[tuple[str, str, float, bool, bool]] = field(default_factory=list)
    # (컬럼명, 범주 코드, 범주 원문) — one-hot 한 열.
    categorical: list[tuple[str, int, str]] = field(default_factory=list)

    @property
    def labels(self) -> list[str]:
        out = ["절편", "연도추세"]
        for name, label, _med, emit_value, emit_missing in self.numeric:
            if emit_value:
                out.append(label)
            if emit_missing:
                out.append(f"{name}#결측")
        out += [f"{name}={level}" for name, _code, level in self.categorical]
        return out


def _binary_key(pattern: np.ndarray) -> bytes:
    """0/1 열의 내용을 해시 키로 만든다 — 열끼리 같은지만 상수 시간에 보려는 것이다."""
    return hashlib.blake2b(np.packbits(pattern).tobytes(), digest_size=16).digest()


def fit_design(
    panel: Panel, numeric: list[str], categorical: list[str], train: np.ndarray
) -> DesignSpec:
    """train 만 보고 설계행렬의 열 목록을 확정한다.

    결측을 0으로 채우지 않는다. 중위값(train 기준)으로 채우고 "결측이었다"를 따로 한
    열로 준다 — structure_score 는 22%가 결측이라 채운 값과 진짜 값을 모델이 구분해야
    한다. 상수열과 결측률이 0 또는 1인 열은 랭크만 깎으므로 뺀다.

    ⚠️ 0/1 열끼리 **같거나 서로 여집합인** 것도 뺀다. 절편이 있으므로 여집합은 부호만
    뒤집은 같은 열이고, 둘이 함께 들어가면 더미 트랩이다. 실측: 재개발 spec 에서
    train 104,859행 중 재개발 구역 필지가 5개뿐이라 ``redev_notice_year#결측`` 이
    ``redev_zone_class=UQ1232`` 의 정확한 여집합이 됐고, 두 계수가 +3.3e9 로 붙어
    train 에서 상쇄되고 test 에서 R² 가 -1e23 으로 폭발했다. 조건수가 7.9e15 —
    배정밀도 eps 의 역수 언저리라 rank-revealing QR 도 full rank 로 봤다.

    앞서 같은 이유로 ``in_redev_zone`` 을 목록에서 뺐지만(``_B_DROPPED``) 결측표시열이
    그 열을 되살렸다. 열 이름을 하나씩 빼는 방식으로는 이 함정을 막을 수 없어서,
    여기서 **내용으로** 판정한다. 범주 더미를 먼저 잡고 결측표시열을 그 뒤에 재는
    이유는, 겹칠 때 범주 쪽이 레벨 이름을 들고 있어 계수를 읽을 수 있어서다.
    """
    idx = _as_index(train)
    spec = DesignSpec()

    seen: dict[bytes, str] = {}

    def claim(pattern: np.ndarray, label: str) -> bool:
        """이 0/1 열을 쓸 수 있으면 등록하고 True. 이미 있는 열이면 False."""
        for key in (_binary_key(pattern), _binary_key(~pattern)):
            if key in seen:
                print(f"       ⚠️ 중복 열 제외: {label} ≡ {seen[key]}")
                return False
        seen[_binary_key(pattern)] = label
        return True

    for name in categorical:
        col_tr = panel.categorical[name][idx]
        # np.unique 로 뽑는다. set(col_tr.tolist()) 은 20만 개짜리 파이썬 리스트를
        # 새로 만들고, fold 10번째쯤에서 MemoryError 로 죽었다(실측).
        cats = sorted(np.unique(col_tr).tolist())
        if len(cats) < 2:
            continue
        levels = panel.cat_levels[name]
        for cat in cats[1:]:  # 첫 범주 = 기준
            share = float((col_tr == cat).mean())
            if 0.0 < share < 1.0 and claim(col_tr == cat, f"{name}={levels[cat]}"):
                # 코드와 함께 원문도 넣는다 — 계수를 사람이 읽을 때 필요하다.
                spec.categorical.append((name, cat, levels[cat]))

    for name in numeric:
        col, label = _logged(panel, name)
        col_tr = col[idx]
        observed = ~np.isnan(col_tr)
        med = float(np.nanmedian(col_tr)) if observed.any() else 0.0
        if np.isnan(med):
            med = 0.0
        filled_tr = np.where(~observed, med, col_tr)
        emit_value = bool(np.ptp(filled_tr) > 0)
        miss_rate = float((~observed).mean())
        emit_missing = bool(0.0 < miss_rate < 1.0) and claim(
            ~observed, f"{name}#결측"
        )
        if emit_value or emit_missing:
            spec.numeric.append((name, label, med, emit_value, emit_missing))

    return spec


def transform_design(
    panel: Panel, spec: DesignSpec, rows: np.ndarray, year_origin: float
) -> np.ndarray:
    """확정된 spec 으로 설계행렬을 만든다. train·test 가 같은 열을 갖는다.

    train 에 없던 범주는 전부 0이 되어 기준 범주로 취급된다 — test 에서 처음 보는
    용도지역을 아는 척하지 않는다.
    """
    idx = _as_index(rows)
    n = len(idx)
    # ⚠️ 열을 리스트에 모아 hstack 하지 않는다. 22만 행 × 90열이면 조각들과 결과가
    # 동시에 살아 있어 메모리를 두 배로 쓰고, 실제로 fold 7 에서 1 MiB 할당에
    # 실패했다. 최종 크기를 먼저 잡고 그 자리에 직접 채운다.
    n_cols = 2 + sum(  # 절편 + 연도추세
        int(emit_value) + int(emit_missing)
        for _n, _l, _m, emit_value, emit_missing in spec.numeric
    ) + len(spec.categorical)
    X = np.empty((n, n_cols), dtype=np.float64)
    X[:, 0] = 1.0
    # 연도는 선형 추세다. 더미를 쓰면 test 연도가 train 에 없어 전부 0이 된다.
    X[:, 1] = panel.base_year[idx] - year_origin
    j = 2
    for name, _label, med, emit_value, emit_missing in spec.numeric:
        col, _ = _logged(panel, name)
        col_rows = col[idx]
        missing = np.isnan(col_rows)
        if emit_value:
            X[:, j] = np.where(missing, med, col_rows)
            j += 1
        if emit_missing:
            X[:, j] = missing
            j += 1
    for name, cat, _level in spec.categorical:
        X[:, j] = panel.categorical[name][idx] == cat
        j += 1
    assert j == n_cols, f"열 수가 안 맞는다: {j} != {n_cols}"
    return X


# ── 적합·평가 ────────────────────────────────────────────────────────────────


def _r2(y: np.ndarray, pred: np.ndarray) -> float:
    sse = float(np.sum((y - pred) ** 2))
    sst = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - sse / sst if sst > 0 else float("nan")


def _r2_within_year(
    y: np.ndarray, pred: np.ndarray, years: np.ndarray
) -> float:
    """연도 수준을 양쪽에서 뺀 뒤의 R² (cross-sectional 설명력).

    "같은 해 필지들 사이의 값 차이를 얼마나 설명하나"다. test 연도 평균을 쓰므로
    순수 out-of-sample 이 아니다 — 보조 지표로만 읽어야 하고 그래서 이름을 따로 뒀다.
    """
    yc = y.astype(np.float64).copy()
    pc = pred.astype(np.float64).copy()
    for yr in np.unique(years):
        m = years == yr
        yc[m] -= yc[m].mean()
        pc[m] -= pc[m].mean()
    return _r2(yc, pc)


def _standardize(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """절편을 뺀 모든 열의 (평균, 표준편차). **train 에서만** 구한다.

    표준화는 계수의 뜻을 바꾸지만 R² 는 바꾸지 않는다. 넣는 이유는 순전히 조건수다 —
    log(면적)·거리(미터, 수천)·one-hot(0/1)·연도추세가 한 행렬에 섞이면 XᵀX 의
    조건수가 커져 해가 흔들린다.
    """
    mu = X[:, 1:].mean(axis=0)
    sd = X[:, 1:].std(axis=0)
    sd[sd == 0] = 1.0  # 상수열이 남아도 0으로 나누지 않는다
    return mu, sd


def _apply_standardize(X: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> None:
    """제자리에서 표준화한다 (복사본을 더 만들지 않는다)."""
    X[:, 1:] -= mu
    X[:, 1:] /= sd


def _solve_ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """랭크에 안전한 최소제곱해.

    ⚠️ 정규방정식(``np.linalg.solve(XᵀX, Xᵀy)``)을 쓰면 안 된다. 메모리는 아끼지만
    ``solve`` 는 **거의** 특이한 행렬에 예외를 던지지 않고 쓰레기를 돌려주고, 그래서
    ``except LinAlgError`` 폴백이 걸리지 않는다. 실측으로 fold 3 의 R² 가
    -235,732 로, Layer B 전체 모형은 -1.3e31 로 폭발했다. 조건수가 나쁜 건
    one-hot 수십 개 + 미터 단위 거리 + 결측표시열이 한 행렬에 있기 때문이다.

    gelsy(rank-revealing QR)는 SVD 보다 싸고 랭크 부족을 정상적으로 처리한다.
    랭크가 열 수보다 작으면 그 사실을 돌려줘 호출자가 로그에 남길 수 있게 한다.

    ⚠️ ``cond`` 를 명시한다. 기본값은 머신 정밀도(≈2.2e-16)라 조건수가 그 역수
    언저리인 행렬을 **full rank 로 통과시킨다.** 실측: 재개발 spec 의 조건수가
    7.9e15 로 나왔고 rank 는 97/97 이었는데 계수는 +3.3e9 였다 — train 에서 서로
    상쇄되고 test 에서 R² 가 -1e23 이 됐다. 1e-10 은 "이 방향은 데이터가 사실상
    말해 주지 않는다"는 선이고, 거기서 자르면 계수가 유한하게 남는다.

    설계 쪽에서 중복 열을 이미 뺐으므로(``fit_design``) 여기 걸리는 일은 없어야
    한다. 그래도 두는 이유는, 새 feature 가 들어올 때 조용히 폭발하는 대신
    랭크 부족으로 **보이게** 하기 위해서다.
    """
    beta, _res, rank, _sv = scipy_lstsq(X, y, cond=1e-10, lapack_driver="gelsy")
    return beta, int(rank)


@dataclass
class FoldResult:
    fold_id: str
    n_train: int
    n_test: int
    r2: dict[str, float]
    r2_within: dict[str, float]


def run_fold(
    panel: Panel,
    fold: str,
    train: np.ndarray,
    test: np.ndarray,
    specs: dict[str, tuple[list[str], list[str]]],
) -> FoldResult:
    year_origin = float(panel.base_year[train].min())
    y_tr, y_te = panel.y[train], panel.y[test]
    years_te = panel.base_year[test]

    r2: dict[str, float] = {}
    r2_within: dict[str, float] = {}
    rank_short: dict[str, tuple[int, int]] = {}
    for label, (numeric, categorical) in specs.items():
        # 열 목록은 train 이 정하고 test 는 따른다 — 각자 정하면 열 수가 어긋난다.
        spec = fit_design(panel, numeric, categorical, train)
        X_tr = transform_design(panel, spec, train, year_origin)
        # 표준화 통계도 train 에서만 구해 test 에 그대로 쓴다.
        mu, sd = _standardize(X_tr)
        _apply_standardize(X_tr, mu, sd)
        n_cols = X_tr.shape[1]
        beta, rank = _solve_ols(X_tr, y_tr)
        del X_tr  # 22만×90 을 test 행렬과 동시에 들고 있을 이유가 없다.
        if rank < n_cols:
            rank_short[label] = (rank, n_cols)
        X_te = transform_design(panel, spec, test, year_origin)
        _apply_standardize(X_te, mu, sd)
        pred = X_te @ beta
        del X_te
        r2[label] = _r2(y_te, pred)
        r2_within[label] = _r2_within_year(y_te, pred, years_te)
    if rank_short:
        # 랭크 부족을 조용히 넘기지 않는다 — 어느 모형이 몇 열 모자랐는지 남긴다.
        detail = ", ".join(f"{k} {r}/{c}" for k, (r, c) in rank_short.items())
        print(f"       ⚠️ 랭크 부족: {detail}")
    return FoldResult(
        fold_id=fold,
        n_train=int(len(train)),
        n_test=int(len(test)),
        r2=r2,
        r2_within=r2_within,
    )


def build_specs() -> dict[str, tuple[list[str], list[str]]]:
    """모형 이름 → (수치 컬럼, 범주 컬럼). M0 가 모든 M1* 의 부분집합이다."""
    m0_num = list(A_NUMERIC) + list(GROWTH_COLUMNS)
    m0_cat = list(A_CATEGORICAL)
    specs: dict[str, tuple[list[str], list[str]]] = {"M0": (m0_num, m0_cat)}

    for name in B_GROUPS:
        nums, cats = _b_columns([name])
        specs[f"M0+{name}"] = (m0_num + nums, m0_cat + cats)

    our_nums, our_cats = _b_columns(list(OUR_GROUPS))
    specs["M1a(접근성+길구조)"] = (m0_num + our_nums, m0_cat + our_cats)

    all_nums, all_cats = _b_columns(list(B_GROUPS))
    specs["M1(LayerB 전체)"] = (m0_num + all_nums, m0_cat + all_cats)
    return specs


def summarize(results: list[FoldResult], specs: dict) -> dict:
    labels = list(specs)
    out: dict = {"folds": len(results), "models": {}}
    for label in labels:
        vals = np.array([r.r2[label] for r in results])
        wvals = np.array([r.r2_within[label] for r in results])
        out["models"][label] = {
            "r2_mean": float(vals.mean()),
            "r2_sd": float(vals.std(ddof=1)),
            "r2_min": float(vals.min()),
            "r2_max": float(vals.max()),
            "r2_within_mean": float(wvals.mean()),
        }
    base = np.array([r.r2["M0"] for r in results])
    base_w = np.array([r.r2_within["M0"] for r in results])
    out["increments"] = {}
    for label in labels:
        if label == "M0":
            continue
        d = np.array([r.r2[label] for r in results]) - base
        dw = np.array([r.r2_within[label] for r in results]) - base_w
        out["increments"][label] = {
            "delta_r2_mean": float(d.mean()),
            "delta_r2_sd": float(d.std(ddof=1)),
            "delta_r2_min": float(d.min()),
            "delta_r2_max": float(d.max()),
            "folds_positive": int((d > 0).sum()),
            "delta_r2_within_mean": float(dw.mean()),
        }
    return out


def resolve_dsn() -> str | None:
    """실험이 붙을 DB. ``EXPERIMENT_DATABASE_URL`` 이 있으면 그쪽이 이긴다.

    실험 표(``parcel_panel``·``cv_folds``)를 Railway 로 옮긴 뒤로, 학습은 로컬
    11.3GB DB 가 아니라 그쪽에서 돈다 — 로컬은 디스크도 RAM 도 모자라서 CatBoost 가
    ``bad allocation`` 으로, 패널 로딩이 ``MemoryError`` 로 죽었다.

    ``DATABASE_URL`` 을 그대로 두는 이유는 로컬 백엔드(``app/``)가 그걸 쓰기
    때문이다. 실험용 주소를 거기 덮어쓰면 백엔드가 실험 DB 를 보게 되는데, 그쪽엔
    두 표밖에 없다.
    """
    return os.getenv("EXPERIMENT_DATABASE_URL") or os.getenv("DATABASE_URL")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", help="요약을 이 경로에 JSON 으로 쓴다")
    args = ap.parse_args()

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return int(bool(sys.stderr.write("DATABASE_URL 이 없다\n")))

    panel = load_panel(dsn)
    specs = build_specs()

    folds = load_fold_ids(dsn)
    print(f"[run] fold {len(folds)}개 × 모형 {len(specs)}개")
    results: list[FoldResult] = []
    for i, fold in enumerate(folds, 1):
        train, test = load_fold(dsn, panel, fold)
        if len(train) == 0 or len(test) == 0:
            print(f"  [{i:2d}/{len(folds)}] {fold:36s} 건너뜀 (train/test 비었다)")
            continue
        res = run_fold(panel, fold, train, test, specs)
        results.append(res)
        print(
            f"  [{i:2d}/{len(folds)}] {fold:36s} "
            f"train={res.n_train:>7,} test={res.n_test:>6,} "
            f"M0={res.r2['M0']:.4f} "
            f"M1a={res.r2['M1a(접근성+길구조)']:.4f} "
            f"M1={res.r2['M1(LayerB 전체)']:.4f}"
        )

    summary = summarize(results, specs)
    n_rows = len(panel)

    print()
    print("=" * 78)
    print("모형별 out-of-fold R²  (log 개별공시지가, spatial_temporal h=1, 12 fold)")
    print("=" * 78)
    print(f"{'모형':24s} {'R² 평균':>9s} {'sd':>7s} {'최소':>7s} {'최대':>7s} {'within':>8s}")
    for label, m in summary["models"].items():
        print(
            f"{label:24s} {m['r2_mean']:>9.4f} {m['r2_sd']:>7.4f} "
            f"{m['r2_min']:>7.4f} {m['r2_max']:>7.4f} {m['r2_within_mean']:>8.4f}"
        )

    print()
    print("=" * 78)
    print("증분 설명력 ΔR²  — 전부 M0 **위에 각각 단독으로** 올린 것이다 (같은 순서)")
    print("=" * 78)
    print(f"{'모형':24s} {'ΔR²':>9s} {'sd':>7s} {'양수 fold':>10s} {'Δwithin':>9s}")
    for label, m in summary["increments"].items():
        print(
            f"{label:24s} {m['delta_r2_mean']:>9.4f} {m['delta_r2_sd']:>7.4f} "
            f"{m['folds_positive']:>7d}/12 {m['delta_r2_within_mean']:>9.4f}"
        )

    ours = summary["increments"]["M1a(접근성+길구조)"]
    print()
    print("-" * 78)
    print("우리가 말할 숫자:")
    print(
        f"  Layer A(행정 산식이 이미 쓰는 변수) 통제 후, 접근성+길 구조의 증분 설명력 "
        f"ΔR² = {ours['delta_r2_mean']:.4f}"
        f"  (fold 간 sd {ours['delta_r2_sd']:.4f}, 12개 중 {ours['folds_positive']}개 fold 양수)"
    )
    print(f"  M0 R² = {summary['models']['M0']['r2_mean']:.4f}"
          f" → M1a R² = {summary['models']['M1a(접근성+길구조)']['r2_mean']:.4f}")
    print()
    print("  ⚠️ '이웃가격'의 증분은 우리 기여로 인용하지 않는다 — 옆 필지에 매겨진")
    print("     공시지가는 같은 행정 산식의 다른 통로다.")
    print("  ⚠️ M0 의 R² 자체를 성과로 인용하지 않는다 — 비준표 변수로 비준표 결과를")
    print("     맞춘 것이므로 높은 게 당연하다.")
    print("-" * 78)

    if args.json:
        payload = {
            "n_rows": n_rows,
            "n_folds": len(folds),
            "scheme": f"{FOLD_SCHEME} h={FOLD_HORIZON}",
            "summary": summary,
            "per_fold": [
                {"fold_id": r.fold_id, "n_train": r.n_train, "n_test": r.n_test,
                 "r2": r.r2, "r2_within": r.r2_within}
                for r in results
            ],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[out] {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
