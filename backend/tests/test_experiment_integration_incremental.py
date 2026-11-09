"""Unit tests for the numeric core of tools/experiment_integration_incremental.py.

This experiment decides whether the land-price direction is viable at all, so a
bug in the OLS / clustered-SE / dummy code would send the whole project the
wrong way. The DB query is not covered here (it needs PostGIS); the estimator is.
"""

from __future__ import annotations

import numpy as np
import pytest

from tools.experiment_integration_incremental import (
    _cluster_se,
    _demean,
    _dummies,
    _ols,
    _verdict,
)


class TestVerdict:
    """판정 규칙. ΔR²를 합격 기준으로 쓰면 표본 크기가 결론을 뒤집는다.

    실제로 같은 방향·같은 데이터에서 3.4만 ✗ → 7.6만 ○ → 70만 ✗로 뒤집혔다.
    그래서 판정은 계수 부호·크기와 유의성으로만 하고 ΔR²는 참고치로 보고한다.
    """

    # ln 계수 0.0329 ≈ +3.3% (전 서울 스펙 B 실측치)
    SEOUL_COEF = 0.0329

    def test_small_delta_r2_still_passes_when_significant_and_in_range(self):
        """전 서울 실측: ΔR²는 0.0013뿐이지만 통과해야 한다."""
        out = _verdict(self.SEOUL_COEF, 15.9, 0.0013)
        assert "○" in out
        # 작다는 사실은 반드시 함께 알려야 한다
        assert "작다" in out

    def test_large_delta_r2_fails_when_sign_is_wrong(self):
        out = _verdict(-0.05, -8.0, 0.05)
        assert "✗" in out
        assert "부호" in out

    def test_fails_when_not_significant(self):
        """첫 실험(3.4만 표본, t=1.06)은 △로 남아야 한다."""
        out = _verdict(0.066, 1.06, 0.0002)
        assert "△" in out

    def test_flags_coefficient_above_literature_ceiling_as_bias(self):
        """문헌 상한(12.7%)을 넘으면 성능이 아니라 편향 신호다."""
        out = _verdict(0.30, 12.0, 0.05)  # ≈ +35%
        assert "⚠" in out
        assert "편향" in out

    def test_verdict_always_reports_the_three_numbers(self):
        out = _verdict(self.SEOUL_COEF, 15.9, 0.0013)
        assert "+3.3%" in out
        assert "+15.90" in out
        assert "+0.0013" in out


def test_demean_matches_explicit_dummies_frisch_waugh_lovell():
    """within 변환이 더미를 넣은 회귀와 계수·잔차·R²가 같아야 한다.

    전 서울(70만 필지 × 945 타일)에서 더미 행렬이 4.4GiB로 터져 within
    변환으로 바꿨다. 이 등가성이 깨지면 보고한 모든 수치가 틀린다.
    """
    rng = np.random.default_rng(7)
    n_groups, per_group = 12, 40
    groups = np.repeat(np.arange(n_groups), per_group)
    n = n_groups * per_group

    x = rng.normal(size=n)
    fe_effect = np.repeat(rng.normal(scale=2.0, size=n_groups), per_group)
    y = 1.0 + 0.75 * x + fe_effect + rng.normal(scale=0.2, size=n)

    # (1) 명시적 더미 방식
    d_fe, _ = _dummies([str(g) for g in groups], min_count=1)
    X_dummy = np.column_stack([np.ones(n), x, d_fe])
    beta_dummy, resid_dummy, r2_dummy = _ols(y, X_dummy)
    x_coef_dummy = beta_dummy[1]

    # (2) within 변환 방식
    yd = _demean(y, groups)
    xd = _demean(x[:, None], groups)
    beta_w, *_ = np.linalg.lstsq(xd, yd, rcond=None)
    resid_w = yd - xd @ beta_w
    sst = float(((y - y.mean()) ** 2).sum())
    r2_w = 1.0 - float(resid_w @ resid_w) / sst

    assert beta_w[0] == pytest.approx(x_coef_dummy, rel=1e-9)
    assert r2_w == pytest.approx(r2_dummy, abs=1e-9)
    assert np.allclose(np.sort(resid_w), np.sort(resid_dummy), atol=1e-9)


def test_demean_zeroes_group_means():
    groups = np.array(["a", "a", "b", "b", "b"])
    A = np.array([[1.0], [3.0], [10.0], [20.0], [30.0]])
    out = _demean(A, groups)
    assert out[:2, 0].sum() == pytest.approx(0.0)
    assert out[2:, 0].sum() == pytest.approx(0.0)
    # 원본은 건드리지 않는다
    assert A[0, 0] == 1.0


