from app.routers.industry_profiles import _load_menu
from app.services.industry_profiles import load_all_profiles
from app.services.personal_fit import PRIORITIES, compute_personal_fit
from app.services.site_score import SubScore


def make_subscores(**scores) -> list[SubScore]:
    defaults = {
        "integration": 70.0,
        "choice": 60.0,
        "mean_depth": 55.0,
        "connectivity": 50.0,
        "transit": 80.0,
        "demand": 65.0,
        "competition": 40.0,  # 밀도 표시 점수 (advantage 변환 전)
    }
    defaults.update(scores)
    return [
        SubScore(k, k, v, "", "", "", "") for k, v in defaults.items()
    ]


BASE_WEIGHTS = {
    "integration": 0.22, "choice": 0.12, "mean_depth": 0.10,
    "connectivity": 0.10, "transit": 0.15, "demand": 0.21, "competition": 0.10,
}


def test_boosting_strong_factor_raises_personal_score():
    subs = make_subscores(choice=95.0, integration=90.0)
    fit = compute_personal_fit(
        base_score=70.0, subscores=subs, base_weights=BASE_WEIGHTS,
        priorities=["high_footfall"],
    )
    no_boost = compute_personal_fit(
        base_score=70.0, subscores=subs, base_weights=BASE_WEIGHTS, priorities=[],
    )
    assert fit.personal_score > no_boost.personal_score
    assert fit.base_score == 70.0  # 기본 점수 불변
    assert any(n.applied for n in fit.priorities)
    assert any("유동 통행축" in s for s in fit.strengths)


def test_boosting_weak_factor_lowers_personal_score_and_warns():
    subs = make_subscores(choice=20.0, integration=15.0)
    fit = compute_personal_fit(
        base_score=70.0, subscores=subs, base_weights=BASE_WEIGHTS,
        priorities=["high_footfall"],
    )
    no_boost = compute_personal_fit(
        base_score=70.0, subscores=subs, base_weights=BASE_WEIGHTS, priorities=[],
    )
    assert fit.personal_score < no_boost.personal_score
    assert any("상충" in c for c in fit.cautions)


def test_unavailable_priority_leaves_score_untouched_with_note():
    subs = make_subscores()
    fit = compute_personal_fit(
        base_score=70.0, subscores=subs, base_weights=BASE_WEIGHTS,
        priorities=["low_rent", "parking"],
    )
    no_boost = compute_personal_fit(
        base_score=70.0, subscores=subs, base_weights=BASE_WEIGHTS, priorities=[],
    )
    assert fit.personal_score == no_boost.personal_score
    assert all(not n.applied for n in fit.priorities)
    assert any("임대료" in n.note for n in fit.priorities)


def test_sales_priority_uses_benchmark_ratio():
    subs = make_subscores()
    premium = compute_personal_fit(
        base_score=70.0, subscores=subs, base_weights=BASE_WEIGHTS,
        priorities=["high_sales"], sales_ratio=2.0,
    )
    budget = compute_personal_fit(
        base_score=70.0, subscores=subs, base_weights=BASE_WEIGHTS,
        priorities=["high_sales"], sales_ratio=0.4,
    )
    assert premium.personal_score > budget.personal_score
    no_data = compute_personal_fit(
        base_score=70.0, subscores=subs, base_weights=BASE_WEIGHTS,
        priorities=["high_sales"], sales_ratio=None,
    )
    assert all(not n.applied for n in no_data.priorities)


def test_max_three_priorities():
    subs = make_subscores()
    fit = compute_personal_fit(
        base_score=70.0, subscores=subs, base_weights=BASE_WEIGHTS,
        priorities=["high_footfall", "low_competition", "strong_workers", "delivery"],
    )
    assert len(fit.priorities) == 3


def test_priority_vocabulary_has_labels():
    for key, spec in PRIORITIES.items():
        assert spec["label"], key
        assert isinstance(spec["factors"], list), key


