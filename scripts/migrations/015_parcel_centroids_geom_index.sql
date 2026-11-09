-- parcel_centroids에 공간 인덱스가 없었다 (2026-08-22 발견).
--
-- `ingest_cadastral.py`가 이 표를 만들 때 PK(pnu)만 걸고 geom_5179에는 아무
-- 인덱스도 두지 않았다. 그래서 필지를 좌표로 찾는 모든 조회가 110만 행 전체
-- 스캔이었다:
--
--   * `compute_parcel_undervalued.py`의 필지 → 최근접 세그먼트 KNN
--   * 세그먼트 주변 필지 조회 (ST_DWithin)
--   * 필지 단위 실험·집계 전반
--
-- 실측: 인덱스 없이 세그먼트 13,821개에 대한 필지 조회가 5분을 넘겨도 안
-- 끝났고, 인덱스를 만들자 **3초**에 끝났다. 인덱스 생성 자체도 3초다.
--
-- 서비스 중단 없이 적용하려면 CONCURRENTLY를 쓴다(트랜잭션 밖에서 실행해야
-- 하므로 psql -f 로 이 파일을 통째로 돌릴 때는 아래 일반 버전이 돈다).

CREATE INDEX IF NOT EXISTS parcel_centroids_geom_idx
  ON parcel_centroids USING GIST (geom_5179);

-- 플래너가 새 인덱스를 쓰도록 통계를 갱신한다.
ANALYZE parcel_centroids;
