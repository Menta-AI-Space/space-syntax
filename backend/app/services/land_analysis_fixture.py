"""Structured Analysis fixture — G6(화면·서술·도구)이 이것으로 개발한다.

**fixture를 손으로 쓴 JSON이 아니라 코드에서 생성한다.** 손으로 쓰면 스키마가
바뀔 때 조용히 낡고, 프런트는 실제 응답과 다른 모양에 맞춰 화면을 만든다. 여기서
실제 판정 규칙(`land_verdict`)을 통과시켜 만들면 **fixture가 규칙을 어기는 일이
구조적으로 불가능하다** — 임계값을 G7에서 바꾸면 fixture도 같이 바뀐다.

네 가지 경우를 담는다. 프런트는 이 넷을 다 그릴 수 있어야 한다:

1. ``undervalued``       — 설명되지 않는 할인분이 크다
2. ``cheap_with_reason`` — 싸지만 맹지·부정형이 그 값을 설명한다
3. ``above_peers``       — 같은 조건 대비 높다 (낙인 표기 금지 확인용)
4. ``undetermined``      — 비교군 부족 + 추정 범위 없음 → 판정 섹션이 통째로 없다

커밋된 JSON은 `frontend-next/public/fixtures/structured-analysis.json`이고,
`backend/tools/write_structured_analysis_fixture.py`로 다시 만든다. 두 쪽이
어긋나면 테스트가 깨진다.
"""

from __future__ import annotations

from app.models.land_analysis import (
    Attribute,
    GrowthOutlook,
    PeerGroup,
    PriceForecast,
    PriceForecastHorizon,
    PriceRange,
    StructuredAnalysis,
    Subject,
)
from app.services.land_verdict import (
    FactorInput,
    VerdictInput,
    build_structured_analysis,
)

FIXTURE_MODEL_VERSION = "fixture-0"


def _undervalued() -> StructuredAnalysis:
    return build_structured_analysis(
        subject=Subject(
            pnu="1156011000100120003",
            base_year=2026,
            address="영등포구 여의도동 12-3",
            ldong_name="여의도동",
            jibun="12-3",
            area_sqm=331.4,
            lat=37.5202,
            lng=126.9317,
        ),
        peer_group=PeerGroup(
            description="같은 동·같은 용도지역·같은 도로접면 1,586필지",
            size=1586,
            level="dong_zone_road",
            criteria=["여의도동", "일반상업지역", "중로한면"],
            median_official_price_per_sqm=12_000_000,
        ),
        attributes=[
            Attribute(key="zoning", label="용도지역", value="일반상업지역"),
            Attribute(key="land_category", label="지목", value="대"),
            Attribute(key="area", label="면적", value="331.4㎡"),
            Attribute(key="road_frontage", label="도로접면", value="중로한면 (12~25m)"),
            Attribute(key="shape", label="형상", value="정형"),
        ],
        inp=VerdictInput(
            peer_count=1586,
            peer_median_price_per_sqm=12_000_000,
            actual_price_per_sqm=8_300_000,
            expected_price_point=11_500_000,
            expected_price_low=10_400_000,
            expected_price_high=12_600_000,
            peer_similarity=0.82,
            in_training_distribution=True,
            price_percentile=18.4,
            structure_percentile=81.2,
            gap_score=62.8,
            unexplained_discount_rank_pct=3.2,
            gap_score_rank_pct=6.1,
            factors=[
                FactorInput(
                    key="road_access",
                    label="큰길에 바로 닿음",
                    direction="positive",
                    magnitude_label="큰 가점",
                    detail="폭 12~25m 도로에 한 면이 접합니다.",
                ),
                FactorInput(
                    key="through_traffic",
                    label="지나다니는 길목",
                    direction="positive",
                    magnitude_label="가점",
                    detail="주변 도로망에서 통과 동선이 모이는 위치입니다.",
                ),
                FactorInput(
                    key="depth",
                    label="깊이가 다소 얕음",
                    direction="negative",
                    magnitude_label="약한 감점",
                    explains_discount=True,
                ),
            ],
        ),
        growth_outlook=GrowthOutlook(
            horizon_years=3,
            own_growth_pct=14.2,
            peer_growth_pct=9.8,
            excess_growth_pct=4.4,
            hot_probability=0.61,
        ),
        price_forecast=PriceForecast(
            horizons=[
                PriceForecastHorizon(
                    years=1,
                    expected_official_price=PriceRange(
                        low=8_500_000, high=9_200_000, point=8_850_000
                    ),
                    annualized_change_pct=6.6,
                ),
                PriceForecastHorizon(
                    years=3,
                    expected_official_price=PriceRange(
                        low=9_100_000, high=11_400_000, point=10_200_000
                    ),
                    annualized_change_pct=7.1,
                ),
                PriceForecastHorizon(
                    years=5,
                    expected_official_price=PriceRange(
                        low=9_600_000, high=13_800_000, point=11_600_000
                    ),
                    annualized_change_pct=6.9,
                ),
            ]
        ),
        model_version=FIXTURE_MODEL_VERSION,
    )


