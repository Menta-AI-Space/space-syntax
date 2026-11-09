"""토지특성정보 → parcel_characteristics 적재.

**두 경로가 있다.** 같은 표에 넣고 같은 upsert를 쓰지만 원천이 다르다:

- ``--files`` : V-World 다운로드 **CSV(D195)**. 한 파일이 **여러 연도의 이력**을
  담는다. 인증이 필요 없고 빠르다 — 기본 경로로 쓴다.
- (기본)     : V-World **NED API**. 파일이 없는 연도를 메우는 데 쓴다. 법정동 ×
  연도로 페이지를 돌기 때문에 느리다(연도당 수십~수백 요청).

⚠️ **D195 CSV는 컬럼명이 배포본마다 다르다** (2026-08-24 실측, 파일 6개 전수):

| 위치 | 금천구 배포본(``AL_11545_D195_*``) | 서울 배포본(``AL_D195_11_*``) |
| ---- | --------------------------------- | ----------------------------- |
| 7    | 기준**년**도                      | 기준**연**도                  |
| 17   | 토지**이동**상황                  | 토지**이용**상황              |

컬럼 **순서는 6개 파일 모두 같다.** 그래서 이름으로 먼저 찾고, 못 찾으면 순서로
물러난다(``ingest_land_prices``와 같은 방식). 이름만 믿으면 배포본이 하나 바뀔 때
조용히 전부 결측이 된다.


## API 경로

필지 저평가 판정의 **통제변수**를 가져온다. ``ingest_land_prices_api.py``와 같은
키·같은 호출 방식이고 엔드포인트만 다르다:

  요청주소: https://api.vworld.kr/ned/data/getLandCharacteristics
  인증:     key(VWORLD_API_KEY) + **domain**(VWORLD_DOMAIN)

**용도지역 폴리곤을 따로 받을 필요가 없어졌다.** 연구 문서 3.5가 요구한 통제변수
"접면등급 · 용도지역 · 지목 · 면적"이 이 API 한 곳에 PNU로 묶여 있다. 원래는
국토부 용도지역지구도 WFS를 배치 적재해 필지에 공간조인할 계획이었는데
(013의 ``land_use_zones``), 그 경로가 통째로 필요 없다.

**2026-08-22 실측** (종로구 평동 1111017700, 2024년):

- 252행 / 126필지 — 공시지가 API와 마찬가지로 한 필지가 여러 행으로 온다.
- 용도지역: 일반상업 136 / 제3종일반주거 74 / 제2종일반주거 42
- 지목: 대 140 / 도로 96 / 공원 8 / 하천 6 / 구거 2
- 면적: 252/252 전부 있음
- 연도 범위 **2015~2026**. land_prices(2009~)보다 짧지만, 연도별 OSM 스냅샷이
  2015년부터라 패널 창은 어차피 2015~2026이다.

⚠️ **도로접면은 결측이 많다** — 실측 표본의 63%가 "지정되지않음"이었다. 비준표
순환성 방어(도로접면을 통제한 뒤의 증분만 본다)에 이 필드만 믿으면 안 되고,
PostGIS로 최근접 도로 폭을 계산하는 경로를 병행해야 한다.

용도지역 **명칭은 원문 그대로 저장**한다. 정규화(``land_use_zones.py``)는 조회
시점에 하므로, 출처 표기가 바뀌어도 적재를 다시 돌릴 필요가 없다.

Run:
  cd backend && .venv/Scripts/python -m tools.ingest_parcel_characteristics \\
      --ldong 1111017700 --years 2024 --dry-run
  cd backend && .venv/Scripts/python -m tools.ingest_parcel_characteristics --years 2015..2026
"""

from __future__ import annotations

import argparse
import csv
import glob
import io
import logging
import sys
import time
import zipfile

import httpx
import psycopg2
import psycopg2.extras

from app.config import settings
from tools.ingest_land_prices_api import (
    MAX_CONSECUTIVE_FAILURES,
    MAX_ROWS_PER_PAGE,
    RETRY_SLEEP_S,
    TIMEOUT_S,
    seoul_ldongs,
)

logger = logging.getLogger(__name__)

