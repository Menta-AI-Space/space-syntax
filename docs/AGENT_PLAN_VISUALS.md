# 자율 에이전트 계획서 — 지도·시각화 담당 (agent/site-visuals)

**이 에이전트는 지도 색상·범례·시각적 표시만 다룬다.** NL 챗 내용(agent.py/AgentChat.tsx),
대시보드 도보시간 버그, 업종/매출 데이터는 다른 에이전트가 각자 브랜치에서 작업 중이니
건드리지 않는다.

## 소유 파일
- `frontend-next/components/map/KakaoMap.tsx` (특히 `MapLegend`, `METRIC_PALETTE`, `HEAT_STOPS`)
- `frontend-next/lib/metricInfo.ts` (METRIC_INFO — 지표 설명 문구)
- `frontend-next/components/agent/AnalysisVisuals.tsx`
- `frontend-next/components/agent/CandidateMiniMap.tsx`

## 확인된 현재 상태 (2026-07-27, 조사 완료)
`MapLegend`(`KakaoMap.tsx:505`)는 이미 그라데이션 바 + "낮음/높음"(또는 mean_depth는
"외곽(깊음)/중심(얕음)") 라벨을 보여준다. **완전히 없는 게 아니다.** 오너가 지적한 "색상이
뭘 뜻하는지 정확히 표시 안 됨"은 다음 중 하나일 가능성이 높다 — 실제 화면 확인 후 원인을
좁혀서 이 문서에 기록할 것:
1. 범례가 화면에서 눈에 잘 안 띄거나(위치·크기), 처음 보는 사람이 놓치기 쉬움.
2. "높음/낮음"만으로는 그 지표(예: choice, integration, mean_depth)가 **무엇을 재는지**
   전달이 안 됨 — "접근성 57" 처럼 숫자만 봐도 감이 안 잡히는 것과 같은 문제.
   `frontend-next/lib/metricInfo.ts`의 `METRIC_INFO[value].easy`/`.explain`이 지도 옆
   analyze 탭 옵션에는 쓰이는데, **지도 범례 자체에는 안 쓰이고 있을 수 있음** — 확인할 것.
3. 새 분석 탭(에이전트 결과)의 지도에는 범례가 아예 안 붙어 있을 수 있음(AnalysisVisuals.tsx
   확인 — 거긴 다른 지도 컴포넌트를 쓸 수 있음).

## 안전 규칙
1. **`agent/site-visuals` 브랜치에서만 커밋·push. `main` 직접 push 금지.**
2. 시작할 때 `git fetch origin && git checkout agent/site-visuals && git pull origin agent/site-visuals`.
3. 커밋 전 검증: `cd frontend-next && npm run typecheck && npm test`. 이 영역은 백엔드 변경 없음.
4. 매 실행 끝에 "진행 상황 로그" 갱신 후 커밋.
5. 확신 없으면 가장 안전한 쪽으로 진행, "질문/확인 필요"에 기록. 멈추지 않는다.

## 다음에 할 일 (우선순위, 하나씩)
1. **원인 진단**: 새 분석 탭과 입지 분석 탭 양쪽에서 실제로 지도에 범례가 보이는지, 지표
   설명이 붙는지 코드 추적으로 확인(브라우저 없이 코드 읽기로 — 이 클라우드 세션엔 화면
   확인 도구가 없을 수 있으니 코드상 연결 여부로 판단). 결론을 이 문서에 기록.
2. **수정**: 범례에 `METRIC_INFO`의 쉬운 설명을 툴팁이나 캡션으로 붙인다. 예: 범례 위에
   "이 색은 [자주 지나가게 되는 길] 정도를 나타내요" 같은 한 줄. 기존 톤(`METRIC_INFO[value].easy`)
   재사용, 새 문구 임의로 만들지 않기.
3. 새 분석 탭 결과 지도(있다면)에도 같은 범례/설명이 붙는지 확인하고 없으면 추가.

## 질문/확인 필요

## 진행 상황 로그
- 2026-07-27: 문서 최초 생성. MapLegend 코드는 존재 확인, 원인은 미확정 상태로 다음 실행에 인계.
