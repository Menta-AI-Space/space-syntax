"""R-ONE 상업용부동산 임대동향조사 통계표(xlsx) → reb_rent_stats 적재.

상권별 공실률·임대료를 property_type(중대형/소규모/집합)별로 최신 분기 값만 뽑는다.
시트 맵(공실률/임대료)은 row3 제목으로 검증하고 어긋나면 실패한다.
여러 분기 파일을 함께 넘기면 각 파일의 분기가 모두 적재된다(재실행 안전).

시트 구조: row4 헤더 [지역구분(1)=시도, 지역구분(2)=상권명, 항목, 코드, 2013.1Q ...],
데이터 행마다 상권 하나, 마지막 컬럼이 최신 분기. 임대료 단위 천원/㎡ → 원/㎡(×1000).

Run: cd backend && .venv/Scripts/python -m tools.ingest_reb_rent_stats \
       "C:\\...\\2026년 1분기 ... 통계표.xlsx" "C:\\...\\2025년 4분기 ... 통계표.xlsx"
"""

from __future__ import annotations

import argparse
import time

import openpyxl
import psycopg2
import psycopg2.extras

from app.config import settings

# property_type → {metric: (sheet, expected_row3_substring)}
SHEET_MAP: dict[str, dict[str, tuple[str, str]]] = {
    "중대형": {"vacancy": ("203", "공실률"), "rent": ("204", "임대료")},
    "소규모": {"vacancy": ("303", "공실률"), "rent": ("304", "임대료")},
    "집합": {"vacancy": ("403", "공실률"), "rent": ("404", "임대료")},
}
HEADER_ROW = 4
DATA_START = 5

_UPSERT_SQL = """
INSERT INTO reb_rent_stats (
  sido_nm, sanggwon_nm, region_cd, property_type, quarter,
  rent_krw_per_sqm, vacancy_pct, ingested_at
)
VALUES %s
ON CONFLICT ON CONSTRAINT reb_rent_stats_key DO UPDATE SET
  region_cd = EXCLUDED.region_cd,
  rent_krw_per_sqm = COALESCE(EXCLUDED.rent_krw_per_sqm, reb_rent_stats.rent_krw_per_sqm),
  vacancy_pct = COALESCE(EXCLUDED.vacancy_pct, reb_rent_stats.vacancy_pct),
  ingested_at = NOW()
"""
_VALUES_TEMPLATE = "(%s, %s, %s, %s, %s, %s, %s, NOW())"


def _row3(ws) -> str:
    return str(next(ws.iter_rows(min_row=3, max_row=3, values_only=True))[0] or "").strip()


def _last_quarter_col(ws) -> tuple[int, str]:
    """마지막 분기 컬럼의 (인덱스 0-based, 라벨). 헤더는 '2026.1Q' 형태."""
    hdr = next(ws.iter_rows(min_row=HEADER_ROW, max_row=HEADER_ROW, values_only=True))
    last_idx, last_label = -1, ""
    for i, c in enumerate(hdr):
        if c and isinstance(c, str) and c.strip().endswith("Q"):
            last_idx, last_label = i, c.strip()
    if last_idx < 0:
        raise SystemExit("분기 컬럼(...Q)을 찾지 못함")
    return last_idx, last_label


def _read_metric(ws, col_idx: int) -> dict[tuple[str, str], tuple[str, float | None]]:
    """(시도, 상권명) → (코드, 최신분기 값). 시도/상권/합계 요약행은 값만 저장."""
    out: dict[tuple[str, str], tuple[str, float | None]] = {}
    for row in ws.iter_rows(min_row=DATA_START, values_only=True):
        sido = str(row[0]).strip() if row[0] else ""
        sanggwon = str(row[1]).strip() if row[1] else ""
        code = str(row[3]).strip() if len(row) > 3 and row[3] else ""
        if not sido or not sanggwon:
            continue
        raw = row[col_idx] if col_idx < len(row) else None
        val: float | None = None
        if raw not in (None, ""):
            try:
                val = float(raw)
            except (TypeError, ValueError):
                val = None
        out[(sido, sanggwon)] = (code, val)
    return out


def ingest(paths: list[str]) -> None:
    t0 = time.perf_counter()
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()

    total = 0
    for path in paths:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows: list[tuple] = []
        quarter_seen = ""
        for ptype, metrics in SHEET_MAP.items():
            vac_sheet, vac_kw = metrics["vacancy"]
            rent_sheet, rent_kw = metrics["rent"]
            ws_v, ws_r = wb[vac_sheet], wb[rent_sheet]
            if vac_kw not in _row3(ws_v) or rent_kw not in _row3(ws_r):
                raise SystemExit(
                    f"{path} {ptype}: 시트 제목 불일치 "
                    f"(공실={_row3(ws_v)!r}, 임대료={_row3(ws_r)!r}) — 시트맵 확인"
                )
            v_idx, v_q = _last_quarter_col(ws_v)
            r_idx, r_q = _last_quarter_col(ws_r)
            quarter = r_q
            quarter_seen = quarter
            vac = _read_metric(ws_v, v_idx)
            rent = _read_metric(ws_r, r_idx)

            keys = set(vac) | set(rent)
            for (sido, sanggwon) in keys:
                code = (rent.get((sido, sanggwon)) or vac.get((sido, sanggwon)) or ("", None))[0]
                rent_val = (rent.get((sido, sanggwon)) or ("", None))[1]
                vac_val = (vac.get((sido, sanggwon)) or ("", None))[1]
                rows.append((
                    sido, sanggwon, code, ptype, quarter,
                    rent_val * 1000 if rent_val is not None else None,  # 천원/㎡ → 원/㎡
                    vac_val,
                ))
        psycopg2.extras.execute_values(
            cur, _UPSERT_SQL, rows, template=_VALUES_TEMPLATE, page_size=500
        )
        conn.commit()
        total += len(rows)
        print(f"  {path.split(chr(92))[-1]}: {len(rows)} rows (분기 {quarter_seen})")

    cur.execute(
        "SELECT property_type, count(*) FILTER (WHERE rent_krw_per_sqm IS NOT NULL), "
        "count(*) FILTER (WHERE vacancy_pct IS NOT NULL) "
        "FROM reb_rent_stats GROUP BY 1 ORDER BY 1"
    )
    for pt, nr, nv in cur.fetchall():
        print(f"    {pt}: 임대료 {nr}, 공실률 {nv}")
    conn.close()
    print(f"DONE in {time.perf_counter() - t0:.0f}s: upserted {total} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", nargs="+", help="분기별 통계표 xlsx 경로")
    args = parser.parse_args()
    ingest(args.xlsx)
