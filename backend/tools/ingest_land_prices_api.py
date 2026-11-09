"""개별공시지가 → land_prices 적재 (V-World NED **API** 경로).

``ingest_land_prices.py``는 V-World **다운로드 CSV**를 읽는다 — 그건 로그인 세션
쿠키가 필요해 사람 손이 들어간다. 이 스크립트는 같은 데이터를 오픈API로 받는다:
쿠키가 필요 없고, 연도를 골라 받을 수 있고, 재실행이 안전하다.

  요청주소: https://api.vworld.kr/ned/data/getIndvdLandPriceAttr
  인증:     key(VWORLD_API_KEY) + **domain**(VWORLD_DOMAIN)

⚠️ **domain이 없으면 INCORRECT_KEY로 거부된다.** 키가 틀린 게 아니라 발급 때
등록한 서비스 URL을 함께 보내야 한다. 서버측 호출이라 필요 없을 줄 알았는데
아니었다(2026-08-21 실측). 이것 때문에 진단 한 바퀴를 돌았다.

**2026-08-21 실측으로 확인된 것** (연구 문서 1.3의 "미확인" 항목들):

- ``pnu``가 응답에 있다 → parcel_centroids와 조인된다. 필지 단위가 성립한다.
- ``stdrYear``로 과거 연도를 받는다. **하한은 2009년**(2008은 totalCount=0).
  2009~2026 = 18년치. 연구 문서가 기대한 37년치(1990~)는 아니다 — 그 아카이브는
  서울 열린데이터광장 파일이었고 2026-08-18 종료됐다. 다만 **패널 설계에는
  영향이 없다**: OSM 도로망 스냅샷이 2015년부터라 교집합이 어차피 2015~2026이다.
- ``pnu``에 **법정동코드 10자리만 넣으면 그 동 전체**가 온다(설명의 "8자리 이상").
  종로구 평동(1111017700) 2024년 → 378건, totalCount 378. 그래서 벌크가 된다.
- **전 필지다.** 그 378건 중 표준지(stdLandAt=Y)는 12건뿐이고 366건이 개별이다.
  표준지만 주는 API가 아니다.
- 제공가능 트래픽 999,999,999 / 일일제한 무제한. 서울 전체 배치가 가능하다.

응답 필드는 land_prices 스키마(migration 012)와 1:1로 대응한다 — 아래 _FIELD_MAP.

Run:
  # 한 동만 (DB 없이 확인)
  cd backend && .venv/Scripts/python -m tools.ingest_land_prices_api \\
      --ldong 1111017700 --years 2024 --dry-run
  # 서울 전체, 최근 연도
  cd backend && .venv/Scripts/python -m tools.ingest_land_prices_api --years 2026
  # 패널용 전 연도
  cd backend && .venv/Scripts/python -m tools.ingest_land_prices_api --years 2015..2026
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import httpx
import psycopg2
import psycopg2.extras

from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.vworld.kr/ned/data/getIndvdLandPriceAttr"
# 실측 하한. 이보다 이전 연도는 totalCount=0으로 온다.
EARLIEST_YEAR = 2009
# 요청변수 numOfRows의 상한(문서 명시).
MAX_ROWS_PER_PAGE = 1000
# 연속 실패가 이만큼 쌓이면 멈춘다 — 키 만료·차단을 조용히 지나치지 않는다.
MAX_CONSECUTIVE_FAILURES = 10
RETRY_SLEEP_S = 2.0
TIMEOUT_S = 30.0

# 응답 필드 → land_prices 컬럼. 실측한 12개 필드가 스키마와 정확히 맞는다.
#   mnnmSlno 본번-부번 / pblntfDe 공시일 / stdLandAt 표준지여부 / stdrYear 기준연도
#   pnu 고유번호 / ldCode 법정동코드 / ldCodeNm 법정동명 / regstrSeCode(Nm) 특수지구분
#   stdrMt 기준월 / pblntfPclnd 공시지가(원/㎡)
_FIELD_MAP = {
    "pnu": "pnu",
    "stdrYear": "base_year",
    "pblntfPclnd": "price_per_sqm",
    "stdrMt": "base_month",
    "stdLandAt": "is_standard",
    "pblntfDe": "notice_date",
    "ldCode": "ldong_code",
    "ldCodeNm": "ldong_name",
    "mnnmSlno": "jibun",
    "regstrSeCode": "special_code",
    "regstrSeCodeNm": "special_name",
}

_UPSERT_SQL = """
INSERT INTO land_prices (
  pnu, base_year, price_per_sqm, base_month, is_standard, notice_date,
  ldong_code, ldong_name, jibun, special_code, special_name, src, ingested_at
) VALUES %s
ON CONFLICT (pnu, base_year) DO UPDATE SET
  price_per_sqm = EXCLUDED.price_per_sqm,
  base_month    = EXCLUDED.base_month,
  is_standard   = EXCLUDED.is_standard,
  notice_date   = EXCLUDED.notice_date,
  ldong_code    = EXCLUDED.ldong_code,
  ldong_name    = EXCLUDED.ldong_name,
  jibun         = EXCLUDED.jibun,
  special_code  = EXCLUDED.special_code,
  special_name  = EXCLUDED.special_name,
  src           = EXCLUDED.src,
  ingested_at   = NOW()
