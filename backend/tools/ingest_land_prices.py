r"""개별공시지가(V-World 공간정보 다운로드 dsId=6, 테이블 AL_D151) → land_prices 적재.

서울 열린데이터광장의 개별공시지가 파일이 2026-08-18 서비스 종료됐고, 같은
커버리지가 V-World에 살아 있다. PNU × 기준연도로 적재한다. 재실행 안전(upsert).

**두 가지 배포본이 있고 모양이 다르다** (2026-08-23 실측):

| 파일명 형태          | 담긴 연도   | 행 수      | 컬럼 | zip / 해제   |
| -------------------- | ----------- | ---------- | ---- | ------------ |
| ``AL_11_D151_<날짜>``| **이력본**  | 3,210만    | 12   | 173MB / 3.5GB|
| ``AL_D151_11_<날짜>``| 그 해만     | 약 89만    | 13   | 11MB / 102MB |

이력본 하나가 1990~2023년 34개 연도를 담는다(``AL_11_D151_20230712``). 즉 이력본
1개 + 연도별 3개(2024·2025·2026)로 **1990~2026 전체 패널**이 된다.

**압축을 풀지 않는다.** 멤버를 ``zf.read()``로 통째로 읽던 예전 방식은 3.5GB
파일에서 죽는다. 사용자 디스크가 꽉 차서 압축조차 못 푸는 상황이 실제로 있었다.
지금은 zip 안의 CSV를 스트리밍으로 읽으므로 디스크·메모리를 거의 쓰지 않는다.

**지역·연도로 좁혀 넣는다.** MVP는 몇 개 권역만 본다 — 전부 넣으면 필요 없는
3천만 행을 쓰고 필지 단위 검증이 어려워진다. ``--ldong``은 PNU 앞자리로 맞추고
(10자리=법정동, 5자리=시군구), CSV 파싱 **전에** 줄 앞자리로 먼저 버린다.
실측: 이력본에서 한 동만 뽑을 때 622초 → 103초.

**다운로드에 V-World 로그인이 필요하다** — 인증 없이 호출하면 Content-Length가
0으로 온다. 사람이 받거나 세션 쿠키를 --cookie로 넘겨 --fetch로 받는다.

  목록:     POST https://www.vworld.kr/dtmk/dtmk_ntads_s002.do
            svcCde=NA&dsId=6&sidoCd=11&formatSelect=CSV
  다운로드: GET  /dtmk/downloadResourceFile.do?ds_id=20171128DS00144&fileNo=<n>

컬럼은 이름 키워드로 찾고 실패하면 컬럼정의서 순서로 물러난다. 새 파일은
**--inspect를 먼저 돌린다**(DB 불필요) — 헤더·인코딩·연도별 행 수를 보여 준다.

Run:
  # 1) 파일 점검 — 이력본은 전수 집계에 10분쯤 걸린다
  cd backend && .venv/Scripts/python -m tools.ingest_land_prices --inspect "D:\data\AL_11_D151_*.zip"

  # 2) MVP 지역만, 전 연도 (신촌권 예시)
  cd backend && .venv/Scripts/python -m tools.ingest_land_prices \
      "D:\data\AL_11_D151_20230712.zip" "D:\data\AL_D151_11_202*.zip" \
      --ldong 1141011700 --ldong 1141011800 --ldong 1141012200

  # 3) 특정 연도만
  cd backend && .venv/Scripts/python -m tools.ingest_land_prices "D:\data\*.zip" \
      --ldong 11545 --years 2021-2026

  # 4) 쿠키로 직접 받기
  cd backend && .venv/Scripts/python -m tools.ingest_land_prices --fetch --cookie "JSESSIONID=..." --out-dir D:\data
"""

from __future__ import annotations

import argparse
import codecs
import csv
import glob
import io
import re
import sys
import time
import zipfile

import httpx
import psycopg2
import psycopg2.extras

from app.config import settings

VWORLD_LIST_URL = "https://www.vworld.kr/dtmk/dtmk_ntads_s002.do"
VWORLD_FILE_URL = "https://www.vworld.kr/dtmk/downloadResourceFile.do"
# 개별공시지가 CSV 리소스 id. SHP은 ...DS00143 이지만 PNU만 쓰므로 CSV로 충분하다.
VWORLD_CSV_DS_ID = "20171128DS00144"
SEOUL_SIDO_CD = "11"

