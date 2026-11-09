"""텔레그램으로 러너에게 일을 시키고 결과를 받는다.

러너와 **같은 컨테이너의 다른 프로세스**로 돈다. 둘은 소켓이나 큐 없이 파일로만
대화한다:

    봇  ──(새 지시)──▶  agent-inbox.jsonl  ──▶  러너가 사이클 시작에 비움
    봇  ◀──(상태 변화)──  docs/agent/task-state.json  (러너가 쓰고 봇은 읽기만)

**상태 파일의 유일한 작성자는 러너다.** 봇이 같이 쓰면 러너가 방금 in_progress로
바꾼 걸 봇이 옛 값으로 덮는 사고가 난다. 그래서 봇은 받은 지시를 inbox에 적기만
하고, 작업으로 만드는 일은 러너가 한다.

폴링(getUpdates)을 쓴다. 웹훅은 공개 URL과 인증서가 필요하고 그 주소를 아무나
두드릴 수 있는데, 폴링은 컨테이너가 텔레그램에 나가서 물어보는 방식이라 열어 줄
포트가 없다. 하루 수십 건 규모에서 폴링의 지연은 무의미하다.

외부 라이브러리를 쓰지 않는다(표준 urllib). Bot API는 단순한 HTTP이고, 의존성
하나가 늘면 컨테이너 빌드와 버전 관리가 같이 늘어난다.

돌리는 법:

    python backend/tools/agent/telegram_bot.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import state as st  # noqa: E402

API = "https://api.telegram.org/bot{token}/{method}"

#: 봇이 받은 지시를 적어 두는 곳. 러너가 사이클 시작에 읽고 비운다.
#:
#: 저장소 밖(작업공간 루트)에 둔다 — 안에 두면 `git add -A`에 딸려 올라가거나
#: 러너의 `git reset --hard`에 지워진다.
INBOX = Path(os.environ.get("AGENT_INBOX") or "/workspace/agent-inbox.jsonl")

#: 상태 파일을 다시 읽는 간격(초). 러너 사이클보다 촘촘하면 낭비다.
STATE_POLL_S = 20

#: 다른 인스턴스가 같은 봇을 폴링할 때 물러나 있는 시간(초). 배포 중 옛/새
#: 컨테이너가 겹치는 구간에서만 생기고 저절로 풀린다.
CONFLICT_BACKOFF_S = 20

#: 사람에게 알릴 상태. pending·in_progress는 알리지 않는다 — 진행 중 알림이
#: 많으면 정작 중요한 완료·실패 알림이 묻힌다.
NOTIFY_STATUSES = ("done", "failed", "blocked", "awaiting_approval")

_STATUS_KO = {
    "done": "✅ 완료",
    "failed": "❌ 실패",
    "blocked": "⛔ 막힘",
    "awaiting_approval": "⚠️ 승인 필요",
}


# ---------------------------------------------------------------------------
# 판단 — 순수 함수 (테스트가 여기를 본다)
# ---------------------------------------------------------------------------


def allowed_ids(raw: str | None) -> set[int]:
    """지시를 받아도 되는 텔레그램 숫자 id들.

    username(@handle)이 아니라 **숫자 id**로 검사한다 — username은 남이 바꿔
    가로챌 수 있지만 숫자 id는 계정에 고정이다.
    """
    out: set[int] = set()
    for chunk in (raw or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            out.add(int(chunk))
    return out


def is_authorized(user_id: Any, allowed: set[int]) -> bool:
    """이 사람의 지시를 실행해도 되는가.

    허용 목록이 **비어 있으면 아무도 통과시키지 않는다.** 변수를 깜빡 비워 둔
    상태에서 "비었으니 전부 허용"으로 열리면, 봇 주소를 아는 누구나 이 저장소에
    코드를 쓰게 된다. 설정 실수의 대가가 전면 개방이어서는 안 된다.
    """
    if not allowed:
        return False
    try:
        return int(user_id) in allowed
    except (TypeError, ValueError):
        return False


#: 사람 승인 없이 진행하면 안 되는 일. 제목이나 파일 범위에 이 말이 있으면
#: 작업을 awaiting_approval로 잠근다.
#:
#: 문서에 "이런 건 승인받아라"라고 적어 두는 것만으로는 아무것도 막지 못한다 —
#: 지키는지 확인할 방법이 없다. 여기서 코드로 건다.
_APPROVAL_RULES: list[tuple[tuple[str, ...], str]] = [
    (("드롭", "drop table", "테이블 삭제", "데이터 삭제", "delete from", "truncate"),
     "운영 데이터 삭제"),
    (("마이그레이션", "migration", "스키마 변경", "schema change"), "DB 스키마 변경"),
    (("시크릿", "secret", "api key", "토큰 발급", "권한 변경", "credential"), "비밀키·권한 변경"),
    (("결제", "구독", "유료", "billing", "payment", "purchase"), "결제·구매"),
    (("워크플로", "workflow", ".github/"), "배포 워크플로 변경"),
]


def approval_reason(title: str, files: list[str] | None = None) -> str | None:
    """이 작업이 사람 승인을 받아야 한다면 그 사유. 아니면 None."""
    blob = (title or "").lower() + " " + " ".join(files or []).lower()
    for keywords, reason in _APPROVAL_RULES:
        if any(k in blob for k in keywords):
            return reason
    return None


def parse_command(text: str) -> tuple[str, str]:
    """받은 메시지를 (명령, 인자)로 나눈다. 명령이 아니면 ("", 원문)."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return "", text
    head, _, rest = text.partition(" ")
    # 그룹에서는 /status@내봇 형태로 온다.
    return head.split("@")[0].lower(), rest.strip()


