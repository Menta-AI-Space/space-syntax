# space-syntax MCP — 분석 엔진을 AI 도구로 직접 쓰기

Claude·Cursor·기타 MCP 클라이언트가 이 엔진의 실제 계산을 도구로 부른다.
(서울 PostGIS 11GB 위에서 도는 진짜 엔진이다 — 프롬프트 흉내가 아니다.)

## 접속

```bash
claude mcp add --transport http space-syntax \
  https://space-syntax-production.up.railway.app/mcp/ \
  --header "X-API-Key: <API_ACCESS_TOKEN 값>"
```

- 끝의 슬래시(`/mcp/`)까지 써야 한다 — 없으면 307을 거친다.
- `API_ACCESS_TOKEN`이 배포에 설정돼 있으면 같은 값을 X-API-Key로 보내야
  한다(REST와 같은 정책). 설정돼 있지 않으면 헤더 없이 붙는다.

## 도구 4개

| 도구 | 하는 일 |
|---|---|
| `score_site` | 주소(또는 좌표)+업종 → 입지 점수·서울 상위%·상권 매출 벤치마크 |
| `judge_lease` | 좌표+월세 → 계약 판정·예상 매출(범위)·손익분기·협상 조건 |
| `compare_candidates` | 후보 2~5곳 → 순위·계약 가능 월세 |
| `find_similar_areas` | 좌표+업종 → 구조가 닮은 서울 상권 3곳과 성과 비교 |

전형적인 흐름: `score_site("신촌역", "cafe")` → 좌표를 받아
`judge_lease(lat, lng, monthly_rent_man=300)` → `find_similar_areas(...)`.

## 구현 메모 (개발자용)

- `backend/app/mcp_server.py` — REST와 **같은 함수**를 부르는 얇은 어댑터.
  로직을 여기 새로 쓰지 말 것(두 경로가 갈라진다).
- 인증: FastAPI 의존성은 mount된 ASGI 앱을 지나치므로 `_ApiKeyGuard`가
  같은 X-API-Key를 직접 검사한다.
- 세션 매니저 lifespan은 main.py startup에서 직접 연다 — 마운트된 서브앱의
  lifespan은 starlette가 실행해 주지 않는다.
- ⚠ `mcp==1.12.4` 고정. 2.0은 starlette>=1.x를 끌어와 fastapi 0.115와
  충돌한다(설치 순간 백엔드 전체가 안 뜬다 — 2026-08-09 실측).
