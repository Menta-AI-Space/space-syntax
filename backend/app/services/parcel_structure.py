"""필지별 「길 구조 등급」 — 땅을 나눠 값을 매길 때 쓰는 상대 배분 근거.

상권 축(길 구조 ↔ 매출)은 서울 1,648개 상권에서 검증됐다
(`docs/VALIDATION_REPORT_2026-09.md`: 구조 5분위 매출 16억 → 54억). 그 검증이
붙어 있는 값을 **필지 단위로 내려주는** 층이 여기다. 새 지표를 만들지 않고,
이미 계산해 둔 `road_segment_structure`(170,707구간, 반경 500m)를 필지가 접한
도로에서 끌어온다.

핵심 판단 두 가지:

- **접한 도로 중 가장 좋은 값을 쓴다(max).** 코너 필지가 큰길과 골목에 동시에
  접하면 사람은 큰길로 인식한다. 평균을 쓰면 골목이 큰길을 깎아내려 코너의
  이점이 사라진다. 실측으로도 최대값이 이겼다(아래).
- **점수는 `structure_pct_in_tile`(타일 안 백분위)을 쓴다.** 강남 논현 1,066필지를
  2026년 공시지가와 대조한 결과(2026-09-30):

  | 집계 방식 | 구조 5분위별 공시지가 중앙값 (만원/㎡) | 최고/최저 |
  | --- | --- | --- |
  | 타일 백분위 · 최대 | 1094 → 1163 → 1194 → 1176 → **2085** | **1.91배** |
  | 원값 · 최대 | 1094 → 1268 → 1145 → 1176 → 1735 | 1.59배 |
  | 가장 가까운 도로 | 1098 → 1119 → 1267 → 1219 → 1229 | 1.12배 |
  | 거리가중 평균 | 1119 → 1157 → 1222 → 1218 → 1251 | 1.12배 |

  ⚠️ **읽는 법**: 갈리는 건 **맨 위 한 칸**이다. 2~4분위는 서로 구분되지 않는다.
  즉 이 지표는 "특급 자리인가 아닌가"를 가르지 세밀한 등급을 매기지 못한다.
  화면과 보고서가 그 이상을 말하면 안 된다.
  ⚠️ 공시지가에는 **순환성**이 있다(CLAUDE.md §4) — 비준표에 도로접면·도로거리가
  들어 있어 길 구조와 겹친다. 위 1.91배는 "우리 지표가 옳다"는 증거가 아니라
  **"행정이 매긴 값과 같은 방향을 본다"**는 정도의 뜻이다. 매출 검증은 상권 축
  (`VALIDATION_REPORT_2026-09.md`)에 있고, 필지 단위 매출 검증은 아직 없다.
- **등급은 언제나 「요청한 범위 안에서의 상대값」이다.** 구조값 자체는 반경
  500m 안의 상대값이라 서울 전역을 가로질러 비교할 수 없다. 그래서 이 모듈은
  절대 등급을 말하지 않고 *이 블록/이 지구 안에서 몇 등*인지만 말한다 —
  분양가 배분이 원래 그런 문제이기도 하다(총액은 정해져 있고 나누는 일).

⚠️ 감정평가가 아니다. 결론 문구는 "얼마짜리다"가 아니라 **"같은 범위의 다른
필지에 견줘 길 구조가 몇 등급"**이고, 금액은 사용자가 총액을 넣었을 때의
**배분 비율**로만 나간다 (CLAUDE.md §4의 표기 원칙).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.spatial_query import get_connection

logger = logging.getLogger(__name__)

# 필지 경계에서 이 거리 안의 도로를 "접한 도로"로 본다. 서울 필지는 도로에
# 바짝 붙어 있고, 20m를 넘기면 뒤쪽 필지가 앞 큰길의 값을 훔쳐 온다.
FRONTAGE_M = 20.0
# 한 번에 다룰 필지 수 상한 — 5초 룰. 실측 2,410필지 0.8초.
MAX_PARCELS = 4000
# 등급 경계(백분위). 5등급은 감정평가·분양에서 익숙한 단위다.
GRADE_CUTS = (80.0, 60.0, 40.0, 20.0)


@dataclass
class ParcelStructure:
    pnu: str
    area_sqm: float
    # 접한 도로 중 가장 좋은 구조 백분위 0~100 (없으면 None — 맹지이거나 데이터 밖)
    structure: float | None
    integration: float | None
    choice: float | None
    # 가장 가까운 도로까지 거리(m)와 접한 도로 수. 수가 많으면 코너다.
    road_distance_m: float | None
    frontage_count: int
    # 같은 도로의 원값. 화면엔 안 쓰고 근거 확인용으로만 남긴다.
    structure_raw: float | None = None
    # 요청 범위 안에서의 백분위(0~100)와 1~5등급. 도로가 없으면 None.
    percentile: float | None = None
    grade: int | None = None
    # 총액을 넣었을 때 이 필지에 배분되는 비율(합이 1). 면적 × 등급 가중.
    allocation_share: float | None = None
    lat: float | None = None
    lng: float | None = None
    # 지도에 칠할 경계 (GeoJSON, WGS84). 소수 6자리면 ~0.1m — 필지 경계에 충분하고
    # 그 이상은 응답만 키운다.
    geometry: dict | None = None


def _percentiles(values: list[float]) -> dict[float, float]:
    """값 → 백분위. 동점은 평균 순위로 처리한다."""
    ordered = sorted(values)
    n = len(ordered)
    out: dict[float, float] = {}
    for value in set(ordered):
        lower = sum(1 for v in ordered if v < value)
        equal = sum(1 for v in ordered if v == value)
        rank = lower + (equal - 1) / 2.0
        out[value] = round(100.0 * rank / (n - 1), 1) if n > 1 else 50.0
    return out


def _grade(percentile: float) -> int:
    for i, cut in enumerate(GRADE_CUTS):
        if percentile >= cut:
            return i + 1
    return len(GRADE_CUTS) + 1


# 필지를 고르는 두 가지 방식이 같은 집계 SQL을 쓰도록 몸통만 떼어 둔다. 반경으로
# 훑는 것과 지번코드로 집는 것이 다른 계산을 하면, 화면에서 대상을 좁혔을 때
# 등급이 미묘하게 달라져 사람이 그 차이를 설명할 수 없다.
_AGGREGATE_SQL = """
    SELECT t.pnu,
           t.area_sqm,
           MAX(r.structure_pct_in_tile) AS structure,
           MAX(r.structure_raw)   AS structure_raw,
           MAX(r.integration_raw) AS integration,
           MAX(r.choice_raw)      AS choice,
           MIN(ST_Distance(t.geom_5179, r.geom_5179)) AS road_m,
           COUNT(r.*)             AS frontage,
           ST_Y(ST_Transform(ST_Centroid(t.geom_5179), 4326)) AS lat,
           ST_X(ST_Transform(ST_Centroid(t.geom_5179), 4326)) AS lng,
           ST_AsGeoJSON(ST_Transform(t.geom_5179, 4326), 6)::json AS geometry
    FROM target t
    LEFT JOIN LATERAL (
      SELECT s.structure_pct_in_tile, s.structure_raw, s.integration_raw, s.choice_raw, s.geom_5179
      FROM road_segment_structure s
      WHERE ST_DWithin(s.geom_5179, t.geom_5179, %(frontage)s)
    ) r ON TRUE
    GROUP BY t.pnu, t.area_sqm, t.geom_5179
