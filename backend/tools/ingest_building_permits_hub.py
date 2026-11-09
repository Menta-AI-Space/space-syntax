"""건축HUB 「대용량 제공 서비스」 건축인허가 기본개요 → building_permits 적재.

`ingest_building_permits.py`는 data.go.kr 파일데이터의 **헤더 있는 CSV**를 읽는다.
건축HUB 대용량 파일은 형식이 다르다 (2026-09-04 실측):

  · 파일   zip 안에 `mart_kcy_01.txt` 하나 (압축 436MB → 1.7GB)
  · 인코딩 UTF-8 BOM
  · 구분자 **파이프(|)**, **헤더 없음**, 41개 필드 고정
  · 전국 6,463,123행 / 그중 서울 550,018행 (8.5%)

⚠️ **헤더가 없으므로 위치로만 읽는다.** 컬럼 순서는 data.go.kr 15044695의 속성정보
순서와 일치하고, 다음 두 가지로 교차검증했다(2026-09-04):
  · 38번을 건축허가일로 볼 때 `허가일 ≤ 사용승인일`이 **99.9%**(n=249,051)
  · 채움률이 허가일 98.3% > 사용승인일 62.9% > 실제착공일 58.4% 로 사건 순서와 맞는다
**배포본이 바뀌어 필드 수가 41이 아니면 즉시 멈춘다** — 조용히 어긋난 컬럼을 읽는 것이
가장 위험하다.

⚠️ 날짜는 원본 그대로 넣는다. 시점 절단은 compute_parcel_market_signals.py에서
base_year 기준으로 **한 곳에서만** 한다(§10 시점 누수 규율).
원본에 2105·2206·2995 같은 오타가 섞여 있는데 `parse_date`가 걸러낸다.

Run:
    cd backend && .venv/Scripts/python -m tools.ingest_building_permits_hub \\
        "D:/멘타 AI/건축인허가/국토교통부_건축인허가_기본개요+(2026년+05월).zip" --sgg 11
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ingest_building_permits import (          # noqa: E402
    DDL, FILL_GEOM, INSERT, make_pnu, parse_date, parse_num,
)


def db_url() -> str:
    """backend/.env 의 DATABASE_URL 을 «직접» 읽는다.

    ⚠️ app.config.settings 를 쓰면 안 된다. config 는 backend/.env → 저장소 루트
    .env → 그 상위 순으로 읽고 «나중 파일이 이긴다». 저장소 루트에 localhost 를
    가리키는 .env 가 있어서, backend/.env 에 운영 DSN 을 넣어도 덮인다
    (2026-09-04 실측 — 이 스크립트가 localhost:5432 로 붙어 실패했다).
    """
    env = Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("backend/.env 에 DATABASE_URL 이 없다 — bash scripts/setup-laptop.sh")

NFIELDS = 41
# 위치 → 뜻 (data.go.kr 15044695 속성정보 순서)
MGM, PLAT_PLC, BLD_NM = 0, 1, 2
SGG, BJD, PLAT_GB, BUN, JI = 3, 4, 5, 6, 7
ARCH_GB_NM = 20
PLAT_AREA, ARCH_AREA, BC_RAT, TOT_AREA = 21, 22, 23, 24
VL_RAT = 26
MAIN_PURPS_NM = 30
START_DATE, PERMIT_DATE, APPROVAL_DATE = 37, 38, 39


def rows(zip_path: Path, sgg_prefix: str | None):
    z = zipfile.ZipFile(zip_path)
    inner = [n for n in z.namelist() if n.lower().endswith((".txt", ".csv"))]
    if len(inner) != 1:
        raise SystemExit(f"zip 안 파일이 {len(inner)}개다 — 확인 필요: {inner}")
    src = f"{zip_path.name}:{inner[0]}"
    bad = 0
    with z.open(inner[0]) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace")
        for lineno, line in enumerate(text, 1):
            r = line.rstrip("\n").rstrip("\r").split("|")
            if len(r) != NFIELDS:
                bad += 1
                if bad <= 3:
                    print(f"  ⚠️ {lineno}행 필드 {len(r)}개 (기대 {NFIELDS}) — 건너뜀")
                if bad > 1000:
                    raise SystemExit("필드 수가 어긋나는 행이 1,000개를 넘었다 — "
                                     "배포본 형식이 바뀌었을 수 있다. 중단한다.")
                continue
            if sgg_prefix and not r[SGG].startswith(sgg_prefix):
                continue
            yield r, src
    if bad:
        print(f"  형식 불일치로 건너뛴 행: {bad:,}")


# 건축HUB 대지구분코드는 PNU 표준과 «다르다» (2026-09-04 실측).
# PNU 11번째 자리는 1=대지 2=산인데, 건축HUB는 0=대지 1=산으로 온다.
# 추측이 아니라 parcel_centroids 매칭률로 확정했다 — 서울 55만 건 기준:
#   코드 0 (545,368건) → PNU '1'로 두면 499,101 매칭 / '2'로 두면 1,761
#   코드 1 (  1,719건) → PNU '1' 396 / '2' 996        ← '2'가 2.5배
#   코드 2 (  1,838건) → 어느 쪽도 안 붙는다(22 / 3). 특수 행으로 보고 그대로 둔다.
# ⚠️ 이 표를 안 거치면 좌표가 0.09%(488건)밖에 안 붙는다.
PLAT_GB_TO_PNU = {"0": "1", "1": "2"}


def to_tuple(r: list[str], src: str):
    gb = PLAT_GB_TO_PNU.get(r[PLAT_GB].strip(), r[PLAT_GB].strip())
    pnu = make_pnu(r[SGG], r[BJD], gb, r[BUN], r[JI])
    key = (r[MGM] or "").strip()
    if not key:
        return None
    return (
        key, pnu, r[SGG].strip(), r[BJD].strip(), r[PLAT_GB].strip(),
        r[BUN].strip(), r[JI].strip(), r[PLAT_PLC].strip() or None,
        r[BLD_NM].strip() or None,
        parse_date(r[PERMIT_DATE]), parse_date(r[START_DATE]), parse_date(r[APPROVAL_DATE]),
        parse_num(r[PLAT_AREA]), parse_num(r[ARCH_AREA]), parse_num(r[TOT_AREA]),
        parse_num(r[VL_RAT]), parse_num(r[BC_RAT]),
        r[MAIN_PURPS_NM].strip() or None, r[ARCH_GB_NM].strip() or None, src,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("zip", help="건축HUB 대용량 zip 경로")
    ap.add_argument("--sgg", default=None, help="시군구코드 앞자리 필터 (서울=11)")
    ap.add_argument("--batch", type=int, default=20000)
    ap.add_argument("--dsn", default=None, help="접속 문자열 직접 지정")
    ap.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 통계만")
    args = ap.parse_args()

    zp = Path(args.zip)
    if not zp.exists():
        raise SystemExit(f"파일이 없다: {zp}")

    conn = None
    if not args.dry_run:
        dsn = args.dsn or db_url()
        host = dsn.split('@')[-1].split('/')[0]
        print(f"  접속: {host}")
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
        print("  building_permits 테이블 준비 완료")

    buf, total, no_pnu, no_date = [], 0, 0, 0
    for r, src in rows(zp, args.sgg):
        t = to_tuple(r, src)
        if t is None:
            continue
        total += 1
        if t[1] is None:
            no_pnu += 1
        if t[9] is None:
            no_date += 1
        buf.append(t)
        if len(buf) >= args.batch:
            if conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, INSERT, buf, page_size=5000)
                conn.commit()
            print(f"  ... {total:,}행")
            buf.clear()

    if buf and conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, INSERT, buf, page_size=5000)
        conn.commit()

    print(f"\n적재 대상 {total:,}행")
    print(f"  PNU 조립 실패 {no_pnu:,} ({no_pnu/max(total,1)*100:.2f}%) — 버리지 않고 NULL로 남긴다")
    print(f"  허가일 없음   {no_date:,} ({no_date/max(total,1)*100:.2f}%)")

    if conn:
        with conn.cursor() as cur:
            print("  좌표 채우는 중 (parcel_centroids 조인)...")
            cur.execute(FILL_GEOM)
            filled = cur.rowcount
            conn.commit()
            cur.execute("SELECT count(*), count(pnu), count(geom_5179), "
                        "min(permit_date), max(permit_date) FROM building_permits")
            n, npnu, ngeom, dmin, dmax = cur.fetchone()
        conn.close()
        print(f"  좌표 채움 {filled:,}행")
        print(f"\nDB 최종: {n:,}행 · PNU {npnu:,} · 좌표 {ngeom:,} · 허가일 {dmin} ~ {dmax}")
    else:
        print("  (dry-run — DB에 쓰지 않음)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
