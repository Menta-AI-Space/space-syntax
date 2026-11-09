"""Synthetic-CSV unit tests for tools/ingest_land_prices.py parsing.

The real V-World AL_D151 header could not be observed before writing the
ingester (download is behind a login), so the parser detects columns by Korean
keyword and falls back to the column-definition order. These tests pin both
paths down, plus the cp949/UTF-8-BOM encodings the Korean public CSVs use.
"""

from __future__ import annotations

import csv
import io
import zipfile

from tools.ingest_land_prices import (
    _UPSERT_SQL,
    _csv_streams,
    _decode,
    _map_header,
    _parse_bool,
    _parse_date,
    _parse_int,
    _rows,
    _to_record,
)

HEADER = [
    "고유번호", "법정동코드", "법정동명", "특수지구분코드", "특수지구분명",
    "지번", "기준연도", "기준월", "공시지가", "공시일자",
    "표준지여부", "데이터기준일자", "원천시도시군구코드",
]

ROWS = [
    # plain row
    ["1111010100100010000", "1111010100", "청운동", "1", "일반", "1-1",
     "2025", "1", "12345000", "20250531", "N", "20250601", "11110"],
    # thousands separator in price, dashed date, standard-parcel flag
    ["1111010100100020000", "1111010100", "청운동", "1", "일반", "1-2",
     "2025", "1", "9,876,000", "2025-05-31", "Y", "20250601", "11110"],
    # malformed PNU -> dropped
    ["badpnu", "1111010100", "청운동", "1", "일반", "1-3",
     "2025", "1", "100", "20250531", "N", "20250601", "11110"],
]


def _csv_bytes(rows, *, header=True, encoding="cp949", bom=False) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    if header:
        writer.writerow(HEADER)
    writer.writerows(rows)
    raw = buf.getvalue().encode(encoding)
    return b"\xef\xbb\xbf" + raw if bom else raw


def _records(raw: bytes):
    return [r for r in (_to_record(row, "test") for row in _rows(raw, "t")) if r]


def test_header_is_mapped_by_keyword():
    colmap = _map_header(HEADER)
    assert colmap is not None
    # "지번" must not be swallowed by "공시지가" (both contain 지) and vice versa.
    assert colmap["price"] == HEADER.index("공시지가")
    assert colmap["jibun"] == HEADER.index("지번")
    assert colmap["pnu"] == 0
    assert colmap["is_standard"] == HEADER.index("표준지여부")


def test_data_row_is_not_mistaken_for_header():
    """A headerless file must not have its first data row consumed as a header."""
    assert _map_header(ROWS[0]) is None


def test_parses_rows_with_header():
    recs = _records(_csv_bytes(ROWS))
    assert len(recs) == 2  # third row has a malformed PNU

    pnu, year, price, month, is_standard, notice = recs[0][:6]
    assert (pnu, year, price, month) == ("1111010100100010000", 2025, 12345000, 1)
    assert is_standard is False
    assert notice == "2025-05-31"
    assert recs[0][7] == "청운동"
    assert recs[0][8] == "1-1"

    assert recs[1][2] == 9876000  # comma-separated price
    assert recs[1][4] is True  # 표준지여부 = Y
    assert recs[1][5] == "2025-05-31"  # already-dashed date


def test_falls_back_to_positional_when_header_absent():
    recs = _records(_csv_bytes(ROWS, header=False))
    assert len(recs) == 2
    assert recs[0][:3] == ("1111010100100010000", 2025, 12345000)


def test_reads_csv_inside_zip(tmp_path):
    zip_path = tmp_path / "AL_11_D151_20250101.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("AL_11_D151_20250101.csv", _csv_bytes(ROWS))

    recs = [
        rec
        for _, raw in _csv_streams(str(zip_path))
        for rec in (_to_record(row, "test") for row in _rows(raw, "z"))
        if rec
    ]
    assert len(recs) == 2


