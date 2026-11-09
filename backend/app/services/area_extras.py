"""상담 모델에 넘기는 「적재만 해 두고 안 쓰던 데이터」 요약.

2026-11-06 소유자 지적: "DB에 유동·거주·외국인 말고도 이것저것 많이 넣어 뒀는데 왜 답은
길 구조 얘기뿐이냐." 실사해 보니 아래 표들은 적재 스크립트는 있는데 서비스 코드가 한
번도 읽지 않았다(또는 일부만 읽었다).

| 표 | 무엇을 말할 수 있나 |
| --- | --- |
| subway_ridership | 가까운 역이 하루 몇 명이 타고 내리는 역인가 (종전엔 역 이름·거리만) |
| golmok_change | 행정동 상권변화지표 — 확장/축소/정체/다이나믹, 영업·폐업 가게의 평균 영업기간 |
| golmok_facility | 행정동 집객시설 — 역·학교·병원·관공서·백화점 수 |
| biz_licenses | 반경 안 인허가 업소의 영업·개업·폐업 — 이 자리 가게가 얼마나 버티나 |
| reb_rent_stats | 부동산원 상권의 공실률·임대료 (월세 판정에서만 쓰이던 것) |
| large_stores | 반경 안 대규모점포(마트·백화점·복합몰) — 사람을 끌어오는 앵커 |
| building_permits | 반경 안 최근 건축허가 — 앞으로 들어설 건물(수요 변화) |
| redevelopment_zones | 이 자리가 정비구역 안·근처인가 — 임차·영업 지속 위험 |

**원칙**
- 섹션마다 따로 실패한다. 운영 DB에 표가 없거나(로컬에만 적재) 질의가 틀려도 그 줄만
  빠지고 상담은 그대로 간다. 각 질의에 statement_timeout을 건다(상담 지연 상한).
- 사실만 낸다. 해석(「뜨는 자리」, 「싸다」)은 모델이 하되 지시문 원칙을 따른다.
- 공시지가·토지특성은 넣지 않는다 — 토지가치 탭을 보류했고(CLAUDE.md §4), 상담에서
  땅값을 판정처럼 말하면 감정평가법상 위험하다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# 질의 하나가 상담을 붙잡는 상한(ms). 넘으면 그 줄만 빠진다.
STATEMENT_TIMEOUT_MS = 2500
# 대규모점포·정비구역은 반경과 무관하게 이 거리까지 본다.
ANCHOR_RADIUS_M = 1000
ZONE_RADIUS_M = 300

_POINT = "ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), 5179)"


def _connect():
    from app.services.spatial_query import get_connection

    return get_connection()


def _years(days: float | None) -> str:
    if days is None:
        return ""
    return f"{days / 365:.1f}년"


def _label(key: str) -> str:
    """golmok detail 키(한글 컬럼명)를 사람이 읽을 이름으로."""
    text = key.replace("_", " ").strip()
    text = re.sub(r"\s*수$", "", text)
    return text


# ───────────────────────────── 섹션별 질의 ─────────────────────────────


def _subway(*, station_name: str | None, **_: Any) -> list[str]:
    if not station_name:
        return []
    from app.services.transit import fetch_station_scale

    scale = fetch_station_scale(station_name)
    if scale is None or not scale.daily_total:
        return []
    lines = f", {scale.line_count}개 노선 환승역" if scale.line_count and scale.line_count > 1 else ""
    return [f"가까운 역 규모: {scale.station_nm} 하루 승하차 약 {scale.daily_total:,}명{lines}"]


def _dong_code(lat: float, lng: float) -> tuple[str, str] | None:
    from app.services.trdar_sales_local import dong_for_point

    match = dong_for_point(lat, lng)
    if match is None:
        return None
    return match.trdar_cd, match.trdar_nm


def _change(cur, *, dong: tuple[str, str] | None, **_: Any) -> list[str]:
    if dong is None:
        return []
    cur.execute(
        """
        SELECT stdr_yyqu_cd, change_ix_nm, detail
        FROM golmok_change
        WHERE region_type = 'dong' AND region_code = %(code)s
        ORDER BY stdr_yyqu_cd DESC
        LIMIT 1
        """,
        {"code": dong[0]},
    )
    row = cur.fetchone()
    if not row or not row[1]:
        return []
    quarter, name, detail = row
    months = [
        f"{_label(k)} {float(v):.0f}개월"
        for k, v in sorted((detail or {}).items())
        if "개월" in k and _is_number(v)
    ][:4]
    tail = f" — {', '.join(months)}" if months else ""
    return [f"상권변화지표({dong[1]}, {quarter}): {name}{tail}"]


def _facility(cur, *, dong: tuple[str, str] | None, **_: Any) -> list[str]:
    if dong is None:
        return []
    cur.execute(
        """
        SELECT stdr_yyqu_cd, detail
        FROM golmok_facility
        WHERE region_type = 'dong' AND region_code = %(code)s
        ORDER BY stdr_yyqu_cd DESC
        LIMIT 1
        """,
        {"code": dong[0]},
    )
    row = cur.fetchone()
    if not row:
        return []
    quarter, detail = row
    counts = [
        (k, int(float(v)))
        for k, v in (detail or {}).items()
        if k.endswith("_수") and _is_number(v) and float(v) > 0 and not k.startswith("집객시설")
    ]
    total = next(
        (int(float(v)) for k, v in (detail or {}).items() if k.startswith("집객시설") and _is_number(v)), None
    )
    if not counts and total is None:
        return []
    counts.sort(key=lambda item: -item[1])
    parts = [f"{_label(k)} {n}" for k, n in counts[:8]]
    head = f"총 {total}곳 — " if total is not None else ""
    return [f"집객시설({dong[1]}, {quarter}): {head}{', '.join(parts)}"]


def _licenses(cur, *, lat: float, lng: float, radius_m: int, **_: Any) -> list[str]:
    """반경 안 인허가 업소 — 업종별 영업 중·최근 3년 개업·폐업, 폐업 가게의 영업기간."""
    cur.execute(
        f"""
        WITH q AS (SELECT {_POINT} AS g)
        SELECT b.biz_type,
               count(*) FILTER (WHERE b.close_date IS NULL AND coalesce(b.status, '') LIKE '영업%%') AS open_now,
               count(*) FILTER (WHERE b.license_date >= current_date - interval '3 years') AS opened_3y,
               count(*) FILTER (WHERE b.close_date >= current_date - interval '3 years') AS closed_3y,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.close_date - b.license_date))
                   FILTER (WHERE b.close_date >= current_date - interval '5 years'
                             AND b.license_date IS NOT NULL
                             AND b.close_date >= b.license_date) AS median_life_days
        FROM biz_licenses b, q
        WHERE ST_DWithin(b.geom_5179, q.g, %(radius)s)
        GROUP BY b.biz_type
        ORDER BY open_now DESC
        LIMIT 4
        """,
        {"lat": lat, "lng": lng, "radius": radius_m},
    )
    lines: list[str] = []
    for biz_type, open_now, opened, closed, life in cur.fetchall():
        if not open_now and not closed:
            continue
        life_txt = f" · 최근 5년 폐업 가게의 영업기간 중앙값 {_years(life)}" if life else ""
        lines.append(
            f"인허가 기준 반경 {radius_m}m {biz_type}: 영업 중 {open_now}곳, "
            f"최근 3년 개업 {opened}·폐업 {closed}{life_txt}"
        )
    if lines:
        lines.append("(인허가 개폐업은 데이터를 적재한 시점까지이며, 적재한 업종만 있다)")
    return lines


def _vacancy(*, lat: float, lng: float, **_: Any) -> list[str]:
    from app.services.reb_rent import fetch_rent_reference

    ref = fetch_rent_reference(lat, lng, building_type="일반")
    if not ref.matched or not ref.types:
        return []
    parts = []
    for t in ref.types:
        bits = []
        if t.vacancy_pct is not None:
            bits.append(f"공실률 {t.vacancy_pct:.1f}%")
        if t.rent_krw_per_sqm:
            bits.append(f"㎡당 월 임대료 약 {t.rent_krw_per_sqm:,.0f}원")
        if bits:
            parts.append(f"{t.property_type} {' · '.join(bits)}")
    if not parts:
        return []
    return [f"부동산원 상권 {ref.sanggwon_nm}({ref.quarter}): " + "; ".join(parts) + " — 상권 평균이며 개별 건물 값이 아니다"]


def _anchors(cur, *, lat: float, lng: float, **_: Any) -> list[str]:
    cur.execute(
        f"""
        WITH q AS (SELECT {_POINT} AS g)
        SELECT s.store_nm, s.store_type, round(ST_Distance(s.geom_5179, q.g)) AS dist
        FROM large_stores s, q
        WHERE ST_DWithin(s.geom_5179, q.g, %(radius)s)
          AND s.close_date IS NULL
          AND coalesce(s.biz_status, '영업') LIKE '영업%%'
        ORDER BY dist
        LIMIT 5
        """,
        {"lat": lat, "lng": lng, "radius": ANCHOR_RADIUS_M},
    )
    rows = cur.fetchall()
    if not rows:
        return [f"반경 {ANCHOR_RADIUS_M}m 안 대규모점포(마트·백화점 등): 없음"]
    parts = [f"{nm}({kind or '대규모점포'}, {int(dist)}m)" for nm, kind, dist in rows]
    return [f"반경 {ANCHOR_RADIUS_M}m 안 대규모점포: " + ", ".join(parts)]


def _permits(cur, *, lat: float, lng: float, radius_m: int, **_: Any) -> list[str]:
    cur.execute(
        f"""
        WITH q AS (SELECT {_POINT} AS g),
        hits AS (
            SELECT p.main_purps_nm, p.tot_area, p.permit_date, p.bld_nm
            FROM building_permits p, q
            WHERE ST_DWithin(p.geom_5179, q.g, %(radius)s)
              AND p.permit_date >= current_date - interval '2 years'
        )
        SELECT (SELECT count(*) FROM hits),
               (SELECT coalesce(sum(tot_area), 0) FROM hits),
               (SELECT json_agg(t) FROM (
                    SELECT main_purps_nm, round(tot_area) AS area, permit_date, bld_nm
                    FROM hits WHERE tot_area IS NOT NULL
                    ORDER BY tot_area DESC LIMIT 3) t)
        """,
        {"lat": lat, "lng": lng, "radius": radius_m},
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return []
    count, total, top = row
    big = [
        f"{(t.get('main_purps_nm') or '용도 미상')} {int(t.get('area') or 0):,}㎡({str(t.get('permit_date') or '')[:7]})"
        for t in (top or [])
    ]
    tail = f" — 큰 것: {', '.join(big)}" if big else ""
    return [
        f"반경 {radius_m}m 최근 2년 건축허가 {count}건(연면적 합 {float(total):,.0f}㎡){tail}"
        " — 허가이며 준공·입주 시점은 다르다"
    ]


def _zones(cur, *, lat: float, lng: float, **_: Any) -> list[str]:
    cur.execute(
        f"""
        WITH q AS (SELECT {_POINT} AS g)
        SELECT z.zone_name, z.zone_class, ST_Contains(z.geom_5179, q.g) AS inside,
               round(ST_Distance(z.geom_5179, q.g)) AS dist
        FROM redevelopment_zones z, q
        WHERE ST_DWithin(z.geom_5179, q.g, %(radius)s)
          -- 2026-11-06: 토지구획정리사업은 정비사업이 아니고 대개 수십 년 전에
          -- 끝났다. 망원동을 「성산토지구획정리사업 구역 안」이라고 경고했다(§9-2).
          AND coalesce(z.zone_name, '') NOT LIKE '%%구획정리%%'
          AND coalesce(z.zone_class, '') NOT LIKE '%%구획정리%%'
          AND coalesce(z.zone_class_l, '') NOT LIKE '%%구획정리%%'
        ORDER BY dist
        LIMIT 3
        """,
        {"lat": lat, "lng": lng, "radius": ZONE_RADIUS_M},
    )
    rows = cur.fetchall()
    if not rows:
        return []
    lines = []
    for name, kind, inside, dist in rows:
        kind_txt = f"({kind})" if kind else ""
        if inside:
            lines.append(
                f"정비구역: 이 자리가 {name}{kind_txt} 구역 안이다(지정 고시 기준, 사업 단계는 "
                "이 데이터로 모른다) — 사업이 진행 중이면 철거·이주로 임차·영업이 끝날 수 있다"
            )
        else:
            lines.append(f"정비구역: {int(dist)}m 거리에 {name}{kind_txt}")
    return lines


def _reason(exc: BaseException) -> str:
    first = (str(exc).splitlines() or [""])[0]
    return (first or type(exc).__name__)[:160]


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


Section = Callable[..., list[str]]

# 자기 연결을 여는 섹션(기존 서비스 재사용). 공유 연결을 잡은 채 부르면 풀 슬롯을
# 둘 잡게 돼, 동시 요청이 몰리면 서로 기다리다 멈출 수 있다 — 그래서 따로 먼저 돈다.
STANDALONE: tuple[tuple[str, Section], ...] = (
    ("지하철 승하차", _subway),
    ("공실률", _vacancy),
)
# 공유 연결 하나로 도는 섹션.
SECTIONS: tuple[tuple[str, Section], ...] = (
    ("상권변화지표", _change),
    ("집객시설", _facility),
    ("인허가 개폐업", _licenses),
    ("대규모점포", _anchors),
    ("건축허가", _permits),
    ("정비구역", _zones),
)


def area_extra_lines(
    lat: float,
    lng: float,
    radius_m: int = 500,
    *,
    station_name: str | None = None,
) -> list[str]:
    """주변 상권 동태 사실 줄들. 섹션마다 따로 실패한다(빠진 줄은 그냥 없다)."""
    try:
        dong = _dong_code(lat, lng)
    except Exception as exc:  # noqa: BLE001
        logger.info("area_extras: 행정동 매칭 실패 %s", exc)
        dong = None
    lines: list[str] = []
    standalone: dict[str, list[str]] = {}
    for name, section in STANDALONE:
        try:
            standalone[name] = section(lat=lat, lng=lng, radius_m=radius_m, station_name=station_name)
        except Exception as exc:  # noqa: BLE001
            logger.info("area_extras: %s 건너뜀 (%s)", name, _reason(exc))
    lines.extend(standalone.get("지하철 승하차", []))
    try:
        with _connect() as conn:
            conn.autocommit = True  # 표 하나가 없어도 다음 질의가 막히지 않게
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
                for name, section in SECTIONS:
                    try:
                        lines.extend(
                            section(
                                cur,
                                lat=lat,
                                lng=lng,
                                radius_m=radius_m,
                                dong=dong,
                                station_name=station_name,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 - 섹션 하나가 죽어도 나머지는 낸다
                        logger.info("area_extras: %s 건너뜀 (%s)", name, _reason(exc))
                cur.execute("RESET statement_timeout")
            conn.autocommit = False
    except Exception as exc:  # noqa: BLE001 - DB 자체가 안 되면 아무 줄도 없다
        logger.info("area_extras: DB 연결 실패 %s", exc)
    lines.extend(standalone.get("공실률", []))
    return lines
