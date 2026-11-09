"""CV fold 배치 — 패널 관측치를 읽어 ``cv_folds``에 train/test 배정을 저장한다.

계산 규칙은 전부 ``app/services/cv_split.py``(DB 없이 도는 순수 함수)에 있고,
여기서는 **읽고 저장하고 실측으로 검증하는 일**만 한다. 이 파일에 판단 로직을
넣지 말 것 — 그러면 테스트가 닿지 않는 곳에 누출 규칙이 생긴다.

읽는 것
-------
``land_prices``(개별공시지가, PNU×기준연도) × ``parcel_centroids``(좌표). 동 코드는
``land_prices.ldong_code``를 쓴다 — 실측에서 ``LEFT(pnu,10)``과 100% 일치했고
(금천·강남 503,454행, 불일치 0) NULL도 없다.

⚠️ **연도가 다 있는 필지만 쓴다.** 어떤 해에 값이 없는 필지를 그대로 넣으면 fold별
표본이 연도에 따라 들쭉날쭉해져 "성능이 올랐다"가 표본 변화인지 모델 개선인지
구분되지 않는다. 빠진 연도를 **채우지는 않는다**(스펙: 결측을 지어내지 않는다) —
완주하지 않은 필지를 제외할 뿐이고, 몇 개가 빠졌는지 로그로 남긴다.

저장 후 반드시 검증한다
-----------------------
fold를 넣고 나서 ``check_leakage``로 fold마다 다시 훑는다. 합성 테스트에 없는
실측 사고(같은 PNU가 동을 옮긴 행, 좌표 이상치)가 있으면 여기서 멈춰야 한다.
누출이 있으면 저장하지 않고 비정상 종료한다 — 깨진 fold로 학습하는 것보다 배치가
실패하는 게 항상 싸다.

Run:
  # 금천·강남 2018~2026, horizon 1, 계산만
  cd backend && .venv/Scripts/python -m tools.compute_cv_folds --dry-run
  # 실제 저장 (진단용 무작위 분할까지 함께)
  cd backend && .venv/Scripts/python -m tools.compute_cv_folds --horizons 1
  # 방법론 A의 3·5년 지평까지
  cd backend && .venv/Scripts/python -m tools.compute_cv_folds --horizons 1,3,5
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import psycopg2.extras

from app.services.cv_split import (
    DEFAULT_SALT,
    SCHEME_RANDOM_SPATIAL,
    SCHEME_SPATIAL_TEMPORAL,
    Fold,
    PanelObs,
    check_leakage,
    check_stratum_coverage,
    missing_train_strata,
    split_spatial_temporal,
)
from app.services.spatial_query import get_connection

logger = logging.getLogger(__name__)

# MVP에서 9개 연도(2018~2026)를 완주한 시군구. 이 밖은 연도 구멍이 커서 패널이
# 안 된다 — 금천 2015~2017은 8,867행뿐(2018년의 44%)이라 넣으면 표본이 흔들린다.
DEFAULT_SGG = ("11545", "11680")
DEFAULT_YEARS = (2018, 2026)

# 화면 데모·회귀 스모크테스트 구역(금천구 독산동, 8,713필지). **항상 test**로 둔다.
# 이유: 화면에 "AI 추정"으로 보여 주는 값이 학습에 쓰인 값이면 그건 예측이 아니라
# 암기다. 대가는 있다 — 학습 표본에서 이 동이 통째로 빠진다. 대가를 감수할지는
# --no-pin-demo로 뒤집을 수 있게 열어 뒀다.
DEMO_TEST_BLOCKS = ("1154510200",)

_PANEL_SQL = """
WITH complete AS (
    SELECT pnu
    FROM land_prices
    WHERE LEFT(pnu, 5) = ANY(%(sgg)s)
      AND base_year BETWEEN %(y0)s AND %(y1)s
    GROUP BY pnu
    HAVING COUNT(DISTINCT base_year) = %(n_years)s
)
SELECT lp.pnu, lp.base_year, lp.ldong_code,
       ST_X(pc.geom_5179), ST_Y(pc.geom_5179)
FROM land_prices lp
JOIN complete c ON c.pnu = lp.pnu
JOIN parcel_centroids pc ON pc.pnu = lp.pnu
WHERE lp.base_year BETWEEN %(y0)s AND %(y1)s
  AND lp.ldong_code IS NOT NULL
