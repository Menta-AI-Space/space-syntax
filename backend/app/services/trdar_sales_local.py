"""Local 상권 추정매출 benchmark (서울 상권분석서비스 file download).

Reads the ``trdar_sales`` table loaded by tools/ingest_commercial.py
(분기 × 상권 × 서비스업종, 2026-07-03 개편 후 신 업종코드 체계).

NOTE: the legacy ``seoul_service_upjong_map.json`` maps to the PRE-2026-07
code system (e.g. CS100001 = 비알코올 음료점업); in this dataset CS100001
is 한식음식점 and 커피-음료 is CS100010 — never reuse the old map here.

Point → 상권 resolution uses the ``trdar_area`` PostGIS table
(tools/ingest_trdar_area.py, OA-15560 폴리곤 1,650개; 골목상권 1,090 ·
전통시장 305 · 발달상권 249 · 관광특구 6 — 서로 겹치지 않아 한 지점은
정확히 한 상권에 속한다). Points outside any 상권 polygon (골목 안쪽 등)
fall back to the nearest 상권 within 300m, and then to the 행정동 —
see SCOPE_* below.
"""

from __future__ import annotations

import re
import statistics
import time
from dataclasses import dataclass

import psycopg2

from app.config import settings

# industry-profile id → 신 체계 서비스업종 코드 (2026-07 개편, trdar_sales 실측 기준)
PROFILE_SVC_CODES: dict[str, dict[str, list[str]]] = {
    "cafe": {
        "primary": ["CS100010"],  # 커피-음료
        "secondary": ["CS100005"],  # 제과점
    },
    "restaurant": {
        "primary": [
            "CS100001",  # 한식음식점
            "CS100002",  # 중식음식점
            "CS100003",  # 일식음식점
            "CS100004",  # 양식음식점
        ],
        "secondary": [
            "CS100006",  # 패스트푸드점
            "CS100007",  # 치킨전문점
            "CS100008",  # 분식전문점
        ],
    },
    "clinic": {
        "primary": ["CS200006"],  # 일반의원
        "secondary": ["CS200007", "CS200008"],  # 치과의원, 한의원
    },
    "academy": {
        "primary": ["CS200001"],  # 일반교습학원
        "secondary": ["CS200002", "CS200003"],  # 외국어학원, 예술학원
    },
    "beauty": {
        "primary": ["CS200028"],  # 미용실
        "secondary": ["CS200029", "CS200030"],  # 네일숍, 피부관리실
    },
    "convenience": {
        # 슈퍼마켓(CS300001)을 secondary로 묶어 뒀더니 '편의점 점포당 월매출'에
        # 슈퍼마켓 매출·점포가 함께 합산돼, 편의점이라고 부르면서 편의점이 아닌
        # 숫자를 보여 줬다. 표본은 줄지만 라벨과 숫자가 같은 것을 가리켜야 한다.
        "primary": ["CS300002"],  # 편의점
    },
    # 아래 넷은 industry-profiles에 프로필이 있는데도 여기 매핑이 없어서 매출
    # 벤치마크가 통째로 비어 있었다(그래서 price_gap·brand_whitespace KPI가
    # 늘 score=null). 코드는 추측이 아니라 trdar_stores 실측에서 골랐고,
    # 괄호 안은 서울 점포 수와 매출이 실제로 있는 상권 수다.
    "pharmacy": {
        # 의약품 = 약국. 점포 6,962 · 매출 583개 상권(85%)으로 커버리지가 높다.
        "primary": ["CS300018"],
    },
    "fitness": {
        # 스포츠클럽(=헬스클럽) 4,125. 상가업소정보의 헬스장(R10307) 4,478과
        # 거의 같은 규모라 같은 대상을 가리키는 게 확인된다.
        "primary": ["CS200024"],
        "secondary": ["CS200005"],  # 스포츠 강습
    },
    "leisure": {
        # 프리셋이 R104(노래방·PC방·오락장) 묶음이라 매출도 같은 범위로 맞춘다.
        # 기타오락장·전자게임장은 점포는 있어도 매출 행이 하나도 없어 뺐다
        # (넣으면 분모만 늘어 점포당 매출이 낮아진다).
        "primary": ["CS200037"],  # 노래방
        "secondary": ["CS200019"],  # PC방
    },
    "lodging": {
        # 여관 3,093. 게스트하우스(CS200035)는 점포는 있으나 매출 행이 0이라 뺐다.
        "primary": ["CS200034"],
    },
    # professional_office(부동산중개업 CS200033)는 일부러 비워 둔다 — 점포는
    # 26,067곳인데 매출이 있는 상권이 1,541개 중 23개(5%)뿐이고 그 값도 점포당
    # 월 109만원으로 업계 현실과 맞지 않는다. 중개수수료가 카드결제로 잘 안
    # 잡히는 업종이라 그렇다. 표본이 이런 데이터는 없는 게 낫다.
    # ev_charging도 대응하는 서비스업종 코드 자체가 없다.
}

