-- 필지 폴리곤 — 지도에서 땅 한 조각을 **클릭할 면**을 만든다.
--
-- 왜 새 테이블인가: `parcel_centroids`는 같은 연속지적도를 읽고도 POINT만 남긴다
-- (`ingest_cadastral.py`가 representative_point()로 폴리곤을 버린다). 점으로는
-- 필지 경계를 그릴 수도, 클릭 판정을 할 수도 없다. 대표점은 이미 여러 곳에서
-- 조인 키로 쓰이므로 건드리지 않고 폴리곤을 따로 둔다.
--
-- 전 서울 89만 필지를 한 번에 넣지 않는다. 동 단위로 넣고 눈으로 확인한 뒤 넓힌다
-- (`--ldong`). 89만 폴리곤을 GeoJSON으로 그냥 내리면 5초 룰을 못 지키므로, 넓힐
-- 때는 벡터 타일(ST_AsMVT)이나 줌별 단순화가 선행돼야 한다.
--
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/016_parcel_polygons.sql

CREATE TABLE IF NOT EXISTS parcel_polygons (
  pnu         TEXT PRIMARY KEY,
  geom_5179   geometry(MultiPolygon, 5179) NOT NULL,
  area_sqm    DOUBLE PRECISION,   -- 기하에서 계산한 면적. 토지특성의 신고 면적과 다를 수 있다.
  src         TEXT NOT NULL DEFAULT 'cadastral',
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 지도 뷰포트로 잘라 오는 질의가 기본이라 공간 인덱스가 필수다.
CREATE INDEX IF NOT EXISTS parcel_polygons_geom_idx
  ON parcel_polygons USING GIST (geom_5179);

-- "이 동의 필지 전부" 질의를 위해 PNU 앞 10자리(법정동)로도 찾는다.
CREATE INDEX IF NOT EXISTS parcel_polygons_ldong_idx
  ON parcel_polygons (left(pnu, 10));
