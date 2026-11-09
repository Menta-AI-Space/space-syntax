from pathlib import Path

import psycopg2
import pytest

from app.config import settings
from app.services.trdar_sales_local import (
    PROFILE_SVC_CODES,
    citywide_benchmark,
    profile_svc_codes,
    sales_for_trdar,
)


def _db_available() -> bool:
    try:
        psycopg2.connect(settings.database_url, connect_timeout=2).close()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="local postgres not running")


def test_every_profile_has_new_system_codes():
    # 2026-07 개편 체계: 카페는 CS100010, CS100001은 이제 한식 — 옛 매핑 혼입 방지
    assert profile_svc_codes("cafe") == ["CS100010"]
    assert "CS100001" in profile_svc_codes("restaurant")
    for profile_id, entry in PROFILE_SVC_CODES.items():
        assert entry["primary"], profile_id
        # secondary는 선택이다 — 편의점은 슈퍼마켓이 섞이지 않도록 일부러 두지 않는다.
        assert profile_svc_codes(profile_id, include_secondary=True) == (
            entry["primary"] + entry.get("secondary", [])
        )


def test_convenience_excludes_supermarket():
    # 회귀 방지: 슈퍼마켓(CS300001)을 secondary로 묶었더니 '편의점 점포당 월매출'에
    # 슈퍼마켓이 합산돼 편의점이 아닌 숫자를 편의점이라고 보여 줬다.
    assert profile_svc_codes("convenience", include_secondary=True) == ["CS300002"]


def test_unknown_profile_returns_empty():
    assert profile_svc_codes("florist") == []


@requires_db
def test_citywide_cafe_benchmark_is_plausible():
    b = citywide_benchmark("cafe")
    assert b.trdar_count > 500  # 서울 상권 ~1,600곳 중 카페 있는 곳
    # 상권 단위 월매출 중앙값: 1천만원 ~ 100억원 사이면 자릿수 정상
    assert 10_000_000 < b.median_monthly_sales_won < 10_000_000_000
    assert b.p25_monthly_sales_won <= b.median_monthly_sales_won <= b.p75_monthly_sales_won
    assert b.quarter >= "20251"


@requires_db
def test_point_to_sales_chain():
    from app.services.trdar_sales_local import sales_benchmark_for_point, trdar_for_point

    # 강남역 사거리 — 상권 폴리곤 내부여야 함
    result = sales_benchmark_for_point(37.4979, 127.0276, "restaurant")
    assert result is not None
    match, sales, benchmark = result
    assert match.contains
    assert "강남" in sales.trdar_nm
    assert sales.monthly_sales_won > benchmark.median_monthly_sales_won
    # 북한산 중턱 — 어떤 상권에서도 300m 초과
    assert trdar_for_point(37.6585, 126.9880) is None


@requires_db
def test_sales_for_trdar_roundtrip():
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT trdar_cd FROM trdar_sales WHERE svc_cd='CS100010' AND month_sales_amt > 0 LIMIT 1"
    )
    trdar_cd = cur.fetchone()[0]
    conn.close()
    result = sales_for_trdar(trdar_cd, "cafe")
    assert result is not None
    assert result.monthly_sales_won > 0
    assert result.trdar_nm


def test_quarterly_amount_is_converted_to_monthly():
    # 회귀 방지: month_sales_amt는 이름과 달리 분기 합계다(레코드 키가 분기).
    # 3으로 나누지 않은 채 monthly로 내보내 화면의 매출이 전부 3배였다.
    from app.services.trdar_sales_local import QUARTER_MONTHS, _monthly

    assert QUARTER_MONTHS == 3
    assert _monthly(9_000_000) == 3_000_000
    # 신촌 카페 실측: 분기 54.2억 → 월 18.1억 (점포 119곳이면 점포당 1,519만원)
    assert round(_monthly(5_421_847_864) / 119 / 10_000) == 1519


def test_per_store_uses_similar_store_count_as_denominator():
    # 회귀 방지: 분모를 store_count로 되돌리면 편의점 점포당 월매출이 1.19억원
    # (실제의 4~5배)으로 돌아간다. 서울 합계에서 프랜차이즈(4,792)가
    # store_count(1,762)보다 많다는 것이 그 컬럼이 전체 점포 수가 아니라는 증거다.
    import re

    from app.services import trdar_sales_local as m

    assert m.STORE_COUNT_COLUMN == "similar_store_count"
    src = Path(m.__file__).read_text(encoding="utf-8")
    # 점포당 매출을 만드는 SQL은 모두 similar_store_count로 집계해야 한다.
    # (별칭이 붙은 sum(st.similar_store_count) 형태도 같이 잡는다.)
    sums = re.findall(r"sum\((?:\w+\.)?(\w*store_count)\)", src)
    assert sums, "점포수 집계 SQL을 찾지 못했다"
    assert set(sums) == {"similar_store_count"}, sums


