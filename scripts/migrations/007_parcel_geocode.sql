-- 로컬 geocoding 인프라: 법정동코드 + 연속지적도 필지 대표점.
-- 실거래(시군구코드+법정동명+지번) → PNU → 좌표를 외부 API 없이 조인으로 해결.
-- Ingest: cd backend && .venv/Scripts/python -m tools.ingest_legal_dong_codes
--         cd backend && .venv/Scripts/python -m tools.ingest_cadastral <LSMD_CONT_LDREG_*.zip ...>
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/007_parcel_geocode.sql

CREATE TABLE IF NOT EXISTS legal_dong_codes (
  region_cd TEXT PRIMARY KEY,       -- 법정동코드 10자리
  lawd_cd TEXT NOT NULL,            -- 시군구 5자리 (region_cd 앞 5)
  addr_name TEXT NOT NULL,          -- 전체 주소명 (예: 서울특별시 서대문구 창천동)
  lowest_name TEXT NOT NULL,        -- 최하위 명칭 (동/읍/면/리)
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS legal_dong_codes_lookup_idx
  ON legal_dong_codes (lawd_cd, lowest_name);

CREATE TABLE IF NOT EXISTS parcel_centroids (
  pnu TEXT PRIMARY KEY,             -- 필지고유번호 19자리
  geom_5179 GEOMETRY(Point, 5179) NOT NULL,
  src TEXT NOT NULL DEFAULT 'cadastral',
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