_UPSERT_SQL = """
INSERT INTO land_prices (
  pnu, base_year, price_per_sqm, base_month, is_standard, notice_date,
  ldong_code, ldong_name, jibun, special_code, special_name, src
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
  ingested_at   = NOW()
WHERE COALESCE(EXCLUDED.base_month, 99) <= COALESCE(land_prices.base_month, 99)
"""

BATCH_SIZE = 5000

# 컬럼정의서(AL_D151) 순서. 헤더를 못 찾았을 때만 쓴다.
_POSITIONAL = [
    "pnu", "ldong_code", "ldong_name", "special_code", "special_name",
    "jibun", "base_year", "base_month", "price", "notice_date",
    "is_standard", "data_date", "src_sgg_code",
]

# 헤더 한글명 → 내부 필드. 공백·괄호를 지운 뒤 부분일치로 찾는다.
_HEADER_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("pnu", ("고유번호", "PNU")),
    ("ldong_code", ("법정동코드",)),
    ("ldong_name", ("법정동명",)),
    ("special_code", ("특수지구분코드",)),
    ("special_name", ("특수지구분명",)),
    ("jibun", ("지번",)),
    ("base_year", ("기준연도", "기준년도")),
    ("base_month", ("기준월",)),
    ("price", ("공시지가",)),
    ("notice_date", ("공시일자",)),
    ("is_standard", ("표준지여부",)),
]


def _norm(s: str) -> str:
    return re.sub(r"[\s()\[\]_·]", "", s or "").strip()


def _map_header(header: list[str]) -> dict[str, int] | None:
    """헤더 행에서 필드 → 컬럼 인덱스를 만든다. PNU와 공시지가를 못 찾으면 None."""
    norm = [_norm(h) for h in header]
    out: dict[str, int] = {}
    for field, keywords in _HEADER_KEYWORDS:
        for i, h in enumerate(norm):
            if any(k in h for k in keywords):
                out.setdefault(field, i)
                break
    # 지번이 "공시지가"에 부분일치하지 않도록 최소 조건만 검사한다.
    if "pnu" not in out or "price" not in out:
        return None
    return out


def _parse_int(v: str) -> int | None:
    v = (v or "").strip().replace(",", "")
    if not v:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def _parse_date(v: str) -> str | None:
    """YYYYMMDD 또는 YYYY-MM-DD → ISO. 그 외는 버린다."""
    v = re.sub(r"[^0-9]", "", (v or "").strip())
    if len(v) != 8:
        return None
    return f"{v[0:4]}-{v[4:6]}-{v[6:8]}"


def _parse_bool(v: str) -> bool | None:
    v = (v or "").strip()
    if not v:
        return None
    # 실제 인코딩 미확인 — Y/N, 1/0, 표준지/일반 모두 받아 둔다.
    if v in ("Y", "y", "1", "T", "true", "True") or "표준" in v:
        return True
    if v in ("N", "n", "0", "F", "false", "False") or "일반" in v:
        return False
    return None


# 인코딩을 알아내려고 앞부분만 읽는 양. 여기에 헤더와 몇 행이 들어가므로
# cp949/UTF-8을 가르기에 충분하다.
_SNIFF_BYTES = 65536


def _sniff_encoding(head: bytes) -> str:
    """앞 조각만 보고 인코딩을 고른다.

    조각 끝에서 멀티바이트 문자가 잘리면 정상 인코딩도 UnicodeDecodeError를
    내므로 **증분 디코더로 final=False**로 검사한다. 그러지 않으면 cp949 파일을
    "cp949 아님"으로 오판하고 전부 깨진 글자로 적재한다.
    """
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    for enc in ("cp949", "utf-8"):
        try:
            codecs.getincrementaldecoder(enc)().decode(head, False)
            return enc
        except UnicodeDecodeError:
            continue
    return "cp949"


