"""자율 개발 상태 — 정본 읽기·쓰기와 사람이 읽을 마크다운 렌더링.

`docs/agent/task-state.json`이 정본이고, 마크다운은 여기서 만들어진 사본이다.
진실을 두 곳에 두면 반드시 어긋나므로 방향은 한쪽으로만 흐른다:

    task-state.json  ──▶  BACKLOG.md · STATUS.md · FAILURES.md · HANDOFF.md

러너가 매 사이클 `save()`를 부르면 마크다운도 같이 갱신된다. 사람이 마크다운을
고쳐도 다음 사이클에 덮어써진다 — 의도한 동작이다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re
from typing import Any

KST = timezone(timedelta(hours=9))

#: backend/tools/agent/state.py → 저장소 루트
REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "docs" / "agent"
STATE_PATH = AGENT_DIR / "task-state.json"

#: awaiting_approval은 러너가 **절대 뽑지 않는** 상태다(ready_tasks 참고).
#: 사람 승인이 필요한 일을 문서 규칙으로만 막으면 지키는지 확인할 방법이 없다 —
#: 상태로 두면 뽑는 코드가 한 곳뿐이라 우회할 자리가 없다.
STATUSES = ("pending", "in_progress", "done", "failed", "blocked", "awaiting_approval")


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def load() -> dict[str, Any]:
    """정본을 읽는다. 없으면 빈 상태를 만든다(첫 실행)."""
    if not STATE_PATH.exists():
        return {
            "version": 1,
            "updated_at": now_kst(),
            "heartbeat": None,
            "paused": False,
            "budget": {
                "daily_limit_usd": 0.5,
                "spent_today_usd": 0.0,
                "day": None,
                "notify_on_limit": True,
                "notified_today": False,
            },
            "auth": {"mode": "subscription", "api_fallback": True},
            "limits": {
                "max_concurrent_agents": 3,
                "max_delegation_depth": 1,
                "agent_timeout_s": 1200,
                "max_attempts": 2,
                "halt_after_consecutive_failures": 3,
            },
            "consecutive_failures": 0,
            "tasks": [],
            "runs": [],
        }
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save(state: dict[str, Any], *, render: bool = True) -> None:
    """정본을 쓰고, 사람이 읽을 마크다운을 다시 만든다.

    임시 파일에 쓴 뒤 바꿔치기한다 — 러너가 쓰는 도중에 죽어도 반쪽짜리 JSON이
    남으면 다음 부팅에서 상태를 통째로 잃는다.
    """
    state["updated_at"] = now_kst()
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)
    if render:
        render_markdown(state)


# ---------------------------------------------------------------------------
# 예산 — 상한에 닿으면 멈추고 한 번만 알린다
# ---------------------------------------------------------------------------


def _today() -> str:
    return datetime.now(KST).date().isoformat()


def roll_day(state: dict[str, Any]) -> bool:
    """날짜가 바뀌었으면 지출과 알림 여부를 초기화한다. 바뀌었으면 True."""
    budget = state.setdefault("budget", {})
    today = _today()
    if budget.get("day") == today:
        return False
    budget["day"] = today
    budget["spent_today_usd"] = 0.0
    budget["notified_today"] = False
    return True


def budget_exhausted(state: dict[str, Any]) -> bool:
    """API 예산을 다 썼는가. 상한이 없으면(None) 절대 소진되지 않는다."""
    budget = state.get("budget") or {}
    limit = budget.get("daily_limit_usd")
    if limit is None:
        return False
    return float(budget.get("spent_today_usd") or 0) >= float(limit)


def add_spend(state: dict[str, Any], usd: float) -> None:
    """API로 쓴 비용을 더한다. 구독으로 돈 작업은 0으로 기록해도 된다."""
    roll_day(state)
    budget = state.setdefault("budget", {})
    budget["spent_today_usd"] = round(float(budget.get("spent_today_usd") or 0) + float(usd), 4)


# ---------------------------------------------------------------------------
# 작업 선택과 재개
# ---------------------------------------------------------------------------


def resume(state: dict[str, Any]) -> list[str]:
    """부팅 시 정리. 되돌린 작업 id를 돌려준다.

    죽은 시점에 in_progress였던 작업은 worktree에 편집이 남아 있으므로 버리지
    않고 pending으로 되돌린다. 다만 재시도 한도를 넘긴 것은 blocked로 옮긴다 —
    같은 실패를 무한히 반복하는 게 가장 비싼 실패 방식이다.
    """
    revived: list[str] = []
    max_attempts = (state.get("limits") or {}).get("max_attempts", 2)
    for task in state.get("tasks", []):
        if task.get("status") != "in_progress":
            continue
        if int(task.get("attempts") or 0) >= max_attempts:
            task["status"] = "blocked"
            task["blocked_reason"] = f"재시도 {task['attempts']}회 실패 — 사람 확인 필요"
        else:
            task["status"] = "pending"
            revived.append(task["id"])
        task["owner"] = None
    return revived


def new_id(state: dict[str, Any]) -> str:
    """다음 작업 번호. 정찰이 담는 일에 붙인다.

    기존 번호 중 최댓값+1을 쓴다 — 길이로 세면 지운 작업이 있을 때 번호가 겹쳐,
    같은 id를 가진 작업이 둘이 되고 상태 갱신이 엉뚱한 쪽에 걸린다.
    """
    nums = []
    for t in state.get("tasks", []):
        m = re.search(r"(\d+)$", str(t.get("id") or ""))
        if m:
            nums.append(int(m.group(1)))
    return f"T-{(max(nums) + 1) if nums else 1:03d}"


def deps_ready(state: dict[str, Any], task: dict[str, Any]) -> bool:
    """이 작업의 선행 작업이 전부 끝났는가.

    선행이 실패했는데 뒤엣것을 돌리면 헛일을 한다. 예: "프론트가 history를
    보낸다"는 "백엔드가 history를 받는다"가 되기 전엔 보내 봐야 무시된다.
    그런데 테스트는 통과하니 done으로 찍히고, 아무도 안 고친 채 남는다.
    """
    for dep_id in task.get("depends_on") or []:
        dep = find(state, dep_id)
        if dep is None or dep.get("status") != "done":
            return False
    return True


def dep_dead(state: dict[str, Any], task: dict[str, Any]) -> str | None:
    """선행이 영영 안 끝날 상황이면 그 사유. 아니면 None.

    선행이 blocked면 뒤엣것도 영영 pending으로 남아 backlog에 쌓인다.
    같이 blocked로 내려 사람이 볼 수 있게 한다.
    """
    for dep_id in task.get("depends_on") or []:
        dep = find(state, dep_id)
        if dep is None:
            return f"선행 작업 {dep_id}이 없다"
        if dep.get("status") == "blocked":
            return f"선행 작업 {dep_id}이 막혀 있다: {dep.get('blocked_reason') or '사유 미기록'}"
    return None


def ready_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    """지금 집을 수 있는 작업들. 우선순위 → id 순."""
    ready = [
        t for t in state.get("tasks", [])
        if t.get("status") == "pending" and deps_ready(state, t)
    ]
    return sorted(ready, key=lambda t: (t.get("priority", 99), t.get("id", "")))


def next_task(state: dict[str, Any]) -> dict[str, Any] | None:
    """다음에 할 작업. 없으면 None.

    멈춰 있거나(paused) 연속 실패가 한도를 넘었으면 아무것도 주지 않는다.
    """
    if state.get("paused"):
        return None
    limits = state.get("limits") or {}
    halt = limits.get("halt_after_consecutive_failures", 3)
    if int(state.get("consecutive_failures") or 0) >= halt:
        return None
    ready = ready_tasks(state)
    return ready[0] if ready else None


def find(state: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    return next((t for t in state.get("tasks", []) if t.get("id") == task_id), None)


def merge_new_tasks(state: dict[str, Any], remote_tasks: list[dict[str, Any]]) -> list[str]:
    """원격(agent-state 브랜치) backlog에서 이 상태가 모르는 작업만 더한다.

    사람이 러너를 재시작시키지 않고 backlog를 넣으려면 agent-state 브랜치에
    task-state.json을 직접 얹는 수밖에 없다(러너가 매 사이클 force-push하는
    자리라 다른 방법이 없다). 그런데 이미 아는 작업(같은 id)까지 원격 값으로
    덮으면, 러너가 방금 in_progress·done으로 바꾼 상태가 원격의 옛 값(대개
    pending)으로 되돌아간다 — 진행 상태의 정본은 항상 로컬이다. 그래서 id가
    겹치지 않는, 진짜 새 작업만 더한다.
    """
    known_ids = {t.get("id") for t in state.get("tasks", [])}
    added: list[str] = []
    for task in remote_tasks:
        task_id = task.get("id")
        if not task_id or task_id in known_ids:
            continue
        state.setdefault("tasks", []).append(task)
        known_ids.add(task_id)
        added.append(task_id)
    return added


def set_status(
    state: dict[str, Any], task_id: str, status: str, *, reason: str | None = None
) -> None:
    """작업 상태를 바꾸고 연속 실패 카운터를 관리한다."""
    if status not in STATUSES:
        raise ValueError(f"알 수 없는 상태: {status}")
    task = find(state, task_id)
    if task is None:
        raise KeyError(task_id)
    task["status"] = status
    if status == "in_progress":
        task["attempts"] = int(task.get("attempts") or 0) + 1
    if status == "blocked":
        task["blocked_reason"] = reason
    if status == "failed":
        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
        # 사유를 여기 적어 두지 않으면 로그에만 남는다. 로그 창은 넘어가고
        # 컨테이너는 재시작하므로, 몇 분 뒤엔 "왜 실패했는지"를 아무도 모른다.
        # 실제로 세 번 겪었다 — 실패는 이유와 함께 저장소에 남아야 한다.
        if reason:
            task["last_error"] = reason[:2000]
    if status == "done":
        state["consecutive_failures"] = 0
        task["owner"] = None


# ---------------------------------------------------------------------------
# 사람이 읽을 사본
# ---------------------------------------------------------------------------

_STATUS_LABEL = {
    "pending": "대기",
    "in_progress": "진행 중",
    "done": "완료",
    "failed": "실패",
    "blocked": "막힘",
    "awaiting_approval": "승인 대기",
}


def _label(status: str) -> str:
    """상태의 한글 이름. 모르는 상태면 원문 그대로 쓴다.

    STATUSES에 새 상태를 넣고 이 표를 깜빡하면 KeyError로 렌더링이 통째로
    죽는다 — 실제로 awaiting_approval을 넣었을 때 그랬다. 상태 하나 늘린 대가가
    상태 파일 전체를 못 쓰게 되는 것이어서는 안 된다.
    """
    return _STATUS_LABEL.get(status, status)


def _by(state: dict[str, Any], status: str) -> list[dict[str, Any]]:
    return [t for t in state.get("tasks", []) if t.get("status") == status]


def render_markdown(state: dict[str, Any]) -> None:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    header = "<!-- 이 파일은 task-state.json에서 자동 생성된다. 직접 고치지 말 것. -->\n\n"

    pending = sorted(_by(state, "pending"), key=lambda t: (t.get("priority", 99), t["id"]))
    lines = [header, "# Backlog\n\n"]
    if pending:
        lines.append("| 우선 | id | 작업 | 소유 파일 |\n|---|---|---|---|\n")
        for t in pending:
            files = ", ".join(t.get("files") or []) or "—"
            lines.append(f"| {t.get('priority','')} | {t['id']} | {t['title']} | `{files}` |\n")
    else:
        lines.append("대기 중인 작업이 없다.\n")
    blocked = _by(state, "blocked")
    if blocked:
        lines.append("\n## 막힌 작업 (사람 확인 필요)\n\n")
        for t in blocked:
            lines.append(f"- **{t['id']} {t['title']}** — {t.get('blocked_reason') or '사유 미기록'}\n")
    (AGENT_DIR / "BACKLOG.md").write_text("".join(lines), encoding="utf-8")

    budget = state.get("budget") or {}
    running = _by(state, "in_progress")
    status_lines = [
        header,
        "# 상태\n\n",
        f"- 마지막 갱신: {state.get('updated_at') or '—'}\n",
        f"- heartbeat: {state.get('heartbeat') or '—'}\n",
        f"- 멈춤(kill switch): {'예' if state.get('paused') else '아니오'}\n",
        f"- 연속 실패: {state.get('consecutive_failures', 0)}\n",
        f"- 오늘 API 지출: ${budget.get('spent_today_usd', 0)} / "
        f"{'상한 없음' if budget.get('daily_limit_usd') is None else '$' + str(budget['daily_limit_usd'])}\n",
        f"- 인증: {(state.get('auth') or {}).get('mode', '?')}"
        f" (API 폴백 {'켬' if (state.get('auth') or {}).get('api_fallback') else '끔'})\n\n",
        "## 집계\n\n",
    ]
    for key in STATUSES:
        status_lines.append(f"- {_label(key)}: {len(_by(state, key))}건\n")
    if running:
        status_lines.append("\n## 지금 진행 중\n\n")
        for t in running:
            status_lines.append(f"- {t['id']} {t['title']} (담당 {t.get('owner') or '?'})\n")
    (AGENT_DIR / "STATUS.md").write_text("".join(status_lines), encoding="utf-8")

    failed = _by(state, "failed")
    fail_lines = [header, "# 실패 기록\n\n"]
    if failed:
        fail_lines.append("같은 실패를 반복하지 않으려고 남긴다.\n\n")
        for t in failed:
            fail_lines.append(f"- **{t['id']} {t['title']}** — 시도 {t.get('attempts', 0)}회\n")
            err = (t.get("last_error") or "").strip()
            if err:
                # 원문 그대로 넣는다. 요약하면 정작 필요한 줄이 빠진다.
                fail_lines.append("\n  ```\n")
                for line in err.splitlines()[:20]:
                    fail_lines.append(f"  {line}\n")
                fail_lines.append("  ```\n\n")
    else:
        fail_lines.append("실패한 작업이 없다.\n")
    (AGENT_DIR / "FAILURES.md").write_text("".join(fail_lines), encoding="utf-8")

    done = _by(state, "done")
    hand = [
        header,
        "# 인수인계 — 사람이 읽는 요약\n\n",
        f"마지막 갱신 {state.get('updated_at') or '—'}\n\n",
        f"- 완료 {len(done)}건 · 대기 {len(pending)}건 · 막힘 {len(blocked)}건 · 실패 {len(failed)}건\n",
    ]
    if done:
        hand.append("\n## 끝낸 것 (최근 10건)\n\n")
        for t in done[-10:]:
            hand.append(f"- {t['id']} {t['title']}\n")
    if blocked:
        hand.append("\n## 결정이 필요한 것\n\n")
        for t in blocked:
            hand.append(f"- **{t['title']}** — {t.get('blocked_reason') or '사유 미기록'}\n")
    (AGENT_DIR / "HANDOFF.md").write_text("".join(hand), encoding="utf-8")
