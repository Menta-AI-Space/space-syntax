"""CV 분할 테스트 — 이 저장소에서 가장 중요한 테스트다.

스펙(``docs/SPEC_LAND_AI_METHODOLOGY.md``)이 검증할 성질을 두 개로 못박았다:
**train과 test에 같은 동이 동시에 들어가지 않는가**, **test 연도가 train보다 뒤인가**.
여기가 새면 모든 성능 수치가 거짓이 된다. 그래서 "돌아간다"가 아니라 그 두 성질을
직접 반증하려 드는 테스트를 둔다.
"""

import pytest

from app.services.cv_split import (
    DEFAULT_SALT,
    GRID_SIZE_M,
    SCHEME_RANDOM_SPATIAL,
    Assignment,
    Fold,
    PanelObs,
    assign_spatial_folds,
    check_leakage,
    check_stratum_coverage,
    grid_cell_key,
    missing_train_strata,
    split_spatial_temporal,
    temporal_cuts,
)

YEARS = tuple(range(2018, 2027))   # 실제 데이터와 같은 2018~2026 9개 연도


def _panel(n_dongs=6, parcels_per_dong=8, years=YEARS, sgg="11545", x0=0.0):
    """합성 패널. 동마다 좌표 구역이 다르게 떨어지도록 배치한다.

    동 하나가 격자 여러 개에 걸치고, 인접 동끼리는 경계 격자를 공유하게 만든다 —
    격자 버퍼가 실제로 걸리는지 보려면 그 상황이 필요하다.
    """
    rows = []
    for d in range(n_dongs):
        ldong = f"{sgg}101{d:02d}"[:10]
        for p in range(parcels_per_dong):
            # 동 간격(400m)을 격자(500m)보다 좁게 둔다 → 동 하나가 격자 두 개에
            # 걸치고 인접 동끼리 경계 격자를 공유한다. 실제 서울이 그런 모양이다.
            span = GRID_SIZE_M * 0.8
            x = x0 + d * span + p * (span / parcels_per_dong)
            y = 0.0
            for year in years:
                rows.append(
                    PanelObs(
                        pnu=f"{ldong}1{d:02d}{p:04d}",
                        base_year=year,
                        ldong_code=ldong,
                        x=x,
                        y=y,
                    )
                )
    return rows


def _two_districts(small_dongs=3, big_dongs=12):
    """구 두 개. 한쪽은 동이 적다 — 실제 금천구(3동) × 강남구(14동)와 같은 모양이다.

    이 비대칭이 층화가 필요한 이유다: 순수 해시 배정은 동이 적은 구를 한 fold가
    통째로 홀드아웃해 버린다.
    """
    return (
        _panel(n_dongs=small_dongs, sgg="11545", x0=0.0)
        + _panel(n_dongs=big_dongs, sgg="11680", x0=100_000.0)
    )


# ── 성질 1: 같은 동이 train·test에 동시에 들어가지 않는다 ────────────────

def test_no_dong_appears_in_both_train_and_test():
    rows = _panel()
    folds = split_spatial_temporal(rows, horizon=1, n_spatial_folds=3)
    assert folds
    for fold in folds:
        train_dongs = {a.spatial_block for a in fold.train}
        test_dongs = {a.spatial_block for a in fold.test}
        assert not (train_dongs & test_dongs), (
            f"{fold.fold_id}: 같은 동이 양쪽에 있다 {train_dongs & test_dongs}"
        )


def test_no_500m_grid_cell_appears_in_both_train_and_test():
    """동 경계를 걸친 격자가 남으면 20m 떨어진 이웃이 train에 있게 된다."""
    rows = _panel()
    folds = split_spatial_temporal(rows, horizon=1, n_spatial_folds=3)
    for fold in folds:
        report = check_leakage(fold, rows)
        assert report.shared_grid_cells == 0, f"{fold.fold_id}: 격자 {report.shared_grid_cells}개 겹침"


def test_grid_buffer_actually_drops_rows():
    """버퍼가 0건이면 장치가 죽어 있는 것이다 — 합성 데이터에서도 걸려야 한다."""
    rows = _panel()
    folds = split_spatial_temporal(rows, horizon=1, n_spatial_folds=3)
    assert any(f.dropped.get("grid_buffer", 0) > 0 for f in folds)


def test_buffer_off_lets_boundary_cells_leak():
    """버퍼를 끄면 실제로 격자가 겹친다 — 위 테스트가 우연이 아님을 보인다."""
    rows = _panel()
    folds = split_spatial_temporal(rows, horizon=1, n_spatial_folds=3, buffer_grid=False)
    assert any(check_leakage(f, rows).shared_grid_cells > 0 for f in folds)


# ── 성질 2: test 연도가 train 연도보다 뒤다 ──────────────────────────────

