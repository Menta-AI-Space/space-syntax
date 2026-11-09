"""Seoul-wide dong benchmarks for demand percentile ranks.

절대 금액·절대 인원만 던지면 해석이 안 된다("아파트 평당 가격만 알려주는 건
딱히 의미가 없다"). 이 모듈의 모든 지표는 서울 425개 행정동 분포에서의 위치
(상위 n%)와 중앙값 대비 배수를 함께 낸다.

무엇을 못 내는지도 분명히 해 둔다:
- **소득 데이터는 이 DB에 없다.** 서울 상권분석서비스 소득소비 데이터셋의
  월_평균_소득_금액 컬럼은 적재되지 않았고(golmok_consumption.detail에 지출
  항목만 있다), 다른 어떤 테이블에도 소득이 없다. 그래서 '서울 평균 소득 대비'는
  계산할 수 없다.
- **golmok_consumption의 지출총금액은 '주민의 구매력'이 아니다.** 거주인구와의
  상관이 -0.01로 사실상 무관하고 종사자 수와 0.43으로 붙는다. 1인당으로 나누면
  소공동이 월 3.3억원, 개포1동이 0원으로 네 자릿수가 벌어진다. 그 지역에서
  **일어나는 소비**의 규모이지 사는 사람의 지불 능력이 아니므로 그렇게 라벨한다.
- **성별×연령 교차(예: 20대 여성) 값은 공개 데이터에 없다.** golmok_flpop과
  dong_footfall 모두 남/녀 합계와 연령대 합계를 따로 줄 뿐 교차표가 없다.
  두 비율을 곱해 추정하면 지어내는 것이므로 각각 따로 낸다.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from app.services.spatial_query import get_connection

from app.services.sgis import (
    SgisConfigError,
    SgisError,
    fetch_seoul_dong_company_rows,
    fetch_seoul_dong_population_rows,
    get_access_token,
)

SEOUL_ADM_PREFIX = "11"
CACHE_TTL_SEC = 24 * 3600

_cache: dict[str, object] = {
    "populations": None,
    "workers": None,
    "youth_pcts": None,
    "youth_expires_at": 0.0,
    "expires_at": 0.0,
}

# 골목 데이터(golmok_*)의 분기 합계를 하루치로 환산할 때 쓰는 분기별 일수.
# '90일'로 뭉뚱그리면 분기마다 최대 2일이 틀어지므로 실제 일수를 쓴다.
_QUARTER_DAYS = {1: 90, 2: 91, 3: 92, 4: 92}


def _quarter_days(yyq: str) -> int:
    """'20254' → 그 분기의 일수 (윤년이면 1분기 91일)."""
    year, quarter = int(yyq[:4]), int(yyq[4:])
    days = _QUARTER_DAYS.get(quarter, 91)
    if quarter == 1 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        days += 1
    return days


@dataclass
class SeoulRank:
    population_top_pct: int | None
    workers_top_pct: int | None
    youth_top_pct: int | None


def _parse_int(value) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_float(value) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def top_percentile_rank(value: float, distribution: list[float]) -> int | None:
    """Return '상위 n%' (1 = best/highest)."""
    clean = [v for v in distribution if v is not None]
    if not clean or value is None:
        return None
    higher = sum(1 for v in clean if v > value)
    return max(1, round(100.0 * (higher + 1) / len(clean)))


def _load_from_local_db() -> tuple[dict[str, int], dict[str, int]] | None:
    """서울 426개 행정동의 (인구, 종사자)를 로컬 sgis_dong_stats에서 읽는다.

    프로젝트 원칙대로 런타임엔 외부 API를 부르지 않는 것이 기본이다(CLAUDE.md).
    원격 SGIS는 키가 없거나 만료되면 통째로 실패해 seoul_rank가 null이 되는데,
    같은 수치가 이미 로컬에 적재돼 있으므로 이쪽을 먼저 쓴다.
    """
    try:
        from app.services.spatial_query import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT adm_cd, population, workers FROM sgis_dong_stats "
                    "WHERE adm_cd LIKE %s",
                    (f"{SEOUL_ADM_PREFIX}%",),
                )
                rows = cur.fetchall()
    except Exception:
        return None

    populations: dict[str, int] = {}
    workers: dict[str, int] = {}
    for adm_cd, pop, wrk in rows:
        code = str(adm_cd or "")
        if len(code) != 8 or not code.startswith(SEOUL_ADM_PREFIX):
            continue
        if pop is not None:
            populations[code] = int(pop)
        if wrk is not None:
            workers[code] = int(wrk)
    if not populations:
        return None
    return populations, workers


async def _ensure_cache() -> None:
    now = time.time()
    if (
        _cache["populations"] is not None
        and _cache["workers"] is not None
        and now < float(_cache["expires_at"])
    ):
        return

    # 로컬 적재분 우선 — 외부 호출 없이 즉시 끝나고, 키 문제로 순위가 통째로
    # 사라지지 않는다. 비어 있을 때만 원격 SGIS로 폴백한다.
    local = _load_from_local_db()
    if local is not None:
        _cache["populations"], _cache["workers"] = local
        _cache["expires_at"] = now + CACHE_TTL_SEC
        return

    async with httpx.AsyncClient(timeout=60.0) as client:
        token = await get_access_token(client)
        pop_rows, co_rows = await asyncio.gather(
            fetch_seoul_dong_population_rows(client, token),
            fetch_seoul_dong_company_rows(client, token),
        )

    populations: dict[str, int] = {}
    for row in pop_rows:
        adm_cd = str(row.get("adm_cd", ""))
        if len(adm_cd) != 8 or not adm_cd.startswith(SEOUL_ADM_PREFIX):
            continue
        pop = _parse_int(row.get("tot_ppltn"))
        if pop is not None:
            populations[adm_cd] = pop

    workers: dict[str, int] = {}
    for row in co_rows:
        adm_cd = str(row.get("adm_cd", ""))
        if len(adm_cd) != 8 or not adm_cd.startswith(SEOUL_ADM_PREFIX):
            continue
        wrk = _parse_int(row.get("tot_worker"))
        if wrk is not None:
            workers[adm_cd] = wrk

    _cache["populations"] = populations
    _cache["workers"] = workers
    _cache["expires_at"] = now + CACHE_TTL_SEC


def compute_seoul_rank(
    adm_cd: str,
    population: int | None,
    workers: int | None,
    youth_pct: float | None,
) -> SeoulRank | None:
    """Compute Seoul dong percentile ranks. Returns None if not Seoul."""
    if not adm_cd.startswith(SEOUL_ADM_PREFIX):
        return None
    if _cache["populations"] is None or _cache["workers"] is None:
        return None

    populations: dict[str, int] = _cache["populations"]  # type: ignore[assignment]
    worker_map: dict[str, int] = _cache["workers"]  # type: ignore[assignment]

    pop_rank = (
        top_percentile_rank(float(population), [float(v) for v in populations.values()])
        if population is not None
        else None
    )
    wrk_rank = (
        top_percentile_rank(float(workers), [float(v) for v in worker_map.values()])
        if workers is not None
        else None
    )
    # youth_pct rank requires separate fetch — computed from age_bands in router
    return SeoulRank(
        population_top_pct=pop_rank,
        workers_top_pct=wrk_rank,
        youth_top_pct=None,
    )


def youth_pct_from_age_bands(age_bands: list) -> float | None:
    """Sum 10대+20대 pct from AgeBand list."""
    total = 0.0
    found = False
    for band in age_bands:
        if band.band in ("10대", "20대"):
            total += band.pct
            found = True
    return round(total, 1) if found else None


async def seoul_rank_for_profile(
    adm_cd: str,
    population: int | None,
    workers: int | None,
    age_bands: list,
) -> SeoulRank | None:
    try:
        await _ensure_cache()
    except (SgisError, SgisConfigError):
        return None

    youth_pct = youth_pct_from_age_bands(age_bands)
    rank = compute_seoul_rank(adm_cd, population, workers, youth_pct)
    if rank is None:
        return None

    # 10–20대 비중의 서울 내 위치도 실제 분포에서 잰다. 예전에는 '서울 평균
    # 28%'를 상수로 박고 거기서 임의의 식으로 순위를 만들어 냈는데, 그 28%도
    # 실측이 아니었고 결과도 35%·15% 같은 계단값으로 튀었다. sgis_dong_stats에
    # 426개 동의 연령대 구성이 이미 있으므로 다른 지표와 같은 방식으로 잰다.
    if youth_pct is not None:
        distribution = _youth_pct_distribution()
        if distribution:
            rank.youth_top_pct = top_percentile_rank(youth_pct, distribution)

    return rank


def _youth_pct_distribution() -> list[float]:
    """서울 행정동별 10–20대 비중 분포 (하루 캐시)."""
    now = time.time()
    cached = _cache.get("youth_pcts")
    if cached is not None and now < float(_cache["youth_expires_at"]):
        return cached  # type: ignore[return-value]
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT age_bands FROM sgis_dong_stats WHERE adm_cd LIKE %s",
                    (f"{SEOUL_ADM_PREFIX}%",),
                )
                rows = cur.fetchall()
    except Exception:
        return []
    pcts: list[float] = []
    for (bands,) in rows:
        if not bands:
            continue
        total = sum(
            float(b.get("pct") or 0.0)
            for b in bands
            if b.get("band") in ("10대", "20대")
        )
        if total > 0:
            pcts.append(round(total, 1))
    _cache["youth_pcts"] = pcts
    _cache["youth_expires_at"] = now + CACHE_TTL_SEC
    return pcts


# ── 행정동 시장 맥락: 유동인구 구성 + 소비 규모 (전부 서울 대비 위치로) ──────

_FLPOP_AGE_BANDS = {
    "10대": "연령대_10_유동인구_수",
    "20대": "연령대_20_유동인구_수",
    "30대": "연령대_30_유동인구_수",
    "40대": "연령대_40_유동인구_수",
    "50대": "연령대_50_유동인구_수",
    "60대 이상": "연령대_60_이상_유동인구_수",
}

_MARKET_CACHE_TTL_S = 6 * 3600
_market_cache: dict[str, object] = {"rows": None, "expires_at": 0.0}


@dataclass
class SeoulRelative:
    """값 하나와, 그 값이 서울 425개 행정동 중 어디쯤인지."""

    value: float
    top_pct: int | None
    seoul_median: float | None

    @property
    def vs_median_pct(self) -> float | None:
        """서울 중앙값 대비 몇 % 높은지 (+18.0 = 18% 높다)."""
        if not self.seoul_median:
            return None
        return round(100.0 * (self.value / self.seoul_median - 1.0), 1)


@dataclass
class DongMarketContext:
    dong_code: str
    dong_name: str
    quarter: str
    # 길단위 유동인구 — 분기 합계를 일평균으로 환산해 읽을 수 있게 한다
    footfall_daily: SeoulRelative
    # 유동인구 구성비(%). 성별×연령 교차는 공개 데이터에 없어 각각 따로 낸다.
    female_pct: SeoulRelative
    age_pcts: dict[str, SeoulRelative] = field(default_factory=dict)
    # 그 지역에서 일어나는 카드 소비 규모(월). 주민 구매력이 아니다 — 상단 참고.
    local_spend_monthly: SeoulRelative | None = None


def _load_market_rows() -> dict[str, dict] | None:
    """서울 425개 행정동의 유동인구 구성 + 소비 규모를 한 번에 읽어 캐시한다.

    어차피 서울 대비 위치를 내려면 전 동의 분포가 필요하다. 해당 동만 읽고
    분포를 또 읽으면 질의가 두 배가 되므로 한 번에 받아 둔다.
    """
    now = time.time()
    if _market_cache["rows"] is not None and now < float(_market_cache["expires_at"]):
        return _market_cache["rows"]  # type: ignore[return-value]

    age_cols = ",\n".join(
        f"(f.detail->>'{src}')::numeric AS \"{band}\""
        for band, src in _FLPOP_AGE_BANDS.items()
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT f.region_code, f.region_name, f.stdr_yyqu_cd, f.tot_flpop,
                           (f.detail->>'여성_유동인구_수')::numeric AS female,
                           {age_cols},
                           c.expndtr_totamt
                    FROM golmok_flpop f
                    LEFT JOIN golmok_consumption c
                           ON c.region_type = 'dong'
                          AND c.region_code = f.region_code
                          AND c.stdr_yyqu_cd = f.stdr_yyqu_cd
                    WHERE f.region_type = 'dong'
                      AND f.stdr_yyqu_cd = (
                          SELECT max(stdr_yyqu_cd) FROM golmok_flpop
                          WHERE region_type = 'dong'
                      )
                    """
                )
                raw = cur.fetchall()
                columns = [d[0] for d in cur.description]
    except Exception:
        return None
    if not raw:
        return None

    rows: dict[str, dict] = {}
    for record in raw:
        row = dict(zip(columns, record))
        total = float(row["tot_flpop"] or 0)
        if total <= 0:
            continue
        quarter = str(row["stdr_yyqu_cd"])
        rows[str(row["region_code"])] = {
            "name": row["region_name"],
            "quarter": quarter,
            "footfall_daily": total / _quarter_days(quarter),
            "female_pct": 100.0 * float(row["female"] or 0) / total,
            "age_pcts": {
                band: 100.0 * float(row[band] or 0) / total
                for band in _FLPOP_AGE_BANDS
            },
            "spend_monthly": (
                float(row["expndtr_totamt"]) / 3.0
                if row["expndtr_totamt"] is not None
                else None
            ),
        }
    _market_cache["rows"] = rows
    _market_cache["expires_at"] = now + _MARKET_CACHE_TTL_S
    return rows


