"""필지 하나 → Structured Analysis. **실제 DB만 읽는다.**

⚠️ **fixture를 서비스 경로에 절대 섞지 않는다.** G6은 화면·서술·도구를 fixture로
개발하지만, 그건 프런트가 `public/fixtures/`를 읽는 것이고 이 함수는 아니다.
여기서 fixture로 폴백하면 사용자가 실제 필지를 눌렀을 때 지어낸 금액이 나가고,
그게 화면에서는 진짜와 구분되지 않는다.

지금 DB에 있는 것과 없는 것:

| 무엇 | 상태 |
| --- | --- |
| 개별공시지가 · 토지특성 · 대표점 | **있다** (`land_prices`·`parcel_characteristics`) |
| 계층 안 백분위 · 갭 점수 | **있다** (`parcel_undervalued_scores`, 배치가 채운다) |
| 비교군 중앙값 | **있다** (같은 `stratum_key` 안에서 계산) |
| 추정 모형 예상값 | **아직 없다** (방법론 C = G2) |

그래서 지금 나오는 판정은 `undetermined / no_estimate`다. **그게 정직한 상태다** —
`land_verdict`의 저평가 정의가 "예상값 대비 남는 몫"이고 예상값이 없으므로,
저평가라고 말할 근거가 없다. 대신 금액·비교군·갭 점수·요인은 다 실제 값으로
채워 나가므로 화면은 비지 않는다. G2가 붙으면 `expected_*`만 채워서 같은 함수가
저평가/고평가를 내기 시작한다.
"""

from __future__ import annotations

import logging

from app.models.land_analysis import Attribute, PeerGroup, StructuredAnalysis, Subject
from app.services.land_verdict import (
    FactorInput,
    VerdictInput,
    build_structured_analysis,
)
from app.services.parcel_land_features import (
    WIDTH_GRADE_LABELS,
    height_display,
    is_unfavorable_height,
    is_unfavorable_shape,
    parse_road_frontage,
    shape_display,
)

logger = logging.getLogger(__name__)

# 추정 모형이 아직 없다는 것을 판정에 실어 보낸다 — 감사 기록에서 "어느 모델이
# 낸 값인가"를 물었을 때 "모델이 없었다"가 답이어야 한다.
NO_MODEL_VERSION = "no-estimator"

# 구조 백분위가 이보다 높으면 "길목"을 가점 요인으로 말한다. 갭 점수와 같은
# 분포(계층 안 백분위)를 쓰므로 별도 임계값이 필요하다.
STRUCTURE_STRONG_PCT = 70.0
STRUCTURE_WEAK_PCT = 30.0

_PARCEL_SQL = """
SELECT s.pnu, s.base_year, s.adm_cd, s.zone_group,
       s.structure_percentile, s.price_percentile, s.gap_score,
       s.stratum_level, s.stratum_key, s.stratum_size,
       COALESCE(lp.price_per_sqm, s.price_per_sqm) AS price_per_sqm,
       c.ldong_name, c.jibun, c.land_category, c.area_sqm, c.road_side_name,
       c.zone_name_1, c.topograph_form, c.topograph_height, c.use_situation,
       ST_Y(ST_Transform(pc.geom_5179, 4326)) AS lat,
       ST_X(ST_Transform(pc.geom_5179, 4326)) AS lng
FROM parcel_undervalued_scores s
LEFT JOIN land_prices lp ON lp.pnu = s.pnu AND lp.base_year = s.base_year
LEFT JOIN parcel_characteristics c ON c.pnu = s.pnu AND c.base_year = s.base_year
LEFT JOIN parcel_centroids pc ON pc.pnu = s.pnu
WHERE s.pnu = %(pnu)s AND s.base_year = %(year)s
"""