API_URL = "https://api.vworld.kr/ned/data/getLandCharacteristics"
RESPONSE_ROOT = "landCharacteristicss"  # 오타처럼 보이지만 실제 응답 키다(s가 둘)
# 실측 하한. 공시지가(2009)보다 늦다.
EARLIEST_YEAR = 2015

_UPSERT_SQL = """
INSERT INTO parcel_characteristics (
  pnu, base_year, zone_name_1, zone_name_2, road_side_name, land_category,
  area_sqm, use_situation, topograph_height, topograph_form, price_per_sqm,
  ldong_code, ldong_name, jibun, special_name, src, ingested_at
) VALUES %s
ON CONFLICT (pnu, base_year) DO UPDATE SET
  zone_name_1      = EXCLUDED.zone_name_1,
  zone_name_2      = EXCLUDED.zone_name_2,
  road_side_name   = EXCLUDED.road_side_name,
  land_category    = EXCLUDED.land_category,
  area_sqm         = EXCLUDED.area_sqm,
  use_situation    = EXCLUDED.use_situation,
  topograph_height = EXCLUDED.topograph_height,
  topograph_form   = EXCLUDED.topograph_form,
  price_per_sqm    = EXCLUDED.price_per_sqm,
  ldong_code       = EXCLUDED.ldong_code,
  ldong_name       = EXCLUDED.ldong_name,
  jibun            = EXCLUDED.jibun,
  special_name     = EXCLUDED.special_name,
  src              = EXCLUDED.src,
  ingested_at      = NOW()
"""
_VALUES_TEMPLATE = (
    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())"
)

# 이 값들은 "값이 없다"는 뜻이라 None으로 눕힌다 — 문자열로 저장하면 나중에
# 계층 키가 "지정되지않음"이라는 가짜 용도지역으로 갈라진다.
_NULL_MARKERS = frozenset({"지정되지않음", "해당없음", "미지정", ""})


def parse_years(spec: list[str]) -> list[int]:
    """ingest_land_prices_api와 같은 문법. 하한만 이 API 것(2015)을 쓴다."""
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
            "%s년은 토지특성에 자료가 없어 건너뛴다 (하한 %d년). "
            "공시지가는 2009년부터 있으므로 두 표의 연도 범위가 다르다.",
            ", ".join(str(y) for y in dropped), EARLIEST_YEAR,
        )
    return usable


def _opt(item: dict, key: str) -> str | None:
    """빈 값과 '지정되지않음'류를 None으로. 가짜 범주를 만들지 않는다."""
    v = (item.get(key) or "").strip()
    return None if v in _NULL_MARKERS else v


def to_row(item: dict, src: str) -> tuple | None:
    """응답 1건 → upsert 튜플. pnu·연도가 없으면 None."""
    pnu = (item.get("pnu") or "").strip()
    year = (item.get("stdrYear") or "").strip()
    if not pnu or not year:
        return None
    try:
        base_year = int(year)
    except ValueError:
        return None

    def num(key: str) -> float | None:
        raw = (item.get(key) or "").strip()
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    price = num("pblntfPclnd")
    area = num("lndpclAr")
    return (
        pnu,
        base_year,
        _opt(item, "prposArea1Nm"),
        _opt(item, "prposArea2Nm"),
        _opt(item, "roadSideCodeNm"),
        _opt(item, "lndcgrCodeNm"),
        area,
        _opt(item, "ladUseSittnNm"),
        _opt(item, "tpgrphHgCodeNm"),
        _opt(item, "tpgrphFrmCodeNm"),
        int(price) if price is not None else None,
        _opt(item, "ldCode"),
        _opt(item, "ldCodeNm"),
        _opt(item, "mnnmSlno"),
        _opt(item, "regstrSeCodeNm"),
        src,
    )