"""


def _rows_to_parcels(rows) -> list[ParcelStructure]:
    return [
        ParcelStructure(
            pnu=row[0],
            area_sqm=float(row[1] or 0.0),
            structure=float(row[2]) if row[2] is not None else None,
            structure_raw=float(row[3]) if row[3] is not None else None,
            integration=float(row[4]) if row[4] is not None else None,
            choice=float(row[5]) if row[5] is not None else None,
            road_distance_m=float(row[6]) if row[6] is not None else None,
            frontage_count=int(row[7] or 0),
            lat=float(row[8]) if row[8] is not None else None,
            lng=float(row[9]) if row[9] is not None else None,
            geometry=row[10],
        )
        for row in rows
    ]


def fetch_parcel_structures_by_pnu(pnus: list[str]) -> list[ParcelStructure]:
    """지번코드로 집어 온다 — 화면에서 "이 필지들이 내 사업지"라고 고른 경우.

    반경 조회와 달리 **대상이 움직이지 않는다.** 지도를 옮겨도 같은 묶음이므로
    등급이 흔들리지 않고, 그래야 보고서에 쓸 수 있다.
    """
    if not pnus:
        return []
    sql = """
    WITH target AS (
      SELECT p.pnu, p.geom_5179, p.area_sqm
      FROM parcel_polygons p
      WHERE p.pnu = ANY(%(pnus)s)
      LIMIT %(limit)s
    )""" + _AGGREGATE_SQL
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"pnus": pnus[:MAX_PARCELS], "limit": MAX_PARCELS, "frontage": FRONTAGE_M})
        return _rows_to_parcels(cur.fetchall())


def fetch_parcel_structures(
    lng: float, lat: float, radius_m: int = 400, limit: int = MAX_PARCELS
) -> list[ParcelStructure]:
    """WGS84 좌표 주변 필지를 읽어 접한 도로의 구조값을 붙인다."""
    sql = """
    WITH centre AS (
      SELECT ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), 5179) AS g
    ), target AS (
      SELECT p.pnu, p.geom_5179, p.area_sqm
      FROM parcel_polygons p, centre c
      WHERE ST_DWithin(p.geom_5179, c.g, %(radius)s)
      ORDER BY ST_Distance(p.geom_5179, c.g)
      LIMIT %(limit)s
    )""" + _AGGREGATE_SQL
    params = {
        "lng": lng, "lat": lat,
        "radius": radius_m,
        "limit": min(limit, MAX_PARCELS),
        "frontage": FRONTAGE_M,
    }
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return _rows_to_parcels(rows)


def grade_parcels(
    parcels: list[ParcelStructure], total_amount: float | None = None
) -> list[ParcelStructure]:
    """요청 범위 안에서 등급을 매기고, 총액이 있으면 배분 비율을 낸다.

    배분은 **면적 × 구조 가중**이다. 구조 가중은 평균 1이 되도록 정규화한
    구조값이라, 구조를 무시하면(모두 같은 값) 순수 면적 비례로 수렴한다 —
    기존 관행(면적 비례)에서 얼마나 움직이는지가 눈에 보이도록 한 것이다.
    """
    scored = [p for p in parcels if p.structure is not None]
    if not scored:
        return parcels

    table = _percentiles([p.structure for p in scored])  # type: ignore[misc]
    for parcel in scored:
        parcel.percentile = table[parcel.structure]  # type: ignore[index]
        parcel.grade = _grade(parcel.percentile)

    mean_structure = sum(p.structure for p in scored) / len(scored)  # type: ignore[misc]
    if mean_structure > 0:
        weights = [
            (p, p.area_sqm * (p.structure / mean_structure))  # type: ignore[operator]
            for p in scored
        ]
        total_weight = sum(w for _, w in weights)
        if total_weight > 0:
            for parcel, weight in weights:
                parcel.allocation_share = weight / total_weight
    return parcels


def summarize(parcels: list[ParcelStructure]) -> dict:
    scored = [p for p in parcels if p.structure is not None]
    landlocked = [p for p in parcels if p.structure is None]
    if not scored:
        return {
            "parcel_count": len(parcels),
            "graded_count": 0,
            "landlocked_count": len(landlocked),
            "structure_spread": 0.0,
            "corner_count": 0,
        }
    values = sorted(p.structure for p in scored)  # type: ignore[misc]
    top = values[-max(1, len(values) // 10)]
    bottom = values[max(0, len(values) // 10 - 1)]
    return {
        "parcel_count": len(parcels),
        "graded_count": len(scored),
        "landlocked_count": len(landlocked),
        # 상위 10% / 하위 10% 배. 1에 가까우면 이 블록은 구조가 고르다는 뜻이고,
        # 그때는 등급으로 값을 가르는 근거가 약하다는 것을 화면이 말해야 한다.
        "structure_spread": round(top / bottom, 2) if bottom > 0 else 0.0,
        "corner_count": sum(1 for p in scored if p.frontage_count >= 4),
    }
