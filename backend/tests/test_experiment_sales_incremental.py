"""검증 실험 1의 계산부가 스스로를 속이지 않는지 본다.

⚠️ 실측 계수나 R² 값은 고정하지 않는다 (CLAUDE.md). 숫자를 박으면 데이터가 갱신될
때마다 테스트가 깨지고, 그걸 피하려다 개선을 막게 된다. 여기서 재는 것은 **답을
아는 합성 데이터에서 계산이 그 답을 돌려주는가**다.

막으려는 사고 셋:

1. **누수** — 같은 상권의 다른 분기가 train 에 남으면 상권 고유효과를 외운 것을
   성능으로 착각한다. 매출이 상권 단위라 이게 새면 ΔR² 전부가 거짓이 된다.
2. **없는 효과를 찾는 것** — 목을 순수 잡음으로 갈아끼웠는데 홀드아웃 ΔR² 가
   올라가면, 그건 목의 기여가 아니라 변수를 더 넣어서 오른 것이다. 심사에서
   말할 숫자가 바로 이 값이므로 여기가 새면 실험 전체가 무의미하다.
3. **클러스터 보정 누락** — 같은 상권 행이 독립이 아닌데 보정을 빼면 표준오차가
   작아져 t 가 부풀고 "유의하다"가 거짓이 된다.
"""

from __future__ import annotations

import numpy as np

from tools.experiment_sales_incremental import (
    M0_NUMERIC,
    SYNTAX_NUMERIC,
    Rows,
    build_design,
    cluster_se,
    group_kfold,
    run,
    solve_ols,
)

N_TRDAR = 300
PER_TRDAR = 12   # 업종 6 × 분기 4 를 흉내낸 상권당 행 수

# 상권 단위로 한 번만 정해지는 변수들. **목 지표는 여기 속한다** — 상권 폴리곤
# 하나에서 계산하므로 그 상권의 모든 업종·분기 행이 같은 값을 갖는다. 픽스처가
# 이걸 재현하지 않으면 클러스터 보정이 필요 없는 상황이 되어 테스트가 헛돈다.
# (같은 이유로 본 실험은 상권 고정효과를 못 쓴다 — 쓰면 목이 흡수돼 사라진다.
#  그래서 지역 통제를 자치구 단위로 둔다.)
TRDAR_LEVEL = tuple(SYNTAX_NUMERIC) + (
    "ln_area_m2", "ln_entrance_m", "ln_bus_m", "ln_arterial_m",
    "stations_500m", "commercial_share",
)


def _synthetic(seed: int, syntax_is_noise: bool = False) -> Rows:
    """목이 진짜 기여하는(또는 잡음인) 상권 패널을 만든다.

    상권 고유효과를 일부러 크게 넣는다 — 이게 있어야 클러스터 보정과 상권
    홀드아웃이 필요한 상황이 재현된다.
    """
    rng = np.random.default_rng(seed)
    n = N_TRDAR * PER_TRDAR
    groups = np.repeat([f"T{i:04d}" for i in range(N_TRDAR)], PER_TRDAR).astype(object)

    # 업종·분기는 상권과 독립으로 뽑는다. i%6 · i%4 처럼 규칙적으로 깔면 둘이
    # i%12 로 서로를 결정해 더미가 공선이 되고, 랭크 결손이 코드 탓으로 보인다.
    categorical = {
        "svc_cd": np.array([f"CS{i:03d}" for i in rng.integers(0, 6, n)], dtype=object),
        "yyq": np.array([f"2025{i}" for i in rng.integers(1, 5, n)], dtype=object),
        "signgu_nm": np.repeat(
            [f"GU{i}" for i in rng.integers(0, 12, N_TRDAR)], PER_TRDAR
        ).astype(object),
        "trdar_se_nm": np.array(["골목상권"] * n, dtype=object),
    }

    numeric: dict[str, np.ndarray] = {}
    for k in M0_NUMERIC + SYNTAX_NUMERIC:
        if k in TRDAR_LEVEL:
            numeric[k] = np.repeat(rng.normal(size=N_TRDAR), PER_TRDAR)
        else:
            numeric[k] = rng.normal(size=n)

    trdar_effect = np.repeat(rng.normal(scale=1.0, size=N_TRDAR), PER_TRDAR)
    y = (
        2.0 * numeric["ln_store_count"]
        + 1.0 * numeric["commercial_share"]
        + trdar_effect
        + rng.normal(scale=0.5, size=n)
    )
    if not syntax_is_noise:
        # integration 만 진짜 효과를 갖는다. choice·road_density 는 0 이어야 한다.
        y = y + 0.30 * numeric["integration_pct"] + 0.20 * numeric["ln_link_density"]
    else:
        for k in SYNTAX_NUMERIC:
            numeric[k] = np.repeat(rng.normal(size=N_TRDAR), PER_TRDAR)

    return Rows(y=y, numeric=numeric, categorical=categorical, groups=groups)