# 비교군 중앙값. **자기를 제외한다** — 넣으면 자기 값이 기준에 섞여 차이가
# 체계적으로 작아진다(비교군이 작을 때 특히 크게 틀어진다).
_PEER_SQL = """
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_sqm) AS median_price,
       COUNT(*) AS peer_count
FROM parcel_undervalued_scores
WHERE stratum_key = %(stratum_key)s
  AND stratum_level = %(stratum_level)s
  AND base_year = %(year)s
  AND pnu <> %(pnu)s
"""


def _attributes(row: dict) -> list[Attribute]:
    """화면에 그대로 찍는 속성. 코드값이 아니라 표시 문자열로 만든다."""
    out: list[Attribute] = []
    zone = (row.get("zone_name_1") or "").strip()
    if zone:
        out.append(Attribute(key="zoning", label="용도지역", value=zone))
    category = (row.get("land_category") or "").strip()
    if category:
        out.append(Attribute(key="land_category", label="지목", value=category))
    area = row.get("area_sqm")
    if area:
        out.append(Attribute(key="area", label="면적", value=f"{area:,.1f}㎡"))

    frontage = parse_road_frontage(row.get("road_side_name"))
    out.append(
        Attribute(
            key="road_frontage",
            label="도로접면",
            value=frontage.display(),
            note=(
                None
                if frontage.known
                else "이 항목은 전체 필지의 일부에만 채워져 있습니다."
            ),
        )
    )
    shape = shape_display(row.get("topograph_form"))
    if shape:
        out.append(Attribute(key="shape", label="형상", value=shape))
    height = height_display(row.get("topograph_height"))
    if height:
        out.append(Attribute(key="height", label="지세", value=height))
    use = (row.get("use_situation") or "").strip()
    if use:
        out.append(Attribute(key="use_situation", label="이용상황", value=use))
    return out


def _factors(row: dict) -> list[FactorInput]:
    """요인. **지금은 SHAP이 없다** — 토지특성에서 직접 읽은 사실뿐이다.

    그래서 `explains_discount`는 "이 단점이 값을 낮출 만한 것인가"라는 판단이고,
    실제로 얼마를 설명하는지는 추정 모형이 붙어야 나온다. 화면에는 크기를 문구로만
    (`magnitude_label`) 보여 주고 숫자로 말하지 않는다.
    """
    out: list[FactorInput] = []
    frontage = parse_road_frontage(row.get("road_side_name"))

    if frontage.landlocked:
        out.append(
            FactorInput(
                key="no_road_access",
                label="맹지 (도로에 닿지 않음)",
                direction="negative",
                magnitude_label="큰 감점",
                explains_discount=True,
                detail="도로에 접하지 않아 그대로는 건축 허가를 받기 어렵습니다.",
            )
        )
    elif frontage.width_grade is not None:
        grade = frontage.width_grade
        label = WIDTH_GRADE_LABELS[grade]
        if grade >= 3:
            out.append(
                FactorInput(
                    key="wide_road",
                    label=f"넓은 길에 접함 — {label}",
                    direction="positive",
                    magnitude_label="큰 가점" if grade == 4 else "가점",
                )
            )
        elif grade == 1:
            out.append(
                FactorInput(
                    key="narrow_road",
                    label=f"좁은 길에만 접함 — {label}",
                    direction="negative",
                    magnitude_label="감점",
                    explains_discount=True,
                )
            )
        if frontage.corner:
            out.append(
                FactorInput(
                    key="corner",
                    label="각지 (두 면 이상 도로에 접함)",
                    direction="positive",
                    magnitude_label="가점",
                )
            )
        if frontage.car_accessible is False:
            out.append(
                FactorInput(
                    key="no_car_access",
                    label="차량 진입이 어려움",
                    direction="negative",
                    magnitude_label="감점",
                    explains_discount=True,
                )
            )

    if is_unfavorable_shape(row.get("topograph_form")):
        out.append(
            FactorInput(
                key="irregular_shape",
                label=shape_display(row.get("topograph_form")) or "형상 불리",
                direction="negative",
                magnitude_label="감점",
                explains_discount=True,
            )
        )
    if is_unfavorable_height(row.get("topograph_height")):
        out.append(
            FactorInput(
                key="unfavorable_height",
                label=height_display(row.get("topograph_height")) or "지세 불리",
                direction="negative",
                magnitude_label="감점",
                explains_discount=True,
            )
        )

    structure = row.get("structure_percentile")
    if structure is not None:
        if structure >= STRUCTURE_STRONG_PCT:
            out.append(
                FactorInput(
                    key="through_traffic",
                    label="지나다니는 길목",
                    direction="positive",
                    magnitude_label="가점",
                    detail="주변 도로망에서 사람과 차가 모이는 위치입니다.",
                )
            )
        elif structure <= STRUCTURE_WEAK_PCT:
            out.append(
                FactorInput(
                    key="off_path",
                    label="지나다니는 길에서 벗어남",
                    direction="negative",
                    magnitude_label="감점",
                    explains_discount=True,
                )
            )
    return out


