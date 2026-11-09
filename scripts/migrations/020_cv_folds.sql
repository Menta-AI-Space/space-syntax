-- 모델 학습·검증용 fold 분할. 세 방법론(A 미래가격 / B 뜰자리 / C 저평가)이
-- 전부 이 표를 공유한다 — "어느 행으로 학습했고 어느 행으로 평가했나"의 정본이다.
--
-- 계산 규칙은 app/services/cv_split.py(순수 함수), 배치는 tools/compute_cv_folds.py.
--
-- Run: psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/020_cv_folds.sql

-- ⚠️ 이 표가 틀리면 모든 성능 수치가 거짓이 된다. 그래서 배정 결과만 담지 않고
-- **어떤 규칙으로 배정됐는지**(scheme·horizon·절단선·블록)를 행마다 함께 남긴다.
-- 나중에 "이 R²는 어느 분할에서 나온 값인가"를 SQL로 되짚을 수 있어야 한다.
CREATE TABLE IF NOT EXISTS cv_folds (
  -- fold_id 형식: "{scheme}-h{horizon}-s{공간fold}-t{절단연도}"
  -- 예) spatial_temporal-h1-s2-t2023
  fold_id       text NOT NULL,
  pnu           text NOT NULL,
  base_year     int  NOT NULL,
  split_role    text NOT NULL CHECK (split_role IN ('train', 'test')),

  -- ── 감사용 컬럼: 배정 근거 ──────────────────────────────────────────
  -- 'spatial_temporal' = 서비스용. 'random_spatial' = 진단용 비교군.
  -- ⚠️ random_spatial의 성능을 서비스 성능으로 보고하면 안 된다. 두 scheme의
  --    성능 차이가 곧 공간 누출의 크기다.
  scheme        text NOT NULL,
  -- 방법론 A의 예측 지평. X(i,t) → P(i,t+h). h=0은 방법론 C(같은 해 횡단면).
  horizon       int  NOT NULL,
  spatial_fold  int  NOT NULL,   -- 홀드아웃 번호 (0..n-1)
  temporal_cut  int  NOT NULL,   -- 시간 절단선. train은 base_year+h <= cut, test는 base_year > cut
  spatial_mode  text NOT NULL,   -- 'ldong'(기본) | 'grid'(진단) | 'pnu'(무작위 비교군)
  spatial_block text NOT NULL,   -- 실제 배정 단위 값. 같은 블록이 양쪽에 있으면 버그다
  computed_at   timestamptz NOT NULL DEFAULT NOW(),

  -- 재실행해도 행이 늘지 않는다 (upsert 키).
  PRIMARY KEY (fold_id, pnu, base_year)
);

-- 학습 배치가 가장 많이 하는 조회: "이 fold의 train 행 / test 행을 다 줘".
CREATE INDEX IF NOT EXISTS cv_folds_role_idx
  ON cv_folds (fold_id, split_role);

-- 필지 하나가 어느 fold에서 test였는지 (화면에서 "이 값은 예측값인가" 판단).
CREATE INDEX IF NOT EXISTS cv_folds_pnu_idx
  ON cv_folds (pnu, base_year);

-- scheme 비교(공간분할 vs 무작위분할)와 horizon별 집계용.
CREATE INDEX IF NOT EXISTS cv_folds_scheme_idx
  ON cv_folds (scheme, horizon, temporal_cut);