def status_summary(state: dict) -> str:
    """/status 응답. 지금 뭘 하고 있는지 한눈에."""
    tasks = state.get("tasks") or []
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t.get("status", "?")] = counts.get(t.get("status", "?"), 0) + 1
    running = [t for t in tasks if t.get("status") == "in_progress"]
    waiting = [t for t in tasks if t.get("status") == "awaiting_approval"]

    lines = []
    if state.get("paused"):
        lines.append("⏸ 멈춰 있습니다 (/resume 으로 재개)")
    lines.append(f"심장박동: {state.get('heartbeat') or '기록 없음'}")
    lines.append(
        "작업: "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        if counts else "작업: 없음"
    )
    if running:
        lines.append("\n*지금 하는 일*")
        lines += [f"· {t['id']} {t['title'][:60]}" for t in running]
    if waiting:
        lines.append("\n*승인 대기*")
        lines += [f"· {t['id']} {t['title'][:60]} — {t.get('needs_approval') or ''}"
                  for t in waiting]
    return "\n".join(lines)


def changed_statuses(before: dict[str, str], state: dict) -> list[dict]:
    """지난번에 본 것과 달라진 작업들 중 알릴 가치가 있는 것.

    처음 볼 때(before가 비었을 때)는 아무것도 알리지 않는다 — 봇을 켜자마자
    지난 며칠치 완료 알림이 쏟아지면 아무도 안 읽는다.
    """
    if not before:
        return []
    out = []
    for task in state.get("tasks") or []:
        status = task.get("status")
        if status in NOTIFY_STATUSES and before.get(task.get("id")) != status:
            out.append(task)
    return out


def snapshot(state: dict) -> dict[str, str]:
    return {t.get("id"): t.get("status") for t in (state.get("tasks") or []) if t.get("id")}


def completion_report(task: dict) -> str:
    """완료·실패 보고문. 무엇이 됐고 무엇을 확인해야 하는지."""
    head = _STATUS_KO.get(task.get("status"), task.get("status", "?"))
    lines = [f"{head}  {task.get('id')}", task.get("title", "")]
    if task.get("status") == "failed" and task.get("last_error"):
        lines += ["", str(task["last_error"])[:600]]
    if task.get("status") == "blocked" and task.get("blocked_reason"):
        lines += ["", f"사유: {task['blocked_reason']}"]
    if task.get("status") == "awaiting_approval":
        lines += ["", f"사유: {task.get('needs_approval') or '확인 필요'}",
                  f"진행하려면 /approve {task.get('id')} , 취소하려면 /reject {task.get('id')}"]
    if task.get("files"):
        lines += ["", "범위: " + ", ".join(task["files"][:4])]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# I/O — 얇게
# ---------------------------------------------------------------------------


def _api(token: str, method: str, params: dict, *, timeout: int = 40) -> dict | None:
    """Bot API 호출. **예외를 던지지 않는다.**

    네트워크 한 번 끊겼다고 봇이 죽으면 사람은 지시를 보내고도 아무 답을 못 받는다.
    24시간 도는 물건에서 조용히 죽는 것이 가장 나쁜 결말이다.
    """
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(
        {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
         for k, v in params.items() if v is not None}
    ).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        # 텔레그램은 **왜** 거절했는지를 응답 본문에 담는다("chat not found",
        # "can't parse entities" 등). 상태코드만 찍으면 400이라는 것만 알고
        # 원인은 영영 모른다 — 이 프로젝트에서 이미 여러 번 당한 실패 방식이다.
        try:
            body = json.loads(exc.read().decode())
        except (ValueError, OSError):
            body = {}
        why = body.get("description") or str(exc)
        print(f"[bot] {method} 거절: {why}", flush=True)
        return body or None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        print(f"[bot] {method} 실패: {exc}", flush=True)
        return None


