# 자율 에이전트 계획서 — 업종·데이터 담당 (agent/site-industry-data)

**이 에이전트는 업종별 분석 완성도와 매출·시세 데이터만 다룬다.** NL 챗, 지도 색상,
대시보드 도보시간 버그는 다른 에이전트가 각자 브랜치에서 작업 중이니 건드리지 않는다.

## 소유 파일
- `backend/app/services/profile_resolver.py`
- `backend/industry-profiles/**/*.json`
- `backend/app/services/industry_kpis.py`
- `backend/app/services/demand_benchmark.py`
- `backend/app/services/housing_market.py`
- `backend/app/services/trdar_sales_local.py`
- `backend/tools/*` (데이터 적재 스크립트류)
- `backend/tests/test_housing_relative.py`, `test_housing_market.py`, `test_demand_benchmark.py` 등

## 배경 — 오늘까지 밝혀진 것
- 업종 프로파일은 `industry-profiles/`에 16개 있지만, 실제로 라이브에서 200 응답이 되는 건
  일부뿐이었다(general 업종은 다운스트림에서 크래시). 팀원이 `subscore.score`가 None일 때
  정렬/서술에서 터지던 버그를 고쳐 이제 대부분 업종이 동작한다 — **하지만 전체 16개를
  하나씩 실제로 확인한 기록은 없다.** 라이브에서 각 업종을 순회 테스트하고 안 되는 게 있으면
  원인을 좁혀 기록할 것(이 클라우드 세션엔 라이브 호출 도구가 없을 수 있음 — 코드 추적으로
  또는 로컬 pytest로 대체 검증).
- 아파트 시세에 서울 대비 상대% (`vs_seoul_pct`, `housing_market.py`)를 오늘 추가했다.
  **캐시 미스 시 블로킹하지 않고 백그라운드로 채우는 패턴**(`_trades_for_lawd`와 동일)을
  꼭 지킬 것 — 안 그러면 site-score의 8초 응답 예산을 깨서 테스트가 깨진다(오늘 실제로 한 번
  깨졌다가 고침, `test_housing_market.py::TestTradesCache` 참고).

## 안전 규칙
1. **`agent/site-industry-data` 브랜치에서만 커밋·push. `main` 직접 push 금지.**
2. 시작할 때 `git fetch origin && git checkout agent/site-industry-data && git pull origin agent/site-industry-data`.
3. 커밋 전 검증: `cd backend && .venv\Scripts\python -m pytest tests/ -q --ignore=tests/test_ingest_hira_specialty.py`
   (openpyxl 미설치로 그 파일만 무관하게 실패 — 그 외 전부 통과 필수).
4. 매 실행 끝에 "진행 상황 로그" 갱신 후 커밋.
5. 확신 없으면 가장 안전한 쪽으로 진행, "질문/확인 필요"에 기록. 멈추지 않는다.
6. **DB 스키마를 바꾸거나 대량 데이터 적재(예: 서울 표준단위구역 매출 전체 적재)는 먼저
   조사·설계만 문서에 남기고, 실행 전 사람 확인을 받는다** — 되돌리기 어렵거나 라이브 DB
   용량/성능에 영향을 줄 수 있는 작업이라서다.

## 다음에 할 일 (우선순위, 하나씩)

1. **업종 16개 전수 점검**: `industry-profiles/`의 모든 profile id에 대해
   `ProfileResolver.resolve(business_type=...)`가 무언가를 반환하는지, 그리고
   `compute_industry_kpis` 등 다운스트림이 None-safe한지 로컬 테스트로 점검(라이브 호출
   없이). 안 되는 게 있으면 원인을 좁혀 기록.

2. **골목 단위 매출 데이터 조사 심화**: 서울시 상권분석서비스 "추정매출-상권"이 2024년부터
   "표준단위구역" 단위로 바뀌었다(카드사 데이터, data.seoul.go.kr OA-15572,
   golmok.seoul.go.kr/source.do). 이게 도로 segment/골목과 얼마나 가까운 공간 단위인지,
   좌표로 매핑 가능한지, `trdar_sales_local.py`가 이미 쓰는 상권(trdar) 단위와 어떤
   관계인지 **조사만** 하고 결론을 이 문서에 기록. 스키마 변경·대량 적재는 사람 확인 후.

3. **대안 설계**: 골목 단위 원 데이터가 끝내 없다면, 동 단위 매출을 space syntax
   (integration/choice)로 골목마다 배분·추정하는 방법을 설계만 문서화. 이게 이 앱만의
   차별점이 될 수 있다는 게 오너 판단.

4. **아파트 시세 상대% 검증**: `vs_seoul_pct` 계산이 다른 자치구/동에서도 상식적인 값을
   내는지 로컬 테스트로 몇 개 케이스 더 검증.

## 질문/확인 필요

## 진행 상황 로그
- 2026-07-27: 문서 최초 생성 (agent/auto-work에서 분기, 오늘 커밋 402ad41 포함 상태).
