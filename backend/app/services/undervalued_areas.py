""""뜰 자리" — 길 구조는 좋은데 아직 가격에 반영 안 된 서울 행정동을 찾는다.

설계 원칙(decision.py / diagnosis.py와 동일): **순위·백분위·갭 점수는 전부
여기(파이썬)에서 결정론적으로 계산**하고, 배치(``tools/compute_undervalued_areas.py``)가
서울 424개 행정동에 대해 미리 계산해 ``undervalued_area_scores``에 저장한다.
요청 시점(``/api/undervalued-areas``)엔 이 테이블만 읽으므로 5초 룰에 걸리지 않는다.

구조 점수는 각 동 중심점에서 site_score/analyze가 쓰는 것과 같은 엔진
(``fetch_roads_in_radius`` → ``build_graph`` → ``compute_metrics``)으로 500m 도보
반경을 분석해 얻는다 — 새 계산 방식을 만들지 않고 이미 검증된 경로를 재사용한다.
이 값은 그 동의 500m 반경 **안에서의** 상대값이므로, 동끼리 비교하려면 서울
424개 동 분포에서의 백분위로 다시 환산해야 한다(``rank_percentile``이 담당).

가격은 ``real_estate_trades``의 동 폴리곤 내 최근 24개월 실거래 ㎡당가 중앙값이다.
표본이 없는 동은 가격 백분위·갭 점수를 계산하지 않는다(없는 값을 지어내지 않는다).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def rank_percentile(value: float, distribution: list[float]) -> float:
    """0~100, 클수록 분포 안에서 높은 값. (동일값 tie는 평균 순위로 처리)"""
    clean = [v for v in distribution if v is not None]
    if not clean or value is None:
        return 0.0
    lower = sum(1 for v in clean if v < value)
    equal = sum(1 for v in clean if v == value)
    # tie 안의 순위를 평균으로 — 전부 같은 값이면 50.0으로 수렴
    rank = lower + (equal - 1) / 2.0
    return round(100.0 * rank / (len(clean) - 1), 1) if len(clean) > 1 else 50.0


@dataclass
class DongRawInput:
    adm_cd: str
    adm_nm: str
    structure_score: float | None  # None = 도로망을 못 찾음 (분석 불가 동)
    price_per_sqm_manwon: float | None
    trade_sample_count: int


@dataclass
class DongScore:
    adm_cd: str
    adm_nm: str
    structure_score: float
    structure_percentile: float
    price_per_sqm_manwon: float | None
    price_percentile: float | None
    trade_sample_count: int
    gap_score: float | None  # structure_percentile - price_percentile; None이면 가격 표본 없음


def compute_gap_scores(rows: list[DongRawInput]) -> list[DongScore]:
    """동별 원시값 → 서울 전체 대비 백분위 + 갭 점수.

    구조 점수가 없는(도로망을 못 찾은) 동은 결과에서 제외한다 — 0점으로 두면
    "구조가 나쁜 동"과 "분석 자체가 안 된 동"이 섞여 순위가 왜곡된다.
    """
    usable = [r for r in rows if r.structure_score is not None]
    structure_dist = [r.structure_score for r in usable]  # type: ignore[misc]
    price_dist = [
        r.price_per_sqm_manwon for r in usable if r.price_per_sqm_manwon is not None
    ]

    out: list[DongScore] = []
    for r in usable:
        structure_pct = rank_percentile(r.structure_score, structure_dist)  # type: ignore[arg-type]
        price_pct = (
            rank_percentile(r.price_per_sqm_manwon, price_dist)
            if r.price_per_sqm_manwon is not None
            else None
        )
        gap = round(structure_pct - price_pct, 1) if price_pct is not None else None
        out.append(
            DongScore(
                adm_cd=r.adm_cd,
                adm_nm=r.adm_nm,
                structure_score=r.structure_score,  # type: ignore[arg-type]
                structure_percentile=structure_pct,
                price_per_sqm_manwon=r.price_per_sqm_manwon,
                price_percentile=price_pct,
                trade_sample_count=r.trade_sample_count,
                gap_score=gap,
            )
        )
    return out


def list_top_undervalued(limit: int = 20) -> list[dict]:
    """미리 계산된 표에서 갭 점수 상위 N개 동을 읽는다. (요청 시점엔 계산하지 않는다)

    **표가 아직 없으면 빈 목록을 준다.** 배치를 한 번도 안 돌린 환경(새로 띄운
    DB, 마이그레이션 전)에서 psycopg2가 UndefinedTable을 던져 API가 500으로
    나가던 것을 막는다 — 화면에는 "서버 내부 오류"가 뜨는데 실제로는 그냥 아직
    계산을 안 한 것이라, 고장과 미준비를 구분하지 못하게 만든다.
    """
    import psycopg2.errors

    from app.services.spatial_query import get_connection

    try:
        return _query_top_undervalued(limit)
    except (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn) as exc:
        # 스키마가 아직 없다 = 아직 안 돌린 것이지 장애가 아니다. 연결 실패
        # (OperationalError)는 잡지 않는다 — 그건 진짜 장애라 500이 맞다.
        logger.info("undervalued_area_scores 스키마 미준비(%s) — 빈 목록으로 응답", type(exc).__name__)
        return []


def _query_top_undervalued(limit: int) -> list[dict]:
    from app.services.spatial_query import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT adm_cd, adm_nm, structure_score, structure_percentile,
                       price_per_sqm_manwon, price_percentile, trade_sample_count,
                       gap_score, computed_at
                FROM undervalued_area_scores
                WHERE gap_score IS NOT NULL
                ORDER BY gap_score DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in rows]
