# 처음 오신 분께 (space-syntax)

> **이 문서는 바깥 사람용이다 (2026-08-29 현재 그런 사람은 없다).**
> `chaeyeon573` / `summerforg` 랩탑에서 권한·자격증명·데이터가 막혀서 왔다면
> 이 문서가 아니라 **`docs/SETUP_LAPTOP.md`**를 봐라. 여기 적힌 "소유자에게
> 물어보세요"는 그 경우엔 자기 자신이라 답이 안 나온다.

서울 상권 분석 웹앱입니다. 지도에서 자리를 찍으면 입지 점수·경쟁·수요·수익성을
계산하고, 자연어로 물으면 AI 에이전트가 알맞은 분석 도구를 골라 답합니다.

- 웹사이트: https://real-estate-79e2a.web.app
- 백엔드 API: https://space-syntax-production.up.railway.app

---

## 먼저 읽어야 할 것 하나

**`main`에 push하면 그 즉시 프로덕션에 배포됩니다.** PR도 리뷰도 없습니다
(GitHub Actions가 `backend/`는 Railway로, `frontend-next/`는 Firebase로 자동
배포). 브랜치 보호는 무료 플랜 private 저장소라 걸 수 없습니다.

그래서 **push 전 로컬 검증이 유일한 안전망**입니다. 이건 타협하지 않습니다:

```bash
cd backend && python -m pytest -q
cd frontend-next && npm run typecheck && npm test && npm run lint
```

실패하면 push하지 마세요. 되돌리는 것도 다시 배포해야 하므로 미리 확인하는 게
항상 빠릅니다.

작업 시작 전에는 **항상 `git pull`** — 여러 사람이 같은 main에 직접 push합니다.

---

## 절대 하지 말아야 할 것

- **`.github/workflows/`를 고치지 마세요.** 이 저장소 시크릿(`RAILWAY_TOKEN`,
  `FIREBASE_TOKEN`)이 워크플로를 통해 주입됩니다. 워크플로를 바꾸면 그 값을
  밖으로 빼낼 수 있습니다. 배포 방식을 바꿔야 하면 소유자와 먼저 상의하세요.
- **키·토큰을 커밋하거나 대화창에 붙여넣지 마세요.** `.env`,
  `frontend-next/.env.local`은 gitignore돼 있습니다.
- 운영 데이터 삭제, DB 스키마 변경, 결제, 대규모 기능 삭제는 소유자 승인이
  필요합니다.

---

## 개발 환경 — 백엔드는 로컬에서 못 돌 수 있습니다

백엔드는 적재가 끝난 **로컬 PostGIS(전체 11.3GB)**가 있어야 대부분의 분석이
돌아갑니다. 그 데이터가 없으면 로컬 백엔드는 못 띄웁니다. 그래도 **프론트 작업은
그대로 됩니다** — 라이브 백엔드에 붙이면 됩니다.

> 예전에 "도로망 9.2GB"라고 적혀 있었지만 틀린 숫자입니다(2026-08-22 실측).
> Space Syntax 입력인 `roads_clean`은 92MB뿐이고, 11GB의 대부분은 생활인구·
> 격자·상권 테이블입니다. 자세한 내역은 CLAUDE.md에 있습니다 — "도로망이
> 무거워서 클라우드에 못 올린다"는 근거로 쓰지 마세요.

```bash
git clone https://github.com/space-syntax/space-syntax.git
cd space-syntax/frontend-next
npm ci
```

`frontend-next/.env.local`을 만들고:

```
BACKEND_API_BASE_URL=https://space-syntax-production.up.railway.app
NEXT_PUBLIC_KAKAO_JS_KEY=<소유자에게 받으세요>
```

Kakao JS 키는 도메인이 등록돼 있어야 지도가 뜹니다(`http://localhost:3000`).
받은 키가 안 되면 소유자에게 도메인 등록을 요청하세요.

```bash
npm run dev     # http://localhost:3000
```

백엔드 코드를 고쳐야 하는데 DB가 없다면, 테스트는 대부분 DB 없이 돕니다:

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
.venv/Scripts/python -m pytest -q
```

---

## 저장소 구조

| 경로 | 무엇 |
|---|---|
| `backend/app/routers/` | API 엔드포인트. `agent.py`가 자연어→도구 라우팅 |
| `backend/app/services/` | 계산 엔진 (공간구문론, 경쟁, 수익성, 인구) |
| `backend/tools/ingest_*.py` | 공공데이터를 DB에 적재하는 스크립트 |
| `backend/tools/agent/` | 24시간 자율 러너 + 텔레그램 봇 |
| `frontend-next/` | Next.js 15 / React 19. 화면 전부 |
| `docs/WHATS_NEW.md` | 최근에 뭐가 바뀌었는지 |

**설계 원칙 하나**: 모든 분석은 5초 안에 나와야 합니다. 그래서 공공데이터는
런타임에 API로 부르지 않고 미리 DB에 적재합니다. 자세한 건 `CLAUDE.md`.

---

## 자율 러너에 대해

이 저장소에는 사람 없이 개발·검증·개선을 이어가는 러너가 있습니다
(`backend/tools/agent/`). **2026-08-01 현재 꺼져 있습니다.**

- 러너 상태는 `main`이 아니라 **`agent-state` 브랜치**의 `docs/agent/`에 있습니다
  (main에 두면 상태 커밋마다 Railway가 재배포돼서 옮겼습니다)
- 다시 켜는 건 소유자가 정합니다. 임의로 켜지 마세요.

---

## 막히면

- 최근 변경 내역: `docs/WHATS_NEW.md`
- 협업·배포 규칙 전문: `CLAUDE.md`
- 프론트 작업 규칙: `frontend-next/CLAUDE.md`
- 그래도 막히면 소유자에게 물어보세요. 추측으로 배포하는 것보다 항상 쌉니다.