_STRATUM_DESCRIPTIONS = {
    "dong_zone": "같은 동·같은 용도지역",
    "sgg_zone": "같은 구·같은 용도지역",
    "zone": "서울 전체·같은 용도지역",
}


def _peer_group(row: dict, peer_count: int, median_price: float | None) -> PeerGroup:
    level = row["stratum_level"]
    base = _STRATUM_DESCRIPTIONS.get(level, "같은 조건")
    criteria = [c for c in (row.get("ldong_name"), row.get("zone_name_1")) if c]
    return PeerGroup(
        description=f"{base} {peer_count:,}필지",
        size=peer_count,
        level=level,
        criteria=criteria,
        median_official_price_per_sqm=median_price,
    )


def _subject(row: dict) -> Subject:
    dong = (row.get("ldong_name") or "").strip()
    jibun = (row.get("jibun") or "").strip()
    address = " ".join(part for part in (dong, jibun) if part) or row["pnu"]
    return Subject(
        pnu=row["pnu"],
        base_year=row["base_year"],
        address=address,
        ldong_name=dong or None,
        jibun=jibun or None,
        area_sqm=row.get("area_sqm"),
        lat=row.get("lat"),
        lng=row.get("lng"),
    )


def build_from_row(row: dict, *, peer_count: int, median_price: float | None) -> StructuredAnalysis:
    """행 하나 → 판정. **순수 함수** — DB 없이 테스트한다."""
    return build_structured_analysis(
        subject=_subject(row),
        peer_group=_peer_group(row, peer_count, median_price),
        attributes=_attributes(row),
        inp=VerdictInput(
            peer_count=peer_count,
            peer_median_price_per_sqm=median_price,
            actual_price_per_sqm=row.get("price_per_sqm"),
            # 추정 모형이 없다 — 지어내지 않고 비워 둔다. 그래서 판정은 보류다.
            expected_price_point=None,
            expected_price_low=None,
            expected_price_high=None,
            price_percentile=row.get("price_percentile"),
            structure_percentile=row.get("structure_percentile"),
            gap_score=row.get("gap_score"),
            factors=_factors(row),
        ),
        model_version=NO_MODEL_VERSION,
    )


def analyze_parcel(pnu: str, *, base_year: int = 2026) -> StructuredAnalysis | None:
    """필지 하나의 판정. 없거나 표가 안 준비됐으면 None.

    None을 주는 것과 "판정 보류"는 다른 상태다 — None은 이 필지를 우리가 모른다는
    뜻이고, 보류는 알지만 판정할 근거가 모자란다는 뜻이다. 호출부가 그 둘을 다르게
    말해야 한다.
    """
    import psycopg2.errors

    from app.services.spatial_query import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_PARCEL_SQL, {"pnu": pnu, "year": base_year})
                fetched = cur.fetchone()
                if fetched is None:
                    return None
                columns = [d[0] for d in cur.description]
                row = dict(zip(columns, fetched))

                cur.execute(
                    _PEER_SQL,
                    {
                        "stratum_key": row["stratum_key"],
                        "stratum_level": row["stratum_level"],
                        "year": base_year,
                        "pnu": pnu,
                    },
                )
                median_price, peer_count = cur.fetchone()
    except (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn) as exc:
        logger.info(
            "필지 판정 스키마 미준비(%s) — None으로 응답", type(exc).__name__
        )
        return None

    return build_from_row(
        row,
        peer_count=int(peer_count or 0),
        median_price=float(median_price) if median_price is not None else None,
    )


