-- 「옆에 뭐가 들어서나」를 잡는 시장 신호 테이블 (2026-08-26)
--
-- 멘토 지적: 저평가 판정의 근거가 길 구조뿐이라 **주변에 새 건물이 들어서는 것**
-- 같은 변화를 반영하지 못한다. 맞는 지적이고, 이는 판정 논리의 2단계(통제)를
-- 강화하라는 요구다. 통제하지 못한 요인이 잔차에 남으면 그 잔차는 "저평가"가
-- 아니라 "우리가 아직 모르는 것"이다 (docs/DECISIONS_LAND_MODEL.md).
--
-- ⚠️ **2026-09-02 개정 — 이 표들의 위치가 바뀌었다.**
-- 위 문단은 C(저평가) 기준으로 쓴 것이다. 제품이 B(뜰 자리)로 바뀌면서
-- 「사람의 행동보다 먼저 존재하는가」가 X와 통제변수를 가르는 기준이 되었다.
--   · 건축 인허가 · 도시계획시설 결정 · 대규모점포 개점 → **X (선행. 우리 기여로 센다)**
--     허가는 준공 2~3년 「전」, 결정고시는 개통 수년 「전」에 난다.
--   · 공실률 · 임대가격지수 → **통제변수 (시도/상권 단위이지 필지 값이 아니다)**
-- 자세한 구분표는 docs/DECISIONS_LAND_MODEL.md §10 「X와 통제변수를 다시 가른다」.
--
-- ⚠️ **X로 올랐다고 시점 규율이 느슨해지는 것이 아니다 — 오히려 반대다.**
-- 통제변수일 때의 누수는 판정을 흐릴 뿐이지만, X일 때의 누수는 곧 성능 조작이다.
--
-- ⚠️⚠️ **시점 누수가 이 표들의 최대 위험이다.**
-- 건축인허가에는 허가일·착공일·사용승인일이 함께 들어 있다. 준공(사용승인)
-- 사실을 **그 이전 연도 행에 붙이면 미래를 보는 것**이고, 그러면 백테스트가
-- 전부 거짓이 된다. CLAUDE.md의 "A/B에는 발표일이 없는 feature를 넣을 수 없다"가
-- 정확히 이 경우다. 그래서 원시 표에는 **날짜를 그대로 보존**하고, 시점 절단은
-- 파생 표(parcel_market_signals)에서 base_year 기준으로 «한 곳에서만» 한다.
--
-- Run: psql -h localhost -p 5432 -U spacesyntax -d spacesyntax \
--        -f scripts/migrations/023_market_signals.sql

SET client_encoding = 'UTF8';