def test_group_kfold_never_splits_a_trdar():
    """상권이 train 과 test 에 동시에 있으면 홀드아웃이 아니다."""
    groups = _synthetic(1).groups
    for test_mask in group_kfold(groups, 5):
        assert set(groups[test_mask]).isdisjoint(set(groups[~test_mask]))


def test_recovers_planted_syntax_effect():
    """심어둔 목 계수를 통제변수 틈에서 되찾는가.

    ⚠️ 허용오차를 상수로 박지 않고 **그 실험이 보고한 클러스터 SE 의 3배**로 둔다.
    목 지표는 상권 단위라 유효 표본이 행 수(3,600)가 아니라 **클러스터 수(300)** 이고,
    상권 고유효과 sd 1.0 이면 SE 가 1.0/√300 ≈ 0.058 이다. 0.05 같은 상수를 쓰면
    참값을 맞혀도 1 SE 어긋난 것만으로 빨간불이 켜진다 — 실제로 그렇게 깨졌다.
    이 3 SE 구간을 벗어나면 그때는 잡음이 아니라 추정이 편향된 것이다.
    """
    res = run(_synthetic(2), folds=5)
    coefs = res["syntax_coefs_in_M1"]
    for name, truth in (("integration_pct", 0.30), ("ln_link_density", 0.20),
                        # 효과를 안 심은 변수. 0 을 되돌려줘야 한다 —
                        # 여기가 크면 통제가 새서 없는 신호를 만든 것이다.
                        ("ln_road_density", 0.0), ("choice_pct", 0.0)):
        c = coefs[name]
        assert abs(c["coef"] - truth) < 3 * c["cluster_se"], (name, c, truth)
    assert res["stage2"]["delta_r2_cv"] > 0.01


def test_placebo_syntax_does_not_raise_holdout_r2():
    """목이 순수 잡음이면 홀드아웃 ΔR² 가 0 근처여야 한다.

    표본 내 R² 는 변수를 넣기만 해도 오르므로 여기서는 CV 쪽을 본다. 이게
    올라가면 심사에서 말할 숫자가 "목의 기여"가 아니라 자유도의 부산물이다.
    """
    res = run(_synthetic(3, syntax_is_noise=True), folds=5)
    # **위쪽만 막는다.** 잡음 변수를 넣어 홀드아웃 R² 가 *떨어지는* 것은 정상이고
    # 오히려 옳은 방향이다(쓸모없는 자유도를 쓴 대가). 양쪽을 다 막으면 정상 동작이
    # 실패로 잡힌다 — 시드 6 에서 실제로 −1.2%p 가 나와 그렇게 깨졌다.
    assert res["stage2"]["delta_r2_cv"] < 0.005


def test_cluster_se_exceeds_naive_se_under_within_trdar_correlation():
    """상권 내 상관이 있을 때 클러스터 SE 가 순진한 SE 보다 커야 한다."""
    rows = _synthetic(4)
    X, names, _ = build_design(rows, M0_NUMERIC + SYNTAX_NUMERIC, ["svc_cd", "yyq"])
    beta = solve_ols(X, rows.y)
    resid = rows.y - X @ beta
    n, k = X.shape
    naive = np.sqrt(
        np.diag(np.linalg.pinv(X.T @ X)) * float((resid**2).sum()) / (n - k)
    )
    clustered = cluster_se(X, rows.y, beta, rows.groups)
    j = names.index("integration_pct")
    assert clustered[j] > naive[j]


def test_design_matrix_drops_one_level_per_dummy():
    """더미 트랩 — 절편과 완전 공선인 열이 생기면 안 된다."""
    rows = _synthetic(5)
    X, names, levels = build_design(rows, ["ln_store_count"], ["svc_cd", "yyq"])
    assert names[0] == "const"
    # 수준 수 − 1 개씩만 들어간다.
    assert X.shape[1] == 1 + 1 + (len(levels["svc_cd"]) - 1) + (len(levels["yyq"]) - 1)
    assert np.linalg.matrix_rank(X) == X.shape[1]