def test_handles_utf8_bom():
    _, encoding = _decode(_csv_bytes(ROWS, encoding="utf-8", bom=True))
    assert encoding == "utf-8-sig"
    assert len(_records(_csv_bytes(ROWS, encoding="utf-8", bom=True))) == 2


def test_rejects_out_of_range_year_and_nonpositive_price():
    bad_year = list(ROWS[0])
    bad_year[HEADER.index("기준연도")] = "1899"
    assert _records(_csv_bytes([bad_year])) == []

    zero_price = list(ROWS[0])
    zero_price[HEADER.index("공시지가")] = "0"
    assert _records(_csv_bytes([zero_price])) == []


def test_record_arity_matches_insert_columns():
    """A record must supply exactly one value per INSERT column.

    Regression: the INSERT list once carried a trailing ingested_at that
    _to_record never produced, so every real ingest died on the first batch
    while the parser tests all passed.
    """
    columns = _UPSERT_SQL.split("INSERT INTO land_prices (", 1)[1].split(")", 1)[0]
    n_columns = len([c for c in columns.replace("\n", " ").split(",") if c.strip()])

    rec = _to_record(next(iter(_rows(_csv_bytes([ROWS[0]]), "t"))), "test")
    assert rec is not None
    assert len(rec) == n_columns


def test_scalar_parsers():
    assert _parse_int("1,234") == 1234
    assert _parse_int("") is None
    assert _parse_int("n/a") is None
    assert _parse_date("20250531") == "2025-05-31"
    assert _parse_date("2025-05-31") == "2025-05-31"
    assert _parse_date("2025") is None
    assert _parse_bool("Y") is True
    assert _parse_bool("표준지") is True
    assert _parse_bool("일반") is False
    assert _parse_bool("") is None


# ── 이력본(3,210만 행) 스트리밍과 지역·연도 필터 ─────────────────────────
#
# 개별공시지가 이력본(AL_11_D151_*)은 한 파일이 1990~2023년 3,210만 행 ·
# 압축 해제 3.5GB다. 예전에는 zip 멤버를 zf.read()로 통째로 읽었는데 그 방식으로는
# 죽는다(사용자 디스크가 꽉 차서 압축조차 못 푸는 상황이 실제로 있었다).
# 아래는 스트리밍 경로와 선필터가 계약을 지키는지 고정한다.

import pytest

from tools.ingest_land_prices import _sniff_encoding, keeps, parse_years


def test_parse_years_range_and_list():
    assert parse_years("2021-2024") == (2021, 2022, 2023, 2024)
    assert parse_years("2015-2016,2026") == (2015, 2016, 2026)
    assert parse_years("2026") == (2026,)
    assert parse_years("") == ()
    assert parse_years("   ") == ()


def test_parse_years_rejects_garbage():
    """오타로 필터가 조용히 사라지면 3,210만 행이 들어간다 — 예외를 내야 한다."""
    with pytest.raises(ValueError):
        parse_years("2021~2024")


def test_keeps_filters_by_pnu_prefix_and_year():
    rec = ("1141010200100010000", 2023, 700)
    assert keeps(rec, ("1141010200",), (2021, 2022, 2023))
    assert not keeps(rec, ("1111010100",), ())      # 다른 동
    assert not keeps(rec, (), (2026,))              # 다른 연도
    assert keeps(rec, ("11410",), ())               # 시군구 5자리도 접두사로 맞는다
    assert keeps(rec, (), ())                       # 필터 없으면 전부 통과


def test_sniff_encoding_survives_split_multibyte():
    """조각 끝에서 한글이 잘려도 cp949를 cp949로 알아봐야 한다.

    증분 디코더를 쓰지 않으면 정상 cp949 파일을 "cp949 아님"으로 오판하고
    전부 깨진 글자로 적재한다.
    """
    body = "서울특별시 강서구 화곡동".encode("cp949")
    assert _sniff_encoding(body[:-1]) == "cp949"
    assert _sniff_encoding("서울".encode("utf-8")) in ("cp949", "utf-8")
    assert _sniff_encoding(b"\xef\xbb\xbfpnu,price") == "utf-8-sig"


