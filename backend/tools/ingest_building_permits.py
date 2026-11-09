"""건축HUB 「건축인허가 기본개요」 CSV → building_permits 적재.

data.go.kr 「국토교통부_건축인허가 기본개요」(15044695) 파일데이터, 또는 건축HUB
OpenAPI(15136267)로 받은 CSV를 읽는다. 연도별로 나뉘어 배포되므로 여러 파일을
차례로 넣어도 안전하다(permit_key 기준 upsert).

**왜 이 데이터를 넣는가.** 저평가 판정의 근거가 길 구조뿐이면 「옆에 새 건물이
들어선다」는 변화를 반영하지 못한다. 인허가는 준공 2~3년 «전»에 나므로 이건
후행지표가 아니라 **선행지표**이고, 통제변수로 넣어야 잔차를 저평가라고 부를 수
있다.

⚠️ **PNU는 원본에 통짜로 없다.** 다섯 컬럼을 이어 붙여 만든다:

    sigungu_cd(5) + bjdong_cd(5) + plat_gb_cd(1) + bun(4) + ji(4) = 19자리

bun·ji는 0으로 왼쪽 채움하고, plat_gb_cd는 1=대지 2=산이다. 조립에 실패한 행도
**버리지 않고 pnu=NULL로 남긴다** — 몇 %를 못 붙였는지 세야 이 데이터의 커버리지를
정직하게 말할 수 있다. 마지막에 그 비율을 출력한다.

⚠️ **날짜를 가공하지 않는다.** 허가일·착공일·사용승인일을 원본 그대로 넣고,
시점 절단은 compute_parcel_market_signals.py에서 base_year 기준으로 한 곳에서만
한다. 여기서 미리 자르면 어느 시점 기준인지 추적할 수 없게 된다.

Run:
  cd backend && .venv/Scripts/python -m tools.ingest_building_permits "C:\\...\\건축인허가_2024.csv"
  cd backend && .venv/Scripts/python -m tools.ingest_building_permits data/*.csv --sgg 11
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from datetime import date, datetime
from pathlib import Path

import psycopg2
import psycopg2.extras

from app.config import settings

DDL = """
CREATE TABLE IF NOT EXISTS building_permits (
    permit_key      text PRIMARY KEY,
    pnu             text,
    sigungu_cd      text NOT NULL,
    bjdong_cd       text,
    plat_gb_cd      text,
    bun             text,
    ji              text,
    plat_plc        text,
    bld_nm          text,
    permit_date     date,
    start_date      date,
    approval_date   date,
    plat_area       double precision,
    arch_area       double precision,
    tot_area        double precision,
    vl_rat          double precision,
    bc_rat          double precision,
    main_purps_nm   text,
    arch_gb_nm      text,
    src             text NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT NOW()
);
ALTER TABLE building_permits ADD COLUMN IF NOT EXISTS geom_5179 geometry(Point, 5179);
CREATE INDEX IF NOT EXISTS building_permits_pnu_idx ON building_permits (pnu);
CREATE INDEX IF NOT EXISTS building_permits_permit_date_idx ON building_permits (permit_date);
CREATE INDEX IF NOT EXISTS building_permits_sgg_idx ON building_permits (sigungu_cd);
CREATE INDEX IF NOT EXISTS building_permits_geom_idx ON building_permits USING GIST (geom_5179);
"""

# 배포 연도에 따라 헤더가 국문/영문으로 갈린다. 후보를 모두 둔다.
COLUMNS: dict[str, tuple[str, ...]] = {
    "sigungu_cd": ("시군구코드", "sigunguCd"),
    "bjdong_cd": ("법정동코드", "bjdongCd"),
    "plat_gb_cd": ("대지구분코드", "platGbCd"),
    "bun": ("번", "bun"),
    "ji": ("지", "ji"),
    "plat_plc": ("대지위치", "platPlc"),
    "bld_nm": ("건물명", "bldNm"),
    "permit_date": ("건축허가일", "archPmsDay", "허가일"),
    "start_date": ("실제착공일", "realStcnsDay", "착공일"),
    "approval_date": ("사용승인일", "useAprDay"),
    "plat_area": ("대지면적", "platArea"),
    "arch_area": ("건축면적", "archArea"),
    "tot_area": ("연면적", "totArea"),
    "vl_rat": ("용적률", "vlRat"),
    "bc_rat": ("건폐율", "bcRat"),
    "main_purps_nm": ("주용도명", "mainPurpsCdNm"),
    "arch_gb_nm": ("건축구분명", "archGbCdNm"),
    "mgm_pk": ("관리허가대장관리번호", "mgmPmsrgstPk", "관리번호"),
    "crtn_day": ("생성일자", "crtnDay"),
}

INSERT = """
INSERT INTO building_permits
    (permit_key, pnu, sigungu_cd, bjdong_cd, plat_gb_cd, bun, ji, plat_plc,
     bld_nm, permit_date, start_date, approval_date, plat_area, arch_area,
     tot_area, vl_rat, bc_rat, main_purps_nm, arch_gb_nm, src)