"""
_VALUES_TEMPLATE = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())"

SEOUL_LDONG_PREFIX = "11"


def parse_years(spec: list[str]) -> list[int]:
    """"2015..2026" 또는 "2024 2025" 둘 다 받는다. 하한 밖은 걸러내고 알린다."""
    years: list[int] = []
    for token in spec:
        if ".." in token:
            lo, _, hi = token.partition("..")
            years.extend(range(int(lo), int(hi) + 1))
        else:
            years.append(int(token))
    usable = sorted({y for y in years if y >= EARLIEST_YEAR})
    dropped = sorted({y for y in years if y < EARLIEST_YEAR})
    if dropped:
        logger.warning(
            "%s년은 이 API에 자료가 없어 건너뛴다 (하한 %d년)",
            ", ".join(str(y) for y in dropped), EARLIEST_YEAR,
        )
    return usable


def to_row(item: dict, src: str) -> tuple | None:
    """응답 1건 → upsert 값 튜플. 필수 값이 비면 None (조용히 0으로 채우지 않는다)."""
    pnu = (item.get("pnu") or "").strip()
    year = (item.get("stdrYear") or "").strip()
    price_raw = (item.get("pblntfPclnd") or "").strip()
    if not pnu or not year or not price_raw:
        return None
    try:
        price = int(price_raw)
        base_year = int(year)
    except ValueError:
        return None
    if price <= 0:
        return None

    def opt(key: str) -> str | None:
        v = (item.get(key) or "").strip()
        return v or None

    month_raw = opt("stdrMt")
    std = opt("stdLandAt")
    return (
        pnu,
        base_year,
        price,
        int(month_raw) if month_raw and month_raw.isdigit() else None,
        None if std is None else std.upper() == "Y",
        opt("pblntfDe"),
        opt("ldCode"),
        opt("ldCodeNm"),
        opt("mnnmSlno"),
        opt("regstrSeCode"),
        opt("regstrSeCodeNm"),
        src,
    )


def dedupe_rows(rows: list[tuple]) -> tuple[list[tuple], int]:
    """(pnu, base_year)로 중복 제거. (남은 행, 제거된 수)

    API가 같은 필지를 3행으로 준다 — 값은 전부 같고 stdrMt 표기만 "01"/"1"로
    다르다(2026-08-21 실측: 평동 2024년 378행 = 126필지 × 3, 가격이 다른 필지 0개).
    land_prices의 PK가 (pnu, base_year)라 그대로 넣어도 결과는 같지만 쓰기가
    3배가 되고, 무엇보다 "378건 적재"라는 보고가 필지 수를 오해하게 만든다.
    """
    seen: dict[tuple[str, int], tuple] = {}
    for r in rows:
        seen.setdefault((r[0], r[1]), r)
    return list(seen.values()), len(rows) - len(seen)


def fetch_page(client: httpx.Client, ldong: str, year: int, page: int) -> tuple[list[dict], int]:
    """(그 페이지의 항목들, totalCount). 자료가 없으면 ([], 0)."""
    params = {
        "key": settings.vworld_api_key.strip(),
        "domain": settings.vworld_domain.strip(),
        "pnu": ldong,
        "stdrYear": str(year),
        "format": "json",
        "numOfRows": str(MAX_ROWS_PER_PAGE),
        "pageNo": str(page),
    }
    resp = client.get(API_URL, params=params, timeout=TIMEOUT_S)
    resp.raise_for_status()
    body = resp.json()

    # 자료 없음: {"response": {..., "totalCount": "0"}}
    if "response" in body:
        return [], int(body["response"].get("totalCount") or 0)

    block = body.get("indvdLandPrices") or {}
    code = block.get("resultCode")
    if code and code != "":
        raise RuntimeError(f"{code}: {block.get('resultMsg')}")
    items = block.get("field") or []
    if isinstance(items, dict):  # 1건이면 리스트가 아니라 객체로 오는 경우 대비
        items = [items]
    return items, int(block.get("totalCount") or len(items))


def fetch_ldong_year(client: httpx.Client, ldong: str, year: int) -> list[dict]:
    """한 법정동 × 한 연도 전체 (페이지네이션)."""
    out: list[dict] = []
    page = 1
    while True:
        items, total = fetch_page(client, ldong, year, page)
        out.extend(items)
        if not items or len(out) >= total or len(items) < MAX_ROWS_PER_PAGE:
            return out
        page += 1


def seoul_ldongs(conn) -> list[tuple[str, str]]:
    """서울 법정동 (코드10, 이름). legal_dong_codes가 이미 적재돼 있어야 한다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT region_cd, addr_name
            FROM legal_dong_codes
            WHERE region_cd LIKE %s AND lowest_name <> ''
            ORDER BY region_cd
            """,
            (SEOUL_LDONG_PREFIX + "%",),
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="+", required=True,
                        help='기준연도. "2026" 또는 "2015..2026"')
    parser.add_argument("--ldong", default=None,
                        help="법정동코드 10자리 하나만 (검증용). 생략하면 서울 전체")
    parser.add_argument("--dry-run", action="store_true", help="받아만 보고 저장하지 않음")
    parser.add_argument("--sleep", type=float, default=0.05, help="요청 간 간격(초)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    # httpx가 INFO로 요청 URL을 통째로 찍는데, 거기에 key와 domain이 들어 있다.
    # 로그 파일·터미널 기록에 비밀값이 남으므로 올린다.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not settings.vworld_api_key.strip():
        raise SystemExit("VWORLD_API_KEY가 .env에 없다.")
    if not settings.vworld_domain.strip():
        raise SystemExit(
            "VWORLD_DOMAIN이 .env에 없다 — 없으면 API가 INCORRECT_KEY로 거부한다."
        )

    years = parse_years(args.years)
    if not years:
        raise SystemExit(f"쓸 수 있는 연도가 없다 (하한 {EARLIEST_YEAR}년).")

    conn = None
    if args.ldong:
        targets = [(args.ldong, args.ldong)]
    else:
        conn = psycopg2.connect(settings.database_url)
        targets = seoul_ldongs(conn)
        if not targets:
            raise SystemExit("legal_dong_codes에 서울 법정동이 없다 — 먼저 적재할 것.")
    if not args.dry_run and conn is None:
        conn = psycopg2.connect(settings.database_url)

    logger.info("법정동 %d개 × 연도 %d개 (%s)", len(targets), len(years), years)

    total_rows = skipped = failures = duplicates = 0
    consecutive = 0
    with httpx.Client() as client:
        for year in years:
            year_rows = 0
            for i, (code, name) in enumerate(targets, 1):
                try:
                    items = fetch_ldong_year(client, code, year)
                    consecutive = 0
                except Exception as exc:
                    failures += 1
                    consecutive += 1
                    logger.warning("%s %s %d년 실패: %s", code, name, year, exc)
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        raise SystemExit(
                            f"연속 {consecutive}회 실패 — 키 만료·차단 여부를 확인할 것."
                        )
                    time.sleep(RETRY_SLEEP_S)
                    continue

                rows = [to_row(it, "vworld_api") for it in items]
                good = [r for r in rows if r is not None]
                skipped += len(rows) - len(good)
                good, dups = dedupe_rows(good)
                duplicates += dups
                year_rows += len(good)

                if good and not args.dry_run:
                    with conn.cursor() as cur:  # type: ignore[union-attr]
                        psycopg2.extras.execute_values(
                            cur, _UPSERT_SQL, good,
                            template=_VALUES_TEMPLATE, page_size=1000,
                        )
                    conn.commit()  # type: ignore[union-attr]

                if i % 50 == 0 or i == len(targets):
                    logger.info("  [%d/%d] %d년 누적 %d건", i, len(targets), year, year_rows)
                if args.sleep:
                    time.sleep(args.sleep)

            total_rows += year_rows
            logger.info("%d년 완료 — %d건", year, year_rows)

    logger.info(
        "전체 완료 — 필지 %d건%s (중복행 제거 %d, 값 미비 제외 %d, 요청 실패 %d)",
        total_rows, " (dry-run, 저장 안 함)" if args.dry_run else "",
        duplicates, skipped, failures,
    )
    if conn is not None:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
