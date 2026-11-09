-- 정비구역(재개발·재건축 등) 폴리곤 → redevelopment_zones
--
-- 원천: 서울 열린데이터광장 "서울시 의제처리구역 위치정보"(OA-20957),
-- 레이어 UPIS_C_UQ181. 도시·주거환경정비법과 도시재정비촉진특별법에 따른
-- 구역 18종이 한 레이어에 들어 있다.
--
-- **재건축·재개발은 땅값에 큰 영향을 준다** (소유자 지적, 2026-08-24). 그래서
-- 이 표의 핵심은 폴리곤이 아니라 **고시일**이다 — 시점이 없으면 과거 연도 행에
-- 붙이는 순간 미래를 보게 되어 방법론 A·B에 쓸 수 없다.
--
-- ⚠️ 고시일은 별도 컬럼이 아니라 ``NTFC_SN``(고시번호)에 인코딩돼 있다:
--
--     11000NTC201706088160
--     └구┘  └─┘└──날짜──┘└번호┘
--
-- 2026-08-24 실측(202602 배포본, 3,209건): 79.8%(2,560건)에서 날짜를 뽑았고
-- **1990~2026년 37개 연도**에 걸쳐 있다. 즉 최신 파일 하나로 과거 전체가
-- 복원된다 — 과거 배포본을 모을 필요가 없다.
--
-- ⚠️ ``SIGNGU_SE``를 자치구 판정에 쓰면 안 된다. 3,209건 중 2,131건(66%)이
-- '11000'(서울시 광역)이다. 자치구는 **공간조인으로** 판정한다.
--
-- ⚠️ 해제된 구역은 최신 파일에 없다. 구역 해제도 땅값에 영향을 주므로,
-- 해제 이력이 필요해지면 과거 배포본(2021~)을 함께 적재해 차집합을 낸다.

CREATE TABLE IF NOT EXISTS redevelopment_zones (
  -- 원본에 안정적인 PK가 없다. 고시번호 + 도면명 해시로 만든다.
  zone_key      text PRIMARY KEY,
  zone_name     text NOT NULL,          -- DGM_NM  (예: "독산시흥구역")
  -- UQ1221=주택정비형 재개발, UQ1222=도시정비형 재개발, UQ1231/1232=재건축,
  -- UQ1811~1814=가로주택·자율주택 등 소규모. 비어 있는 행도 있다(원본 결측).
  zone_class    text,                   -- SCLAS_CL
  zone_class_l  text,                   -- LCLAS_CL
  notice_no     text,                   -- NTFC_SN 원문 (감사용 — 날짜 추출 근거)
  -- NTFC_SN에서 뽑은 고시일. **A/B에 쓸 수 있는지를 이 컬럼이 정한다.**
  -- NULL이면 시점 불명이라 방법론 C에만 쓴다.
  notice_date   date,
  consent_no    text,                   -- WTNNC_SN (의제처리 승인번호)
  sgg_code      text,                   -- SIGNGU_SE 원문. 66%가 '11000'이니 신뢰하지 말 것.
  drawing_date  date,                   -- CREATE_DAT (도면 생성일 — 고시일이 아니다)
  area_sqm      double precision,       -- DGM_AR (원본 신고 면적)
  src           text NOT NULL,          -- 어느 배포본에서 왔는지 (예: uq181_202602)
  geom_5179     geometry(MultiPolygon, 5179) NOT NULL,
  ingested_at   timestamptz NOT NULL DEFAULT NOW()
);

-- 필지 ↔ 구역 공간조인이 유일한 접근 패턴이다.
CREATE INDEX IF NOT EXISTS redevelopment_zones_geom_idx
  ON redevelopment_zones USING GIST (geom_5179);
-- "t년에 이 구역이 이미 지정돼 있었나"를 연도별로 물으므로 고시일에 인덱스를 둔다.
CREATE INDEX IF NOT EXISTS redevelopment_zones_notice_idx
  ON redevelopment_zones (notice_date);
CREATE INDEX IF NOT EXISTS redevelopment_zones_class_idx
  ON redevelopment_zones (zone_class);

-- ⚠️⚠️ zone_class가 NULL인 행을 "정비구역"으로 쓰지 말 것 (2026-08-24 실측).
-- 3,209건 중 1,052건이 NULL이고 평균 면적 272,180㎡ — 실제 정비구역(3천~8만㎡)의
-- 3~90배다. 정체는 1940~90년대 토지구획정리사업이다(영동2 1985년 1,300ha,
-- 시흥 1980년 563ha, 영등포 1946년). 구 하나를 통째로 덮는다.
--
-- 금천구 실측: 구역에 걸린 필지 15,940건 중 15,697건(98%)이 이 NULL 구역이다.
-- 그대로 쓰면 거의 모든 필지가 1로 찍혀 변별력이 0이 된다.
-- zone_class IS NOT NULL로 걸러야 1,966건(12%)이 남고 그게 실제 재개발·재건축이다.
--
-- 이 뷰를 쓰면 실수할 수 없다. feature 생성은 여기서 읽는다.
CREATE OR REPLACE VIEW redevelopment_zones_real AS
SELECT * FROM redevelopment_zones
WHERE zone_class IS NOT NULL
  -- 토지구획정리사업이 코드를 갖고 들어오는 경우를 면적으로 한 번 더 막는다.
  -- 실제 정비구역 최대가 약 30ha대이므로 100ha를 상한으로 둔다.
  AND (area_sqm IS NULL OR area_sqm < 1000000);
