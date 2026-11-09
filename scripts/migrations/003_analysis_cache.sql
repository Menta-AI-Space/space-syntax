-- Persistent cache for stable Space Syntax baseline analyses.
-- Network and analysis versions make road/config invalidation atomic.
-- Run: psql -h localhost -U spacesyntax -d spacesyntax -f scripts/migrations/003_analysis_cache.sql

CREATE TABLE IF NOT EXISTS analysis_cache_entries (
  network_version TEXT NOT NULL,
  analysis_version TEXT NOT NULL,
  engine TEXT NOT NULL CHECK (engine IN ('depthmapx', 'python')),
  segment_limit INTEGER NOT NULL CHECK (segment_limit > 0),
  center_lng DOUBLE PRECISION NOT NULL,
  center_lat DOUBLE PRECISION NOT NULL,
  radius_m INTEGER NOT NULL CHECK (radius_m IN (500, 1000)),
  mode TEXT NOT NULL CHECK (mode IN ('walk', 'drive')),
  result JSONB NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (
    network_version,
    analysis_version,
    engine,
    segment_limit,
    center_lng,
    center_lat,
    radius_m,
    mode
  )
);

CREATE INDEX IF NOT EXISTS analysis_cache_entries_time_idx
  ON analysis_cache_entries (computed_at DESC);
