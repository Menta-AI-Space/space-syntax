-- SGIS 행정동 경계 + 총조사 통계 로컬 스토어 (연 1회 재적재).
-- fetch_demand_profile이 원격 SGIS API 대신 이 테이블들을 우선 조회한다.
-- Ingest: cd backend && .venv/Scripts/python -m tools.ingest_sgis_dong
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/004_sgis_dong.sql

CREATE TABLE IF NOT EXISTS sgis_dong_boundary (
  adm_cd TEXT PRIMARY KEY,
  adm_nm TEXT NOT NULL,
  boundary_year TEXT NOT NULL,
  geom geometry(MultiPolygon, 5179) NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS sgis_dong_boundary_geom_idx
  ON sgis_dong_boundary USING GIST (geom);

-- 통계는 경계와 독립적으로 적재/갱신될 수 있어 FK를 걸지 않는다.
CREATE TABLE IF NOT EXISTS sgis_dong_stats (
  adm_cd TEXT PRIMARY KEY,
  adm_nm TEXT NOT NULL,
  stats_year TEXT,
  population INTEGER,
  households INTEGER,
  companies INTEGER,
  workers INTEGER,
  avg_age DOUBLE PRECISION,
  population_density DOUBLE PRECISION,
  age_bands JSONB,   -- [{"band","count","pct"}] (pplsummary)
  gender JSONB,      -- {"male_pct","female_pct","male_count","female_count"}
  housing JSONB,     -- {"apartment_pct","officetel_pct","row_house_pct","detached_pct","apartment_count","officetel_count"}
  industries JSONB,  -- [{"code","name","companies","workers"}] KSIC 대분류 상위 8
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