VALUES %s
ON CONFLICT (permit_key) DO UPDATE SET
    pnu = EXCLUDED.pnu,
    permit_date = EXCLUDED.permit_date,
    start_date = EXCLUDED.start_date,
    approval_date = EXCLUDED.approval_date,
    tot_area = EXCLUDED.tot_area,
    main_purps_nm = EXCLUDED.main_purps_nm,
    arch_gb_nm = EXCLUDED.arch_gb_nm,
    src = EXCLUDED.src,
    ingested_at = NOW()
"""

# 필지 중심점을 복사해 반경 집계를 빠르게 만든다. 매 조회마다 parcel_centroids와
# 조인하면 필지 110만 × 인허가 수십만이라 느리다.
FILL_GEOM = """
UPDATE building_permits b
   SET geom_5179 = p.geom_5179
  FROM parcel_centroids p
 WHERE b.pnu = p.pnu
   AND b.geom_5179 IS NULL
"""


def _pick(row: dict[str, str], key: str) -> str:
    """헤더 후보 중 실제로 존재하는 것을 찾아 값을 돌려준다."""
    for cand in COLUMNS[key]:
        if cand in row and row[cand] not in (None, ""):
            return str(row[cand]).strip()
    return ""


def make_pnu(sigungu: str, bjdong: str, plat_gb: str, bun: str, ji: str) -> str | None:
    """PNU 19자리를 조립한다. 하나라도 모양이 안 맞으면 None.

    ⚠️ 조용히 잘라내거나 채우지 않는다. 예를 들어 시군구코드가 4자리로 온
    행을 0으로 채워 맞추면 «다른 자치구의 필지»가 되어 버린다.
    """
    sigungu = re.sub(r"\D", "", sigungu)
    bjdong = re.sub(r"\D", "", bjdong)
    plat_gb = re.sub(r"\D", "", plat_gb) or "1"
    bun = re.sub(r"\D", "", bun)
    ji = re.sub(r"\D", "", ji)

    if len(sigungu) != 5 or len(bjdong) != 5 or len(plat_gb) != 1:
        return None
    if not bun or len(bun) > 4 or len(ji) > 4:
        return None
    return f"{sigungu}{bjdong}{plat_gb}{bun.zfill(4)}{(ji or '0').zfill(4)}"


def parse_date(raw: str) -> date | None:
    """YYYYMMDD / YYYY-MM-DD 를 받는다. 0·공백·미래 날짜는 None."""
    s = re.sub(r"\D", "", raw or "")
    if len(s) != 8 or s.startswith("0000"):
        return None
    try:
        d = datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None
    # 원본에 2099년 같은 값이 섞여 들어온다. 그대로 두면 시점 절단이 망가진다.
    if d.year < 1960 or d.year > date.today().year + 1:
        return None
    return d


def parse_num(raw: str) -> float | None:
    s = (raw or "").replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_rows(path: Path, sgg_prefix: str | None):
    """CSV 한 파일을 읽어 INSERT 튜플을 뱉는다."""
    # 공공데이터 CSV는 cp949가 많고 UTF-8 BOM도 섞인다.
    for encoding in ("utf-8-sig", "cp949"):
        try:
            fh = path.open(encoding=encoding, newline="")
            fh.readline()
            fh.seek(0)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"인코딩을 판별하지 못했다: {path}")

    with fh:
        for row in csv.DictReader(fh):
            sigungu = _pick(row, "sigungu_cd")
            if sgg_prefix and not sigungu.startswith(sgg_prefix):
                continue
            bjdong = _pick(row, "bjdong_cd")
            plat_gb = _pick(row, "plat_gb_cd")
            bun, ji = _pick(row, "bun"), _pick(row, "ji")
            pnu = make_pnu(sigungu, bjdong, plat_gb, bun, ji)

            mgm = _pick(row, "mgm_pk")
            crtn = _pick(row, "crtn_day")
            seed = f"{sigungu}|{mgm}|{crtn}|{bjdong}|{bun}|{ji}"
            permit_key = hashlib.sha1(seed.encode()).hexdigest()

            yield (
                permit_key, pnu, sigungu, bjdong, plat_gb, bun, ji,
                _pick(row, "plat_plc") or None,
                _pick(row, "bld_nm") or None,
                parse_date(_pick(row, "permit_date")),
                parse_date(_pick(row, "start_date")),
                parse_date(_pick(row, "approval_date")),
                parse_num(_pick(row, "plat_area")),
                parse_num(_pick(row, "arch_area")),
                parse_num(_pick(row, "tot_area")),
                parse_num(_pick(row, "vl_rat")),
                parse_num(_pick(row, "bc_rat")),
                _pick(row, "main_purps_nm") or None,
                _pick(row, "arch_gb_nm") or None,
                path.name,
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", nargs="+", help="건축인허가 기본개요 CSV 경로")
    ap.add_argument("--sgg", default=None,
                    help="시군구코드 접두어로 거른다 (예: 11 = 서울)")
    ap.add_argument("--batch", type=int, default=5000)
    args = ap.parse_args()

    conn = psycopg2.connect(settings.database_url)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(DDL)

    total = 0
    buf: list[tuple] = []
    for pattern in args.csv:
        for path in sorted(Path().glob(pattern)) or [Path(pattern)]:
            if not path.exists():
                print(f"  ✗ 없음: {path}")
                continue
            n = 0
            for rec in read_rows(path, args.sgg):
                buf.append(rec)
                n += 1
                if len(buf) >= args.batch:
                    psycopg2.extras.execute_values(cur, INSERT, buf, page_size=1000)
                    buf.clear()
            total += n
            print(f"  ✓ {path.name}: {n:,}행")
    if buf:
        psycopg2.extras.execute_values(cur, INSERT, buf, page_size=1000)

    cur.execute(FILL_GEOM)
    filled = cur.rowcount
    conn.commit()

    # 커버리지를 반드시 보고한다 — 몇 %를 필지에 못 붙였는지 모르면
    # 이 데이터로 만든 통제변수의 신뢰도를 말할 수 없다.
    cur.execute("SELECT count(*), count(pnu), count(geom_5179) FROM building_permits")
    rows, with_pnu, with_geom = cur.fetchone()
    print(f"\n적재 {total:,}행 · 표 전체 {rows:,}행")
    print(f"  PNU 조립 성공 {with_pnu:,} ({with_pnu / max(rows, 1):.1%})")
    print(f"  좌표 결합 성공 {with_geom:,} ({with_geom / max(rows, 1):.1%})  (이번에 {filled:,}건 채움)")
    if with_geom < rows * 0.7:
        print("  ⚠️ 좌표 결합률이 70% 미만이다 — parcel_centroids 적재 범위를 확인할 것.")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