def send(token: str, chat_id: int | str, text: str) -> bool:
    """메시지 하나 보낸다.

    **서식(parse_mode)을 쓰지 않는다.** 보내는 내용에 작업 제목·실패 로그·사용자
    문구가 그대로 들어가는데, 거기 `*` `_` `` ` `` `[` 가 하나만 섞여도 텔레그램이
    "can't parse entities"로 통째로 거절한다. 실제로 배포 직후 알림 6건이 전부
    이걸로 튕겼다. 굵은 글씨를 얻자고 전달 자체를 잃는 건 남는 장사가 아니다 —
    이 봇이 나르는 건 장식이 아니라 "무엇이 끝났고 무엇이 깨졌는지"다.
    """
    resp = _api(token, "sendMessage",
                {"chat_id": chat_id, "text": text,
                 "disable_web_page_preview": True}, timeout=20)
    ok = bool(resp and resp.get("ok"))
    # **성공도 적는다.** 성공을 조용히 넘기면 "보냈는데 안 온다"와 "아예 안
    # 보냈다"를 구분할 수 없다. 실제로 그 상태가 돼서 원인을 못 짚었다 —
    # 로그가 없으면 남은 건 추측뿐이고, 추측으로 고친 것은 고친 게 아니다.
    head = " ".join(text.split())[:40]
    print(f"[bot] → {chat_id}: {'보냄' if ok else '실패'} | {head}", flush=True)
    return ok


#: 실제로 말을 걸어 온 대화들. 알림은 여기로만 보낸다.
#:
#: 텔레그램은 **사용자가 먼저 봇에게 말을 건 적이 없으면** 그 id로 보내는 것을
#: 거절한다("chat not found"). 그래서 허용 목록의 숫자 id로 무작정 보내면,
#: 사람이 /start를 누르기 전까지 모든 알림이 조용히 실패한다 — 러너는 일을
#: 끝냈는데 아무 소식이 없는 상태가 된다.
CHATS = Path(os.environ.get("AGENT_CHATS") or "/workspace/agent-chats.json")


