"""대규모점포 인허가 CSV → large_stores 적재.

「전국대규모및준대규모점포 표준데이터」(data.go.kr 15114138) 또는 LOCALDATA
「생활_대규모점포」(15045013), 서울 열린데이터 OA-16096 중 무엇이든 받는다.
헤더가 배포처마다 달라 후보를 여러 개 둔다.

**왜 넣는가.** 백화점·복합쇼핑몰·대형마트가 들어오면 주변 상권이 통째로 바뀐다.
길 구조로는 설명되지 않는 변화이므로 통제하지 않으면 그 효과가 목의 공로로
계산된다.

⚠️ **영업상태와 폐업일을 반드시 읽는다.** 폐업한 점포를 "지금 있는 것"으로 세면
반대 방향으로 틀린다. 시점 절단은 compute_parcel_market_signals.py 가 하지만,
그러려면 여기서 날짜를 «버리지 않고» 넣어 두어야 한다.

⚠️ 좌표는 배포본에 따라 EPSG:5174(중부원점)로 오는 경우가 있다. 그대로 5179로
넣으면 수백 m 어긋난다 — --srid 로 원본 좌표계를 명시하고 DB에서 변환한다.

Run:
  cd backend && .venv/Scripts/python -m tools.ingest_large_stores "C:\\...\\대규모점포.csv"
  cd backend && .venv/Scripts/python -m tools.ingest_large_stores data.csv --srid 5174 --sgg 11
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
CREATE TABLE IF NOT EXISTS large_stores (
    store_key       text PRIMARY KEY,
    store_nm        text NOT NULL,
    store_type      text,
    biz_status      text,
    license_date    date,
    close_date      date,
    addr            text,
    sigungu_cd      text,
    store_area      double precision,
    src             text NOT NULL,
    geom_5179       geometry(Point, 5179),
    ingested_at     timestamptz NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS large_stores_geom_idx ON large_stores USING GIST (geom_5179);
CREATE INDEX IF NOT EXISTS large_stores_date_idx ON large_stores (license_date);
"""

COLUMNS: dict[str, tuple[str, ...]] = {
    "mgt_no": ("관리번호", "MGTNO", "개방자치단체코드"),
    "store_nm": ("사업장명", "BPLCNM", "점포명", "상호명"),
    "store_type": ("업태구분명", "UPTAENM", "점포구분", "대규모점포종류명"),
    "biz_status": ("영업상태명", "TRDSTATENM", "영업상태구분코드"),
    "license_date": ("인허가일자", "APVPERMYMD", "허가일자"),
    "close_date": ("폐업일자", "DCBYMD", "인허가취소일자"),
    "addr": ("소재지전체주소", "SITEWHLADDR", "도로명전체주소", "RDNWHLADDR"),
    "store_area": ("시설면적", "매장면적", "FACLTSIZE", "SITEAREA"),
    "x": ("좌표정보(X)", "좌표정보(x)", "X", "경도"),
    "y": ("좌표정보(Y)", "좌표정보(y)", "Y", "위도"),
}

INSERT = """
INSERT INTO large_stores
    (store_key, store_nm, store_type, biz_status, license_date, close_date,
     addr, sigungu_cd, store_area, src, geom_5179)
VALUES %s
ON CONFLICT (store_key) DO UPDATE SET
    biz_status = EXCLUDED.biz_status,
    close_date = EXCLUDED.close_date,
    store_area = EXCLUDED.store_area,
    geom_5179 = COALESCE(EXCLUDED.geom_5179, large_stores.geom_5179),
    src = EXCLUDED.src,
    ingested_at = NOW()
"""

TEMPLATE = ("(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), %s), 5179))")

# 폐업을 뜻하는 말이 배포처마다 다르다. 하나라도 걸리면 폐업으로 본다.
CLOSED_WORDS = ("폐업", "취소", "말소", "휴업", "직권")


def _pick(row: dict[str, str], key: str) -> str:
    for cand in COLUMNS[key]:
        if cand in row and row[cand] not in (None, ""):
            return str(row[cand]).strip()
    return ""


def parse_date(raw: str) -> date | None:
    s = re.sub(r"\D", "", raw or "")
    if len(s) != 8 or s.startswith("0000"):
        return None
    try:
        d = datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None
    return d if 1960 <= d.year <= date.today().year + 1 else None


def parse_num(raw: str) -> float | None:
    s = (raw or "").replace(",", "").strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def is_closed(status: str) -> bool:
    return any(w in (status or "") for w in CLOSED_WORDS)


def read_rows(path: Path, srid: int, sgg_prefix: str | None):
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
            name = _pick(row, "store_nm")
            if not name:
                continue
            addr = _pick(row, "addr")
            if sgg_prefix and sgg_prefix not in addr[:12]:
                continue

            x, y = parse_num(_pick(row, "x")), parse_num(_pick(row, "y"))
            status = _pick(row, "biz_status")
            close = parse_date(_pick(row, "close_date"))
            # 상태가 폐업인데 폐업일이 비어 있는 행이 흔하다. 그대로 두면 시점
            # 절단에서 «영업 중»으로 잡힌다 — 인허가일로라도 채워 둔다.
            license_date = parse_date(_pick(row, "license_date"))
            if close is None and is_closed(status):
                close = license_date

            seed = f"{_pick(row, 'mgt_no')}|{name}|{addr}"
            yield (
                hashlib.sha1(seed.encode()).hexdigest(),
                name,
                _pick(row, "store_type") or None,
                status or None,
                license_date,
                close,
                addr or None,
                None,
                parse_num(_pick(row, "store_area")),
                path.name,
                x, y, srid,
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--srid", type=int, default=4326,
                    help="원본 좌표계. 표준데이터는 4326, LOCALDATA 구버전은 5174")
    ap.add_argument("--sgg", default=None, help="주소 접두어로 거른다 (예: 서울)")
    args = ap.parse_args()

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute(DDL)

    total = 0
    for pattern in args.csv:
        for path in sorted(Path().glob(pattern)) or [Path(pattern)]:
            if not path.exists():
                print(f"  ✗ 없음: {path}")
                continue
            rows = list(read_rows(path, args.srid, args.sgg))
            if rows:
                psycopg2.extras.execute_values(cur, INSERT, rows,
                                               template=TEMPLATE, page_size=500)
            total += len(rows)
            print(f"  ✓ {path.name}: {len(rows):,}행")
    conn.commit()

    cur.execute("SELECT count(*), count(*) FILTER (WHERE close_date IS NULL), "
                "count(geom_5179) FROM large_stores")
    n, alive, geo = cur.fetchone()
    print(f"\n표 전체 {n:,}행 · 영업 중 {alive:,} · 좌표 있음 {geo:,} ({geo / max(n, 1):.1%})")
    if geo < n * 0.8:
        print("  ⚠️ 좌표 결합률이 낮다 — --srid 를 잘못 줬을 수 있다.")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
