"""러너 kill switch — 프로세스를 죽이지 않고 세운다.

`task-state.json`의 `paused`만 바꾼다. 진행 중인 작업은 끝까지 마치고 새 작업을
집지 않는다. 프로세스를 강제로 죽이면 worktree가 중간 상태로 남지만, 이 방법은
작업 경계에서 깨끗하게 선다.

    python backend/tools/agent/pause.py on     # 세우기
    python backend/tools/agent/pause.py off    # 재개
    python backend/tools/agent/pause.py        # 현재 상태만 보기
"""

import sys

from state import load, save


def main(argv: list[str]) -> int:
    state = load()
    if len(argv) < 2:
        print("멈춤" if state.get("paused") else "돌아가는 중")
        return 0
    arg = argv[1].lower()
    if arg not in ("on", "off"):
        print(f"알 수 없는 인자: {arg} (on 또는 off)")
        return 2
    state["paused"] = arg == "on"
    save(state)
    print("세웠다 — 진행 중인 작업은 마치고 멈춘다" if state["paused"] else "다시 돌린다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