@requires_db
def test_similar_store_count_is_total_stores_not_a_wider_set():
    """분모 근거: 유사_업종_점포_수 = 점포_수 + 프랜차이즈_점포_수 (전 행 항등).

    이 항등식이 '유사 업종이라 더 넓은 집합 아니냐'는 의심을 정리한다 —
    store_count가 비프랜차이즈만 센 것이고 similar가 전체 합계다. 원본이
    바뀌어 이 관계가 깨지면 분모 선택의 근거도 사라지므로 여기서 잡는다.
    """
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT count(*), count(*) FILTER (
            WHERE similar_store_count = store_count + franchise_count
        )
        FROM trdar_stores
        WHERE store_count IS NOT NULL AND similar_store_count IS NOT NULL
          AND franchise_count IS NOT NULL
        """
    )
    total, equal = cur.fetchone()
    conn.close()
    assert total > 100_000
    assert equal == total, f"{total - equal}행에서 항등식이 깨졌다"


def test_citywide_and_local_use_the_same_svc_codes():
    """회귀 방지: 서울 중앙값만 primary로 재면 서로 다른 업종 묶음을 비교하게 된다.

    지역 값은 primary+secondary 합계인데 중앙값은 primary만 쓰던 탓에 카페는
    19.4% 낮은 기준과, 한식은 10.1% 높은 기준과 비교되고 있었다.
    """
    import inspect

    from app.services import trdar_sales_local as m

    src = inspect.getsource(m.citywide_benchmark)
    assert "include_secondary=True" in src


@requires_db
def test_dong_fallback_covers_points_outside_every_trdar():
    """상권 밖 22%를 행정동 매출로 메운다 — 여기서 None이면 벤치마크가 통째로 없다."""
    from app.services.trdar_sales_local import (
        SCOPE_DONG,
        DONG_SE_NM,
        dong_for_point,
        sales_benchmark_for_point,
        trdar_for_point,
    )

    # 상계9동 주택가 — 어떤 상권 폴리곤에서도 300m를 벗어난 실제 카페 좌표
    lat, lng = 37.6685679, 127.0611156
    assert trdar_for_point(lat, lng) is None, "이 지점은 상권 밖이어야 테스트가 성립한다"

    result = sales_benchmark_for_point(lat, lng, "cafe")
    assert result is not None
    match, sales, benchmark = result
    # 어느 단위로 계산했는지 반드시 드러나야 한다 — 상권 매출과 정밀도가 다르다.
    assert match.scope == SCOPE_DONG
    assert sales.scope == SCOPE_DONG
    assert match.trdar_se_nm == DONG_SE_NM
    assert match.trdar_nm
    assert sales.per_store_monthly_won and sales.per_store_monthly_won > 0
    # 서울 대비 위치도 같은 단위(행정동 분포)로 계산해야 비교가 성립한다.
    assert benchmark.scope == SCOPE_DONG

    assert dong_for_point(lat, lng).trdar_cd == match.trdar_cd
    # 서울 밖은 억지로 가장 가까운 동을 주지 않는다
    assert dong_for_point(37.2636, 127.0286) is None


@requires_db
def test_dong_code_map_covers_essentially_all_of_seoul():
    """통계청 adm_cd ↔ 행자부 행정동코드는 공통 키가 없어 이름으로 잇는다.

    426개 경계 중 425개가 붙는다(못 붙는 개포3동은 golmok이 아직 옛 이름
    '일원2동'으로 들고 있는 행정동 개편 건이다). 이 비율이 떨어지면 상권 밖
    지점들이 조용히 '데이터 없음'으로 되돌아간다.
    """
    from app.services.trdar_sales_local import _dong_code_map

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    mapping = _dong_code_map(cur)
    cur.execute("SELECT count(*) FROM sgis_dong_boundary")
    total = cur.fetchone()[0]
    conn.close()
    assert len(mapping) >= total - 2, f"{len(mapping)}/{total}만 매핑됐다"


@requires_db
def test_store_denominator_excludes_upjong_without_sales():
    """분자에 없는 업종을 분모에만 넣으면 점포당 매출이 낮아진다.

    한 상권에 분식 점포는 있는데 분식 매출 행이 없는 경우가 흔해, 이 조건이
    빠지면 restaurant는 23.5%, cafe는 11.0% 과소평가된다.
    """
    import inspect

    from app.services import trdar_sales_local as m

    for fn in (m.sales_for_trdar, m.citywide_benchmark, m.sales_for_dong):
        src = inspect.getsource(fn)
        assert "EXISTS" in src or "_STORE_SUBQUERY" in src, fn.__name__