# 좌표 → 그 점에 가장 가까운 필지. 주소로 물어온 질문을 필지에 붙이는 다리다.
#
# 상한(기본 80m)을 두는 이유: 지오코딩이 동 중심이나 건물 대표점을 주는 일이
# 흔해서, 상한 없이 최근접을 잡으면 엉뚱한 필지를 "그 땅"이라고 답한다. 못 찾으면
# None을 돌려 호출부가 "찾지 못했다"고 말하게 한다 — 지어내지 않는다.
_NEAREST_PNU_SQL = """
SELECT pc.pnu
FROM parcel_centroids pc
CROSS JOIN LATERAL (
  SELECT ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), 5179) AS g
) q
WHERE ST_DWithin(pc.geom_5179, q.g, %(max_m)s)
ORDER BY pc.geom_5179 <-> q.g
LIMIT 1
"""

NEAREST_PARCEL_MAX_M = 80.0


def nearest_parcel_pnu(lat: float, lng: float, *, max_m: float = NEAREST_PARCEL_MAX_M) -> str | None:
    """좌표에서 `max_m` 안의 가장 가까운 필지 PNU. 없으면 None."""
    import psycopg2.errors

    from app.services.spatial_query import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_NEAREST_PNU_SQL, {"lat": lat, "lng": lng, "max_m": max_m})
                row = cur.fetchone()
    except (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn) as exc:
        logger.info("필지 대표점 스키마 미준비(%s) — None으로 응답", type(exc).__name__)
        return None
    return row[0] if row else None


def analyze_parcel_at(
    lat: float, lng: float, *, base_year: int = 2026, max_m: float = NEAREST_PARCEL_MAX_M
) -> StructuredAnalysis | None:
    """좌표로 물어온 질문을 필지 판정으로. 붙일 필지가 없으면 None."""
    pnu = nearest_parcel_pnu(lat, lng, max_m=max_m)
    if pnu is None:
        return None
    return analyze_parcel(pnu, base_year=base_year)


# 좌표 → 행정동 코드. "이 동네에서 싼 땅" 질문을 동 단위 조회에 붙이는 다리다.
#
# 동 이름으로 코드를 찾지 않고 좌표로 찾는 이유: 이름은 1:1이 아니다(같은 이름의
# 동이 여러 구에 있고, 행정동과 법정동이 다르다). 좌표는 이미 지오코딩으로 확정한
# 값이므로 그 점이 들어가는 경계를 찾으면 모호함이 없다.
_DONG_AT_SQL = """
SELECT adm_cd, adm_nm
FROM sgis_dong_boundary
-- 이 표의 기하 컬럼 이름은 `geom`이다(다른 표들은 `geom_5179`). SRID는 5179로 같다.
WHERE ST_Contains(geom, ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), 5179))
LIMIT 1
"""


def dong_adm_cd_at(lat: float, lng: float) -> tuple[str, str] | None:
    """좌표가 들어가는 행정동의 (코드, 이름). 못 찾으면 None."""
    import psycopg2.errors

    from app.services.spatial_query import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_DONG_AT_SQL, {"lat": lat, "lng": lng})
                row = cur.fetchone()
    except (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn) as exc:
        logger.info("행정동 경계 스키마 미준비(%s) — None으로 응답", type(exc).__name__)
        return None
    return (row[0], row[1]) if row else None
