-- "뜰 자리" — 길 구조는 좋은데 아직 가격에 반영 안 된 서울 행정동.
-- 배치로 미리 계산해 저장한다(5초 룰: 요청 시점엔 이 테이블만 읽는다).
-- Ingest: cd backend && .venv/Scripts/python -m tools.compute_undervalued_areas
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/007_undervalued_areas.sql

CREATE TABLE IF NOT EXISTS undervalued_area_scores (
  adm_cd TEXT PRIMARY KEY,
  adm_nm TEXT NOT NULL,
  structure_score DOUBLE PRECISION NOT NULL,      -- 500m 반경 평균 (choice_walk+integration_walk)/2, 0~1
  structure_percentile DOUBLE PRECISION NOT NULL, -- 서울 424개 동 중 구조 순위, 0~100 (높을수록 구조 좋음)
  price_per_sqm_manwon DOUBLE PRECISION,           -- 동 내 실거래 중앙값 (만원/㎡), 표본 없으면 NULL
  price_percentile DOUBLE PRECISION,               -- 서울 동 중 가격 순위, 0~100 (높을수록 비쌈). 표본 없으면 NULL
  trade_sample_count INTEGER NOT NULL DEFAULT 0,
  gap_score DOUBLE PRECISION,                      -- structure_percentile - price_percentile. 클수록 저평가.
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS undervalued_area_scores_gap_idx
  ON undervalued_area_scores (gap_score DESC NULLS LAST);
