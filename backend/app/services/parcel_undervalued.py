"""필지(PNU) 단위 저평가 판정 — 개별공시지가 대비 접면 도로 구조.

``undervalued_areas.py``(행정동 424개)의 필지 단위 버전이다. 두 단위는 서로를
대체하지 않고 2단으로 쓴다: **동은 탐색**(서울에서 어느 동네를 볼까), **필지는
판정**(그 안에서 어느 땅이 싼가). 동 단위는 실거래를 필지에 붙일 수 없어서
(지번 마스킹 → PNU 생성 불가, ``docs/LAND_VALUE_MODEL_RESEARCH.md`` 1.2) 어쩔 수
없이 집계한 것이었고, 개별공시지가(``land_prices``, PNU×기준연도)가 적재되면서
필지 단위가 가능해졌다.

**동 단위와 결정적으로 다른 점 둘**

1. 구조값이 반경 평균이 아니라 **그 필지가 접한 도로 세그먼트**의 값이다.
   동 단위는 동 중심점 500m 반경 평균이라 같은 동 안 대로변과 이면도로의 차이를
   지웠다 — 그 차이가 공간구문론이 파는 신호인데도. 필지 단위는 그걸 살린다.
   (연구 문서 3.5의 ``log(지가_i) = β·Integration_(seg(i), r) + γ·통제`` 와 같은 단위)

2. 백분위를 서울 전체가 아니라 **계층(stratum) 안에서** 매긴다. 이게 없으면
   결과가 틀린다 — 상업지역은 애초에 간선도로변에 지정되고 용적률이 높아 그
   자체로 비싸므로, 용도지역을 섞어 비교하면 "길 구조 좋음"과 "땅값 비쌈"의
   공통 원인이 전부 구조의 몫으로 계산된다. 그러면 **주거지역 필지가 전부
   저평가로 뜬다** (구조는 괜찮은데 상업지역만큼 안 비싸니까). 연구 문서가
   용도지역을 "가장 중요한 단일 통제변수"로 못박은 이유다.

**이 모듈이 하는 것은 회귀가 아니다.** 연구 문서가 정한 최종 형태는 로그선형
헤도닉 + 동 고정효과 + 공간 HAC이고, 그건 statsmodels가 들어온 뒤에 붙인다.
여기서 하는 계층별 백분위는 같은 교란을 비모수적으로 막는 1차 근사다 —
새 의존성이 없고, 순위·백분위를 파이썬에서 결정론적으로 계산한다는 저장소
원칙(``undervalued_areas.py`` 설계 원칙)을 그대로 따른다. 회귀로 옮길 때
이 결과가 sanity check 기준선이 된다.

⚠️ **순환성** — 개별공시지가 = 비교표준지 × 토지가격비준표 가격배율이고, 비준표
22개 항목에 도로접면(⑯)·도로거리(⑰)가 들어 있다. 즉 구조와 공시지가의 상관은
일부가 행정 산식의 재현이다. 접면등급·도로거리를 계층 키나 통제에 넣은 뒤의
**증분**만 신호로 본다. 높은 상관 자체는 성과가 아니다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.undervalued_areas import rank_percentile

logger = logging.getLogger(__name__)

# 계층 하나가 이보다 작으면 백분위가 불안정하다(한두 필지가 순위를 지배).
# 그럴 땐 더 넓은 계층으로 물러난다 — 아래 STRATUM_LADDER.
MIN_STRATUM_SIZE = 30

# 계층을 좁은 것부터 넓은 것 순으로 시도한다. 좁을수록 통제가 강하지만
# (같은 동·같은 용도지역끼리 비교) 표본이 모자랄 수 있다.
#   dong_zone : 행정동 × 용도지역   — 가장 강한 통제 (동 고정효과 + 용도지역)
#   sgg_zone  : 시군구 × 용도지역   — 동 표본이 얇을 때
#   zone      : 용도지역만          — 최후. 지역 간 가격차가 섞인다
STRATUM_LADDER = ("dong_zone", "sgg_zone", "zone")

# adm_cd(행정동 코드) 앞 5자리가 시군구다.
SGG_CODE_LEN = 5


@dataclass
class ParcelRawInput:
    """필지 하나의 원시 입력. 좌표·기하는 배치(SQL) 쪽에서 이미 해소돼 있다."""

    pnu: str
    adm_cd: str                     # 필지가 속한 행정동 코드
    zone_code: str | None           # 용도지역 코드. None = 용도지역을 못 붙임
    structure_score: float | None   # 접면 도로 세그먼트의 (choice+integration)/2
    price_per_sqm: float | None     # 개별공시지가 원/㎡ (해당 기준연도)
    base_year: int
    buildable: bool = True          # 개발 가능 여부(용적률·특수지). False면 판정 제외


@dataclass
class ParcelScore:
    pnu: str
    adm_cd: str
    zone_code: str
    base_year: int
    structure_score: float
    price_per_sqm: float
    structure_percentile: float
    price_percentile: float
    gap_score: float
    stratum_key: str        # 어느 계층에서 비교했는지 (감사 가능하도록 남긴다)
    stratum_level: str      # STRATUM_LADDER 중 실제로 쓰인 단계
    stratum_size: int       # 그 계층의 필지 수


def _stratum_key(row: ParcelRawInput, level: str) -> str:
    """계층 키. zone_code가 없는 필지는 애초에 판정 대상이 아니다(호출 전 필터)."""
    zone = row.zone_code or ""
    if level == "dong_zone":
        return f"{row.adm_cd}|{zone}"
    if level == "sgg_zone":
        return f"{row.adm_cd[:SGG_CODE_LEN]}|{zone}"
    if level == "zone":
        return zone
    raise ValueError(f"알 수 없는 계층 단계: {level}")


def _is_judgeable(row: ParcelRawInput) -> bool:
    """판정 가능한 필지인가.

    구조·가격·용도지역 중 하나라도 없으면 제외한다 — 0으로 채우면 "구조가 나쁜
    필지"와 "값을 못 붙인 필지"가 섞여 순위가 망가진다(동 단위와 같은 원칙).
    개발 불가 필지도 제외한다: 지을 수 없는 땅이 싼 것은 저평가가 아니다.
    """
    return (
        row.structure_score is not None
        and row.price_per_sqm is not None
        and bool(row.zone_code)
        and row.buildable
    )


def assign_strata(
    rows: list[ParcelRawInput], *, min_size: int = MIN_STRATUM_SIZE
) -> dict[str, tuple[str, str]]:
    """필지별로 쓸 계층을 정한다 → {pnu: (level, stratum_key)}.

    좁은 계층부터 확인해, 그 계층이 min_size를 채우면 거기서 멈춘다. 끝까지
    못 채우면 마지막(가장 넓은) 단계를 쓴다 — 통제가 약해지지만 판정을 포기하는
    것보다 낫고, ``stratum_level``에 무엇을 썼는지 남으므로 나중에 걸러낼 수 있다.
    """
    chosen: dict[str, tuple[str, str]] = {}
    counts_by_level: dict[str, dict[str, int]] = {}
    for level in STRATUM_LADDER:
        counts: dict[str, int] = {}
        for row in rows:
            counts[_stratum_key(row, level)] = counts.get(_stratum_key(row, level), 0) + 1
        counts_by_level[level] = counts

    for row in rows:
        for level in STRATUM_LADDER:
            key = _stratum_key(row, level)
            if counts_by_level[level][key] >= min_size:
                chosen[row.pnu] = (level, key)
                break
        else:
            last = STRATUM_LADDER[-1]
            chosen[row.pnu] = (last, _stratum_key(row, last))
    return chosen


def compute_parcel_gaps(
    rows: list[ParcelRawInput], *, min_stratum_size: int = MIN_STRATUM_SIZE
) -> list[ParcelScore]:
    """필지별 갭 점수 = 계층 안 구조 백분위 − 계층 안 가격 백분위.

    갭이 클수록 "같은 동네·같은 용도지역의 다른 필지들에 비해, 길 구조는 위쪽인데
    공시지가는 아래쪽"이라는 뜻이다. 시세 대비 싸다는 말이 아니다 — 공시지가는
    나지상정 평가액이고 시세반영률이 100%가 아니므로 **금액이 아니라 %로만** 말한다.
    """
    judgeable = [r for r in rows if _is_judgeable(r)]
    if not judgeable:
        return []

    strata = assign_strata(judgeable, min_size=min_stratum_size)

    # 분포는 (단계, 키)마다 **그 단계에서 키가 일치하는 모든 필지**로 만든다.
    # 자기가 그 단계를 골랐는지는 상관없다 — 넓은 계층으로 물러나는 목적이 더 넓은
    # 모집단과 비교하는 것이므로, "같이 물러난 필지들끼리만" 비교하면 뜻이 뒤집힌다
    # (물러난 필지가 혼자면 백분위가 50/50으로 굳어 갭이 0이 된다).
    dists: dict[tuple[str, str], tuple[list[float], list[float]]] = {}
    for level in STRATUM_LADDER:
        for row in judgeable:
            struct_d, price_d = dists.setdefault((level, _stratum_key(row, level)), ([], []))
            struct_d.append(row.structure_score)  # type: ignore[arg-type]
            price_d.append(row.price_per_sqm)  # type: ignore[arg-type]

    out: list[ParcelScore] = []
    for row in judgeable:
        level, key = strata[row.pnu]
        struct_d, price_d = dists[(level, key)]
        structure_pct = rank_percentile(row.structure_score, struct_d)  # type: ignore[arg-type]
        price_pct = rank_percentile(row.price_per_sqm, price_d)  # type: ignore[arg-type]
        out.append(
            ParcelScore(
                pnu=row.pnu,
                adm_cd=row.adm_cd,
                zone_code=row.zone_code,  # type: ignore[arg-type]
                base_year=row.base_year,
                structure_score=row.structure_score,  # type: ignore[arg-type]
                price_per_sqm=row.price_per_sqm,  # type: ignore[arg-type]
                structure_percentile=structure_pct,
                price_percentile=price_pct,
                gap_score=round(structure_pct - price_pct, 1),
                stratum_key=key,
                stratum_level=level,
                stratum_size=len(struct_d),
            )
        )
    return out


def top_undervalued(
    scores: list[ParcelScore],
    *,
    limit: int = 20,
    allowed_levels: tuple[str, ...] = ("dong_zone", "sgg_zone"),
) -> list[ParcelScore]:
    """갭 상위 N개. 기본값은 통제가 약한 ``zone`` 단계를 제외한다.

    ``zone``만으로 비교된 필지는 강남과 도봉이 한 분포에 섞여 있어 갭이 지역
    가격차를 반영한다 — 목록 맨 위에 두면 안 된다. 필요하면 allowed_levels로 연다.
    """
    eligible = [s for s in scores if s.stratum_level in allowed_levels]
    return sorted(eligible, key=lambda s: s.gap_score, reverse=True)[:limit]


# ── 조회 (배치가 채운 표를 읽기만 한다 — 5초 룰) ────────────────────────────

# 화면 기본값으로 남기는 지목. 배치는 도로·하천 같은 "매매 대상이 아닌" 것만
# 걸러내는데, 그것만으로는 목록 위가 임야·철도용지로 덮인다 — 실측으로 상위
# 1,000필지의 21%가 임야(전체 비중 1.2%, 17배 과대표집), 6%가 철도용지였다.
# 목이 좋은데 공시지가가 낮은 게 맞지만 **저평가라서가 아니라 그 용도로 쓸 수
# 없어서**다. 철도용지·학교용지는 매매 자체가 안 된다.
#
# 배치가 아니라 여기서 거르는 이유: 판정 결과(669,610행)는 그대로 두고 화면만
# 좁힌다. 기준을 바꾸고 싶으면 6분짜리 배치를 다시 돌리지 않고 파라미터만 바꾸면
# 된다. ``land_categories=None``으로 부르면 필터가 통째로 꺼진다.
DEFAULT_LAND_CATEGORIES = ("대",)

# 이보다 작은 필지는 목록에서 뺀다. 실측에서 지목 "답"의 평균이 52㎡였고 13㎡짜리
# 필지도 상위에 올라왔는데, 그 크기는 투자 후보로 의미가 없다.
DEFAULT_MIN_AREA_SQM = 60.0

_TOP_IN_DONG_SQL = """
SELECT s.pnu, s.adm_cd, s.zone_group, s.base_year,
       s.structure_percentile, s.price_percentile, s.gap_score,
       s.price_per_sqm, s.stratum_level, s.stratum_size,
       c.ldong_name, c.jibun, c.land_category, c.area_sqm, c.road_side_name,
       b.adm_nm,
       ST_Y(ST_Transform(pc.geom_5179, 4326)) AS lat,
       ST_X(ST_Transform(pc.geom_5179, 4326)) AS lng