# 매출을 어느 공간 단위로 계산했는지. 골목상권/발달상권 등 지정 상권 안이면
# "trdar", 상권 밖이라 행정동 평균으로 메운 것이면 "dong" — 정밀도가 다르므로
# 반드시 구분해서 보여 줘야 한다.
SCOPE_TRDAR = "trdar"
SCOPE_DONG = "dong"

# 상권 데이터의 trdar_se_nm은 골목상권/발달상권/전통시장/관광특구 넷 중 하나다.
# 행정동 폴백은 같은 자리에 이 라벨을 넣어 화면에서 바로 구분되게 한다.
DONG_SE_NM = "행정동"

_CACHE_TTL_S = 6 * 3600
_benchmark_cache: dict[tuple[str, str], tuple[float, "CitywideSalesBenchmark"]] = {}


class TrdarSalesError(RuntimeError):
    pass


@dataclass
class CitywideSalesBenchmark:
    profile_id: str
    quarter: str
    svc_codes: list[str]
    trdar_count: int
    median_monthly_sales_won: int
    p25_monthly_sales_won: int
    p75_monthly_sales_won: int
    weekend_share_pct: float | None
    median_per_store_monthly_won: int | None = None
    # 지역 값과 같은 단위로 비교해야 의미가 있다 — 상권 값은 상권 분포와,
    # 행정동 값은 행정동 분포와 비교한다(scope별로 따로 계산·캐시한다).
    scope: str = SCOPE_TRDAR


@dataclass
class TrdarSales:
    trdar_cd: str
    trdar_nm: str
    quarter: str
    monthly_sales_won: int
    monthly_sales_cnt: int | None
    weekend_share_pct: float | None
    store_count: int | None = None
    franchise_count: int | None = None
    per_store_monthly_won: int | None = None
    scope: str = SCOPE_TRDAR


# trdar_sales.month_sales_amt는 이름과 달리 **분기 합계**다. 레코드 키가 분기
# (기준_년분기_코드)라 한 달치를 담을 수 없고, 같은 저장소의 seoul_sales도
# `(quarterly / 3)`으로 월을 만든다. 그런데 이 모듈만 3으로 나누지 않은 채
# 필드 이름에 monthly를 붙여 내보내서, 화면의 상권 월매출·점포당 월매출·예상
# 월매출이 전부 3배로 부풀려져 있었다(신촌 카페 점포당 4,556만원 → 실제 1,519만원).
# 점수는 '점포당 ÷ 서울중앙값' 비율이라 3배가 상쇄돼 영향이 없었고, 그래서
# 오래 눈에 띄지 않았다.
QUARTER_MONTHS = 3


def _monthly(quarterly_won: float) -> float:
    """분기 매출 → 월 매출."""
    return quarterly_won / QUARTER_MONTHS


# 점포당 매출의 분모로 trdar_stores.store_count(점포_수)를 쓰면 편의점 점포당
# 월매출이 1.19억원(중앙값)으로 나온다 — 실제 편의점의 4~5배다. 그 컬럼이
# 업종 전체 점포 수가 아니라는 증거: 서울 합계로 프랜차이즈(4,792)가
# store_count(1,762)보다 많다. 편의점은 대부분 프랜차이즈라 있을 수 없는 관계다.
#
# similar_store_count(유사_업종_점포_수)가 맞는 분모라는 건 이제 확인됐다.
# 남아 있던 의심('유사 업종'이라 더 넓은 집합 아니냐)은 두 가지로 풀렸다:
#
# 1. 항등식. trdar_stores 304,775행 **전부**에서 예외 없이
#      similar_store_count = store_count + franchise_count
#    이다(행정동 단위 golmok_stores 704,470행도 동일). 즉 '유사 업종'이 아니라
#    store_count가 비(非)프랜차이즈 점포만 센 것이고 similar가 전체 합계다.
#    편의점 1,762 + 4,792 = 6,554, 한식 44,642 + 5,917 = 50,559 처럼 딱 맞는다.
#
# 2. 실측 대조. 상가업소정보(sbiz_stores, 실제 점포 좌표 537,489개)를 상권
#    폴리곤에 공간조인해 업종별로 세어 similar_store_count와 비교하면
#    편의점 0.93 · 미용실 1.04 · 네일숍 1.00 · 한식 1.11 · 치과 1.12 ·
#    노래방 1.13배로 거의 일치한다. 반면 store_count는 편의점에서 실제의
#    0.25배라 분모로 쓸 수 없다.
STORE_COUNT_COLUMN = "similar_store_count"


