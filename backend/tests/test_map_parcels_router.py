"""지도용 필지 조회 — 경계 + 판정.

지도는 **땅 전체**를 그려야 한다. 판정된 필지만 주면 지도에 구멍이 뚫리고,
사용자는 그게 "판정 못 함"인지 "땅이 아님"인지 구분할 수 없다. 그래서 이 조회는
``parcel_polygons``를 왼쪽에 두고 판정을 LEFT JOIN한다 — 판정 없는 필지가 판정
필드를 비운 채로 온다. 그 계약을 여기서 지킨다.
"""

import json

import psycopg2.errors
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import parcel_undervalued as mod

_GEOM = {
    "type": "MultiPolygon",
    "coordinates": [[[[126.9324, 37.5200], [126.9325, 37.5200],
                      [126.9325, 37.5201], [126.9324, 37.5200]]]],
}


def _row(**kw):
    """조회가 돌려주는 한 행. 판정이 있는 필지를 기본으로 둔다."""
    base = {
        "pnu": "1156011000100430005",
        "geometry": json.dumps(_GEOM),
        "geom_area_sqm": 1520.0,
        "gap_score": 46.2,
        "structure_percentile": 88.0,
        "price_percentile": 41.8,
        "stratum_level": "dong_zone",
        "stratum_size": 173,
        "zone_group": "commercial_general",
        "official_price_per_sqm": 16740000.0,
        "ldong_name": "서울특별시 영등포구 여의도동",
        "jibun": "43-5",
        "land_category": "대",
        "area_sqm": 1518.4,
        "road_side_name": "광대로한면",
        "zone_name_1": "일반상업지역",
    }
    base.update(kw)
    return base


def _client(monkeypatch, rows):
    monkeypatch.setattr(
        "app.routers.undervalued_parcels.list_parcels_near", lambda *a, **k: rows
    )
    return TestClient(app)


_Q = {"lng": 126.9317, "lat": 37.5202, "radius_m": 150}


# ── 판정 없는 필지도 온다 ──────────────────────────────────────────────


def test_unjudged_parcel_is_returned_with_empty_judgment(monkeypatch):
    """실측: 여의도동 폴리곤 529 vs 판정 283. 판정된 것만 주면 지도가 절반 빈다."""
    unjudged = _row(
        gap_score=None, structure_percentile=None, price_percentile=None,
        stratum_level=None, stratum_size=None, zone_group=None,
    )
    res = _client(monkeypatch, [_row(), unjudged]).get(
        "/api/undervalued-parcels".replace("undervalued-parcels", "map-parcels"), params=_Q
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["parcels"]) == 2
    assert body["judged_count"] == 1, "판정 수를 따로 세어 화면이 회색을 구분할 수 있어야 한다"
    assert body["parcels"][1]["gap_score"] is None
    # 판정이 없어도 땅의 신원과 공시지가는 나와야 한다.
    assert body["parcels"][1]["official_price_per_sqm"] == 16740000.0
    assert body["parcels"][1]["address"].endswith("43-5")


def test_geometry_is_parsed_not_passed_as_string(monkeypatch):
    """프런트가 그대로 지도에 넘길 수 있어야 한다 — 문자열이면 다시 파싱해야 한다."""
    res = _client(monkeypatch, [_row()]).get("/api/map-parcels", params=_Q)
    geometry = res.json()["parcels"][0]["geometry"]
    assert isinstance(geometry, dict)
    assert geometry["type"] == "MultiPolygon"


def test_parcel_without_geometry_is_skipped(monkeypatch):
    """기하가 없으면 지도에 그릴 수 없다 — 유령 행으로 내보내지 않는다."""
    res = _client(monkeypatch, [_row(), _row(pnu="X", geometry=None)]).get(
        "/api/map-parcels", params=_Q
    )
    assert len(res.json()["parcels"]) == 1


def test_address_survives_missing_jibun(monkeypatch):
    res = _client(monkeypatch, [_row(jibun=None)]).get("/api/map-parcels", params=_Q)
    assert res.json()["parcels"][0]["address"] == "서울특별시 영등포구 여의도동"


