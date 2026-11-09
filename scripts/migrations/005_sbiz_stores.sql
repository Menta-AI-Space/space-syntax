-- 소상공인시장진흥공단 상가(상권)정보 분기 덤프 로컬 스토어.
-- fetch_stores_in_radius가 원격 API 대신 이 테이블을 우선 조회한다 (ST_DWithin).
-- Ingest: cd backend && .venv/Scripts/python -m tools.ingest_sbiz_stores <zip경로>
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/005_sbiz_stores.sql

CREATE TABLE IF NOT EXISTS sbiz_stores (
  bizes_id TEXT PRIMARY KEY,
  bizes_nm TEXT NOT NULL DEFAULT '',
  inds_lcls_cd TEXT NOT NULL DEFAULT '',
  inds_lcls_nm TEXT NOT NULL DEFAULT '',
  inds_mcls_cd TEXT NOT NULL DEFAULT '',
  inds_mcls_nm TEXT NOT NULL DEFAULT '',
  inds_scls_cd TEXT NOT NULL DEFAULT '',
  inds_scls_nm TEXT NOT NULL DEFAULT '',
  lon DOUBLE PRECISION NOT NULL,
  lat DOUBLE PRECISION NOT NULL,
  bld_mng_no TEXT NOT NULL DEFAULT '',
  flr_no TEXT NOT NULL DEFAULT '',
  rdnm_adr TEXT NOT NULL DEFAULT '',
  data_quarter TEXT NOT NULL,
  geom_5179 GEOMETRY(Point, 5179) NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS sbiz_stores_geom_idx
  ON sbiz_stores USING GIST (geom_5179);

CREATE INDEX IF NOT EXISTS sbiz_stores_scls_idx
  ON sbiz_stores (inds_scls_cd);
