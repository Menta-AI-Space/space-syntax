-- 개별공시지가(V-World dsId=6 / AL_D151) 적재 테이블.
--
-- 종속변수 테이블이다. 필지(PNU) × 기준연도가 키이며, 서울은 1990~2026년
-- 37개 스냅샷이 확보된다 — 필지 고정효과 패널이 이 시계열 위에서 돌아간다.
-- 자세한 배경은 docs/LAND_VALUE_MODEL_RESEARCH.md.
--
-- parcel_centroids가 이미 PNU를 키로 갖고 있어 좌표 조인은 그쪽에 맡긴다.
-- 여기엔 지오메트리를 두지 않는다.

CREATE TABLE IF NOT EXISTS land_prices (
  pnu             text    NOT NULL,
  base_year       int     NOT NULL,
  -- 원 데이터의 "공시지가"는 원/㎡ 이다.
  price_per_sqm   bigint  NOT NULL,
  base_month      int,
  -- 표준지여부. 표준지(약 2만 필지 표본)와 개별(전 필지)을 구분해야 한다 —
  -- 선행연구(강창덕 2021)가 표준지만 썼을 가능성이 있어 비교에 필요하다.
  is_standard     boolean,
  notice_date     date,
  ldong_code      text,
  ldong_name      text,
  jibun           text,
  -- 특수지구분(일반/도로/하천 등). 도로·하천 필지는 분석에서 빼야 할 수 있다.
  special_code    text,
  special_name    text,
  src             text,
  ingested_at     timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (pnu, base_year)
);

-- 연도별 배치 적재/조회가 기본 접근 패턴이다.
CREATE INDEX IF NOT EXISTS land_prices_year_idx ON land_prices (base_year);
-- 표준지만 뽑아 선행연구와 비교할 때 쓴다.
CREATE INDEX IF NOT EXISTS land_prices_standard_idx
  ON land_prices (base_year) WHERE is_standard;