def _csv_streams(path: str):
    """(라벨, 텍스트 스트림) 쌍을 yield. zip이면 내부 csv를 모두, 아니면 파일 자체를.

    **압축을 풀지 않고, 파일을 메모리에 올리지도 않는다.** 예전에는 멤버를
    ``zf.read()``로 통째로 읽었는데, 개별공시지가 **이력본**(``AL_11_D151_*``)은
    한 파일이 1990~2023년 3,210만 행 · 압축 해제 3.5GB라 그 방식으로는 죽는다.
    실측: 압축 3.5GB 대비 zip은 173MB이고, 스트리밍으로는 디스크·메모리를 거의
    쓰지 않는다. 사용자 디스크가 꽉 차서 압축조차 못 푸는 상황이 실제로 있었다.

    호출자는 **한 스트림을 다 소비한 뒤** 다음으로 넘어가야 한다 — 제너레이터가
    재개될 때 앞 스트림이 닫힌다.
    """
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not names:
                print(f"  ! {path}: no csv inside zip ({zf.namelist()[:5]})")
            for name in names:
                # 인코딩 판별용으로 한 번, 본문용으로 한 번 연다. 되감기(seek)에
                # 의존하지 않는 이유: ZipExtFile의 seek 가능 여부가 파이썬 버전과
                # 원본 파일 종류에 따라 달라 조용히 실패할 수 있다.
                with zf.open(name) as probe:
                    enc = _sniff_encoding(probe.read(_SNIFF_BYTES))
                with zf.open(name) as raw:
                    yield (
                        f"{path}::{name}",
                        io.TextIOWrapper(raw, encoding=enc, errors="replace", newline=""),
                    )
    else:
        with open(path, "rb") as probe:
            enc = _sniff_encoding(probe.read(_SNIFF_BYTES))
        with open(path, "rb") as raw:
            yield path, io.TextIOWrapper(raw, encoding=enc, errors="replace", newline="")


