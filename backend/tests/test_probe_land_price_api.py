"""프로브의 순수 판정 로직 — 네트워크 없이."""

from xml.etree import ElementTree as ET

from tools.probe_land_price_api import (
    PNU_FIELD_HINTS,
    PRICE_FIELD_HINTS,
    YEAR_FIELD_HINTS,
    find_hint,
    parse_any,
    walk_keys,
)


def _fields(payload) -> set[str]:
    out: set[str] = set()
    walk_keys(payload, out)
    return out


def test_parse_any_detects_json():
    kind, val = parse_any('{"a": 1}')
    assert kind == "json" and val == {"a": 1}


def test_parse_any_detects_xml():
    kind, val = parse_any("<root><a>1</a></root>")
    assert kind == "xml" and isinstance(val, ET.Element)


def test_parse_any_tolerates_leading_whitespace():
    assert parse_any('\n  {"a": 1}')[0] == "json"


def test_walk_keys_collects_nested_json_fields():
    payload = {"response": {"body": {"items": [{"pnu": "1", "pblntfPclnd": 500}]}}}
    assert {"response", "body", "items", "pnu", "pblntfPclnd"} <= _fields(payload)


def test_walk_keys_collects_xml_tags():
    root = ET.fromstring("<response><body><item><pnu>1</pnu></item></body></response>")
    assert {"body", "item", "pnu"} <= _fields(root)


def test_walk_keys_stops_before_runaway_depth():
    """구조를 모르는 응답을 받으므로 깊이 폭주로 죽지 않아야 한다."""
    node: dict = {"leaf": 1}
    for i in range(50):
        node = {f"level{i}": node}
    assert _fields(node)  # 예외 없이 무언가는 돌려준다


# ── 세 가지 결정적 질문 ────────────────────────────────────────────────

def test_finds_pnu_field_by_exact_name():
    assert find_hint({"pnu", "price"}, PNU_FIELD_HINTS) == "pnu"


def test_finds_pnu_field_case_insensitively():
    assert find_hint({"PNU", "price"}, PNU_FIELD_HINTS) == "PNU"


def test_finds_pnu_inside_a_longer_field_name():
    assert find_hint({"landPnuCode"}, PNU_FIELD_HINTS) == "landPnuCode"


def test_finds_korean_field_names():
    """출처에 따라 한글 필드명으로 온다."""
    assert find_hint({"토지대장번호", "공시지가"}, PNU_FIELD_HINTS) == "토지대장번호"
    assert find_hint({"토지대장번호", "공시지가"}, PRICE_FIELD_HINTS) == "공시지가"


def test_finds_year_field():
    assert find_hint({"stdrYear"}, YEAR_FIELD_HINTS) == "stdrYear"


def test_missing_pnu_returns_none_so_the_probe_can_fail_loudly():
    """PNU가 없으면 필지 단위가 성립하지 않는다 — 조용히 넘어가면 안 된다."""
    assert find_hint({"sido", "sigungu", "price"}, PNU_FIELD_HINTS) is None


def test_price_hint_does_not_match_unrelated_field():
    assert find_hint({"sido", "sigungu"}, PRICE_FIELD_HINTS) is None