# ── D195 CSV 경로 ──────────────────────────────────────────────────────────
# 이름 후보 → 위치. 순서는 6개 배포본에서 모두 같으므로 이름이 없으면 위치로
# 물러난다. 이름만 믿으면 배포본이 하나 바뀔 때 조용히 전부 결측이 된다.
_CSV_FIELDS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("pnu",              0,  ("고유번호",)),
    ("ldong_code",       1,  ("법정동코드",)),
    ("ldong_name",       2,  ("법정동명",)),
    ("regstr_se",        4,  ("대장구분명",)),
    ("jibun",            5,  ("지번",)),
    ("base_year",        7,  ("기준연도", "기준년도")),
    ("land_category",   10,  ("지목명",)),
    ("area_sqm",        11,  ("토지면적",)),
    ("zone_name_1",     13,  ("용도지역명1",)),
    ("zone_name_2",     15,  ("용도지역명2",)),
    ("use_situation",   17,  ("토지이용상황", "토지이동상황")),
    ("topograph_height", 19, ("지형높이",)),
    ("topograph_form",  21,  ("지형형상",)),
    ("road_side_name",  23,  ("도로접면",)),
    ("price_per_sqm",   24,  ("공시지가",)),
)


def resolve_csv_columns(header: list[str]) -> dict[str, int]:
    """헤더 → {논리명: 열 번호}. 이름으로 찾고 실패하면 고정 위치로 물러난다."""
    norm = [h.strip().lstrip("﻿") for h in header]
    out: dict[str, int] = {}
    for key, pos, names in _CSV_FIELDS:
        idx = next((norm.index(n) for n in names if n in norm), None)
        if idx is None and pos < len(norm):
            idx = pos
        if idx is not None:
            out[key] = idx
    return out


# ── D194 SHP 경로 ──────────────────────────────────────────────────────────
# D194는 D195와 **같은 내용에 도형이 붙은** 배포본인데, DBF 컬럼명이 ``A0``~``A26``
# 으로 익명화돼 있어 이름으로 찾을 수가 없다. 그래서 위치로만 매핑한다.
#
# ⚠️ **D195보다 한 칸 밀려 있다.** ``A0``이 일련번호이고 ``A1``이 PNU다
# (D195는 0번이 PNU). 2026-08-24 실측(``AL_11680_D194_20211129``)::
#
#     A1  1168010100106010000   고유번호
#     A8  2021                  기준연도
#     A11 대                    지목명
#     A12 493.9                 토지면적
#     A14 일반상업지역          용도지역명1
#     A16 제3종일반주거지역     용도지역명2
#     A18 상업용                토지이용상황
#
# D195에 없는 연도(2020~2022·2025)를 이걸로 메운다. 값의 의미는 같으므로 같은
# 표·같은 upsert에 넣는다.
_D194_OFFSET = 1

# 익명 컬럼이라 이름 후보를 둘 수 없다 — 위치가 유일한 근거다. 그래서 적재 후
# 반드시 표본을 눈으로 확인한다(용도지역에 엉뚱한 값이 들어오면 위치가 밀린 것).
_D194_COLUMNS: dict[str, int] = {
    key: pos + _D194_OFFSET for key, pos, _ in _CSV_FIELDS
}


def is_d194(path: str) -> bool:
    """파일명으로 D194(SHP)와 D195(CSV)를 가른다.

    배포본 이름이 두 형태로 온다 — ``AL_11680_D194_*``와 ``AL_D194_11680_*``.
    내용으로 판별하려면 zip을 두 번 열어야 하고, 서울 배포본에서는 그때
    MemoryError가 난다(2026-08-24 실측).
    """
    stem = path.replace("\\", "/").rsplit("/", 1)[-1].upper()
    return "D194" in stem


def resolve_d194_columns(field_names: list[str]) -> dict[str, int]:
    """D194 DBF 필드명 목록 → {논리명: 인덱스}.

    ``A0``~``A26`` 형태가 맞는지 확인만 하고 고정 오프셋을 쓴다. 형태가 다르면
    조용히 엉뚱한 컬럼을 읽는 대신 실패한다 — 용도지역이 밀려 들어가면
    계층이 전부 깨진다.
    """
    if not field_names or field_names[0].upper() != "A0":
        raise SystemExit(
            f"D194 DBF 형태가 예상과 다르다 (첫 필드={field_names[:3]}). "
            "컬럼 위치를 다시 확인할 것 — 밀린 채로 넣으면 용도지역이 깨진다."
        )
    return dict(_D194_COLUMNS)


