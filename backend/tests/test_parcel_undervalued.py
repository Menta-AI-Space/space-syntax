from app.services.parcel_undervalued import (
    MIN_STRATUM_SIZE,
    ParcelRawInput,
    assign_strata,
    compute_parcel_gaps,
    top_undervalued,
)


def _parcel(pnu, adm_cd, zone, structure, price, **kw):
    return ParcelRawInput(
        pnu=pnu,
        adm_cd=adm_cd,
        zone_code=zone,
        structure_score=structure,
        price_per_sqm=price,
        base_year=2026,
        **kw,
    )


def _filler(adm_cd, zone, n, *, start=0, structure=0.5, price=500.0):
    """계층 최소 표본을 채우는 필지들. 구조·가격을 같이 올려 상관을 만들지 않는다."""
    return [
        _parcel(f"{adm_cd}-{zone}-f{i}", adm_cd, zone, structure, price)
        for i in range(start, start + n)
    ]


# ── 기본 동작 ──────────────────────────────────────────────────────────

def test_high_structure_low_price_gets_positive_gap_within_same_zone():
    rows = _filler("11110510", "1", MIN_STRATUM_SIZE) + [
        _parcel("target-cheap", "11110510", "1", structure=0.95, price=100.0),
        _parcel("target-rich", "11110510", "1", structure=0.05, price=900.0),
    ]
    scores = {s.pnu: s for s in compute_parcel_gaps(rows)}
    # 구조 1등 + 가격 꼴찌 → 갭이 가장 크다
    assert scores["target-cheap"].gap_score > 0
    assert scores["target-rich"].gap_score < 0
    assert scores["target-cheap"].gap_score > scores["target-rich"].gap_score


def test_gap_is_structure_percentile_minus_price_percentile():
    rows = [
        _parcel("a", "11110510", "1", 0.9, 100.0),
        _parcel("b", "11110510", "1", 0.5, 500.0),
        _parcel("c", "11110510", "1", 0.1, 900.0),
    ]
    a = {s.pnu: s for s in compute_parcel_gaps(rows)}["a"]
    assert a.structure_percentile == 100.0
    assert a.price_percentile == 0.0
    assert a.gap_score == 100.0


# ── 교란 통제: 이 테스트가 계층화의 존재 이유다 ──────────────────────────

def test_zoning_confounder_does_not_make_all_residential_look_undervalued():
    """상업지역은 간선도로변에 지정돼 구조·가격이 함께 높다.

    용도지역을 섞어 하나의 분포로 비교하면 주거지역 필지는 "구조는 중간인데
    가격은 최하위"가 되어 전부 저평가로 뜬다. 계층을 나누면 주거지역은 주거지역
    안에서만 비교되므로 그 허위 신호가 사라진다.
    """
    commercial = [
        _parcel(f"c{i}", "11110510", "commercial", structure=0.90 + i * 0.001, price=3000.0)
        for i in range(MIN_STRATUM_SIZE)
    ]
    # 주거지역: 구조는 상업지역보다 낮고 가격은 훨씬 낮다. 내부에서는 구조·가격이
    # 같은 방향으로 붙어 있어(비싼 데가 구조도 좋다) 저평가가 아니어야 한다.
    residential = [
        _parcel(f"r{i}", "11110510", "residential", structure=0.40 + i * 0.01, price=400.0 + i * 10)
        for i in range(MIN_STRATUM_SIZE)
    ]

    scores = compute_parcel_gaps(commercial + residential)
    by_pnu = {s.pnu: s for s in scores}

    # 주거지역 필지는 자기 계층에서 비교된다
    assert all(by_pnu[f"r{i}"].stratum_level == "dong_zone" for i in range(MIN_STRATUM_SIZE))
    assert by_pnu["r0"].stratum_key.endswith("|residential")

    # 그리고 주거지역이 통째로 저평가로 뜨지 않는다 — 갭 평균이 0 근처다
    res_gaps = [by_pnu[f"r{i}"].gap_score for i in range(MIN_STRATUM_SIZE)]
    assert abs(sum(res_gaps) / len(res_gaps)) < 1.0

    # 상위 목록에 주거지역이 싹 몰리지도 않는다
    top = top_undervalued(scores, limit=5)
    assert not all(s.zone_code == "residential" for s in top)


def test_percentile_is_measured_inside_the_stratum_not_across_all_parcels():
    """싼 상업지역 필지가 '주거지역보다 비싸다'는 이유로 고평가되지 않는다."""
    commercial = [
        _parcel(f"c{i}", "11110510", "commercial", structure=0.5, price=3000.0)
        for i in range(MIN_STRATUM_SIZE - 1)
    ] + [_parcel("c-cheap", "11110510", "commercial", structure=0.99, price=1000.0)]
    residential = _filler("11110510", "residential", MIN_STRATUM_SIZE, price=300.0)

    by_pnu = {s.pnu: s for s in compute_parcel_gaps(commercial + residential)}
    cheap = by_pnu["c-cheap"]
    # 상업지역 안에서는 가장 싸고 구조가 가장 좋다 → 뚜렷한 저평가
    assert cheap.price_percentile == 0.0
    assert cheap.structure_percentile == 100.0
    assert cheap.gap_score == 100.0


