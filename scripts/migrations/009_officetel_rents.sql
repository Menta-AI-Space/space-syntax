-- 오피스텔 전월세 실거래 스냅샷 (국토부 RTMSDataSvcOffiRent).
-- 수익성 분석의 임대료 밴드용: 반경 검색 + 월세/전세 분리.
-- 매매(real_estate_trades)와 스키마가 달라(보증금+월세) 별도 테이블로 둔다.
-- Ingest: cd backend && .venv/Scripts/python -m tools.ingest_officetel_rents
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/009_officetel_rents.sql

CREATE TABLE IF NOT EXISTS officetel_rents (
  id BIGSERIAL PRIMARY KEY,
  lawd_cd TEXT NOT NULL,
  sgg_nm TEXT NOT NULL DEFAULT '',
  umd_nm TEXT NOT NULL DEFAULT '',
  jibun TEXT NOT NULL DEFAULT '',
  bldg_nm TEXT NOT NULL DEFAULT '',            -- 오피스텔명
  deal_date DATE NOT NULL,
  deposit_manwon BIGINT NOT NULL,              -- 보증금 (만원)
  monthly_rent_manwon BIGINT NOT NULL,         -- 월세 (만원); 0 = 전세
  is_jeonse BOOLEAN NOT NULL DEFAULT FALSE,
  area_sqm DOUBLE PRECISION NOT NULL,          -- 전용면적
  floor TEXT NOT NULL DEFAULT '',
  build_year TEXT NOT NULL DEFAULT '',
  contract_type TEXT NOT NULL DEFAULT '',      -- 신규 | 갱신
  contract_term TEXT NOT NULL DEFAULT '',
  use_rr_right TEXT NOT NULL DEFAULT '',        -- 갱신요구권 사용
  pre_deposit_manwon BIGINT NOT NULL DEFAULT 0,
  pre_monthly_rent_manwon BIGINT NOT NULL DEFAULT 0,
  monthly_rent_per_sqm DOUBLE PRECISION,       -- 월세 KRW/m² (월세 계약만)
  geocode_query TEXT NOT NULL DEFAULT '',
  geo_precision TEXT NOT NULL DEFAULT 'none',  -- exact | approx | parcel | none
  lon DOUBLE PRECISION,
  lat DOUBLE PRECISION,
  geom_5179 GEOMETRY(Point, 5179),
  dup_seq INT NOT NULL DEFAULT 0,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT officetel_rents_dedup_key UNIQUE (
    lawd_cd, umd_nm, jibun, bldg_nm, deal_date,
    deposit_manwon, monthly_rent_manwon, floor, area_sqm, dup_seq
  )
);

CREATE INDEX IF NOT EXISTS officetel_rents_geom_idx
  ON officetel_rents USING GIST (geom_5179);

CREATE INDEX IF NOT EXISTS officetel_rents_date_idx
  ON officetel_rents (deal_date);

CREATE INDEX IF NOT EXISTS officetel_rents_jeonse_idx
  ON officetel_rents (is_jeonse);
