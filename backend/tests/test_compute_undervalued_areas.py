"""동 단위 배치의 **적재**를 지킨다.

이 배치는 426개 동을 12분에 걸쳐 다 계산해 놓고 마지막 INSERT에서 죽고 있었다
(2026-08-23). 값 튜플에 ``"NOW()"`` 문자열이 하나 더 들어 있는데 template의
플레이스홀더는 8개뿐이라 psycopg2가 ``not all arguments converted``로 터졌다.

계산 로직 테스트로는 절대 안 잡힌다 — SQL을 건드리지 않기 때문이다. 그래서
여기서는 **execute_values에 실제로 넘어가는 인자**를 붙잡아 본다.
"""

from dataclasses import dataclass

import pytest

from tools import compute_undervalued_areas as mod


@dataclass
class _Score:
    adm_cd: str = "11010530"
    adm_nm: str = "서울특별시 종로구 사직동"
    structure_score: float = 0.648
    structure_percentile: float = 71.2
    price_per_sqm_manwon: float | None = 1052.1
    price_percentile: float | None = 64.0
    trade_sample_count: int = 270
    gap_score: float | None = 7.2


class _Cur:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def cursor(self):
        return _Cur()


def _captured(monkeypatch, scores):
    seen = {}

    def fake_execute_values(_cur, sql, argslist, template=None, **_kw):
        seen["sql"] = sql
        seen["rows"] = argslist
        seen["template"] = template

    monkeypatch.setattr(
        mod.psycopg2.extras, "execute_values", fake_execute_values
    )
    mod.upsert_scores(_Conn(), scores)
    return seen


def test_value_count_matches_template_placeholders(monkeypatch):
    """튜플 길이 == template의 %s 개수. 어긋나면 배치가 마지막에 통째로 죽는다."""
    seen = _captured(monkeypatch, [_Score()])
    placeholders = seen["template"].count("%s")
    assert placeholders == len(seen["rows"][0]), (
        f"template에 %s가 {placeholders}개인데 값은 {len(seen['rows'][0])}개다"
    )


def test_computed_at_is_not_passed_as_a_value(monkeypatch):
    """``computed_at``은 template의 NOW() 리터럴이 채운다.

    문자열 "NOW()"를 값으로 넘기면 SQL 함수가 아니라 **문자열**이 되어, 설령
    개수가 맞아도 타임스탬프 컬럼에 들어가지 않는다.
    """
    seen = _captured(monkeypatch, [_Score()])
    assert "NOW()" not in seen["rows"][0]
    assert "NOW()" in seen["template"]


def test_insert_columns_match_value_count(monkeypatch):
    """INSERT 컬럼 목록과 값 개수도 맞아야 한다 (NOW()가 채우는 것 포함)."""
    seen = _captured(monkeypatch, [_Score()])
    columns = seen["sql"].split("(", 1)[1].split(")", 1)[0]
    n_columns = len([c for c in columns.split(",") if c.strip()])
    # 값 튜플 + template이 직접 채우는 NOW() 하나.
    assert n_columns == len(seen["rows"][0]) + 1


def test_every_score_becomes_one_row(monkeypatch):
    seen = _captured(monkeypatch, [_Score(), _Score(adm_cd="11010540")])
    assert len(seen["rows"]) == 2
    assert seen["rows"][0][0] == "11010530"
    assert seen["rows"][1][0] == "11010540"


def test_missing_price_survives_as_null(monkeypatch):
    """실거래 표본이 없는 동은 가격이 None이다 — 0으로 바뀌면 안 된다.
    0은 '아주 싸다'로 읽혀 갭 상위를 통째로 오염시킨다."""
    seen = _captured(
        monkeypatch,
        [_Score(price_per_sqm_manwon=None, price_percentile=None, trade_sample_count=0)],
    )
    row = seen["rows"][0]
    assert row[4] is None
    assert row[5] is None


@pytest.mark.parametrize("n", [0, 1, 5])
def test_does_not_crash_on_any_batch_size(monkeypatch, n):
    seen = _captured(monkeypatch, [_Score(adm_cd=f"110105{i:02d}") for i in range(n)])
    assert len(seen["rows"]) == n