# ── 계층 사다리 ────────────────────────────────────────────────────────

def test_small_dong_zone_falls_back_to_wider_stratum():
    """동×용도지역 표본이 얇으면 시군구×용도지역으로 물러난다."""
    # 같은 시군구(11110) 안 다른 동들에 흩어진 소수 필지들
    rows = []
    for d in range(6):
        rows += [
            _parcel(f"p{d}-{i}", f"1111{d:04d}", "1", 0.5, 500.0)
            for i in range(6)
        ]
    strata = assign_strata(rows)
    levels = {lvl for lvl, _key in strata.values()}
    assert levels == {"sgg_zone"}
    # 시군구 계층 키는 앞 5자리만 쓴다
    assert all(key.startswith("11110|") for _lvl, key in strata.values())


def test_falls_back_to_zone_only_when_even_sgg_is_thin():
    rows = [_parcel(f"p{i}", f"{10000000 + i * 100000}", "9", 0.5, 500.0) for i in range(MIN_STRATUM_SIZE)]
    strata = assign_strata(rows)
    assert {lvl for lvl, _ in strata.values()} == {"zone"}
    assert {key for _, key in strata.values()} == {"9"}


def test_narrowest_stratum_wins_when_large_enough():
    rows = _filler("11110510", "1", MIN_STRATUM_SIZE)
    strata = assign_strata(rows)
    assert {lvl for lvl, _ in strata.values()} == {"dong_zone"}


def test_stratum_size_is_recorded():
    n = MIN_STRATUM_SIZE + 4
    scores = compute_parcel_gaps(_filler("11110510", "1", n))
    assert all(s.stratum_size == n for s in scores)


# ── 제외 규칙 ──────────────────────────────────────────────────────────

def test_parcel_without_structure_is_excluded_not_zeroed():
    rows = _filler("11110510", "1", MIN_STRATUM_SIZE) + [
        _parcel("no-structure", "11110510", "1", None, 500.0)
    ]
    assert "no-structure" not in {s.pnu for s in compute_parcel_gaps(rows)}


def test_parcel_without_land_price_is_excluded():
    rows = _filler("11110510", "1", MIN_STRATUM_SIZE) + [
        _parcel("no-price", "11110510", "1", 0.8, None)
    ]
    assert "no-price" not in {s.pnu for s in compute_parcel_gaps(rows)}


def test_parcel_without_zone_is_excluded():
    """용도지역을 못 붙인 필지는 통제가 불가능하므로 판정하지 않는다."""
    rows = _filler("11110510", "1", MIN_STRATUM_SIZE) + [
        _parcel("no-zone", "11110510", None, 0.8, 100.0)
    ]
    assert "no-zone" not in {s.pnu for s in compute_parcel_gaps(rows)}


def test_unbuildable_parcel_is_excluded():
    """지을 수 없는 땅이 싼 것은 저평가가 아니다 — 가격이 맞는 것이다."""
    rows = _filler("11110510", "1", MIN_STRATUM_SIZE) + [
        _parcel("cannot-build", "11110510", "1", 0.99, 50.0, buildable=False)
    ]
    scores = compute_parcel_gaps(rows)
    assert "cannot-build" not in {s.pnu for s in scores}


def test_empty_input_returns_empty():
    assert compute_parcel_gaps([]) == []


def test_all_unjudgeable_returns_empty():
    rows = [_parcel("a", "11110510", None, None, None)]
    assert compute_parcel_gaps(rows) == []


# ── 상위 목록 ──────────────────────────────────────────────────────────

def test_top_undervalued_excludes_zone_only_stratum_by_default():
    """지역이 섞인 zone 단계 비교는 기본 목록에서 뺀다 (지역 가격차가 갭에 섞인다)."""
    strong = _filler("11110510", "1", MIN_STRATUM_SIZE) + [
        _parcel("strong-signal", "11110510", "1", 0.99, 10.0)
    ]
    thin = [_parcel(f"t{i}", f"2{i:07d}", "7", 0.5, 500.0) for i in range(MIN_STRATUM_SIZE)]
    thin.append(_parcel("thin-but-huge-gap", "29999999", "7", 0.99, 1.0))

    scores = compute_parcel_gaps(strong + thin)
    pnus = {s.pnu for s in top_undervalued(scores, limit=10)}
    assert "strong-signal" in pnus
    assert "thin-but-huge-gap" not in pnus  # zone 단계라 제외

    # 필요하면 열 수 있다
    opened = {s.pnu for s in top_undervalued(scores, limit=10, allowed_levels=("dong_zone", "sgg_zone", "zone"))}
    assert "thin-but-huge-gap" in opened


def test_top_undervalued_is_sorted_descending_by_gap():
    rows = _filler("11110510", "1", MIN_STRATUM_SIZE) + [
        _parcel("mid", "11110510", "1", 0.7, 300.0),
        _parcel("best", "11110510", "1", 0.99, 10.0),
    ]
    top = top_undervalued(compute_parcel_gaps(rows), limit=3)
    assert [s.pnu for s in top][0] == "best"
    assert top == sorted(top, key=lambda s: s.gap_score, reverse=True)
