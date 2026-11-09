"""토지특성 적재 — 네트워크 없이 도는 부분.

API 응답 모양은 2026-08-22 실측, D195 CSV 헤더는 2026-08-24 실측(파일 6개 전수).
"""

import csv
import io
import zipfile

import pytest

from tools.ingest_parcel_characteristics import (
    EARLIEST_YEAR,
    csv_row_to_row,
    dedupe_rows,
    fetch_page,
    parse_years,
    resolve_csv_columns,
    rows_from_csv,
    to_row,
)

# 라이브 응답 1건 (종로구 평동 1-1, 2024년)
LIVE_ITEM = {
    "tpgrphHgCodeNm": "지정되지않음",
    "stdrYear": "2024",
    "lndcgrCodeNm": "도로",
    "roadSideCodeNm": "지정되지않음",
    "pblntfPclnd": "3762000",
    "prposArea2Nm": "지정되지않음",
    "prposArea1Nm": "일반상업지역",
    "ladUseSittnNm": "도로 등",
    "tpgrphFrmCodeNm": "지정되지않음",
    "regstrSeCodeNm": "일반",
    "lndpclAr": "23.6",
    "ldCode": "1111017700",
    "ldCodeNm": "서울특별시 종로구 평동",
    "mnnmSlno": "1-1",
    "pnu": "1111017700100010001",
    "stdrMt": "01",
}

# 튜플 자리 (가독성용)
PNU, YEAR, ZONE1, ZONE2, ROADSIDE, CATEGORY, AREA = 0, 1, 2, 3, 4, 5, 6
USE_SIT, TOPO_H, TOPO_F, PRICE = 7, 8, 9, 10


def test_live_item_maps_the_control_variables():
    r = to_row(LIVE_ITEM, "vworld_api")
    assert r[PNU] == "1111017700100010001"
    assert r[YEAR] == 2024
    assert r[ZONE1] == "일반상업지역"      # 가장 중요한 통제변수
    assert r[CATEGORY] == "도로"           # 지목
    assert r[AREA] == 23.6                 # 면적
    assert r[PRICE] == 3762000             # 공시지가 원/㎡


def test_unspecified_markers_become_none_not_a_fake_category():
    """'지정되지않음'을 문자열로 저장하면 가짜 용도지역으로 계층이 갈라진다."""
    r = to_row(LIVE_ITEM, "s")
    assert r[ROADSIDE] is None     # roadSideCodeNm = 지정되지않음
    assert r[ZONE2] is None        # prposArea2Nm  = 지정되지않음
    assert r[TOPO_H] is None
    assert r[TOPO_F] is None


def test_real_road_side_grade_is_kept():
    r = to_row({**LIVE_ITEM, "roadSideCodeNm": "광대소각"}, "s")
    assert r[ROADSIDE] == "광대소각"


def test_zone_name_is_stored_verbatim_not_normalized():
    """정규화는 조회 시점(land_use_zones.py)에 한다 — 적재는 원문을 지킨다."""
    r = to_row({**LIVE_ITEM, "prposArea1Nm": "제2종 일반주거지역"}, "s")
    assert r[ZONE1] == "제2종 일반주거지역"


@pytest.mark.parametrize("field", ["pnu", "stdrYear"])
def test_row_without_key_fields_is_dropped(field):
    assert to_row({**LIVE_ITEM, field: ""}, "s") is None


def test_non_numeric_area_becomes_none_rather_than_crashing():
    assert to_row({**LIVE_ITEM, "lndpclAr": "-"}, "s")[AREA] is None


def test_missing_price_is_allowed():
    """공시지가는 land_prices가 정본이다 — 여기 없다고 행을 버리지 않는다."""
    r = to_row({**LIVE_ITEM, "pblntfPclnd": ""}, "s")
    assert r is not None and r[PRICE] is None


# ── 연도 하한 (공시지가와 다르다) ──────────────────────────────────────

def test_floor_is_2015_not_2009():
    """토지특성은 2015년부터다. 공시지가(2009~)와 범위가 다르다."""
    assert EARLIEST_YEAR == 2015
    assert parse_years(["2009..2017"]) == [2015, 2016, 2017]


def test_range_and_list_both_parse():
    assert parse_years(["2015..2017", "2020"]) == [2015, 2016, 2017, 2020]


# ── 중복 (한 필지가 여러 행) ───────────────────────────────────────────

def test_dedupe_collapses_repeated_parcel_rows():
    a = to_row(LIVE_ITEM, "s")
    b = to_row({**LIVE_ITEM, "stdrMt": "1"}, "s")
    kept, removed = dedupe_rows([a, b])
    assert len(kept) == 1 and removed == 1


def test_dedupe_keeps_distinct_years():
    a = to_row(LIVE_ITEM, "s")
    b = to_row({**LIVE_ITEM, "stdrYear": "2025"}, "s")
    assert dedupe_rows([a, b]) == ([a, b], 0)


# ── 응답 봉투 ──────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payloads):
        self._payloads = payloads
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return _FakeResponse(self._payloads[len(self.calls) - 1])