ORDER BY lp.pnu, lp.base_year
"""

_COUNT_ALL_SQL = """
SELECT COUNT(*) FROM land_prices
WHERE LEFT(pnu, 5) = ANY(%(sgg)s) AND base_year BETWEEN %(y0)s AND %(y1)s
"""

_UPSERT_SQL = """
INSERT INTO cv_folds (
    fold_id, pnu, base_year, split_role, scheme, horizon,
    spatial_fold, temporal_cut, spatial_mode, spatial_block, computed_at
) VALUES %s
ON CONFLICT (fold_id, pnu, base_year) DO UPDATE SET
    split_role    = EXCLUDED.split_role,
    scheme        = EXCLUDED.scheme,
    horizon       = EXCLUDED.horizon,
    spatial_fold  = EXCLUDED.spatial_fold,
    temporal_cut  = EXCLUDED.temporal_cut,
    spatial_mode  = EXCLUDED.spatial_mode,
    spatial_block = EXCLUDED.spatial_block,
    computed_at   = EXCLUDED.computed_at
"""


def load_panel(conn, *, sgg: list[str], years: tuple[int, int]) -> list[PanelObs]:
    y0, y1 = years
    n_years = y1 - y0 + 1
    params = {"sgg": list(sgg), "y0": y0, "y1": y1, "n_years": n_years}
    with conn.cursor() as cur:
        cur.execute(_COUNT_ALL_SQL, params)
        n_all = cur.fetchone()[0]
        cur.execute(_PANEL_SQL, params)
        rows = [
            PanelObs(pnu=r[0], base_year=r[1], ldong_code=r[2], x=r[3], y=r[4])
            for r in cur.fetchall()
        ]
    n_parcels = len({r.pnu for r in rows})
    logger.info(
        "패널 %d행 (필지 %d개 × 연도 %d개) — 원본 %d행에서 %d행 제외(연도 미완주 또는 좌표 없음)",
        len(rows), n_parcels, n_years, n_all, n_all - len(rows),
    )
    return rows


# ⚠️ upsert 키가 (fold_id, pnu, base_year)라, 배정 규칙이 바뀌면(층화 도입·salt
# 변경) **옛 배정 행이 그대로 남는다**. 같은 fold에 옛 배정과 새 배정이 섞이면
# train에 test 필지가 들어앉는데 어느 검증도 그걸 못 잡는다 — 가장 위험한 상태다.
# 그래서 저장 전에 그 (scheme, horizon) 조합을 지우고 새로 넣는다.
_DELETE_SQL = "DELETE FROM cv_folds WHERE scheme = %s AND horizon = %s"


def replace_scheme(conn, scheme: str, horizon: int) -> int:
    with conn.cursor() as cur:
        cur.execute(_DELETE_SQL, (scheme, horizon))
        n = cur.rowcount
    conn.commit()
    return n


def store_fold(conn, fold: Fold) -> int:
    values = [
        (
            fold.fold_id, a.pnu, a.base_year, a.split_role, fold.scheme,
            fold.horizon, fold.spatial_fold, fold.temporal_cut,
            fold.spatial_mode, a.spatial_block,
        )
        for a in fold.assignments
    ]
    if not values:
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur, _UPSERT_SQL, values,
            template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
            page_size=5000,
        )
    conn.commit()
    return len(values)


def verify_folds(folds: list[Fold], rows: list[PanelObs]) -> None:
    """저장 전에 fold마다 누출 성질을 실측으로 확인한다. 하나라도 깨지면 중단한다.

    합성 테스트에 없는 실측 사고(좌표 이상치, 동 코드가 바뀐 행)가 여기서 잡힌다.
    깨진 fold로 학습하는 것보다 배치가 실패하는 게 항상 싸다.
    """
    broken = []
    missing_by_fold: list[tuple[str, tuple[str, ...]]] = []
    for fold in folds:
        report = check_leakage(fold, rows)
        # 무작위 비교군은 정의상 블록·격자가 겹친다(그게 비교군인 이유다).
        # 시간 성질과 표본 존재만 본다.
        if fold.scheme == SCHEME_RANDOM_SPATIAL:
            ok = (
                report.n_train > 0 and report.n_test > 0
                and report.max_train_year is not None
                and report.min_test_year is not None
                and report.max_train_year < report.min_test_year
            )
        else:
            ok = report.ok and report.shared_grid_cells == 0
        if not ok:
            broken.append(report)
        # 층(시군구) 커버리지 — train에서 어느 구가 통째로 사라지면 그 fold는
        # 구 간 외삽을 재는 것이라 다른 fold와 평균할 수 없다. 실사고가 있었다:
        # 금천구의 배정 가능한 동 2개(독산동은 test 고정)를 한 fold가 다 홀드아웃했다.
        missing = missing_train_strata(fold)
        if missing:
            missing_by_fold.append((fold.fold_id, missing))
        logger.info(
            "  %s train %d / test %d · train≤%s < test≥%s · 블록겹침 %d · 격자겹침 %d "
            "· 구별 %s · 제외 %s",
            fold.fold_id, report.n_train, report.n_test,
            report.max_train_year, report.min_test_year,
            len(report.shared_blocks), report.shared_grid_cells,
            dict(sorted(check_stratum_coverage(fold).items())), fold.dropped,
        )
    if broken:
        for r in broken:
            logger.error("누출 발견: %s %r", r.fold_id, r)
        raise SystemExit("누출이 있는 fold가 있다 — 저장하지 않고 중단한다")
    if missing_by_fold:
        for fold_id, missing in missing_by_fold:
            logger.error("train에 없는 구: %s → %s", fold_id, missing)
        raise SystemExit(
            "어떤 fold의 train에서 시군구가 통째로 빠졌다 — 층화 배정이 성립하지 "
            "않는다(그 구의 동이 fold 수만큼 없다). 저장하지 않고 중단한다"
        )
    logger.info("누출·층 커버리지 검증 통과 — fold %d개", len(folds))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sgg", default=",".join(DEFAULT_SGG),
                        help="시군구코드 5자리 콤마 구분")
    parser.add_argument("--years", default=f"{DEFAULT_YEARS[0]}-{DEFAULT_YEARS[1]}",
                        help="패널 연도 범위 (예: 2018-2026)")
    parser.add_argument("--horizons", default="1",
                        help="예측 지평 콤마 구분. 0=방법론 C(같은 해), 1/3/5=방법론 A")
    parser.add_argument("--spatial-folds", type=int, default=4)
    parser.add_argument("--spatial-mode", default="ldong", help="ldong(기본) | grid(진단)")
    parser.add_argument("--max-cuts", type=int, default=3,
                        help="시간 절단선 개수 상한 (최신 쪽을 남긴다)")
    parser.add_argument("--salt", default=DEFAULT_SALT,
                        help="⚠️ 바꾸면 과거 성능 수치와 비교가 불가능해진다")
    parser.add_argument("--no-pin-demo", action="store_true",
                        help="데모 구역(독산동) test 고정을 해제한다")
    parser.add_argument("--no-random", action="store_true",
                        help="진단용 무작위 분할 비교군을 만들지 않는다")
    parser.add_argument("--dry-run", action="store_true", help="계산·검증만 하고 저장하지 않음")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    y0, y1 = (int(v) for v in args.years.split("-"))
    horizons = [int(v) for v in args.horizons.split(",")]
    pinned = () if args.no_pin_demo else DEMO_TEST_BLOCKS
    schemes = [SCHEME_SPATIAL_TEMPORAL]
    if not args.no_random:
        schemes.append(SCHEME_RANDOM_SPATIAL)

    with get_connection() as conn:
        rows = load_panel(conn, sgg=args.sgg.split(","), years=(y0, y1))
        if not rows:
            raise SystemExit("패널이 비어 있다 — land_prices/parcel_centroids를 먼저 확인할 것")

        # (horizon, scheme) 묶음 하나씩 만들고 → 검증하고 → 저장하고 → 버린다.
        # 24개 fold를 한꺼번에 메모리에 들고 있으면 배정 객체가 550만 개가 되어
        # MemoryError가 난다(실제로 났다). horizon을 늘릴 때 터지는 자리다.
        total = 0
        for horizon in horizons:
            for scheme in schemes:
                t0 = time.time()
                folds = split_spatial_temporal(
                    rows,
                    horizon=horizon,
                    n_spatial_folds=args.spatial_folds,
                    spatial_mode=args.spatial_mode,
                    scheme=scheme,
                    salt=args.salt,
                    pinned_test_blocks=pinned,
                    max_temporal_cuts=args.max_cuts,
                )
                logger.info(
                    "h=%d %s → fold %d개 (%.1fs)", horizon, scheme, len(folds), time.time() - t0
                )
                verify_folds(folds, rows)
                if args.dry_run:
                    continue
                # 배정 규칙이 바뀌었을 수 있으므로 지우고 새로 넣는다.
                n_del = replace_scheme(conn, scheme, horizon)
                if n_del:
                    logger.info("기존 배정 삭제 — %s h=%d %d행", scheme, horizon, n_del)
                for i, fold in enumerate(folds, 1):
                    t1 = time.time()
                    n = store_fold(conn, fold)
                    total += n
                    logger.info("[%d/%d] %s %d행 저장 (%.1fs)",
                                i, len(folds), fold.fold_id, n, time.time() - t1)
                del folds

        if args.dry_run:
            logger.info("dry-run, 저장 안 함")
            return 0
        logger.info("완료 — %d행", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
