"""Trending street signals from quarterly store snapshots (segment-aggregated)."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

from app.config import settings
from app.services.seoul_commercial import (
    CategoryTrendSeries,
    StreetTrendSignal,
    fetch_category_trend_series,
    fetch_nearby_street_signals,
)


@dataclass
class TrendResult:
    category_label: str
    series: CategoryTrendSeries | None
    street_signals: list[StreetTrendSignal]
    data_scope: str
    warnings: list[str]


async def compute_trends(
    lat: float,
    lng: float,
    *,
    category_label: str | None = None,
    business_type: str = "cafe",
    inds_mcls_cd: str | None = None,
    inds_scls_cd: str | None = None,
    adm_name: str | None = None,
) -> TrendResult:
    """Trend block for dashboard — Seoul API first, DB snapshots when populated."""
    category_map = {
        "cafe": "카페·베이커리",
        "convenience": "편의점",
        "restaurant": "음식점",
        "clinic": "의료",
        "academy": "학원",
        "general": "전체 점포",
    }
    resolved_label = category_label or category_map.get(business_type, "전체 점포")
    warnings: list[str] = []
    gu_name = _extract_gu(adm_name)

    series: CategoryTrendSeries | None = None
    if settings.data_seoul_go_api_key.strip():
        try:
            series = await asyncio.wait_for(
                fetch_category_trend_series(category_label=resolved_label),
                timeout=2.5,
            )
        except TimeoutError:
            warnings.append("서울 상권 추이 API가 지연되어 이번 응답에서는 생략했습니다.")
        except Exception as exc:
            warnings.append(f"서울 상권 API: {exc}")
    else:
        warnings.append("DATA_SEOUL_GO_API_KEY 미설정 — 서울 쇼트컷 비활성")

    db_signals = await asyncio.to_thread(
        _signals_from_snapshots, lat, lng, business_type
    )
    if db_signals:
        street_signals = db_signals
        data_scope = "SBIZ 분기 스냅샷 (세그먼트 집계)"
    else:
        try:
            street_signals = await asyncio.wait_for(
                fetch_nearby_street_signals(
                    lat, lng, gu_name=gu_name, category_label=resolved_label
                ),
                timeout=2.5,
            )
        except TimeoutError:
            street_signals = []
            warnings.append("서울 주변 상권 API가 지연되어 이번 응답에서는 생략했습니다.")
        data_scope = street_signals[0].data_scope if street_signals else "데이터 없음"

    return TrendResult(
        category_label=resolved_label,
        series=series,
        street_signals=street_signals,
        data_scope=data_scope,
        warnings=warnings,
    )


def _extract_gu(adm_name: str | None) -> str | None:
    if not adm_name:
        return None
    parts = adm_name.split()
    for p in parts:
        if p.endswith("구"):
            return p
    return parts[0] if parts else None


def _signals_from_snapshots(lat: float, lng: float, business_type: str) -> list[StreetTrendSignal]:
    """Read segment trend signals from PostGIS when store_snapshots is populated."""
    try:
        import psycopg2
        import psycopg2.extras

        with psycopg2.connect(settings.database_url) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT EXISTS (
                      SELECT FROM information_schema.tables
                      WHERE table_name = 'store_snapshots'
                    ) AS exists
                    """
                )
                if not cur.fetchone()["exists"]:
                    return []
                cur.execute(
                    """
                    SELECT segment_osm_id, quarter,
                           SUM(CASE WHEN category_match THEN 1 ELSE 0 END) AS cat_cnt,
                           COUNT(*) AS total_cnt
                    FROM store_snapshots
                    WHERE segment_osm_id IN (
                      SELECT osm_id FROM roads_clean r
                      WHERE ST_DWithin(
                        r.geom_5179,
                        ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179),
                        400
                      )
                      LIMIT 30
                    )
                    GROUP BY segment_osm_id, quarter
                    ORDER BY quarter
                    """,
                    (lng, lat),
                )
                rows = cur.fetchall()
    except Exception:
        return []

    if not rows:
        return []

    # Group by segment, compare earliest vs latest quarter share
    from collections import defaultdict

    by_seg: dict[int, list] = defaultdict(list)
    for row in rows:
        by_seg[int(row["segment_osm_id"])].append(row)

    signals: list[StreetTrendSignal] = []
    for seg_id, qrows in by_seg.items():
        qrows.sort(key=lambda r: str(r["quarter"]))
        if len(qrows) < 2:
            continue
        start, end = qrows[0], qrows[-1]
        tot_start = int(start["total_cnt"] or 0)
        tot_end = int(end["total_cnt"] or 0)
        if tot_end < 20 or tot_start <= 0:
            continue
        share_start = int(start["cat_cnt"] or 0) / tot_start
        share_end = int(end["cat_cnt"] or 0) / tot_end
        growth = round(100.0 * (share_end - share_start) / max(share_start, 0.01), 1)
        label = "신호 강함" if growth >= 10 else "관찰 중"
        signals.append(
            StreetTrendSignal(
                name=f"세그먼트 {seg_id}",
                label=label,
                category_growth_pct=growth,
                store_growth_pct=growth,
                detail=f"업종 비중 {share_start:.1%}→{share_end:.1%}",
                data_scope="SBIZ 분기 스냅샷",
            )
        )
    signals.sort(key=lambda s: s.category_growth_pct or 0, reverse=True)
    return signals[:5]
