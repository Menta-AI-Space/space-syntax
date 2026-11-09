-- 임대 매물 호가 스냅샷 (내부 컨설팅 분석 전용).
-- ⚠ 호가(asking price)이며 실거래가 아님. 플랫폼 API·comparable 서비스에서
--   절대 조회하지 않는다. 컨설팅 산출물에는 "호가 기준" 라벨을 강제한다.
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/008_rent_listings.sql

CREATE TABLE IF NOT EXISTS rent_listings (
  source TEXT NOT NULL,                  -- 예: naver
  article_no TEXT NOT NULL,
  property_kind TEXT NOT NULL,           -- SG(상가) | OPST(오피스텔) 등 소스 코드
  trade_kind TEXT NOT NULL,              -- B2(월세) | B1(전세) | A1(매매)
  deposit_manwon BIGINT,
  rent_manwon BIGINT,
  area_contract_sqm DOUBLE PRECISION,    -- 계약면적
  area_exclusive_sqm DOUBLE PRECISION,   -- 전용면적
  floor_info TEXT NOT NULL DEFAULT '',   -- 예: 2/5
  name TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  cortar_no TEXT NOT NULL DEFAULT '',    -- 법정동코드 10자리
  lat DOUBLE PRECISION,
  lon DOUBLE PRECISION,
  geom_5179 GEOMETRY(Point, 5179),
  data_grade TEXT NOT NULL DEFAULT '호가',
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (source, article_no)
);

CREATE INDEX IF NOT EXISTS rent_listings_geom_idx
  ON rent_listings USING GIST (geom_5179);

CREATE INDEX IF NOT EXISTS rent_listings_kind_idx
  ON rent_listings (property_kind, trade_kind);