def test_response_root_has_the_doubled_s():
    """실제 응답 키가 landCharacteristicss다 — 오타처럼 보이지만 그게 맞다."""
    client = _FakeClient([{"landCharacteristicss": {"field": [LIVE_ITEM], "totalCount": "1"}}])
    items, total = fetch_page(client, "1111017700", 2024, 1)
    assert items == [LIVE_ITEM] and total == 1


def test_missing_year_returns_empty():
    client = _FakeClient([{"response": {"totalCount": "0"}}])
    assert fetch_page(client, "1111017700", 2010, 1) == ([], 0)


def test_error_code_raises():
    client = _FakeClient([{"landCharacteristicss": {"resultCode": "INCORRECT_KEY", "resultMsg": "x"}}])
    with pytest.raises(RuntimeError, match="INCORRECT_KEY"):
        fetch_page(client, "1111017700", 2024, 1)


def test_request_carries_domain():
    client = _FakeClient([{"landCharacteristicss": {"field": [], "totalCount": "0"}}])
    fetch_page(client, "1111017700", 2024, 1)
    assert "domain" in client.calls[0]


# ── D195 CSV 경로 ──────────────────────────────────────────────────────────
# 두 배포본의 실제 헤더 (2026-08-24 실측). 7·17번만 다르고 순서는 같다.
HEADER_SEOUL = [
    "고유번호", "법정동코드", "법정동명", "대장구분코드", "대장구분명", "지번",
    "토지일련번호", "기준연도", "기준월", "지목코드", "지목명", "토지면적",
    "용도지역코드1", "용도지역명1", "용도지역코드2", "용도지역명2",
    "토지이용상황코드", "토지이용상황", "지형높이코드", "지형높이",
    "지형형상코드", "지형형상", "도로접면코드", "도로접면", "공시지가",
    "데이터기준일자",
]
HEADER_GU = list(HEADER_SEOUL)
HEADER_GU[7] = "기준년도"      # 연 → 년
HEADER_GU[17] = "토지이동상황"  # 이용 → 이동

ROW = [
    "1154510100100020239", "1154510100", "서울특별시 금천구 가산동", "1", "일반",
    "2-239", "1", "2018", "01", "08", "대", "330.5",
    "33", "준공업지역", "00", "지정되지않음",
    "120", "연립", "03", "완경사", "05", "부정형", "06", "소로한면", "782000",
    "2019-08-09",
]


def test_both_header_variants_resolve_to_same_columns():
    """배포본마다 다른 컬럼명(기준년도/기준연도)이 같은 위치로 풀려야 한다."""
    a = resolve_csv_columns(HEADER_SEOUL)
    b = resolve_csv_columns(HEADER_GU)
    assert a == b
    assert a["base_year"] == 7 and a["use_situation"] == 17


def test_unknown_header_falls_back_to_position():
    """이름을 못 찾아도 고정 위치로 물러난다 — 조용히 전부 결측이 되면 안 된다."""
    cols = resolve_csv_columns([f"col{i}" for i in range(26)])
    assert cols["pnu"] == 0 and cols["base_year"] == 7 and cols["price_per_sqm"] == 24


def test_csv_row_matches_api_row_shape():
    """CSV 경로와 API 경로가 같은 upsert 튜플 모양을 내야 한다."""
    row = csv_row_to_row(ROW, resolve_csv_columns(HEADER_GU), "vworld_d195")
    api = to_row(LIVE_ITEM, "vworld_api")
    assert len(row) == len(api)
    assert row[0] == "1154510100100020239"
    assert row[1] == 2018
    assert row[2] == "준공업지역"      # zone_name_1
    assert row[3] is None              # zone_name_2: 지정되지않음 → None
    assert row[4] == "소로한면"        # road_side_name
    assert row[5] == "대"              # land_category
    assert row[6] == 330.5             # area_sqm
    assert row[10] == 782000           # price_per_sqm


def test_null_markers_become_none_not_fake_category():
    """'지정되지않음'을 문자열로 저장하면 가짜 용도지역 계층이 생긴다."""
    cells = list(ROW)
    cells[13] = "지정되지않음"
    row = csv_row_to_row(cells, resolve_csv_columns(HEADER_GU), "s")
    assert row[2] is None


def test_row_without_pnu_or_year_is_dropped():
    cols = resolve_csv_columns(HEADER_GU)
    no_pnu = list(ROW); no_pnu[0] = ""
    no_year = list(ROW); no_year[7] = ""
    bad_year = list(ROW); bad_year[7] = "연도아님"
    assert csv_row_to_row(no_pnu, cols, "s") is None
    assert csv_row_to_row(no_year, cols, "s") is None
    assert csv_row_to_row(bad_year, cols, "s") is None


