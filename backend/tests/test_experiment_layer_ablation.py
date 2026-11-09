"""설계행렬이 더미 트랩을 만들지 않는지 본다.

⚠️ 모델 학습 자체는 테스트하지 않는다 (CLAUDE.md). 계수를 고정하면 테스트가 개선을
막는다. 여기서 재는 것은 학습 결과가 아니라 **설계행렬의 구조**다 — 같은 열이 두 번
들어가면 그건 데이터가 바뀌어서가 아니라 코드가 틀린 것이다.

막으려는 사고: 재개발 spec 에서 train 104,859행 중 재개발 구역 필지가 5개뿐이라
``redev_notice_year#결측`` 이 ``redev_zone_class`` 더미의 정확한 여집합이 됐다.
절편까지 셋이 선형종속인데 조건수가 7.9e15 (배정밀도 eps 의 역수 언저리)라
rank-revealing QR 이 full rank 로 통과시켰고, 계수 +3.3e9 두 개가 train 에서
상쇄되고 test 에서 R² 가 -1e23 으로 폭발했다.
"""

from __future__ import annotations

import numpy as np

from tools.experiment_layer_ablation import Panel, fit_design, transform_design


def _panel(numeric: dict[str, list], categorical: dict[str, list],
           levels: dict[str, list[str]]) -> Panel:
    n = len(next(iter(numeric.values())) if numeric else next(iter(categorical.values())))
    return Panel(
        y=np.zeros(n),
        # 연도가 한 값뿐이면 연도추세 열이 상수가 돼 랭크가 깎인다 — 실제 fold 는
        # 여러 연도를 담으므로 픽스처도 그렇게 둔다.
        base_year=np.array([2018 + (i % 5) for i in range(n)]),
        numeric={k: np.array(v, dtype=np.float64) for k, v in numeric.items()},
        categorical={k: np.array(v, dtype=np.int16) for k, v in categorical.items()},
        cat_levels={k: list(v) for k, v in levels.items()},
    )


def _design(panel: Panel, numeric: list[str], categorical: list[str]) -> tuple:
    train = np.arange(len(panel.base_year))
    spec = fit_design(panel, numeric, categorical, train)
    X = transform_design(panel, spec, train, 2018.0)
    return spec, X


def test_missing_flag_that_mirrors_a_category_dummy_is_dropped():
    """결측표시열이 범주 더미의 여집합이면 뺀다 — 재개발 spec 에서 난 실제 사고다."""
    n = 200
    # 5행만 재개발 구역이고, 그 5행에만 고시연도가 있다.
    year = [np.nan] * n
    zone = [0] * n  # 0 = <NA>
    for i in range(5):
        year[i] = 2015.0
        zone[i] = 1
    panel = _panel({"redev_notice_year": year}, {"redev_zone_class": zone},
                   {"redev_zone_class": ["<NA>", "UQ1232"]})

    spec, X = _design(panel, ["redev_notice_year"], ["redev_zone_class"])

    assert "redev_notice_year#결측" not in spec.labels, spec.labels
    assert "redev_zone_class=UQ1232" in spec.labels
    # 절편 + 연도추세 + 중위값열 + 더미 하나. 여집합 열이 빠져 랭크가 산다.
    assert np.linalg.matrix_rank(X) == X.shape[1]
    assert np.linalg.cond(X) < 1e8


def test_identical_binary_columns_are_dropped():
    """두 범주형이 같은 분할을 담고 있으면 한쪽만 남긴다."""
    n = 100
    half = [0] * 50 + [1] * 50
    panel = _panel({}, {"a": half, "b": half},
                   {"a": ["a0", "a1"], "b": ["b0", "b1"]})

    spec, X = _design(panel, [], ["a", "b"])

    assert [lab for lab in spec.labels if lab.startswith(("a=", "b="))] == ["a=a1"]
    assert np.linalg.matrix_rank(X) == X.shape[1]


def test_independent_missing_flag_survives():
    """겹치지 않는 결측표시열은 그대로 둔다 — 무는 게 목적이 아니다."""
    n = 100
    year = [np.nan if i % 3 == 0 else 2015.0 for i in range(n)]
    zone = [1 if i < 40 else 0 for i in range(n)]
    panel = _panel({"redev_notice_year": year}, {"redev_zone_class": zone},
                   {"redev_zone_class": ["<NA>", "UQ1232"]})

    spec, X = _design(panel, ["redev_notice_year"], ["redev_zone_class"])

    assert "redev_notice_year#결측" in spec.labels
    assert "redev_zone_class=UQ1232" in spec.labels
    assert np.linalg.matrix_rank(X) == X.shape[1]


def test_train_and_test_get_the_same_columns():
    """열 목록은 train 이 정하고 test 는 따른다 — 열 수가 어긋나면 matmul 이 터진다."""
    n = 120
    year = [np.nan if i % 4 else 2015.0 for i in range(n)]
    zone = [i % 3 for i in range(n)]
    panel = _panel({"redev_notice_year": year}, {"redev_zone_class": zone},
                   {"redev_zone_class": ["<NA>", "UQ1", "UQ2"]})

    train = np.arange(0, 60)
    test = np.arange(60, n)
    spec = fit_design(panel, ["redev_notice_year"], ["redev_zone_class"], train)
    X_tr = transform_design(panel, spec, train, 2018.0)
    X_te = transform_design(panel, spec, test, 2018.0)

    assert X_tr.shape[1] == X_te.shape[1] == len(spec.labels)
