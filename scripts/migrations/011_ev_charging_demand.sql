-- 환경부 충전건별 충전량 합성데이터(2024, 12.2M rows)를 시군구×충전소유형으로
-- 집계한 EV 충전 수요 강도. 원본은 합성데이터(원본 통계 특성 보존)라 건별
-- 신뢰는 낮지만 시군구 집계 수준의 수요 비교에는 유효.
-- Loader: backend/tools/ingest_ev_charging_demand.py (연 1회 재적재).

CREATE TABLE IF NOT EXISTS ev_charging_demand (
  sido TEXT NOT NULL,
  sgg TEXT NOT NULL,
  station_type TEXT NOT NULL,       -- 충전소 유형 대분류 (공공시설/주거시설/...)
  sessions BIGINT NOT NULL,
  total_kwh DOUBLE PRECISION NOT NULL,
  charger_count INTEGER NOT NULL,   -- distinct 충전소ID+충전기ID
  kwh_per_charger DOUBLE PRECISION, -- 연간 충전량 / 충전기 수
  data_year INTEGER NOT NULL DEFAULT 2024,
  PRIMARY KEY (sido, sgg, station_type)
);

CREATE INDEX IF NOT EXISTS ev_charging_demand_sgg_idx ON ev_charging_demand (sgg);
