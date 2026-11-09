-- 주변 유사필지 가격·변화 feature (PPT Feature Pool 12).
--
-- 필지 × 연도마다 "그 해 이웃 필지들의 개별공시지가 분포"를 담는다. 계산은
-- backend/tools/compute_parcel_neighbor_prices.py.
--
-- 키는 (pnu, base_year) — land_prices·parcel_characteristics와 같아서 그대로
-- 조인된다. 재실행해도 행이 늘지 않게 upsert한다.
--
-- ⚠️ 이 표를 feature로 쓸 때 반드시 알아야 할 것 넷:
--
-- 1. **대상 필지 자신의 가격은 이웃 통계에서 빠져 있다.** 넣으면 종속변수
--    (공시지가)가 feature로 새어 들어가 R²가 부풀고 실제로는 아무것도 예측하지
--    못한다. 배치가 PNU 동일성으로 제외한다(거리 0이 아니라 식별자 기준 —
--    같은 좌표에 두 필지가 있어도 옆 필지는 살린다).
-- 2. **이웃은 같은 base_year 안에서만 찾는다.** 2026년 이웃 가격을 2018년
--    관측치에 붙이면 미래 정보가 샌다. 배치의 순수 함수가 연도가 섞인 입력을
--    받으면 ValueError로 죽는다.
-- 3. **이웃이 없으면 count는 0이지만 median·mean·p25·p75는 NULL이다.** 0으로
--    채우면 "이웃 없음"이 "이웃이 공짜"로 학습된다. 학습 코드에서 NULL을
--    0으로 impute하지 말 것 — 결측 플래그로 쓰거나 행을 빼라.
-- 4. **경계 절단 편향이 있다.** 반경 안 이웃이 적재 범위 밖에 있으면 잘린다.
--    land_prices는 2026년만 전 서울(887,699행)이고 2018~2025는 금천·강남
--    두 구뿐이다. 그래서 같은 필지라도 2026년은 인접 구 이웃까지 보고
--    2018~2025는 구 경계에서 잘린다 — 연도 간 이웃 통계를 그대로 차분하면
--    "가격 변화"가 아니라 "적재 범위 변화"를 보는 것이 된다.
--    `edge_dist_m`(그 해 적재 범위 경계까지의 거리)와 `edge_truncated`
--    (300m 안쪽인가)로 표시했고, `donor_sgg_count`로 그 해 범위 자체를 남긴다.
--    실측(2026): 금천 6.6%·강남 1.8%가 truncated. 2018년(두 구만):
--    금천 18.5%·강남 15.4%.
--
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/021_parcel_neighbor_prices.sql

CREATE TABLE IF NOT EXISTS parcel_neighbor_prices (
  pnu           text NOT NULL,
  base_year     int  NOT NULL,

  -- 대상 필지 자신의 값. 이웃 통계에는 들어가지 않았고, 아래 비율의 분자이자
  -- 손 검산용이다. ⚠️ 이것은 종속변수(공시지가)이므로 feature로 쓰지 말 것.
  price_per_sqm double precision NOT NULL,
  -- app/services/land_use_zones.normalize_zone_name()으로 정규화한 대분류.
  -- 원본 명칭(parcel_characteristics.zone_name_1)을 그대로 키로 쓰면 공백·괄호
  -- 표기 차이로 같은 용도지역이 여러 계층으로 쪼개진다.
  zone_group    text,

  -- ── 반경 300m 이웃 (자기 제외) ────────────────────────────────────────
  neighbor_count_300m   int NOT NULL,
  neighbor_median_300m  double precision,
  neighbor_mean_300m    double precision,
  neighbor_p25_300m     double precision,
  neighbor_p75_300m     double precision,

  -- ── 반경 500m 이웃 (자기 제외) ────────────────────────────────────────
  neighbor_count_500m   int NOT NULL,
  neighbor_median_500m  double precision,
  neighbor_mean_500m    double precision,
  neighbor_p25_500m     double precision,
  neighbor_p75_500m     double precision,

  -- ── 최근접 20필지 ─────────────────────────────────────────────────────
  -- 반경 고정 통계는 밀도가 낮은 곳(공원·산·공업지 경계)에서 표본이 비어
  -- 버린다. 개수를 고정하면 그런 곳에서도 값이 남는다. 대신 "20필지가 얼마나
  -- 멀리까지 퍼졌는지"는 이 컬럼만 봐서는 모르니 count를 함께 둔다
  -- (도너가 20개보다 적으면 20 미만이 들어간다).
  neighbor_knn20_median double precision,
  neighbor_knn20_count  int NOT NULL,

  -- ── 같은 용도지역으로 제한한 이웃 (용도지역 통제) ─────────────────────
  -- 공시지가 = 비교표준지 × 비준표 배율이고 비준표에 용도지역이 들어 있다.
  -- 용도지역을 섞은 이웃 중위는 "옆에 상업지역이 있느냐"를 주로 반영하므로,
  -- 통제된 비교용으로 같은 대분류만 뽑은 값을 따로 둔다.
  same_zone_median_300m double precision,
  same_zone_count_300m  int NOT NULL,

  -- ── 파생 ──────────────────────────────────────────────────────────────
  -- 자기 가격 / 이웃 중위. 이웃 중위가 NULL이거나 0이면 NULL이다(0으로 나누지
  -- 않는다). ⚠️ 분자가 종속변수라서 이 컬럼도 학습 feature로 쓰면 누출이다 —
  -- 사후 진단·화면 표기용이다.
  price_ratio_to_neighbor_300m double precision,

  -- ── 경계 절단 ─────────────────────────────────────────────────────────
  -- 그 해 land_prices가 덮는 영역(= 그 해 적재된 시군구들의 합집합)의 경계까지의
  -- 거리. 경계는 sgis_dong_boundary를 공간조인해서 그 해 필지가 들어 있는 동을
  -- 골라 합집합한 것이다(land_prices의 법정동 코드와 SGIS 행정동 코드는 체계가
  -- 달라서 코드로 못 맞춘다 — 공간으로 판정한다).
  -- ⚠️ SGIS 경계는 일반화된(점이 적은) 폴리곤이라 이 거리는 ±수십 m 수준이다.
  -- 정밀 거리로 쓰지 말고 플래그로만 쓸 것.
  edge_dist_m     double precision,
  edge_truncated  boolean NOT NULL,
  -- 그 해 land_prices에 있던 시군구 수. 2018~2025는 2, 2026은 25(전 서울).
  -- 연도 간 비교 시 "범위가 달라졌다"를 이 컬럼으로 걸러낸다.
  donor_sgg_count int NOT NULL,

  computed_at   timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (pnu, base_year)
);

-- 학습용 연도별 스캔.
CREATE INDEX IF NOT EXISTS parcel_neighbor_prices_year_idx
  ON parcel_neighbor_prices (base_year);
-- 경계 절단분을 빼고 학습할 때.
CREATE INDEX IF NOT EXISTS parcel_neighbor_prices_edge_idx
  ON parcel_neighbor_prices (base_year, edge_truncated);
