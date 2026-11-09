-- 필지별 교통 접근성 feature (PPT Feature Pool 6·7·8) → parcel_accessibility
--
-- 6 지하철 접근성 / 7 버스·대중교통 / 8 간선도로·네트워크 접근성 을 한 표에 담는다.
-- 원천은 전부 이미 적재된 로컬 테이블이다 (공공데이터 통합 원칙 — 런타임 API 호출
-- 없음): 지하철역·출입구·버스정류장은 ``planet_osm_point``, 도로는 ``roads_clean``.
--
-- ⚠️⚠️ **이 표는 시불변(time-invariant)이다. 연도 컬럼이 없는 것이 의도다.**
--
-- OSM 적재본은 스냅샷 **하나**뿐이다(``osm2pgsql_properties.import_timestamp``
-- = 2026-06-30). 즉 "2015년의 역 위치"라는 정보를 우리는 갖고 있지 않다. 그래서
-- 키를 ``(pnu)``로만 두고, 그 스냅샷 기준일을 ``as_of``에 남긴다.
--
-- **연도별로 복사해서 (pnu, base_year)로 만들지 말 것.** 같은 값을 12개 연도에
-- 뿌리면 표만 12배가 되고, 패널 회귀에서는 "2015년 필지가 2026년의 역을 이미
-- 알고 있다"가 되어 leakage처럼 보인다(실제로는 정보가 없는 것인데 있는 것처럼
-- 보이게 만든다). 방법론 A/B(필지 고정효과 1차 차분)에서 이 feature 묶음은
-- **차분하면 전부 0이 되어 자동 탈락한다** — 그게 정직한 결과다. 시간 변화가
-- 필요해지면 연도별 OSM 스냅샷(Geofabrik 2015~)을 따로 적재한 뒤 별도 표를
-- 만든다. 이 표를 늘려 쓰지 않는다.
--
-- ⚠️ 좌표계: ``planet_osm_*``의 지오메트리 컬럼은 ``way``이고 **EPSG:4326**이다
-- (3857이 아니다 — osm2pgsql 기본값은 3857이지만 이 DB는 ``-l``로 적재됐다,
-- 2026-08-24 실측). 반면 ``parcel_centroids.geom_5179``·``roads_clean.geom_5179``는
-- 5179(m 단위)다. 거리를 m로 재려면 **반드시 ST_Transform(way, 5179)**를 거쳐야
-- 한다. 이걸 틀리면 오류 없이 엉뚱한 숫자가 나온다 (이 프로젝트에서 5174/5186
-- 혼동으로 공간조인이 0건 된 사고가 있었다).
--
-- ⚠️ 결측 규칙: **거리는 NULL, 개수는 0.** 반경 안에 아무것도 없으면 개수 0이
-- 맞지만, 거리를 0으로 채우면 "정보 없음"이 "바로 옆"으로 학습된다. 실제로는
-- 서울 안이라 최근접 역·정류장은 항상 존재하므로 거리 NULL은 거의 없어야 하고,
-- NULL이 많이 나오면 계산이 틀렸다는 신호로 읽는다.
--
-- Run:
--   psql -h localhost -p 5433 -U spacesyntax -d spacesyntax \
--        -f scripts/migrations/018_parcel_accessibility.sql

SET client_encoding = 'UTF8';

CREATE TABLE IF NOT EXISTS parcel_accessibility (
  pnu                    text PRIMARY KEY,
  -- PNU 앞 5자리. 구 단위로 재계산·조회하므로 파생해서 들고 있는다.
  sgg_code               text NOT NULL,

  -- === feature 6: 지하철 접근성 ===
  -- planet_osm_point railway='station' 기준. ⚠️ 이 태그는 지하철만이 아니라
  -- 일반철도(Korail) 역도 포함한다. 서울 안에서는 대부분 환승 가능한 전철역이라
  -- 구분하지 않았다. 구분이 필요해지면 operator/name으로 갈라 별 컬럼을 둔다.
  nearest_station_m      double precision,   -- 직선거리(m). 없으면 NULL (0 아님)
  nearest_station_name   text,               -- 그 역 이름 (검산용으로 꼭 남긴다)
  -- 출입구(railway='subway_entrance')까지의 거리. **역 중심보다 이쪽이 정확하다** —
  -- 실제 도보 접근점은 출입구이고, 큰 환승역은 중심과 출입구가 300m 넘게 벌어진다.
  -- ⚠️ 출입구 2,217개 중 name이 있는 건 96개뿐이라 이름 컬럼은 두지 않았다.
  nearest_entrance_m     double precision,
  stations_within_500m   integer NOT NULL DEFAULT 0,
  stations_within_1000m  integer NOT NULL DEFAULT 0,

  -- === feature 7: 버스·대중교통 ===
  nearest_bus_stop_m     double precision,
  bus_stops_within_300m  integer NOT NULL DEFAULT 0,
  bus_stops_within_500m  integer NOT NULL DEFAULT 0,

  -- === feature 8: 간선도로·네트워크 접근성 ===
  -- 간선 = highway in ('motorway','trunk','primary','secondary').
  -- 근거: (1) OSM 한국 태깅 관행에서 motorway=고속도로, trunk=자동차전용도로,
  -- primary=주간선(대로급), secondary=보조간선에 대응하고 tertiary부터는 집산도로다.
  -- (2) 이 프로젝트가 이미 같은 경계를 쓰고 있다 — app/services/area_search.py의
  -- 간선 필터와 config.py의 도로 등급 목록이 이 4개를 상위군으로 둔다.
  -- (3) ``_link``(램프)는 제외했다. 램프는 간선의 접속부라 폭·통행량이 본선과
  -- 다르고, "간선에 가깝다"는 신호로는 본선 선형이 맞다.
  -- ⚠️ 양면성: 간선에 가까우면 접근성은 좋지만 소음·보행단절로 주거 지가는
  -- 떨어질 수 있다. 부호를 미리 가정하지 말 것.
  nearest_arterial_m     double precision,
  -- drivable 도로 전체(service·residential 포함) 최근접 거리. **맹지 판정 보조**다.
  -- 이 값이 크면 접도가 없다는 뜻이지만, 단독으로 맹지라 단정하지 말 것 —
  -- 필지 대표점(centroid) 기준이라 큰 필지는 접도해도 값이 커진다.
  nearest_road_m         double precision,

  -- OSM 스냅샷 기준일. 이 표가 시불변이라는 사실을 기록하는 컬럼이다.
  as_of                  date NOT NULL,
  computed_at            timestamptz NOT NULL DEFAULT NOW()
);

-- 구 단위로 재계산·검증하므로 접두 5자리에 인덱스를 둔다.
CREATE INDEX IF NOT EXISTS parcel_accessibility_sgg_idx
  ON parcel_accessibility (sgg_code);
-- "역세권 필지" 같은 필터가 첫 사용 패턴이다.
CREATE INDEX IF NOT EXISTS parcel_accessibility_station_idx
  ON parcel_accessibility (nearest_station_m);
