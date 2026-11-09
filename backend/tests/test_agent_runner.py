"""자율 러너의 판단 — 무엇을 할지, 한도인지 실패인지.

사람이 안 보는 동안 도는 코드라 "왜 그렇게 결정했는지"가 테스트에 남아 있어야
한다. 특히 한도를 실패로 잘못 세면 연속 실패 카운터가 올라가 러너가 스스로
멈춰 버린다 — 아무도 안 볼 때 조용히 죽는 실패다.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "agent"))

import runner  # noqa: E402
import state as agent_state  # noqa: E402


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_state, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(agent_state, "STATE_PATH", tmp_path / "task-state.json")
    return agent_state.load()


def _task(tid, status="pending", priority=1, files=None):
    return {
        "id": tid, "title": f"{tid} 작업", "status": status, "owner": None,
        "priority": priority, "files": files or [], "branch": None,
        "attempts": 0, "blocked_reason": None,
    }


def test_kill_switch_and_halt_are_reported_differently(st):
    """둘 다 '일하지 않는다'지만 사람에게 보여 줄 말이 다르다.

    멈춤은 의도된 것이고, 연속 실패는 봐 줘야 하는 것이다.
    """
    st["tasks"] = [_task("T-1")]

    st["paused"] = True
    assert runner.decide(st).action == "paused"

    st["paused"] = False
    st["consecutive_failures"] = 3
    halted = runner.decide(st)
    assert halted.action == "halted"
    assert "연속" in halted.reason


def test_empty_backlog_means_go_find_work_not_go_idle(st):
    """사람이 backlog를 채워 줘야만 도는 물건이면 24시간 도는 의미가 없다.

    할 일이 떨어졌을 때 노는 게 아니라 새로 찾아야 한다.
    """
    st["tasks"] = [_task("T-1", "done"), _task("T-2", "blocked")]

    decision = runner.decide(st)

    assert decision.action == "scout"
    assert decision.task_id is None


def test_scouting_does_not_override_a_stop(st):
    """멈춤과 연속 실패는 정찰보다 먼저다 — 세우라고 했는데 일을 찾으면 안 된다."""
    st["tasks"] = []

    st["paused"] = True
    assert runner.decide(st).action == "paused"

    st["paused"] = False
    st["consecutive_failures"] = 3
    assert runner.decide(st).action == "halted"


def test_work_picks_the_highest_priority_task(st):
    st["tasks"] = [_task("T-9", priority=9), _task("T-1", priority=1)]

    decision = runner.decide(st)

    assert decision.action == "work"
    assert decision.task_id == "T-1"


def test_rate_limit_is_not_mistaken_for_failure():
    """한도를 실패로 세면 연속 실패로 러너가 스스로 멈춘다."""
    # claude가 재시도 이벤트에 실어 주는 형태
    assert runner.is_rate_limited({"error": "rate_limit"}, "") is True
    assert runner.is_rate_limited(
        {"events": [{"subtype": "api_retry", "error": "rate_limit"}]}, ""
    ) is True
    # JSON을 못 받았을 때를 대비해 stderr도 본다
    assert runner.is_rate_limited(None, "Error: rate limit exceeded") is True


def test_real_failures_are_not_swallowed_as_rate_limit():
    """반대 방향도 중요하다 — 진짜 실패를 한도로 오해하면 영원히 재시도한다."""
    assert runner.is_rate_limited({"error": "invalid_request"}, "") is False
    assert runner.is_rate_limited(None, "SyntaxError: invalid syntax") is False
    assert runner.is_rate_limited(None, "") is False
    assert runner.is_rate_limited({}, "") is False


def test_prompt_pins_the_file_ownership():
    """여러 agent가 같은 파일을 고치면 서로의 작업을 덮어쓴다."""
    prompt = runner.task_prompt(_task("T-1", files=["backend/app/routers/agent.py"]))

    assert "backend/app/routers/agent.py" in prompt
    assert "범위 밖의 파일은 건드리지 마라" in prompt
    # 푸시는 러너가 검증 뒤에만 한다 — claude에게 맡기지 않는다.
    assert "커밋과 푸시는 하지 마라" in prompt
    # 오늘 밤 배운 것: 소스 문자열 검사 테스트는 값이 틀려도 통과한다.
    assert "소스 문자열을 검사하는 테스트는 쓰지 마라" in prompt


def test_prompt_survives_a_task_without_declared_files():
    """범위를 안 적은 작업이 들어와도 지시문이 깨지지 않아야 한다."""
    prompt = runner.task_prompt(_task("T-1", files=[]))

    assert "범위 미지정" in prompt
    assert "최소한으로" in prompt


def test_paid_key_is_refused_when_the_fallback_is_off():
    """폴백이 꺼져 있는데 API 키가 있으면 구독이 아니라 API로 과금된다.

    인증 우선순위상 키(3번)가 구독 토큰(5번)보다 먼저 쓰이기 때문이다. 조용히
    돌면 사용자가 모르는 사이에 요금이 나간다.

    main()이 아니라 판단 함수를 검사한다 — main()을 거치면 가드를 통과하는
    순간 테스트가 진짜 claude 프로세스를 띄운다(실제로 스위트가 10분을 넘겼다).
    """
    off = {"auth": {"api_fallback": False}}
    on = {"auth": {"api_fallback": True}}

    assert runner.api_key_would_be_billed({"ANTHROPIC_API_KEY": "sk-x"}, off) is True
    # 폴백을 켜 두었다면 그건 사용자가 고른 것이므로 막지 않는다.
    assert runner.api_key_would_be_billed({"ANTHROPIC_API_KEY": "sk-x"}, on) is False
    # 키가 없으면 구독으로 간다 — 정상 경로다.
    assert runner.api_key_would_be_billed({}, off) is False
    assert runner.api_key_would_be_billed({"ANTHROPIC_API_KEY": ""}, off) is False


def test_subprocess_failures_come_back_as_results_not_exceptions():
    """예외로 죽으면 실패 기록조차 안 남는다.

    실제로 그랬다: verify()의 pytest가 900초를 넘겨 TimeoutExpired가 올라갔고,
    컨테이너는 재시작을 반복했으며, 무슨 일이 있었는지 아무도 몰랐다. 사람이
    안 보는 동안 도는 프로그램에서 예외로 죽는 건 가장 나쁜 결말이다.
    """
    # 없는 명령 — 예전에는 FileNotFoundError가 올라가 러너가 죽었다.
    missing = runner._run(["definitely-not-a-real-command-xyz"], timeout=10)
    assert missing.returncode == runner.RC_NOT_FOUND
    assert "실행할 수 없다" in missing.stderr

    # 제한 시간 초과 — 결과로 돌아오고 이유가 붙는다.
    slow = runner._run(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1
    )
    assert slow.returncode == runner.RC_TIMEOUT
    assert "넘겨 중단했다" in slow.stderr


def test_a_timeout_is_reported_differently_from_a_broken_test():
    """느려진 것과 깨진 것은 다른 문제다 — 같은 문구로 적으면 엉뚱한 데를 고친다."""
    import subprocess as sp

    def fake_run(cmd, *, timeout, cwd=None):
        if "pytest" in " ".join(cmd):
            return sp.CompletedProcess(cmd, runner.RC_TIMEOUT, "", "900초를 넘겨 중단했다")
        return sp.CompletedProcess(cmd, 0, "backend/app/x.py\n", "")

    original = runner._run
    runner._run = fake_run
    try:
        passed, detail = runner.verify()
    finally:
        runner._run = original

    assert passed is False
    assert "제한 시간을 넘겼다" in detail
    assert "테스트 실패" not in detail


def test_failure_excerpt_keeps_the_line_that_names_the_broken_test():
    """출력을 뒤에서 그냥 자르면 경고만 담긴다.

    실제로 그랬다 — 러너가 남긴 사유가 전부 DeprecationWarning이라, 어느
    테스트가 왜 깨졌는지는 한 줄도 없었다. 사유를 남기는 목적이 "다음 사람이
    원인을 안다"는 것인데 그러면 없느니만 못하다.
    """
    output = (
        "FAILED tests/test_agent.py::test_routing - AssertionError: 'trends' != 'site_score'\n"
        "1 failed, 604 passed\n"
        + "\n".join(f"  DeprecationWarning: on_event is deprecated {i}" for i in range(200))
    )

    excerpt = runner._failure_excerpt(output)

    assert "test_routing" in excerpt
    assert "DeprecationWarning" not in excerpt


def test_failure_excerpt_falls_back_instead_of_returning_nothing():
    """형식을 못 알아봐도 빈손으로 돌려주지 않는다."""
    assert runner._failure_excerpt("알 수 없는 출력") == "알 수 없는 출력"
    assert runner._failure_excerpt("") == ""
    assert runner._failure_excerpt(None) == ""


def test_scouted_tasks_get_unique_ids(st):
    """지운 작업이 있어도 번호가 겹치면 안 된다.

    길이로 세면 T-003을 지운 뒤 새 일이 또 T-003이 되고, 상태 갱신이 엉뚱한
    작업에 걸린다.
    """
    st["tasks"] = [_task("T-001"), _task("T-005")]

    added = runner.add_scouted(st, [
        {"title": "새 일 하나", "files": ["a.py"], "priority": 10, "why": ""},
        {"title": "새 일 둘", "files": ["b.py"], "priority": 20, "why": ""},
    ])

    assert added == ["T-006", "T-007"]
    assert len({t["id"] for t in st["tasks"]}) == 4


def test_scout_does_not_add_work_that_is_already_queued(st):
    """같은 일을 또 담으면 backlog가 중복으로 부풀고 같은 작업을 두 번 한다."""
    st["tasks"] = [_task("T-001")]
    st["tasks"][0]["title"] = "이미 있는 일"

    added = runner.add_scouted(st, [
        {"title": "이미 있는 일", "files": [], "priority": 10, "why": ""},
        {"title": "진짜 새 일", "files": [], "priority": 10, "why": ""},
    ])

    assert len(added) == 1
    assert st["tasks"][-1]["title"] == "진짜 새 일"


def test_scout_skips_a_reworded_duplicate_title(st):
    """정찰은 매번 새 LLM 호출이라 같은 발견을 마침표·불릿·공백만 다르게 적는다.

    제목 완전일치만 보면 이런 사소한 재표현이 계속 새 작업으로 쌓여, 병합 안
    된(아직 auto/* 브랜치에만 있는) 같은 작업이 backlog에 무한히 다시 담긴다.
    """
    st["tasks"] = [_task("T-001")]
    st["tasks"][0]["title"] = "혼잡도 카드에 실제 계산값이 아니라 목업 텍스트가 나온다"

    added = runner.add_scouted(st, [
        # 끝에 마침표, 앞에 불릿 — 같은 발견의 사소한 재표현
        {"title": "- 혼잡도 카드에 실제 계산값이 아니라 목업 텍스트가 나온다.",
         "files": [], "priority": 10, "why": ""},
    ])

    assert added == []
    assert len(st["tasks"]) == 1


def test_scout_keeps_a_different_finding_with_a_similar_template(st):
    """정찰 제목은 "카드에 목업 텍스트가 나온다" 같은 틀을 반복해서 쓴다.

    틀이 같다고 다 같은 작업은 아니다 — 혼잡도 카드와 사고이력 카드는 서로
    다른 버그이므로, 유사도 문턱을 낮게 잡으면 서로 다른 작업이 하나로
    뭉개진다.
    """
    st["tasks"] = [_task("T-001")]
    st["tasks"][0]["title"] = "혼잡도 카드에 목업 텍스트가 나온다"

    added = runner.add_scouted(st, [
        {"title": "사고이력 카드에 목업 텍스트가 나온다", "files": [], "priority": 10, "why": ""},
    ])

    assert len(added) == 1
    assert st["tasks"][-1]["title"] == "사고이력 카드에 목업 텍스트가 나온다"


def test_scout_dedup_does_not_confuse_short_unrelated_titles(st):
    """짧은 제목끼리는 글자가 우연히 많이 겹칠 수 있다 — 그렇다고 같은 일은 아니다."""
    st["tasks"] = [_task("T-001")]
    st["tasks"][0]["title"] = "새 일 하나"

    added = runner.add_scouted(st, [
        {"title": "새 일 둘", "files": [], "priority": 10, "why": ""},
    ])

    assert len(added) == 1


def test_two_tasks_run_together_only_when_their_files_do_not_overlap(st):
    """같은 파일을 동시에 고치면 나중에 끝난 쪽이 앞 쪽 작업을 덮어쓴다."""
    st["tasks"] = [
        _task("T-1", files=["backend/app/routers/agent.py"]),
        _task("T-2", files=["frontend-next/lib/api.ts"]),
    ]

    picked = runner.pick_parallel(st, 2)

    assert [t["id"] for t in picked] == ["T-1", "T-2"]


def test_overlapping_files_are_not_paired(st):
    st["tasks"] = [
        _task("T-1", files=["backend/app/routers/agent.py"]),
        _task("T-2", files=["backend/app/routers/agent.py", "x.py"]),
        _task("T-3", priority=5, files=["docs/README.md"]),
    ]

    picked = runner.pick_parallel(st, 2)

    ids = [t["id"] for t in picked]
    assert "T-1" in ids
    assert "T-2" not in ids, "같은 파일을 쓰는 작업이 짝지어졌다"
    assert "T-3" in ids


def test_a_task_without_a_declared_scope_runs_alone(st):
    """범위를 안 적었다는 건 어디든 건드릴 수 있다는 뜻이다 — 짝을 붙이면 위험하다."""
    st["tasks"] = [_task("T-1", files=[]), _task("T-2", files=["a.py"])]

    picked = runner.pick_parallel(st, 2)

    assert [t["id"] for t in picked] == ["T-1"]


def test_a_stop_beats_parallelism(st):
    """세우라고 했으면 둘이든 하나든 아무것도 집지 않는다."""
    st["tasks"] = [_task("T-1", files=["a.py"]), _task("T-2", files=["b.py"])]

    st["paused"] = True
    assert runner.pick_parallel(st, 2) == []

    st["paused"] = False
    st["consecutive_failures"] = 3
    assert runner.pick_parallel(st, 2) == []


def test_failure_excerpt_understands_the_frontend_test_format():
    """프론트는 node:test라 pytest와 형식이 다르다.

    T-053이 "프론트 test 실패"라고만 남기고 끝나 어느 테스트가 왜 깨졌는지
    알 수 없었다 — 백엔드만 고치고 프론트를 빠뜨린 결과다.
    """
    output = (
        "# Subtest: 경쟁이 적으면 그건 진짜 근거다\n"
        "not ok 12 - 경쟁이 적으면 그건 진짜 근거다\n"
        "  AssertionError: Expected values to be strictly equal\n"
        "# pass 86\n# fail 1\n"
    )

    excerpt = runner._failure_excerpt(output)

    assert "not ok 12" in excerpt
    assert "AssertionError" in excerpt


def test_failure_excerpt_understands_typescript_errors():
    output = "lib/api.ts(42,7): error TS2322: Type 'string' is not assignable to type 'number'.\nFound 1 error."

    excerpt = runner._failure_excerpt(output)

    assert "error TS2322" in excerpt


def test_fallback_branch_name_is_derived_from_the_task():
    """main에 못 올렸을 때 물러날 브랜치 이름."""
    assert runner.branch_for({"id": "T-084"}) == "auto/T-084"


def test_state_is_not_carried_into_the_work_branch():
    """상태가 브랜치에 갇히면 러너가 자기가 뭘 했는지 잊고 같은 일을 반복한다.

    실제로 겪었다 — 완료 표시가 push되지 않아 5개 작업이 전부 두 번씩 돌았다.
    코드는 브랜치로, 상태는 main으로 간다.
    """
    import subprocess as sp

    calls = []

    def fake_run(cmd, *, timeout, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "diff", "--cached"]:
            return sp.CompletedProcess(cmd, 0, "backend/app/x.py\n", "")
        return sp.CompletedProcess(cmd, 0, "", "")

    original = runner._run
    runner._run = fake_run
    try:
        ok, note = runner.commit_and_push({"id": "T-1", "title": "무언가"}, Path("/tmp/w1"))
    finally:
        runner._run = original

    assert ok is True
    add = next(c for c in calls if c[:2] == ["git", "add"])
    assert ":!docs/agent" in add, "상태 파일이 작업 브랜치 커밋에 딸려 간다"

    pushes = [c for c in calls if c[:2] == ["git", "push"]]
    assert pushes, "push가 없다"
    # 검증을 통과했으면 최신 main 위에 얹어 바로 반영한다(2026-07-30 결정).
    # 브랜치에 담아 두면 아무도 병합하지 않아 101개가 쌓이고 67개가 상했다.
    assert any("HEAD:main" in " ".join(c) for c in pushes), "main에 반영하지 않았다"
    assert all("auto/T-1" not in " ".join(c) for c in pushes), "성공 경로인데 브랜치로 갔다"
    assert any(c[:2] == ["git", "rebase"] for c in calls), "rebase 없이 main에 밀었다"


def test_work_falls_back_to_a_branch_when_main_push_keeps_failing():
    """main push가 계속 튕기면 브랜치로 물러나 일을 잃지 않는다."""
    import subprocess as sp

    calls = []

    def fake_run(cmd, *, timeout, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "diff", "--cached"]:
            return sp.CompletedProcess(cmd, 0, "backend/app/x.py\n", "")
        if cmd[:2] == ["git", "push"] and "HEAD:main" in " ".join(cmd):
            return sp.CompletedProcess(cmd, 1, "", "rejected: non-fast-forward")
        return sp.CompletedProcess(cmd, 0, "", "")

    original = runner._run
    runner._run = fake_run
    try:
        ok, note = runner.commit_and_push({"id": "T-2", "title": "무언가"}, Path("/tmp/w2"))
    finally:
        runner._run = original

    assert ok is True, "일을 잃었다 — 브랜치로도 못 남겼다"
    assert "auto/T-2" in note, note
    main_pushes = [c for c in calls if c[:2] == ["git", "push"] and "HEAD:main" in " ".join(c)]
    assert len(main_pushes) == runner._MAIN_PUSH_ATTEMPTS, "재시도 횟수가 다르다"


def test_rebase_conflict_goes_straight_to_a_branch_without_retrying():
    """rebase 충돌은 재시도해도 그대로다 — 바로 브랜치로 넘긴다."""
    import subprocess as sp

    calls = []

    def fake_run(cmd, *, timeout, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "diff", "--cached"]:
            return sp.CompletedProcess(cmd, 0, "backend/app/x.py\n", "")
        if cmd[:2] == ["git", "rebase"] and "--abort" not in cmd:
            return sp.CompletedProcess(cmd, 1, "", "CONFLICT")
        return sp.CompletedProcess(cmd, 0, "", "")

    original = runner._run
    runner._run = fake_run
    try:
        ok, note = runner.commit_and_push({"id": "T-3", "title": "무언가"}, Path("/tmp/w3"))
    finally:
        runner._run = original

    assert ok is True and "auto/T-3" in note, note
    assert any(c[:3] == ["git", "rebase", "--abort"] for c in calls), "rebase를 되돌리지 않았다"
    assert not [c for c in calls if c[:2] == ["git", "push"] and "HEAD:main" in " ".join(c)], \
        "충돌인데 main으로 밀었다"


def test_scout_does_not_readd_work_whose_files_are_still_unmerged(st):
    """제목이 매번 달라도 같은 파일을 겨냥하면 중복이다.

    실제로 겪었다: industry_profiles/franchise 검색이 PR 병합 여부를 안 보고
    제목만 바꿔 가며 6번 넘게 다시 담겼다. done은 브랜치에 올렸다는 뜻이지
    main에 합쳐졌다는 뜻이 아니므로, 아직 안 합쳐진 작업이 다루던 파일과
    겹치면 제목이 달라도 걸러야 한다.
    """
    st["tasks"] = [_task("T-001", status="done", files=["backend/app/routers/agent.py"])]

    added = runner.add_scouted(
        st,
        [{"title": "franchise 검색이 _TOOLS에 없다 (다른 표현)",
          "files": ["backend/app/routers/agent.py"], "priority": 10, "why": ""}],
        active_files={"backend/app/routers/agent.py"},
    )

    assert added == []
    assert len(st["tasks"]) == 1


def test_scout_still_adds_work_for_untouched_files(st):
    """겹치지 않는 새 문제까지 막으면 정찰이 무용지물이 된다."""
    st["tasks"] = [_task("T-001", status="done", files=["backend/app/routers/agent.py"])]

    added = runner.add_scouted(
        st,
        [{"title": "다른 자리의 새 문제", "files": ["backend/app/services/x.py"],
          "priority": 10, "why": ""}],
        active_files={"backend/app/routers/agent.py"},
    )

    assert len(added) == 1


def test_unresolved_files_treats_an_open_pr_as_still_active(st, monkeypatch):
    """done인데 PR이 아직 열려 있으면(=안 합쳐졌으면) 그 파일은 여전히 안 끝난 것이다."""
    st["tasks"] = [_task("T-001", status="done", files=["backend/app/routers/agent.py"])]

    monkeypatch.setattr(runner, "pr_state", lambda branch, root=None: "OPEN")

    files = runner.unresolved_files(st)

    assert files == {"backend/app/routers/agent.py"}
    assert agent_state.find(st, "T-001").get("pr_merged") is not True


def test_unresolved_files_drops_a_task_once_its_pr_is_merged(st, monkeypatch):
    """병합이 확인되면 그 파일은 더 이상 '안 끝난 것'이 아니다 — 진짜로 끝났다."""
    st["tasks"] = [_task("T-001", status="done", files=["backend/app/routers/agent.py"])]

    monkeypatch.setattr(runner, "pr_state", lambda branch, root=None: "MERGED")

    files = runner.unresolved_files(st)

    assert files == set()
    assert agent_state.find(st, "T-001")["pr_merged"] is True


def test_unresolved_files_does_not_ask_gh_again_once_merged(st, monkeypatch):
    """병합이 확인된 뒤에는 매 정찰마다 gh를 다시 묻지 않는다 — 비용을 안 늘린다."""
    st["tasks"] = [_task("T-001", status="done", files=["a.py"])]
    st["tasks"][0]["pr_merged"] = True

    calls = []
    monkeypatch.setattr(runner, "pr_state", lambda branch, root=None: calls.append(branch) or "MERGED")

    files = runner.unresolved_files(st)

    assert files == set()
    assert calls == []


def test_unresolved_files_treats_an_unconfirmed_pr_as_still_active(st, monkeypatch):
    """gh 확인이 실패하면(None) 병합됐다고 잘못 넘기지 않는다 — 안전한 쪽은 재발견 억제다."""
    st["tasks"] = [_task("T-001", status="done", files=["backend/app/routers/agent.py"])]

    monkeypatch.setattr(runner, "pr_state", lambda branch, root=None: None)

    files = runner.unresolved_files(st)

    assert files == {"backend/app/routers/agent.py"}


def test_unresolved_files_includes_pending_and_failed_without_asking_gh(st, monkeypatch):
    """이미 알려진 문제(대기·실패)는 gh를 물을 필요가 없다 — 아직 브랜치조차 없을 수 있다."""
    st["tasks"] = [
        _task("T-001", status="pending", files=["a.py"]),
        _task("T-002", status="failed", files=["b.py"]),
        _task("T-003", status="blocked", files=["c.py"]),
    ]
    monkeypatch.setattr(
        runner, "pr_state",
        lambda branch, root=None: (_ for _ in ()).throw(AssertionError("done이 아닌데 gh를 물었다")),
    )

    files = runner.unresolved_files(st)

    assert files == {"a.py", "b.py", "c.py"}


def test_pr_state_treats_a_failed_gh_call_as_unknown(monkeypatch):
    """gh가 없거나 실패하면 '모른다'로 돌아온다 — '병합 안 됐다'와는 다른 뜻이라 안전한 쪽으로 접는다."""
    def fake_run(cmd, *, timeout, cwd=None):
        import subprocess as sp
        return sp.CompletedProcess(cmd, 1, "", "no pull requests found")

    monkeypatch.setattr(runner, "_run", fake_run)

    assert runner.pr_state("auto/T-001") is None


def test_pr_state_reads_the_state_field(monkeypatch):
    def fake_run(cmd, *, timeout, cwd=None):
        import subprocess as sp
        assert "auto/T-001" in cmd
        return sp.CompletedProcess(cmd, 0, "MERGED\n", "")

    monkeypatch.setattr(runner, "_run", fake_run)

    assert runner.pr_state("auto/T-001") == "MERGED"


def test_scouted_work_never_outranks_what_a_person_asked_for(st):
    """정찰은 기계적으로 찾을 수 있는 것만 찾는데 그런 건 수백 개다.

    우선순위를 같이 주면 사람이 시킨 큰 작업(대화 맥락 같은)이 "이 필드가 화면에
    없다" 류에 계속 밀려 영영 차례가 안 온다.
    """
    st["tasks"] = [_task("T-001", priority=1)]  # 사람이 넣은 지시

    runner.add_scouted(st, [
        {"title": "정찰이 찾은 일", "files": ["a.py"], "priority": 1, "why": ""},
    ])

    scouted = st["tasks"][-1]
    assert scouted["priority"] >= runner.SCOUT_MIN_PRIORITY
    # 다음에 집는 건 여전히 사람이 시킨 쪽이어야 한다.
    assert runner.decide(st).task_id == "T-001"


def test_a_task_waits_for_the_one_it_builds_on(st):
    """선행이 안 끝났으면 뒤엣것은 안 집힌다.

    "프론트가 이전 대화를 실어 보낸다"를 "백엔드가 이전 대화를 받는다"보다 먼저
    하면 아직 없는 필드를 향해 코드를 쓴다. 그런데 타입도 테스트도 통과하니
    done으로 찍히고, 실제로는 아무 효과가 없는 채 남는다.
    """
    st["tasks"] = [
        _task("T-088", priority=1),
        dict(_task("T-089", priority=2), depends_on=["T-088"]),
    ]

    assert [t["id"] for t in runner.pick_parallel(st, limit=2)] == ["T-088"]

    agent_state.set_status(st, "T-088", "done")
    assert [t["id"] for t in runner.pick_parallel(st, limit=2)] == ["T-089"]


def test_work_behind_a_blocked_task_is_blocked_too_not_left_pending(st):
    """선행이 막히면 뒤엣것도 막는다.

    pending으로 두면 러너는 영영 건너뛰는데 사람은 BACKLOG.md에서 "대기 중"으로
    읽는다. 아무도 안 보는 자리에서 조용히 죽는 게 제일 나쁘다.
    """
    st["tasks"] = [
        _task("T-088", priority=1),
        dict(_task("T-089", priority=2), depends_on=["T-088"]),
    ]
    agent_state.set_status(st, "T-088", "blocked", reason="사람 결정 필요")

    assert runner.block_dead_dependents(st) == ["T-089"]
    dependent = agent_state.find(st, "T-089")
    assert dependent["status"] == "blocked"
    assert "T-088" in (dependent["blocked_reason"] or "")


def test_a_big_task_gets_more_than_the_twenty_minute_default(st):
    """작업마다 시간 예산이 다르다.

    화면에 필드 하나 띄우는 건 20분이면 넘치지만 "대화가 이어지게 만들어라"는
    분석→구현→테스트→수정을 한 세션에 돌아야 한다. 20분에 잘리면 검증 전에
    죽으니 push가 없고, 다음 회차가 또 처음부터 한다.
    """
    assert runner.budget_for({"id": "T-001"}) == runner.TASK_TIMEOUT_S
    assert runner.budget_for({"id": "T-088", "budget_min": 120}) == 120 * 60
    # 상한은 있어야 한다 — 무한정 붙잡고 있으면 그 자리가 하루 종일 막힌다.
    assert runner.budget_for({"id": "T-x", "budget_min": 10_000}) == runner.MAX_TASK_TIMEOUT_S


def test_runner_swaps_itself_in_when_its_own_code_changes(monkeypatch):
    """러너 코드가 바뀌면 새 코드로 다시 띄운다.

    sync_repo가 매 회차 새 코드를 당겨오지만 파이썬은 시작할 때 읽은 모듈을
    계속 쓴다. 이게 없으면 러너 자체를 고쳐 push해도 컨테이너에서는 옛 코드가
    영영 돈다 — 24시간 무인 운영에서 "사람이 눌러 줘야 반영되는 것"은
    반영되지 않는 것이다.
    """
    calls = []
    monkeypatch.setattr(runner.os, "execv", lambda exe, argv: calls.append(argv))

    # 안 바뀌었으면 그냥 계속 돈다 — 매 회차 재시작하면 아무 일도 못 한다.
    runner.restart_if_own_code_changed(runner.own_code_fingerprint())
    assert calls == []

    runner.restart_if_own_code_changed("이전-지문")
    assert len(calls) == 1


def _fake_run(calls, *, rc=0, out=""):
    """_run을 가로채 명령만 모은다."""
    def run(cmd, *, timeout=0, cwd=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, rc, out, "")
    return run


def test_state_goes_to_its_own_branch_not_main(monkeypatch):
    """상태는 main이 아니라 러너 전용 브랜치로 간다.

    Railway 서비스가 GitHub에 직접 연결돼 있어서 main의 **모든** 커밋이 재빌드를
    띄운다. 러너는 매 사이클 상태를 올리므로 main으로 밀면 자기 push로 자기
    컨테이너를 10분마다 죽인다 — 2026-07-30에 실제로 그랬고, 그래서 20분 예산도
    못 채웠다. 라이브 백엔드도 같이 재시작돼 캐시가 매번 비었다.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(runner, "_run", _fake_run(calls, out="docs/agent/STATUS.md"))

    assert runner.push_state("2건 처리") is True

    pushes = [c for c in calls if "push" in c]
    assert pushes, "push를 아예 안 했다"
    for cmd in pushes:
        assert f"HEAD:refs/heads/{runner.STATE_BRANCH}" in cmd, cmd
        assert "main" not in cmd, f"상태를 main으로 밀었다: {cmd}"


def test_state_is_restored_from_the_branch_on_start(monkeypatch):
    """재시작하면 브랜치에서 상태를 꺼내 온다.

    컨테이너는 main을 clone하고 시작한다. 상태를 브랜치로 옮긴 뒤 이걸 안 하면
    재시작마다 러너가 기억을 잃고 같은 일을 처음부터 다시 한다.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(runner, "_run", _fake_run(calls))

    assert runner.restore_state_from_branch() is True
    assert any(c[:2] == ["git", "fetch"] and runner.STATE_BRANCH in " ".join(c) for c in calls)
    assert any("docs/agent" in c and "checkout" in c for c in calls)


def test_restore_does_not_fail_the_runner_when_the_branch_is_missing(monkeypatch):
    """상태 브랜치가 아직 없어도 러너는 뜬다 — 첫 실행이 그렇다."""
    monkeypatch.setattr(runner, "_run", _fake_run([], rc=1))
    assert runner.restore_state_from_branch() is False


def _fake_git(calls, *, status_out="", pull_rc=0):
    """git status와 git pull을 구분해서 답하는 _run 대역."""
    def run(cmd, *, timeout=0, cwd=None):
        calls.append(list(cmd))
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, status_out, "")
        if cmd[:2] == ["git", "pull"]:
            return subprocess.CompletedProcess(cmd, pull_rc, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def test_uncommitted_own_state_does_not_stop_the_pull(monkeypatch):
    """러너 자기 상태 파일만 더러우면 pull은 계속돼야 한다.

    2026-07-30에 러너가 헛돈 실제 원인이다. 러너는 매 사이클 st.save()로
    docs/agent/task-state.json을 쓰는데, 정찰이 빈손인 사이클은 push_state를
    안 부른다. 그러면 커밋 안 된 자기 상태 파일 하나가 dirty를 만들어
    sync_repo가 **영구히** False를 돌려주고, 그 순간부터
    - 코드 자기 갱신이 멈춰(restart_if_own_code_changed가 볼 새 코드가 안 옴)
      07-29 17:13/23:46에 넣은 scout 중복 필터를 돌던 프로세스가 못 읽었고,
      07-30 02:43에 옛 코드로 T-179를 중복 생성했다
    - kill switch(pause.py on)가 안 먹었다
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(runner, "_run", _fake_git(
        calls, status_out=" M docs/agent/task-state.json\n M docs/agent/STATUS.md\n"))

    assert runner.sync_repo() is True
    assert any(c[:2] == ["git", "pull"] for c in calls), "pull을 아예 안 했다"


def test_someone_elses_edit_still_stops_the_pull(monkeypatch):
    """남의 작업 중 편집이 남아 있으면 여전히 건너뛴다 — rebase로 흔들면 안 된다."""
    calls: list[list[str]] = []
    monkeypatch.setattr(runner, "_run", _fake_git(
        calls, status_out=" M backend/app/routers/agent.py\n"))

    assert runner.sync_repo() is False
    assert not any(c[:2] == ["git", "pull"] for c in calls), "남의 편집 위로 pull했다"


def test_own_state_mixed_with_real_edits_still_stops_the_pull(monkeypatch):
    """자기 상태 파일이 섞여 있어도, 남의 편집이 하나라도 있으면 건너뛴다."""
    monkeypatch.setattr(runner, "_run", _fake_git(
        [], status_out=" M docs/agent/task-state.json\n M frontend-next/lib/api.ts\n"))
    assert runner.sync_repo() is False


def test_pull_uses_autostash_so_own_state_does_not_block_rebase(monkeypatch):
    """자기 상태만 더러운 채로 rebase하면 git이 거부한다 — autostash로 넘긴다."""
    calls: list[list[str]] = []
    monkeypatch.setattr(runner, "_run", _fake_git(
        calls, status_out=" M docs/agent/task-state.json\n"))

    runner.sync_repo()
    pull = next(c for c in calls if c[:2] == ["git", "pull"])
    assert "--autostash" in pull


def test_merge_new_tasks_adds_a_task_the_local_state_does_not_know(st):
    """사람이 agent-state 브랜치에 얹은 새 작업은 로컬에 없으니 그대로 더한다."""
    st["tasks"] = [_task("T-001")]

    added = agent_state.merge_new_tasks(st, [
        {"id": "T-001", "title": "이미 아는 일", "status": "pending"},
        {"id": "T-050", "title": "사람이 급히 넣은 일", "status": "pending", "priority": 1},
    ])

    assert added == ["T-050"]
    assert agent_state.find(st, "T-050")["title"] == "사람이 급히 넣은 일"


def test_merge_new_tasks_does_not_let_remote_roll_back_local_progress(st):
    """같은 id면 로컬이 정본이다.

    러너가 방금 T-001을 in_progress·done으로 바꿨는데, 사람의 클론에 있던
    옛(pending) 버전이 원격에 남아 있다고 그걸로 덮으면 진행한 일이 사라진다.
    """
    st["tasks"] = [_task("T-001", status="done")]
    agent_state.find(st, "T-001")["attempts"] = 2

    added = agent_state.merge_new_tasks(st, [
        {"id": "T-001", "title": "T-1 작업", "status": "pending", "attempts": 0},
    ])

    assert added == []
    assert agent_state.find(st, "T-001")["status"] == "done"
    assert agent_state.find(st, "T-001")["attempts"] == 2


def test_merge_new_tasks_ignores_entries_without_an_id(st):
    """id가 없으면 어떤 로컬 작업과도 짝지을 수 없다 — 조용히 건너뛴다."""
    st["tasks"] = []

    added = agent_state.merge_new_tasks(st, [{"title": "id가 빠진 항목"}])

    assert added == []
    assert st["tasks"] == []


def test_fetch_remote_tasks_reads_the_state_branch_without_touching_the_worktree(monkeypatch):
    """원격 task-state.json을 git show로만 읽는다 — checkout하지 않는다."""
    calls = []

    def fake_run(cmd, *, timeout=0, cwd=None):
        calls.append(cmd)
        if cmd[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "show"]:
            payload = json.dumps({"tasks": [{"id": "T-050", "title": "새 일"}]})
            return subprocess.CompletedProcess(cmd, 0, payload, "")
        raise AssertionError(f"예상 못 한 명령: {cmd}")

    monkeypatch.setattr(runner, "_run", fake_run)

    tasks = runner.fetch_remote_tasks()

    assert tasks == [{"id": "T-050", "title": "새 일"}]
    assert any(c[:2] == ["git", "fetch"] and runner.STATE_BRANCH in " ".join(c) for c in calls)
    show = next(c for c in calls if c[:2] == ["git", "show"])
    assert f"origin/{runner.STATE_BRANCH}:docs/agent/task-state.json" in show
    assert not any(c[:2] == ["git", "checkout"] for c in calls), "작업 트리를 건드렸다"


def test_fetch_remote_tasks_returns_empty_when_fetch_fails(monkeypatch):
    """네트워크 문제로 fetch가 실패해도 빈 목록을 돌려줄 뿐 예외로 안 죽는다."""
    monkeypatch.setattr(runner, "_run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "fail"))

    assert runner.fetch_remote_tasks() == []


def test_pull_remote_backlog_merges_saves_and_pushes_when_something_new_arrives(st, monkeypatch):
    """새 작업을 받으면 로컬에 반영하고 바로 push해 다음 정찰이 또 안 담게 한다."""
    st["tasks"] = [_task("T-001")]
    monkeypatch.setattr(runner, "fetch_remote_tasks",
                         lambda root=None: [{"id": "T-050", "title": "새 일", "status": "pending"}])
    pushed = []
    monkeypatch.setattr(runner, "push_state", lambda note: pushed.append(note) or True)

    added = runner.pull_remote_backlog(st)

    assert added == ["T-050"]
    assert agent_state.find(st, "T-050") is not None
    reloaded = agent_state.load()
    assert agent_state.find(reloaded, "T-050") is not None, "새로 받은 작업을 저장하지 않았다"
    assert pushed, "새로 받은 작업을 다시 올리지 않았다 — 다음 push_state가 사람이 넣은 일을 지운다"


def test_pull_remote_backlog_does_nothing_when_remote_has_no_news(st, monkeypatch):
    """새로 온 게 없으면 굳이 저장·push하지 않는다 — 매 사이클 헛 force-push를 피한다."""
    st["tasks"] = [_task("T-001")]
    monkeypatch.setattr(runner, "fetch_remote_tasks",
                         lambda root=None: [{"id": "T-001", "title": "이미 아는 일"}])
    pushed = []
    monkeypatch.setattr(runner, "push_state", lambda note: pushed.append(note) or True)

    added = runner.pull_remote_backlog(st)

    assert added == []
    assert pushed == []


def test_dirty_paths_reads_the_new_name_of_a_renamed_file(monkeypatch):
    """`R  old -> new` 줄에서 실제 경로는 화살표 뒤다.

    앞을 읽으면 docs/agent/에서 밖으로 옮긴 파일을 자기 상태로 오인해 건너뛴다.
    """
    monkeypatch.setattr(runner, "_run", _fake_git(
        [], status_out='R  docs/agent/old.md -> backend/app/new.py\n'))
    assert runner._dirty_paths() == ["backend/app/new.py"]


def test_failure_reason_survives_when_the_error_is_only_on_stderr():
    """실패 사유는 stdout·stderr 양쪽에서 찾는다.

    pytest는 실패를 stdout에, npm·node는 stderr에 적는다. 한쪽만 보면 반대쪽
    실패가 사유 없이 기록된다. 그리고 이 stderr 인자가 병합 중에 사라져
    T-209가 통째로 날아갔다 — 사유를 적는 함수가 사유 없이 죽은 것이다.
    """
    reason = runner._failure_excerpt("정상 로그뿐", stderr="error TS2345: 타입이 안 맞는다")
    assert "TS2345" in reason


def test_claude_binary_is_resolved_not_assumed():
    """실행 파일을 이름으로만 부르면 윈도우에서 못 띄운다.

    npm 전역 설치는 `claude.cmd`인데 확장자 없는 `claude`는 bash 스크립트라
    파이썬 subprocess가 WinError 2로 죽는다 — 로컬에서 러너를 돌리면 첫
    claude 호출부터 실패했다(2026-08-09 실측). which가 PATHEXT를 따른다.
    """
    resolved = runner.claude_bin()
    assert resolved  # 못 찾아도 빈 문자열이 아니라 "claude"로 떨어진다
    assert isinstance(resolved, str)
