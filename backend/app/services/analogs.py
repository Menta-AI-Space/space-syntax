"""이 자리와 "장사 구조가 비슷한" 서울 상권 찾기 (find_analogs).

GrowthFactor의 find_analogs를 서울 데이터로 옮긴 것. 핵심 규칙 하나:

**유사도는 원인 변수로 재고, 성과는 비교만 한다.** 점포당 매출(성과)로
비슷한 곳을 찾으면 "매출이 비슷한 곳은 매출이 비슷하다"는 동어반복이 된다.
비슷하게 생긴 상권(직장인 규모·점포 밀도·주말/평일 성격·프랜차이즈 비율)을
찾아 놓고, 그곳들의 성과가 어떤지를 보여줘야 "구조가 같은데 성과가 다르다면
왜인가"라는 진짜 질문이 생긴다.

데이터는 서울 상권분석서비스 적재분(trdar_sales·trdar_stores·trdar_workpop,
약 1,650개 상권)만 쓴다 — 런타임 외부 API 호출 없음(5초 룰).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import psycopg2

from app.config import settings
from app.services.trdar_sales_local import _monthly, profile_svc_codes


class AnalogsError(Exception):
    pass


@dataclass
class TrdarFeatures:
    """상권 하나의 '생김새'(유사도 입력)와 '성과'(비교 출력)."""

    trdar_cd: str
    trdar_nm: str
    # --- 원인 변수 (유사도에 들어감) ---
    workers: float | None       # 직장인 규모
    store_count: float          # 동일업종 점포 수 (밀도 대용)
    weekend_share: float | None  # 주말 매출 비중 — 오피스/주거 성격
    franchise_ratio: float | None
    # --- 성과 (유사도에 안 들어감 — 비교만) ---
    per_store_monthly_won: float | None


#: 유사도에 쓰는 특성과 로그 변환 여부. 규모 변수(직장인·점포수)는 로그를
#: 씌운다 — 강남(30만)과 주택가(3천)를 선형으로 재면 큰 쪽 차이만 지배한다.
_FEATURES: tuple[tuple[str, bool], ...] = (
    ("workers", True),
    ("store_count", True),
    ("weekend_share", False),
    ("franchise_ratio", False),
)


def _vector(row: TrdarFeatures) -> list[float] | None:
    """유사도 벡터. 원인 변수가 하나라도 없으면 후보에서 뺀다 —
    없는 값을 0으로 메우면 '데이터 없는 상권'끼리 비슷하다고 나온다."""
    out: list[float] = []
    for name, use_log in _FEATURES:
        value = getattr(row, name)
        if value is None:
            return None
        out.append(math.log1p(float(value)) if use_log else float(value))
    return out


def rank_analogs(
    subject: TrdarFeatures, candidates: list[TrdarFeatures], k: int = 3
) -> list[tuple[TrdarFeatures, float]]:
    """subject와 구조가 비슷한 순으로 k곳. (상권, 유사도 0~100) 목록.

    z-정규화한 유클리드 거리 — 특성마다 단위가 달라(명·개·비율) 그대로 재면
    큰 단위가 지배한다. 순수 함수라 DB 없이 테스트된다.
    """
    sv = _vector(subject)
    if sv is None:
        return []
    rows = [(c, _vector(c)) for c in candidates if c.trdar_cd != subject.trdar_cd]
    vectors = [v for _, v in rows if v is not None]
    if not vectors:
        return []

    dims = len(sv)
    means = [sum(v[i] for v in vectors) / len(vectors) for i in range(dims)]
    stds = [
        math.sqrt(sum((v[i] - means[i]) ** 2 for v in vectors) / len(vectors)) or 1.0
        for i in range(dims)
    ]

    def z(vec: list[float]) -> list[float]:
        return [(vec[i] - means[i]) / stds[i] for i in range(dims)]

    szv = z(sv)
    scored: list[tuple[TrdarFeatures, float]] = []
    for cand, vec in rows:
        if vec is None:
            continue
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(szv, z(vec))))
        # 거리→유사도(0~100). 2σ 거리면 0점 — 그보다 먼 것은 "비슷하다"고
        # 부를 수 없다.
        similarity = max(0.0, 100.0 * (1.0 - dist / (2.0 * math.sqrt(dims))))
        scored.append((cand, similarity))
    scored.sort(key=lambda pair: -pair[1])
    return [pair for pair in scored if pair[1] > 0][:k]


def _load_features(profile_id: str) -> tuple[str, list[TrdarFeatures]]:
    """전 상권의 특성 한 번에. (분기, 목록).

    상권이 ~1,650개라 전량을 파이썬으로 가져와 계산해도 수십 ms다 — SQL에서
    z-정규화까지 하려고 복잡한 쿼리를 쓰지 않는다.
    """
    codes = profile_svc_codes(profile_id, include_secondary=True)
    if not codes:
        raise AnalogsError(f"업종 매핑 없음: {profile_id}")
    conn = psycopg2.connect(settings.database_url, connect_timeout=4)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT max(yyq) FROM trdar_sales WHERE svc_cd = ANY(%s)", (codes,)
        )
        quarter = (cur.fetchone() or [None])[0]
        if not quarter:
            raise AnalogsError("매출 데이터 없음")
        cur.execute(
            """
            SELECT s.trdar_cd,
                   max(s.trdar_nm),
                   sum(s.month_sales_amt),
                   sum(s.weekday_sales_amt),
                   sum(s.weekend_sales_amt),
                   sum(st.store_count),
                   sum(st.franchise_count),
                   max(w.total_workers)
            FROM trdar_sales s
            LEFT JOIN trdar_stores st
                   ON st.yyq = s.yyq AND st.trdar_cd = s.trdar_cd AND st.svc_cd = s.svc_cd
            LEFT JOIN trdar_workpop w
                   ON w.yyq = s.yyq AND w.trdar_cd = s.trdar_cd
            WHERE s.yyq = %s AND s.svc_cd = ANY(%s)
            GROUP BY s.trdar_cd
            """,
            (quarter, codes),
        )
        out: list[TrdarFeatures] = []
        for cd, nm, sales, wk, we, stores, fr, workers in cur.fetchall():
            stores_n = float(stores) if stores else 0.0
            # 점포 3곳 미만은 뺀다 — 한두 집 매출로 "이 동네는 이렇다"고
            # 말하면 그 집 사정이 동네 이야기로 둔갑한다.
            if stores_n < 3:
                continue
            weekend = None
            if wk is not None and we is not None and (wk + we) > 0:
                weekend = float(we) / float(wk + we)
            out.append(TrdarFeatures(
                trdar_cd=cd, trdar_nm=nm or cd,
                workers=float(workers) if workers is not None else None,
                store_count=stores_n,
                weekend_share=weekend,
                franchise_ratio=(float(fr) / stores_n) if fr is not None else None,
                per_store_monthly_won=_monthly(float(sales)) / stores_n if sales else None,
            ))
        return quarter, out
    finally:
        conn.close()


#: 특성 전량 로드는 분기가 바뀔 때만 달라진다 — 한 시간 캐시.
_cache: dict[str, tuple[float, str, list[TrdarFeatures]]] = {}
_CACHE_TTL_S = 3600.0


def find_analogs(trdar_cd: str, profile_id: str, k: int = 3) -> dict[str, Any]:
    """이 상권과 구조가 비슷한 서울 상권 k곳과 그곳의 성과."""
    now = time.monotonic()
    cached = _cache.get(profile_id)
    if cached is not None and now - cached[0] < _CACHE_TTL_S:
        quarter, rows = cached[1], cached[2]
    else:
        quarter, rows = _load_features(profile_id)
        _cache[profile_id] = (now, quarter, rows)

    subject = next((r for r in rows if r.trdar_cd == trdar_cd), None)
    if subject is None:
        raise AnalogsError("이 지점의 상권 데이터가 없어 비슷한 동네를 찾을 수 없습니다.")

    ranked = rank_analogs(subject, rows, k=k)

    def _row(f: TrdarFeatures, similarity: float | None) -> dict[str, Any]:
        return {
            "trdar_cd": f.trdar_cd,
            "trdar_nm": f.trdar_nm,
            "similarity": round(similarity, 1) if similarity is not None else None,
            "per_store_monthly_won": f.per_store_monthly_won,
            "store_count": int(f.store_count),
            "weekend_share_pct": round(f.weekend_share * 100, 1) if f.weekend_share is not None else None,
            "franchise_ratio_pct": round(f.franchise_ratio * 100, 1) if f.franchise_ratio is not None else None,
            "workers": int(f.workers) if f.workers is not None else None,
        }

    return {
        "subject": _row(subject, None),
        "analogs": [_row(f, sim) for f, sim in ranked],
        "quarter": quarter,
        "basis": "직장인 규모 · 점포 밀도 · 주말 비중 · 프랜차이즈 비율",
        "data_source": "서울시 상권분석서비스",
    }