-- ── 1. 건축 인허가 ────────────────────────────────────────────────────
-- 국토교통부 건축HUB 「건축인허가 기본개요」 (data.go.kr 15044695 / API 15136267).
--
-- 이 표가 멘토 지적의 정면 답이다. 준공 2~3년 «전»에 허가가 나므로 이건
-- 후행지표가 아니라 **선행지표**이고, 우리 논리(구조지표=선행)와 결이 같다.
--
-- ⚠️ PNU 는 원본에 통짜로 없고 다섯 컬럼을 이어 붙여 만든다:
--      sigungu_cd(5) + bjdong_cd(5) + plat_gb_cd(1) + bun(4) + ji(4) = 19
--    bun·ji 는 0 패딩이 필요하고, plat_gb_cd 는 1=대지 2=산이다.
--    조립에 실패한 행도 «버리지 않고» pnu = NULL 로 남긴다 — 얼마나 못 붙였는지
--    세야 이 데이터의 커버리지를 정직하게 말할 수 있다.
CREATE TABLE IF NOT EXISTS building_permits (
  -- 원본 PK. 관리허가대장 관리번호(mgm_bldrgst_pk)가 안정적이지 않은 경우가 있어
  -- 시군구+대장번호+생성일자 해시로 만든다.
  permit_key      text PRIMARY KEY,
  pnu             text,                 -- 조립 실패 시 NULL (버리지 않는다)
  sigungu_cd      text NOT NULL,
  bjdong_cd       text,
  plat_gb_cd      text,                 -- 1=대지, 2=산
  bun             text,
  ji              text,
  plat_plc        text,                 -- 대지위치 원문 (검산용으로 꼭 남긴다)
  bld_nm          text,

  -- === 시점 컬럼 — 누수 방지의 핵심 ===
  -- 세 날짜의 «의미가 다르다». 어떤 시점 기준으로 자를지는 쓰는 쪽이 정한다.
  permit_date     date,                 -- 건축허가일   ← 가장 이르고 가장 안전하다
  start_date      date,                 -- 실제착공일
  approval_date   date,                 -- 사용승인일(준공) ← 가장 늦다

  -- === 규모 ===
  plat_area       double precision,     -- 대지면적(㎡)
  arch_area       double precision,     -- 건축면적(㎡)
  tot_area        double precision,     -- 연면적(㎡)  ← 「특별한 건물」 판정의 주 지표
  vl_rat          double precision,     -- 용적률
  bc_rat          double precision,     -- 건폐율
  main_purps_nm   text,                 -- 주용도명 (업무시설·판매시설·공동주택 …)
  arch_gb_nm      text,                 -- 건축구분명 (신축·증축·대수선 …)

  src             text NOT NULL,        -- 어느 배포본/연도에서 왔는지
  ingested_at     timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS building_permits_pnu_idx ON building_permits (pnu);
-- 시점 절단 조회가 가장 잦다.
CREATE INDEX IF NOT EXISTS building_permits_permit_date_idx ON building_permits (permit_date);
CREATE INDEX IF NOT EXISTS building_permits_sgg_idx ON building_permits (sigungu_cd);

-- 인허가 위치. 필지 중심점을 그대로 복사해 둔다 — 매번 parcel_centroids 와
-- 조인하면 반경 집계가 느리다(필지 110만 × 인허가 수십만).
ALTER TABLE building_permits ADD COLUMN IF NOT EXISTS geom_5179 geometry(Point, 5179);
CREATE INDEX IF NOT EXISTS building_permits_geom_idx
  ON building_permits USING GIST (geom_5179);


-- ── 2. 대규모점포 ─────────────────────────────────────────────────────
-- 「전국대규모및준대규모점포 표준데이터」(data.go.kr 15114138) 또는 LOCALDATA.
-- 백화점·복합쇼핑몰·대형마트가 들어오면 주변 상권이 통째로 바뀐다 — 길 구조로는
-- 설명되지 않는 변화이므로 반드시 통제한다.
--
-- ⚠️ 영업상태를 꼭 본다. 폐업한 점포를 «지금 있는 것»으로 세면 반대로 틀린다.
CREATE TABLE IF NOT EXISTS large_stores (
  store_key       text PRIMARY KEY,     -- 관리번호(원본) 또는 이름+주소 해시
  store_nm        text NOT NULL,
  store_type      text,                 -- 대형마트·백화점·쇼핑센터·복합쇼핑몰·전문점
  biz_status      text,                 -- 영업/정상, 폐업 …
  license_date    date,                 -- 인허가일자  ← 시점 절단에 쓴다
  close_date      date,                 -- 폐업일자 (있으면)
  addr            text,
  sigungu_cd      text,
  store_area      double precision,     -- 매장면적(㎡)
  src             text NOT NULL,
  geom_5179       geometry(Point, 5179),
  ingested_at     timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS large_stores_geom_idx ON large_stores USING GIST (geom_5179);
CREATE INDEX IF NOT EXISTS large_stores_date_idx ON large_stores (license_date);


-- ── 3. 신설 역·노선 계획 ──────────────────────────────────────────────
-- ⚠️ **정형화된 공공데이터가 없다** (2026-08-26 확인). 노선별 고시·보도자료로
-- 흩어져 있어 자동 적재가 불가능하다. 그래서 이 표는 **사람이 채우는 표**다.
-- 자동으로 채워지는 척하면 안 되므로 출처(src_url)와 확인일(verified_on)을
-- 필수로 두고, 없으면 넣지 못하게 한다.
--
-- ⚠️ 개통 «예정»일은 자주 바뀐다. 그래서 예정일 자체를 feature 로 쓰지 않고,
-- **고시일(announced_on)** 을 시점 기준으로 쓴다 — 시장이 그 사실을 안 날짜는
-- 개통일이 아니라 고시일이기 때문이다.
CREATE TABLE IF NOT EXISTS planned_transit (
  plan_key        text PRIMARY KEY,
  line_nm         text NOT NULL,        -- 노선명
  station_nm      text,                 -- 역명 (노선 전체면 NULL)
  stage           text NOT NULL,        -- 계획/예타/기본계획/착공/개통
  announced_on    date NOT NULL,        -- 고시·발표일  ← 시점 기준은 «이것»
  open_expected   date,                 -- 개통 예정일 (참고용, feature 로 쓰지 말 것)
  src_url         text NOT NULL,        -- 근거 문서 URL
  verified_on     date NOT NULL,        -- 사람이 확인한 날
  note            text,
  geom_5179       geometry(Point, 5179) NOT NULL,
  ingested_at     timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS planned_transit_geom_idx ON planned_transit USING GIST (geom_5179);


-- ── 4. 공실률 ─────────────────────────────────────────────────────────
-- 한국부동산원 상업용부동산 임대동향조사 — 분기별 지역별 공실률·임대가격지수.
--
-- 왜 필요한가: 임대료만 보면 「비싸다」까지만 안다. 공실률을 함께 보면
-- **「비싼데 안 나간다」**를 구분할 수 있고, 저평가 판정에서 이 구분이 크다.
--
-- ⚠️ 단위가 «필지가 아니다». 시도 또는 상권 단위다. 그래서 이 값을 필지 판정에
-- 그대로 쓰면 생태학적 오류다 — 통제변수로만 쓰고, 화면에 필지 값으로 표시하지
-- 않는다 (CONTEXT.md 「동은 탐색, 필지는 판정」).
CREATE TABLE IF NOT EXISTS vacancy_rates (
  yyq             text NOT NULL,        -- 2025Q1 형식
  region_cd       text NOT NULL,        -- 시도코드 또는 상권코드
  region_nm       text,
  region_level    text NOT NULL,        -- sido / trdar
  property_type   text NOT NULL,        -- office / medium_large / small
  vacancy_rate    double precision,     -- %
  rent_index      double precision,     -- 임대가격지수
  src             text NOT NULL,
  ingested_at     timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (yyq, region_cd, region_level, property_type)
);


-- ── 5. 필지 × 연도 파생 신호 (통제변수) ───────────────────────────────
-- ⚠️⚠️ **시점 절단은 오직 이 표를 만들 때만 한다.**
-- 각 행은 「base_year 시점에 이미 공표되어 있던 사실」만 담는다. 그래야
-- 과거 행에 미래 정보가 새지 않는다. 계산은 compute_parcel_market_signals.py.
--
-- 반경을 300m·500m·1km 로 나눈 이유: 옆 건물(300m)과 동네 앵커(1km)는 영향의
-- 성격이 다르다. 하나로 합치면 어느 쪽이 작동했는지 설명하지 못한다.
CREATE TABLE IF NOT EXISTS parcel_market_signals (
  pnu                       text NOT NULL,
  base_year                 int  NOT NULL,

  -- === 건축 인허가 (선행) ===
  -- 「최근 3년」인 이유: 허가 → 준공이 대개 2~3년이라, 이 창이 곧 「지금 짓고 있다」다.
  permits_300m_3y           int NOT NULL DEFAULT 0,
  permits_500m_3y           int NOT NULL DEFAULT 0,
  permit_area_300m_3y       double precision NOT NULL DEFAULT 0,  -- 연면적 합(㎡)
  -- 연면적 1만㎡ 이상 = 대형. 이것 하나가 여러 건의 소규모 신축보다 영향이 크다.
  big_permit_500m_3y        int NOT NULL DEFAULT 0,
  nearest_permit_m          double precision,                     -- 최근접 허가까지 거리
  years_since_permit        double precision,                     -- 그 허가의 경과 연수

  -- === 대규모점포 (앵커) ===
  large_store_1km           int NOT NULL DEFAULT 0,               -- 영업 중인 점포 수
  large_store_opened_1km_3y int NOT NULL DEFAULT 0,               -- 최근 3년 신규 개점
  nearest_large_store_m     double precision,

  -- === 신설 교통 (계획) ===
  nearest_planned_station_m double precision,
  planned_station_stage     text,                                 -- 고시 당시 단계

  -- === 공실률 (수급) ===
  -- 필지가 속한 상권/시도 값을 복사해 온다. «필지 값이 아니다» — 통제용이다.
  vacancy_rate              double precision,
  vacancy_region_level      text,

  computed_at               timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (pnu, base_year)
);

CREATE INDEX IF NOT EXISTS parcel_market_signals_year_idx
  ON parcel_market_signals (base_year);
