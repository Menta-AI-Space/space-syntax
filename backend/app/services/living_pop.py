"""Seoul 250m-grid living-population profiles from local PostGIS.

Tables are loaded by ``tools/ingest_living_pop.py`` (서울열린데이터광장
OA-22784 daily files). ``living_pop_slot`` holds weekday/weekend hourly
averages per grid; this service area-weights the grids intersecting the
analysis radius into one hourly presence curve.

All entry points degrade to ``None`` when the tables are missing or the
DB is unreachable — living population is an enrichment, never a hard
dependency of site scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.spatial_query import get_connection

# 심야: 밤 10시부터 다음날 6시 전까지
LATE_NIGHT_HOURS: tuple[int, ...] = (22, 23, 0, 1, 2, 3, 4, 5)
# 주말 낮 영업시간대 (미용·병원 등)
WEEKEND_PRIME_HOURS: tuple[int, ...] = (11, 12, 13, 14, 15, 16, 17, 18)
# 평일 출근시간대 (카페 테이크아웃)
MORNING_COMMUTE_HOURS: tuple[int, ...] = (7, 8)
# 평일 점심시간대 (식당 점심수요)
LUNCH_HOURS: tuple[int, ...] = (11, 12, 13)
# 평일 저녁시간대 (식당 저녁수요)
DINNER_HOURS: tuple[int, ...] = (17, 18, 19, 20)

_PROFILE_SQL = """
WITH buffer AS (
  SELECT ST_Buffer(
    ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179), %s
  ) AS geom
)
SELECT s.day_type,
       s.hour,
       SUM(s.avg_total * ST_Area(ST_Intersection(g.geom, b.geom)) / ST_Area(g.geom)),
       MAX(s.sample_days)
FROM living_pop_grid g
JOIN living_pop_slot s USING (grid_code)
CROSS JOIN buffer b
WHERE ST_Intersects(g.geom, b.geom)
GROUP BY s.day_type, s.hour
"""


@dataclass
class LivingPopProfile:
    weekday_hourly: dict[int, float] = field(default_factory=dict)
    weekend_hourly: dict[int, float] = field(default_factory=dict)
    weekday_sample_days: int = 0
    weekend_sample_days: int = 0


# 단기체류 외국인 (OA-22786, tools/ingest_living_pop_foreign.py) — 별도 테이블
_FOREIGN_PROFILE_SQL = _PROFILE_SQL.replace(
    "living_pop_slot", "living_pop_foreign_slot"
)


def _fetch_profile(sql: str, lat: float, lng: float, radius_m: int) -> LivingPopProfile | None:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (lng, lat, radius_m))
                rows = cur.fetchall()
    except Exception:
        return None
    if not rows:
        return None
    profile = LivingPopProfile()
    for day_type, hour, total, sample_days in rows:
        if day_type == "weekend":
            profile.weekend_hourly[int(hour)] = float(total)
            profile.weekend_sample_days = max(
                profile.weekend_sample_days, int(sample_days)
            )
        else:
            profile.weekday_hourly[int(hour)] = float(total)
            profile.weekday_sample_days = max(
                profile.weekday_sample_days, int(sample_days)
            )
    return profile


def fetch_living_pop_profile(
    lat: float, lng: float, radius_m: int
) -> LivingPopProfile | None:
    """Hourly weekday/weekend presence within the radius, or None if unavailable."""
    return _fetch_profile(_PROFILE_SQL, lat, lng, radius_m)


def fetch_living_pop_foreign_profile(
    lat: float, lng: float, radius_m: int
) -> LivingPopProfile | None:
    """단기체류 외국인 hourly presence — same shape, separate table."""
    return _fetch_profile(_FOREIGN_PROFILE_SQL, lat, lng, radius_m)


def combined_with_foreign(
    domestic: LivingPopProfile | None, foreign: LivingPopProfile | None
) -> tuple[LivingPopProfile | None, float | None]:
    """내국인+외국인 체류 합산 프로파일과 전체 대비 외국인 비중(%).

    총 체류 수요 = 내국인 + 단기체류 외국인. 비중은 관광상권 여부를
    설명하는 주석용 지표 (양쪽 시간대가 모두 있을 때만 계산).
    """
    if domestic is None:
        return None, None
    if foreign is None:
        return domestic, None
    combined = LivingPopProfile(
        weekday_sample_days=domestic.weekday_sample_days,
        weekend_sample_days=domestic.weekend_sample_days,
    )
    dom_sum = 0.0
    forn_sum = 0.0
    for target, dom_hourly, forn_hourly in (
        (combined.weekday_hourly, domestic.weekday_hourly, foreign.weekday_hourly),
        (combined.weekend_hourly, domestic.weekend_hourly, foreign.weekend_hourly),
    ):
        for hour, value in dom_hourly.items():
            extra = forn_hourly.get(hour, 0.0)
            target[hour] = value + extra
            dom_sum += value
            forn_sum += extra
    total = dom_sum + forn_sum
    share = round(100.0 * forn_sum / total, 1) if total > 0 else None
    return combined, share


def slot_average(hourly: dict[int, float], hours: tuple[int, ...]) -> float | None:
    values = [hourly[h] for h in hours if h in hourly]
    if not values:
        return None
    return sum(values) / len(values)


def late_night_average(profile: LivingPopProfile) -> float | None:
    """All-days 심야 평균 (weekday/weekend 표본가중 없이 단순 평균)."""
    parts = [
        v
        for hourly in (profile.weekday_hourly, profile.weekend_hourly)
        for v in ([slot_average(hourly, LATE_NIGHT_HOURS)] if hourly else [])
        if v is not None
    ]
    if not parts:
        return None
    return sum(parts) / len(parts)


def weekend_prime_average(profile: LivingPopProfile) -> float | None:
    if not profile.weekend_hourly:
        return None
    return slot_average(profile.weekend_hourly, WEEKEND_PRIME_HOURS)


def morning_commute_average(profile: LivingPopProfile) -> float | None:
    """평일 출근시간(7–9시) 평균 체류 인구."""
    return slot_average(profile.weekday_hourly, MORNING_COMMUTE_HOURS)


def lunch_average(profile: LivingPopProfile) -> float | None:
    """평일 점심(11–14시) 평균 체류 인구."""
    return slot_average(profile.weekday_hourly, LUNCH_HOURS)


def dinner_average(profile: LivingPopProfile) -> float | None:
    """평일 저녁(17–21시) 평균 체류 인구."""
    return slot_average(profile.weekday_hourly, DINNER_HOURS)
