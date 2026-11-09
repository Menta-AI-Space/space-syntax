"""V-World 개별공시지가 API 적재 — 네트워크 없이 도는 부분.

실제 응답 모양은 2026-08-21에 라이브 API로 확인한 것을 그대로 썼다.
"""

import pytest

from tools.ingest_land_prices_api import (
    EARLIEST_YEAR,
    MAX_ROWS_PER_PAGE,
    fetch_ldong_year,
    fetch_page,
    parse_years,
    to_row,
)

# 라이브 응답 1건 (종로구 평동 2015년) — 필드명·값 형식을 실측 그대로 둔다.
LIVE_ITEM = {
    "mnnmSlno": "211",
    "pblntfDe": "2015-05-29",
    "stdLandAt": "N",
    "stdrYear": "2015",
    "pnu": "1111017700102110000",
    "lastUpdtDt": "2023-08-10",
    "ldCode": "1111017700",
    "ldCodeNm": "서울특별시 종로구 평동",
    "regstrSeCodeNm": "일반",
    "stdrMt": "01",
    "pblntfPclnd": "3026000",
    "regstrSeCode": "1",
}


# ── 연도 파싱 ──────────────────────────────────────────────────────────

def test_parse_years_accepts_range():
    assert parse_years(["2015..2018"]) == [2015, 2016, 2017, 2018]


def test_parse_years_accepts_list():
    assert parse_years(["2024", "2026"]) == [2024, 2026]


def test_parse_years_mixes_both_and_dedupes():
    assert parse_years(["2024..2026", "2025"]) == [2024, 2025, 2026]


def test_parse_years_drops_years_below_the_measured_floor():
    """2008 이하는 API가 totalCount=0으로 준다 — 헛되이 요청하지 않는다."""
    assert parse_years(["2005..2011"]) == [2009, 2010, 2011]


def test_parse_years_returns_empty_when_all_below_floor():
    assert parse_years(["1990", "2000"]) == []


def test_floor_matches_what_was_measured():
    assert EARLIEST_YEAR == 2009


# ── 응답 1건 → DB 행 ───────────────────────────────────────────────────

def test_live_item_maps_to_every_schema_column():
    row = to_row(LIVE_ITEM, "vworld_api")
    assert row == (
        "1111017700102110000",   # pnu
        2015,                    # base_year
        3026000,                 # price_per_sqm (원/㎡)
        1,                       # base_month  ("01" → 1)
        False,                   # is_standard ("N" → False)
        "2015-05-29",            # notice_date
        "1111017700",            # ldong_code
        "서울특별시 종로구 평동",   # ldong_name
        "211",                   # jibun
        "1",                     # special_code
        "일반",                   # special_name
        "vworld_api",            # src
    )


def test_standard_land_flag_is_parsed():
    assert to_row({**LIVE_ITEM, "stdLandAt": "Y"}, "s")[4] is True
    assert to_row({**LIVE_ITEM, "stdLandAt": "N"}, "s")[4] is False


def test_missing_standard_flag_stays_none_not_false():
    """'표준지가 아니다'와 '모른다'는 다르다."""
    assert to_row({**LIVE_ITEM, "stdLandAt": ""}, "s")[4] is None


@pytest.mark.parametrize("field", ["pnu", "stdrYear", "pblntfPclnd"])
def test_row_without_a_required_field_is_dropped(field):
    """필수 값이 비면 0으로 채우지 않고 버린다."""
    assert to_row({**LIVE_ITEM, field: ""}, "s") is None


def test_non_numeric_price_is_dropped():
    assert to_row({**LIVE_ITEM, "pblntfPclnd": "-"}, "s") is None


def test_zero_price_is_dropped():
    assert to_row({**LIVE_ITEM, "pblntfPclnd": "0"}, "s") is None


def test_blank_optional_fields_become_none():
    row = to_row({**LIVE_ITEM, "mnnmSlno": "", "regstrSeCodeNm": "  "}, "s")
    assert row[8] is None and row[10] is None


