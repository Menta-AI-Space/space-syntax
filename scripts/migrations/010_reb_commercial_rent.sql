-- 한국부동산원(R-ONE) 상업용부동산 임대동향조사: 상권 구획 + 상권별 임대료·공실률.
-- 상가 임대료는 정부 실거래 미공개 → 이 지역통계를 '참고치'로만 사용(물건 단위 아님).
-- 수익성 분석의 상가 월세 입력칸 기본 밴드 + 공실률 기본값 근거.
-- Ingest: cd backend && .venv/Scripts/python -m tools.ingest_commercial_districts "<상권구획도(업로드용).zip>"
--         cd backend && .venv/Scripts/python -m tools.ingest_reb_rent_stats "<통계표.xlsx>" ...
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/010_reb_commercial_rent.sql

CREATE TABLE IF NOT EXISTS commercial_districts (
  id BIGSERIAL PRIMARY KEY,
  sanggwon_nm TEXT NOT NULL,          -- 상권명 (예: 명동)
  sido_cd TEXT NOT NULL DEFAULT '',   -- 2자리 시도코드 (11=서울)
  region_cd TEXT NOT NULL DEFAULT '', -- R-ONE 상권코드 (있을 때)
  note TEXT NOT NULL DEFAULT '',
  area_sqm DOUBLE PRECISION,
  has_sohgyumo BOOLEAN NOT NULL DEFAULT FALSE,   -- 소규모상가 조사 대상
  has_jungdaehyeong BOOLEAN NOT NULL DEFAULT FALSE, -- 중대형상가
  has_office BOOLEAN NOT NULL DEFAULT FALSE,      -- 오피스
  has_jibhap BOOLEAN NOT NULL DEFAULT FALSE,      -- 집합상가
  geom_5179 GEOMETRY(MultiPolygon, 5179) NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT commercial_districts_key UNIQUE (sido_cd, sanggwon_nm)
);

CREATE INDEX IF NOT EXISTS commercial_districts_geom_idx
  ON commercial_districts USING GIST (geom_5179);

CREATE TABLE IF NOT EXISTS reb_rent_stats (
  id BIGSERIAL PRIMARY KEY,
  sido_nm TEXT NOT NULL,              -- 시도명 (예: 서울)
  sanggwon_nm TEXT NOT NULL,          -- 상권명
  region_cd TEXT NOT NULL DEFAULT '', -- 상권코드 (엑셀 '코드' 컬럼)
  property_type TEXT NOT NULL,        -- 중대형 | 소규모 | 집합
  quarter TEXT NOT NULL,              -- 예: 2026.1Q
  rent_krw_per_sqm DOUBLE PRECISION,  -- 월 임대료 원/㎡ (엑셀 천원/㎡ × 1000)
  vacancy_pct DOUBLE PRECISION,       -- 공실률 %
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT reb_rent_stats_key UNIQUE (sido_nm, sanggwon_nm, property_type, quarter)
);

CREATE INDEX IF NOT EXISTS reb_rent_stats_lookup_idx
  ON reb_rent_stats (sanggwon_nm, property_type, quarter);