def remember_chat(chat_id: int | str) -> None:
    try:
        known = set(json.loads(CHATS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        known = set()
    if chat_id in known:
        return
    known.add(chat_id)
    try:
        CHATS.parent.mkdir(parents=True, exist_ok=True)
        CHATS.write_text(json.dumps(sorted(known, key=str)), encoding="utf-8")
    except OSError:
        pass


def notify_targets(allowed: set[int]) -> list[int | str]:
    """알림을 보낼 대화들. 아직 아무도 말을 안 걸었으면 허용 목록으로 시도한다."""
    try:
        known = json.loads(CHATS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = []
    return known or sorted(allowed)


def append_inbox(entry: dict) -> None:
    """받은 지시를 러너가 가져갈 자리에 적는다."""
    INBOX.parent.mkdir(parents=True, exist_ok=True)
    with INBOX.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_state_quietly() -> dict:
    """상태를 읽되, 러너가 쓰는 도중이면 조용히 포기한다.

    러너는 임시 파일에 쓰고 바꿔치기하므로 반쪽 파일이 남을 일은 거의 없지만,
    읽기가 실패했다고 봇을 죽일 이유는 전혀 없다.
    """
    try:
        return st.load()
    except (OSError, json.JSONDecodeError):
        return {}


HELP = """[할 수 있는 것]

그냥 하고 싶은 걸 말하면 작업으로 만듭니다.
예) 입지 점수 화면에 그래프 추가해줘

/goal 목표…  큰 목표를 설계→병렬 작업으로 분해해 진행
/status  지금 뭐 하는 중인지
/pause  잠시 멈춤     /resume  재개
/approve T-123  승인   /reject T-123  취소
"""


def handle_message(token: str, msg: dict, allowed: set[int]) -> None:
    """메시지 하나 처리."""
    chat_id = (msg.get("chat") or {}).get("id")
    user_id = (msg.get("from") or {}).get("id")
    text = msg.get("text") or ""
    if chat_id is None:
        return

    print(f"[bot] ← chat {chat_id} / user {user_id}: {(text or '')[:60]}", flush=True)

    if not is_authorized(user_id, allowed):
        # **답하지 않는다.** 거절 메시지를 보내면 이 봇이 살아 있다는 걸
        # 알려 주는 꼴이고, 누가 두드리는지에 대한 정보만 상대에게 준다.
        print(f"[bot] 허용되지 않은 사용자 {user_id} — 무시", flush=True)
        return

    # 이 대화로는 보낼 수 있다는 게 방금 증명됐다 — 알림 주소로 기억해 둔다.
    remember_chat(chat_id)

    cmd, arg = parse_command(text)

    if cmd in ("/start", "/help"):
        send(token, chat_id, HELP)
        return

    if cmd == "/status":
        send(token, chat_id, status_summary(load_state_quietly()) or "상태를 읽지 못했습니다.")
        return

    if cmd == "/goal":
        # 큰 목표 — 러너의 Architect가 계약→병렬→리뷰로 분해한다.
        if not arg.strip():
            send(token, chat_id, "목표를 같이 적어 주세요. 예: /goal 프랜차이즈 비교 기능 추가")
            return
        append_inbox({"kind": "goal", "title": arg.strip(), "chat_id": chat_id,
                      "at": st.now_kst()})
        send(token, chat_id,
             "목표로 접수했습니다. 설계(작업 분해)부터 시작해서 진행 상황을 알려 드릴게요.")
        return

    if cmd in ("/pause", "/resume", "/approve", "/reject"):
        append_inbox({"kind": cmd.lstrip("/"), "arg": arg, "chat_id": chat_id,
                      "at": st.now_kst()})
        done = {"pause": "멈춤을 요청했습니다.", "resume": "재개를 요청했습니다.",
                "approve": f"{arg or '작업'} 승인을 전달했습니다.",
                "reject": f"{arg or '작업'} 취소를 전달했습니다."}[cmd.lstrip("/")]
        send(token, chat_id, done + " 다음 사이클에 반영됩니다.")
        return

    if not text.strip():
        return

    reason = approval_reason(text)
    append_inbox({"kind": "task", "title": text.strip(), "chat_id": chat_id,
                  "needs_approval": reason, "at": st.now_kst()})
    if reason:
        send(token, chat_id,
             f"접수했습니다. 다만 {reason}에 해당해서 바로 시작하지 않고 "
             "승인을 먼저 여쭙겠습니다.")
    else:
        send(token, chat_id, "접수했어요. 작업으로 만들어 시작하겠습니다.")


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    allowed = allowed_ids(os.environ.get("TELEGRAM_ALLOWED_IDS"))
    if not token:
        print("[bot] TELEGRAM_BOT_TOKEN이 없다 — 봇 없이 러너만 돈다", flush=True)
        return 0
    if not allowed:
        print("[bot] TELEGRAM_ALLOWED_IDS가 비어 있다. 아무 지시도 받지 않는다 "
              "— 변수를 채우고 다시 배포할 것", file=sys.stderr, flush=True)

    print(f"[bot] 시작 (허용 사용자 {len(allowed)}명)", flush=True)
    offset: int | None = None
    seen = snapshot(load_state_quietly())
    last_state_poll = 0.0

    while True:
        updates = _api(token, "getUpdates",
                       {"offset": offset, "timeout": 25}, timeout=40)

        # 배포 중에는 옛 컨테이너와 새 컨테이너가 잠깐 겹친다. 둘이 동시에
        # 폴링하면 텔레그램이 한쪽을 Conflict로 끊는데, 즉시 다시 물으면 서로
        # 끊어 대며 로그만 채운다. 물러나 있으면 옛 쪽이 곧 사라지고 정리된다.
        if updates and not updates.get("ok") and "conflict" in str(
                updates.get("description", "")).lower():
            time.sleep(CONFLICT_BACKOFF_S)
            continue

        for update in (updates or {}).get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message") or update.get("edited_message")
            if msg:
                try:
                    handle_message(token, msg, allowed)
                except Exception as exc:  # 메시지 하나 때문에 봇이 죽지 않는다
                    print(f"[bot] 처리 실패: {exc}", flush=True)

        # 상태 변화 알림 — 러너가 결과를 남기면 사람에게 옮긴다.
        if time.time() - last_state_poll >= STATE_POLL_S:
            last_state_poll = time.time()
            state = load_state_quietly()
            targets = notify_targets(allowed)
            for task in changed_statuses(seen, state):
                for chat_id in targets:
                    send(token, chat_id, completion_report(task))
            if state:
                seen = snapshot(state)


if __name__ == "__main__":
    raise SystemExit(main())
