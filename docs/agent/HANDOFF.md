<!-- 이 파일은 task-state.json에서 자동 생성된다. 직접 고치지 말 것. -->

# 인수인계 — 사람이 읽는 요약

마지막 갱신 2026-08-01T20:39:17+09:00

- 완료 144건 · 대기 0건 · 막힘 1건 · 실패 3건

## 끝낸 것 (최근 10건)

- T-183 PurchaseVerdictResponse.market_band(MarketBandOut)에 insufficient_sample/relaxations/data_source가 빠져 표본 1~2건짜리 실거래 비교가 신뢰할 만한 값처럼 보인다
- T-184 AreaSearchResponse에 region_label 필드가 없어 동네 탐색 결과 화면의 지역 칩이 항상 빈값
- T-185 diagnose의 상권매출 추세 원인이 trdar_for_point의 contains/distance_m(최근접 폴백 여부)를 안 실어 폭넓게 확정형으로 서술됨
- T-186 runner.py의 commit_and_push가 'PR 오픈=done'으로 기록하고 add_scouted dedup은 제목 완전일치만 봐 같은 미구현이 15회+ 재발견·재작업된다
- T-187 업종별 KPI·체크리스트(industry_profiles.get_adaptive_profile)를 agent.py _TOOLS에 연결
- T-188 franchise_brand이 문장에서 안 뽑힐 때 GET /api/franchise/brands 검색으로 폴백
- T-189 restaurant 프리셋이 inds_mcls_cd=I201(한식)로 고정돼 있어 비-한식 음식점도 한식으로 표시·집계됨
- T-190 경쟁/포화 지표 9개 업종에서 점수 반전 없이 표시(초록 막대로 오인)
- T-191 seoul_sales.py resolve_seoul_service_code가 매핑에 없는 업종(미용실·헬스장·오락·숙박·주유소·전기차충전·전문서비스, 심지어 restaurant까지)을 조용히 카페/한식 매출로 대체한다
- T-192 seoul_sales.py fetch_sales_benchmark의 Open API 경로가 점포 수 0/None을 대체하면서 CSV 스냅샷 경로와 달리 경고를 안 남긴다

## 결정이 필요한 것

- **5초 룰 — _extract_spots를 analysis_cache에 태울지** — 사람 결정 대기. 캐시 키가 어긋나면 추천이 조용히 바뀐다. 근거와 검증 절차는 docs/PERF_AREA_SEARCH.md