def _history_zip(tmp_path):
    """이력본 모양의 zip — 헤더 12컬럼, 같은 PNU가 여러 연도로 반복된다."""
    hdr = ["고유번호", "법정동코드", "법정동명", "특수지구분코드", "특수지구분명",
           "지번", "기준연도", "기준월", "공시지가", "공시일자",
           "표준지여부", "데이터기준일자"]
    rows = []
    for pnu, ldong in (("1141010200100010000", "1141010200"),
                       ("1111010100100010000", "1111010100")):
        for year in (2021, 2022, 2023):
            rows.append([pnu, ldong, "테스트동", "1", "일반", "1-1",
                         str(year), "1", str(700000 + year), f"{year}-05-31",
                         "N", "2023-07-12"])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(hdr)
    w.writerows(rows)
    path = tmp_path / "AL_11_D151_test.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("AL_11_D151_test.csv", buf.getvalue().encode("cp949"))
    return path


def test_streams_zip_without_reading_whole_member(tmp_path):
    """멤버를 메모리에 올리지 않는다 — 텍스트 스트림이 나와야 한다."""
    (label, stream), = list(_csv_streams(str(_history_zip(tmp_path))))
    assert stream.encoding == "cp949"
    # bytes가 아니라 lazily 읽는 스트림이다.
    assert hasattr(stream, "read") and not isinstance(stream, (bytes, bytearray))


def test_history_file_yields_same_pnu_across_years(tmp_path):
    """패널의 핵심 계약: 같은 PNU가 연도별로 반복되어 나온다."""
    recs = []
    for label, stream in _csv_streams(str(_history_zip(tmp_path))):
        recs += [r for r in (_to_record(x, "t") for x in _rows(stream, label)) if r]
    years = sorted(y for p, y, *_ in recs if p == "1141010200100010000")
    assert years == [2021, 2022, 2023]


def test_line_prefilter_matches_unfiltered_result(tmp_path):
    """선필터를 켜도 결과가 같아야 한다 — 실측에서 622초를 103초로 줄인 경로다.

    같지 않으면 성능이 아니라 데이터 손실이다.
    """
    zp = str(_history_zip(tmp_path))
    want = []
    for label, stream in _csv_streams(zp):
        want += [r for r in (_to_record(x, "t") for x in _rows(stream, label))
                 if r and keeps(r, ("1141010200",), ())]
    got = []
    for label, stream in _csv_streams(zp):
        got += [r for r in (_to_record(x, "t") for x in _rows(stream, label,
                                                             prefixes=("1141010200",)))
                if r and keeps(r, ("1141010200",), ())]
    assert got == want
    assert len(got) == 3, "한 동 × 3개 연도"


def test_prefilter_disabled_when_pnu_is_not_first_column(tmp_path):
    """선필터는 PNU가 첫 필드라는 가정에 기댄다 — 아니면 조용히 0행이 된다.

    그 경우 스스로 꺼져야 한다. 안 꺼지면 컬럼 순서가 다른 파일에서 적재가
    통째로 비고, 아무도 알아채지 못한다.
    """
    hdr = ["기준연도", "고유번호", "공시지가"]
    rows = [["2023", "1141010200100010000", "750000"]]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(hdr)
    w.writerows(rows)
    path = tmp_path / "odd.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("odd.csv", buf.getvalue().encode("cp949"))

    got = []
    for label, stream in _csv_streams(str(path)):
        got += [r for r in (_to_record(x, "t") for x in _rows(stream, label,
                                                             prefixes=("1141010200",))) if r]
    assert len(got) == 1, "PNU가 첫 컬럼이 아니면 선필터를 끄고 전 행을 봐야 한다"


