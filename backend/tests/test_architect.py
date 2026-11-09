"""Architect fan-out — 계획 파서가 소유권 규칙을 강제하는지 고정한다.

LLM 출력은 계약이 아니라 제안이다. 프롬프트가 "병렬끼리 파일 겹치지 마라"고
말해도 어기는 날이 온다 — 그날 두 워커가 같은 파일을 덮지 않게 파서가
직렬화한다.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "agent"))

import runner  # noqa: E402
import state as agent_state  # noqa: E402


def _plan(tasks):
    return json.dumps({"tasks": tasks}, ensure_ascii=False)


def test_contract_first_fanout_reviewer_graph():
    """계약 → 병렬 워커들 → 리뷰의 의존 그래프가 그대로 만들어진다."""
    state = {"tasks": []}
    made = runner.parse_plan(_plan([
        {"title": "계약: api.ts에 X 필드", "files": ["frontend-next/lib/api.ts"], "priority": 1},
        {"title": "백엔드 X 계산", "files": ["backend/app/services/x.py"],
         "depends_on_titles": ["계약: api.ts에 X 필드"], "priority": 2},
        {"title": "화면 X 표시", "files": ["frontend-next/components/X.tsx"],
         "depends_on_titles": ["계약: api.ts에 X 필드"], "priority": 2},
        {"title": "통합 리뷰", "files": [],
         "depends_on_titles": ["백엔드 X 계산", "화면 X 표시"], "priority": 9},
    ]), state)

    assert len(made) == 4
    contract, backend, front, review = made
    assert contract["depends_on"] == []
    assert backend["depends_on"] == [contract["id"]]
    assert front["depends_on"] == [contract["id"]]
    assert set(review["depends_on"]) == {backend["id"], front["id"]}
    # 계약이 안 끝나면 워커는 안 뽑힌다
    assert agent_state.next_task(state)["id"] == contract["id"]
    assert [t["id"] for t in runner.pick_parallel(state, 2)] == [contract["id"]]
    # 계약 완료 → 두 워커가 병렬로 뽑힌다
    agent_state.set_status(state, contract["id"], "done")
    assert {t["id"] for t in runner.pick_parallel(state, 2)} == {backend["id"], front["id"]}


def test_overlapping_parallel_siblings_are_serialized():
    """Architect가 규칙을 어기고 병렬 둘에 같은 파일을 주면 파서가 직렬화한다."""
    state = {"tasks": []}
    made = runner.parse_plan(_plan([
        {"title": "A", "files": ["backend/app/routers/agent.py"]},
        {"title": "B", "files": ["backend/app/routers/agent.py"]},
    ]), state)
    a, b = made
    assert b["depends_on"] == [a["id"]]
    assert [t["id"] for t in runner.pick_parallel(state, 2)] == [a["id"]]


def test_unknown_dependency_titles_are_dropped_not_fatal():
    state = {"tasks": []}
    made = runner.parse_plan(_plan([
        {"title": "A", "files": ["x.py"], "depends_on_titles": ["없는 작업"]},
    ]), state)
    assert made[0]["depends_on"] == []


def test_goal_inbox_becomes_a_plan_task(tmp_path):
    """봇의 /goal은 즉시 작업이 아니라 [설계] 작업이 된다 — Architect가 분해한다."""
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(json.dumps({"kind": "goal", "title": "프랜차이즈 비교 기능"},
                                ensure_ascii=False) + "\n", encoding="utf-8")
    state = {"tasks": []}
    runner.drain_inbox(state, inbox)
    task = state["tasks"][0]
    assert task["kind"] == "plan"
    assert task["goal"] == "프랜차이즈 비교 기능"
    assert task["title"].startswith("[설계]")
