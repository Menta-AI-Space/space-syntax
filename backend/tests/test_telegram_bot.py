"""텔레그램 봇 — 권한·승인 판정과 러너로 넘어가는 경로를 고정한다.

여기서 지키려는 것은 둘이다:
1. **아무나 이 저장소에 코드를 쓰게 하지 않는다** (권한 검사)
2. **승인이 필요한 일이 승인 없이 시작되지 않는다** (awaiting_approval)

둘 다 "설정을 깜빡했을 때 안전한 쪽으로 닫히는가"까지 본다 — 실수의 대가가
전면 개방이면 안 된다.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "agent"))

import runner  # noqa: E402
import state as agent_state  # noqa: E402
import telegram_bot as bot  # noqa: E402


# --- 권한 ------------------------------------------------------------------


def test_only_listed_numeric_ids_may_give_orders():
    allowed = bot.allowed_ids("123, 456")
    assert bot.is_authorized(123, allowed)
    assert bot.is_authorized("456", allowed)  # 텔레그램이 문자열로 줄 때가 있다
    assert not bot.is_authorized(999, allowed)
    assert not bot.is_authorized(None, allowed)


def test_empty_allowlist_closes_the_door_instead_of_opening_it():
    """변수를 깜빡 비워 두면 아무도 못 들어온다.

    "비었으니 전부 허용"으로 열리면 봇 주소를 아는 누구나 이 저장소에 코드를
    쓰게 된다. 설정 실수의 대가가 전면 개방이어서는 안 된다.
    """
    assert bot.allowed_ids("") == set()
    assert not bot.is_authorized(123, set())


def test_unauthorized_message_gets_no_reply_at_all(monkeypatch):
    """허용되지 않은 사람에게는 거절조차 답하지 않는다.

    "권한이 없습니다"라고 답하면 이 봇이 살아 있다는 사실을 알려 주는 꼴이다.
    """
    sent, inboxed = [], []
    monkeypatch.setattr(bot, "send", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(bot, "append_inbox", lambda e: inboxed.append(e))

    bot.handle_message("tok", {"chat": {"id": 5}, "from": {"id": 999},
                               "text": "배포해줘"}, {123})
    assert sent == []
    assert inboxed == []


# --- 승인 ------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "옛 테이블 삭제해줘",
    "DB 스키마 변경 좀",
    "API key 새로 발급해서 넣어줘",
    "유료 플랜 결제해줘",
    ".github/workflows 고쳐줘",
])
def test_dangerous_orders_need_a_human_yes(text):
    assert bot.approval_reason(text) is not None


@pytest.mark.parametrize("text", [
    "입지 점수 화면에 그래프 추가해줘",
    "샐러드집이 한식으로 나오는 버그 고쳐줘",
    "테스트 좀 더 붙여줘",
])
def test_ordinary_work_starts_without_asking(text):
    assert bot.approval_reason(text) is None


def test_an_order_needing_approval_is_never_queued_as_pending(tmp_path):
    """승인이 필요한 일은 pending으로 들어가지 않는다.

    pending으로 넣으면 그 순간 다음 사이클이 집어 간다 — 승인을 물어보는 사이에
    이미 실행돼 버린다. 잠근 채로 사람을 기다려야 한다.
    """
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(json.dumps({
        "kind": "task", "title": "운영 테이블 삭제해줘", "needs_approval": "운영 데이터 삭제",
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    state = {"tasks": []}
    runner.drain_inbox(state, inbox)

    task = state["tasks"][0]
    assert task["status"] == "awaiting_approval"
    assert task["needs_approval"] == "운영 데이터 삭제"
    # 러너가 뽑는 두 경로 어디에도 안 나와야 한다.
    assert agent_state.next_task(state) is None
    assert runner.pick_parallel(state) == []


def test_approval_releases_the_task_and_rejection_blocks_it(tmp_path):
    state = {"tasks": [
        {"id": "T-900", "title": "위험한 일", "status": "awaiting_approval",
         "needs_approval": "운영 데이터 삭제", "priority": 1, "files": []},
        {"id": "T-901", "title": "다른 위험한 일", "status": "awaiting_approval",
         "needs_approval": "결제·구매", "priority": 1, "files": []},
    ]}
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(
        json.dumps({"kind": "approve", "arg": "T-900"}) + "\n"
        + json.dumps({"kind": "reject", "arg": "T-901"}) + "\n",
        encoding="utf-8")

    runner.drain_inbox(state, inbox)

    assert agent_state.find(state, "T-900")["status"] == "pending"
    assert agent_state.find(state, "T-901")["status"] == "blocked"


def test_approval_of_an_unknown_task_is_ignored(tmp_path):
    """없는 작업을 승인해도 아무 일도 일어나지 않는다 — 오타로 뭔가 풀리면 안 된다."""
    state = {"tasks": []}
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(json.dumps({"kind": "approve", "arg": "T-없음"}) + "\n", encoding="utf-8")
    assert runner.drain_inbox(state, inbox) == []
    assert state["tasks"] == []


# --- inbox 자체 -------------------------------------------------------------


def test_inbox_is_emptied_so_orders_do_not_repeat(tmp_path):
    """비우지 않으면 같은 지시가 매 사이클 새 작업으로 쌓인다."""
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(json.dumps({"kind": "task", "title": "그래프 추가"},
                                ensure_ascii=False) + "\n", encoding="utf-8")
    state = {"tasks": []}

    assert len(runner.drain_inbox(state, inbox)) == 1
    assert runner.drain_inbox(state, inbox) == []
    assert len(state["tasks"]) == 1


def test_one_broken_line_does_not_lose_the_others(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(
        "{깨진 줄\n" + json.dumps({"kind": "task", "title": "멀쩡한 지시"},
                                 ensure_ascii=False) + "\n", encoding="utf-8")
    state = {"tasks": []}
    runner.drain_inbox(state, inbox)
    assert [t["title"] for t in state["tasks"]] == ["멀쩡한 지시"]


def test_missing_inbox_is_not_an_error(tmp_path):
    """봇을 안 쓰는 환경에서도 러너는 그냥 돈다."""
    assert runner.drain_inbox({"tasks": []}, tmp_path / "없는파일.jsonl") == []


def test_pause_and_resume_flow_through_the_inbox(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    state = {"tasks": []}

    inbox.write_text(json.dumps({"kind": "pause"}) + "\n", encoding="utf-8")
    runner.drain_inbox(state, inbox)
    assert state["paused"] is True

    inbox.write_text(json.dumps({"kind": "resume"}) + "\n", encoding="utf-8")
    runner.drain_inbox(state, inbox)
    assert state["paused"] is False


# --- 알림 ------------------------------------------------------------------


def test_first_read_does_not_replay_days_of_old_results():
    """봇을 켜자마자 지난 완료 알림이 쏟아지면 아무도 안 읽는다."""
    state = {"tasks": [{"id": "T-1", "status": "done"}, {"id": "T-2", "status": "failed"}]}
    assert bot.changed_statuses({}, state) == []


def test_only_meaningful_transitions_are_reported():
    state = {"tasks": [
        {"id": "T-1", "status": "done", "title": "끝난 일"},
        {"id": "T-2", "status": "in_progress", "title": "하는 중"},
        {"id": "T-3", "status": "failed", "title": "깨진 일"},
    ]}
    before = {"T-1": "in_progress", "T-2": "pending", "T-3": "failed"}
    reported = {t["id"] for t in bot.changed_statuses(before, state)}
    # T-1은 새로 끝났으니 알리고, T-2는 진행 중이라 안 알리고,
    # T-3은 이미 알린 실패라 다시 안 알린다.
    assert reported == {"T-1"}


def test_failure_report_carries_the_reason():
    report = bot.completion_report(
        {"id": "T-9", "status": "failed", "title": "차트", "last_error": "TS2345 타입 오류"})
    assert "T-9" in report and "TS2345" in report


def test_commands_parse_even_with_the_bot_name_attached():
    # 그룹에서는 /status@내봇 형태로 온다.
    assert bot.parse_command("/status@space_bot") == ("/status", "")
    assert bot.parse_command("/approve T-123") == ("/approve", "T-123")
    assert bot.parse_command("그래프 추가해줘") == ("", "그래프 추가해줘")


def test_notifications_go_to_chats_that_actually_opened_the_bot(tmp_path, monkeypatch):
    """알림은 실제로 말을 걸어 온 대화로 보낸다.

    텔레그램은 사용자가 먼저 봇에게 말을 건 적이 없으면 그 id로 보내는 것을
    거절한다("chat not found"). 허용 목록 숫자로 무작정 보내면 사람이 /start를
    누르기 전까지 모든 알림이 조용히 실패한다 — 러너는 일을 끝냈는데 아무
    소식이 없는 상태가 된다. 실제로 배포 직후 그랬다.
    """
    monkeypatch.setattr(bot, "CHATS", tmp_path / "chats.json")
    # 아직 아무도 안 걸었으면 허용 목록으로라도 시도한다.
    assert bot.notify_targets({7}) == [7]

    bot.remember_chat(4242)
    assert bot.notify_targets({7}) == [4242]


def test_an_authorized_message_registers_its_chat_for_later_notices(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "CHATS", tmp_path / "chats.json")
    monkeypatch.setattr(bot, "send", lambda *a, **k: True)
    monkeypatch.setattr(bot, "append_inbox", lambda e: None)

    bot.handle_message("tok", {"chat": {"id": 55}, "from": {"id": 123},
                               "text": "/status"}, {123})
    assert bot.notify_targets(set()) == [55]


def test_an_unauthorized_message_never_registers_a_chat(tmp_path, monkeypatch):
    """모르는 사람의 대화를 알림 주소로 기억하면 결과 보고가 새어 나간다."""
    monkeypatch.setattr(bot, "CHATS", tmp_path / "chats.json")
    monkeypatch.setattr(bot, "send", lambda *a, **k: True)

    bot.handle_message("tok", {"chat": {"id": 66}, "from": {"id": 999},
                               "text": "/status"}, {123})
    assert bot.notify_targets(set()) == []


def test_messages_never_fail_because_of_formatting(monkeypatch):
    """서식을 쓰지 않으므로 어떤 문구가 와도 전달된다.

    작업 제목·실패 로그에 * _ ` [ 가 하나만 섞여도 텔레그램은 Markdown을
    "can't parse entities"로 통째로 거절한다. 실제로 배포 직후 알림 6건이
    전부 이걸로 튕겼다. 이 봇이 나르는 건 장식이 아니라 무엇이 끝났고 무엇이
    깨졌는지다 — 굵은 글씨를 얻자고 전달을 잃을 수 없다.
    """
    sent = {}
    def fake_api(token, method, params, **kw):
        sent.update(params)
        return {"ok": True}
    monkeypatch.setattr(bot, "_api", fake_api)

    assert bot.send("tok", 1, "실패: `unexpected keyword` *arg* _x_ [링크")
    assert "parse_mode" not in sent


def test_report_of_a_failure_with_markdown_characters_is_plain_text():
    report = bot.completion_report({
        "id": "T-9", "status": "failed", "title": "AgentChat *렌더링* 손보기",
        "last_error": "_failure_excerpt() got an unexpected keyword argument 'stderr'",
    })
    assert "```" not in report
    assert "_failure_excerpt" in report  # 사유는 그대로 살아 있다