def _cheap_with_reason() -> StructuredAnalysis:
    return build_structured_analysis(
        subject=Subject(
            pnu="1156011000100450001",
            base_year=2026,
            address="영등포구 여의도동 45-1",
            ldong_name="여의도동",
            jibun="45-1",
            area_sqm=118.7,
            lat=37.5196,
            lng=126.9331,
        ),
        peer_group=PeerGroup(
            description="같은 동·같은 용도지역 412필지",
            size=412,
            level="dong_zone",
            criteria=["여의도동", "제3종일반주거지역"],
            median_official_price_per_sqm=9_000_000,
        ),
        attributes=[
            Attribute(key="zoning", label="용도지역", value="제3종일반주거지역"),
            Attribute(key="land_category", label="지목", value="대"),
            Attribute(key="area", label="면적", value="118.7㎡"),
            Attribute(key="road_frontage", label="도로접면", value="맹지 (도로에 닿지 않음)"),
            Attribute(key="shape", label="형상", value="부정형"),
        ],
        inp=VerdictInput(
            peer_count=412,
            peer_median_price_per_sqm=9_000_000,
            actual_price_per_sqm=5_900_000,
            expected_price_point=6_200_000,
            expected_price_low=5_400_000,
            expected_price_high=7_000_000,
            peer_similarity=0.74,
            in_training_distribution=True,
            price_percentile=11.2,
            structure_percentile=34.5,
            gap_score=23.3,
            unexplained_discount_rank_pct=47.0,
            gap_score_rank_pct=52.0,
            factors=[
                FactorInput(
                    key="no_road_access",
                    label="맹지 (도로에 닿지 않음)",
                    direction="negative",
                    magnitude_label="큰 감점",
                    explains_discount=True,
                    detail="도로에 접하지 않아 그대로는 건축 허가를 받기 어렵습니다.",
                ),
                FactorInput(
                    key="irregular_shape",
                    label="부정형 (땅 모양이 고르지 않음)",
                    direction="negative",
                    magnitude_label="감점",
                    explains_discount=True,
                ),
                FactorInput(
                    key="small_area",
                    label="면적이 작음",
                    direction="negative",
                    magnitude_label="약한 감점",
                    explains_discount=True,
                ),
            ],
        ),
        model_version=FIXTURE_MODEL_VERSION,
    )


