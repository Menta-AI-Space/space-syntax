"""용도지역 명칭 정규화 + 개발 제한 판정.

출처(SHP·GeoJSON·WFS)마다 용도지역 명칭 표기가 다르다 — 공백, 괄호, "지역"
접미사 유무, 약칭. 계층 키(``parcel_undervalued.py``의 dong_zone)에 원본 문자열을
그대로 쓰면 같은 용도지역이 표기 차이로 여러 계층으로 쪼개져 표본이 흩어진다.
그래서 여기서 한 번 정규화한다.

**용적률 숫자는 여기 없다.** 서울시 도시계획조례의 용도지역별 용적률 상한은
조례 원문으로 확인해야 하고, 기억으로 적으면 규제 수치를 틀리게 말하는 것이라
그 자체가 사고다. 지금은 **개발이 범주적으로 제한되는 용도지역만** 제외한다
(``DEVELOPMENT_RESTRICTED``). 용적률 기반의 정밀한 개발가능성 판정은 조례를
확인한 뒤 별도로 붙인다 — 그때까지 이 모듈의 판정은 "보수적 제외"에 그친다.
"""

from __future__ import annotations

import re

# 정규화된 대분류. 국토계획법 용도지역 체계를 따른다.
RESIDENTIAL_1_EXCLUSIVE = "residential_1_exclusive"   # 제1종전용주거지역
RESIDENTIAL_2_EXCLUSIVE = "residential_2_exclusive"   # 제2종전용주거지역
RESIDENTIAL_1_GENERAL = "residential_1_general"       # 제1종일반주거지역
RESIDENTIAL_2_GENERAL = "residential_2_general"       # 제2종일반주거지역
RESIDENTIAL_3_GENERAL = "residential_3_general"       # 제3종일반주거지역
RESIDENTIAL_SEMI = "residential_semi"                 # 준주거지역
COMMERCIAL_CENTRAL = "commercial_central"             # 중심상업지역
COMMERCIAL_GENERAL = "commercial_general"             # 일반상업지역
COMMERCIAL_NEIGHBOR = "commercial_neighbor"           # 근린상업지역
COMMERCIAL_DISTRIBUTION = "commercial_distribution"   # 유통상업지역
INDUSTRIAL_EXCLUSIVE = "industrial_exclusive"         # 전용공업지역
INDUSTRIAL_GENERAL = "industrial_general"             # 일반공업지역
INDUSTRIAL_SEMI = "industrial_semi"                   # 준공업지역
GREEN_CONSERVATION = "green_conservation"             # 보전녹지지역
GREEN_PRODUCTION = "green_production"                 # 생산녹지지역
GREEN_NATURAL = "green_natural"                       # 자연녹지지역
RESTRICTED_DEVELOPMENT = "restricted_development"     # 개발제한구역

# 원본 명칭 → 대분류. 위에서부터 먼저 맞는 것을 쓰므로 **더 좁은 패턴이 위에**
# 와야 한다 ("제1종전용"이 "전용"보다 먼저, "준주거"가 "주거"보다 먼저).
_PATTERNS: list[tuple[str, str]] = [
    (r"개발제한", RESTRICTED_DEVELOPMENT),
    (r"제1종전용주거", RESIDENTIAL_1_EXCLUSIVE),
    (r"제2종전용주거", RESIDENTIAL_2_EXCLUSIVE),
    (r"제1종일반주거", RESIDENTIAL_1_GENERAL),
    (r"제2종일반주거", RESIDENTIAL_2_GENERAL),
    (r"제3종일반주거", RESIDENTIAL_3_GENERAL),
    (r"준주거", RESIDENTIAL_SEMI),
    (r"중심상업", COMMERCIAL_CENTRAL),
    (r"일반상업", COMMERCIAL_GENERAL),
    (r"근린상업", COMMERCIAL_NEIGHBOR),
    (r"유통상업", COMMERCIAL_DISTRIBUTION),
    (r"전용공업", INDUSTRIAL_EXCLUSIVE),
    (r"일반공업", INDUSTRIAL_GENERAL),
    (r"준공업", INDUSTRIAL_SEMI),
    (r"보전녹지", GREEN_CONSERVATION),
    (r"생산녹지", GREEN_PRODUCTION),
    (r"자연녹지", GREEN_NATURAL),
]

# 개발이 범주적으로 제한돼 "구조가 좋은데 싸다"가 저평가가 아닌 용도지역.
# 이런 필지는 값이 낮은 게 정상이므로 판정에서 빼야 한다 — 안 빼면 상위 목록이
# 그린벨트와 녹지로 채워지고, 개발업자가 첫 화면에서 신뢰를 잃는다.
#
# 주의: 자연녹지지역은 제한적 개발이 가능하다. 여기 넣은 것은 **보수적 선택**이며,
# 용적률 기반 정밀 판정이 붙으면 다시 볼 것. 넓게 빼는 쪽이 틀린 것을 추천하는
# 것보다 싸다.
DEVELOPMENT_RESTRICTED: frozenset[str] = frozenset(
    {
        RESTRICTED_DEVELOPMENT,
        GREEN_CONSERVATION,
        GREEN_PRODUCTION,
        GREEN_NATURAL,
    }
)

_STRIP_RE = re.compile(r"[\s()\[\]{}·,]+")


def normalize_zone_name(raw: str | None) -> str | None:
    """원본 용도지역 명칭 → 대분류 키. 못 알아보면 None.

    None을 돌려주면 그 필지는 판정에서 제외된다(``parcel_undervalued`` 쪽에서
    zone_code 없음으로 처리) — 모르는 용도지역을 아무 계층에 넣으면 통제가 깨지므로,
    조용히 뭉치는 것보다 빼는 게 맞다. 배치가 미인식 명칭을 집계해 로그로 남긴다.
    """
    if not raw:
        return None
    flat = _STRIP_RE.sub("", raw)
    for pattern, group in _PATTERNS:
        if re.search(pattern, flat):
            return group
    return None


def is_buildable(zone_group: str | None) -> bool:
    """이 용도지역에서 개발이 (범주적으로) 가능한가.

    ``DEVELOPMENT_RESTRICTED``에 들면 False. 모르는 값(None)도 False로 둔다 —
    판정 근거가 없는 필지를 "지을 수 있다"고 가정하면 안 된다.
    """
    if zone_group is None:
        return False
    return zone_group not in DEVELOPMENT_RESTRICTED
