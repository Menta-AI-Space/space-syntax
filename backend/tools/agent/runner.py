"""24시간 자율 개발 러너 — 사이클 루프.

한 사이클에서 하는 일:

    상태 읽기 → 멈춤/한도 확인 → 다음 작업 고르기 → claude에 맡기기
    → 검증 → 통과하면 커밋·푸시 → 상태 기록 → 잠깐 쉼

이 파일의 원칙 두 가지:

1. **검증 없이 push하지 않는다.** 이 저장소는 main push가 곧 프로덕션 배포다
   (CLAUDE.md의 승인 예외). 그래서 검증이 유일한 안전망이고, 여기가 뚫리면
   사람이 안 보는 사이에 라이브가 깨진다.
2. **판단은 순수 함수로 빼고 I/O는 얇게.** 사람이 안 보는 동안 도는 코드라
   "왜 그렇게 결정했는지"를 테스트로 고정할 수 있어야 한다.

돌리는 법:

    python backend/tools/agent/runner.py            # 계속
    python backend/tools/agent/runner.py --once     # 한 사이클만 (점검용)
    python backend/tools/agent/runner.py --dry-run  # 고르기만 하고 아무것도 안 함
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import state as st  # noqa: E402

REPO = st.REPO_ROOT

#: 한 작업에 주는 시간. 넘으면 죽이고 실패로 적는다(CLAUDE.md의 실행 제한).
TASK_TIMEOUT_S = 1200

#: 작업이 "묶음"일 때 주는 시간. 화면에 필드 하나 띄우는 일은 20분이면 넘치지만,
#: "대화가 이어지게 만들어라" 같은 건 분석→구현→테스트→수정을 한 세션 안에서
#: 돌아야 하고 그건 20분에 안 끝난다. 중간에 잘리면 아무것도 안 남는다 —
#: 검증 전에 죽으니 push가 없고, 다음 회차가 또 처음부터 한다.
#: task["budget_min"]으로 작업마다 지정한다.
MAX_TASK_TIMEOUT_S = 4 * 3600


def budget_for(task: dict) -> int:
    """이 작업에 줄 시간(초). 안 적혀 있으면 기본값."""
    minutes = task.get("budget_min")
    if not isinstance(minutes, (int, float)) or minutes <= 0:
        return TASK_TIMEOUT_S
    return min(int(minutes * 60), MAX_TASK_TIMEOUT_S)
#: 할 일이 없을 때 쉬는 간격. 짧게 돌아도 새 일이 생기지 않는다.
IDLE_SLEEP_S = 300
#: 구독 한도에 걸렸을 때. 한도는 보통 시간 단위로 풀린다.
RATE_LIMIT_SLEEP_S = 900

#: claude에게 허용할 도구. 이 저장소를 고치고 검증하는 데 필요한 것만.
#: 푸시는 러너가 직접 한다 — 검증을 통과한 뒤에만 해야 하므로 claude에게 맡기지
#: 않는다.
ALLOWED_TOOLS = "Read,Edit,Write,Glob,Grep,Bash(python *),Bash(npm *),Bash(git diff *),Bash(git status *),Bash(git add *)"


# ---------------------------------------------------------------------------
# 판단 — 순수 함수 (테스트가 여기를 본다)
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    """이번 사이클에 무엇을 할지."""

    action: str  # "work" | "scout" | "idle" | "paused" | "halted"
    task_id: str | None = None
    reason: str = ""


def decide(state: dict) -> Decision:
    """상태만 보고 이번 사이클의 행동을 정한다.

    멈춤과 연속 실패를 여기서 갈라 두는 이유: 둘 다 "일하지 않는다"지만 사람에게
    보여 줄 말이 다르다. 멈춤은 의도된 것이고 연속 실패는 봐 줘야 하는 것이다.
    """
    if state.get("paused"):
        return Decision("paused", reason="kill switch가 켜져 있다")

    limits = state.get("limits") or {}
    halt = limits.get("halt_after_consecutive_failures", 3)
    if int(state.get("consecutive_failures") or 0) >= halt:
        return Decision("halted", reason=f"연속 {halt}회 실패 — 사람 확인이 필요하다")

    task = st.next_task(state)
    if task is None:
        # 그냥 놀지 않는다. 할 일이 떨어지면 새로 찾는 것이 이 러너의 존재 이유다
        # — 사람이 backlog를 채워 줘야만 도는 물건이면 24시간 도는 의미가 없다.
        return Decision("scout", reason="대기 중인 작업이 없어 새로 찾는다")
    return Decision("work", task_id=task["id"])


def pick_parallel(state: dict, limit: int = 2) -> list[dict]:
    """동시에 돌릴 작업들. 파일이 겹치면 같이 돌리지 않는다.

    두 에이전트가 같은 파일을 고치면 나중에 끝난 쪽이 앞 쪽 작업을 덮어쓴다.
    worktree로 작업공간은 갈라 두지만, 그래도 main에 합칠 때 충돌한다 —
    애초에 안 겹치는 것끼리만 묶는 게 싸다.

    범위를 안 적은 작업(files 비어 있음)은 어디든 건드릴 수 있다는 뜻이므로
    혼자 돌린다.
    """
    if state.get("paused"):
        return []
    limits = state.get("limits") or {}
    if int(state.get("consecutive_failures") or 0) >= limits.get("halt_after_consecutive_failures", 3):
        return []

    # 선행이 안 끝난 작업은 빼고 고른다. 안 그러면 "백엔드가 history를 받는다"와
    # "프론트가 history를 보낸다"가 같은 회차에 나란히 돌아, 뒤엣것이 아직 없는
    # 필드를 향해 코드를 쓴다.
    ready = st.ready_tasks(state)
    picked: list[dict] = []
    claimed: set[str] = set()
    for task in ready:
        if len(picked) >= limit:
            break
        files = set(task.get("files") or [])
        if not files:
            # 범위 미지정 — 첫 번째로만 고르고 짝을 안 붙인다.
            if not picked:
                return [task]
            continue
        if files & claimed:
            continue
        picked.append(task)
        claimed |= files
    return picked


def api_key_would_be_billed(env: dict, state: dict) -> bool:
    """구독이 아니라 API로 과금될 상황인가.

    인증 우선순위상 ANTHROPIC_API_KEY(3번)가 구독 토큰(5번)보다 먼저 쓰인다.
    폴백이 꺼져 있는데 키가 환경에 있으면 사용자가 모르는 사이에 요금이 나간다.

    순수 함수로 둔 이유: main()을 거쳐 검사하면 테스트가 가드를 통과하는 순간
    실제 claude 프로세스(최대 20분)를 띄운다. 실제로 그래서 테스트 스위트가
    10분을 넘겼다 — 판단은 부작용 없이 검사할 수 있어야 한다.
    """
    if not env.get("ANTHROPIC_API_KEY"):
        return False
    return not (state.get("auth") or {}).get("api_fallback")


def is_rate_limited(payload: dict | None, stderr: str) -> bool:
    """구독 사용량 한도에 걸렸는가.

    claude는 재시도 이벤트에 `error: "rate_limit"`을 실어 준다. JSON을 못 받은
    경우를 대비해 stderr도 함께 본다 — 한도를 실패로 잘못 세면 연속 실패
    카운터가 올라가 러너가 스스로 멈춰 버린다.
    """
    if payload:
        blob = json.dumps(payload, ensure_ascii=False).lower()
        if "rate_limit" in blob or "rate limit" in blob:
            return True
    lowered = (stderr or "").lower()
    return "rate_limit" in lowered or "rate limit" in lowered


def task_prompt(task: dict) -> str:
    """작업 하나를 claude에게 주는 지시문.

    소유 파일을 못 박는 이유: 여러 agent가 같은 파일을 고치면 서로의 작업을
    덮어쓴다. 범위를 지시문에 적어 두면 worktree 격리와 함께 이중으로 막힌다.
    """
    files = ", ".join(task.get("files") or []) or "(범위 미지정 — 최소한으로 고칠 것)"
    return (
        f"작업: {task['title']}\n\n"
        f"고쳐도 되는 파일: {files}\n"
        "이 범위 밖의 파일은 건드리지 마라.\n\n"
        "규칙:\n"
        "- docs/agent/GOALS.md의 품질 기준을 지킨다. 특히 화면에 적힌 것과 실제 "
        "계산된 것이 달라선 안 되고, 없는 값을 지어내지 않는다.\n"
        "- 동작을 검사하는 테스트를 함께 쓴다. 소스 문자열을 검사하는 테스트는 "
        "쓰지 마라 — 문구는 맞는데 값은 틀린 상태를 통과시킨다.\n"
        "- **공유 계약은 읽기 전용이다**: backend/app/models/schemas.py, "
        "backend/app/config.py, frontend-next/lib/api.ts, "
        "frontend-next/styles/tokens.css, .github/workflows/**. 네 소유 파일에 "
        "이들이 없으면 절대 고치지 마라 — 병렬로 도는 다른 작업이 지금 이 "
        "계약을 기준으로 코드를 쓰고 있다(docs/OWNERSHIP.md).\n"
        "- 커밋과 푸시는 하지 마라. 검증은 러너가 한다.\n"
        "- 할 수 없으면 억지로 고치지 말고 왜 못 하는지 적고 끝내라.\n"
    )


# ---------------------------------------------------------------------------
# I/O — 얇게
# ---------------------------------------------------------------------------


def claude_bin() -> str:
    """claude 실행 파일 경로.

    윈도우에서 npm 전역 설치는 `claude.cmd`인데, 확장자 없는 `claude`는 bash
    스크립트라 파이썬 subprocess가 못 띄운다(WinError 2) — 로컬에서 러너를
    돌리면 첫 호출부터 죽었다. which가 PATHEXT를 따라 실제 파일을 찾아 준다.
    컨테이너(리눅스)에서는 그냥 `claude`가 잡힌다.
    """
    return shutil.which("claude") or "claude"


def backend_python() -> str:
    """백엔드 테스트를 돌릴 파이썬.

    로컬(윈도우)은 `.venv/Scripts/python.exe`, 컨테이너(리눅스)는
    `.venv/bin/python`, venv가 아예 없으면 지금 도는 파이썬. 경로를 하드코딩하면
    러너가 Railway에서 첫 검증부터 깨진다.
    """
    for candidate in (
        REPO / "backend" / ".venv" / "Scripts" / "python.exe",
        REPO / "backend" / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


#: 타임아웃과 실행 불가를 나타내는 종료코드. 셸 관례를 따른다.
RC_TIMEOUT = 124
RC_NOT_FOUND = 127


def _run(cmd: list[str], *, timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """실행 결과를 돌려준다. **예외를 던지지 않는다.**

    타임아웃과 실행 실패를 예외로 두면 러너가 통째로 죽는다. 실제로 그랬다:
    verify()의 pytest가 900초를 넘겨 TimeoutExpired가 올라갔고, 컨테이너는
    재시작을 반복했으며, 그 사이 실패는 기록조차 안 됐다. 사람이 안 보는 동안
    도는 프로그램이 예외로 죽는 것은 가장 나쁜 결말이다 — 무슨 일이 있었는지
    아무도 모른다.

    그래서 여기서 결과로 바꿔 놓고, 호출부는 종료코드만 보면 되게 한다.
    """
    try:
        return subprocess.run(
            cmd, cwd=str(cwd or REPO), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return subprocess.CompletedProcess(
            cmd, RC_TIMEOUT, partial, f"{timeout}초를 넘겨 중단했다"
        )
    except (FileNotFoundError, OSError) as exc:
        return subprocess.CompletedProcess(cmd, RC_NOT_FOUND, "", f"실행할 수 없다: {exc}")


def invoke_claude(
    task: dict, *, timeout: int | None = None, cwd: Path | None = None
) -> tuple[bool, dict | None, str]:
    """claude에게 작업을 맡긴다. (성공여부, JSON응답, stderr)

    `--bare`는 쓰지 않는다 — bare 모드는 CLAUDE_CODE_OAUTH_TOKEN을 안 읽어서
    구독 인증이 통하지 않는다(문서에 명시돼 있다).
    """
    cmd = [
        claude_bin(), "-p", task_prompt(task),
        "--allowedTools", ALLOWED_TOOLS,
        "--permission-mode", "acceptEdits",
        "--output-format", "json",
    ]
    proc = _run(cmd, timeout=budget_for(task) if timeout is None else timeout, cwd=cwd)
    if proc.returncode == RC_NOT_FOUND:
        return False, None, "claude 명령을 찾을 수 없다 (이미지에 CLI가 없다)"
    payload = None
    if proc.stdout:
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = None

    if proc.returncode == 0:
        return True, payload, proc.stderr or ""

    # 실패 원인은 stderr가 아니라 **stdout의 JSON**에 실려 오는 경우가 많다.
    # stderr만 보고하면 "[failed] T-001: "처럼 아무것도 없는 줄만 남아 진단이
    # 불가능하다 — 사람이 안 보는 동안 도는 러너에서 이건 치명적이다.
    parts = [f"종료코드 {proc.returncode}"]
    if isinstance(payload, dict):
        for key in ("error", "result", "subtype", "is_error"):
            if payload.get(key):
                parts.append(f"{key}={payload[key]}")
    if not isinstance(payload, dict) and proc.stdout:
        parts.append("stdout=" + proc.stdout.strip()[:600])
    if proc.stderr:
        parts.append("stderr=" + proc.stderr.strip()[:600])
    return False, payload, " | ".join(parts)


#: 실패한 테스트를 가리키는 줄. pytest는 `FAILED tests/x.py::test_y - 이유`,
#: `--tb=line`은 `/path/x.py:12: AssertionError` 꼴로 찍는다.
_FAILURE_LINE = (
    # pytest
    "FAILED ", "ERROR ", "Error:", "assert ",
    # node:test(프론트), tsc, eslint — 형식이 달라 pytest 표시만 찾으면 못 잡는다
    "not ok ", "AssertionError", "error TS", "✖ ",
)


def _failure_excerpt(
    output: str | None, limit: int = 1800, *, stderr: str | None = None
) -> str:
    """테스트 출력에서 **왜 깨졌는지** 알려 주는 줄만 남긴다.

    출력을 뒤에서 그냥 자르면 경고와 요약만 담긴다. 사유를 남기는 목적이
    "다음 사람이 원인을 안다"는 것인데, 정작 실패한 테스트 이름이 빠지면
    없느니만 못하다.

    stdout과 stderr를 **둘 다** 본다. pytest는 실패를 stdout에 적지만 npm·node는
    stderr에 적는다 — 한쪽만 보면 반대쪽 실패가 사유 없이 기록된다.

    이 매개변수가 병합 중에 사라져 T-209가 통째로 날아갔다("작업 스레드가 예외로
    끝났다: unexpected keyword argument 'stderr'"). 사유를 적는 함수가 사유 없이
    죽으면 러너는 왜 실패했는지 영영 모른다.
    """
    text = ((output or "") + "\n" + (stderr or "")).strip()
    hits = [ln for ln in text.splitlines() if any(k in ln for k in _FAILURE_LINE)]
    if hits:
        return "\n".join(hits)[:limit]
    # 못 찾으면 원문 끝을 준다 — 아무것도 안 주는 것보단 낫다.
    return text.strip()[-limit:]


def verify(root: Path | None = None) -> tuple[bool, str]:
    """push해도 되는지 확인한다. 여기가 유일한 안전망이다.

    백엔드는 DB 없이도 565/584가 돌므로 컨테이너에서 검증할 수 있다. 프론트는
    변경이 있을 때만 돌린다 — 매번 돌리면 사이클이 느려지고, 백엔드만 고친
    작업에서 프론트 검사는 아무것도 못 잡는다.
    """
    root = root or REPO
    changed = _run(["git", "diff", "--name-only", "HEAD"], timeout=60, cwd=root).stdout
    touched_backend = "backend/" in changed
    touched_frontend = "frontend-next/" in changed

    if touched_backend:
        # 경고를 끄고 트레이스를 한 줄로 줄인다. 안 그러면 출력 끝이 전부
        # DeprecationWarning이라, 뒤에서 잘라 담은 사유에 정작 **어느 테스트가
        # 왜 깨졌는지**가 안 들어온다 — 실제로 그래서 한 번 헛돌았다.
        proc = _run([backend_python(), "-m", "pytest", "-q", "--tb=line", "-p", "no:warnings"],
                    timeout=900, cwd=root / "backend")
        if proc.returncode == RC_TIMEOUT:
            # "테스트 실패"와 원인이 다르다 — 느려진 것이지 깨진 게 아니다.
            return False, "백엔드 테스트가 제한 시간을 넘겼다 — " + (proc.stderr or "")
        if proc.returncode != 0:
            return False, "백엔드 테스트 실패\n" + _failure_excerpt(proc.stdout, stderr=proc.stderr)

    if touched_frontend:
        for script in ("typecheck", "test", "lint"):
            proc = _run(["npm", "run", script], timeout=900, cwd=root / "frontend-next")
            if proc.returncode == RC_TIMEOUT:
                return False, f"프론트 {script}가 제한 시간을 넘겼다 — " + (proc.stderr or "")
            if proc.returncode != 0:
                # 백엔드와 같은 이유로 발췌한다. T-053이 "프론트 test 실패"라고만
                # 남기고 끝나 어느 테스트가 왜 깨졌는지 알 수 없었다 — 백엔드만
                # 고치고 프론트를 빠뜨린 결과다.
                return False, f"프론트 {script} 실패\n" + _failure_excerpt(proc.stdout, stderr=proc.stderr)

    if not touched_backend and not touched_frontend:
        return True, "코드 변경 없음"
    return True, "검증 통과"


def branch_for(task: dict) -> str:
    """이 작업이 올라갈 브랜치."""
    return f"auto/{task['id']}"


#: main push가 경합으로 튕길 때 재시도 횟수. 러너와 사람이 같은 main에 올리니
#: 한 번은 흔히 튕긴다 — 다시 fetch+rebase하면 대개 통과한다.
_MAIN_PUSH_ATTEMPTS = 3


def commit_and_push(task: dict, root: Path | None = None) -> tuple[bool, str]:
    """검증을 통과한 변경을 최신 main 위에 얹어 **바로 반영한다**.

    2026-07-30 소유자 결정으로 브랜치+PR 방식에서 되돌렸다. 브랜치에 담아 두면
    아무도 병합하지 않아 하루 만에 101개가 쌓였고, 그중 67개는 베이스가 100커밋
    이상 뒤처져 같은 파일에서 서로 충돌해 통째로 버려야 했다 — 밤새 80건을
    처리했는데 라이브에 반영된 것이 0건이었다. "검증만 통과한 변경이 라이브로
    나가는 위험"보다 "아무것도 나가지 않는 것"이 더 나빴다.

    안전장치는 그대로다 — 검증(pytest·typecheck·test·lint)을 통과한 변경만 여기
    온다. 대신 사람이 하루 한두 번 라이브를 눈으로 점검하는 것으로 "화면 확인"을
    갚는다.

    경합으로 push가 튕기면 다시 rebase해서 재시도하고, 끝내 안 되거나 rebase가
    충돌하면 작업 브랜치로 물러난다 — 어느 경우에도 일을 잃지 않는다.

    상태 파일(docs/agent)은 여기 담지 않는다. push_state가 따로 올린다 —
    브랜치에 갇히면 러너가 자기가 뭘 했는지 잊고 같은 일을 반복한다.
    """
    root = root or REPO

    _run(["git", "add", "-A", "--", ".", ":!docs/agent"], timeout=60, cwd=root)
    staged = _run(["git", "diff", "--cached", "--name-only"], timeout=60, cwd=root).stdout.strip()
    if not staged:
        return True, "올릴 변경이 없다"

    message = f"{task['title']}\n\n자율 러너 작업 {task['id']}.\n"
    proc = _run(["git", "commit", "-m", message], timeout=120, cwd=root)
    if proc.returncode != 0:
        return False, proc.stderr or proc.stdout

    for attempt in range(1, _MAIN_PUSH_ATTEMPTS + 1):
        _run(["git", "fetch", "origin", "main"], timeout=180, cwd=root)
        rebased = _run(["git", "rebase", "origin/main"], timeout=180, cwd=root)
        if rebased.returncode != 0:
            # 충돌은 재시도해도 그대로다 — 되돌리고 브랜치로 넘긴다.
            _run(["git", "rebase", "--abort"], timeout=60, cwd=root)
            return _fallback_to_branch(task, root, "main에 rebase 충돌")
        pushed = _run(["git", "push", "origin", "HEAD:main"], timeout=300, cwd=root)
        if pushed.returncode == 0:
            return True, "main 반영" + (f" (재시도 {attempt}회)" if attempt > 1 else "")

    return _fallback_to_branch(task, root, f"main push가 {_MAIN_PUSH_ATTEMPTS}회 튕김")


def _fallback_to_branch(task: dict, root: Path, why: str) -> tuple[bool, str]:
    """main에 못 올린 변경을 작업 브랜치에 남긴다 — 일을 잃지 않기 위한 마지막 수단.

    여기로 오면 사람이 봐야 한다. 브랜치가 쌓이면 다시 100개가 되므로,
    STATUS/HANDOFF에서 이 문구를 보면 바로 병합하거나 폐기할 것.
    """
    branch = branch_for(task)
    proc = _run(["git", "push", "-f", "origin", f"HEAD:refs/heads/{branch}"], timeout=300, cwd=root)
    if proc.returncode != 0:
        return False, f"{why} · 브랜치 push도 실패: {proc.stderr or proc.stdout}"
    opened = open_pr(task, branch, root)
    return True, f"{why} → {branch}로 물러남 (사람 확인 필요)" + (" · PR 열림" if opened else "")


def open_pr(task: dict, branch: str, root: Path) -> bool:
    """리뷰용 PR. 실패해도 작업은 성공이다 — 브랜치는 이미 올라가 있다."""
    body = (
        f"자율 러너 작업 `{task['id']}`.\n\n"
        f"{task.get('why') or ''}\n\n"
        "러너가 검증(백엔드 pytest + 프론트 typecheck·test·lint)을 통과한 변경만 "
        "올립니다. 다만 **화면으로 확인한 사람은 없습니다** — 표시가 실제로 제대로 "
        "보이는지는 병합 전에 눈으로 봐 주세요.\n"
    )
    proc = _run(
        ["gh", "pr", "create", "--base", "main", "--head", branch,
         "--title", task["title"][:80], "--body", body],
        timeout=180, cwd=root,
    )
    if proc.returncode == 0:
        return True
    # 재시도한 작업이면 PR이 이미 있다 — 실패가 아니다.
    return "already exists" in ((proc.stderr or "") + (proc.stdout or "")).lower()


def pr_state(branch: str, root: Path | None = None) -> str | None:
    """이 브랜치의 PR 상태(OPEN/MERGED/CLOSED). PR이 없거나 확인 못 하면 None.

    확인 못 한 것을 "병합됐다"로 잘못 넘기면 재발견을 막는다는 이 함수의
    목적이 뒤집힌다 — 그래서 실패는 항상 None(= 아직 안 끝났다로 취급)이다.
    """
    proc = _run(
        ["gh", "pr", "view", branch, "--json", "state", "-q", ".state"],
        timeout=60, cwd=root or REPO,
    )
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip().upper()
    return value or None


def unresolved_files(state: dict, root: Path | None = None) -> set[str]:
    """지금 안 끝난 작업들이 다루는 파일 범위 — 정찰이 같은 자리를 다시 못
    담게 거르는 데 쓴다.

    "done"은 브랜치에 올리고 PR을 열었다는 뜻이지 main에 합쳐졌다는 뜻이
    아니다(commit_and_push 참고 — main으로 직접 밀지 않는다). 병합 전까지
    main은 그대로라, 확인 없이 done을 "끝났다"로 치면 다음 정찰이 같은 문제를
    새 제목으로 또 담는다.

    pending/in_progress/blocked/failed는 이미 알려진 문제라 gh를 물을 필요
    없이 바로 "안 끝난 것"으로 친다. done만 PR 병합 여부를 gh로 확인한다 —
    병합이 확인되면 task에 `pr_merged=True`를 남겨 다음 사이클부터는 다시
    묻지 않는다(gh 호출은 아직 안 합쳐진 done 작업 수만큼만 든다).
    """
    files: set[str] = set()
    for task in state.get("tasks", []):
        status = task.get("status")
        if status in ("pending", "in_progress", "blocked", "failed"):
            files |= set(task.get("files") or [])
        elif status == "done" and not task.get("pr_merged"):
            if pr_state(branch_for(task), root) == "MERGED":
                task["pr_merged"] = True
            else:
                files |= set(task.get("files") or [])
    return files


#: 러너가 자기 상태를 쓰는 자리. 여기 변경은 "남의 작업 중 편집"이 아니라
#: 러너 자신이 방금 저장한 것이므로, dirty 판정에서 제외한다.
_OWN_STATE_DIR = "docs/agent/"


def _dirty_paths(root: Path | None = None) -> list[str]:
    """작업 트리에서 변경된 경로 목록. 러너 자기 상태 파일은 뺀다.

    `git status --porcelain`의 각 줄은 "XY 경로" 꼴이다. 이름이 바뀐 경우
    "R  old -> new"로 나오므로 화살표 뒤를 실제 경로로 본다.
    """
    out = _run(["git", "status", "--porcelain"], timeout=60, cwd=root).stdout
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip().strip('"')
        if path.startswith(_OWN_STATE_DIR):
            continue
        paths.append(path)
    return paths


def sync_repo(root: Path | None = None) -> bool:
    """사이클 시작마다 원격을 따라잡는다.

    이게 없으면 러너는 부팅 때 받은 코드와 상태로만 산다. 그래서
    `pause.py on`을 저장소에 올려도 **돌고 있는 컨테이너는 영영 못 읽는다** —
    kill switch가 재시작 전까지 안 먹는 반쪽이 된다. 사람이 급히 세우려 할 때
    안 서는 스위치는 없는 것보다 나쁘다.

    덤으로 코드 수정도 재배포 없이 다음 사이클에 반영된다(main 루프의
    restart_if_own_code_changed가 여기서 당겨온 코드로 갈아탄다).

    남의 작업 중 편집이 남아 있으면 건너뛴다 — rebase로 흔들면 안 된다.

    **단, `docs/agent/`는 예외다** (2026-07-31 수정). 러너는 매 사이클
    `st.save()`로 task-state.json을 쓰는데, 정찰이 빈손이거나 work가 아닌
    사이클은 push_state를 안 부른다(runner.py의 "idle" 경로). 그러면 커밋 안 된
    자기 상태 파일 하나 때문에 dirty가 되어 **이 함수가 영구히 False를
    돌려주고, 그 순간부터 pull이 멈춘다.** 결과로 위에 적은 세 가지가 전부
    죽었다:

    - 코드 자기 갱신이 멈춤 → 07-29 17:13/23:46에 넣은 scout 중복 필터를 돌던
      프로세스가 못 읽어, 07-30 02:43에 옛 코드로 T-179를 중복 생성했다
      (같은 문제가 제목만 바꿔 6~8회 재등록된 실제 원인)
    - kill switch가 안 먹음
    - 옛 상태로 계속 삶

    자기 상태 파일만 더러울 때는 `--autostash`로 넘어간다. 러너 자신이 쓴
    것이므로 잠시 치웠다 되붙여도 남의 작업을 흔들지 않는다.
    """
    if _dirty_paths(root):
        return False
    return _run(
        ["git", "pull", "--rebase", "--autostash", "origin", "main"],
        timeout=180, cwd=root,
    ).returncode == 0


#: 상태 파일을 올리는 브랜치. main이 아니다.
#:
#: main에 올리면 Railway가 재빌드한다. 배포 워크플로의 경로 필터(backend/·
#: frontend-next/)는 통과하지만, Railway 서비스가 **GitHub에 직접 연결돼 있어서**
#: main의 모든 커밋이 빌드를 띄운다 — 워크플로와 무관한 두 번째 배포 경로다.
#: 러너는 매 사이클 상태를 올리므로 자기 push로 자기 컨테이너를 죽였다.
#: 2026-07-30 실측 배포 이력: 09:11 09:20 09:24 09:35 09:39 09:51 09:54 10:10
#: 10:18 10:21 10:30 10:34 10:44 — 러너 사이클 간격과 정확히 같다. 그래서 20분
#: 예산도 못 채웠고 브랜치만 83개 쌓였다.
#:
#: 라이브 백엔드도 같이 재시작돼 도로망 캐시가 매번 비었다(응답 9초).
#: 공동작업자 쪽 문제도 여기서 온다 — 하루 150개 상태 커밋이 main에 쌓여
#: pull마다 충돌했다.
STATE_BRANCH = "agent-state"


def push_state(note: str) -> bool:
    """상태 파일만 러너 전용 브랜치에 올린다.

    컨테이너가 재시작하면 entrypoint가 `git reset --hard`를 하므로, 올려 두지
    않은 상태는 통째로 사라진다 — 실패 기록도 backlog 진행도 다 날아가고 러너가
    같은 일을 처음부터 다시 한다. 24시간 도는 물건에서 이건 치명적이다.
    그래서 올리기는 해야 하고, 다만 main이 아니어야 한다(STATE_BRANCH 참고).
    """
    _run(["git", "add", "docs/agent"], timeout=60)
    staged = _run(["git", "diff", "--cached", "--name-only"], timeout=60).stdout.strip()
    if not staged:
        return True
    proc = _run(["git", "commit", "-m", f"chore(agent): 상태 갱신 — {note}"], timeout=120)
    if proc.returncode != 0:
        return False
    pushed = _run(
        ["git", "push", "-f", "origin", f"HEAD:refs/heads/{STATE_BRANCH}"], timeout=300
    ).returncode == 0
    _collapse_local_state_commits(note)
    return pushed


def _collapse_local_state_commits(note: str) -> None:
    """로컬에 쌓인 상태 커밋을 하나로 접는다.

    상태 커밋은 origin/main에 안 올라가므로 로컬 main에 계속 남고, 매 사이클
    `pull --rebase`가 그걸 전부 다시 얹는다 — 하루 150개면 rebase가 갈수록
    느려지고 충돌 지점도 늘어난다. 항상 한 개만 남긴다.
    """
    if _run(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
            timeout=60).returncode != 0:
        return  # origin/main이 조상이 아니면(아직 pull 전) 건드리지 않는다
    ahead = _run(["git", "rev-list", "--count", "origin/main..HEAD"], timeout=60).stdout.strip()
    if not ahead.isdigit() or int(ahead) < 2:
        return
    _run(["git", "reset", "--soft", "origin/main"], timeout=60)
    _run(["git", "commit", "-m", f"chore(agent): 상태 — {note}"], timeout=120)


def fetch_remote_tasks(root: Path | None = None) -> list[dict]:
    """agent-state 브랜치의 task-state.json을 원격에서 그대로 읽는다.

    작업 트리는 건드리지 않는다(`git show`만 쓴다) — sync_repo가 이미 dirty
    판정을 관리하는 자리라, 여기서 checkout까지 하면 그 로직과 겹쳐 꼬인다.

    실패하면 빈 목록을 돌려준다. 병합은 있으면 좋은 것이지, 네트워크 문제로
    사이클 전체가 멈추면 안 된다(이 파일의 원칙 — I/O는 예외를 던지지 않는다).
    """
    root = root or REPO
    fetched = _run(
        ["git", "fetch", "origin", f"{STATE_BRANCH}:refs/remotes/origin/{STATE_BRANCH}"],
        timeout=180, cwd=root,
    )
    if fetched.returncode != 0:
        return []
    shown = _run(
        ["git", "show", f"origin/{STATE_BRANCH}:docs/agent/task-state.json"],
        timeout=60, cwd=root,
    )
    if shown.returncode != 0:
        return []
    try:
        return json.loads(shown.stdout).get("tasks") or []
    except json.JSONDecodeError:
        return []


def pull_remote_backlog(state: dict, root: Path | None = None) -> list[str]:
    """사이클마다 agent-state 브랜치를 합쳐, 사람이 재시작 없이 backlog를 넣을
    수 있게 한다.

    이게 없으면 두 가지가 다 깨진다:
    1. 사람이 agent-state 브랜치에 새 작업을 올려도 **돌고 있는 컨테이너는
       다음 재시작 전까지 못 읽는다** — restore_state_from_branch가 main()의
       부팅 시점에 딱 한 번만 불린다.
    2. 더 나쁘게는, push_state가 매 사이클 로컬 상태를 그대로 force-push한다
       — 병합 없이 두면 사람이 올린 작업이 다음 push에서 통째로 지워진다.

    이미 아는 작업은 원격 값으로 덮지 않는다(state.merge_new_tasks) — 로컬이
    진행 상태의 정본이다. 새로 받은 게 있을 때만 저장하고 곧바로 push해
    다음 정찰이 같은 걸 또 담지 않게 한다.
    """
    remote_tasks = fetch_remote_tasks(root)
    added = st.merge_new_tasks(state, remote_tasks)
    if added:
        st.save(state)
        push_state(f"사람이 넣은 backlog {len(added)}건 반영")
        print(f"[merge] 원격 backlog에서 {len(added)}건 받음: {', '.join(added)}")
    return added


def restore_state_from_branch() -> bool:
    """상태 브랜치에서 docs/agent를 되살린다.

    컨테이너는 main을 clone하고 시작한다. 상태는 이제 main에 없으므로, 이걸
    안 하면 재시작할 때마다 러너가 기억을 잃고 처음부터 다시 한다 — 상태를
    브랜치로 옮긴 대가를 여기서 갚아야 한다.
    """
    fetched = _run(
        ["git", "fetch", "origin", f"{STATE_BRANCH}:refs/remotes/origin/{STATE_BRANCH}"],
        timeout=180,
    )
    if fetched.returncode != 0:
        print(f"[state] {STATE_BRANCH} 브랜치가 아직 없다 — main의 상태로 시작한다")
        return False
    got = _run(
        ["git", "checkout", f"origin/{STATE_BRANCH}", "--", "docs/agent"], timeout=120
    )
    if got.returncode != 0:
        print(f"[state] 상태를 못 꺼냈다: {(got.stderr or got.stdout)[:200]}")
        return False
    print(f"[state] {STATE_BRANCH}에서 상태를 되살렸다")
    return True


def notify(title: str, body: str) -> bool:
    """사람에게 알린다 — GitHub 이슈. 러너가 Railway에 있어 다른 채널이 없다.

    이슈를 만들면 GitHub이 메일을 보내므로 별도 설정이 필요 없다. 실패해도
    러너를 멈추지 않는다 — 알림을 못 보낸 것이 일을 멈출 이유는 아니다.
    """
    try:
        proc = _run(["gh", "issue", "create", "--title", title, "--body", body], timeout=120)
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ---------------------------------------------------------------------------
# 작업공간 격리 — 동시에 둘이 일하려면 각자의 체크아웃이 있어야 한다
# ---------------------------------------------------------------------------

#: 푸시는 한 번에 하나씩. 둘이 동시에 main으로 밀면 하나는 non-fast-forward로
#: 튕기고, 재시도하는 동안 상대의 커밋 위에 rebase가 겹쳐 꼬인다.
_PUSH_LOCK = threading.Lock()


def worktree_for(task: dict, slot: int) -> Path:
    """이 작업이 쓸 체크아웃. 없으면 만든다.

    같은 저장소의 별도 작업 트리라 파일이 서로 안 보인다 — 두 에이전트가 같은
    파일을 열어도 서로를 덮어쓰지 않는다.

    node_modules는 심볼릭 링크로 빌려 온다. worktree마다 `npm ci`를 돌리면
    작업 하나에 몇 분이 더 붙고, 어차피 읽기만 한다.
    """
    root = REPO.parent / f"w{slot}"
    branch = f"auto/{task['id']}"

    if not (root / ".git").exists():
        # 남아 있던 등록 정보를 먼저 털고 만든다 — 앞 사이클이 죽었으면
        # worktree가 등록만 남아 add가 거절된다.
        _run(["git", "worktree", "prune"], timeout=60)
        _run(["git", "branch", "-D", branch], timeout=60)
        made = _run(["git", "worktree", "add", "-f", "-b", branch, str(root), "origin/main"],
                    timeout=300)
        if made.returncode != 0:
            return REPO  # 못 만들면 본 체크아웃에서 혼자 돈다
    else:
        _run(["git", "fetch", "origin", "main"], timeout=180, cwd=root)
        _run(["git", "checkout", "-B", branch, "origin/main"], timeout=120, cwd=root)
        _run(["git", "reset", "--hard", "origin/main"], timeout=120, cwd=root)
        _run(["git", "clean", "-fd"], timeout=120, cwd=root)

    modules = root / "frontend-next" / "node_modules"
    source = REPO / "frontend-next" / "node_modules"
    if source.exists() and not modules.exists():
        try:
            modules.symlink_to(source, target_is_directory=True)
        except OSError:
            pass  # 링크를 못 걸면 프론트 검사만 건너뛴다 — 백엔드 검증은 그대로 돈다

    # 이 링크가 커밋에 딸려 들어간 적이 있다. `git add -A`는 심볼릭 링크도 담고,
    # .gitignore의 `node_modules/`는 **디렉터리만** 매칭해서 링크를 못 막았다.
    # worktree의 exclude에 한 번 더 적어 두 겹으로 막는다.
    exclude = root / ".git" / "info" / "exclude"
    try:
        if exclude.parent.exists():
            existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
            if "frontend-next/node_modules" not in existing:
                exclude.write_text(
                    existing + "\nfrontend-next/node_modules\n", encoding="utf-8"
                )
    except OSError:
        pass
    return root


def run_task(task: dict, root: Path) -> tuple[str, str]:
    """작업 하나를 끝까지. (결과, 사유) — 결과는 done|failed|rate_limited.

    스레드에서 돌기 때문에 여기서는 상태를 만지지 않는다. 공유 dict를 여러
    스레드가 고치면 마지막에 쓴 쪽만 남는다 — 상태 반영은 호출부가 한 곳에서
    순서대로 한다.
    """
    ok, payload, stderr = invoke_claude(task, cwd=root)

    if is_rate_limited(payload, stderr):
        return "rate_limited", stderr
    if not ok:
        return "failed", stderr

    passed, detail = verify(root)
    if not passed:
        _run(["git", "checkout", "--", "."], timeout=120, cwd=root)
        return "failed", detail

    with _PUSH_LOCK:
        pushed, note = commit_and_push(task, root)
    return ("done", note) if pushed else ("failed", note)


# ---------------------------------------------------------------------------
# 설계 — 큰 목표를 계약→병렬→리뷰 그래프로 분해한다
# ---------------------------------------------------------------------------

ARCHITECT_PROMPT = """너는 이 저장소의 Architect다. 아래 **목표 하나**를 병렬
에이전트들이 서로 기다리지 않고 수행할 수 있는 작업 그래프로 분해하라.

목표: {goal}

규칙 (docs/OWNERSHIP.md의 소유권 지도를 반드시 읽고 따르라):
1. **계약 먼저.** 공유 계약(schemas.py·api.ts 등)을 바꿔야 하면 그것만 하는
   작업을 맨 앞에 하나 두고, 나머지 전부가 그것에 의존하게 하라. 계약 변경이
   필요 없으면 계약 작업을 만들지 마라.
2. **소유로 나눠라, 직업으로 나누지 마라.** 각 작업의 files는 소유권 지도의
   한 영역 안에 있어야 하고, 병렬로 돌 작업끼리는 files가 겹치면 안 된다.
   겹치면 depends_on으로 직렬화하라.
3. **각 작업은 스스로 검증한다.** 제목에 무엇을 하는지, why에 완료 기준
   (테스트 통과 포함)을 적어라.
4. **마지막에 리뷰 작업 하나.** 모든 워커 작업에 의존하며, 전체 테스트 통과와
   계약 위반·중복 구현 검사를 한다.
5. 작업은 계약·리뷰 포함 6개 이하. 잘게 썰수록 좋은 게 아니다 — 서로 안
   기다리는 최소 단위면 된다.

**JSON만** 출력하라. 설명·코드펜스 없이:
{{"tasks":[{{"title":"...","files":["..."],"priority":1,
"depends_on_titles":["앞 작업 제목"],"budget_min":90,"why":"완료 기준 포함"}}]}}
"""


def parse_plan(raw: str, state: dict) -> list[dict]:
    """Architect의 JSON을 실제 작업들로 바꾼다. 규칙 위반은 여기서 고친다.

    LLM 출력은 계약이 아니라 제안이다 — 병렬 작업끼리 files가 겹치면 뒤엣것을
    앞엣것에 의존시켜 직렬화하고, 없는 제목에 의존하면 그 의존을 버린다.
    검증을 프롬프트에만 맡기면 어긴 날 두 워커가 같은 파일을 덮는다.
    """
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        found = json.loads(raw[start:end + 1]).get("tasks") or []
    except json.JSONDecodeError:
        return []

    tasks: list[dict] = []
    title_to_id: dict[str, str] = {}
    for item in found[:6]:
        title = str(item.get("title") or "").strip()
        if not title or title in title_to_id:
            continue
        task_id = st.new_id(state)
        files = [str(f) for f in (item.get("files") or [])][:8]
        deps = [
            title_to_id[t] for t in (item.get("depends_on_titles") or [])
            if t in title_to_id
        ]
        # 병렬(의존 무관) 형제와 files가 겹치면 직렬화 — 소유권 규칙의 강제.
        for prev in tasks:
            if prev["id"] in deps:
                continue
            if set(files) & set(prev["files"]):
                deps.append(prev["id"])
        task = {
            "id": task_id, "title": title, "status": "pending", "owner": None,
            "priority": int(item.get("priority") or 5),
            "files": files, "branch": None, "attempts": 0,
            "blocked_reason": None,
            "why": str(item.get("why") or "").strip(),
            "depends_on": deps,
            "budget_min": min(int(item.get("budget_min") or 90), 240),
        }
        state.setdefault("tasks", []).append(task)
        tasks.append(task)
        title_to_id[title] = task_id
    return tasks


def plan_goal(goal: str, state: dict) -> list[str]:
    """목표 하나를 Architect에게 분해시켜 backlog에 넣는다. 실패하면 빈 목록."""
    cmd = [
        claude_bin(), "-p", ARCHITECT_PROMPT.format(goal=goal),
        "--allowedTools", "Read,Glob,Grep",
        "--output-format", "json",
    ]
    proc = _run(cmd, timeout=600)
    if proc.returncode != 0:
        print(f"[architect] 실패: {(proc.stderr or proc.stdout or '')[:300]}")
        return []
    try:
        result = json.loads(proc.stdout).get("result") or ""
    except (json.JSONDecodeError, AttributeError):
        return []
    made = parse_plan(result, state)
    for t in made:
        deps = f" (선행 {','.join(t['depends_on'])})" if t.get("depends_on") else ""
        print(f"[architect] {t['id']} {t['title'][:40]}{deps}")
    return [t["id"] for t in made]


# ---------------------------------------------------------------------------
# 정찰 — 할 일이 떨어지면 새로 찾는다
# ---------------------------------------------------------------------------

#: 한 번에 새로 담을 수 있는 작업 수. 무제한이면 정찰이 backlog를 쓰레기로
#: 채운다 — 적게 담고 자주 도는 편이 낫다.
SCOUT_MAX_NEW = 3

SCOUT_PROMPT = """이 저장소에서 **다음에 할 가치가 있는 작업**을 찾아라.

지금까지 고친 것 대부분은 한 유형이었다: 엔진 계산은 맞는데 **화면이 그 계산을
반대로 말하거나, 없는 값을 말하거나, 못 믿을 값을 그대로 점수에 넣는** 자리.
같은 유형을 우선 찾되, 그것만 찾지는 마라.

특히 이 프로젝트의 목표는 "AI 에이전트가 만들어 둔 모든 기능을 조작할 수 있게"
하는 것이다. backend/app/routers/ 의 엔드포인트 중 backend/app/routers/agent.py
의 _TOOLS에 없는 것이 있으면 그게 최우선이다.

규칙:
- 이미 docs/agent/BACKLOG.md에 있는 것은 다시 담지 마라.
- 사람 승인이 필요한 것(운영 데이터 변경, 비밀키, 결제, 대규모 삭제)은 담지 마라.
- 억지로 만들지 마라. 진짜 없으면 빈 배열을 내라. 없는 일을 지어내는 것이
  아무것도 안 하는 것보다 나쁘다.

**JSON만** 출력하라. 설명·머리말·코드펜스 없이:
{"tasks":[{"title":"한 줄 제목","files":["고칠 파일 경로"],"priority":10,"why":"왜 필요한지 한 줄"}]}
"""


def scout(limit: int = SCOUT_MAX_NEW) -> list[dict]:
    """다음에 할 일을 찾아 온다. 실패하면 빈 목록 — 정찰 실패로 러너를 세우지 않는다."""
    cmd = [
        claude_bin(), "-p", SCOUT_PROMPT,
        "--allowedTools", "Read,Glob,Grep",
        "--output-format", "json",
    ]
    proc = _run(cmd, timeout=600)
    if proc.returncode != 0:
        print(f"[scout] 실패: {(proc.stderr or proc.stdout or '')[:300]}")
        return []
    try:
        result = json.loads(proc.stdout).get("result") or ""
    except (json.JSONDecodeError, AttributeError):
        return []
    # 모델이 코드펜스를 붙이는 경우가 있다. JSON 본문만 꺼낸다.
    start, end = result.find("{"), result.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        found = json.loads(result[start:end + 1]).get("tasks") or []
    except json.JSONDecodeError:
        return []
    clean = []
    for t in found[:limit]:
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        clean.append({
            "title": title,
            "files": [str(f) for f in (t.get("files") or [])][:8],
            "priority": int(t.get("priority") or 50),
            "why": str(t.get("why") or "").strip(),
        })
    return clean


#: 사람이 직접 넣은 지시는 이 값보다 우선순위가 높다(숫자가 작다). 정찰이 담는
#: 일은 그 아래로 내려, 큰 작업이 자잘한 것에 계속 밀리지 않게 한다.
SCOUT_MIN_PRIORITY = 10

#: 이 비율 이상 비슷한 제목은 같은 작업으로 본다(글자 2-그램 자카드 유사도).
#: 정찰은 매번 새 LLM 호출이라 같은 발견을 마침표·불릿·따옴표·공백처럼 사소하게
#: 다르게 적는다 — 완전일치만 보면 이런 사소한 재표현이 계속 새 작업으로 쌓여,
#: 병합 안 된 같은 작업이 무한히 재발굴된다.
#:
#: 문턱을 높게 잡은 이유: 실제로 붙여 보면 "화면에 값이 안 나온다" 같은 같은
#: 틀을 쓰는 서로 다른 카드/필드 이야기(예: 혼잡도 카드 vs 사고이력 카드)조차
#: 글자 단위로는 0.6~0.7까지 겹친다 — 낮은 문턱은 진짜 다른 작업을 하나로
#: 뭉갠다. 반대로 마침표·공백·조사 같은 사소한 차이는 0.9 이상 겹친다. 그
#: 틈 사이인 0.85로 잡아, "사소한 재표현"만 잡고 "다른 대상, 같은 문장 틀"은
#: 다른 작업으로 남긴다.
TITLE_DUPLICATE_RATIO = 0.85


def _normalize_title(title: str) -> str:
    """비교용으로 다듬는다 — 공백·구두점·대소문자 차이는 같은 제목으로 본다."""
    normalized = title.strip().lower()
    normalized = re.sub(r"[\s\-_·,.!?:;'\"()\[\]{}/\\]+", " ", normalized)
    return normalized.strip()


def _char_bigrams(title: str) -> set[str]:
    normalized = _normalize_title(title)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[i:i + 2] for i in range(len(normalized) - 1)}


def _title_similarity(a: str, b: str) -> float:
    """두 제목의 유사도(0~1)를 돌려준다. 완전일치면 1.0.

    단어 단위 비교 대신 글자 2-그램을 쓴다 — 한국어는 조사가 붙어 활용형이
    바뀌므로("카드에" vs "카드가") 단어를 그대로 비교하면 같은 말도 다르게 잡힌다.
    """
    bigrams_a, bigrams_b = _char_bigrams(a), _char_bigrams(b)
    if not bigrams_a or not bigrams_b:
        return 1.0 if _normalize_title(a) == _normalize_title(b) else 0.0
    return len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b)


def _find_duplicate(item: dict, existing_tasks: list[dict]) -> dict | None:
    """이미 담긴 작업 중 이 정찰 결과와 같은 일로 보이는 것을 찾는다. 없으면 None."""
    for task in existing_tasks:
        if _title_similarity(item["title"], task["title"]) >= TITLE_DUPLICATE_RATIO:
            return task
    return None


def add_scouted(state: dict, found: list[dict], active_files: set[str] | None = None) -> list[str]:
    """찾은 일을 backlog에 담는다. 같은 문제로 보이면 건너뛴다.

    "같다"의 기준은 둘이다 — 하나만 보면 각각 새는 구멍이 있다:
    1. **제목 유사도**(_find_duplicate): 정찰은 매번 새 LLM 호출이라 같은
       문제를 사소하게 다르게 적는다. 완전일치만 보면 재표현이 전부 새
       작업으로 쌓인다.
    2. **파일 범위 겹침**(active_files): 아직 main에 안 들어간 작업들의 파일
       범위와 겹치면 같은 자리를 가리키는 것이다 — 실제로 industry_profiles
       검색 하나가 제목을 바꿔 가며 6번 넘게 중복 등록됐다.

    정찰이 담는 일은 우선순위를 사람 지시 아래로 내린다. 정찰은 "기계적으로
    찾을 수 있는 것"(응답 필드가 화면에 없다 같은)만 찾는데 그런 건 수백 개라,
    우선순위를 같이 주면 사람이 시킨 큰 작업이 영영 차례가 안 온다.
    """
    existing_tasks = list(state.get("tasks", []))
    active_files = active_files or set()
    added = []
    for item in found:
        if _find_duplicate(item, existing_tasks) is not None:
            continue
        if active_files and set(item["files"]) & active_files:
            continue
        task_id = st.new_id(state)
        new_task = {
            "id": task_id, "title": item["title"], "status": "pending",
            "owner": None, "priority": max(item["priority"], SCOUT_MIN_PRIORITY),
            "files": item["files"],
            "branch": None, "attempts": 0, "blocked_reason": None,
            "why": item["why"],
        }
        state.setdefault("tasks", []).append(new_task)
        existing_tasks.append(new_task)
        added.append(task_id)
    return added


# ---------------------------------------------------------------------------
# 사이클
# ---------------------------------------------------------------------------


def block_dead_dependents(state: dict) -> list[str]:
    """선행이 막힌 작업을 같이 막는다.

    안 그러면 영영 안 집히는 pending으로 backlog에 남아, 사람은 "대기 중"으로
    읽고 러너는 건너뛴다. 아무도 안 보는 자리에서 조용히 죽는 게 제일 나쁘다.
    """
    blocked = []
    for task in state.get("tasks", []):
        if task.get("status") != "pending":
            continue
        reason = st.dep_dead(state, task)
        if reason:
            st.set_status(state, task["id"], "blocked", reason=reason)
            blocked.append(task["id"])
            print(f"[blocked] {task['id']}: {reason}")
    return blocked


#: 텔레그램 봇이 받은 지시를 적어 두는 곳. 봇과 러너는 이 파일로만 대화한다.
#: 상태 파일의 **작성자는 러너 하나뿐**이어야 한다 — 봇도 같이 쓰면 러너가 방금
#: in_progress로 바꾼 값을 봇이 옛 값으로 덮는다.
INBOX = Path(os.environ.get("AGENT_INBOX") or "/workspace/agent-inbox.jsonl")


def drain_inbox(state: dict, path: Path | None = None) -> list[str]:
    """봇이 받아 둔 지시를 작업·명령으로 바꾸고 inbox를 비운다.

    한 줄이 깨져 있어도 나머지는 처리한다 — 지시 하나 때문에 나머지를 통째로
    잃는 게 더 나쁘다.
    """
    path = path or INBOX
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if not raw.strip():
        return []

    notes: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = entry.get("kind")

        if kind == "goal":
            # 큰 목표는 바로 작업이 아니라 **계획 작업**이 된다 — 다음 사이클에
            # Architect가 분해한다. 봇 프로세스에서 바로 분해하지 않는 이유:
            # claude 호출은 러너의 예산·한도 관리 안에서 돌아야 한다.
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            task_id = st.new_id(state)
            state.setdefault("tasks", []).append({
                "id": task_id, "title": f"[설계] {title}", "status": "pending",
                "owner": None, "priority": 0, "files": [], "branch": None,
                "attempts": 0, "blocked_reason": None, "kind": "plan",
                "goal": title, "why": "Architect가 계약→병렬→리뷰로 분해한다",
                "depends_on": [], "budget_min": 15,
            })
            notes.append(f"{task_id} 설계 대기: {title[:40]}")

        elif kind == "task":
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            task_id = st.new_id(state)
            reason = entry.get("needs_approval")
            state.setdefault("tasks", []).append({
                "id": task_id, "title": title,
                # 승인이 필요한 일은 pending으로 넣지 않는다 — 넣는 순간 다음
                # 사이클이 집어 간다. 잠근 채로 사람을 기다린다.
                "status": "awaiting_approval" if reason else "pending",
                "owner": None,
                # 사람이 직접 시킨 일은 정찰이 담은 것보다 앞선다.
                "priority": 1, "files": [], "branch": None, "attempts": 0,
                "blocked_reason": None, "needs_approval": reason,
                "why": "텔레그램으로 받은 지시",
                "depends_on": [], "budget_min": 90,
            })
            notes.append(f"{task_id} 담음: {title[:40]}")

        elif kind in ("pause", "resume"):
            state["paused"] = kind == "pause"
            notes.append("멈춤" if kind == "pause" else "재개")

        elif kind in ("approve", "reject"):
            task = st.find(state, str(entry.get("arg") or "").strip().upper())
            if task is None or task.get("status") != "awaiting_approval":
                continue
            if kind == "approve":
                task["status"] = "pending"
                task["needs_approval"] = None
                notes.append(f"{task['id']} 승인됨")
            else:
                task["status"] = "blocked"
                task["blocked_reason"] = "사람이 취소함"
                notes.append(f"{task['id']} 취소됨")

    try:
        path.write_text("", encoding="utf-8")
    except OSError:
        pass
    return notes


def run_cycle(state: dict, *, dry_run: bool = False) -> str:
    """한 사이클. 다음에 얼마나 쉴지를 정하는 결과 문자열을 돌려준다."""
    st.roll_day(state)
    state["heartbeat"] = st.now_kst()
    if not dry_run:
        for note in drain_inbox(state):
            print(f"[inbox] {note}")
    block_dead_dependents(state)

    decision = decide(state)

    if decision.action == "scout":
        if dry_run:
            print("[scout] (dry-run) 새 작업을 찾지 않는다")
            return "dry-run"
        found = scout()
        active_files = unresolved_files(state)
        added = add_scouted(state, found, active_files)
        st.save(state)
        if added:
            push_state(f"정찰 — {len(added)}건 담음")
            print(f"[scout] {len(added)}건 담음: {', '.join(added)}")
            return "done"  # 바로 다음 사이클에 집는다
        print("[scout] 담을 만한 일을 못 찾았다")
        return "idle"

    if decision.action != "work":
        st.save(state)
        print(f"[{decision.action}] {decision.reason}")
        return decision.action

    # 계획 작업은 혼자, Architect 경로로 돈다 — 분해가 끝나야 병렬이 생긴다.
    plan_task = next(
        (t for t in st.ready_tasks(state) if t.get("kind") == "plan"), None
    )
    if plan_task is not None:
        if dry_run:
            print(f"[architect] (dry-run) {plan_task['goal'][:40]}")
            return "dry-run"
        st.set_status(state, plan_task["id"], "in_progress")
        st.save(state)
        made = plan_goal(plan_task["goal"], state)
        if made:
            st.set_status(state, plan_task["id"], "done")
        else:
            st.set_status(state, plan_task["id"], "failed",
                          reason="Architect가 계획을 내지 못했다")
        st.save(state)
        push_state(f"설계 — {len(made)}건 분해")
        return "done"

    # --- 여기부터 동시 실행 -------------------------------------------------
    limit = int((state.get("limits") or {}).get("max_concurrent_agents", 3)) - 1
    batch = pick_parallel(state, max(1, limit))
    if dry_run:
        print("[work] (dry-run) " + ", ".join(f"{t['id']} {t['title'][:30]}" for t in batch))
        return "dry-run"

    for task in batch:
        st.set_status(state, task["id"], "in_progress")
        task["owner"] = f"builder{batch.index(task) + 1}"
    st.save(state)
    print("[work] " + " | ".join(f"{t['id']} {t['title'][:40]}" for t in batch))

    roots = {t["id"]: worktree_for(t, i + 1) for i, t in enumerate(batch)}
    results: dict[str, tuple[str, str]] = {}
    with cf.ThreadPoolExecutor(max_workers=len(batch)) as pool:
        futures = {pool.submit(run_task, t, roots[t["id"]]): t for t in batch}
        for future in cf.as_completed(futures):
            task = futures[future]
            try:
                results[task["id"]] = future.result()
            except Exception as exc:  # 스레드에서 터져도 러너는 살아 있어야 한다
                results[task["id"]] = ("failed", f"작업 스레드가 예외로 끝났다: {exc}")

    # 상태 반영은 한 곳에서 순서대로. 스레드가 각자 고치면 마지막 쓴 것만 남는다.
    outcomes = []
    for task in batch:
        outcome, note = results.get(task["id"], ("failed", "결과가 없다"))
        if outcome == "rate_limited":
            task["status"] = "pending"
            task["owner"] = None
        elif outcome == "done":
            st.set_status(state, task["id"], "done")
        else:
            st.set_status(state, task["id"], "failed", reason=note)
        outcomes.append(outcome)
        print(f"[{outcome}] {task['id']} — {note[:400]}")

    st.save(state)
    push_state(f"{len(batch)}건 처리")

    if all(o == "rate_limited" for o in outcomes):
        budget = state.setdefault("budget", {})
        if budget.get("notify_on_limit") and not budget.get("notified_today"):
            notify(
                "구독 사용량 한도 도달 — 자율 러너 대기 중",
                "한도가 풀리면 자동으로 재개합니다. 추가 비용은 쓰지 않습니다.\n"
                "멈추려면: `python backend/tools/agent/pause.py on`\n",
            )
            budget["notified_today"] = True
            st.save(state)
        return "rate_limited"
    return "done" if any(o == "done" for o in outcomes) else "failed"


_SLEEP = {
    "done": 5,
    "scout": 5,
    "failed": 60,
    "idle": IDLE_SLEEP_S,
    "paused": 60,
    "halted": IDLE_SLEEP_S,
    "rate_limited": RATE_LIMIT_SLEEP_S,
}


#: 이 파일들이 바뀌면 러너가 자기를 다시 띄운다. 러너 본체 + 상태 모듈.
_OWN_SOURCES = ("runner.py", "state.py")


def own_code_fingerprint() -> str:
    """지금 돌고 있는 러너 코드의 지문."""
    here = Path(__file__).resolve().parent
    parts = []
    for name in _OWN_SOURCES:
        path = here / name
        parts.append(f"{name}:{path.stat().st_mtime_ns if path.exists() else 0}")
    return "|".join(parts)


def restart_if_own_code_changed(before: str) -> None:
    """러너 코드가 바뀌었으면 자기를 다시 띄운다.

    sync_repo가 매 회차 새 코드를 당겨오지만 파이썬은 시작할 때 읽은 모듈을
    계속 쓴다. 그래서 러너 자체를 고쳐 push해도 컨테이너에서는 옛 코드가
    영영 돈다 — 사람이 Railway에서 손으로 재배포해 줘야만 반영된다.
    24시간 무인 운영에서 "사람이 눌러 줘야 반영되는 것"은 반영되지 않는 것이다.

    사이클 사이에서만 부른다. 작업 도중에 갈아타면 worktree가 어중간하게
    남는다.
    """
    if own_code_fingerprint() == before:
        return
    print("[restart] 러너 코드가 바뀌었다 — 새 코드로 다시 띄운다", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())] + sys.argv[1:])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="한 사이클만")
    parser.add_argument("--dry-run", action="store_true", help="고르기만 하고 실행 안 함")
    args = parser.parse_args(argv[1:])

    if not args.dry_run:
        # 상태를 읽기 전에 브랜치에서 꺼낸다. 순서가 반대면 clone된 main의 옛
        # 상태를 정본으로 읽고, 그 위에 덮어써서 밤새 한 일이 지워진다.
        restore_state_from_branch()

    state = st.load()
    revived = st.resume(state)
    if revived:
        print(f"중단됐던 작업을 되살렸다: {', '.join(revived)}")
    st.save(state)

    if api_key_would_be_billed(dict(os.environ), state):
        print("경고: API 폴백이 꺼져 있는데 ANTHROPIC_API_KEY가 환경에 있다. "
              "이대로 두면 구독이 아니라 API로 과금된다.", file=sys.stderr)
        return 2

    while True:
        # 상태를 읽기 **전에** 당겨온다. 순서가 반대면 방금 올린 pause를 한
        # 사이클 늦게 읽는다.
        fingerprint = own_code_fingerprint()
        sync_repo()
        if not (args.once or args.dry_run):
            # 당겨온 것 중에 러너 자신이 있으면 여기서 갈아탄다. 작업을 집기
            # 전이라 중간에 끊기는 게 없다.
            restart_if_own_code_changed(fingerprint)
        state = st.load()
        if not args.dry_run:
            pull_remote_backlog(state)
        result = run_cycle(state, dry_run=args.dry_run)
        if args.once or args.dry_run:
            return 0
        time.sleep(_SLEEP.get(result, 60))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