def _relative(value: float | None, distribution: list[float]) -> SeoulRelative | None:
    if value is None or not distribution:
        return None
    ordered = sorted(distribution)
    return SeoulRelative(
        value=round(value, 1),
        top_pct=top_percentile_rank(value, distribution),
        seoul_median=round(ordered[len(ordered) // 2], 1),
    )


def dong_market_context(dong_code: str) -> DongMarketContext | None:
    """행정동 코드(서울열린데이터 체계) → 유동인구 구성·소비 규모 + 서울 대비 위치."""
    rows = _load_market_rows()
    if not rows:
        return None
    entry = rows.get(dong_code)
    if entry is None:
        return None

    footfall = _relative(
        entry["footfall_daily"], [r["footfall_daily"] for r in rows.values()]
    )
    female = _relative(entry["female_pct"], [r["female_pct"] for r in rows.values()])
    if footfall is None or female is None:
        return None
    ages = {}
    for band in _FLPOP_AGE_BANDS:
        rel = _relative(
            entry["age_pcts"][band], [r["age_pcts"][band] for r in rows.values()]
        )
        if rel is not None:
            ages[band] = rel
    return DongMarketContext(
        dong_code=dong_code,
        dong_name=entry["name"],
        quarter=entry["quarter"],
        footfall_daily=footfall,
        female_pct=female,
        age_pcts=ages,
        local_spend_monthly=_relative(
            entry["spend_monthly"],
            [
                r["spend_monthly"]
                for r in rows.values()
                if r["spend_monthly"] is not None
            ],
        ),
    )


def dong_market_context_for_point(lat: float, lng: float) -> DongMarketContext | None:
    """좌표 → 행정동 → 시장 맥락. 서울 밖이면 None."""
    from app.services.trdar_sales_local import dong_for_point

    match = dong_for_point(lat, lng)
    if match is None:
        return None
    return dong_market_context(match.trdar_cd)
