# 러너를 Railway에 올리기

## 회원님이 직접 하셔야 하는 것 (제가 대신 못 합니다)

### 1. 구독 토큰

```bash
claude setup-token
```

브라우저 승인 후 출력되는 1년짜리 토큰. **구독으로 인증하므로 API 과금이 없다.**
어디에도 저장되지 않으니 그 자리에서 복사해야 한다.

### 2. GitHub 토큰

github.com/settings/tokens → **Generate new token** (Fine-grained)

| 항목 | 값 |
|---|---|
| Repository access | Only select repositories → `space-syntax/space-syntax` |
| Contents | Read and write (push = 배포) |
| Issues | Read and write (한도 알림) |
| Metadata | Read-only (자동) |
| **나머지 전부** | **No access** |

**Workflows는 절대 켜지 말 것** — 켜면 러너가 배포 워크플로 자체를 고칠 수 있다.

## Railway 설정

1. `space-syntax` 프로젝트 → **+ Add** → GitHub Repo → `space-syntax/space-syntax`
2. 서비스 이름을 `space-syntax-agent`로 바꾼다 (기존 `space-syntax`와 구분)
3. **Settings → Build**
   - Builder: `Dockerfile`
   - Dockerfile Path: `Dockerfile.agent`
4. **Settings → Deploy**
   - Start Command: `/usr/local/bin/agent-entrypoint.sh`
   - Restart Policy: `Always`
5. **Variables** — 두 개만

   | 이름 | 값 |
   |---|---|
   | `CLAUDE_CODE_OAUTH_TOKEN` | 1번에서 받은 **긴 문자열 그대로** |
   | `GH_TOKEN` | 2번에서 받은 `github_pat_...` |

   ⚠️ **설명문을 값으로 붙여넣지 마세요.** 실제로 한 번 그랬고, 컨테이너는
   `API Error: Header '14' has invalid value: 'Bearer (claude setup-token 결과)'`
   로 죽었다. 값이 `(`나 공백으로 시작하면 잘못 넣은 것이다.

   `ANTHROPIC_API_KEY`는 **넣지 마세요.** 넣는 순간 구독이 아니라 API로 과금됩니다
   (인증 우선순위상 키가 토큰보다 먼저 쓰인다). entrypoint가 이 변수를 발견하면
   시작을 거부합니다.

## 잘 떴는지 확인

Railway 로그에 이게 보이면 정상이다:

```
[entrypoint] 저장소 clone
[entrypoint] 프론트 의존성 설치
[entrypoint] 러너 시작 (구독 인증, API 과금 없음)
[work] T-001 에이전트에 잠식 도구(cannibalization) 붙이기
```

자격증명이 빠졌으면 바로 멈추고 무엇이 없는지 말한다. 조용히 헛돌지 않는다.

## 멈추는 법

```bash
python backend/tools/agent/pause.py on
```

`task-state.json`의 `paused`만 바꾼다. 러너는 진행 중인 작업을 마치고 새 작업을
집지 않는다. 커밋해서 push하면 러너가 다음 사이클에 읽는다.

급하면 Railway에서 서비스를 정지시켜도 된다 — 재시작 시 중간 편집은 버리고
task-state.json 기준으로 되살린다.

## 상태 보기

- 사람이 읽을 요약: `docs/agent/HANDOFF.md`
- 지금 뭐 하는지: `docs/agent/STATUS.md`
- 남은 일: `docs/agent/BACKLOG.md`

셋 다 러너가 매 사이클 다시 쓴다. 정본은 `task-state.json`이다.