def test_note_refuses_market_price_reading(monkeypatch):
    """공시지가는 시세가 아니다. 회색 필지의 뜻도 함께 알려야 한다."""
    note = _client(monkeypatch, [_row()]).get("/api/map-parcels", params=_Q).json()["note"]
    assert "공시지가" in note and "시세가 아닙니다" in note
    assert "회색" in note


# ── 조회 계층 ──────────────────────────────────────────────────────────


def test_query_passes_radius_and_year(monkeypatch):
    seen = {}

    class _Cur:
        description = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, _sql, params):
            seen.update(params)

        def fetchall(self):
            return []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _Cur()

    monkeypatch.setattr("app.services.spatial_query.get_connection", lambda: _Conn())
    mod.list_parcels_near(126.9317, 37.5202, radius_m=150, base_year=2026)
    assert seen["radius_m"] == 150
    assert seen["year"] == 2026
    assert seen["lng"] == 126.9317


def test_missing_table_returns_empty_list_not_500(monkeypatch):
    """폴리곤을 아직 적재하지 않은 범위는 장애가 아니다 — 동 단위로 넣기 때문이다."""

    def boom():
        raise psycopg2.errors.UndefinedTable("relation does not exist")

    monkeypatch.setattr("app.services.spatial_query.get_connection", boom)
    assert mod.list_parcels_near(126.9317, 37.5202) == []


def test_connection_failure_still_raises(monkeypatch):
    def boom():
        raise psycopg2.OperationalError("could not connect")

    monkeypatch.setattr("app.services.spatial_query.get_connection", boom)
    with pytest.raises(psycopg2.OperationalError):
        mod.list_parcels_near(126.9317, 37.5202)


# ── 상한에 걸렸을 때 ───────────────────────────────────────────────────


def test_truncation_is_reported(monkeypatch):
    """조용히 자르면 화면의 구멍을 사용자가 "땅이 아님"으로 읽는다."""
    rows = [_row(pnu=f"11545102001{i:08d}") for i in range(500)]
    res = _client(monkeypatch, rows).get("/api/map-parcels", params={**_Q, "limit": 500})
    assert res.json()["truncated"] is True


def test_not_truncated_when_under_limit(monkeypatch):
    res = _client(monkeypatch, [_row()]).get("/api/map-parcels", params={**_Q, "limit": 500})
    assert res.json()["truncated"] is False


def test_query_orders_by_distance_not_gap_score(monkeypatch):
    """실측 회귀: 갭 순 정렬 + LIMIT이면 판정 없는 필지가 통째로 잘린다.

    독산동 반경 400m에서 "필지 500곳 · 판정 500곳"이 나왔다 — 회색 필지를 함께
    준다는 이 조회의 계약이 깨진 상태였다. 거리순이면 잘리는 것은 항상 바깥
    테두리라 사용자가 지도를 움직여 채울 수 있다.
    """
    seen = {}

    class _Cur:
        description = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, _params):
            seen["sql"] = sql

        def fetchall(self):
            return []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _Cur()

    monkeypatch.setattr("app.services.spatial_query.get_connection", lambda: _Conn())
    mod.list_parcels_near(126.9317, 37.5202)
    sql = seen["sql"]
    assert "gap_score DESC" not in sql, "갭 순 정렬 + LIMIT은 판정 없는 필지를 전멸시킨다"
    assert "<->" in sql, "거리순 정렬이어야 잘리는 것이 바깥 테두리다"


# ── 범위 상한 ──────────────────────────────────────────────────────────


def test_radius_is_capped(monkeypatch):
    """전 서울 89만 폴리곤을 GeoJSON으로 내리면 5초 룰을 못 지킨다.
    넓히려면 벡터 타일이 선행이므로 반경 상한이 그 방어선이다."""
    res = _client(monkeypatch, []).get("/api/map-parcels", params={**_Q, "radius_m": 5000})
    assert res.status_code == 422
