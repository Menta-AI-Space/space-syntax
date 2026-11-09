"""시군구 EV 충전 수요 (환경부 충전건별 합성데이터 2024 집계).

ev_charging_demand는 시군구×충전소유형 집계 테이블 (12M 세션 → 2,290행,
tools/ingest_ev_charging_demand.py). 합성데이터 특성상 충전기ID가 세션마다
거의 유니크해 '충전기당' 지표는 무의미 — 시군구 총량 분포만 신뢰하고
전국 백분위로 정규화해 쓴다. 분석 지점→시군구는 sgis_dong_boundary
(adm_nm '서울특별시 중구 황학동' 형식) point-in-polygon으로 해석.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.spatial_query import get_connection


@dataclass
class EvDemand:
    sido: str
    sgg: str
    total_kwh: float
    sessions: int
    demand_pct: float  # 전국 시군구 중 상위 백분위 (100 = 최고 수요)


def ev_demand_for_point(lat: float, lng: float) -> EvDemand | None:
    """지점의 시군구 연간 충전량 + 전국 백분위. 데이터/경계 없으면 None."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT adm_nm FROM sgis_dong_boundary
                    WHERE ST_Contains(
                      geom,
                      ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179)
                    )
                    LIMIT 1
                    """,
                    (lng, lat),
                )
                row = cur.fetchone()
                if not row:
                    return None
                parts = row[0].split()
                if len(parts) < 2:
                    return None
                sido, sgg = parts[0], parts[1]
                cur.execute(
                    """
                    WITH sgg_totals AS (
                      SELECT sido, sgg, sum(total_kwh) AS kwh, sum(sessions) AS cnt
                      FROM ev_charging_demand GROUP BY sido, sgg
                    ),
                    ranked AS (
                      SELECT *, percent_rank() OVER (ORDER BY kwh) AS pct
                      FROM sgg_totals
                    )
                    SELECT kwh, cnt, pct FROM ranked WHERE sido = %s AND sgg = %s
                    """,
                    (sido, sgg),
                )
                stats = cur.fetchone()
    except Exception:
        return None
    if not stats:
        return None
    return EvDemand(
        sido=sido,
        sgg=sgg,
        total_kwh=float(stats[0]),
        sessions=int(stats[1]),
        demand_pct=round(float(stats[2]) * 100.0, 1),
    )