def profile_svc_codes(profile_id: str, *, include_secondary: bool = False) -> list[str]:
    entry = PROFILE_SVC_CODES.get(profile_id)
    if not entry:
        return []
    codes = list(entry["primary"])
    if include_secondary:
        # secondary가 없는 업종도 있다(편의점) — 없으면 primary만 쓴다.
        codes += entry.get("secondary", [])
    return codes


def _weekend_share(weekday_amt, weekend_amt) -> float | None:
    if weekday_amt is None or weekend_amt is None:
        return None
    weekday_amt, weekend_amt = float(weekday_amt), float(weekend_amt)
    total = weekday_amt + weekend_amt
    if total <= 0:
        return None
    return round(100.0 * weekend_amt / total, 1)


# 점포당 매출의 분모는 '매출에 실제로 기여한 업종'만 세야 한다. 프로필 하나가
# 여러 서비스업종을 묶는데(restaurant = 한식+중식+일식+양식+패스트푸드+치킨+분식),
# 어떤 상권에는 분식 점포는 있어도 분식 매출 행이 없다. 그런 업종을 분모에만
# 넣으면 분자는 그대로인 채 분모만 커져 점포당 매출이 낮아진다 — 실측으로
# restaurant는 23.5%, cafe는 11.0% 과소평가되고 있었다. 그래서 store 집계에
# '같은 분기·같은 지역에 매출 행이 있는 업종'이라는 조건을 건다.
_TRDAR_STORE_SUBQUERY = """
    SELECT yyq, trdar_cd, sum(similar_store_count) AS stores,
           sum(franchise_count) AS frc
    FROM trdar_stores st
    WHERE svc_cd = ANY(%(codes)s) AND yyq = %(quarter)s
      AND EXISTS (
          SELECT 1 FROM trdar_sales s2
          WHERE s2.yyq = st.yyq AND s2.trdar_cd = st.trdar_cd
            AND s2.svc_cd = st.svc_cd AND s2.month_sales_amt IS NOT NULL
      )
    GROUP BY yyq, trdar_cd
"""

# 행정동 단위(golmok_*). 점포수는 jsonb detail에만 있고 컬럼명이 한글이다.
_DONG_STORE_SUBQUERY = """
    SELECT stdr_yyqu_cd, region_code,
           sum((detail->>'유사_업종_점포_수')::int) AS stores,
           sum((detail->>'프랜차이즈_점포_수')::int) AS frc
    FROM golmok_stores st
    WHERE region_type = 'dong' AND svc_cd = ANY(%(codes)s)
      AND stdr_yyqu_cd = %(quarter)s
      AND EXISTS (
          SELECT 1 FROM golmok_sales s2
          WHERE s2.region_type = 'dong' AND s2.stdr_yyqu_cd = st.stdr_yyqu_cd
            AND s2.region_code = st.region_code AND s2.svc_cd = st.svc_cd
            AND s2.selng_amt IS NOT NULL
      )
    GROUP BY stdr_yyqu_cd, region_code
"""