# ── 수시공시 (같은 필지·연도에 두 행) ────────────────────────────────────


def _rec(pnu, year, price, month):
    """_to_record가 돌려주는 모양. 4번째가 base_month다."""
    return (pnu, year, price, month, False, f"{year}-05-31",
            pnu[:10], "테스트동", "1-1", "1", "일반", "t")


def test_dedupe_keeps_regular_notice_not_the_later_one():
    """정기공시(1월)를 남긴다 — 최신성보다 기준일 일관성이 중요하다.

    실측(독산동 이력본): 280,797개 키 중 434개가 두 행이고 **가격이 다르다**
    (2010년 1,410,000원/1월 vs 1,420,000원/7월). 0.15%의 필지에만 7월 기준을
    쓰면 그 필지들만 기준일이 달라 연도별 상승률 계산이 어긋난다.
    """
    from tools.ingest_land_prices import dedupe_by_key

    rows = [_rec("1154510200100590027", 2010, 1_410_000, 1),
            _rec("1154510200100590027", 2010, 1_420_000, 7)]
    kept, dropped = dedupe_by_key(rows)
    assert dropped == 1
    assert kept[0][2] == 1_410_000, "1월 정기공시를 남겨야 한다"

    # 순서가 뒤집혀 와도 같은 결과여야 한다 — 파일 순서에 기대지 않는다.
    kept2, _ = dedupe_by_key(list(reversed(rows)))
    assert kept2[0][2] == 1_410_000


def test_dedupe_treats_missing_month_as_last():
    """기준월이 없는 행은 가장 나중으로 본다 — 정기공시가 있으면 그쪽을 쓴다."""
    from tools.ingest_land_prices import dedupe_by_key

    kept, dropped = dedupe_by_key([
        _rec("1111010100100010000", 2020, 500, None),
        _rec("1111010100100010000", 2020, 600, 1),
    ])
    assert dropped == 1
    assert kept[0][2] == 600


def test_dedupe_keeps_different_years_and_parcels():
    from tools.ingest_land_prices import dedupe_by_key

    rows = [_rec("A" * 19, 2020, 1, 1), _rec("A" * 19, 2021, 2, 1),
            _rec("B" * 19, 2020, 3, 1)]
    kept, dropped = dedupe_by_key(rows)
    assert dropped == 0 and len(kept) == 3


def test_upsert_guards_against_overwriting_regular_with_midyear():
    """배치가 갈려도 결과가 같아야 한다 — SQL이 기준월을 직접 막는다.

    dedupe_by_key는 **한 배치 안**만 본다. 1월 행과 7월 행이 다른 배치로 갈리면
    두 개의 INSERT 문이 되고, ON CONFLICT는 나중 문장이 이긴다 — 어느 쪽이
    나중인지는 배치 크기에 달린 우연이다. WHERE 절이 그 우연을 없앤다.
    """
    from tools.ingest_land_prices import _UPSERT_SQL

    assert "COALESCE(EXCLUDED.base_month, 99) <= COALESCE(land_prices.base_month, 99)" \
        in _UPSERT_SQL


def test_upsert_cannot_touch_same_row_twice_in_one_statement():
    """왜 dedupe가 필요한지 — 계약을 문서가 아니라 테스트에 남긴다.

    ON CONFLICT DO UPDATE는 한 문장 안에서 같은 행을 두 번 건드리면
    CardinalityViolation으로 죽는다. 실제로 이 적재가 그렇게 죽었다.
    """
    from tools.ingest_land_prices import dedupe_by_key

    batch = [_rec("1154510200100470003", 2012, 1_930_000, 1),
             _rec("1154510200100470003", 2012, 1_930_000, 7)]
    kept, _ = dedupe_by_key(batch)
    keys = [(r[0], r[1]) for r in kept]
    assert len(keys) == len(set(keys)), "배치에 같은 (pnu, base_year)가 남으면 적재가 죽는다"