def _decode(raw: bytes) -> tuple[str, str]:
    """(텍스트, 인코딩). 한국 공공 CSV는 cp949가 기본이고 UTF-8도 섞인다."""
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", "replace"), "utf-8-sig"
    for enc in ("cp949", "utf-8"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", "replace"), "cp949(replace)"


def _rows(source, label: str, *, verbose: bool = False, prefixes: tuple[str, ...] = ()):
    """CSV → (필드dict) 제너레이터. 헤더 유무를 스스로 판단한다.

    ``source``는 **바이트이거나 텍스트 스트림**이다. 스트림을 받는 쪽이 본 경로이고
    (이력본은 3.5GB라 메모리에 못 올린다), 바이트는 테스트와 작은 파일을 위한
    편의 경로다.

    ``prefixes``가 있으면 **CSV 파싱 전에 줄 앞자리로 먼저 버린다.** PNU가 첫
    필드이므로 줄의 앞자리가 곧 PNU 앞자리다. 이력본 3,210만 행에서 한 동만
    뽑을 때 이 선필터가 없으면 전 행을 csv.reader로 파싱해 622초가 걸린다 —
    99.99%가 버려질 행이다.

    ⚠️ 이 선필터는 **첫 필드가 PNU라는 가정**에 기댄다. 헤더가 다른 순서로
    오는 파일에서는 통째로 걸러져 0행이 된다. 그래서 헤더 매핑에서 pnu 인덱스가
    0이 아니면 선필터를 스스로 끈다.
    """
    if isinstance(source, (bytes, bytearray)):
        text, enc = _decode(bytes(source))
        handle = io.StringIO(text, newline="")
    else:
        handle = source
        enc = getattr(source, "encoding", "?")
    reader = csv.reader(handle)
    try:
        first = next(reader)
    except StopIteration:
        return

    colmap = _map_header(first)
    if colmap is None:
        # 헤더가 아니라 데이터 행이었다 → 컬럼정의서 순서로 물러난다.
        colmap = {name: i for i, name in enumerate(_POSITIONAL)}
        if verbose:
            print(f"  {label}: no header -> positional ({enc}), first row treated as data")
        pending = [first]
    else:
        if verbose:
            print(f"  {label}: header detected ({enc}) -> {sorted(colmap)}")
        pending = []

    def get(row: list[str], field: str) -> str:
        i = colmap.get(field)
        if i is None or i >= len(row):
            return ""
        return row[i]

    # 선필터는 PNU가 첫 필드일 때만 안전하다 — 아니면 조용히 0행이 된다.
    prefilter = prefixes if colmap.get("pnu") == 0 else ()
    if prefixes and not prefilter and verbose:
        print(f"  {label}: pnu가 첫 컬럼이 아니어서 줄 선필터를 끈다")

    if prefilter:
        # 헤더 다음부터는 줄 단위로 먼저 버린다. csv.reader를 다시 만드는 이유:
        # 파싱 비용이 이 함수 시간의 대부분이고, 버릴 줄에는 그 비용을 쓰지 않는다.
        def _kept_lines():
            for line in handle:
                if line.startswith(prefilter):
                    yield line

        reader = csv.reader(_kept_lines())

    for row in (r for src in (pending, reader) for r in src):
        if not row:
            continue
        yield {f: get(row, f) for f in _POSITIONAL if f in colmap} | {
            "_pnu": get(row, "pnu"),
            "_price": get(row, "price"),
        }


def _to_record(r: dict, src: str) -> tuple | None:
    pnu = (r.get("_pnu") or "").strip()
    # 특수지는 19자를 넘을 수 있다(컬럼정의서 주석) — 하한만 검사한다.
    if len(pnu) < 19 or not pnu.isdigit():
        return None
    price = _parse_int(r.get("_price", ""))
    year = _parse_int(r.get("base_year", ""))
    if price is None or price <= 0 or year is None or not (1980 <= year <= 2100):
        return None
    return (
        pnu, year, price,
        _parse_int(r.get("base_month", "")),
        _parse_bool(r.get("is_standard", "")),
        _parse_date(r.get("notice_date", "")),
        (r.get("ldong_code") or "").strip() or None,
        (r.get("ldong_name") or "").strip() or None,
        (r.get("jibun") or "").strip() or None,
        (r.get("special_code") or "").strip() or None,
        (r.get("special_name") or "").strip() or None,
        src,
    )


def inspect(paths: list[str], limit: int) -> None:
    """DB 없이 파일만 본다. 헤더가 미확인이므로 적재 전에 이걸 먼저 돌린다."""
    # 원문 CSV 행을 그대로 찍으므로 콘솔 코드페이지가 cp1252면 죽는다(실측).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    for path in paths:
        print(f"\n=== {path}")
        # 앞 몇 줄 보기와 전수 집계를 **따로 연다.** 한 스트림으로 둘을 하려면
        # 전문을 메모리에 담아야 하는데, 이력본은 3.5GB라 그럴 수 없다.
        for label, stream in _csv_streams(path):
            print(f"  {label}: enc={stream.encoding}")
            for i, line in enumerate(stream):
                if i >= limit:
                    break
                print(f"    {line.rstrip()[:300]}")
            break
        for label, stream in _csv_streams(path):
            ok = bad = 0
            years: dict[int, int] = {}
            for r in _rows(stream, label, verbose=True):
                rec = _to_record(r, "inspect")
                if rec is None:
                    bad += 1
                else:
                    ok += 1
                    years[rec[1]] = years.get(rec[1], 0) + 1
            ys = sorted(years)
            span = f"{ys[0]}~{ys[-1]} ({len(ys)}개 연도)" if ys else "없음"
            print(f"  -> valid {ok:,} / dropped {bad:,}; base_year {span}")
            if len(ys) > 1:
                print(f"     연도별: {[(y, years[y]) for y in ys]}")


def fetch(cookie: str, out_dir: str, sido: str) -> list[str]:
    """V-World에서 시도 단위 CSV를 연도별로 받는다. 세션 쿠키가 필요하다."""
    import os

    os.makedirs(out_dir, exist_ok=True)
    headers = {
        "Cookie": cookie,
        "Referer": VWORLD_LIST_URL,
        "User-Agent": "Mozilla/5.0",
    }
    with httpx.Client(headers=headers, timeout=300.0, follow_redirects=True) as client:
        resp = client.post(VWORLD_LIST_URL, data={
            "svcCde": "NA", "dsId": "6", "sidoCd": sido,
            "formatSelect": "CSV", "datPageSize": "100",
            "startDate": "1990-01-01", "endDate": "2026-12-31",
        })
        resp.raise_for_status()
        file_nos = sorted(set(re.findall(r"fileNo=(\d+)", resp.text)))
        if not file_nos:
            raise SystemExit(
                "no fileNo found in listing: cookie expired, or not logged in.\n"
                "Log in to V-World, then copy the Cookie header from DevTools > Network."
            )
        print(f"listing: {len(file_nos)} files")

        saved: list[str] = []
        for no in file_nos:
            r = client.get(VWORLD_FILE_URL,
                           params={"ds_id": VWORLD_CSV_DS_ID, "fileNo": no})
            if not r.content:
                print(f"  ! fileNo={no}: empty response (auth failed?) - stopping.")
                break
            dest = f"{out_dir}/land_price_{sido}_{no}.zip"
            with open(dest, "wb") as f:
                f.write(r.content)
            print(f"  {dest}: {len(r.content):,} bytes")
            saved.append(dest)
    return saved


def dedupe_by_key(rows: list[tuple]) -> tuple[list[tuple], int]:
    """(pnu, base_year)당 한 행만 남긴다 — **기준월이 작은 것 우선.**

    같은 필지·같은 연도에 두 행이 오는 경우가 있다. 중복이 아니라 **수시공시**다:
    정기공시는 1월 1일 기준(기준월=1)이고, 그 해에 분할·합병·지목변경이 있으면
    7월 1일 기준으로 다시 공시된다(기준월=7).

    실측(독산동 이력본): 280,797개 키 중 434개(0.15%)가 두 행이고, **가격이
    다르다** — 예: 2010년 1,410,000(1월) / 1,420,000(7월). 그냥 버릴 값이 아니다.

    **1월(정기)을 쓴다.** 패널의 종속변수는 연 1개여야 하고(PK가 그 뜻이다),
    0.15%의 필지에만 7월 기준을 쓰면 그 필지들만 기준일이 달라 연도별 상승률
    계산이 어긋난다. 일관성이 최신성보다 중요하다.

    ⚠️ **7월 공시가 있다는 것 자체가 정보다** — 그 해에 그 필지에 변동이 있었다는
    신호이므로 feature로 남길 값이다. 지금 스키마에는 담을 자리가 없어 버리고
    있고, 이건 별도 티켓으로 남긴다.

    이 함수가 없으면 적재가 죽는다: ``ON CONFLICT DO UPDATE``는 **한 문장 안에서
    같은 행을 두 번 건드릴 수 없다**(CardinalityViolation). 배치에 두 행이 함께
    들어가는 순간 터진다.
    """
    best: dict[tuple[str, int], tuple] = {}
    for r in rows:
        key = (r[0], r[1])
        cur = best.get(key)
        # base_month가 None이면 가장 나중으로 취급한다 — 정기공시가 있으면 그쪽을 쓴다.
        if cur is None or (r[3] if r[3] is not None else 99) < (
            cur[3] if cur[3] is not None else 99
        ):
            best[key] = r
    return list(best.values()), len(rows) - len(best)


def parse_years(spec: str) -> tuple[int, ...]:
    """"2021-2026" 또는 "2021,2023" → 연도 집합. 빈 문자열이면 필터 없음.

    범위와 목록을 섞어 쓸 수 있다("2015-2018,2026"). 잘못된 조각은 조용히
    무시하지 않고 예외를 낸다 — 오타로 필터가 통째로 사라지면 3천만 행이
    들어간다.
    """
    if not spec.strip():
        return ()
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return tuple(sorted(out))


def keeps(rec: tuple, ldongs: tuple[str, ...], years: tuple[int, ...]) -> bool:
    """이 행을 적재할까 — 지역·연도 필터.

    **필터를 파이썬에서 거는 이유**: 이력본은 서울 전역 1990~2023년 3,210만 행인데
    MVP는 두 권역 몇 개 동만 본다(신촌권·종로을지로권). 전부 적재하면 필요 없는
    3천만 행을 쓰고, 필지 단위 검증이 어려워진다 — 작은 범위로 end-to-end를 먼저
    통과시키는 것이 이 프로젝트의 원칙이다(PPT "MVP 범위와 실행 구조").

    ldong은 **PNU 앞자리 접두사**로 맞춘다. 10자리면 법정동, 5자리면 시군구다.
    비어 있으면 필터를 걸지 않는다(서울 전역).
    """
    if ldongs and not rec[0].startswith(ldongs):
        return False
    if years and rec[1] not in years:
        return False
    return True


def ingest(
    paths: list[str],
    src: str,
    *,
    ldongs: tuple[str, ...] = (),
    years: tuple[int, ...] = (),
) -> None:
    t0 = time.perf_counter()
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()

    total = skipped = filtered = deduped = 0
    for path in paths:
        file_rows = 0
        for label, stream in _csv_streams(path):
            batch: list[tuple] = []
            for r in _rows(stream, label, prefixes=ldongs):
                rec = _to_record(r, src)
                if rec is None:
                    skipped += 1
                    continue
                if not keeps(rec, ldongs, years):
                    filtered += 1
                    continue
                batch.append(rec)
                if len(batch) >= BATCH_SIZE:
                    rows_, dropped = dedupe_by_key(batch)
                    deduped += dropped
                    psycopg2.extras.execute_values(cur, _UPSERT_SQL, rows_, page_size=1000)
                    conn.commit()
                    file_rows += len(rows_)
                    batch = []
            if batch:
                rows_, dropped = dedupe_by_key(batch)
                deduped += dropped
                psycopg2.extras.execute_values(cur, _UPSERT_SQL, rows_, page_size=1000)
                conn.commit()
                file_rows += len(rows_)
        total += file_rows
        print(f"  {path}: {file_rows:,} rows")

    cur.execute(
        "SELECT count(*), count(DISTINCT pnu), min(base_year), max(base_year) FROM land_prices"
    )
    n, n_pnu, y0, y1 = cur.fetchone()
    conn.close()
    print(
        f"DONE in {time.perf_counter() - t0:.0f}s: upserted {total:,} "
        f"(형식오류 {skipped:,} · 범위밖 {filtered:,} · 수시공시 {deduped:,}); "
        f"DB total {n:,} rows / {n_pnu:,} parcels / {y0}~{y1}"
    )


def _expand(patterns: list[str]) -> list[str]:
    paths: list[str] = []
    for pattern in patterns:
        matched = sorted(glob.glob(pattern))
        if not matched:
            print(f"warning: no match for {pattern}", file=sys.stderr)
        paths.extend(matched)
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="land price CSV/zip paths or globs")
    parser.add_argument("--inspect", action="store_true",
                        help="check header/encoding/valid rows without DB (run before ingesting)")
    parser.add_argument("--inspect-lines", type=int, default=3)
    parser.add_argument("--fetch", action="store_true",
                        help="download from V-World (needs --cookie)")
    parser.add_argument("--cookie", default="", help="V-World session Cookie header")
    parser.add_argument("--out-dir", default=".", help="where --fetch saves files")
    parser.add_argument("--sido", default=SEOUL_SIDO_CD, help="sido code (Seoul=11)")
    parser.add_argument("--src", default="vworld_dsid6")
    parser.add_argument(
        "--ldong", action="append", default=[],
        help=(
            "PNU 접두사로 지역을 좁힌다(반복 가능). 10자리=법정동, 5자리=시군구. "
            "생략하면 파일 전체. MVP는 권역 몇 개 동만 넣는다 — 이력본은 서울 전역 "
            "3,210만 행이라 전부 넣으면 필지 단위 검증이 어려워진다."
        ),
    )
    parser.add_argument(
        "--years", default="",
        help="연도 범위/목록. 예: 2021-2026 또는 2021,2023,2026. 생략하면 전부",
    )
    args = parser.parse_args()

    targets = _expand(args.files) if args.files else []
    if args.fetch:
        if not args.cookie:
            raise SystemExit("--fetch requires --cookie (V-World login session)")
        targets = fetch(args.cookie, args.out_dir, args.sido) + targets
    if not targets:
        raise SystemExit("no input files")

    if args.inspect:
        inspect(targets, args.inspect_lines)
    else:
        ingest(
            targets, args.src,
            ldongs=tuple(args.ldong),
            years=parse_years(args.years),
        )
