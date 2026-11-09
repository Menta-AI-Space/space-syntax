-- 국토부 RTMS 매매 실거래 스냅샷 (상업업무용/오피스텔/아파트).
-- 수익성 분석의 comparable 조회용: 반경 검색(ST_DWithin) + 거래유형 필터.
-- Ingest: cd backend && .venv/Scripts/python -m tools.ingest_real_estate_trades
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/006_real_estate_trades.sql

CREATE TABLE IF NOT EXISTS real_estate_trades (
  id BIGSERIAL PRIMARY KEY,
  property_type TEXT NOT NULL,                 -- commercial | officetel | apartment
  lawd_cd TEXT NOT NULL,                       -- 시군구 5자리
  sgg_nm TEXT NOT NULL DEFAULT '',
  umd_nm TEXT NOT NULL DEFAULT '',
  jibun TEXT NOT NULL DEFAULT '',              -- 마스킹('*') 가능
  bldg_nm TEXT NOT NULL DEFAULT '',            -- 아파트/오피스텔명 (상업업무용은 '')
  deal_date DATE NOT NULL,
  deal_amount_manwon BIGINT NOT NULL,          -- 만원
  area_sqm DOUBLE PRECISION NOT NULL,          -- 상업: 건물면적 / 집합: 전용면적
  plottage_ar DOUBLE PRECISION,                -- 상업 대지면적 (통건물 거래 판단 보조)
  floor TEXT NOT NULL DEFAULT '',
  build_year TEXT NOT NULL DEFAULT '',
  building_use TEXT NOT NULL DEFAULT '',
  building_type TEXT NOT NULL DEFAULT '',      -- 일반(통건물) | 집합(구분호실) | ''
  land_use TEXT NOT NULL DEFAULT '',           -- 용도지역
  share_dealing_type TEXT NOT NULL DEFAULT '', -- '지분' = 지분거래
  cdeal_type TEXT NOT NULL DEFAULT '',         -- 'O' = 해제된 계약
  cdeal_day TEXT NOT NULL DEFAULT '',
  dealing_gbn TEXT NOT NULL DEFAULT '',        -- 중개거래 | 직거래
  price_per_sqm DOUBLE PRECISION,              -- KRW/m²
  dup_seq INT NOT NULL DEFAULT 0,              -- 동일 (단지·층·면적·가격·일자) 복수 호실 구분
  geocode_query TEXT NOT NULL DEFAULT '',      -- 좌표를 얻은 질의 문자열
  geo_precision TEXT NOT NULL DEFAULT 'none',  -- exact | approx(동 중심) | none
  lon DOUBLE PRECISION,
  lat DOUBLE PRECISION,
  geom_5179 GEOMETRY(Point, 5179),
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT real_estate_trades_dedup_key UNIQUE (
    property_type, lawd_cd, umd_nm, jibun, bldg_nm,
    deal_date, deal_amount_manwon, floor, area_sqm, dup_seq
  )
);

CREATE INDEX IF NOT EXISTS real_estate_trades_geom_idx
  ON real_estate_trades USING GIST (geom_5179);

CREATE INDEX IF NOT EXISTS real_estate_trades_type_date_idx
  ON real_estate_trades (property_type, deal_date);

CREATE INDEX IF NOT EXISTS real_estate_trades_bldg_type_idx
  ON real_estate_trades (building_type);
