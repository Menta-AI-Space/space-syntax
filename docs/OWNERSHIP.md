# 코드 소유권 지도 — 병렬 에이전트의 경계선

여러 에이전트(러너 워커·사람·외부 세션)가 같은 repo에서 동시에 일할 때
충돌을 만드는 건 "직업 분담"(백엔드/프론트)이 아니라 **같은 파일을 같이
만지는 것**이다. 그래서 경계는 직업이 아니라 소유권으로 긋는다.

## 영역과 소유 경로

| 영역 | 소유 경로 | 무엇 |
|---|---|---|
| **agent-core** | `backend/app/routers/agent.py` | 자연어→도구 라우팅, 대화, 서술 |
| **engines** | `backend/app/services/**` (아래 contract 제외) | 계산 엔진 (점수·판정·유사상권…) |
| **api-endpoints** | `backend/app/routers/**` (agent.py 제외) | REST 엔드포인트 |
| **chat-ui** | `frontend-next/components/agent/**`, `frontend-next/lib/answer*.ts` | 대화 화면·답변 렌더링 |
| **dashboard-ui** | `frontend-next/components/**` (agent 제외), `frontend-next/app/**` | 탭·지도·대시보드 |
| **runner-infra** | `backend/tools/agent/**`, `scripts/**`, `Dockerfile.agent`, `railway.agent.json` | 자율 러너·봇·배포 |
| **ingest** | `backend/tools/ingest_*.py` | 공공데이터 적재 |

## 공유 계약 (Core 소유 — 나머지는 읽기 전용)

여러 영역이 동시에 기대는 파일들. **여기를 두 에이전트가 같이 고치면 전부가
멈춘다.** 계약 변경이 필요한 작업은 반드시 ①계약 변경 작업을 먼저 하나로
떼고 ②나머지가 그것에 depends_on을 걸어야 한다.

```
backend/app/models/schemas.py     # API 요청/응답 모양
backend/app/config.py             # 설정 키
frontend-next/lib/api.ts          # 프론트가 아는 API 모양
frontend-next/styles/tokens.css   # 디자인 토큰
.github/workflows/**              # 배포 (사람 소유 — 러너·워커 수정 금지)
```

## 작업 카드 양식 (Architect가 fan-out할 때)

```
title: CoupangAdapter 구현            ← 한 줄
owns: src/adapters/coupang/**        ← 이 안에서만 쓴다
do_not_modify: 공유 계약 + 남의 소유   ← 기본값, 명시 안 해도 적용
depends_on: [T-계약작업]              ← 계약이 먼저 굳는다
done_when: 구현 + 테스트 작성 + 통과   ← 스스로 검증까지
```

## 병렬 판정 4문항 (작업을 쪼갤 때마다)

1. 서로 같은 파일을 거의 안 만지는가? → 아니면 depends_on으로 직렬화
2. 입출력 계약이 확정돼 있는가? → 아니면 계약 작업을 먼저 분리
3. 다른 작업 결과를 기다리는가? → 기다리면 병렬이 아니다
4. 스스로 테스트할 수 있는가? → done_when에 테스트 통과를 넣는다

## 실행 주체

- **러너**(Railway): Architect 프롬프트가 목표를 이 규칙대로 분해해
  task-state에 넣고, 워커 2개가 worktree 격리로 병렬 실행, 마지막에
  Reviewer 작업이 통합 검증한다. (`backend/tools/agent/runner.py`)
- **사람/외부 세션**: 같은 지도를 따른다. 자기 영역 밖을 고쳐야 하면
  그 영역 소유 작업으로 쪼개거나, 공유 계약이면 계약 변경을 먼저 떼라.
