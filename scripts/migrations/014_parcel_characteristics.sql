-- 토지특성정보(V-World NED getLandCharacteristics) 적재 테이블.
--
-- 필지 저평가 판정의 **통제변수**가 여기서 온다. 연구 문서 3.5가 요구한
-- "접면등급·용도지역·지목·면적"이 이 API 한 곳에 다 있어서, 용도지역 폴리곤을
-- 따로 받아 공간조인할 필요가 없어졌다(013의 land_use_zones는 미사용).
--
-- 키는 (pnu, base_year) — land_prices와 같아서 그대로 조인된다.
-- 연도 범위는 2015~2026 (실측). land_prices(2009~)보다 짧지만 연도별 OSM
-- 스냅샷이 2015부터라 패널 창은 어차피 2015~2026이다.
--
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/014_parcel_characteristics.sql

CREATE TABLE IF NOT EXISTS parcel_characteristics (
  pnu             text NOT NULL,
  base_year       int  NOT NULL,
  -- 가장 중요한 단일 통제변수. 원문 명칭 그대로 두고, 정규화는 조회 시점에
  -- app/services/land_use_zones.py가 한다 (출처 표기가 바뀌어도 적재는 안 건드린다).
  zone_name_1     text,          -- prposArea1Nm 예: 일반상업지역
  zone_name_2     text,          -- prposArea2Nm 2차 용도지역 (대개 "지정되지않음")
  -- 도로접면 12구분 (광대소각/광대한면/중로각지 …). ⚠️ 결측이 많다 —
  -- 실측 표본에서 63%가 "지정되지않음"이었다. 순환성 방어에 이것만 믿지 말 것.
  road_side_name  text,
  land_category   text,          -- lndcgrCodeNm 지목 (대/도로/공원/하천 …)
  area_sqm        double precision,  -- lndpclAr 면적
  use_situation   text,          -- ladUseSittnNm 이용상황
  topograph_height text,         -- tpgrphHgCodeNm 지세(고저)
  topograph_form  text,          -- tpgrphFrmCodeNm 형상
  -- 공시지가도 함께 온다. land_prices와 대조해 두 API의 일치를 확인할 수 있다.
  price_per_sqm   bigint,
  ldong_code      text,
  ldong_name      text,
  jibun           text,
  special_name    text,          -- regstrSeCodeNm 특수지구분
  src             text NOT NULL,
  ingested_at     timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (pnu, base_year)
);

-- 계층(용도지역) 단위 집계·필터에 쓰인다.
CREATE INDEX IF NOT EXISTS parcel_characteristics_zone_idx
  ON parcel_characteristics (base_year, zone_name_1);
-- 도로·하천 필지 제외에 쓰인다.
CREATE INDEX IF NOT EXISTS parcel_characteristics_category_idx
  ON parcel_characteristics (land_category);