def _zip_csv(tmp_path, name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{name}.csv", buf.getvalue().encode("cp949"))
    return str(path)


def test_rows_from_csv_filters_by_year_and_region(tmp_path):
    """연도·지역으로 좁히는 것이 실제로 걸러야 한다 — 여기가 새면 표가 오염된다."""
    gangnam = list(ROW)
    gangnam[0] = "1168010100100010001"
    y2019 = list(ROW); y2019[7] = "2019"
    path = _zip_csv(tmp_path, "AL_11545_D195_test", HEADER_GU, [ROW, gangnam, y2019])

    rows, stats = rows_from_csv(path, {2018}, ("11545",), "vworld_d195")
    assert [r[0] for r in rows] == ["1154510100100020239"]
    assert stats["out_of_scope"] == 2  # 강남구 1건 + 2019년 1건

    rows, _ = rows_from_csv(path, None, (), "vworld_d195")
    assert len(rows) == 3


def test_multi_year_history_file_keeps_every_year(tmp_path):
    """D195는 한 파일에 여러 연도가 들어 있다 — 연도를 접어버리면 패널이 무너진다."""
    rows_in = []
    for year in (2013, 2016, 2018, 2019):
        r = list(ROW); r[7] = str(year)
        rows_in.append(r)
    path = _zip_csv(tmp_path, "hist", HEADER_GU, rows_in)
    rows, _ = rows_from_csv(path, None, (), "vworld_d195")
    assert sorted(r[1] for r in rows) == [2013, 2016, 2018, 2019]
    deduped, dropped = dedupe_rows(rows)
    assert len(deduped) == 4 and dropped == 0


# ── D194 SHP 경로 ──────────────────────────────────────────────────────────
# D194는 D195와 같은 내용에 도형이 붙은 배포본인데 DBF 컬럼명이 A0~A26으로
# 익명화돼 있다. 이름으로 못 찾으니 **위치가 유일한 근거**이고, 한 칸이라도
# 밀리면 용도지역에 엉뚱한 값이 들어가 계층이 전부 깨진다. 여기가 그 방어선이다.

# 2026-08-24 실측 (AL_11680_D194_20200914, 역삼동 601번지).
D194_FIELDS = [f"A{i}" for i in range(27)]
D194_REC = [
    366484789, "1168010100106010000", "1168010100", "서울특별시 강남구 역삼동",
    "1", "일반", "601", "601 대", "2020", "01", "08", "대", 493.9,
    "22", "일반상업지역", "15", "제3종일반주거지역", "210", "업무용",
    "01", "평지", "03", "가로장방", "06", "광대소각", 44700000, "2020-09-14",
]


def test_d194_is_detected_by_filename():
    from tools.ingest_parcel_characteristics import is_d194
    assert is_d194("../data/land/AL_11680_D194_20200914.zip")
    assert is_d194(r"C:\data\AL_D194_11545_20250814.zip")   # 이름 순서가 뒤바뀐 배포본
    assert not is_d194("../data/land/AL_11545_D195_20190809.zip")
    assert not is_d194("AL_D195_11_20240730.zip")


def test_d194_columns_are_offset_by_one_from_d195():
    """A0이 일련번호이고 A1이 PNU다 — D195(0번이 PNU)보다 한 칸 밀려 있다."""
    from tools.ingest_parcel_characteristics import resolve_d194_columns
    cols = resolve_d194_columns(D194_FIELDS)
    assert cols["pnu"] == 1
    assert cols["base_year"] == 8
    assert cols["zone_name_1"] == 14
    assert cols["use_situation"] == 18
    assert cols["price_per_sqm"] == 25


def test_d194_unexpected_shape_fails_loudly():
    """조용히 엉뚱한 컬럼을 읽는 대신 죽어야 한다 — 밀린 채 넣으면 계층이 깨진다."""
    from tools.ingest_parcel_characteristics import resolve_d194_columns
    with pytest.raises(SystemExit, match="D194 DBF 형태"):
        resolve_d194_columns(["고유번호", "법정동코드"])
    with pytest.raises(SystemExit):
        resolve_d194_columns([])


def test_d194_record_parses_to_correct_semantics():
    """위치 매핑이 맞는지 값의 뜻으로 확인한다."""
    from tools.ingest_parcel_characteristics import resolve_d194_columns
    cols = resolve_d194_columns(D194_FIELDS)
    cells = ["" if v is None else str(v) for v in D194_REC]
    row = csv_row_to_row(cells, cols, "vworld_d194")
    assert row[0] == "1168010100106010000"
    assert row[1] == 2020
    assert row[2] == "일반상업지역"        # zone_name_1
    assert row[3] == "제3종일반주거지역"   # zone_name_2
    assert row[4] == "광대소각"            # road_side_name
    assert row[5] == "대"                  # land_category
    assert row[6] == 493.9                 # area_sqm
    assert row[7] == "업무용"              # use_situation
    assert row[10] == 44700000             # price_per_sqm


def test_d194_and_d195_produce_same_tuple_shape():
    """두 경로가 같은 표에 들어간다 — 모양이 다르면 upsert가 깨진다."""
    from tools.ingest_parcel_characteristics import resolve_d194_columns
    d194 = csv_row_to_row(
        [str(v) for v in D194_REC], resolve_d194_columns(D194_FIELDS), "a")
    d195 = csv_row_to_row(ROW, resolve_csv_columns(HEADER_GU), "b")
    assert len(d194) == len(d195)
