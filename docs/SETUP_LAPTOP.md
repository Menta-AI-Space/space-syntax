# 새 랩탑을 작업 가능한 상태로 만들기

저장소는 조직 소유(`space-syntax/space-syntax`)이고 `chaeyeon573`·`summerforg`
**둘 다 admin**이다. 그래서 **여기서 막히는 건 권한 문제가 아니라 자격증명이
그 랩탑에 안 깔린 문제**다. 승인을 기다리지 말고 아래대로 자기 손으로 채우면 된다.

한 줄 요약:

```bash
bash scripts/setup-laptop.sh
```

점검하고, 없는 것만 채우고, 운영 DB에 실제로 붙는지까지 확인한다. 비밀값은
화면에 찍지 않는다. 이미 있는 값은 덮지 않는다(`--force-db`로 강제).

---

## 1. GitHub — 두 계정 다 admin이다 (2026-08-30 조직 이전 완료)

저장소는 **`space-syntax/space-syntax`**다. 옛 경로 `chaeyeon573/space-syntax`는
리다이렉트되지만 새로 쓰는 곳에는 쓰지 마라.

| 계정 | 역할 |
|---|---|
| `chaeyeon573` | admin |
| `summerforg` | **admin** |

그래서 **권한 때문에 막힐 일이 없다.** Settings·Secrets·Actions 재실행 전부
자기 계정으로 된다. 확인은 이 한 줄이면 된다:

```bash
gh secret list -R space-syntax/space-syntax
```

`FIREBASE_TOKEN`·`NEXT_PUBLIC_KAKAO_JS_KEY`·`RAILWAY_TOKEN` 세 개가 보이면 정상이다.

랩탑에 로그인이 안 돼 있으면:

```bash
gh auth login          # 자기 계정으로
gh auth status         # 지금 누구인지
```

커밋 작성자(`user.name`/`user.email`)는 `gh` 로그인과 무관하다.

> **왜 옮겼나.** 이전 전에는 개인 계정 소유 저장소라 협업자 권한 단계가 `write`
> 하나뿐이었다. API로 `permission=admin`을 넣어도 `204`만 돌아오고 역할은 안
> 바뀌었다(`maintain`도 마찬가지). 역할 선택은 조직 소유 저장소에서만 된다.
> 이전 후 같은 명령이 그대로 먹혔다.
>
> 이전으로 깨진 것은 없었다 — 시크릿 3개 타임스탬프 그대로, 협업자·이슈·PR 유지,
> 옛 URL 리다이렉트. **단 Railway의 GitHub 저장소 연결은 옛 경로를 가리키므로
> 대시보드에서 다시 걸어야 한다** (배포 자체는 `deploy.yml`이 프로젝트 ID로
> 하므로 영향 없다).

**조직 Owner는 별개다.** 저장소 admin으로 이 저장소 일은 다 되지만, 조직 설정·
청구·새 저장소 생성에는 조직 Owner가 필요하고 그건 `admin:org` 스코프를 요구한다:

```bash
gh auth refresh -h github.com -s admin:org
```

## 2. 데이터 — 로컬 DB를 열어줄 필요가 없다

한쪽 랩탑의 로컬 PostGIS(11.3GB)를 포트 개방하거나 터널로 넘길 필요가 **없다.**
**운영 DB에 그냥 붙으면 된다.** 2026-08-30에 토지가치 파생 테이블 12개를 올려서
로컬 65개 / 운영 64개가 됐다 — 차이는 `cv_folds` 하나뿐이고 그건 옮기는 게 아니라
`tools/compute_cv_folds.py`로 만드는 것이다 (`docs/INFRA.md`). 주요 행 수:

| 테이블 | 행 수 |
|---|---|
| `land_prices` | 887,699 |
| `parcel_centroids` | 1,106,139 |
| `roads_clean` | 203,983 |
| `golmok_sales` | 852,424 |

Railway가 이미 TCP 프록시를 열어 뒀다. 접속 문자열은 DB 서비스 변수에서
조립한다 — `DATABASE_PUBLIC_URL` 같은 건 없고, 내부용 `DATABASE_URL`은
`postgres.railway.internal`을 가리켜서 **밖에서는 안 붙는다.**

```
postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@$RAILWAY_TCP_PROXY_DOMAIN:$RAILWAY_TCP_PROXY_PORT/$POSTGRES_DB
```

이 조립을 `scripts/setup-laptop.sh`가 대신 한다. 손으로 보려면:

```bash
railway login
railway variables -p space-syntax -s postgres -e production --json
```

⚠️ `--json`을 빼지 마라. 표 형식은 비밀값을 화면에 그대로 뿌린다.

### 운영 DB에 붙었을 때의 규칙

- **읽기 전용으로 쓴다.** 계정은 `spacesyntax` 하나뿐이라 기술적으로는 쓰기가
  된다. 여기서 `DROP`·`UPDATE`를 치면 라이브가 즉시 깨진다.
- **적재 스크립트(`backend/tools/ingest_*.py`)를 운영 DSN으로 돌리지 마라.**
  적재는 로컬 DB에서 하고, 검증 뒤 따로 운영에 넣는다.
- 스키마 변경은 `scripts/migrations/`를 통해서만 한다.