def _latest_quarter(cur, codes: list[str], scope: str) -> str | None:
    """매출과 점포수가 **둘 다** 있는 가장 최근 분기.

    행정동 매출은 상권보다 한 분기 앞서 들어오는 반면(20261 vs 20254) 점포수는
    아직 없다. 매출만 있는 분기를 고르면 분모가 없어 점포당이 통째로 사라진다.
    """
    if scope == SCOPE_DONG:
        cur.execute(
            """
            SELECT max(s.stdr_yyqu_cd) FROM golmok_sales s
            WHERE s.region_type = 'dong' AND s.svc_cd = ANY(%s)
              AND EXISTS (
                  SELECT 1 FROM golmok_stores st
                  WHERE st.region_type = 'dong' AND st.svc_cd = ANY(%s)
                    AND st.stdr_yyqu_cd = s.stdr_yyqu_cd
              )
            """,
            (codes, codes),
        )
    else:
        cur.execute(
            """
            SELECT max(s.yyq) FROM trdar_sales s
            WHERE s.svc_cd = ANY(%s)
              AND EXISTS (
                  SELECT 1 FROM trdar_stores st
                  WHERE st.svc_cd = ANY(%s) AND st.yyq = s.yyq
              )
            """,
            (codes, codes),
        )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def citywide_benchmark(
    profile_id: str, scope: str = SCOPE_TRDAR
) -> CitywideSalesBenchmark:
    """서울 전체 분포에서의 위치 — scope와 같은 공간 단위로 계산한다.

    상권 값은 상권 분포(1,650개)와, 행정동 값은 행정동 분포(425개)와 비교해야
    한다. 상권 중앙값에 행정동 값을 대면 행정동이 조용한 주택가까지 포함하는
    만큼 체계적으로 낮게 나와 '서울 평균 이하'라는 잘못된 해석이 붙는다.
    """
    now = time.monotonic()
    key = (profile_id, scope)
    cached = _benchmark_cache.get(key)
    if cached is not None and now - cached[0] < _CACHE_TTL_S:
        return cached[1]

    # 지역 값(sales_for_trdar/sales_for_dong)은 primary+secondary를 합쳐 내는데
    # 여기만 primary로 재던 탓에, 서로 다른 업종 묶음끼리 비교하고 있었다.
    # 카페는 서울 중앙값이 19.4% 낮게(제과점이 빠져서) 잡혀 어느 자리든 실제보다
    # 좋아 보였고, 한식은 반대로 10.1% 높게 잡혀 나빠 보였다. 같은 묶음으로 맞춘다.
    codes = profile_svc_codes(profile_id, include_secondary=True)
    if not codes:
        raise TrdarSalesError(f"업종 매핑 없음: {profile_id}")
    conn = psycopg2.connect(settings.database_url, connect_timeout=4)
    try:
        cur = conn.cursor()
        quarter = _latest_quarter(cur, codes, scope)
        if not quarter:
            raise TrdarSalesError("해당 업종의 매출 데이터가 없습니다")
        params = {"codes": codes, "quarter": quarter}
        if scope == SCOPE_DONG:
            cur.execute(
                f"""
                SELECT s.region_code, sum(s.selng_amt),
                       sum((s.detail->>'주중_매출_금액')::numeric),
                       sum((s.detail->>'주말_매출_금액')::numeric),
                       max(st.stores)
                FROM golmok_sales s
                LEFT JOIN ({_DONG_STORE_SUBQUERY}) st
                       ON st.stdr_yyqu_cd = s.stdr_yyqu_cd
                      AND st.region_code = s.region_code
                WHERE s.region_type = 'dong' AND s.stdr_yyqu_cd = %(quarter)s
                  AND s.svc_cd = ANY(%(codes)s) AND s.selng_amt IS NOT NULL
                GROUP BY s.region_code
                """,
                params,
            )
        else:
            cur.execute(
                f"""
                SELECT s.trdar_cd, sum(s.month_sales_amt), sum(s.weekday_sales_amt),
                       sum(s.weekend_sales_amt), max(st.stores)
                FROM trdar_sales s
                LEFT JOIN ({_TRDAR_STORE_SUBQUERY}) st
                       ON st.yyq = s.yyq AND st.trdar_cd = s.trdar_cd
                WHERE s.yyq = %(quarter)s AND s.svc_cd = ANY(%(codes)s)
                  AND s.month_sales_amt IS NOT NULL
                GROUP BY s.trdar_cd
                """,
                params,
            )
        rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        raise TrdarSalesError("해당 업종의 매출 데이터가 없습니다")

    amounts = sorted(int(_monthly(float(r[1]))) for r in rows)
    shares = [
        s for r in rows if (s := _weekend_share(r[2], r[3])) is not None
    ]
    per_store = sorted(
        _monthly(float(r[1])) / float(r[4]) for r in rows if r[4] and float(r[4]) > 0
    )
    benchmark = CitywideSalesBenchmark(
        profile_id=profile_id,
        quarter=quarter,
        svc_codes=codes,
        trdar_count=len(amounts),
        median_monthly_sales_won=int(statistics.median(amounts)),
        p25_monthly_sales_won=amounts[len(amounts) // 4],
        p75_monthly_sales_won=amounts[(len(amounts) * 3) // 4],
        weekend_share_pct=round(statistics.median(shares), 1) if shares else None,
        median_per_store_monthly_won=(
            int(statistics.median(per_store)) if per_store else None
        ),
        scope=scope,
    )
    _benchmark_cache[key] = (now, benchmark)
    return benchmark


@dataclass
class TrdarMatch:
    trdar_cd: str
    trdar_nm: str
    trdar_se_nm: str
    contains: bool  # False = nearest-within-300m fallback
    distance_m: float
    scope: str = SCOPE_TRDAR


def trdar_for_point(lat: float, lng: float) -> TrdarMatch | None:
    """Resolve a WGS84 point to a 상권 (containing polygon, else nearest ≤300m)."""
    conn = psycopg2.connect(settings.database_url, connect_timeout=4)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT trdar_cd, trdar_nm, trdar_se_nm,
                   ST_Contains(geom, pt) AS contains,
                   ST_DistanceSphere(geom, pt) AS dist_m
            FROM trdar_area, ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS pt
            ORDER BY geom <-> pt
            LIMIT 1
            """,
            (lng, lat),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    trdar_cd, trdar_nm, se_nm, contains, dist_m = row
    if not contains and (dist_m is None or dist_m > 300):
        return None
    return TrdarMatch(
        trdar_cd=trdar_cd,
        trdar_nm=trdar_nm,
        trdar_se_nm=se_nm,
        contains=bool(contains),
        distance_m=round(float(dist_m or 0.0), 1),
    )


# ── 행정동 폴백 ────────────────────────────────────────────────────────────
#
# 실측 점포 좌표 기준으로 22%가 어떤 상권 폴리곤에도 들어가지 않는다. 그 지점은
# 지금까지 매출 벤치마크가 통째로 없었다. golmok_sales에는 상권(trdar) 말고
# 행정동(dong) 단위가 서울 425개 전부에 대해 들어 있으므로 그것으로 메운다.
#
# 두 테이블의 행정동 코드 체계가 다르다: sgis_dong_boundary(폴리곤)는 통계청
# adm_cd, golmok_*는 행자부 행정동코드다. 공통 키가 없어 (자치구명, 행정동명)을
# 정규화해서 잇는다 — 423/425가 정확히 붙고, 나머지는 행정동 개편(상일동 →
# 상일1·2동) 때문이라 끝 숫자를 떼는 규칙 하나로 흡수한다.
_DONG_MAP_TTL_S = 24 * 3600
_dong_map_cache: tuple[float, dict[str, tuple[str, str]]] | None = None


def _norm_name(name: str) -> str:
    """비교용 정규화 — 가운뎃점·공백·깨진 구분자를 떼고 한글과 숫자만 남긴다.

    원본에 '종로1·2·3·4가동'이 '종로1?2?3?4가동'처럼 인코딩이 깨져 들어온
    행이 있어 문자 그대로는 못 맞춘다.
    """
    return re.sub(r"[^가-힣0-9]", "", name or "")


def _drop_trailing_seq(name: str) -> str:
    """'상일1동' → '상일동'. 행정동이 쪼개지기 전 이름으로 되돌린다."""
    return re.sub(r"\d+(?=동$)", "", name)


def _dong_code_map(cur) -> dict[str, tuple[str, str]]:
    """통계청 adm_cd → (golmok 행정동코드, 행정동명)."""
    global _dong_map_cache
    now = time.monotonic()
    if _dong_map_cache is not None and now - _dong_map_cache[0] < _DONG_MAP_TTL_S:
        return _dong_map_cache[1]

    cur.execute(
        "SELECT DISTINCT region_code, region_name FROM golmok_sales WHERE region_type='gu'"
    )
    gu_names = dict(cur.fetchall())
    # 최신 분기로 먼저 좁힌다. 전 분기를 DISTINCT ON으로 훑으면 359,826행을
    # 정렬하느라 4.7초가 걸려 5초 룰을 혼자 다 먹는다 — 같은 425개 결과에 0.37초.
    cur.execute(
        """
        SELECT DISTINCT region_code, region_name
        FROM golmok_sales
        WHERE region_type = 'dong'
          AND stdr_yyqu_cd = (
              SELECT max(stdr_yyqu_cd) FROM golmok_sales WHERE region_type = 'dong'
          )
        """
    )
    golmok_dongs = cur.fetchall()

    exact: dict[tuple[str, str], tuple[str, str]] = {}
    stripped: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for code, name in golmok_dongs:
        gu = _norm_name(gu_names.get(code[:5], ""))
        exact[(gu, _norm_name(name))] = (code, name)
        stripped.setdefault(
            (gu, _drop_trailing_seq(_norm_name(name))), []
        ).append((code, name))

    cur.execute("SELECT adm_cd, adm_nm FROM sgis_dong_boundary")
    mapping: dict[str, tuple[str, str]] = {}
    for adm_cd, adm_nm in cur.fetchall():
        parts = (adm_nm or "").split()
        if len(parts) < 2:
            continue
        key = (_norm_name(parts[-2]), _norm_name(parts[-1]))
        hit = exact.get(key)
        if hit is None:
            # 쪼개진 행정동은 원래 이름으로 되돌려 찾되, 후보가 하나일 때만
            # 받아들인다 — 둘 이상이면 어느 쪽인지 알 수 없으므로 포기한다.
            candidates = stripped.get((key[0], _drop_trailing_seq(key[1])), [])
            if len(candidates) == 1:
                hit = candidates[0]
        if hit is not None:
            mapping[adm_cd] = hit
    _dong_map_cache = (now, mapping)
    return mapping


def dong_for_point(lat: float, lng: float) -> TrdarMatch | None:
    """WGS84 좌표 → 행정동. 서울 밖이면 None (억지로 가장 가까운 동을 주지 않는다)."""
    conn = psycopg2.connect(settings.database_url, connect_timeout=4)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT adm_cd, adm_nm FROM sgis_dong_boundary
            WHERE ST_Contains(
                geom, ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5179)
            )
            LIMIT 1
            """,
            (lng, lat),
        )
        row = cur.fetchone()
        if not row:
            return None
        mapped = _dong_code_map(cur).get(row[0])
    finally:
        conn.close()
    if mapped is None:
        return None
    code, name = mapped
    return TrdarMatch(
        trdar_cd=code,
        trdar_nm=name,
        trdar_se_nm=DONG_SE_NM,
        contains=True,
        distance_m=0.0,
        scope=SCOPE_DONG,
    )


def sales_for_dong(dong_cd: str, profile_id: str) -> TrdarSales | None:
    """행정동 단위 업종 매출 — 상권 밖 지점을 메우는 용도.

    금액 단위는 상권과 완전히 같다: golmok_sales(region_type='trdar')와
    trdar_sales.month_sales_amt가 겹치는 85,732행에서 값이 100% 일치하므로
    같은 분기 합계이고, 같은 `_monthly()`로 월 환산한다.
    """
    codes = profile_svc_codes(profile_id, include_secondary=True)
    if not codes:
        return None
    conn = psycopg2.connect(settings.database_url, connect_timeout=4)
    try:
        cur = conn.cursor()
        quarter = _latest_quarter(cur, codes, SCOPE_DONG)
        if not quarter:
            return None
        cur.execute(
            f"""
            SELECT max(s.region_name), sum(s.selng_amt), sum(s.selng_co),
                   sum((s.detail->>'주중_매출_금액')::numeric),
                   sum((s.detail->>'주말_매출_금액')::numeric),
                   max(st.stores), max(st.frc)
            FROM golmok_sales s
            LEFT JOIN ({_DONG_STORE_SUBQUERY}) st
                   ON st.stdr_yyqu_cd = s.stdr_yyqu_cd
                  AND st.region_code = s.region_code
            WHERE s.region_type = 'dong' AND s.region_code = %(dong)s
              AND s.stdr_yyqu_cd = %(quarter)s AND s.svc_cd = ANY(%(codes)s)
              AND s.selng_amt IS NOT NULL
            """,
            {"codes": codes, "quarter": quarter, "dong": dong_cd},
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row or row[1] is None:
        return None
    nm, amt, cnt, weekday, weekend, stores, frc = row
    stores = int(stores) if stores else None
    monthly = _monthly(float(amt))
    return TrdarSales(
        trdar_cd=dong_cd,
        trdar_nm=nm or "",
        quarter=quarter,
        monthly_sales_won=int(monthly),
        monthly_sales_cnt=int(_monthly(float(cnt))) if cnt is not None else None,
        weekend_share_pct=_weekend_share(weekday, weekend),
        store_count=stores,
        franchise_count=int(frc) if frc is not None else None,
        per_store_monthly_won=int(monthly / stores) if stores else None,
        scope=SCOPE_DONG,
    )


def sales_benchmark_for_point(
    lat: float, lng: float, profile_id: str
) -> tuple[TrdarMatch, TrdarSales, CitywideSalesBenchmark] | None:
    """point → 업종 매출 + 서울 전체 대비 위치.

    지정 상권(골목상권·발달상권 등) 안이면 그 상권 값을 쓰고, 상권 밖이거나
    그 상권에 해당 업종 매출 행이 없으면 행정동 평균으로 내려간다. 어느
    단위로 계산했는지는 match/sales의 ``scope``와 ``trdar_se_nm``에 남는다.
    """
    match = trdar_for_point(lat, lng)
    if match is not None:
        sales = sales_for_trdar(match.trdar_cd, profile_id)
        if sales is not None:
            return match, sales, citywide_benchmark(profile_id, SCOPE_TRDAR)

    dong = dong_for_point(lat, lng)
    if dong is None:
        return None
    dong_sales = sales_for_dong(dong.trdar_cd, profile_id)
    if dong_sales is None:
        return None
    return dong, dong_sales, citywide_benchmark(profile_id, SCOPE_DONG)


@dataclass
class TrdarSalesTrend:
    """상권 점포당 월매출의 분기 추세 (적재된 전 분기 범위)."""

    trdar_cd: str
    base_quarter: str
    latest_quarter: str
    base_per_store_won: int
    latest_per_store_won: int
    change_pct: float
    quarters: int


@dataclass
class PerStoreBand:
    """최근 분기들의 점포당 월매출 변동폭 — 예측을 점이 아니라 범위로 말할 재료.

    한 분기 값 하나로 "예상 매출 572만원"이라고 말하면 확정치처럼 읽히는데,
    같은 상권도 분기마다 실제로 출렁인다. 그 출렁임이 곧 정직한 불확실성이다.
    """

    low_won: int
    high_won: int
    quarters: int
    from_q: str
    to_q: str


def per_store_recent_band(
    trdar_cd: str, profile_id: str, max_quarters: int = 4
) -> PerStoreBand | None:
    """최근 max_quarters개 분기의 점포당 월매출 최소~최대.

    분기가 2개 미만이면 밴드를 만들지 않는다 — 점 하나로 범위를 지어내지
    않는다.
    """
    trend_points = _per_store_quarterly_points(trdar_cd, profile_id)
    points = trend_points[-max_quarters:]
    if len(points) < 2:
        return None
    values = [v for _, v in points]
    return PerStoreBand(
        low_won=min(values), high_won=max(values), quarters=len(points),
        from_q=points[0][0], to_q=points[-1][0],
    )


def _per_store_quarterly_points(trdar_cd: str, profile_id: str) -> list[tuple[str, int]]:
    """분기별 (yyq, 점포당 월매출 원). sales_trend_for_trdar와 같은 쿼리."""
    codes = profile_svc_codes(profile_id, include_secondary=True)
    if not codes:
        return []
    conn = psycopg2.connect(settings.database_url, connect_timeout=4)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.yyq, sum(s.month_sales_amt), max(st.stores)
            FROM trdar_sales s
            JOIN (
              SELECT yyq, trdar_cd, sum(similar_store_count) AS stores
              FROM trdar_stores
              WHERE trdar_cd = %s AND svc_cd = ANY(%s)
              GROUP BY yyq, trdar_cd
            ) st ON st.yyq = s.yyq AND st.trdar_cd = s.trdar_cd
            WHERE s.trdar_cd = %s AND s.svc_cd = ANY(%s)
              AND s.month_sales_amt IS NOT NULL
            GROUP BY s.yyq
            ORDER BY s.yyq
            """,
            (trdar_cd, codes, trdar_cd, codes),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [
        (str(yyq), int(_monthly(float(amt)) // int(stores)))
        for yyq, amt, stores in rows
        if amt is not None and stores
    ]


def sales_trend_for_trdar(trdar_cd: str, profile_id: str) -> TrdarSalesTrend | None:
    """가장 오래된 분기 → 최신 분기 점포당 매출 변화율.

    적재된 분기가 2개 미만이거나 점포수가 없으면 추세를 만들지 않는다
    (없는 데이터를 추정하지 않는다).
    """
    codes = profile_svc_codes(profile_id, include_secondary=True)
    if not codes:
        return None
    conn = psycopg2.connect(settings.database_url, connect_timeout=4)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.yyq, sum(s.month_sales_amt), max(st.stores)
            FROM trdar_sales s
            JOIN (
              SELECT yyq, trdar_cd, sum(similar_store_count) AS stores
              FROM trdar_stores
              WHERE trdar_cd = %s AND svc_cd = ANY(%s)
              GROUP BY yyq, trdar_cd
            ) st ON st.yyq = s.yyq AND st.trdar_cd = s.trdar_cd
            WHERE s.trdar_cd = %s AND s.svc_cd = ANY(%s)
              AND s.month_sales_amt IS NOT NULL
            GROUP BY s.yyq
            ORDER BY s.yyq
            """,
            (trdar_cd, codes, trdar_cd, codes),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    points = [
        (str(yyq), int(_monthly(float(amt)) // int(stores)))
        for yyq, amt, stores in rows
        if amt is not None and stores
    ]
    if len(points) < 2:
        return None
    (base_q, base_v), (latest_q, latest_v) = points[0], points[-1]
    if base_v <= 0:
        return None
    return TrdarSalesTrend(
        trdar_cd=trdar_cd,
        base_quarter=base_q,
        latest_quarter=latest_q,
        base_per_store_won=base_v,
        latest_per_store_won=latest_v,
        change_pct=round((latest_v - base_v) / base_v * 100.0, 1),
        quarters=len(points),
    )


def sales_for_trdar(trdar_cd: str, profile_id: str) -> TrdarSales | None:
    """Latest-quarter sales for one 상권 — for use once point→상권 join exists."""
    codes = profile_svc_codes(profile_id, include_secondary=True)
    if not codes:
        return None
    conn = psycopg2.connect(settings.database_url, connect_timeout=4)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT yyq, max(trdar_nm), sum(month_sales_amt), sum(month_sales_cnt),
                   sum(weekday_sales_amt), sum(weekend_sales_amt)
            FROM trdar_sales
            WHERE trdar_cd = %s AND svc_cd = ANY(%s) AND month_sales_amt IS NOT NULL
            GROUP BY yyq ORDER BY yyq DESC LIMIT 1
            """,
            (trdar_cd, codes),
        )
        row = cur.fetchone()
        stores = franchises = None
        if row:
            # 분모는 이 상권·이 분기에 **매출 행이 실제로 있는** 업종만 센다
            # (_TRDAR_STORE_SUBQUERY 주석 참고 — 안 그러면 점포당이 낮아진다).
            cur.execute(
                """
                SELECT sum(st.similar_store_count), sum(st.franchise_count)
                FROM trdar_stores st
                WHERE st.yyq = %s AND st.trdar_cd = %s AND st.svc_cd = ANY(%s)
                  AND EXISTS (
                      SELECT 1 FROM trdar_sales s2
                      WHERE s2.yyq = st.yyq AND s2.trdar_cd = st.trdar_cd
                        AND s2.svc_cd = st.svc_cd AND s2.month_sales_amt IS NOT NULL
                  )
                """,
                (row[0], trdar_cd, codes),
            )
            store_row = cur.fetchone()
            if store_row:
                stores = int(store_row[0]) if store_row[0] is not None else None
                franchises = int(store_row[1]) if store_row[1] is not None else None
    finally:
        conn.close()
    if not row:
        return None
    yyq, nm, amt, cnt, wd, we = row
    return TrdarSales(
        trdar_cd=trdar_cd,
        trdar_nm=nm or "",
        quarter=yyq,
        monthly_sales_won=int(_monthly(float(amt))),
        monthly_sales_cnt=int(_monthly(float(cnt))) if cnt is not None else None,
        weekend_share_pct=_weekend_share(wd, we),
        store_count=stores,
        franchise_count=franchises,
        per_store_monthly_won=int(_monthly(float(amt)) / stores) if stores else None,
        scope=SCOPE_TRDAR,
    )
