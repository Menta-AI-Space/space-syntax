-- E-Gen (국립중앙의료원 전국 병·의원 찾기) registry with full weekday/weekend hours.
-- Source: data.go.kr B552657 HsptlAsembySearchService (getHsptlMdcncListInfoInqire).
-- Complements hira_hospitals: HIRA has authoritative registry but ~7% hours coverage
-- (Seoul 0%); E-Gen has near-full duty-hours coverage + coords.
-- Loader: backend/tools/ingest_egen_hospitals.py (quarterly re-run, per-sido upsert).

CREATE TABLE IF NOT EXISTS egen_hospitals (
  hpid TEXT PRIMARY KEY,
  name TEXT,
  div_name TEXT,                  -- dutyDivNam (의원/병원/종합병원/치과의원/한의원...)
  addr TEXT,
  lon DOUBLE PRECISION NOT NULL,
  lat DOUBLE PRECISION NOT NULL,
  sat_start TEXT,                 -- dutyTime6s (HHMM)
  sat_end TEXT,
  sun_start TEXT,                 -- dutyTime7s
  sun_end TEXT,
  open_sat BOOLEAN NOT NULL DEFAULT FALSE,
  open_sun BOOLEAN NOT NULL DEFAULT FALSE,
  geom GEOMETRY(Point, 4326),
  geom_5179 GEOMETRY(Point, 5179),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS egen_hospitals_div_idx ON egen_hospitals (div_name);
CREATE INDEX IF NOT EXISTS egen_hospitals_geom_5179_idx ON egen_hospitals USING GIST (geom_5179);
