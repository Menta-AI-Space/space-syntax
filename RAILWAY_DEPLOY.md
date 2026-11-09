# Railway 배포 가이드

GCP/Firebase Blaze 결제 인증(신분증+카드사진 업로드)을 피하려고 백엔드+DB를
Railway로 옮긴다. 계정 생성·카드 등록은 본인만 할 수 있어서 아래 단계 중
"사람이 할 일"은 직접, "기술 작업"은 Claude가 준비/실행했다.

## 왜 Railway인가

`docker-compose.yml`이 이미 "DB 서비스 + 백엔드 서비스" 2-서비스 구조라
Railway 구조와 거의 1:1로 대응된다. Dockerfile도 이미 완성돼 있어 별도
빌드 설정 없이 그대로 배포 가능.

## 사람이 할 일 (계정/결제 — Claude 대신 못 함)

1. https://railway.app 가입 (GitHub 계정으로 로그인 추천 — 이후 자동배포 연동이 쉬움)
2. 새 프로젝트 생성 → "Empty Project"
3. **DB 서비스 추가**: "+ New" → "Empty Service" (Railway 기본 Postgres 템플릿 말고!)
   - Settings → Source → Docker Image → `postgis/postgis:16-3.4` 입력
   - Settings → Networking → 내부 포트 5432, TCP Proxy 활성화(공개 접속용, 마이그레이션에만 필요하면 나중에 꺼도 됨)
   - Variables 탭에서 설정:
     - `POSTGRES_DB=spacesyntax`
     - `POSTGRES_USER=spacesyntax`
     - `POSTGRES_PASSWORD=` (강력한 비밀번호로 직접 설정 — 여기 채우지 말 것, Railway 대시보드에만 입력)
   - Settings → Volumes → 마운트 경로 `/var/lib/postgresql/data` 볼륨 추가 (영구 저장용, 필수)
4. **백엔드 서비스 추가**: "+ New" → "GitHub Repo" → `space-syntax/space-syntax` 선택
   - Settings → Source → Root Directory: `backend`
   - Settings → Build → Dockerfile 자동 감지됨 (별도 설정 불필요)
5. **결제**: Settings → Billing → 카드 등록 (일반적으로 카드 정보만 요구, 신분증 업로드 없음)

## 사람이 채워야 할 환경변수 (백엔드 서비스 Variables 탭)

로컬 `.env`에는 아래 5개만 값이 들어있고 나머지는 원래 값이 없으면 빈 문자열로
동작한다(해당 기능만 조용히 비활성화). 실제 키 값은 이 문서/채팅에 붙여넣지
말고 Railway 대시보드에 직접 입력할 것.

| 변수 | 이 프로젝트에서의 역할 | 로컬에 값 있음? |
|---|---|---|
| `DATABASE_URL` | DB 서비스 연결 문자열. Railway가 DB 서비스 생성 후 `Variable Reference`로 자동 채워줄 수 있음(`${{Postgres.DATABASE_URL}}` 형태로 참조 가능하면 그걸 씀) | - |
| `DEPTHMAPX_ENABLED` | `true` | O |
| `DEPTHMAPX_COMMAND` | `python /app/tools/depthmapx_bridge.py --cli /usr/local/bin/depthmapXcli` | O |
| `DEPTHMAPX_STRICT` | 로컬 값 그대로 이전 | O |
| `ANALYSIS_CACHE_VERSION` | 로컬 값 그대로 이전 (`depthmapx-tulip-v2`) | O |
| `KAKAO_REST_KEY` | 카카오맵 | 비어있음 — 값 있으면 채우기 |
| `DATA_GO_API_KEY` | data.go.kr | 비어있음 |
| `DATA_SEOUL_GO_API_KEY` | 서울 열린데이터광장 | 비어있음 |
| `VWORLD_API_KEY` / `VWORLD_DOMAIN` | VWorld 지오코딩 | 비어있음 |
| `SGIS_CONSUMER_KEY` / `SGIS_CONSUMER_SECRET` | SGIS 통계청 API | 비어있음 |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | LLM 설명(없으면 규칙기반 템플릿으로 자동 대체) | 비어있음 |
| `OPINET_API_KEY` | 오피넷 | 비어있음 |
| `TELEGRAM_AGENT_BOT_TOKEN` | 웹과 같은 상권분석 Agent를 Telegram에서 쓰는 **서비스 전용** BotFather 토큰. Hermes/자율개발 봇 토큰과 공유 금지 | 비어있음 |
| `TELEGRAM_AGENT_ALLOWED_IDS` | 이 서비스 봇을 사용할 Telegram 숫자 사용자 ID(쉼표 구분). 비우면 전원 거부 | 비어있음 |

### Telegram 상권분석 Agent 켜기

이 봇은 별도 서버가 아니라 기존 `space-syntax` 백엔드 안의 daemon thread로
동작하므로 Railway 서비스나 LLM을 하나 더 띄우지 않는다. 질문은 웹 화면과 동일한
`/api/agent/ask` 함수를 실행한다.

1. Telegram `@BotFather`에서 서비스용 새 봇을 만든다.
2. Railway → `space-syntax` → Variables에 위 두 변수를 입력한다.
3. 배포를 재시작하고 새 봇에서 `/start`를 누른다.
4. `신촌 카페 상권 분석해줘`처럼 질문한다. 후속 질문 문맥은 채팅별 최근 5턴까지
   유지되며 `/new`로 초기화한다.

**중요:** 기존 Hermes `@spacesyntaxbot`이나 `space-syntax-agent` 개발 러너와 같은
토큰을 쓰면 두 프로세스가 `getUpdates`를 동시에 폴링해 충돌한다. 반드시 별도
BotFather 토큰을 사용한다. 토큰 값은 문서·채팅·커밋에 넣지 않는다.

## Claude가 준비/실행한 것

- [x] DB 덤프 (`analysis_cache_entries` 제외 — 689MB 캐시는 새 DB에서 재예열)
- [ ] Railway Postgres 서비스가 뜨면 연결 문자열을 알려주면 → 덤프 복원
- [ ] 백엔드 배포 후 헬스체크·스팟 API 호출로 검증
- [ ] (요청 시) 캐시 재예열 배치 재실행

## 다음 단계

1. 위 "사람이 할 일" 1~5 완료
2. DB 서비스가 뜨면 Railway 대시보드에서 **Postgres 연결 문자열**(Public Networking URL, `postgresql://...`)을 복사해서 여기 채팅에 붙여넣기
3. → Claude가 덤프 복원 진행
4. 백엔드 서비스 배포 URL이 나오면 그것도 공유 → 프론트엔드 `NEXT_PUBLIC_API_BASE_URL`을 그 URL로 바꿔 재배포
