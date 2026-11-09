"""Structured Analysis fixture를 프런트가 읽는 자리에 쓴다.

    python -m tools.write_structured_analysis_fixture          # 쓴다
    python -m tools.write_structured_analysis_fixture --check   # 어긋나면 실패

`--check`는 테스트가 쓰는 경로다. 커밋된 JSON과 코드가 어긋나면 프런트가 실제
응답과 다른 모양에 맞춰 화면을 만들게 되므로, 그 상태로 push되지 않게 막는다.
DB가 필요 없다 — fixture는 순수 계산이다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.land_analysis_fixture import fixture_payload  # noqa: E402

# 프런트가 그대로 fetch할 수 있는 자리에 둔다 — 백엔드를 띄우지 않고 화면을
# 만들 수 있어야 G6이 적재와 독립이 된다.
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend-next"
    / "public"
    / "fixtures"
    / "structured-analysis.json"
)


def render() -> str:
    return json.dumps(fixture_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    # Windows 콘솔·subprocess의 기본 인코딩이 cp1252라 한글 메시지에서 죽는다.
    # 실제로 테스트가 이걸로 실패했다 — 파일 내용이 아니라 print가 원인이었다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="쓰지 않고 커밋된 파일과 비교만 한다 (테스트·CI용)",
    )
    args = parser.parse_args()

    rendered = render()
    if args.check:
        if not FIXTURE_PATH.exists():
            print(f"fixture 파일이 없습니다: {FIXTURE_PATH}", file=sys.stderr)
            return 1
        if FIXTURE_PATH.read_text(encoding="utf-8") != rendered:
            print(
                "fixture가 코드와 어긋납니다. "
                "python -m tools.write_structured_analysis_fixture 를 다시 돌리세요.",
                file=sys.stderr,
            )
            return 1
        print("fixture 최신 상태입니다.")
        return 0

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {FIXTURE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
