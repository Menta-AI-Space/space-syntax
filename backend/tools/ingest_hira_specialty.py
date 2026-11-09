"""Ingest HIRA (심평원) quarterly 병원정보서비스 + 진료과목정보 into PostGIS.

Source: 보건의료빅데이터개방시스템 "전국 병의원 및 약국 현황" quarterly ZIP,
same family as tools/ingest_hira_hospitals.py but a different pair of files:
  1.병원정보서비스              → basic institution info + coords + 종별/의사수
  5.의료기관별상세정보서비스_03_진료과목정보 → per-institution 진료과목 (specialty) + 전문의수

Usage:
    python -m tools.ingest_hira_specialty --zip <path-to-quarterly-zip>
    (defaults to ../../data/hira/hira_hospitals_2026.6.zip relative to backend/)

NOTE on table ownership / why this isn't named ingest_hira_hospitals.py:
tools/ingest_hira_hospitals.py already exists and owns hira_hospitals' weekend-hours
columns (sat_start/sat_end/sun_start/sun_end/open_sat/open_sun/no_trmt_sun), populated
from a different HIRA zip (전국병의원약국현황, file 4.세부정보) and read live by
app/services/spatial_query.py::clinic_weekend_supply(). This script UPSERTs
(ON CONFLICT ykiho DO UPDATE) only the basic-institution + specialty-adjacent columns
below, deliberately omitting the weekend-hours columns from its UPDATE SET so that
re-running this ingest never clobbers the other pipeline's data. Quarterly refresh:
just rerun with the new zip — INSERT adds new institutions, UPDATE refreshes existing
ones, hira_hospital_subjects is fully TRUNCATE+reloaded (new table, no other consumer).
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterator

import openpyxl
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

DEFAULT_ZIP = Path(__file__).resolve().parents[2] / "data" / "hira" / "hira_hospitals_2026.6.zip"

BASIC_MARKER = "1.병원정보서비스"
SUBJECT_MARKER = "_03_진료과목정보"

DDL = """
ALTER TABLE hira_hospitals ADD COLUMN IF NOT EXISTS cl_code text;
ALTER TABLE hira_hospitals ADD COLUMN IF NOT EXISTS cl_name text;
ALTER TABLE hira_hospitals ADD COLUMN IF NOT EXISTS phone text;
ALTER TABLE hira_hospitals ADD COLUMN IF NOT EXISTS opened_date date;
ALTER TABLE hira_hospitals ADD COLUMN IF NOT EXISTS total_doctors integer;
ALTER TABLE hira_hospitals ADD COLUMN IF NOT EXISTS doctor_specialist_count integer;
ALTER TABLE hira_hospitals ADD COLUMN IF NOT EXISTS dentist_specialist_count integer;
ALTER TABLE hira_hospitals ADD COLUMN IF NOT EXISTS hanbang_specialist_count integer;

