-- 학습용 통합 패널 parcel_panel — 세 방법론(A 미래가격 / B 뜰자리 / C 저평가)의
-- 공용 입력이자, 모델 출력이 돌아와 앉는 자리.
--
-- 이름: 이슈 #29(G1 §9)에서는 parcel_features라 불렀지만, 결정 문서
-- (docs/DECISIONS_LAND_MODEL.md, 2026-08-24)가 **parcel_panel**로 확정했다 —
-- "처음부터 패널 구조로 만든다"가 요지라 이름도 패널이다. 키는 (pnu, base_year).
--
-- 적재는 backend/tools/build_parcel_panel.py. 이 배치는 feature 컬럼만 쓰고,
-- 모델 출력 컬럼(expected_price~predicted_at)은 **건드리지 않는다** — G2 학습
-- 배치가 나중에 채우고, feature 재적재가 예측값을 지우면 안 되기 때문이다.
--
-- ⚠️ Layer A와 Layer B를 컬럼 군으로 분리한 것이 이 표의 핵심이다.
-- 개별공시지가 = 비교표준지 × 토지가격비준표 배율이고 비준표 항목에 용도지역·
-- 도로접면·지목·이용상황이 들어 있다. 즉 Layer A로 공시지가를 예측하면 모델은
-- 행정 산식을 재현할 뿐이다(높은 R²는 성과가 아니다). 우리가 파는 것은 Layer A를
-- 통제한 뒤 Layer B의 **증분 설명력**이다. 어느 컬럼이 어느 층인지의 정본은
-- backend/tools/build_parcel_panel.py의 LAYER_A_COLUMNS / LAYER_B_COLUMNS이고,
-- 테스트가 그 목록과 이 표의 컬럼이 어긋나지 않게 고정한다.
--
-- ⚠️ 이 표에 절대 들어가면 안 되는 것 (누출):
--   - gap_score · price_percentile · structure_percentile
--     (parcel_undervalued_scores) — 가격 백분위가 들어 있어 target 그 자체다.
--   - price_ratio_to_neighbor_300m (parcel_neighbor_prices) — 분자가 target이다.
--   feature로 요긴해 보여서 누군가 다시 넣고 싶어질 컬럼들이라 이름을 박아 둔다.
--   진단·화면용으로는 원본 표를 pnu로 조인해 쓰면 된다. 복사할 이유가 없다.
--
-- Run:
--   psql -h localhost -p 5433 -U spacesyntax -d spacesyntax \
--        -f scripts/migrations/022_parcel_panel.sql

SET client_encoding = 'UTF8';