def test_youth_and_foreign_priorities_use_extra_factor_scores():
    fit = compute_personal_fit(
        base_score=70.0,
        subscores=make_subscores(),
        base_weights=BASE_WEIGHTS,
        priorities=["youth", "foreign"],
        extra_factor_scores={"youth": 90.0, "foreign": 80.0},
    )
    assert fit.personal_score > 70.0
    assert all(note.applied for note in fit.priorities)
    assert "youth" in fit.weights_used
    assert "foreign" in fit.weights_used


def test_teen_priority_uses_extra_factor_scores():
    fit = compute_personal_fit(
        base_score=70.0,
        subscores=make_subscores(),
        base_weights=BASE_WEIGHTS,
        priorities=["teen"],
        extra_factor_scores={"teen": 90.0},
    )
    no_boost = compute_personal_fit(
        base_score=70.0,
        subscores=make_subscores(),
        base_weights=BASE_WEIGHTS,
        priorities=[],
        extra_factor_scores={"teen": 90.0},
    )
    assert fit.personal_score > no_boost.personal_score
    assert all(note.applied for note in fit.priorities)
    assert "teen" in fit.weights_used
    # 데이터 없으면 점수 무변화 + 미적용 노트 (youth와 동일 규약)
    no_data = compute_personal_fit(
        base_score=70.0,
        subscores=make_subscores(),
        base_weights=BASE_WEIGHTS,
        priorities=["teen"],
    )
    assert all(not note.applied for note in no_data.priorities)


def test_industry_menu_structure_and_profile_refs():
    menu = _load_menu()
    profiles = set(load_all_profiles().keys())
    assert len(menu["categories"]) == 11
    seen_ids = set()
    for cat in menu["categories"]:
        assert cat["id"] and cat["label"]
        assert cat["items"], cat["id"]
        for item in cat["items"]:
            assert item["id"] not in seen_ids, f"duplicate item id {item['id']}"
            seen_ids.add(item["id"])
            if item["profile"] is not None:
                assert item["profile"] in profiles, (
                    f"{cat['id']}/{item['id']} references unknown profile {item['profile']}"
                )


def test_priority_boost_strength_changes_the_personal_score():
    """강도 1.0이면 기본과 같고, 3.0이면 1.8보다 더 벌어진다 — 슬라이더의 계약."""
    from app.services.personal_fit import compute_personal_fit

    # high_footfall은 choice·integration 요인으로 매핑된다(PRIORITIES 참고).
    kwargs = dict(
        base_score=60.0,
        subscores=[
            type("S", (), {"key": "choice", "score": 95.0})(),
            type("S", (), {"key": "demand", "score": 40.0})(),
        ],
        base_weights={"choice": 0.3, "demand": 0.7},
        priorities=["high_footfall"],
        extra_factor_scores={},
    )
    neutral = compute_personal_fit(**kwargs, priority_boosts={"high_footfall": 1.0})
    default = compute_personal_fit(**kwargs)  # 종전 BOOST(1.8)
    maxed = compute_personal_fit(**kwargs, priority_boosts={"high_footfall": 3.0})

    assert neutral.personal_score <= default.personal_score <= maxed.personal_score
    assert maxed.personal_score > neutral.personal_score


def test_broken_boost_values_are_clamped_not_fatal():
    """프론트 버그로 0이나 100이 와도 점수가 무너지지 않는다 — 경계로 고정."""
    from app.services.personal_fit import compute_personal_fit

    kwargs = dict(
        base_score=60.0,
        subscores=[
            type("S", (), {"key": "choice", "score": 95.0})(),
            type("S", (), {"key": "demand", "score": 40.0})(),
        ],
        base_weights={"choice": 0.3, "demand": 0.7},
        priorities=["high_footfall"],
        extra_factor_scores={},
    )
    zero = compute_personal_fit(**kwargs, priority_boosts={"high_footfall": 0})
    huge = compute_personal_fit(**kwargs, priority_boosts={"high_footfall": 100})
    lo = compute_personal_fit(**kwargs, priority_boosts={"high_footfall": 1.0})
    hi = compute_personal_fit(**kwargs, priority_boosts={"high_footfall": 3.0})
    assert zero.personal_score == lo.personal_score
    assert huge.personal_score == hi.personal_score
