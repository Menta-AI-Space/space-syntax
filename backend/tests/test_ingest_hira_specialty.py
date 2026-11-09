"""Synthetic-xlsx unit tests for tools/ingest_hira_specialty.py parsing.

Builds a tiny in-memory ZIP (mirrors the real HIRA quarterly bundle's file-naming
convention) instead of committing the 61MB real snapshot.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

import openpyxl
import pytest

from tools.ingest_hira_specialty import (
    _data_version_from_entry_name,
    _decode_zip_entry_name,
    _find_entry,
    _load_basic,
    _load_subjects,
)

BASIC_HEADER = [
    "암호화요양기호", "요양기관명", "종별코드", "종별코드명",
    "시도코드", "시도코드명", "시군구코드", "시군구코드명", "읍면동", "우편번호",
    "주소", "전화번호", "병원홈페이지", "개설일자", "총의사수",
    "의과일반의 인원수", "의과인턴 인원수", "의과레지던트 인원수", "의과전문의 인원수",
    "치과일반의 인원수", "치과인턴 인원수", "치과레지던트 인원수", "치과전문의 인원수",
    "한방일반의 인원수", "한방인턴 인원수", "한방레지던트 인원수", "한방전문의 인원수",
    "조산사 인원수", "좌표(X)", "좌표(Y)",
]

SUBJECT_HEADER = ["암호화요양기호", "요양기관명", "진료과목코드", "진료과목코드명", "과목별 전문의수", "선택진료 의사수"]


def _xlsx_bytes(header: list[str], rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def sample_zip() -> zipfile.ZipFile:
    basic_rows = [
        [
            "YKIHO1", "정형외과의원", "31", "의원", "110000", "서울", "110001", "서대문구",
            "신촌동", "03722", "서울 서대문구 신촌로 1", "02-1234-5678", None,
            date(2010, 3, 1), 2, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            126.9370, 37.5559,
        ],
        [
            # missing coordinates → should be skipped, counted
            "YKIHO2", "무좌표의원", "31", "의원", "110000", "서울", "110001", "서대문구",
            "신촌동", "03722", "서울 서대문구 신촌로 2", "02-1234-9999", None,
            date(2015, 1, 1), 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            None, None,
        ],
    ]
    subject_rows = [
        ["YKIHO1", "정형외과의원", "05", "정형외과", 2, 0],
        ["YKIHO1", "정형외과의원", "21", "재활의학과", 0, 0],
        ["YKIHO2", "무좌표의원", "01", "내과", 1, 0],
    ]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "전국 병의원 및 약국 현황 2026.6/1.병원정보서비스(2026.6.).xlsx",
            _xlsx_bytes(BASIC_HEADER, basic_rows),
        )
        zf.writestr(
            "전국 병의원 및 약국 현황 2026.6/5.의료기관별상세정보서비스_03_진료과목정보(2026.6.).xlsx",
            _xlsx_bytes(SUBJECT_HEADER, subject_rows),
        )
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_find_entry_locates_basic_and_subject_files(sample_zip):
    names = sample_zip.namelist()
    basic = _find_entry(names, "1.병원정보서비스")
    subject = _find_entry(names, "_03_진료과목정보")
    assert "1.병원정보서비스" in basic
    assert "_03_진료과목정보" in subject


def test_data_version_extracted_from_entry_name(sample_zip):
    basic = _find_entry(sample_zip.namelist(), "1.병원정보서비스")
    assert _data_version_from_entry_name(_decode_zip_entry_name(basic)) == "2026.6"


def test_load_basic_skips_missing_coordinates(sample_zip):
    basic_entry = _find_entry(sample_zip.namelist(), "1.병원정보서비스")
    rows, skipped = _load_basic(sample_zip, basic_entry)

    assert skipped == 1  # YKIHO2 has no coordinates
    assert len(rows) == 1
    row = rows[0]
    # (ykiho, name, kind, cl_code, cl_name, sido, sgg, addr, phone, opened_date,
    #  total_doctors, doctor_specialist, dentist_specialist, hanbang_specialist,
    #  lon, lat, lon, lat, lon, lat)
    assert row[0] == "YKIHO1"
    assert row[1] == "정형외과의원"
    assert row[3] == "31"  # cl_code = 종별코드
    assert row[4] == "의원"  # cl_name = 종별코드명
    assert row[10] == 2  # total_doctors
    assert row[11] == 2  # doctor_specialist_count
    assert row[14] == pytest.approx(126.9370)
    assert row[15] == pytest.approx(37.5559)


def test_load_subjects_parses_all_rows_including_orphan_ykiho(sample_zip):
    subject_entry = _find_entry(sample_zip.namelist(), "_03_진료과목정보")
    rows = _load_subjects(sample_zip, subject_entry)

    assert len(rows) == 3
    by_code = {(r[0], r[1]): r for r in rows}
    assert by_code[("YKIHO1", "05")][2] == "정형외과"
    assert by_code[("YKIHO1", "05")][3] == 2
    assert by_code[("YKIHO1", "21")][3] == 0
    # subjects for institutions skipped from hira_hospitals (bad coords) are still
    # parsed here — the join at query time simply won't match anything spatially.
    assert ("YKIHO2", "01") in by_code