def test_demean_handles_unsorted_groups():
    groups = np.array(["b", "a", "b", "a"])
    A = np.array([[10.0], [1.0], [20.0], [3.0]])
    out = _demean(A, groups)
    assert out[0, 0] == pytest.approx(-5.0)  # b 평균 15
    assert out[2, 0] == pytest.approx(+5.0)
    assert out[1, 0] == pytest.approx(-1.0)  # a 평균 2
    assert out[3, 0] == pytest.approx(+1.0)


def test_cluster_se_extra_params_widens_se():
    """흡수된 고정효과를 자유도에 세면 표준오차가 커져야 한다."""
    rng = np.random.default_rng(11)
    n = 300
    x = rng.normal(size=n)
    y = 0.4 * x + rng.normal(scale=0.5, size=n)
    X = x[:, None]
    _, resid, _ = _ols(y, X)
    groups = np.repeat(np.arange(15), 20)

    se_plain = _cluster_se(X, resid, groups)
    se_with_fe = _cluster_se(X, resid, groups, extra_params=15)
    assert se_with_fe[0] > se_plain[0]


def test_ols_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    n = 500
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = 3.0 + 2.0 * x1 - 1.5 * x2  # noiseless: must recover exactly
    X = np.column_stack([np.ones(n), x1, x2])

    beta, resid, r2 = _ols(y, X)

    assert beta == pytest.approx([3.0, 2.0, -1.5], abs=1e-9)
    assert np.abs(resid).max() < 1e-9
    assert r2 == pytest.approx(1.0, abs=1e-12)


def test_ols_r2_is_zero_for_intercept_only():
    y = np.array([1.0, 2.0, 3.0, 4.0])
    X = np.ones((4, 1))
    _, _, r2 = _ols(y, X)
    assert r2 == pytest.approx(0.0, abs=1e-12)


def test_cluster_se_shrinks_toward_ols_when_every_row_is_its_own_cluster():
    """With singleton clusters the sandwich reduces to (near) heteroskedastic-robust.

    The point is not exact equality — it is that the SE stays finite and the
    same order of magnitude as the textbook OLS SE, so a coding slip that
    inflates or zeroes it out gets caught.
    """
    rng = np.random.default_rng(1)
    n = 400
    x = rng.normal(size=n)
    y = 1.0 + 0.5 * x + rng.normal(scale=0.3, size=n)
    X = np.column_stack([np.ones(n), x])
    beta, resid, _ = _ols(y, X)

    se_cluster = _cluster_se(X, resid, np.arange(n))

    # Textbook homoskedastic OLS SE for comparison.
    sigma2 = float(resid @ resid) / (n - X.shape[1])
    se_ols = np.sqrt(np.diag(sigma2 * np.linalg.pinv(X.T @ X)))

    assert np.all(np.isfinite(se_cluster))
    assert np.all(se_cluster > 0)
    assert se_cluster[1] == pytest.approx(se_ols[1], rel=0.35)


def test_cluster_se_grows_when_errors_are_correlated_within_cluster():
    """Perfectly correlated within-cluster errors must widen the SE.

    This is the whole reason for clustering: parcels in the same dong share
    shocks, so treating them as independent understates uncertainty.
    """
    rng = np.random.default_rng(2)
    n_groups, per_group = 20, 25
    groups = np.repeat(np.arange(n_groups), per_group)
    n = n_groups * per_group

    x = rng.normal(size=n)
    # One shock per group, shared by every row in it.
    shock = np.repeat(rng.normal(scale=1.0, size=n_groups), per_group)
    y = 1.0 + 0.5 * x + shock
    X = np.column_stack([np.ones(n), x])
    beta, resid, _ = _ols(y, X)

    se_clustered = _cluster_se(X, resid, groups)
    se_naive = _cluster_se(X, resid, np.arange(n))

    # Intercept is where the shared shock bites hardest.
    assert se_clustered[0] > se_naive[0]


def test_dummies_drops_most_common_level_and_folds_rare_ones():
    values = ["a"] * 50 + ["b"] * 40 + ["c"] * 2
    mat, labels = _dummies(values, min_count=10)

    # "a" is the base (dropped); "c" is too rare and folds into (기타).
    assert mat.shape == (92, 2)
    assert all(lab.startswith("a→") for lab in labels)
    assert {lab.split("→")[1] for lab in labels} == {"b", "(기타)"}

    # Base rows are all-zero; every other row has exactly one 1.
    assert mat[:50].sum() == 0
    assert np.all(mat[50:].sum(axis=1) == 1)


def test_dummies_returns_empty_when_only_one_level_survives():
    mat, labels = _dummies(["a"] * 30, min_count=10)
    assert mat.shape == (30, 0)
    assert labels == []


def test_dummies_are_collinear_free_with_intercept():
    """base level dropped => [intercept, dummies] must be full rank."""
    values = ["a"] * 30 + ["b"] * 30 + ["c"] * 30
    mat, _ = _dummies(values, min_count=5)
    X = np.hstack([np.ones((90, 1)), mat])
    assert np.linalg.matrix_rank(X) == X.shape[1]
