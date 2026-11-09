"""지방행정인허가(LOCALDATA) 업종별 CSV → biz_licenses 적재.

**왜 넣는가.** 상권 매출(`golmok_sales`)은 2021Q1부터·상권 단위뿐이라 「뜬 동네 안에서
어느 골목이 떴나」「구조 좋은 자리 가게가 더 오래 사나」「2021년 이전 사건(경의선숲길·
서울로7017)」을 잴 수 없었다. 인허가 자료는 업소마다 **인허가일·폐업일·좌표**가 있어
그 셋을 전부 연다. 검증 설계는 `C:\\dev\\space-syntax\\검증설계_뜨고진자리_2026-09-17.md`.

**출처.** https://file.localdata.go.kr/file/{slug}/info?orgCode=6110000_ALL (서울 전체).
localdata.go.kr 본 사이트는 이 PC에서 연결이 안 되지만 file 서브도메인은 된다 —
페이지를 한 번 열어 세션 쿠키를 받고 `/file/validate/download-count`를 거친 뒤 받는다
(Referer 필요). 파일은 **CP949** CSV, 좌표는 「보정계수 안 들어간 Bessel 중부원점
TM(EPSG:5174)」.

좌표 변환은 Bessel→GRS80 datum shift(towgs84)를 명시한 proj 문자열로 한다(아래
`PROJ_5174_BESSEL`). 2026-09-17 실측: 운영 PostGIS 3.4 카탈로그의 5174(towgs84 없음)로
변환해도 결과가 0.5m 안에서 같았다 — PROJ 가 한국 Bessel datum 을 스스로 처리한다.
그래도 명시해 두는 쪽이 PostGIS/PROJ 버전에 안 흔들린다. 검증 표본: 종로구 내수동 74 →
(126.9732, 37.5738) 광화문 일치, 용산동2가 45-9 → (126.9873, 37.5415) 해방촌 일치.

⚠️ **폐업 업소를 버리지 않는다.** 이 표의 존재 이유가 생존분석·개폐업 패널이다.
영업상태·폐업일을 그대로 넣고, 시점 절단은 실험 쪽에서 한다.

업종 라벨은 파일명 앞 slug로 정한다 (LOCALDATA 파일 slug → 한글):
  general_restaurants 일반음식점 · rest_cafes 휴게음식점 · bakeries 제과점 · beauty_salons 미용업
다른 업종은 `--biz-type`으로 준다.

Run:
  cd backend && .venv/Scripts/python -m tools.ingest_biz_licenses <dir-or-csv> [...]
  cd backend && .venv/Scripts/python -m tools.ingest_biz_licenses foo.csv --biz-type 노래연습장
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import date
from pathlib import Path

import psycopg2
import psycopg2.extras

from app.config import settings

PROJ_5174_BESSEL = (
    "+proj=tmerc +lat_0=38 +lon_0=127.0028902777778 +k=1 +x_0=200000 +y_0=500000 "
    "+ellps=bessel +towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43 +units=m +no_defs"
)

SLUG_TO_TYPE = {
    "general_restaurants": "일반음식점",
    "rest_cafes": "휴게음식점",
    "bakeries": "제과점",
    "beauty_salons": "미용업",
    "barber_shops": "이용업",
    "laundries": "세탁업",
    "karaoke_rooms": "노래연습장",
    "billiard_halls": "당구장",
    "pc_bangs": "PC방",
    "optical_shops": "안경업",
    "pharmacies": "약국",
    "clinics": "의원",
    "singing_bars": "단란주점",
    "entertainment_bars": "유흥주점",
    "tobacco_retailers": "담배소매업",
}

DDL = """
CREATE TABLE IF NOT EXISTS biz_licenses (
    mgt_no          text PRIMARY KEY,
    biz_type        text NOT NULL,
    org_code        text,
    name            text,
    uptae           text,
    status          text,
    detail_status   text,
    license_date    date,
    close_date      date,
    area_m2         double precision,
    addr_jibun      text,
    addr_road       text,
    x_5174          double precision,
    y_5174          double precision,
    geom_5179       geometry(Point, 5179),
    src             text NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS biz_licenses_geom_idx ON biz_licenses USING GIST (geom_5179);
CREATE INDEX IF NOT EXISTS biz_licenses_type_idx ON biz_licenses (biz_type);
CREATE INDEX IF NOT EXISTS biz_licenses_lic_idx ON biz_licenses (license_date);
CREATE INDEX IF NOT EXISTS biz_licenses_close_idx ON biz_licenses (close_date);
"""

INSERT = """
INSERT INTO biz_licenses
    (mgt_no, biz_type, org_code, name, uptae, status, detail_status,
     license_date, close_date, area_m2, addr_jibun, addr_road, x_5174, y_5174, src)
VALUES %s
ON CONFLICT (mgt_no) DO UPDATE SET
    biz_type = EXCLUDED.biz_type,
    name = EXCLUDED.name,
    uptae = EXCLUDED.uptae,
    status = EXCLUDED.status,
    detail_status = EXCLUDED.detail_status,
    license_date = EXCLUDED.license_date,
    close_date = EXCLUDED.close_date,
    area_m2 = EXCLUDED.area_m2,
    addr_jibun = EXCLUDED.addr_jibun,
    addr_road = EXCLUDED.addr_road,
    x_5174 = EXCLUDED.x_5174,
    y_5174 = EXCLUDED.y_5174,
    geom_5179 = NULL,
    src = EXCLUDED.src,
    ingested_at = NOW()
"""

GEOM_UPDATE = f"""
UPDATE biz_licenses
SET geom_5179 = ST_Transform(ST_SetSRID(ST_MakePoint(x_5174, y_5174), 5174),
                             %s, 5179)
WHERE geom_5179 IS NULL AND x_5174 IS NOT NULL AND y_5174 IS NOT NULL
  AND x_5174 BETWEEN 100000 AND 300000 AND y_5174 BETWEEN 350000 AND 550000
"""

COLS = {
    "org_code": "개방자치단체코드",
    "mgt_no": "관리번호",
    "license_date": "인허가일자",
    "status": "영업상태명",
    "detail_status": "상세영업상태명",
    "close_date": "폐업일자",
    "area": "소재지면적",
    "name": "사업장명",
    "uptae": "업태구분명",
    "addr_road": "도로명주소",
    "addr_jibun": "지번주소",
    "x": "좌표정보(X)",
    "y": "좌표정보(Y)",
}


def _open_csv(path: Path) -> io.TextIOWrapper:
    raw = path.read_bytes()
    for enc in ("cp949", "utf-8-sig"):
        try:
            text = raw.decode(enc)
            return io.StringIO(text)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"{path}: cp949/utf-8 어느 쪽으로도 디코드되지 않는다")


def _date(v: str) -> date | None:
    v = (v or "").strip()
    if len(v) < 8:
        return None
    try:
        if "-" in v:
            return date.fromisoformat(v[:10])
        return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
    except ValueError:
        return None


def _float(v: str) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _biz_type_for(path: Path, override: str | None) -> str:
    if override:
        return override
    stem = path.stem
    for slug, label in SLUG_TO_TYPE.items():
        if stem.startswith(slug):
            return label
    raise SystemExit(f"{path.name}: 업종을 파일명에서 못 정한다 — --biz-type 을 줘라")


def _rows(path: Path, biz_type: str):
    reader = csv.reader(_open_csv(path))
    header = next(reader)
    idx = {h.strip(): i for i, h in enumerate(header)}
    missing = [k for k in COLS.values() if k not in idx]
    if missing:
        raise SystemExit(f"{path.name}: 헤더에 {missing} 가 없다")

    def g(row: list[str], key: str) -> str:
        i = idx[COLS[key]]
        return row[i] if i < len(row) else ""

    src = path.name
    for row in reader:
        mgt = g(row, "mgt_no").strip()
        if not mgt:
            continue
        yield (
            mgt,
            biz_type,
            g(row, "org_code").strip() or None,
            g(row, "name").strip() or None,
            g(row, "uptae").strip() or None,
            g(row, "status").strip() or None,
            g(row, "detail_status").strip() or None,
            _date(g(row, "license_date")),
            _date(g(row, "close_date")),
            _float(g(row, "area")),
            g(row, "addr_jibun").strip() or None,
            g(row, "addr_road").strip() or None,
            _float(g(row, "x")),
            _float(g(row, "y")),
            src,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="CSV 파일 또는 폴더(*_6110000.csv)")
    ap.add_argument("--biz-type", default=None, help="파일명으로 못 정할 때 업종 라벨")
    ap.add_argument("--batch", type=int, default=5000)
    args = ap.parse_args()

    files: list[Path] = []
    for p in args.paths:
        pth = Path(p)
        if pth.is_dir():
            files.extend(sorted(pth.glob("*.csv")))
        else:
            files.append(pth)
    if not files:
        print("적재할 CSV가 없다", file=sys.stderr)
        return 2

    conn = psycopg2.connect(settings.database_url)
    conn.autocommit = False
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
        for f in files:
            biz_type = _biz_type_for(f, args.biz_type)
            n = 0
            with conn.cursor() as cur:
                batch: list[tuple] = []
                for rec in _rows(f, biz_type):
                    batch.append(rec)
                    if len(batch) >= args.batch:
                        psycopg2.extras.execute_values(cur, INSERT, batch, page_size=args.batch)
                        n += len(batch)
                        batch.clear()
                        if n % 50000 == 0:
                            print(f"  {f.name}: {n:,}", flush=True)
                if batch:
                    psycopg2.extras.execute_values(cur, INSERT, batch, page_size=args.batch)
                    n += len(batch)
            conn.commit()
            total += n
            print(f"{f.name} → {biz_type}: {n:,}행", flush=True)

        with conn.cursor() as cur:
            cur.execute(GEOM_UPDATE, (PROJ_5174_BESSEL,))
            print(f"좌표 변환: {cur.rowcount:,}행", flush=True)
            cur.execute(
                """
                SELECT biz_type, COUNT(*), COUNT(geom_5179),
                       MIN(license_date), MAX(license_date),
                       SUM(CASE WHEN close_date IS NULL THEN 1 ELSE 0 END) AS open_now
                FROM biz_licenses GROUP BY biz_type ORDER BY 2 DESC
                """
            )
            for row in cur.fetchall():
                print("  ", row)
        conn.commit()
    finally:
        conn.close()
    print(f"총 {total:,}행 적재")
    return 0


if __name__ == "__main__":
    sys.exit(main())