def _shp_streams(path: str):
    """(라벨, 필드명목록, 레코드 이터레이터) — D194 zip에서 SHP DBF를 읽는다.

    지오메트리는 쓰지 않는다(``parcel_polygons``가 이미 갖고 있다). DBF 값만
    필요하므로 ``iterRecords``로 스트리밍한다 — 서울 전체 배포본을 통째로
    올리면 MemoryError가 난다(2026-08-24 실측).
    """
    from tools.ingest_cadastral import _shp_readers_from_zip

    for name, reader in _shp_readers_from_zip(path):
        field_names = [f[0] for f in reader.fields[1:]]
        yield f"{path}::{name}", field_names, reader.iterRecords()


def rows_from_d194(
    path: str, years: set[int] | None, prefixes: tuple[str, ...], src: str
) -> tuple[list[tuple], dict[str, int]]:
    """D194 zip 하나 → upsert 튜플들. ``rows_from_csv``와 같은 계약."""
    out: list[tuple] = []
    stats = {"lines": 0, "bad": 0, "out_of_scope": 0}
    for label, field_names, records in _shp_streams(path):
        cols = resolve_d194_columns(field_names)
        logger.info("%s: D194 위치매핑 (연도열=%s 이용상황열=%s)",
                    label, cols.get("base_year"), cols.get("use_situation"))
        pnu_i = cols["pnu"]
        for rec in records:
            stats["lines"] += 1
            cells = [("" if v is None else str(v)) for v in rec]
            if prefixes and not (
                len(cells) > pnu_i and cells[pnu_i].startswith(prefixes)
            ):
                stats["out_of_scope"] += 1
                continue
            row = csv_row_to_row(cells, cols, src)
            if row is None:
                stats["bad"] += 1
                continue
            if years is not None and row[1] not in years:
                stats["out_of_scope"] += 1
                continue
            out.append(row)
    return out, stats


def csv_row_to_row(cells: list[str], cols: dict[str, int], src: str) -> tuple | None:
    """CSV 한 줄 → upsert 튜플. API의 ``to_row``와 같은 열 순서를 낸다."""

    def val(key: str) -> str | None:
        i = cols.get(key)
        if i is None or i >= len(cells):
            return None
        v = cells[i].strip()
        return None if v in _NULL_MARKERS else v

    pnu, year = val("pnu"), val("base_year")
    if not pnu or not year:
        return None
    try:
        base_year = int(year)
    except ValueError:
        return None

    def num(key: str) -> float | None:
        raw = val(key)
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    price = num("price_per_sqm")
    return (
        pnu, base_year,
        val("zone_name_1"), val("zone_name_2"), val("road_side_name"),
        val("land_category"), num("area_sqm"), val("use_situation"),
        val("topograph_height"), val("topograph_form"),
        int(price) if price is not None else None,
        val("ldong_code"), val("ldong_name"), val("jibun"), val("regstr_se"),
        src,
    )


def _csv_streams(path: str):
    """(라벨, 텍스트 스트림). zip이면 내부 csv를, 아니면 파일 자체를 연다.

    ``ingest_land_prices``와 같은 이유로 **압축을 풀지 않는다** — D195 서울
    배포본은 해제하면 178MB다.
    """
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not names:
                logger.warning("%s: zip 안에 csv가 없다 (%s)", path, zf.namelist()[:5])
                return
            for name in names:
                with zf.open(name) as raw:
                    yield f"{path}::{name}", io.TextIOWrapper(
                        raw, encoding="cp949", errors="replace", newline=""
                    )
    else:
        with open(path, "rb") as raw:
            yield path, io.TextIOWrapper(
                raw, encoding="cp949", errors="replace", newline=""
            )


