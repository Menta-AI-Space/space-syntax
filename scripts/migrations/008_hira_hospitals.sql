-- HIRA (심평원) nationwide hospital/clinic registry with weekend treatment hours.
-- Source: 보건의료빅데이터개방시스템 "전국 병의원 및 약국 현황" quarterly ZIP
-- (files 1.병원정보서비스 + 4.의료기관별상세정보서비스_02_세부정보, joined on 암호화요양기호).
-- Loader: backend/tools/ingest_hira_hospitals.py (quarterly re-run, truncate+reload).

CREATE TABLE IF NOT EXISTS hira_hospitals (
  ykiho TEXT PRIMARY KEY,          -- 암호화요양기호
  name TEXT,
  kind TEXT,                       -- 종별코드명 (의원/병원/치과의원/한의원/...)
  sido TEXT,
  sgg TEXT,
  addr TEXT,
  lon DOUBLE PRECISION NOT NULL,
  lat DOUBLE PRECISION NOT NULL,
  sat_start TEXT,                  -- 진료시작시간_토요일 (HHMM)
  sat_end TEXT,
  sun_start TEXT,
  sun_end TEXT,
  no_trmt_sun TEXT,                -- 휴진안내_일요일 free text
  open_sat BOOLEAN NOT NULL DEFAULT FALSE,
  open_sun BOOLEAN NOT NULL DEFAULT FALSE,
  geom GEOMETRY(Point, 4326),
  geom_5179 GEOMETRY(Point, 5179),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hira_hospitals_kind_idx ON hira_hospitals (kind);
CREATE INDEX IF NOT EXISTS hira_hospitals_geom_idx ON hira_hospitals USING GIST (geom);
CREATE INDEX IF NOT EXISTS hira_hospitals_geom_5179_idx ON hira_hospitals USING GIST (geom_5179);
