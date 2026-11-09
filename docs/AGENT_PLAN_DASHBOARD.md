# 자율 에이전트 계획서 — 확장분석(대시보드) 버그 담당 (agent/site-dashboard)

**이 에이전트는 "확장 분석" 탭(도보시간 등)의 동작 버그만 다룬다.** NL 챗, 지도 색상,
업종/매출 데이터는 다른 에이전트가 각자 브랜치에서 작업 중이니 건드리지 않는다.

## 소유 파일
- `frontend-next/components/dashboard/DashboardPanel.tsx`
- `frontend-next/components/dashboard/IsochroneSection.tsx`
- `frontend-next/components/dashboard/*` (leaseUtils 등 이 탭 관련 전부)
- `backend/app/services/isochrone.py` (필요시 — 프론트 문제로 보이면 백엔드는 손대지 않는다)

## 확인된 현재 상태 (2026-07-27, 코드 조사만 — 실제 화면 재현은 아직 안 함)
"도보 시간을 바꿔도 아무 변화가 없다"는 오너 지적. 코드를 보니 **"분(minutes)" 상태가 두
군데로 나뉘어 있어 혼동 가능성이 있다** — 이게 원인인지 실제 재현으로 확인할 것:

1. `isoMinutes` (기본값 `[5, 10, 15]`, `DashboardPanel.tsx:60`) — `IsochroneSection`의
   체크박스(`selectedMinutes`)로 어느 밴드를 **보여줄지** 고른다. `handleIsoMinutesToggle`
   (`:285`)이 처리하는데, 이미 3개 밴드를 한 번에 다 받아온 상태에서 **필터링만** 하는
   것으로 보인다 — 켜고 꺼도 지도 위 폴리곤이 실제로 다시 그려지는지 확인 필요.
2. `competitorMinutes` (기본값 `5`) — `IsochroneSection`의 **행 클릭**(`onBandClick`)으로
   바뀌는데(`handleMinutesChange`, `:268`), 이건 경쟁 분석 반경(`competitor_minutes`)에만
   쓰이고 지도의 도보 반경 표시 자체와는 다른 값이다. 오너가 "도보 시간"이라고 부른 게
   체크박스 쪽(1번)인지 행 클릭 쪽(2번)인지에 따라 버그 위치가 다르다.

**결론 없음 — 다음 실행이 실제로 재현해서 어느 컨트롤이 문제인지, 요청은 나가는데 화면이
안 바뀌는지(프론트 렌더 문제) 아니면 요청 자체가 안 나가는지(상태 관리 문제)를 먼저 밝힐 것.**

## 안전 규칙
1. **`agent/site-dashboard` 브랜치에서만 커밋·push. `main` 직접 push 금지.**
2. 시작할 때 `git fetch origin && git checkout agent/site-dashboard && git pull origin agent/site-dashboard`.
3. 커밋 전 검증: `cd frontend-next && npm run typecheck && npm test`
   (backend/isochrone.py를 건드렸다면 `cd backend && .venv\Scripts\python -m pytest tests/test_isochrone.py -q`도).
4. 매 실행 끝에 "진행 상황 로그" 갱신 후 커밋.
5. 확신 없으면 가장 안전한 쪽으로 진행, "질문/확인 필요"에 기록. 멈추지 않는다.
6. 이 클라우드 세션엔 브라우저 확인 도구가 없을 수 있다 — 코드 추적으로 데이터 흐름을 따라가
   버그를 좁히고, 확신이 서면 수정 후 사람이 다음에 눈으로 확인하도록 "질문/확인 필요"에
   "이 부분은 실제 화면 확인 필요"라고 명시할 것.

## 다음에 할 일 (우선순위, 하나씩)
1. **재현 및 원인 확정**: 위 두 컨트롤(체크박스 vs 행클릭) 중 무엇이 "무반응"인지 코드
   흐름으로 좁힌다. `api.isochrone` 호출이 실제로 새 `minutes` 배열로 나가는지, 응답이
   `setData`로 반영되는지, 지도 컴포넌트(KakaoMap)가 `analysisData`/등가 prop 변화를
   감지해 다시 그리는지까지 추적. 원인을 이 문서에 명확히 기록.
2. **수정**: 원인에 따라 상태 갱신 로직 또는 지도 리렌더 트리거 수정.
3. 수정 후 `isoMinutes`/`competitorMinutes` 두 개념이 사용자에게 헷갈리지 않는지(라벨링
   등) 여지가 있으면 짚어 "질문/확인 필요"에 남긴다 — UI 문구 변경은 사람 확인 후.

## 질문/확인 필요

## 진행 상황 로그
- 2026-07-27: 문서 최초 생성. 코드 조사로 두 개의 minutes 상태(isoMinutes/competitorMinutes)를
  발견, 어느 쪽이 실제 버그인지는 미확정 — 다음 실행이 재현할 것.