@pytest.mark.parametrize("horizon", [0, 1, 3, 5])
def test_all_test_years_are_after_all_train_years(horizon):
    rows = _panel()
    folds = split_spatial_temporal(rows, horizon=horizon, n_spatial_folds=3)
    assert folds, f"horizon={horizon}에서 fold가 하나도 안 나왔다"
    for fold in folds:
        train_years = {a.base_year for a in fold.train}
        test_years = {a.base_year for a in fold.test}
        assert train_years and test_years
        assert max(train_years) < min(test_years), (
            f"{fold.fold_id}: train {max(train_years)} >= test {min(test_years)}"
        )


@pytest.mark.parametrize("horizon", [1, 3, 5])
def test_train_label_year_never_crosses_the_cut(horizon):
    """정답 연도(base_year + horizon)까지 절단선 안에 있어야 한다.

    이걸 안 지키면 t만 보고 "과거니까 train"이라 넣은 행의 **정답이 미래 값**이 된다.
    horizon을 헷갈리면 정확히 여기서 샌다.
    """
    rows = _panel()
    for fold in split_spatial_temporal(rows, horizon=horizon, n_spatial_folds=3):
        for a in fold.train:
            assert a.base_year + horizon <= fold.temporal_cut
        for a in fold.test:
            assert a.base_year > fold.temporal_cut


def test_embargo_grows_with_horizon():
    """horizon이 길면 버리는 연도 구간이 넓어진다 (h=0이면 없다)."""
    rows = _panel()
    def embargo(h):
        folds = split_spatial_temporal(rows, horizon=h, n_spatial_folds=3, max_temporal_cuts=1)
        return sum(f.dropped.get("embargo_year", 0) for f in folds)

    assert embargo(0) == 0
    assert embargo(1) > 0
    assert embargo(3) > embargo(1)


def test_temporal_cuts_respects_min_train_years_and_needs_future():
    cuts = temporal_cuts(YEARS, horizon=1, min_train_years=3)
    # h=1이면 cut=2021에서 train 연도가 2018·2019·2020 세 개다 → 여기가 첫 절단선
    assert cuts[0] == 2021
    # 2026 뒤에는 test 연도가 없다
    assert 2026 not in cuts
    # horizon이 길면 첫 절단선이 뒤로 밀린다
    assert temporal_cuts(YEARS, horizon=3, min_train_years=3)[0] == 2023


def test_temporal_cuts_returns_empty_when_years_are_too_few():
    assert temporal_cuts([2025, 2026], horizon=1, min_train_years=3) == []


def test_no_folds_when_panel_is_too_short():
    rows = _panel(years=(2025, 2026))
    assert split_spatial_temporal(rows, horizon=1) == []


# ── 결정론 ──────────────────────────────────────────────────────────────

def test_same_input_gives_same_folds():
    rows = _panel()
    a = split_spatial_temporal(rows, horizon=1, n_spatial_folds=3)
    b = split_spatial_temporal(list(reversed(rows)), horizon=1, n_spatial_folds=3)
    key = lambda folds: [
        (f.fold_id, sorted((x.pnu, x.base_year, x.split_role) for x in f.assignments))
        for f in folds
    ]
    assert key(a) == key(b)


def test_block_assignment_is_stable_and_not_python_hash():
    """파이썬 hash()는 실행마다 바뀐다. 값을 하드코딩해 회귀를 잡는다."""
    blocks = ["1154510100", "1154510200", "1168010100"]
    got = assign_spatial_folds(blocks, n_folds=4, salt=DEFAULT_SALT)
    assert got == assign_spatial_folds(blocks, n_folds=4, salt=DEFAULT_SALT)
    # salt를 바꾸면 배정이 바뀐다 (분할을 의도적으로 갈 수 있어야 한다)
    assert got != assign_spatial_folds(blocks, n_folds=4, salt="other")


def test_grid_cell_key_is_floor_based():
    assert grid_cell_key(0, 0) == grid_cell_key(499, 499)
    assert grid_cell_key(0, 0) != grid_cell_key(500, 0)
    # 음수 좌표에서도 truncation이 아니라 floor여야 한다 (0 주변에서 셀이 두 배로 넓어지면 안 된다)
    assert grid_cell_key(-1, 0) != grid_cell_key(1, 0)


# ── 데모 구역 고정 ───────────────────────────────────────────────────────

def test_pinned_block_is_test_in_every_fold_and_never_train():
    """화면에 보이는 값이 학습에 쓰인 값이면 "예측"이라고 말할 수 없다."""
    rows = _panel()
    demo = rows[0].ldong_code
    folds = split_spatial_temporal(
        rows, horizon=1, n_spatial_folds=3, pinned_test_blocks=(demo,)
    )
    for fold in folds:
        assert demo in fold.test_blocks
        assert all(a.spatial_block != demo for a in fold.train)
    # 고정 블록이 어느 fold에서든 test 행으로 실제로 등장한다
    assert any(any(a.spatial_block == demo for a in f.test) for f in folds)


