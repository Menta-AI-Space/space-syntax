-- 필지 하나의 비교군 중앙값 조회용 인덱스.
--
-- `/api/parcel-analysis`가 필지를 하나 읽은 뒤 "같은 계층의 다른 필지들"의
-- 공시지가 중앙값을 구한다. 그 조건이 `stratum_key = ? AND stratum_level = ?
-- AND base_year = ?`인데, 013에 만든 인덱스 둘은 (base_year, stratum_level,
-- gap_score)와 (adm_cd, base_year, gap_score)라 stratum_key로 좁히지 못한다.
-- 없으면 669,610행 전량 스캔이 필지 클릭마다 일어나 5초 룰에 걸린다.
--
-- ⚠️ 운영 반영은 사람이 한다 (CLAUDE.md — DB migration의 운영 반영은 승인 필요).
-- 인덱스 생성은 되돌릴 수 있고 데이터를 바꾸지 않지만, 표가 클 때 잠금이 걸릴 수
-- 있으므로 운영에서는 CONCURRENTLY로 만드는 것을 권한다:
--
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS parcel_undervalued_stratum_idx
--     ON parcel_undervalued_scores (base_year, stratum_level, stratum_key);
--
-- (CONCURRENTLY는 트랜잭션 블록 안에서 못 쓰므로 이 파일에는 넣지 않는다.)

CREATE INDEX IF NOT EXISTS parcel_undervalued_stratum_idx
  ON parcel_undervalued_scores (base_year, stratum_level, stratum_key);