CREATE TABLE IF NOT EXISTS hira_hospital_subjects (
    id bigserial PRIMARY KEY,
    ykiho text NOT NULL,
    subject_code text NOT NULL,
    subject_name text NOT NULL DEFAULT '',
    specialist_count integer,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hira_hospital_subjects_ykiho_idx
    ON hira_hospital_subjects (ykiho);
CREATE INDEX IF NOT EXISTS hira_hospital_subjects_subject_code_idx
    ON hira_hospital_subjects (subject_code);

CREATE TABLE IF NOT EXISTS hira_ingest_meta (
    source text PRIMARY KEY,
    data_version text,
    loaded_at timestamptz,
    row_count integer,
    skipped_count integer
);
"""

_UPSERT_BASIC_SQL = """
INSERT INTO hira_hospitals (
    ykiho, name, kind, cl_code, cl_name, sido, sgg, addr, phone, opened_date,
    total_doctors, doctor_specialist_count, dentist_specialist_count,
    hanbang_specialist_count, lon, lat, geom, geom_5179
) VALUES %s
ON CONFLICT (ykiho) DO UPDATE SET
    name = EXCLUDED.name,
    kind = EXCLUDED.kind,
    cl_code = EXCLUDED.cl_code,
    cl_name = EXCLUDED.cl_name,
    sido = EXCLUDED.sido,
    sgg = EXCLUDED.sgg,
    addr = EXCLUDED.addr,
    phone = EXCLUDED.phone,
    opened_date = EXCLUDED.opened_date,
    total_doctors = EXCLUDED.total_doctors,
    doctor_specialist_count = EXCLUDED.doctor_specialist_count,
    dentist_specialist_count = EXCLUDED.dentist_specialist_count,
    hanbang_specialist_count = EXCLUDED.hanbang_specialist_count,
    lon = EXCLUDED.lon,
    lat = EXCLUDED.lat,
    geom = EXCLUDED.geom,
    geom_5179 = EXCLUDED.geom_5179
"""
_UPSERT_BASIC_TEMPLATE = (
    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
    " ST_SetSRID(ST_MakePoint(%s,%s),4326),"
    " ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s),4326),5179))"
)


def _decode_zip_entry_name(name: str) -> str:
    """HIRA ZIPs are sometimes EUC-KR bytes mis-flagged as CP437 by zipfile."""
    try:
        return name.encode("cp437").decode("euc-kr")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _find_entry(names: list[str], marker: str) -> str:
    for n in names:
        if marker in _decode_zip_entry_name(n) or marker in n:
            return n
    raise FileNotFoundError(f"zip entry containing {marker!r} not found")


def _data_version_from_entry_name(name: str) -> str:
    m = re.search(r"\((\d{4}\.\d{1,2})\.?\)", name)
    return m.group(1) if m else "unknown"


def _to_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_date(v):
    if v is None or v == "":
        return None
    if hasattr(v, "date"):
        return v.date()
    text = str(v).strip()
    return text or None


def _to_str(v) -> str | None:
    if v is None:
        return None
    text = str(v).strip()
    return text or None


def _header_index(header_row: tuple, wanted: list[str]) -> dict[str, int]:
    idx: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        name = str(cell).strip() if cell is not None else ""
        if name in wanted:
            idx[name] = i
    missing = [w for w in wanted if w not in idx]
    if missing:
        raise ValueError(f"Missing expected columns in xlsx: {missing}")
    return idx


def _iter_rows(zf: zipfile.ZipFile, entry: str, wanted: list[str]) -> Iterator[dict]:
    """Yield each data row as {column_name: cell_value}, robust to column reordering."""
    with zf.open(entry) as f:
        data = f.read()
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows_iter = ws.iter_rows(values_only=True)
        header = next(rows_iter)
        idx = _header_index(header, wanted)
        for row in rows_iter:
            if row is None or all(c is None for c in row):
                continue
            yield {col: row[idx[col]] for col in wanted}
    finally:
        wb.close()


_BASIC_COLS = [
    "암호화요양기호", "요양기관명", "종별코드", "종별코드명",
    "시도코드명", "시군구코드명", "주소", "전화번호", "개설일자",
    "총의사수", "의과전문의 인원수", "치과전문의 인원수", "한방전문의 인원수",
    "좌표(X)", "좌표(Y)",
]

_SUBJECT_COLS = ["암호화요양기호", "진료과목코드", "진료과목코드명", "과목별 전문의수"]


def _load_basic(zf: zipfile.ZipFile, entry: str) -> tuple[list[tuple], int]:
    rows: list[tuple] = []
    skipped = 0
    for d in _iter_rows(zf, entry, _BASIC_COLS):
        lon, lat = d["좌표(X)"], d["좌표(Y)"]
        if lon is None or lat is None or lon == "" or lat == "":
            skipped += 1
            continue
        try:
            lon, lat = float(lon), float(lat)
        except (TypeError, ValueError):
            skipped += 1
            continue
        ykiho, name = d["암호화요양기호"], d["요양기관명"]
        cl_code = _to_str(d["종별코드"])
        cl_name = _to_str(d["종별코드명"])
        sido = _to_str(d["시도코드명"])
        sgg = _to_str(d["시군구코드명"])
        addr = _to_str(d["주소"])
        phone = _to_str(d["전화번호"])
        opened_date = _to_date(d["개설일자"])
        total_doctors = _to_int(d["총의사수"])
        doctor_specialist = _to_int(d["의과전문의 인원수"])
        dentist_specialist = _to_int(d["치과전문의 인원수"])
        hanbang_specialist = _to_int(d["한방전문의 인원수"])
        rows.append((
            ykiho, name, cl_name, cl_code, cl_name, sido, sgg, addr, phone,
            opened_date, total_doctors, doctor_specialist, dentist_specialist,
            hanbang_specialist, lon, lat, lon, lat, lon, lat,
        ))
    return rows, skipped


def _load_subjects(zf: zipfile.ZipFile, entry: str) -> list[tuple]:
    rows: list[tuple] = []
    for d in _iter_rows(zf, entry, _SUBJECT_COLS):
        ykiho = d["암호화요양기호"]
        subject_code = _to_str(d["진료과목코드"])
        subject_name = _to_str(d["진료과목코드명"]) or ""
        specialist_count = _to_int(d["과목별 전문의수"]) or 0
        if not subject_code:
            continue
        rows.append((ykiho, subject_code, subject_name, specialist_count))
    return rows


def main(zip_path: str) -> int:
    zf = zipfile.ZipFile(zip_path)
    names = zf.namelist()
    basic_entry = _find_entry(names, BASIC_MARKER)
    subject_entry = _find_entry(names, SUBJECT_MARKER)
    data_version = _data_version_from_entry_name(_decode_zip_entry_name(basic_entry))

    print(f"Reading {basic_entry!r} ...")
    basic_rows, skipped = _load_basic(zf, basic_entry)
    print(f"Reading {subject_entry!r} ...")
    subject_rows = _load_subjects(zf, subject_entry)

    conn = psycopg2.connect(settings.database_url)
    with conn:
        with conn.cursor() as cur:
            cur.execute(DDL)

            execute_values(
                cur, _UPSERT_BASIC_SQL, basic_rows,
                template=_UPSERT_BASIC_TEMPLATE, page_size=2000,
            )

            cur.execute("TRUNCATE hira_hospital_subjects")
            execute_values(
                cur,
                "INSERT INTO hira_hospital_subjects "
                "(ykiho, subject_code, subject_name, specialist_count) VALUES %s",
                subject_rows,
                page_size=5000,
            )

            cur.execute(
                """
                INSERT INTO hira_ingest_meta (source, data_version, loaded_at, row_count, skipped_count)
                VALUES (%s, %s, NOW(), %s, %s)
                ON CONFLICT (source) DO UPDATE SET
                    data_version = EXCLUDED.data_version,
                    loaded_at = EXCLUDED.loaded_at,
                    row_count = EXCLUDED.row_count,
                    skipped_count = EXCLUDED.skipped_count
                """,
                ("hira_hospitals_basic", data_version, len(basic_rows), skipped),
            )
            cur.execute(
                """
                INSERT INTO hira_ingest_meta (source, data_version, loaded_at, row_count, skipped_count)
                VALUES (%s, %s, NOW(), %s, %s)
                ON CONFLICT (source) DO UPDATE SET
                    data_version = EXCLUDED.data_version,
                    loaded_at = EXCLUDED.loaded_at,
                    row_count = EXCLUDED.row_count,
                    skipped_count = EXCLUDED.skipped_count
                """,
                ("hira_hospital_subjects", data_version, len(subject_rows), 0),
            )
    conn.close()

    print(
        f"DONE data_version={data_version}: "
        f"hira_hospitals upserted {len(basic_rows):,} ({skipped} skipped, no/invalid coords); "
        f"hira_hospital_subjects reloaded {len(subject_rows):,}"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", default=str(DEFAULT_ZIP), help="Path to HIRA quarterly zip")
    args = parser.parse_args()
    raise SystemExit(main(args.zip))
