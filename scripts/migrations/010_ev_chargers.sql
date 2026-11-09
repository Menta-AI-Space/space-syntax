-- 환경부(한국환경공단) 전기차 충전소 registry.
-- Source: data.go.kr B552584 EvCharger getChargerInfo (인증키 필요; 2022 CSV는
-- 좌표가 없고 4년 낡아 미사용). Loader: backend/tools/ingest_ev_chargers.py.

CREATE TABLE IF NOT EXISTS ev_chargers (
  stat_id TEXT NOT NULL,          -- 충전소 ID (statId)
  chger_id TEXT NOT NULL,         -- 충전기 ID (chgerId)
  name TEXT,
  addr TEXT,
  lon DOUBLE PRECISION NOT NULL,
  lat DOUBLE PRECISION NOT NULL,
  charger_type TEXT,              -- chgerType 코드 (급속/완속 구분 가능)
  output_kw TEXT,                 -- output (충전용량 kW)
  busi_name TEXT,                 -- 운영기관
  zcode TEXT,                     -- 시도 코드
  geom GEOMETRY(Point, 4326),
  geom_5179 GEOMETRY(Point, 5179),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (stat_id, chger_id)
);

CREATE INDEX IF NOT EXISTS ev_chargers_geom_5179_idx ON ev_chargers USING GIST (geom_5179);
CREATE INDEX IF NOT EXISTS ev_chargers_zcode_idx ON ev_chargers (zcode);
