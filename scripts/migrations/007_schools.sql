-- Nationwide elementary/middle/high school locations (한국지방교육행정연구재단).
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/003_schools.sql

CREATE TABLE IF NOT EXISTS schools (
  id BIGSERIAL PRIMARY KEY,
  school_id TEXT UNIQUE,
  name TEXT,
  school_level TEXT,
  status TEXT,
  address TEXT,
  lon DOUBLE PRECISION NOT NULL,
  lat DOUBLE PRECISION NOT NULL,
  geom GEOMETRY(Point, 4326),
  geom_5179 GEOMETRY(Point, 5179),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS schools_level_idx ON schools (school_level);
CREATE INDEX IF NOT EXISTS schools_geom_idx ON schools USING GIST (geom);
CREATE INDEX IF NOT EXISTS schools_geom_5179_idx ON schools USING GIST (geom_5179);
