import psycopg2
import pytest

from app.config import settings
from app.services.dong_center import (
    _DONG_SEARCH_RE_SQL,
    _DONG_SEARCH_SQL,
    find_dong_centers,
    resolve_unique_dong,
)


def _db_available() -> bool:
    try:
        psycopg2.connect(settings.database_url, connect_timeout=2).close()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="local postgres not running")


def test_stem_fallback_sql_anchors_to_the_dong_name():
    # 어간 폴백을 ILIKE '%서초%동'으로 쓰면 '서울특별시 서초구 내곡동'까지 걸린다
    # ('서초'가 구 이름에 있고 이름이 '동'으로 끝나서). 정규식으로 마지막 낱말에만
    # 걸리게 묶어야 한다.
    assert "b.adm_nm ~ %s" in _DONG_SEARCH_RE_SQL
    assert "ILIKE" not in _DONG_SEARCH_RE_SQL
    # 원래 조회는 그대로 ILIKE를 쓴다.
    assert "b.adm_nm ILIKE %s" in _DONG_SEARCH_SQL


@requires_db
def test_legal_dong_names_resolve_to_admin_dongs():
    # 사람들은 법정동 이름으로 친다 — 경계 테이블에는 행정동만 있다.
    names = [c.name for c in find_dong_centers("역삼동", 6)]
    assert names and all("역삼" in n for n in names), names

    # 어간이 아예 다른 경우는 같은 구 안에서 이어 준다(여의도동 → 여의동).
    assert [c.name for c in find_dong_centers("여의도동", 6)] == ["영등포구 여의동"]


@requires_db
def test_stem_fallback_does_not_leak_other_dongs_in_the_same_gu():
    # 회귀 방지: '서초동'이 '서초구 내곡동'·'서초구 잠원동'을 물어오면 안 된다.
    names = [c.name for c in find_dong_centers("서초동", 6)]
    assert names, "서초동이 한 건도 안 잡혔다"
    assert all("서초" in n.split(" ", 1)[1] for n in names), names


@requires_db
def test_duplicate_dong_names_across_gus_are_all_offered():
    # '삼성동'은 관악구에 그대로, 강남구에는 삼성1·2동으로 있다 — 둘 다 보여야
    # 사용자가 고를 수 있다.
    names = [c.name for c in find_dong_centers("삼성동", 6)]
    assert any(n.startswith("관악구") for n in names), names
    assert any(n.startswith("강남구") for n in names), names
    # 어느 구인지 모르므로 자동 확정은 하지 않는다(기존 지오코딩에 맡긴다).
    assert resolve_unique_dong("삼성동") is None


def test_neighborhood_key_groups_numbered_dongs():
    from app.services.dong_center import _neighborhood_key

    assert _neighborhood_key("용산구 이태원1동") == ("용산구", "이태원")
    assert _neighborhood_key("용산구 이태원2동") == ("용산구", "이태원")
    assert _neighborhood_key("성동구 성수1가1동") == ("성동구", "성수")
    # 번호가 없는 동은 그대로
    assert _neighborhood_key("마포구 연남동") == ("마포구", "연남")


@requires_db
def test_numbered_dongs_in_one_gu_resolve_to_one_commercial_center():
    # '이태원'은 이태원1·2동으로 갈려 있어 '모호함'으로 넘겼더니 상권 밖 지점이
    # 잡혔다 — 그 자리 반경 500m의 오락 점포는 1곳, 이태원역 쪽은 13곳이었다.
    d = resolve_unique_dong("이태원")
    assert d is not None and d.name == "용산구 이태원동"
    # 이태원역(37.5347, 126.9946) 부근이어야 한다.
    assert abs(d.lat - 37.535) < 0.01 and abs(d.lng - 126.994) < 0.01

    assert resolve_unique_dong("역삼동").name == "강남구 역삼동"
    assert resolve_unique_dong("목동").name == "양천구 목동"


@requires_db
def test_same_name_in_different_gus_stays_ambiguous():
    # 관악구 삼성동과 강남구 삼성1·2동은 서로 다른 동네다 — 자동으로 고르면 안 된다.
    assert resolve_unique_dong("삼성동") is None
