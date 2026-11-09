"""자율 러너의 상태 관리 — 재개·예산·멈춤.

이 로직이 틀리면 24시간 내내 조용히 잘못 돈다. 사람이 안 보고 있으므로
"틀렸는데 그럴싸하게 계속 도는 것"이 가장 나쁜 실패다.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "agent"))

import state as agent_state  # noqa: E402


@pytest.fixture
def st(tmp_path, monkeypatch):
    """정본을 임시 디렉터리로 옮겨 실제 docs/agent를 건드리지 않는다."""
    monkeypatch.setattr(agent_state, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(agent_state, "STATE_PATH", tmp_path / "task-state.json")
    return agent_state.load()


def _task(tid, status="pending", priority=1, attempts=0):
    return {
        "id": tid, "title": tid, "status": status, "owner": None,
        "priority": priority, "files": [], "branch": None,
        "attempts": attempts, "blocked_reason": None,
    }


def test_interrupted_work_is_resumed_not_discarded(st):
    """죽는 순간 진행 중이던 작업의 편집은 worktree에 남아 있다 — 버리지 않는다."""
    st["tasks"] = [_task("T-1", "in_progress", attempts=1)]

    revived = agent_state.resume(st)

    assert revived == ["T-1"]
    assert st["tasks"][0]["status"] == "pending"
    assert st["tasks"][0]["owner"] is None
    # 시도 횟수는 유지된다 — 재개도 한 번의 시도로 세야 무한 반복이 막힌다.
    assert st["tasks"][0]["attempts"] == 1


def test_repeatedly_failing_work_stops_instead_of_looping(st):
    """같은 실패를 무한히 반복하는 게 가장 비싼 실패 방식이다."""
    st["tasks"] = [_task("T-1", "in_progress", attempts=2)]  # max_attempts=2

    agent_state.resume(st)

    assert st["tasks"][0]["status"] == "blocked"
    assert "재시도" in st["tasks"][0]["blocked_reason"]


def test_next_task_follows_priority_and_skips_unavailable(st):
    st["tasks"] = [
        _task("T-3", "pending", priority=3),
        _task("T-1", "pending", priority=1),
        _task("T-2", "done", priority=2),
        _task("T-9", "blocked", priority=0),  # 우선순위가 높아도 막힌 건 안 준다
    ]

    assert agent_state.next_task(st)["id"] == "T-1"


def test_kill_switch_stops_new_work(st):
    st["tasks"] = [_task("T-1")]
    st["paused"] = True

    assert agent_state.next_task(st) is None


def test_consecutive_failures_halt_the_runner(st):
    """계속 실패하는데 계속 도는 건 돈만 태운다."""
    st["tasks"] = [_task("T-1")]
    st["consecutive_failures"] = 3  # halt_after_consecutive_failures=3

    assert agent_state.next_task(st) is None

    # 하나라도 성공하면 카운터가 풀린다.
    st["tasks"].append(_task("T-2", "in_progress"))
    agent_state.set_status(st, "T-2", "done")
    assert st["consecutive_failures"] == 0
    assert agent_state.next_task(st)["id"] == "T-1"


def test_budget_limit_is_enforced_and_resets_daily(st):
    st["budget"]["daily_limit_usd"] = 0.5
    st["budget"]["day"] = agent_state._today()

    agent_state.add_spend(st, 0.3)
    assert agent_state.budget_exhausted(st) is False

    agent_state.add_spend(st, 0.25)
    assert agent_state.budget_exhausted(st) is True

    # 날짜가 바뀌면 지출과 알림 여부가 함께 초기화된다.
    st["budget"]["notified_today"] = True
    st["budget"]["day"] = "2000-01-01"
    assert agent_state.roll_day(st) is True
    assert st["budget"]["spent_today_usd"] == 0.0
    assert st["budget"]["notified_today"] is False
    assert agent_state.budget_exhausted(st) is False


def test_no_limit_means_never_exhausted(st):
    """상한을 안 걸었으면 소진 판정이 나면 안 된다 — 0과 None은 다르다."""
    st["budget"]["daily_limit_usd"] = None
    agent_state.add_spend(st, 999)

    assert agent_state.budget_exhausted(st) is False


def test_state_survives_a_crash_midwrite(st):
    """쓰다 죽어도 반쪽짜리 JSON이 남으면 다음 부팅에서 상태를 통째로 잃는다."""
    st["tasks"] = [_task("T-1")]
    agent_state.save(st)

    reloaded = agent_state.load()
    assert [t["id"] for t in reloaded["tasks"]] == ["T-1"]
    # 임시 파일이 남아 있지 않아야 한다.
    assert not (agent_state.STATE_PATH.with_suffix(".json.tmp")).exists()


def test_markdown_mirrors_are_generated_from_the_source_of_truth(st):
    st["tasks"] = [
        _task("T-1", "pending"),
        {**_task("T-2", "blocked"), "blocked_reason": "사람 결정 대기"},
    ]
    agent_state.save(st)

    backlog = (agent_state.AGENT_DIR / "BACKLOG.md").read_text(encoding="utf-8")
    handoff = (agent_state.AGENT_DIR / "HANDOFF.md").read_text(encoding="utf-8")

    assert "T-1" in backlog
    # 막힌 작업은 backlog 표가 아니라 별도 절에 사유와 함께 나온다.
    assert "사람 결정 대기" in backlog
    assert "사람 결정 대기" in handoff
    # 사람이 손으로 고치면 덮어써진다는 걸 파일 자체가 말해야 한다.
    assert "직접 고치지 말 것" in backlog


def test_failure_reason_survives_in_the_repo(tmp_path, monkeypatch):
    """실패 사유가 로그에만 있으면 몇 분 뒤엔 아무도 모른다.

    로그 창은 넘어가고 컨테이너는 재시작한다. 실제로 T-001이 검증에서 떨어졌는데
    이유를 못 건져 같은 배포를 여러 번 반복했다. 사유는 상태와 함께 저장소에
    남아야 사람이 나중에 읽을 수 있다.
    """
    monkeypatch.setattr(agent_state, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(agent_state, "STATE_PATH", tmp_path / "task-state.json")
    st = agent_state.load()
    st["tasks"] = [{
        "id": "T-1", "title": "무언가", "status": "in_progress", "owner": "builder",
        "priority": 1, "files": [], "branch": None, "attempts": 1, "blocked_reason": None,
    }]

    agent_state.set_status(st, "T-1", "failed", reason="백엔드 테스트 실패\nE   assert 3 == 4")
    agent_state.save(st)

    assert st["tasks"][0]["last_error"].startswith("백엔드 테스트 실패")
    failures = (tmp_path / "FAILURES.md").read_text(encoding="utf-8")
    assert "T-1" in failures
    assert "assert 3 == 4" in failures, "사람이 읽는 파일에 사유가 안 남았다"


def test_failure_without_a_reason_does_not_fabricate_one(tmp_path, monkeypatch):
    """사유를 모를 땐 비워 둔다 — 없는 설명을 지어내면 더 나쁘다."""
    monkeypatch.setattr(agent_state, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(agent_state, "STATE_PATH", tmp_path / "task-state.json")
    st = agent_state.load()
    st["tasks"] = [{
        "id": "T-1", "title": "무언가", "status": "in_progress", "owner": None,
        "priority": 1, "files": [], "branch": None, "attempts": 1, "blocked_reason": None,
    }]

    agent_state.set_status(st, "T-1", "failed")
    agent_state.save(st)

    assert "last_error" not in st["tasks"][0]
    assert "```" not in (tmp_path / "FAILURES.md").read_text(encoding="utf-8")


def test_a_new_status_does_not_break_the_whole_report():
    """상태를 하나 늘렸다고 렌더링이 통째로 죽으면 안 된다.

    awaiting_approval을 STATUSES에 넣고 라벨 표를 깜빡했을 때 KeyError로
    STATUS.md·BACKLOG.md가 전부 안 써졌다. 상태 하나 늘린 대가가 상태 파일
    전체를 못 쓰게 되는 것이어서는 안 된다.
    """
    assert agent_state._label("awaiting_approval") == "승인 대기"
    assert agent_state._label("아직_없는_상태") == "아직_없는_상태"
