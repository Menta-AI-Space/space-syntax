"""비슷한 상권 찾기 — 유사도가 원인 변수로만 계산되는지 고정한다.

성과(점포당 매출)로 비슷한 곳을 찾으면 "매출이 비슷한 곳은 매출이 비슷하다"는
동어반복이 된다. 구조가 닮은 곳을 찾아 성과를 비교해야 "같은 구조인데 왜
성과가 다른가"라는 진짜 질문이 생긴다.
"""

from app.services.analogs import TrdarFeatures, rank_analogs


def _t(cd, workers, stores, weekend, franchise, per_store):
    return TrdarFeatures(
        trdar_cd=cd, trdar_nm=cd, workers=workers, store_count=stores,
        weekend_share=weekend, franchise_ratio=franchise,
        per_store_monthly_won=per_store,
    )


def test_similarity_ignores_the_outcome():
    """성과가 정반대여도 구조가 같으면 최상위로 나온다."""
    subject = _t("나", workers=500, stores=30, weekend=0.35, franchise=0.05, per_store=500)
    twin_poor = _t("쌍둥이", workers=520, stores=31, weekend=0.36, franchise=0.05, per_store=100)
    rich_alien = _t("남", workers=200_000, stores=300, weekend=0.10, franchise=0.60, per_store=500)

    ranked = rank_analogs(subject, [twin_poor, rich_alien], k=2)
    assert ranked[0][0].trdar_cd == "쌍둥이"
    assert ranked[0][1] > ranked[-1][1]


def test_subject_is_never_its_own_analog():
    subject = _t("나", 500, 30, 0.35, 0.05, 500)
    ranked = rank_analogs(subject, [subject, _t("남", 510, 29, 0.34, 0.06, 700)], k=3)
    assert all(r.trdar_cd != "나" for r, _ in ranked)


def test_missing_causal_data_excludes_a_candidate():
    """원인 변수가 빈 상권은 후보에서 빠진다 — 0으로 메우면 '데이터 없는
    상권'끼리 비슷하다고 나온다."""
    subject = _t("나", 500, 30, 0.35, 0.05, 500)
    hole = _t("구멍", None, 30, 0.35, 0.05, 500)
    ok = _t("정상", 480, 28, 0.33, 0.04, 900)
    ranked = rank_analogs(subject, [hole, ok], k=3)
    assert [r.trdar_cd for r, _ in ranked] == ["정상"]


def test_faraway_structures_are_not_called_similar():
    """2σ보다 먼 것은 '비슷하다'고 부르지 않는다 — 유사도 0은 잘린다."""
    subject = _t("나", 500, 30, 0.35, 0.05, 500)
    candidates = [
        _t("이웃", 510, 31, 0.34, 0.05, 700),
        _t("반대", 300_000, 500, 0.90, 0.95, 700),
        _t("이웃2", 490, 29, 0.36, 0.06, 300),
    ]
    ranked = rank_analogs(subject, candidates, k=3)
    names = [r.trdar_cd for r, _ in ranked]
    assert "이웃" in names and "이웃2" in names
    assert all(sim > 0 for _, sim in ranked)