# ── 시군구 층화: 어느 fold에서도 구가 train에서 사라지지 않는다 ──────────

def test_every_district_stays_in_train_in_every_fold():
    """실사고 고정 — 금천구(동 3개) 전체가 한 fold의 test로 몰려 train에서 사라졌다.

    그 fold는 "강남으로 배워 금천을 맞히기"(구 간 외삽)가 되어 다른 fold와 같은
    것을 재지 않는다. 누출은 아니지만 fold 평균을 오염시킨다.
    """
    rows = _two_districts()
    for fold in split_spatial_temporal(rows, horizon=1, n_spatial_folds=4):
        counts = check_stratum_coverage(fold)
        assert counts.get("train|11545", 0) > 0, f"{fold.fold_id}: 금천 train 0건"
        assert counts.get("train|11680", 0) > 0, f"{fold.fold_id}: 강남 train 0건"
        assert missing_train_strata(fold) == ()


def test_stratification_holds_with_demo_pin_which_shrinks_the_small_district():
    """데모 고정(독산동)이 작은 구의 배정 가능한 동을 하나 더 줄인다 — 실제 조건.

    작은 구를 4동으로 둔 이유: 고정 1개를 빼면 3개가 남고, 이 합성 좌표는 동들이
    서로 격자를 공유할 만큼 촘촘해서(버퍼가 걸리게 일부러 그렇게 만들었다) 배정
    가능한 동이 2개면 버퍼가 남은 train 동을 통째로 지울 수 있다. 실제 금천구는
    가산동·시흥동이 km 단위로 넓어 경계 격자만 깎이지만, **그 상황이 생기면 배치가
    ``missing_train_strata``로 하드 실패한다**(아래 테스트).
    """
    rows = _two_districts(small_dongs=4)
    pinned = rows[0].ldong_code            # 작은 구의 동 하나를 test로 고정
    for fold in split_spatial_temporal(
        rows, horizon=1, n_spatial_folds=4, pinned_test_blocks=(pinned,)
    ):
        assert check_stratum_coverage(fold).get("train|11545", 0) > 0
        assert missing_train_strata(fold) == ()