FROM parcel_undervalued_scores s
JOIN parcel_characteristics c
  ON c.pnu = s.pnu AND c.base_year = s.base_year
LEFT JOIN parcel_centroids pc ON pc.pnu = s.pnu
LEFT JOIN sgis_dong_boundary b ON b.adm_cd = s.adm_cd
WHERE s.adm_cd = %(adm_cd)s
  AND s.stratum_level = ANY(%(levels)s)
  AND (%(categories)s::text[] IS NULL OR c.land_category = ANY(%(categories)s))
  AND (%(min_area)s::float8 IS NULL OR c.area_sqm >= %(min_area)s)
ORDER BY s.gap_score DESC
LIMIT %(limit)s
"""


def list_top_parcels_in_dong(
    adm_cd: str,
    *,
    limit: int = 20,
    allowed_levels: tuple[str, ...] = ("dong_zone", "sgg_zone"),
    land_categories: tuple[str, ...] | None = DEFAULT_LAND_CATEGORIES,
    min_area_sqm: float | None = DEFAULT_MIN_AREA_SQM,
) -> list[dict]:
    """행정동 하나 안에서 갭 상위 필지. 요청 시점엔 계산하지 않는다.

    **표가 아직 없으면 빈 목록을 준다** — 배치를 한 번도 안 돌린 환경에서 500이
    아니라 "아직 계산 안 함"으로 보이게 한다. 동 단위 API와 같은 처리다.
    """
    import psycopg2.errors

    from app.services.spatial_query import get_connection

    params = {
        "adm_cd": adm_cd,
        "levels": list(allowed_levels),
        "categories": list(land_categories) if land_categories else None,
        "min_area": min_area_sqm,
        "limit": limit,
    }
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_TOP_IN_DONG_SQL, params)
                rows = cur.fetchall()
                columns = [d[0] for d in cur.description]
    except (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn) as exc:
        logger.info(
            "필지 판정 스키마 미준비(%s) — 빈 목록으로 응답", type(exc).__name__
        )
        return []
    return [dict(zip(columns, row)) for row in rows]


# ── 지도용 조회 (필지 경계) ──────────────────────────────────────────────

# 지도는 **땅 전체**를 그려야 한다. 판정된 필지만 주면 지도에 구멍이 뚫리고,
# 사용자는 그게 "데이터 없음"인지 "땅이 아님"인지 구분할 수 없다. 그래서
# parcel_polygons를 왼쪽에 두고 판정·공시지가·속성을 LEFT JOIN한다 —
# 판정이 없는 필지는 판정 필드가 빈 채로 온다.
#
# 새 엔드포인트를 만들지 않고 이 함수를 필지 조회 옆에 두는 이유: "이 범위의
# 필지들"이라는 질문은 하나이므로 조회도 한 군데 모아 둔다.
#
# ⚠️ **정렬은 거리순이어야 한다.** 처음에 ``gap_score DESC NULLS LAST``로 두었더니
# 필지가 limit을 넘는 순간 판정 없는 필지가 통째로 잘려 나갔다 — 독산동 실측에서
# 반경 400m에 "필지 500곳 · 판정 500곳"이 나왔고, 회색 필지를 보여 주겠다는 이
# 조회의 계약이 조용히 깨진 상태였다. 갭 순 정렬은 지도에서 뜻이 없기도 하다:
# 잘려 나가는 것이 화면 어디인지 알 수 없어 구멍이 무작위로 뚫린다. 거리순이면
# 잘리는 것은 항상 바깥 테두리라 사용자가 지도를 움직여 채울 수 있다.
_PARCELS_IN_BOX_SQL = """
SELECT p.pnu,
       ST_AsGeoJSON(ST_Transform(p.geom_5179, 4326), 6) AS geometry,
       p.area_sqm AS geom_area_sqm,
       s.gap_score, s.structure_percentile, s.price_percentile,
       s.stratum_level, s.stratum_size, s.zone_group,
       lp.price_per_sqm AS official_price_per_sqm,
       c.ldong_name, c.jibun, c.land_category, c.area_sqm, c.road_side_name,
       c.zone_name_1
