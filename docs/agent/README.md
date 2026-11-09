# 자율 개발 상태 — 읽는 법과 쓰는 법

러너와 subagent는 **대화 기억이 아니라 이 디렉터리**를 보고 일한다. 프로세스가
죽거나 컨텍스트가 초기화돼도 여기만 읽으면 마지막 작업부터 이어갈 수 있다.

## 무엇이 정본인가

`task-state.json`이 **정본**이다. 기계가 읽고 쓴다.

나머지 마크다운은 **사람이 읽으라고 러너가 매 사이클 다시 써 주는 사본**이다.
사람이 마크다운을 고쳐도 다음 사이클에 덮어써진다 — 작업을 추가하고 싶으면
`BACKLOG.md`가 아니라 `task-state.json`의 `tasks`에 넣거나, 그냥 사람이 대화로
지시하면 된다.

> 진실을 두 곳에 두면 반드시 어긋난다. 그래서 방향을 한쪽으로만 흐르게 했다:
> `task-state.json` → 마크다운. 반대는 없다.

| 파일 | 쓰는 주체 | 내용 |
|---|---|---|
| `task-state.json` | 러너 (정본) | 전체 상태 |
| `GOALS.md` | 사람 | 무엇을 만들려는가 — 러너는 읽기만 |
| `BACKLOG.md` | 러너 | 남은 작업, 우선순위 순 |
| `STATUS.md` | 러너 | 지금 무엇을 하고 있는가, heartbeat |
| `DECISIONS.md` | 러너 | 되돌리기 어려운 판단과 그 근거 |
| `FAILURES.md` | 러너 | 실패한 작업과 원인 — 같은 실패를 반복하지 않으려고 |
| `HANDOFF.md` | 러너 | 사람이 아침에 읽을 요약 |

`GOALS.md`만 사람이 쓴다. 러너는 그걸 읽고 backlog를 만든다.

## task-state.json 구조

```jsonc
{
  "version": 1,
  "updated_at": "2026-07-29T09:12:00+09:00",
  "heartbeat": "2026-07-29T09:12:00+09:00",   // 러너가 살아 있다는 신호
  "paused": false,                             // kill switch
  "budget": { "daily_limit_usd": null, "spent_today_usd": 0 },
  "auth": { "mode": "subscription", "api_fallback": false },
  "tasks": [
    {
      "id": "T-014",
      "title": "에이전트에 잠식 도구 붙이기",
      "status": "pending",        // pending|in_progress|done|failed|blocked
      "owner": null,              // 진행 중이면 agent 이름
      "priority": 2,              // 낮을수록 먼저
      "files": ["backend/app/routers/agent.py"],  // 소유 범위 — 밖은 안 건드린다
      "branch": null,
      "attempts": 0,
      "blocked_reason": null,     // blocked일 때만
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "runs": [
    {
      "task_id": "T-014", "agent": "builder", "model": "sonnet",
      "started_at": "...", "ended_at": "...",
      "result": "done", "cost_usd": 0.0, "note": ""
    }
  ]
}
```

## 재개 규칙

러너가 부팅하면:

1. `task-state.json`을 읽는다. 없으면 `GOALS.md`를 보고 새로 만든다.
2. `paused`가 true면 아무것도 하지 않고 heartbeat만 남긴다.
3. `status="in_progress"`인 작업을 `pending`으로 되돌린다. 그 작업의 worktree는
   그대로 남아 있으므로 하던 자리에서 다시 시작한다. **중간 결과를 버리지
   않는다** — 죽은 시점의 편집은 worktree에 있다.
4. `attempts >= 2`인 작업은 `blocked`로 옮기고 `FAILURES.md`에 적는다.
   같은 실패를 무한히 반복하는 게 가장 비싼 실패 방식이다.
5. 우선순위 순으로 다음 작업을 고른다.

## 멈추는 법 (kill switch)

```bash
# 러너를 건드리지 않고 세우기 — 다음 사이클에서 스스로 멈춘다
python backend/tools/agent/pause.py on
python backend/tools/agent/pause.py off
```

`task-state.json`의 `paused`만 바꾼다. 진행 중인 작업은 끝까지 마치고 새 작업을
집지 않는다. 급하면 Railway에서 서비스를 정지시켜도 되지만, 그러면 진행 중인
worktree가 중간 상태로 남는다(재개 규칙 3번이 처리한다).