def test_no_fold_holds_out_more_than_its_share_of_a_district():
    """라운드로빈이니 한 fold가 한 구에서 가져가는 동은 ceil(n/폴드수) 이하다."""
    blocks = [f"11545101{i:02d}" for i in range(3)] + [f"11680101{i:02d}" for i in range(12)]
    stratum = {b: b[:5] for b in blocks}
    assigned = assign_spatial_folds(blocks, n_folds=4, stratum_of=stratum)
    for sgg, total in (("11545", 3), ("11680", 12)):
        for f in range(4):
            held = sum(1 for b, v in assigned.items() if v == f and b.startswith(sgg))
            assert held <= -(-total // 4)


def test_single_block_district_is_warned_about(caplog):
    """동이 1개인 구는 홀드아웃이 불가능하다 — 조용히 넘기면 사고가 재발한다."""
    blocks = ["1130010100"] + [f"11680101{i:02d}" for i in range(8)]
    with caplog.at_level("WARNING"):
        assign_spatial_folds(blocks, n_folds=4, stratum_of={b: b[:5] for b in blocks})
    assert any("11300" in r.getMessage() for r in caplog.records), caplog.text


def test_missing_train_strata_flags_a_hand_made_extrapolation_fold():
    bad = Fold(
        fold_id="bad3", scheme="x", horizon=1, spatial_fold=0, temporal_cut=2022,
        spatial_mode="ldong", train_years=(2018,), test_years=(2023,), test_blocks=("1154510100",),
        assignments=[
            Assignment("1168010100100001", 2018, "train", "1168010100"),
            Assignment("1154510100100001", 2023, "test", "1154510100"),
        ],
    )
    assert missing_train_strata(bad) == ("11545",)


def test_stratified_assignment_is_still_deterministic():
    blocks = [f"11545101{i:02d}" for i in range(3)] + [f"11680101{i:02d}" for i in range(12)]
    stratum = {b: b[:5] for b in blocks}
    a = assign_spatial_folds(blocks, n_folds=4, stratum_of=stratum, salt=DEFAULT_SALT)
    b = assign_spatial_folds(
        list(reversed(blocks)), n_folds=4, stratum_of=stratum, salt=DEFAULT_SALT
    )
    assert a == b
    assert a != assign_spatial_folds(blocks, n_folds=4, stratum_of=stratum, salt="other")


# ── 진단용 무작위 분할 ───────────────────────────────────────────────────

def test_random_scheme_puts_neighbours_on_both_sides():
    """무작위 분할은 이웃 암기를 막지 못한다 — 그래서 비교군으로만 쓴다.

    같은 동(=같은 비교표준지 권역)이 train·test에 함께 들어가는 것이 여기서
    확인되고, 그 상태의 성능과 공간 분할 성능의 차이가 누출의 크기다.
    """
    rows = _panel()
    folds = split_spatial_temporal(
        rows, horizon=1, n_spatial_folds=3, scheme=SCHEME_RANDOM_SPATIAL
    )
    assert folds
    dong_of = {r.pnu: r.ldong_code for r in rows}
    leaked = False
    for fold in folds:
        train_dongs = {dong_of[a.pnu] for a in fold.train}
        test_dongs = {dong_of[a.pnu] for a in fold.test}
        if train_dongs & test_dongs:
            leaked = True
    assert leaked, "무작위 분할인데도 동이 갈렸다 — 비교군 구실을 못 한다"


def test_random_scheme_still_respects_the_time_split():
    """공간만 무작위로 바꾸고 시간은 그대로 둔다 — 그래야 차이가 공간 효과다."""
    rows = _panel()
    for fold in split_spatial_temporal(
        rows, horizon=1, n_spatial_folds=3, scheme=SCHEME_RANDOM_SPATIAL
    ):
        assert max(a.base_year for a in fold.train) < min(a.base_year for a in fold.test)


# ── 진단 함수 자체 ───────────────────────────────────────────────────────

def test_check_leakage_flags_a_hand_made_bad_fold():
    """진단이 실제로 잡는지 본다 — 통과만 하는 진단은 진단이 아니다."""
    bad = Fold(
        fold_id="bad", scheme="x", horizon=1, spatial_fold=0, temporal_cut=2022,
        spatial_mode="ldong", train_years=(2018,), test_years=(2023,), test_blocks=("A",),
        assignments=[
            Assignment("p1", 2018, "train", "A"),
            Assignment("p2", 2023, "test", "A"),
        ],
    )
    report = check_leakage(bad)
    assert report.shared_blocks == ("A",)
    assert not report.ok


def test_check_leakage_flags_time_inversion():
    bad = Fold(
        fold_id="bad2", scheme="x", horizon=1, spatial_fold=0, temporal_cut=2022,
        spatial_mode="ldong", train_years=(2025,), test_years=(2019,), test_blocks=("B",),
        assignments=[
            Assignment("p1", 2025, "train", "A"),
            Assignment("p2", 2019, "test", "B"),
        ],
    )
    assert not check_leakage(bad2 := bad).ok
    assert bad2.fold_id == "bad2"


def test_check_leakage_passes_real_folds():
    rows = _panel()
    for fold in split_spatial_temporal(rows, horizon=1, n_spatial_folds=3):
        assert check_leakage(fold, rows).ok


# ── 방어적 인자 검증 ─────────────────────────────────────────────────────

def test_single_spatial_fold_is_rejected():
    with pytest.raises(ValueError):
        split_spatial_temporal(_panel(), n_spatial_folds=1)


def test_unknown_spatial_mode_is_rejected():
    with pytest.raises(ValueError):
        split_spatial_temporal(_panel(), spatial_mode="dong_ish")


def test_parcel_that_changes_dong_between_years_is_rejected():
    """블록이 흔들리는 필지는 어떤 fold에서 train·test 양쪽에 등장한다.

    블록 집합끼리는 겹치지 않으니 check_leakage로도 안 잡힌다 — 그래서 입구에서 막는다.
    """
    rows = _panel(n_dongs=3, parcels_per_dong=4)
    moved = rows[0]
    rows.append(
        PanelObs(pnu=moved.pnu, base_year=2027, ldong_code="9999999999",
                 x=moved.x, y=moved.y)
    )
    with pytest.raises(ValueError, match="공간 블록"):
        split_spatial_temporal(rows, horizon=1, n_spatial_folds=3)


def test_negative_horizon_is_rejected():
    with pytest.raises(ValueError):
        split_spatial_temporal(_panel(), horizon=-1)


def test_grid_mode_splits_dongs_and_is_diagnostic_only():
    """grid 모드는 스펙 성질(동 무분할)을 만족하지 않는다 — 그래서 기본값이 아니다."""
    rows = _panel()
    folds = split_spatial_temporal(rows, horizon=1, n_spatial_folds=3, spatial_mode="grid")
    dong_of = {r.pnu: r.ldong_code for r in rows}
    assert any(
        {dong_of[a.pnu] for a in f.train} & {dong_of[a.pnu] for a in f.test} for f in folds
    )
    # 다만 격자 자체는 절대 겹치지 않는다 (배정 단위가 격자니까)
    for fold in folds:
        assert check_leakage(fold, rows).shared_grid_cells == 0