# ── 응답 봉투 처리 ─────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """페이지 순서대로 미리 정한 응답을 돌려준다."""

    def __init__(self, payloads):
        self._payloads = payloads
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return _FakeResponse(self._payloads[len(self.calls) - 1])


def _envelope(items, total=None):
    return {"indvdLandPrices": {"field": items, "totalCount": str(total if total is not None else len(items))}}


def test_empty_year_returns_no_items():
    """자료 없는 연도는 {"response": {... totalCount: "0"}} 로 온다."""
    client = _FakeClient([{"response": {"totalCount": "0", "resultCode": "", "resultMsg": ""}}])
    items, total = fetch_page(client, "1111017700", 2008, 1)
    assert items == [] and total == 0


def test_single_item_returned_as_object_is_wrapped_in_a_list():
    client = _FakeClient([{"indvdLandPrices": {"field": LIVE_ITEM, "totalCount": "1"}}])
    items, total = fetch_page(client, "1111017700", 2015, 1)
    assert items == [LIVE_ITEM] and total == 1


def test_error_result_code_raises_rather_than_returning_empty():
    """INCORRECT_KEY를 '자료 없음'으로 삼키면 배치가 조용히 0건을 적재한다."""
    client = _FakeClient([
        {"indvdLandPrices": {"resultCode": "INCORRECT_KEY", "resultMsg": "인증키 정보가 올바르지 않습니다."}}
    ])
    with pytest.raises(RuntimeError, match="INCORRECT_KEY"):
        fetch_page(client, "1111017700", 2024, 1)


def test_pagination_stops_on_a_short_page():
    client = _FakeClient([_envelope([LIVE_ITEM] * 10, total=10)])
    assert len(fetch_ldong_year(client, "1111017700", 2024)) == 10
    assert len(client.calls) == 1


def test_pagination_follows_until_total_is_reached():
    full = [_envelope([LIVE_ITEM] * MAX_ROWS_PER_PAGE, total=MAX_ROWS_PER_PAGE + 5)]
    full.append(_envelope([LIVE_ITEM] * 5, total=MAX_ROWS_PER_PAGE + 5))
    client = _FakeClient(full)
    assert len(fetch_ldong_year(client, "1111017700", 2024)) == MAX_ROWS_PER_PAGE + 5
    assert [c["pageNo"] for c in client.calls] == ["1", "2"]


def test_request_always_carries_domain():
    """domain이 빠지면 V-World가 INCORRECT_KEY로 거부한다 (2026-08-21 실측)."""
    client = _FakeClient([_envelope([])])
    fetch_page(client, "1111017700", 2024, 1)
    assert "domain" in client.calls[0]


# ── 중복 행 (2026-08-21 실측: 한 필지가 3행으로 온다) ──────────────────

def test_dedupe_keeps_one_row_per_parcel_year():
    from tools.ingest_land_prices_api import dedupe_rows
    a = to_row(LIVE_ITEM, "s")
    b = to_row({**LIVE_ITEM, "stdrMt": "1"}, "s")   # 표기만 다른 같은 행
    kept, removed = dedupe_rows([a, b, a])
    assert len(kept) == 1 and removed == 2


def test_dedupe_keeps_different_years_apart():
    from tools.ingest_land_prices_api import dedupe_rows
    a = to_row(LIVE_ITEM, "s")
    b = to_row({**LIVE_ITEM, "stdrYear": "2024"}, "s")
    kept, removed = dedupe_rows([a, b])
    assert len(kept) == 2 and removed == 0


def test_dedupe_keeps_different_parcels_apart():
    from tools.ingest_land_prices_api import dedupe_rows
    a = to_row(LIVE_ITEM, "s")
    b = to_row({**LIVE_ITEM, "pnu": "1111017700100010001"}, "s")
    kept, removed = dedupe_rows([a, b])
    assert len(kept) == 2 and removed == 0


def test_dedupe_of_empty_is_empty():
    from tools.ingest_land_prices_api import dedupe_rows
    assert dedupe_rows([]) == ([], 0)
