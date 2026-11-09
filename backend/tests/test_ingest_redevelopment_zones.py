"""정비구역(UQ181) 적재 — 네트워크·DB 없이 도는 부분.

값은 2026-08-24 실측(``UQ181_의제처리구역_202602.zip``, 3,209건).

**이 파일이 지키는 것은 고시일 파싱 하나다.** 고시일이 없으면 정비구역을 과거
연도 행에 붙일 수 없고(미래를 본다), 그러면 재개발·재건축이 땅값에 준 영향을
방법론 A·B에서 아예 못 본다. 날짜를 조용히 잘못 뽑거나 조용히 버리는 것이
여기서 가장 비싼 실수다.
"""

from __future__ import annotations

from datetime import date

from tools.ingest_redevelopment_zones import (
    _as_multipolygon,
    _src_label,
    parse_dbf_date,
    parse_notice_date,
    zone_key,
)


class TestParseNoticeDate:
    """``NTFC_SN`` → 고시일. 실측 표본으로 고정한다."""

    def test_real_samples(self):
        # 2026-08-24 실측 4건. NTC=고시, AGZ=의제처리 승인 둘 다 같은 형식이다.
        assert parse_notice_date("11000NTC201706088160") == date(2017, 6, 8)
        assert parse_notice_date("11530NTC202405200001") == date(2024, 5, 20)
        assert parse_notice_date("11000AGZ201711309293") == date(2017, 11, 30)
        assert parse_notice_date("11000NTC200605180069") == date(2006, 5, 18)

    def test_pre_1990_is_kept_not_dropped(self):
        """1950~60년대 토지구획정리사업이 실제로 있다 — 자르면 514건을 버린다."""
        assert parse_notice_date("11000NTC195312089944") == date(1953, 12, 8)
        assert parse_notice_date("11000NTC196409303150") == date(1964, 9, 30)

    def test_unparseable_returns_none_never_a_guess(self):
        """추측해서 채우면 A/B가 조용히 미래를 본다. None이 정답이다."""
        for bad in (None, "", "   ", "이상한값", "11000XYZ20170608",
                    "1100NTC20170608", "11000NTC2017"):
            assert parse_notice_date(bad) is None

    def test_impossible_calendar_date_returns_none(self):
        assert parse_notice_date("11000NTC20171340") is None      # 너무 짧다
        assert parse_notice_date("11000NTC20171340xxxx") is None  # 13월
        assert parse_notice_date("11000NTC20170231aaaa") is None  # 2월 31일

    def test_year_out_of_range_returns_none(self):
        assert parse_notice_date("11000NTC19440101aaaa") is None  # 하한 밖
        assert parse_notice_date("11000NTC21010101aaaa") is None  # 상한 밖

    def test_surrounding_whitespace_tolerated(self):
        assert parse_notice_date("  11000NTC201706088160  ") == date(2017, 6, 8)


class TestParseDbfDate:
    """``CREATE_DAT``(도면 생성일)은 고시일이 아니다 — 별도로 보관만 한다."""

    def test_valid(self):
        assert parse_dbf_date("20191215") == date(2019, 12, 15)

    def test_blank_and_zero_are_none(self):
        for bad in (None, "", "        ", "00000000", "2019", "20191515"):
            assert parse_dbf_date(bad) is None


class TestZoneKey:
    """원본에 PK가 없다. 겹친 행을 조용히 덮으면 구역이 사라진다."""

    def test_stable_for_same_input(self):
        a = zone_key("11000NTC201706088160", "가재울8재정비촉진구역", 0)
        b = zone_key("11000NTC201706088160", "가재울8재정비촉진구역", 0)
        assert a == b

    def test_same_notice_different_drawing_gets_different_key(self):
        """한 고시로 여러 도면이 오는 경우가 있다."""
        a = zone_key("11000NTC201706088160", "가재울8재정비촉진구역", 0)
        b = zone_key("11000NTC201706088160", "가재울9재정비촉진구역", 1)
        assert a != b

    def test_identical_name_and_notice_still_split_by_sequence(self):
        a = zone_key("11000NTC202106240001", "서울독산 기업형임대주택 공급촉진지구", 0)
        b = zone_key("11000NTC202106240001", "서울독산 기업형임대주택 공급촉진지구", 5)
        assert a != b

    def test_missing_fields_do_not_crash(self):
        assert zone_key(None, None, 0)


class TestGeometryFilter:
    """선·점은 구역 경계가 아니라 지적 부속 요소다 — 저장하면 유령 행이 된다."""

    def test_polygon_is_wrapped(self):
        from shapely.geometry import Polygon
        poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        out = _as_multipolygon(poly)
        assert out is not None and out.geom_type == "MultiPolygon"

    def test_line_and_point_rejected(self):
        from shapely.geometry import LineString, Point
        assert _as_multipolygon(LineString([(0, 0), (1, 1)])) is None
        assert _as_multipolygon(Point(0, 0)) is None

    def test_empty_rejected(self):
        from shapely.geometry import Polygon
        assert _as_multipolygon(Polygon()) is None


class TestSrcLabel:
    """어느 배포본에서 왔는지 남겨야 나중에 해제 이력을 차집합으로 낼 수 있다."""

    def test_windows_and_posix_paths(self):
        assert _src_label(r"C:\data\UQ181_x_202602.zip") == "uq181_UQ181_x_202602"
        assert _src_label("/data/UQ181_x_202602.zip") == "uq181_UQ181_x_202602"

    def test_distinct_releases_get_distinct_labels(self):
        a = _src_label("UQ181_의제처리_20231004.zip")
        b = _src_label("UQ181_의제처리구역_202602.zip")
        assert a != b

    def test_label_fits_column(self):
        assert len(_src_label("x" * 200 + ".zip")) <= 64