def rows_from_csv(
    path: str, years: set[int] | None, prefixes: tuple[str, ...], src: str
) -> tuple[list[tuple], dict[str, int]]:
    """D195 파일 하나 → upsert 튜플들. 연도·지역으로 좁힌다."""
    out: list[tuple] = []
    stats = {"lines": 0, "bad": 0, "out_of_scope": 0}
    for label, stream in _csv_streams(path):
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration:
            continue
        cols = resolve_csv_columns(header)
        logger.info("%s: 연도열=%s 이용상황열=%s",
                    label, cols.get("base_year"), cols.get("use_situation"))
        pnu_i = cols.get("pnu", 0)
        for cells in reader:
            stats["lines"] += 1
            # PNU 앞자리로 먼저 버린다 — 파싱보다 싸다.
            if prefixes and not (
                len(cells) > pnu_i and cells[pnu_i].startswith(prefixes)
            ):
                stats["out_of_scope"] += 1
                continue
            row = csv_row_to_row(cells, cols, src)
            if row is None:
                stats["bad"] += 1
                continue
            if years is not None and row[1] not in years:
                stats["out_of_scope"] += 1
                continue
            out.append(row)
    return out, stats


def dedupe_rows(rows: list[tuple]) -> tuple[list[tuple], int]:
    """(pnu, base_year)로 접는다 — 공시지가와 같이 한 필지가 여러 행으로 온다."""
    seen: dict[tuple[str, int], tuple] = {}
    for r in rows:
        seen.setdefault((r[0], r[1]), r)
    return list(seen.values()), len(rows) - len(seen)


def fetch_page(client, ldong: str, year: int, page: int) -> tuple[list[dict], int]:
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

    if "response" in body:  # 자료 없음
        return [], int(body["response"].get("totalCount") or 0)

    block = body.get(RESPONSE_ROOT) or {}
    code = block.get("resultCode")
    if code:
        raise RuntimeError(f"{code}: {block.get('resultMsg')}")
    items = block.get("field") or []
    if isinstance(items, dict):
        items = [items]
    return items, int(block.get("totalCount") or len(items))


def fetch_ldong_year(client, ldong: str, year: int) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        items, total = fetch_page(client, ldong, year, page)
        out.extend(items)
        if not items or len(out) >= total or len(items) < MAX_ROWS_PER_PAGE:
            return out
        page += 1


