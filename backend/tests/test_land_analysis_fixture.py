"""fixture 테스트 — G6이 이 파일에 대고 화면을 만든다.

여기서 지키는 것 둘:

1. **커밋된 JSON이 코드와 어긋나지 않는다.** 어긋난 채로 push되면 프런트가 실제
   응답과 다른 모양에 맞춰 화면을 만들고, G7에서 fixture를 실제 출력으로 바꿔
   끼울 때 통째로 다시 손대야 한다.
2. **네 경우가 다 들어 있다.** 프런트가 판정 없는 경우(undetermined)를 그리지
   못하면 비교군이 얇은 필지에서 화면이 빈다.
"""

import json
import subprocess
import sys
from pathlib import Path

from app.models.land_analysis import SCHEMA_VERSION, StructuredAnalysis
from app.services.land_analysis_fixture import build_fixture

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    BACKEND_DIR.parent / "frontend-next" / "public" / "fixtures" / "structured-analysis.json"
)


def test_fixture_covers_all_four_cases():
    cases = build_fixture()
    assert set(cases) == {"undervalued", "cheap_with_reason", "above_peers", "undetermined"}
    for key, analysis in cases.items():
        assert analysis.verdict.label == key, f"{key} fixture가 다른 라벨로 판정됐다"


def test_fixture_exercises_all_three_methodology_slots():
    """A·B·C 자리가 셋 다 채워진 예가 하나는 있어야 프런트가 다 그려 본다."""
    rich = build_fixture()["undervalued"]
    assert rich.current_valuation is not None      # 방법론 C
    assert rich.growth_outlook is not None         # 방법론 B
    assert rich.price_forecast is not None         # 방법론 A
    assert [h.years for h in rich.price_forecast.horizons] == [1, 3, 5]


def test_undetermined_case_has_no_valuation_for_the_front_to_render():
    empty = build_fixture()["undetermined"]
    assert empty.current_valuation is None
    assert empty.verdict.strength == "weak"
    assert empty.caveats


def test_committed_fixture_matches_the_code():
    result = subprocess.run(
        [sys.executable, "-m", "tools.write_structured_analysis_fixture", "--check"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",   # Windows 기본이 cp1252라 한글 메시지 디코드가 깨진다
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_committed_fixture_still_validates_against_the_schema():
    """스키마가 바뀌었는데 fixture만 남은 상태를 잡는다."""
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["cases"], "fixture에 case가 없다"
    for key, raw in payload["cases"].items():
        analysis = StructuredAnalysis.model_validate(raw)
        assert analysis.schema_version == SCHEMA_VERSION
        assert analysis.verdict.label == key