def _above_peers() -> StructuredAnalysis:
    return build_structured_analysis(
        subject=Subject(
            pnu="1156011000100080012",
            base_year=2026,
            address="영등포구 여의도동 8-12",
            ldong_name="여의도동",
            jibun="8-12",
            area_sqm=642.0,
            lat=37.5211,
            lng=126.9302,
        ),
        peer_group=PeerGroup(
            description="같은 동·같은 용도지역·같은 도로접면 96필지",
            size=96,
            level="dong_zone_road",
            criteria=["여의도동", "일반상업지역", "광대한면"],
            median_official_price_per_sqm=15_000_000,
        ),
        attributes=[
            Attribute(key="zoning", label="용도지역", value="일반상업지역"),
            Attribute(key="land_category", label="지목", value="대"),
            Attribute(key="area", label="면적", value="642.0㎡"),
            Attribute(key="road_frontage", label="도로접면", value="광대한면 (25m 이상)"),
            Attribute(key="shape", label="형상", value="정형"),
        ],
        inp=VerdictInput(
            peer_count=96,
            peer_median_price_per_sqm=15_000_000,
            actual_price_per_sqm=19_500_000,
            expected_price_point=15_800_000,
            expected_price_low=14_200_000,
            expected_price_high=17_600_000,
            peer_similarity=0.61,
            in_training_distribution=True,
            price_percentile=94.0,
            structure_percentile=72.0,
            gap_score=-22.0,
            unexplained_discount_rank_pct=88.0,
            gap_score_rank_pct=91.0,
            factors=[
                FactorInput(
                    key="wide_road",
                    label="넓은 길에 접함",
                    direction="positive",
                    magnitude_label="큰 가점",
                ),
                FactorInput(
                    key="large_area",
                    label="면적이 넉넉함",
                    direction="positive",
                    magnitude_label="가점",
                ),
            ],
        ),
        model_version=FIXTURE_MODEL_VERSION,
    )


def _undetermined() -> StructuredAnalysis:
    """비교군이 7필지뿐이고 추정 범위도 없다 → 판정 섹션이 통째로 없다."""
    return build_structured_analysis(
        subject=Subject(
            pnu="1156011000100990007",
            base_year=2026,
            address="영등포구 여의도동 99-7",
            ldong_name="여의도동",
            jibun="99-7",
            area_sqm=87.2,
            lat=37.5188,
            lng=126.9345,
        ),
        peer_group=PeerGroup(
            description="같은 동·같은 용도지역 7필지",
            size=7,
            level="dong_zone",
            criteria=["여의도동", "자연녹지지역"],
            median_official_price_per_sqm=11_000_000,
        ),
        attributes=[
            Attribute(key="zoning", label="용도지역", value="자연녹지지역"),
            Attribute(key="land_category", label="지목", value="임야"),
            Attribute(
                key="road_frontage",
                label="도로접면",
                value="확인되지 않음",
                note="이 항목은 전체 필지의 82%에만 채워져 있습니다.",
            ),
        ],
        inp=VerdictInput(
            peer_count=7,
            peer_median_price_per_sqm=11_000_000,
            actual_price_per_sqm=9_000_000,
            peer_similarity=0.38,
            in_training_distribution=False,
            price_percentile=28.0,
            factors=[],
        ),
        model_version=FIXTURE_MODEL_VERSION,
    )


def build_fixture() -> dict[str, StructuredAnalysis]:
    """네 경우를 라벨로 키를 매겨 돌려준다."""
    return {
        "undervalued": _undervalued(),
        "cheap_with_reason": _cheap_with_reason(),
        "above_peers": _above_peers(),
        "undetermined": _undetermined(),
    }


def fixture_payload() -> dict:
    """커밋되는 JSON의 정확한 모양. 프런트가 그대로 fetch한다."""
    cases = build_fixture()
    return {
        "generated_by": "backend/tools/write_structured_analysis_fixture.py",
        "note": (
            "코드에서 생성한 fixture입니다. 손으로 고치지 마세요 — "
            "규칙이나 스키마를 바꾼 뒤 스크립트를 다시 돌리세요."
        ),
        "cases": {key: analysis.model_dump(mode="json") for key, analysis in cases.items()},
    }
