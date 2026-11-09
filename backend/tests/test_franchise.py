"""Tests for app.routers.franchise — brand-detail snapshot-year selection.

``franchise_brands`` has one row per (yr, brand_nm, hq_conm): the same brand
can have several snapshot-year rows with different frcs_cnt values. The bug
this guards against: picking the row with the largest frcs_cnt instead of the
most recent yr, which silently mixes an old year's churn/cost numbers with a
different year's store count.

``FakeCursor`` below is a tiny real SQL engine (parses WHERE/ORDER BY/LIMIT
out of the query text and actually applies them to in-memory rows) rather
than a query-text lookup table, so a regression that changes the ORDER BY
column in franchise.py is caught by wrong *data* coming back, not by
matching wording.
"""

from __future__ import annotations

import re

import pytest

from app.routers.franchise import _brand_detail


class FakeFranchiseDB:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(
        self,
        *,
        brand_nm: str,
        yr: str,
        frcs_cnt: int | None,
        hq_conm: str | None = "본사",
        industry_mid: str | None = "외식업",
        new_open_cnt: int | None = None,
        contract_end_cnt: int | None = None,
        contract_cancel_cnt: int | None = None,
        startup_cost_total_manwon: float | None = None,
    ) -> None:
        self.rows.append(
            {
                "brand_nm": brand_nm,
                "yr": yr,
                "frcs_cnt": frcs_cnt,
                "hq_conm": hq_conm,
                "industry_mid": industry_mid,
                "new_open_cnt": new_open_cnt,
                "contract_end_cnt": contract_end_cnt,
                "contract_cancel_cnt": contract_cancel_cnt,
                "startup_cost_total_manwon": startup_cost_total_manwon,
            }
        )


def _select_columns(query: str) -> list[str]:
    m = re.search(r"SELECT\s+(.*?)\s+FROM", query, re.IGNORECASE | re.DOTALL)
    return [c.strip() for c in m.group(1).split(",")]


def _apply_where(rows: list[dict], query: str, params: tuple) -> list[dict]:
    m = re.search(r"WHERE\s+brand_nm\s*(=|ILIKE)\s*%s", query, re.IGNORECASE)
    if not m:
        return rows
    op = m.group(1).upper()
    value = params[0]
    if op == "=":
        return [r for r in rows if r["brand_nm"] == value]
    needle = value.strip("%").lower()
    return [r for r in rows if needle in r["brand_nm"].lower()]


def _sort_pass(rows: list[dict], col: str, desc: bool, nulls_last: bool) -> list[dict]:
    non_null = [r for r in rows if r.get(col) is not None]
    null_rows = [r for r in rows if r.get(col) is None]
    non_null.sort(key=lambda r: r[col], reverse=desc)
    if nulls_last:
        return non_null + null_rows
    return (null_rows + non_null) if desc else (non_null + null_rows)


def _apply_order_by(rows: list[dict], query: str) -> list[dict]:
    m = re.search(r"ORDER BY\s+(.*?)(?:\s+LIMIT|\s*$)", query, re.IGNORECASE | re.DOTALL)
    if not m:
        return rows
    parts = [p.strip() for p in m.group(1).split(",")]
    result = list(rows)
    # Stable multi-key sort: apply least-significant key first.
    for part in reversed(parts):
        tokens = part.split()
        col = tokens[0]
        desc = "DESC" in part.upper()
        nulls_last = "NULLS LAST" in part.upper()
        result = _sort_pass(result, col, desc, nulls_last)
    return result


def _apply_limit(rows: list[dict], query: str) -> list[dict]:
    m = re.search(r"LIMIT\s+(\d+)", query, re.IGNORECASE)
    if not m:
        return rows
    return rows[: int(m.group(1))]


class FakeCursor:
    def __init__(self, db: FakeFranchiseDB) -> None:
        self.db = db
        self._result: list[tuple] = []

    def execute(self, query: str, params: tuple = ()) -> None:
        rows = _apply_where(self.db.rows, query, params)
        rows = _apply_order_by(rows, query)
        rows = _apply_limit(rows, query)
        cols = _select_columns(query)
        self._result = [
            tuple(1 if c == "1" else r.get(c) for c in cols) for r in rows
        ]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self, db: FakeFranchiseDB) -> None:
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _use_fake_db(monkeypatch, db: FakeFranchiseDB) -> None:
    import app.routers.franchise as franchise

    monkeypatch.setattr(franchise, "get_connection", lambda: FakeConnection(db))


def test_brand_detail_picks_latest_year_even_when_an_older_year_has_more_stores(monkeypatch):
    db = FakeFranchiseDB()
    # 2022 snapshot: brand was much bigger back then.
    db.add(brand_nm="이디야커피", yr="2022", frcs_cnt=3000, new_open_cnt=50,
            contract_end_cnt=10, contract_cancel_cnt=5, startup_cost_total_manwon=8000)
    # 2024 snapshot: fewer stores today, but this is the current picture.
    db.add(brand_nm="이디야커피", yr="2024", frcs_cnt=2500, new_open_cnt=30,
            contract_end_cnt=20, contract_cancel_cnt=10, startup_cost_total_manwon=9500)
    _use_fake_db(monkeypatch, db)

    detail = _brand_detail("이디야커피")

    assert detail is not None
    assert detail["yr"] == "2024"
    assert detail["frcs_cnt"] == 2500
    assert detail["startup_cost_total_manwon"] == 9500.0


def test_brand_detail_ilike_fallback_also_uses_latest_year_of_the_matched_brand(monkeypatch):
    db = FakeFranchiseDB()
    # Two distinct brands match the partial query; "메가엠지씨커피" is the more
    # prominent one by store count, so it should be the one whose data is used.
    db.add(brand_nm="메가엠지씨커피", yr="2021", frcs_cnt=9000)
    db.add(brand_nm="메가엠지씨커피", yr="2024", frcs_cnt=3000)
    db.add(brand_nm="메가바이트커피", yr="2024", frcs_cnt=10)
    _use_fake_db(monkeypatch, db)

    detail = _brand_detail("메가")

    assert detail is not None
    assert detail["brand_nm"] == "메가엠지씨커피"
    assert detail["yr"] == "2024"
    assert detail["frcs_cnt"] == 3000


def test_brand_detail_returns_none_when_nothing_matches(monkeypatch):
    db = FakeFranchiseDB()
    db.add(brand_nm="이디야커피", yr="2024", frcs_cnt=2500)
    _use_fake_db(monkeypatch, db)

    assert _brand_detail("없는브랜드") is None


def test_brand_detail_computes_churn_pct_from_the_selected_year_only(monkeypatch):
    db = FakeFranchiseDB()
    db.add(brand_nm="이디야커피", yr="2022", frcs_cnt=1000, contract_end_cnt=100, contract_cancel_cnt=100)
    db.add(brand_nm="이디야커피", yr="2024", frcs_cnt=2000, contract_end_cnt=20, contract_cancel_cnt=20)
    _use_fake_db(monkeypatch, db)

    detail = _brand_detail("이디야커피")

    assert detail["yr"] == "2024"
    # (20 + 20) / 2000 * 100, not the 2022 numbers.
    assert detail["churn_pct"] == pytest.approx(2.0)