FROM parcel_polygons p
LEFT JOIN parcel_undervalued_scores s ON s.pnu = p.pnu
LEFT JOIN land_prices lp ON lp.pnu = p.pnu AND lp.base_year = %(year)s
LEFT JOIN parcel_characteristics c ON c.pnu = p.pnu AND c.base_year = %(year)s
CROSS JOIN LATERAL (
  SELECT ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), 5179) AS g
) q
WHERE ST_DWithin(p.geom_5179, q.g, %(radius_m)s)
ORDER BY p.geom_5179 <-> q.g
LIMIT %(limit)s
"""


def list_parcels_near(
    lng: float,
    lat: float,
    *,
    radius_m: float = 150.0,
    base_year: int = 2026,
    limit: int = 500,
) -> list[dict]:
    """반경 안 필지의 **경계와 판정**. 판정이 없는 필지도 포함한다.

    ``parcel_polygons``가 채워진 범위만 결과가 나온다 — 폴리곤은 동 단위로
    적재하므로(``ingest_parcel_polygons.py``) 안 넣은 동은 빈 목록이다. 그게
    "판정이 없다"와 다른 상태임을 호출자가 구분할 수 있어야 한다.

    표가 없으면 빈 목록을 준다 — 배치·적재를 안 한 환경에서 500이 아니라
    "아직 준비 안 됨"으로 보이게 하는 다른 조회들과 같은 처리다.
    """
    import psycopg2.errors

    from app.services.spatial_query import get_connection

    params = {
        "lng": lng,
        "lat": lat,
        "radius_m": radius_m,
        "year": base_year,
        "limit": limit,
    }
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_PARCELS_IN_BOX_SQL, params)
                rows = cur.fetchall()
                columns = [d[0] for d in cur.description]
    except (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn) as exc:
        logger.info(
            "필지 경계 스키마 미준비(%s) — 빈 목록으로 응답", type(exc).__name__
        )
        return []
    return [dict(zip(columns, row)) for row in rows]
