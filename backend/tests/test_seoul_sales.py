"""resolve_seoul_service_code / fetch_sales_benchmark 회귀 테스트.

배경: 매핑에 없는 업종(미용실·헬스장·오락·숙박·주유소·전기차충전·전문서비스,
그리고 restaurant 프리셋까지)이 조용히 카페(CS100001)나 한식(CS100010)
매출로 대체되어, 화면에는 그럴싸한 숫자가 뜨지만 실제로는 완전히 다른
업종의 매출이 표시되던 문제. 이 테스트는 실제로 반환되는 값(서비스코드·
매출 유무)을 검사한다 — 경고 문구 존재 여부만 보는 테스트는 문구는 맞고
값은 틀린 상태를 통과시키므로 쓰지 않는다.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.services import seoul_sales
from app.services.seoul_sales import fetch_sales_benchmark, resolve_seoul_service_code


def test_mapped_scls_code_resolves_directly():
    resolved = resolve_seoul_service_code(
        inds_scls_cd="I21201", inds_mcls_cd=None, business_label="카페"
    )
    assert resolved == ("CS100001", "비알코올 음료점업")


def test_mapped_mcls_code_resolves_when_scls_missing():
    resolved = resolve_seoul_service_code(
        inds_scls_cd=None, inds_mcls_cd="I201", business_label="한식 음식점"
    )
    assert resolved == ("CS100010", "한식 음식점업")


def test_unmapped_codes_return_none_instead_of_a_fake_default():
    # 미용실(beauty preset): inds_mcls_cd=S207, 매핑에 없음
    assert (
        resolve_seoul_service_code(
            inds_scls_cd=None, inds_mcls_cd="S207", business_label="미용실"
        )
        is None
    )
    # 헬스장(fitness preset)
    assert (
        resolve_seoul_service_code(
            inds_scls_cd="R10307", inds_mcls_cd="R103", business_label="헬스장"
        )
        is None
    )
    # 오락·여가(leisure preset)
    assert (
        resolve_seoul_service_code(
            inds_scls_cd=None, inds_mcls_cd="R104", business_label="오락·여가"
        )
        is None
    )
    # 숙박(lodging preset)
    assert (
        resolve_seoul_service_code(
            inds_scls_cd=None, inds_mcls_cd="I101", business_label="숙박"
        )
        is None
    )
    # 주유소(gas_station preset)
    assert (
        resolve_seoul_service_code(
            inds_scls_cd="G21401", inds_mcls_cd="G214", business_label="주유소"
        )
        is None
    )
    # 전기차충전(ev_charging — 프리셋 자체가 코드 없이 비어 있음)
    assert (
        resolve_seoul_service_code(
            inds_scls_cd=None, inds_mcls_cd=None, business_label="전기차충전"
        )
        is None
    )
    # 부동산·전문서비스(professional_office preset)
    assert (
        resolve_seoul_service_code(
            inds_scls_cd="L10203", inds_mcls_cd="L102", business_label="부동산 중개"
        )
        is None
    )


def test_generic_restaurant_label_does_not_false_match_korean_food():
    # restaurant 프리셋은 특정 소분류를 단정하지 않기 위해 inds_mcls_cd/scls_cd가
    # 둘 다 None이다 (backend/app/services/sbiz_upjong.py 참고). 라벨의 "음식점"
    # 이라는 단어만으로 한식(I201)에 키워드 매치되면 치킨집·중식집 등도 전부
    # "한식 매출"로 잘못 표시되는 예전 버그가 재발한다.
    assert (
        resolve_seoul_service_code(
            inds_scls_cd=None, inds_mcls_cd=None, business_label="일반 음식점"
        )
        is None
    )


def test_keyword_match_still_works_for_specific_labels():
    resolved = resolve_seoul_service_code(
        inds_scls_cd=None, inds_mcls_cd=None, business_label="스타벅스 카페"
    )
    assert resolved == ("CS100001", "비알코올 음료점업")


def test_fetch_sales_benchmark_reports_no_data_for_unmapped_business_type():
    bench = asyncio.run(
        fetch_sales_benchmark(
            37.5559,
            126.9370,
            adm_name="서울특별시 강남구 역삼동",
            inds_scls_cd=None,
            inds_mcls_cd="S207",
            business_label="미용실",
        )
    )
    assert bench.monthly_revenue_man is None
    assert bench.per_store_monthly_man is None
    assert bench.total_quarterly_sales_won is None
    assert bench.data_source == "미제공"
    assert bench.warnings


def test_fetch_sales_benchmark_does_not_substitute_korean_food_for_generic_restaurant():
    bench = asyncio.run(
        fetch_sales_benchmark(
            37.5559,
            126.9370,
            adm_name="서울특별시 강남구 역삼동",
            inds_scls_cd=None,
            inds_mcls_cd=None,
            business_label="일반 음식점",
        )
    )
    assert bench.monthly_revenue_man is None
    assert bench.data_source == "미제공"
    assert bench.service_code != "CS100010"


def test_fetch_sales_benchmark_warns_when_api_omits_store_count(monkeypatch):
    # Open API 경로가 점포 수 0/None을 받으면 CSV 스냅샷 경로처럼
    # fallback으로 대체하고 경고를 남겨야 한다. 예전에는 조용히 대체해서
    # 화면에는 그럴싸한 매출/점포당 매출이 뜨지만 실제 점포 수 데이터는
    # 없는 상태였다.
    monkeypatch.setattr(settings, "data_seoul_go_api_key", "test-key")

    async def fake_fetch_sales_from_api(service_code, *, adm_name, dong_name):
        return 900_000_000, None, "서울시 상권분석 Open API (fake)"

    monkeypatch.setattr(seoul_sales, "_fetch_sales_from_api", fake_fetch_sales_from_api)

    bench = asyncio.run(
        fetch_sales_benchmark(
            37.5559,
            126.9370,
            adm_name="서울특별시 강남구 역삼동",
            inds_scls_cd="I21201",
            inds_mcls_cd=None,
            business_label="카페",
            fallback_store_count=7,
        )
    )

    assert bench.total_quarterly_sales_won == 900_000_000
    # 점포 수가 fallback(7)로 조용히 대체되지 않고, 왜 대체됐는지 경고가 남아야 한다.
    assert bench.store_count == 7
    assert any("점포 수 미확인" in w for w in bench.warnings)
    # per_store 값 자체는 fallback 점포 수 기준으로 계산되어 있어야 한다 (숫자가 사라지면 안 됨).
    expected_per_store = round((900_000_000 / 3) / seoul_sales.MAN_WON / 7, 1)
    assert bench.per_store_monthly_man == expected_per_store
