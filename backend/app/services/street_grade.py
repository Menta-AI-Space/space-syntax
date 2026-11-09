"""지도 분석 — 서울 길을 「목 좋은 길」 3단계로 칠한다 (2026-10-26).

새 지표를 만들지 않는다. 이미 계산해 둔 `road_segment_structure`(170,707구간)의
`choice_raw`를 읽기만 한다. 이 열은 이름과 달리 원값이 아니라 **NACH**(정규화
Choice)이고 depthmapX로 계산했다(`tools/compute_segment_structure.py` 머리말). NACH는
크기가 다른 망끼리 비교하도록 만든 지표라 **서울 전역 백분위**로 줄 세운다 — 상권
매출 검증(상권 1,648개, 구조 5분위 분기 매출 16억 → 54억, `VALIDATION_REPORT_2026-09.md`)도
같은 열을 서울 전역으로 비교했다.

**왜 3단계뿐인가.** 강남 논현 1,066필지를 공시지가와 대조하면 맨 위 칸만 튀고
(1.9배) 2~4분위는 구분되지 않았다. 상권 매출은 5분위가 순서대로 올라가지만 상관이
+0.24로 약하다. 그래서 상위 10%만 확실히 칠하고, 중간(10~50%)은 옅게, 하위 50%는
회색으로 둔다 — 중간과 하위의 차이는 작다고 화면이 함께 말한다.

⚠️ 측정 창은 반경 500m가 아니라 타일 부분그래프(≈1.8km 창) 안의 radius-n이다.
서울 전역 위상구조가 아니다.
"""

from __future__ import annotations

import bisect
import logging
import math
import threading
from dataclasses import dataclass

from app.services.spatial_query import get_connection

logger = logging.getLogger(__name__)

# 서울 전역 백분위 경계. 상위 10% → 1등급, 상위 50%까지 → 2등급, 나머지 3등급.
TOP_PCT = 90.0
MID_PCT = 50.0
# 화면의 긴 변이 이보다 크면(서울을 멀리서 볼 때) 상위 10%만 보낸다. 실측: 서울 대부분을
# 덮는 화면은 7.8만 구간·13MB라 그대로는 못 보낸다. 강남역 한 화면(1.5km)은 600구간·97KB.
OVERVIEW_SPAN_M = 4000.0
# 한 응답의 구간 수 상한. 넘치면 좋은 길부터 자른다(못 그린 건 회색 길이다).
MAX_SEGMENTS = 20000

_QUANTILE_SQL = """
    SELECT percentile_cont(%(qs)s::float8[]) WITHIN GROUP (ORDER BY choice_raw)
    FROM road_segment_structure
    WHERE choice_raw IS NOT NULL
"""

_SEGMENTS_SQL = """
    SELECT s.choice_raw,
           s.highway,
           ST_AsGeoJSON(ST_Transform(ST_Simplify(s.geom_5179, %(simplify)s), 4326), 5)::json
    FROM road_segment_structure s
    WHERE s.geom_5179 && ST_Transform(ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326), 5179)
      AND s.choice_raw IS NOT NULL
      AND s.choice_raw >= %(min_choice)s
    ORDER BY s.choice_raw DESC
    LIMIT %(limit)s
"""

_quantiles: list[float] | None = None
_lock = threading.Lock()


@dataclass
class StreetSegment:
    grade: int  # 1 = 상위 10%, 2 = 10~50%, 3 = 하위 50%
    top_pct: int  # 서울 길 구간 중 상위 몇 %인가 (1~100)
    highway: str | None
    # [[lat, lng], ...] — 카카오 지도가 바로 쓰는 순서
    path: list[list[float]]


def load_quantiles() -> list[float]:
    """서울 전역 NACH의 1~99 백분위 경계 99개. 처음 한 번만 읽고(실측 0.9초) 캐시한다."""
    global _quantiles
    with _lock:
        if _quantiles is None:
            qs = [i / 100.0 for i in range(1, 100)]
            with get_connection() as conn, conn.cursor() as cur:
                cur.execute(_QUANTILE_SQL, {"qs": qs})
                _quantiles = [float(v) for v in cur.fetchone()[0]]
        return _quantiles


def percentile_of(value: float, quantiles: list[float]) -> int:
    """값이 서울 전역에서 몇 백분위인지(0~99). 경계 99개 중 몇 개 이상인가."""
    return bisect.bisect_right(quantiles, value)


def top_pct_of(percentile: int) -> int:
    """백분위 → 「상위 몇 %」. 맨 위도 0%가 아니라 1%로 말한다."""
    return max(1, 100 - percentile)


def grade_of(percentile: float) -> int:
    if percentile >= TOP_PCT:
        return 1
    if percentile >= MID_PCT:
        return 2
    return 3


def bbox_span_m(w: float, s: float, e: float, n: float) -> float:
    """화면 긴 변의 대략 길이(m). 서울 위도에서 이 정도 근사면 충분하다."""
    mid_lat = math.radians((s + n) / 2.0)
    width = abs(e - w) * 111_320.0 * math.cos(mid_lat)
    height = abs(n - s) * 110_574.0
    return max(width, height)


def _lines(geometry: dict) -> list[list[list[float]]]:
    """GeoJSON(LineString/MultiLineString) → [[lat, lng], ...] 선들."""
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "LineString":
        parts = [coords]
    elif kind == "MultiLineString":
        parts = coords
    else:
        return []
    return [[[round(y, 5), round(x, 5)] for x, y in part] for part in parts if len(part) >= 2]


def fetch_street_grades(w: float, s: float, e: float, n: float) -> tuple[bool, list[StreetSegment]]:
    """화면 범위의 길을 등급과 함께 돌려준다. (멀리서 본 화면인가, 구간들)."""
    quantiles = load_quantiles()
    overview = bbox_span_m(w, s, e, n) > OVERVIEW_SPAN_M
    # 상위 10% 경계값 = 90번째 백분위 경계(인덱스 89).
    min_choice = quantiles[int(TOP_PCT) - 1] if overview else -1e9
    params = {
        "w": w, "s": s, "e": e, "n": n,
        "min_choice": min_choice,
        "limit": MAX_SEGMENTS,
        # 멀리서 볼 땐 8m로 단순화해 응답을 줄인다. 가까이선 1m(사실상 원형).
        "simplify": 8.0 if overview else 1.0,
    }
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(_SEGMENTS_SQL, params)
        rows = cur.fetchall()

    segments: list[StreetSegment] = []
    for choice, highway, geometry in rows:
        pct = percentile_of(float(choice), quantiles)
        grade = grade_of(pct)
        top = top_pct_of(pct)
        for path in _lines(geometry or {}):
            segments.append(StreetSegment(grade=grade, top_pct=top, highway=highway, path=path))
    return overview, segments