def run_files(args) -> int:
    """``--files`` 경로. D195 CSV/zip에서 읽어 같은 표에 upsert한다."""
    paths: list[str] = []
    for pattern in args.files:
        hit = sorted(glob.glob(pattern))
        if not hit:
            logger.warning("%s: 해당 파일이 없다", pattern)
        paths.extend(hit)
    if not paths:
        raise SystemExit("읽을 파일이 없다.")

    years = set(parse_years(args.years)) if args.years else None
    prefixes = tuple(args.ldong or ())
    logger.info("파일 %d개 · 연도 %s · 지역 %s",
                len(paths), sorted(years) if years else "전부", prefixes or "전부")

    conn = None if args.dry_run else psycopg2.connect(settings.database_url)
    total = dups_total = bad_total = 0
    no_zone = 0
    try:
        for path in paths:
            # D194는 SHP(DBF), D195는 CSV다. 파일명으로 가른다 — 내용 판별을
            # 하려면 zip을 두 번 열어야 하고 서울 배포본은 그때 메모리가 터진다.
            if is_d194(path):
                rows, stats = rows_from_d194(path, years, prefixes, args.src)
            else:
                rows, stats = rows_from_csv(path, years, prefixes, args.src)
            rows, dups = dedupe_rows(rows)
            by_year: dict[int, int] = {}
            for r in rows:
                by_year[r[1]] = by_year.get(r[1], 0) + 1
            no_zone += sum(1 for r in rows if r[2] is None)

            if rows and conn is not None:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur, _UPSERT_SQL, rows,
                        template=_VALUES_TEMPLATE, page_size=1000,
                    )
                conn.commit()

            total += len(rows)
            dups_total += dups
            bad_total += stats["bad"]
            logger.info(
                "%s: %d필지 적재 (연도별 %s · 중복 %d · 값미비 %d · 범위밖 %d)",
                path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                len(rows), dict(sorted(by_year.items())), dups,
                stats["bad"], stats["out_of_scope"],
            )
    finally:
        if conn is not None:
            conn.close()

    logger.info(
        "전체 완료 — %d필지%s (중복 %d · 값미비 %d)",
        total, " (dry-run, 저장 안 함)" if args.dry_run else "", dups_total, bad_total,
    )
    if no_zone:
        logger.warning("용도지역 없는 필지 %d건 — 그 필지는 판정에서 빠진다", no_zone)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", nargs="+", default=None,
                        help="D195 CSV/zip 경로 또는 glob. 주면 API 대신 파일에서 읽는다.")
    parser.add_argument("--years", nargs="+", default=None,
                        help='"2026" 또는 "2015..2026". --files에서는 생략하면 파일의 전 연도.')
    parser.add_argument("--ldong", action="append", default=None,
                        help="PNU 접두사로 좁힌다(반복 가능). 10자리=법정동, 5자리=시군구.")
    parser.add_argument("--dry-run", action="store_true", help="받아만 보고 저장하지 않음")
    parser.add_argument("--src", default=None, help="src 컬럼 값 (기본: 경로에 따라 자동)")
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.files:
        args.src = args.src or "vworld_d195"
        return run_files(args)
    args.src = args.src or "vworld_api"
    if not args.years:
        raise SystemExit("API 경로에는 --years가 필요하다.")

    # 요청 URL에 key·domain이 들어 있다 — 로그에 비밀값을 남기지 않는다.
    if not settings.vworld_api_key.strip():
        raise SystemExit("VWORLD_API_KEY가 .env에 없다.")
    if not settings.vworld_domain.strip():
        raise SystemExit("VWORLD_DOMAIN이 .env에 없다 — 없으면 INCORRECT_KEY로 거부된다.")

    years = parse_years(args.years)
    if not years:
        raise SystemExit(f"쓸 수 있는 연도가 없다 (하한 {EARLIEST_YEAR}년).")

    conn = None
    if args.ldong:
        targets = [(code, code) for code in args.ldong]
    else:
        conn = psycopg2.connect(settings.database_url)
        targets = seoul_ldongs(conn)
        if not targets:
            raise SystemExit("legal_dong_codes에 서울 법정동이 없다 — 먼저 적재할 것.")
    if not args.dry_run and conn is None:
        conn = psycopg2.connect(settings.database_url)

    logger.info("법정동 %d개 × 연도 %d개 (%s)", len(targets), len(years), years)

    total = skipped = failures = duplicates = 0
    no_zone = 0
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
                        raise SystemExit(f"연속 {consecutive}회 실패 — 키 상태를 확인할 것.")
                    time.sleep(RETRY_SLEEP_S)
                    continue

                rows = [to_row(it, "vworld_api") for it in items]
                good = [r for r in rows if r is not None]
                skipped += len(rows) - len(good)
                good, dups = dedupe_rows(good)
                duplicates += dups
                no_zone += sum(1 for r in good if r[2] is None)
                year_rows += len(good)

                if good and not args.dry_run:
                    with conn.cursor() as cur:  # type: ignore[union-attr]
                        psycopg2.extras.execute_values(
                            cur, _UPSERT_SQL, good,
                            template=_VALUES_TEMPLATE, page_size=1000,
                        )
                    conn.commit()  # type: ignore[union-attr]

                if i % 50 == 0 or i == len(targets):
                    logger.info("  [%d/%d] %d년 누적 %d필지", i, len(targets), year, year_rows)
                if args.sleep:
                    time.sleep(args.sleep)

            total += year_rows
            logger.info("%d년 완료 — %d필지", year, year_rows)

    logger.info(
        "전체 완료 — 필지 %d건%s (중복행 제거 %d, 값 미비 %d, 요청 실패 %d)",
        total, " (dry-run, 저장 안 함)" if args.dry_run else "",
        duplicates, skipped, failures,
    )
    if no_zone:
        # 용도지역이 없으면 그 필지는 판정에서 빠진다 — 규모를 알고 있어야 한다.
        logger.warning(
            "용도지역이 없는 필지 %d건 (%.1f%%) — 이 필지들은 저평가 판정에서 제외된다",
            no_zone, 100.0 * no_zone / max(total, 1),
        )
    if conn is not None:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
