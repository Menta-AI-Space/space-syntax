"""area_extras — 적재만 해 두고 안 쓰던 표를 상담 사실 줄로 (2026-11-06).

실제 DB 없이 질의 문구로 응답을 고르는 가짜 커서로, 줄 모양과 「섹션 하나가 죽어도
나머지는 낸다」를 고정한다.
"""

from __future__ import annotations

import contextlib
import datetime as dt

from app.services import area_extras, reb_rent, transit


class _FakeCursor:
    def __init__(self, answers):
        self.answers = answers  # [(SQL 안의 표 이름, 결과 행들 | Exception)]
        self._rows = []
        self.sql: list[str] = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if sql.startswith(("SET ", "RESET ")):
            self._rows = []
            return
        for table, rows in self.answers:
            if table in sql:
                if isinstance(rows, Exception):
                    raise rows
                self._rows = list(rows)
                return
        self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = False

    def cursor(self):
        return self._cursor


def _install(monkeypatch, answers, *, dong=("11440660", "서교동")):
    cursor = _FakeCursor(answers)

    @contextlib.contextmanager
    def _connect():
        yield _FakeConn(cursor)

    monkeypatch.setattr(area_extras, "_connect", _connect)
    monkeypatch.setattr(area_extras, "_dong_code", lambda lat, lng: dong)
    monkeypatch.setattr(
        transit,
        "fetch_station_scale",
        lambda name: transit.StationScale(station_nm="홍대입구", daily_total=152340, line_count=3),
    )
    monkeypatch.setattr(
        reb_rent,
        "fetch_rent_reference",
        lambda lat, lng, building_type="집합": reb_rent.RebRentReference(
            matched=True,
            sanggwon_nm="홍대합정",
            quarter="2026Q2",
            types=[reb_rent.RebRentType("중대형", 61000, 8.4), reb_rent.RebRentType("소규모", None, 3.1)],
        ),
    )
    return cursor


_ALL = [
    ("golmok_change", [("2026Q2", "상권확장", {"운영_영업_개월_평균": "71.2", "폐업_영업_개월_평균": "38.6"})]),
    ("golmok_facility", [("2026Q2", {"집객시설_수": "48", "지하철_역_수": "2", "은행_수": "7", "대학교_수": "1", "극장_수": "0"})]),
    ("biz_licenses", [("휴게음식점", 210, 140, 95, 820.0), ("일반음식점", 330, 120, 110, None)]),
    ("large_stores", [("롯데마트 서교점", "대형마트", 420.0)]),
    ("building_permits", [(12, 85000.4, [{"main_purps_nm": "업무시설", "area": 32000, "permit_date": "2025-03-14", "bld_nm": None}])]),
    ("redevelopment_zones", [("홍대 제2구역", "재개발", True, 0.0)]),
]


def test_extra_lines_cover_every_idle_table(monkeypatch):
    _install(monkeypatch, _ALL)
    lines = area_extras.area_extra_lines(37.557, 126.924, 500, station_name="홍대입구역")
    text = "\n".join(lines)

    assert "가까운 역 규모: 홍대입구 하루 승하차 약 152,340명, 3개 노선 환승역" in text
    assert "상권변화지표(서교동, 2026Q2): 상권확장 — 운영 영업 개월 평균 71개월, 폐업 영업 개월 평균 39개월" in text
    assert "집객시설(서교동, 2026Q2): 총 48곳 — 은행 7, 지하철 역 2, 대학교 1" in text
    assert "극장" not in text  # 0곳은 빼고
    assert "반경 500m 휴게음식점: 영업 중 210곳, 최근 3년 개업 140·폐업 95 · 최근 5년 폐업 가게의 영업기간 중앙값 2.2년" in text
    assert "일반음식점: 영업 중 330곳, 최근 3년 개업 120·폐업 110" in text
    assert "반경 1000m 안 대규모점포: 롯데마트 서교점(대형마트, 420m)" in text
    assert "최근 2년 건축허가 12건(연면적 합 85,000㎡) — 큰 것: 업무시설 32,000㎡(2025-03)" in text
    assert "이 자리가 홍대 제2구역(재개발) 구역 안이다" in text
    assert "부동산원 상권 홍대합정(2026Q2): 중대형 공실률 8.4% · ㎡당 월 임대료 약 61,000원; 소규모 공실률 3.1%" in text


def test_a_missing_table_only_drops_its_own_line(monkeypatch):
    """운영 DB에 표가 없으면(로컬에만 적재) 그 줄만 빠지고 나머지는 나온다."""
    answers = [(t, RuntimeError('relation "biz_licenses" does not exist') if t == "biz_licenses" else r) for t, r in _ALL]
    _install(monkeypatch, answers)
    lines = area_extras.area_extra_lines(37.557, 126.924, 500, station_name="홍대입구역")
    text = "\n".join(lines)
    assert "휴게음식점" not in text
    assert "대규모점포" in text and "상권변화지표" in text and "정비구역" in text


def test_every_query_runs_under_a_timeout(monkeypatch):
    cursor = _install(monkeypatch, _ALL)
    area_extras.area_extra_lines(37.557, 126.924, 500)
    assert cursor.sql[0].startswith("SET statement_timeout")
    assert cursor.sql[-1].startswith("RESET statement_timeout")


def test_no_dong_and_no_station_skip_those_sections(monkeypatch):
    _install(monkeypatch, _ALL, dong=None)
    lines = area_extras.area_extra_lines(37.557, 126.924, 500, station_name=None)
    text = "\n".join(lines)
    assert "상권변화지표" not in text and "집객시설" not in text and "가까운 역 규모" not in text
    assert "대규모점포" in text


def test_db_down_returns_nothing_but_does_not_raise(monkeypatch):
    _install(monkeypatch, _ALL)

    @contextlib.contextmanager
    def _broken():
        raise ConnectionError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(area_extras, "_connect", _broken)
    monkeypatch.setattr(reb_rent, "fetch_rent_reference", lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))
    monkeypatch.setattr(transit, "fetch_station_scale", lambda name: None)
    assert area_extras.area_extra_lines(37.557, 126.924, 500, station_name="홍대입구역") == []


def test_years_helper():
    assert area_extras._years(730) == "2.0년"
    assert area_extras._years(None) == ""
    assert isinstance(dt.date.today(), dt.date)


def test_land_readjustment_zones_are_excluded_in_sql(monkeypatch):
    """토지구획정리사업은 정비구역 경고에서 뺀다(망원동 오경보, §9-2)."""
    cursor = _install(monkeypatch, _ALL)
    area_extras.area_extra_lines(37.557, 126.924, 500)
    zone_sql = next(sql for sql in cursor.sql if "redevelopment_zones" in sql)
    assert "구획정리" in zone_sql and "NOT LIKE" in zone_sql
