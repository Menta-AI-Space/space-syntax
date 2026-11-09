"""필지 API — 조회 계층과 라우터.

계산 규칙은 ``test_parcel_undervalued.py``가 검증한다. 여기서는 **읽는 쪽**만
본다: 지목·면적 필터가 실제로 SQL에 걸리는가, 스키마가 없을 때 500이 아니라
빈 목록으로 물러나는가, 응답이 백분위를 본문으로 내보내는가.
"""

import psycopg2.errors
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import parcel_undervalued as mod


def _row(**kw):
    """DB 한 행. 실제 쿼리가 주는 컬럼을 그대로 흉내낸다."""
    base = {
        "pnu": "1114010600100010000",
        "adm_cd": "11140660",
        "zone_group": "residential_2_general",
        "base_year": 2026,
        "structure_percentile": 95.1,
        "price_percentile": 0.2,
        "gap_score": 94.9,
        "price_per_sqm": 2054000.0,
        "stratum_level": "dong_zone",
        "stratum_size": 3212,
        "ldong_name": "서울특별시 마포구 서교동",
        "jibun": "449-40",
        "land_category": "대",
        "area_sqm": 101.1,
        "road_side_name": "세로한면(가)",
        "adm_nm": "서울특별시 마포구 서교동",
        "lat": 37.557202,
        "lng": 126.921,
    }
    base.update(kw)
    return base


# ── 필터가 실제로 SQL에 걸리는가 ──────────────────────────────────────


def _captured_params(monkeypatch, **call_kw):
    """``list_top_parcels_in_dong``이 SQL에 넘긴 파라미터를 잡아낸다."""
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

    monkeypatch.setattr(
        "app.services.spatial_query.get_connection", lambda: _Conn()
    )
    mod.list_top_parcels_in_dong("11140660", **call_kw)
    return seen


def test_default_keeps_only_buildable_lots(monkeypatch):
    """기본값은 대(垈)만. 이걸 놓치면 목록 위가 임야·철도용지로 덮인다 —
    목이 좋은데 공시지가가 낮은 게 맞지만 저평가라서가 아니라 못 쓰는 땅이라서다."""
    params = _captured_params(monkeypatch)
    assert params["categories"] == ["대"]
    assert params["min_area"] == mod.DEFAULT_MIN_AREA_SQM


def test_filter_can_be_turned_off(monkeypatch):
    """판정 결과를 다시 계산하지 않고 기준만 바꿀 수 있어야 한다."""
    params = _captured_params(
        monkeypatch, land_categories=None, min_area_sqm=None
    )
    assert params["categories"] is None
    assert params["min_area"] is None


def test_weak_stratum_level_excluded_by_default(monkeypatch):
    """``zone``만으로 비교된 필지는 강남과 도봉이 한 분포에 섞여 있다."""
    params = _captured_params(monkeypatch)
    assert "zone" not in params["levels"]
    assert set(params["levels"]) == {"dong_zone", "sgg_zone"}


# ── 스키마 미준비 vs 진짜 장애 ────────────────────────────────────────


def test_missing_table_returns_empty_list_not_500(monkeypatch):
    """배치를 안 돌린 환경에서 500이면 '고장'과 '아직 계산 안 함'이 구분되지 않는다."""

    def boom():
        raise psycopg2.errors.UndefinedTable("relation does not exist")

    monkeypatch.setattr("app.services.spatial_query.get_connection", boom)
    assert mod.list_top_parcels_in_dong("11140660") == []


def test_connection_failure_still_raises(monkeypatch):
    """DB가 죽은 것은 진짜 장애다 — 빈 목록으로 덮으면 안 된다."""

    def boom():
        raise psycopg2.OperationalError("could not connect")

    monkeypatch.setattr("app.services.spatial_query.get_connection", boom)
    with pytest.raises(psycopg2.OperationalError):
        mod.list_top_parcels_in_dong("11140660")


# ── 라우터 응답 ────────────────────────────────────────────────────────


def test_response_carries_percentiles_and_address(monkeypatch):
    monkeypatch.setattr(
        "app.routers.undervalued_parcels.list_top_parcels_in_dong",
        lambda *a, **k: [_row()],
    )
    res = TestClient(app).get("/api/undervalued-parcels", params={"adm_cd": "11140660"})
    assert res.status_code == 200
    body = res.json()
    parcel = body["parcels"][0]
    assert parcel["address"] == "서울특별시 마포구 서교동 449-40"
    assert parcel["structure_percentile"] == 95.1
    assert parcel["price_percentile"] == 0.2
    assert parcel["stratum_size"] == 3212


def test_note_refuses_market_price_claim(monkeypatch):
    """공시지가는 시세가 아니다. 화면이 '시세보다 싸다'로 읽히면 안 된다."""
    monkeypatch.setattr(
        "app.routers.undervalued_parcels.list_top_parcels_in_dong",
        lambda *a, **k: [_row()],
    )
    res = TestClient(app).get("/api/undervalued-parcels", params={"adm_cd": "11140660"})
    note = res.json()["note"]
    assert "공시지가" in note
    assert "매수 추천이 아닙니다" in note


def test_address_survives_missing_jibun(monkeypatch):
    """지번 결측이 실측 0.02% 있다 — 'None'이 주소로 새어 나가면 안 된다."""
    monkeypatch.setattr(
        "app.routers.undervalued_parcels.list_top_parcels_in_dong",
        lambda *a, **k: [_row(jibun=None)],
    )
    res = TestClient(app).get("/api/undervalued-parcels", params={"adm_cd": "11140660"})
    assert res.json()["parcels"][0]["address"] == "서울특별시 마포구 서교동"


def test_empty_result_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        "app.routers.undervalued_parcels.list_top_parcels_in_dong",
        lambda *a, **k: [],
    )
    res = TestClient(app).get("/api/undervalued-parcels", params={"adm_cd": "11140660"})
    assert res.status_code == 200
    assert res.json()["parcels"] == []
