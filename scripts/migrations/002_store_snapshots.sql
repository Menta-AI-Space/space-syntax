-- Quarterly SBIZ store snapshots snapped to nearest road segment (50m).
-- Run: psql -h localhost -U spacesyntax -d spacesyntax -f scripts/migrations/002_store_snapshots.sql

CREATE TABLE IF NOT EXISTS store_snapshots (
  id BIGSERIAL PRIMARY KEY,
  quarter TEXT NOT NULL,
  bizes_id TEXT,
  bizes_nm TEXT,
  inds_scls_cd TEXT,
  inds_mcls_nm TEXT,
  lon DOUBLE PRECISION NOT NULL,
  lat DOUBLE PRECISION NOT NULL,
  segment_osm_id BIGINT,
  category_match BOOLEAN DEFAULT FALSE,
  geom GEOMETRY(Point, 4326),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS store_snapshots_quarter_idx ON store_snapshots (quarter);
CREATE INDEX IF NOT EXISTS store_snapshots_segment_quarter_idx
  ON store_snapshots (segment_osm_id, quarter);
CREATE INDEX IF NOT EXISTS store_snapshots_geom_idx
  ON store_snapshots USING GIST (geom);
