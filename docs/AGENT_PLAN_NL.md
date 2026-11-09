# 자율 에이전트 계획서 — NL 에이전트 담당 (agent/nl-agent)

**이 에이전트는 "새 분석" 탭의 자연어 챗(agent.py + AgentChat.tsx)만 다룬다.**
지도/대시보드/업종데이터는 다른 에이전트(agent/site-visuals, agent/site-dashboard,
agent/site-industry-data)가 각자 브랜치에서 따로 작업 중이니 그쪽 파일은 건드리지 않는다.

## 소유 파일 (이 안에서만 작업)
- `backend/app/routers/agent.py`
- `backend/tests/test_agent.py`
- `frontend-next/components/agent/AgentChat.tsx`
- `frontend-next/styles/agent.css` (agent-chat/agent-answer/agent-listings 관련 클래스만 —
  다른 에이전트도 이 파일을 쓸 수 있으니 관련 없는 블록은 건드리지 않는다)

## 안전 규칙
1. **`agent/nl-agent` 브랜치에서만 커밋·push. `main` 직접 push 금지.**
   push되면 자동 배포되는 저장소다(`.github/workflows/deploy.yml`).
2. 시작할 때 `git fetch origin && git checkout agent/nl-agent && git pull origin agent/nl-agent`.
3. 커밋 전 검증 필수:
   - `cd backend && .venv\Scripts\python -m pytest tests/test_agent.py tests/test_geocoding.py -q`
   - `cd frontend-next && npm run typecheck && npm test`
4. 매 실행 끝에 이 문서의 "진행 상황 로그"를 갱신하고 커밋에 포함.
5. 확신 없는 판단은 가장 안전한 쪽으로 진행하고 "질문/확인 필요"에 남긴다. 멈추지 않는다.

## 제품 방향 (요약)
소비자 중심: 결론 먼저, 필요한 것만 요약, 더 물으면 확장. 판매자식 데이터 나열 금지.

**LLM 답변 원칙**: 플랫폼이 가진 정보를 질문에 맞춰 답에 자연스럽게 녹인다(나열 금지).
플랫폼에 없는 정보는 일반 LLM 지식으로 보완.

## 예시(examples) 설계 — 두 가지 의도, 챗이 구분해서 답해야 함
1. **골목 비교** (미착수): "신촌에서 카페 하려면 어떤 골목이 좋은지 비교해줘" — 지역+업종은
   정했고 어디가 좋은지 묻는다. 특정 주소를 여러 개 찍어 비교하지 않는다(공실 여부 알 수 없어
   무의미). **골목(도로 segment) 단위로 비교** — space syntax의 integration/choice가 원래
   segment 단위라 이 방향과 정합. "이 골목은 유동인구 많다, 20대가 많이 다닌다" 식으로 답한다.
   구현 아이디어: 반경 내 도로 segment들을 점수로 순위 매겨 상위 몇 개 골목을 뽑고, 각각의
   space syntax 지표를 사람 말로 번역해 비교 카드로 보여준다. `backend/app/routers/site_score.py`의
   segment 계산 로직(`app/services/graph_builder.py`, `app/services/scoring.py`)을 참고하되
   그 파일들은 이 에이전트 소유가 아니므로 **읽기만** 하고 수정은 site-* 에이전트에게 맡긴다.
   agent.py 안에 새 엔드포인트(예: `/api/agent/compare-alleys`)를 만드는 방향을 우선 검토.
2. **단일 주소 평가** (이미 구현됨): "신촌 ○○로 12번지 카페 자리 어때?" — 지금 챗의 기본 흐름.

## 오늘까지 완료된 것 (2026-07-27)
- [x] 지오코딩 "근처/주변" 등 군더더기 제거
- [x] 미용실 등 7개 업종 확장 + 미지원 업종 친절한 422
- [x] 답변을 섹션(제목+본문)으로 렌더링, 별표 노출 해결
- [x] "이 동네 상가·매물 보기" 네이버 링크 추가
- [x] 아파트 시세 서울 대비 상대% 반영 (housing_market.py는 site-industry-data 소유지만
      LLM 사실 문장 부분만 agent 쪽에서 참고)
- [x] 예시 문구를 "점수 매겨줘"→"어때?" 식으로, 질문형으로 정리

## 다음에 할 일 (우선순위, 하나씩)
1. **골목 비교(예시 1) 구현** — 위 설계대로. 가장 중요하고 아직 미착수.
2. 답변 톤 재검토: "정보를 나열하지 말고 질문에 답하는 형태로"가 실제로 잘 지켜지는지
   여러 케이스(카페/미용실/노래방)로 확인하고 어색한 부분 다듬기.
3. 예시 문구 A/B 후보 더 다듬기 — 사용자가 "이렇게 물으면 되는구나"를 예시만 보고 알 수 있게.

## 질문/확인 필요

## 진행 상황 로그
- 2026-07-27: 문서 최초 생성 (agent/auto-work에서 분기).
