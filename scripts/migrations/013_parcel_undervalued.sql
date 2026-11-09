-- 필지 단위 저평가 판정에 필요한 테이블 셋.
-- 계산 로직은 app/services/parcel_undervalued.py, 배치는 tools/compute_*.py.
--
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/013_parcel_undervalued.sql

-- ── 1. 도로 세그먼트 구조값 ────────────────────────────────────────────
-- 서울 전역을 타일로 나눠 한 번 계산해 둔다. 필지가 약 87만 개인데 세그먼트는
-- 훨씬 적으므로, 필지마다 반경 분석을 돌리는 대신 세그먼트를 먼저 계산하고
-- 필지에 붙인다.
--
-- ⚠️ choice_raw는 타일 간 비교가 불가능하다. nx.betweenness_centrality를
-- normalized=False로 호출하므로(space_syntax.py:142) 값이 그래프 크기에 대략
-- 비례한다 — 세그먼트가 많은 타일의 raw 값이 무조건 크다. 그래서 타일 안에서의
-- 백분위(structure_pct_in_tile)를 함께 저장하고, 필지 비교에는 그쪽을 쓴다.
CREATE TABLE IF NOT EXISTS road_segment_structure (
  seg_key       text PRIMARY KEY,   -- osm_id + 좌표열 해시 (roads_clean에 PK가 없다)
  osm_id        bigint NOT NULL,
  highway       text,
  -- (choice + integration) / 2 — undervalued_areas와 같은 결합 방식
  structure_raw double precision NOT NULL,
  choice_raw    double precision,
  integration_raw double precision,
  -- 이 세그먼트가 속한 타일 안에서의 백분위(0~100). 타일 간 비교용.
  structure_pct_in_tile double precision NOT NULL,
  tile_key      text NOT NULL,      -- 어느 타일에서 계산했는지 (감사용)
  tile_segments int  NOT NULL,      -- 그 타일의 세그먼트 수 (백분위 신뢰도 판단용)
  radius_m      int  NOT NULL,      -- 분석 반경
  geom_5179     geometry(LineString, 5179) NOT NULL,
  computed_at   timestamptz NOT NULL DEFAULT NOW()
);

-- 필지 → 최근접 세그먼트 KNN 조인에 쓰인다.
CREATE INDEX IF NOT EXISTS road_segment_structure_geom_idx
  ON road_segment_structure USING GIST (geom_5179);

-- ── 2. 용도지역 폴리곤 ─────────────────────────────────────────────────
-- 가장 중요한 단일 통제변수다. 상업지역이 애초에 간선도로변에 지정되므로 이걸
-- 넣지 않으면 "Integration이 높으면 비싸다"의 대부분이 실제로는 "상업지역이
-- 비싸다"가 된다 (docs/LAND_VALUE_MODEL_RESEARCH.md 3.5).
--
-- 출처는 확보 경로에 따라 다르다(SHP/GeoJSON/WFS) — src에 남긴다.
CREATE TABLE IF NOT EXISTS land_use_zones (
  id          bigserial PRIMARY KEY,
  zone_code   text,               -- 원본 코드 (있으면)
  zone_name   text NOT NULL,      -- 제1종전용주거지역 / 일반상업지역 …
  -- 계층 키·용적률 룰에 쓰는 정규화된 대분류. 원본 명칭 표기가 출처마다
  -- 다르므로(공백·괄호) 여기서 한 번 정규화해 둔다.
  zone_group  text NOT NULL,      -- residential_1_excl / commercial_general …
  notice_date date,
  src         text NOT NULL,
  geom_5179   geometry(MultiPolygon, 5179) NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS land_use_zones_geom_idx
  ON land_use_zones USING GIST (geom_5179);
CREATE INDEX IF NOT EXISTS land_use_zones_group_idx ON land_use_zones (zone_group);

-- ── 3. 판정 결과 ───────────────────────────────────────────────────────
-- 요청 시점엔 이 표만 읽는다 (5초 룰). 배치가 채운다.
CREATE TABLE IF NOT EXISTS parcel_undervalued_scores (
  pnu                   text NOT NULL,
  base_year             int  NOT NULL,
  adm_cd                text NOT NULL,
  zone_group            text NOT NULL,
  structure_score       double precision NOT NULL,  -- 타일 내 백분위를 입력으로 씀
  price_per_sqm         double precision NOT NULL,   -- 개별공시지가 원/㎡
  structure_percentile  double precision NOT NULL,
  price_percentile      double precision NOT NULL,
  gap_score             double precision NOT NULL,
  -- 어느 계층에서 비교했는지. 통제가 약한 'zone' 단계는 상위 목록에서 뺀다.
  stratum_level         text NOT NULL,
  stratum_key           text NOT NULL,
  stratum_size          int  NOT NULL,
  seg_key               text,                        -- 근거 세그먼트 (역추적용)
  computed_at           timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (pnu, base_year)
);

-- 상위 목록 조회: 통제가 유효한 계층 안에서 갭 내림차순.
CREATE INDEX IF NOT EXISTS parcel_undervalued_top_idx
  ON parcel_undervalued_scores (base_year, stratum_level, gap_score DESC);
-- 동 하나를 클릭했을 때 그 안 필지들.
CREATE INDEX IF NOT EXISTS parcel_undervalued_dong_idx
  ON parcel_undervalued_scores (adm_cd, base_year, gap_score DESC);