CREATE TABLE IF NOT EXISTS parcel_panel (
  -- ── 식별 ──────────────────────────────────────────────────────────────
  pnu                    text NOT NULL,
  base_year              int  NOT NULL,
  sgg_code               text NOT NULL,       -- PNU 앞 5자리 (층화·CV와 같은 키)
  ldong_code             text,                -- 법정동 10자리 (CV 공간 블록)

  -- ── 종속변수 ──────────────────────────────────────────────────────────
  -- 개별공시지가(원/㎡). 시세·실거래가가 아니다 — CONTEXT.md의 세 가격 구분.
  official_price_per_sqm double precision NOT NULL,

  -- ── Layer A: 행정 산식(비준표)이 이미 쓰는 변수 — 통제변수 ─────────────
  -- 연도별 실측값이다(parcel_characteristics, 2018~2026 완비). 현재값을 과거로
  -- 복사한 것이 아니다 — 결정 문서의 "용도지역 복사 금지" 함정은 연도별 원본을
  -- 그대로 조인해서 피했다.
  zone_name              text,                -- 용도지역명1 (원문)
  zone_group             text,                -- normalize_zone_name() 대분류
  land_category          text,                -- 지목
  use_situation          text,                -- 이용상황
  area_sqm               double precision,    -- 토지면적
  topograph_height       text,                -- 지형높이
  topograph_form         text,                -- 지형형상
  road_side_name         text,                -- 도로접면 ← 순환성의 최대 원천

  -- ── Layer B: 우리 차별점 — 접근성 (시불변, OSM 스냅샷 하나) ────────────
  -- parcel_accessibility를 pnu로만 조인한다. base_year 조인이 **없는 것이
  -- 의도다** — 스냅샷이 하나라 연도별 값이 존재하지 않고, 있는 척 복사하면
  -- "2018년 필지가 2026년의 역을 안다"가 된다. as_of가 그 사실을 기록한다.
  nearest_station_m      double precision,
  nearest_entrance_m     double precision,
  stations_within_500m   int,
  stations_within_1000m  int,
  nearest_bus_stop_m     double precision,
  bus_stops_within_300m  int,
  bus_stops_within_500m  int,
  nearest_arterial_m     double precision,
  nearest_road_m         double precision,
  access_as_of           date,

  -- ── Layer B: 길 구조 (시불변 스냅샷) ──────────────────────────────────
  -- parcel_undervalued_scores.structure_score(목 점수)만 가져온다.
  -- 같은 표의 percentile·gap_score는 가격이 섞여 있어 절대 가져오지 않는다.
  structure_score        double precision,
  structure_as_of        int,                 -- 그 점수의 base_year (현재 2026)

  -- ── Layer B: 이웃 가격 (연도별, 자기 제외) ────────────────────────────
  -- parcel_neighbor_prices를 (pnu, base_year)로 조인 — 같은 해 이웃만 본다.
  neighbor_median_300m   double precision,
  neighbor_count_300m    int,
  neighbor_median_500m   double precision,
  neighbor_count_500m    int,
  neighbor_knn20_median  double precision,
  same_zone_median_300m  double precision,
  same_zone_count_300m   int,
  -- 경계 절단 플래그. 2018~2025는 도너가 두 구뿐이라 구 경계에서 이웃이 잘린다.
  edge_truncated         boolean,
  donor_sgg_count        int,

  -- ── Layer B: 재개발·재건축 (시변 — 고시일 기준) ───────────────────────
  -- redevelopment_zones_real 중 **고시일이 있는 구역만**, 그리고
  -- notice_date < 1월 1일(base_year) 인 것만 센다. 공시지가 기준일이 1월 1일이라
  -- 그해 중에 고시된 구역은 그해 가격에 반영될 수 없다. 고시일이 없는 구역은
  -- 시점을 모르므로 A/B feature로 못 쓴다(결정 문서) — 여기 안 들어간다.
  in_redev_zone          boolean NOT NULL DEFAULT false,
  redev_zone_class       text,                -- 겹치면 가장 최근 고시 구역의 분류
  redev_notice_year      int,                 -- 그 구역의 고시 연도

  -- ── 성장 (target의 과거값 — 자기회귀 feature) ─────────────────────────
  -- P(t)/P(t-k) - 1. 과거값이라 누출은 아니지만 target의 사촌이므로,
  -- ablation에서 Layer A와 함께 통제군으로 다룬다. 이력 없는 행은 NULL이다
  -- (강남 2018 이전 미적재 → 1y는 2019부터, 3y는 2021부터, 5y는 2023부터).
  price_growth_1y        double precision,
  price_growth_3y        double precision,
  price_growth_5y        double precision,
  -- 이웃 중위의 3년 성장. ⚠️ 도너 범위가 같은 해끼리만 계산한다
  -- (donor_sgg_count가 다르면 NULL) — 2026년은 도너가 2개 구→25개 구로 바뀌어
  -- 그냥 차분하면 "가격 변화"가 아니라 "적재 범위 변화"가 나온다.
  neighbor_growth_3y     double precision,

  -- ── 모델 출력 (G2가 채운다 — feature 배치는 여기 안 쓴다) ─────────────
  expected_price         double precision,    -- "행정이 매길 값"의 추정 (시세 아님)
  price_gap              double precision,    -- 실제 − 추정 (모형 잔차)
  growth_gap             double precision,    -- 방법론 B: 자기 성장 − peer 성장
  hot_prob               double precision,    -- 방법론 B: Hot=1 확률
  model_version          text,
  predicted_at           timestamptz,

  computed_at            timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (pnu, base_year)
);

-- 학습 배치의 기본 스캔: 연도·구 단위.
CREATE INDEX IF NOT EXISTS parcel_panel_year_sgg_idx
  ON parcel_panel (base_year, sgg_code);
-- CV 공간 블록 조인.
CREATE INDEX IF NOT EXISTS parcel_panel_ldong_idx
  ON parcel_panel (ldong_code, base_year);