로컬 DB를 쓰던 랩탑에서 실수로 운영을 가리키게 되는 걸 막으려고, 스크립트는
이미 있는 `DATABASE_URL`을 덮지 않는다. 바꾸려면 `--force-db`를 명시해야 한다.

## 3. Railway / Firebase — 계정은 하나다

| 무엇 | 계정 |
|---|---|
| Railway | `cmdysdntqh08@gmail.com` (Chae Yeon Kim) |
| Firebase | `chaeyeon573` 로 배포 (`real-estate-79e2a`) |

```bash
railway login
npx -y firebase-tools@latest login
```

**배포에는 둘 다 필요 없다.** main에 push하면 GitHub Actions가 저장소 시크릿
(`RAILWAY_TOKEN`·`FIREBASE_TOKEN`)으로 배포한다. CLI 로그인이 필요한 건
`railway logs`·`railway variables`처럼 **왜 깨졌는지 볼 때**다.

라이브인지 확인하는 기준은 프로젝트 이름이 아니라 `RAILWAY_PUBLIC_DOMAIN`이다.
상세는 `docs/INFRA.md`.

## 4. 프론트 — Kakao 지도 키

`frontend-next/.env.local`:

```
BACKEND_API_BASE_URL=https://space-syntax-production.up.railway.app
NEXT_PUBLIC_API_BASE_URL=https://space-syntax-production.up.railway.app
NEXT_PUBLIC_KAKAO_JS_KEY=<Kakao developers 콘솔의 JavaScript 키>
```

지도가 안 뜨면 키가 틀린 게 아니라 **도메인 등록이 안 된 것**일 때가 많다.
Kakao developers → 내 애플리케이션 → 플랫폼 → Web 에 `http://localhost:3000`이
들어 있어야 한다.

`VWORLD_API_KEY`는 공시지가 원본을 받을 때만 필요하다(V-World 무료 로그인).
이미 적재돼 있으므로 평소 작업에는 없어도 된다.

## 5. 다 됐는지 확인

```bash
cd frontend-next && npm ci && npm run lint && npm run typecheck && npm test
```

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
.venv/Scripts/python -m pytest -q
```

이 두 개가 통과하면 push해도 되는 랩탑이다.

---

## 여전히 막히면 — 증상별

| 증상 | 원인 | 조치 |
|---|---|---|
| `gh secret list`가 403 | 그 랩탑에 gh 로그인 없음 / 다른 계정 | `gh auth login` 또는 `gh auth switch` |
| `git push`가 404 | remote가 옛 경로 | `git remote set-url origin https://github.com/space-syntax/space-syntax.git` |
| `railway variables`가 권한 오류 | 그 랩탑에 railway 로그인 없음 | `railway login` |
| 백엔드가 DB 없다고 죽는다 | `DATABASE_URL`이 로컬을 가리킴 | `bash scripts/setup-laptop.sh --force-db` |
| 지도가 회색 | Kakao 도메인 미등록 | 콘솔에 `http://localhost:3000` 추가 |
| `npm run lint` 실패로 배포만 죽는다 | lint를 빼고 push함 | push 전 lint 필수 (CLAUDE.md §3) |
| Actions의 「Railway (backend)」만 `Unauthorized`로 실패, 프론트는 초록 | 저장소 시크릿 `RAILWAY_TOKEN`(계정 토큰)이 폐기됨. **2026-10-22는 Railway 보안 사고로 Railway가 모든 API 토큰을 일괄 무효화**했다(status.railway.com 10/22 04:17 UTC, 같은 날 빌드도 전 요금제 일시 중단 — `railway up`이 「Deploys have been paused temporarily」). 우리 쪽 실수나 결제 문제가 아니었다 | **Railway 프로젝트 주인 계정**(`cmdysdntqh08@gmail.com`)으로 railway.com/account/tokens에서 새 토큰 → GitHub 저장소 Settings → Secrets → Actions의 `RAILWAY_TOKEN` 교체 → 실패한 run 재실행. 프론트만 라이브에 오르고 백엔드는 옛 버전이라 **새 API를 부르는 화면이 라이브에서 깨진다** |
| PowerShell에서 `railway`가 「인식되지 않습니다」 | npm 전역 폴더가 PowerShell PATH에 없다(Git Bash에선 됨). 사용자 폴더가 한글이라 `.cmd` 우회도 경로를 못 찾는다 | Git Bash에서 실행하거나, `railway.exe`(`%APPDATA%\npm\node_modules\@railway\cli\bin\`)를 영문 경로에 복사해 실행 |
| `railway whoami`는 되는데 `railway up`이 `Unauthorized` | **다른 Railway 계정**으로 로그인함. `railway list`가 비어 있으면 그 계정엔 프로젝트가 없다 (2026-10-22: `neverforg77@gmail.com`으로 로그인 → 거부) | 위 표의 Railway 계정으로 `railway login`, 또는 그 계정에서 이 계정을 워크스페이스에 초대 |

`docs/ONBOARDING.md`는 **바깥 사람**이 생겼을 때 읽히는 문서다(현재 없음).
이 저장소를 쓰는 두 랩탑은 이 문서를 본다.
