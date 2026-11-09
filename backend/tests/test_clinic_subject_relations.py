"""Validation tests for the clinic specialty data-wiring JSON files:
- data/clinic_qcode_subject_map.json (SBIZ Q102xx -> HIRA subject_code)
- data/clinic_subject_relations.json (direct/adjacent substitution weights)
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

QCODES = [f"Q102{n:02d}" for n in range(1, 13)]  # Q10201..Q10212


def _load(name: str) -> dict:
    with (DATA_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


def test_qcode_subject_map_covers_all_clinic_small_categories():
    m = _load("clinic_qcode_subject_map.json")
    for qcode in QCODES:
        assert qcode in m, f"{qcode} missing from clinic_qcode_subject_map.json"
        entry = m[qcode]
        assert "subject_codes" in entry
        assert isinstance(entry["subject_codes"], list)


def test_qcode_subject_map_ambiguous_entries_are_documented():
    m = _load("clinic_qcode_subject_map.json")
    # Q10202 (외과 의원) lumps 일반외과(04) and 정형외과(05) together — must be
    # documented, not silently mapped to a single wrong code.
    ortho = m["Q10202"]
    assert set(ortho["subject_codes"]) == {"04", "05"}
    assert "note" in ortho

    # Q10209 (기타 의원) is a catch-all that cannot be mapped to specific codes —
    # must resolve to an empty list so callers fall back to SBIZ-based matching.
    other = m["Q10209"]
    assert other["subject_codes"] == []
    assert "note" in other


def test_subject_relations_weights_are_in_valid_range():
    relations = _load("clinic_subject_relations.json")
    for code, entry in relations.items():
        if code.startswith("_"):
            continue
        adjacent = entry.get("adjacent") or {}
        for adj_code, weight in adjacent.items():
            assert 0.0 < weight <= 1.0, f"{code}->{adj_code} weight out of range: {weight}"
            assert adj_code != code, f"{code} lists itself as adjacent"


def test_subject_relations_includes_documented_examples():
    """Spot-check the relations the task brief called out explicitly."""
    relations = _load("clinic_subject_relations.json")
    assert relations["05"]["adjacent"]["21"] == 0.7  # 정형외과 -> 재활의학과
    assert relations["05"]["adjacent"]["06"] == 0.5  # 정형외과 -> 신경외과
    assert relations["05"]["adjacent"]["09"] == 0.6  # 정형외과 -> 마취통증의학과
    assert relations["14"]["adjacent"]["08"] == 0.6  # 피부과 -> 성형외과
    assert relations["01"]["adjacent"]["23"] == 0.7  # 내과 -> 가정의학과
    assert relations["13"]["adjacent"]["11"] == 0.3  # 이비인후과 -> 소아청소년과


def test_resolve_subject_codes_uses_the_json_files():
    from app.services.real_competition import resolve_subject_codes

    assert resolve_subject_codes("Q10205") == ["12"]  # 안과
    assert resolve_subject_codes("Q10202") == ["04", "05"]  # 외과 (ambiguous)
    assert resolve_subject_codes("Q10209") is None  # catch-all -> fallback
    assert resolve_subject_codes(None) is None
    assert resolve_subject_codes("Q99999") is None  # unknown code -> fallback


def test_expand_subject_weights_includes_direct_and_adjacent():
    from app.services.real_competition import _expand_subject_weights

    weights = _expand_subject_weights(["05"])  # 정형외과 선택
    assert weights["05"] == 1.0
    assert weights["21"] == 0.7
    assert weights["06"] == 0.5
    assert weights["09"] == 0.6
