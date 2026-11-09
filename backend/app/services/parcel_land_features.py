"""토지특성 원문 → 화면과 모델이 쓰는 파생값. **순수 함수, DB 없음.**

`parcel_characteristics`의 원문 컬럼은 사람이 읽을 수도, 모델이 먹을 수도 없는
형태다. `road_side_name`이 "광대소각" 한 덩어리에 폭 등급·각지 여부를 같이 담고
있고, `topograph_form`은 "부정형"처럼 뜻은 알겠는데 순서가 없다.

여기서 셋으로 쪼갠다:

- **순서형 폭 등급** (`width_grade`): 세로 1 → 소로 2 → 중로 3 → 광대 4.
  모델이 순서를 쓸 수 있게 숫자로 만든다.
- **각지 여부** (`corner`): 두 면 이상이 도로에 닿는가. 같은 폭이라도 각지가 비싸다.
- **맹지 플래그** (`landlocked`): 도로에 닿지 않는가. 그대로는 건축 허가가 어려워
  값에 가장 크게 작용하는 단점이다.

⚠️ **결측을 0으로 채우지 않는다.** `road_side_name`은 실측 표본에서 63%가
"지정되지않음"이었다. 0으로 채우면 "정보 없음"이 "맹지"(가장 나쁨)로 학습되고,
화면에서는 도로에 닿는 땅이 맹지로 표시된다. 모르는 것은 `None`으로 둔다.

⚠️ **폭 구간은 개별공시지가 도로접면 구분의 정의를 그대로 쓴다** — 광대 25m 이상 ·
중로 12m 이상 25m 미만 · 소로 8m 이상 12m 미만 · 세로 8m 미만. 우리가 정한 값이
아니라 평가 기준의 값이므로, 바꾸려면 근거 문서를 함께 고쳐야 한다.

⚠️ **순환성** — 도로접면은 토지가격비준표 항목 그 자체다. 이 파생값으로 공시지가를
예측하면 상당 부분 행정 산식을 재현한다. 화면 설명에는 유용하지만, 모델 성능의
근거로 쓸 때는 이것을 통제한 뒤의 증분만 본다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 폭 등급. 순서가 뜻을 갖는다 — 그래서 문자열이 아니라 정수다.
WIDTH_GRADE_LABELS: dict[int, str] = {
    4: "광대 (25m 이상)",
    3: "중로 (12~25m)",
    2: "소로 (8~12m)",
    1: "세로 (8m 미만)",
}

# 원문 접두어 → 폭 등급. 긴 것부터 본다("광대"가 "대"보다 먼저).
_WIDTH_PREFIXES: tuple[tuple[str, int], ...] = (
    ("광대", 4),
    ("중로", 3),
    ("소로", 2),
    ("세로", 1),
)

# 각지를 뜻하는 표기. 원문이 "광대소각"·"중로각지"·"세로각지(가)"처럼 섞여 있다.
_CORNER_MARKS = ("각지", "소각", "세각")

# 값이 비어 있는 것과 같은 표기들. 원본이 빈 문자열 대신 이 말을 넣어 온다.
_MISSING_MARKS = ("", "지정되지않음", "지정되지 않음", "미지정", "해당없음")


@dataclass(frozen=True)
class RoadFrontage:
    """도로접면 하나를 쪼갠 결과.

    `width_grade`가 None이면 **모른다**는 뜻이다 — 맹지(등급 없음)와 구분된다.
    맹지는 `landlocked=True`이고 `width_grade`는 None이다.
    """

    width_grade: int | None
    corner: bool | None
    landlocked: bool | None
    # 자동차 접근 가능 여부. 세로 계열의 (가)/(불) 표기에서만 나온다.
    car_accessible: bool | None
    raw: str | None

    @property
    def known(self) -> bool:
        return self.landlocked is not None

    def display(self) -> str:
        """화면·서술에 그대로 쓰는 한 줄. 원문 코드를 노출하지 않는다."""
        if not self.known:
            return "확인되지 않음"
        if self.landlocked:
            return "맹지 (도로에 닿지 않음)"
        parts = [WIDTH_GRADE_LABELS.get(self.width_grade or 0, "도로에 접함")]
        if self.corner:
            parts.append("각지 (두 면 이상 접함)")
        if self.car_accessible is False:
            parts.append("차량 진입 어려움")
        return " · ".join(parts)


def parse_road_frontage(raw: str | None) -> RoadFrontage:
    """`road_side_name` 원문 한 덩어리를 폭·각지·맹지로 쪼갠다."""
    text = (raw or "").strip()
    if text in _MISSING_MARKS:
        return RoadFrontage(None, None, None, None, raw)

    if "맹지" in text:
        # 맹지는 폭 등급이 없다. 0을 넣으면 "가장 좁은 도로"와 섞인다.
        return RoadFrontage(None, False, True, False, raw)

    grade: int | None = None
    for prefix, value in _WIDTH_PREFIXES:
        if text.startswith(prefix):
            grade = value
            break

    corner = any(mark in text for mark in _CORNER_MARKS)
    car: bool | None = None
    if "(가)" in text:
        car = True
    elif "(불)" in text:
        car = False

    if grade is None:
        # "도로에 닿는다"는 것까지는 알지만 폭은 모르는 표기.
        return RoadFrontage(None, corner, False, car, raw)
    return RoadFrontage(grade, corner, False, car, raw)


# 형상. 순서가 있다 — 정형이 값에 유리하고 부정형·자루형이 불리하다.
# 원문(`topograph_form`)의 표기를 그대로 키로 쓴다.
SHAPE_LABELS: dict[str, str] = {
    "정방형": "정방형 (거의 정사각형)",
    "가로장방형": "가로로 긴 네모",
    "세로장방형": "세로로 긴 네모",
    "장방형": "긴 네모",
    "사다리형": "사다리 모양",
    "부정형": "부정형 (모양이 고르지 않음)",
    "자루형": "자루형 (진입부가 좁음)",
    "삼각형": "삼각형",
    "역삼각형": "역삼각형",
}

# 값에 불리하게 작용하는 형상. "단점이 값을 설명하는가"를 따질 때 쓴다.
UNFAVORABLE_SHAPES = ("부정형", "자루형", "삼각형", "역삼각형")

# 지세(고저). 급경사·저지는 개발이 어려워 값에 불리하다.
HEIGHT_LABELS: dict[str, str] = {
    "평지": "평지",
    "완경사": "완만한 경사",
    "급경사": "급경사",
    "고지": "주변보다 높음",
    "저지": "주변보다 낮음",
}
UNFAVORABLE_HEIGHTS = ("급경사", "저지")


def shape_display(raw: str | None) -> str | None:
    """형상 원문 → 화면 문구. 모르는 표기는 원문을 그대로 보여준다."""
    text = (raw or "").strip()
    if text in _MISSING_MARKS:
        return None
    return SHAPE_LABELS.get(text, text)


def height_display(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if text in _MISSING_MARKS:
        return None
    return HEIGHT_LABELS.get(text, text)


def is_unfavorable_shape(raw: str | None) -> bool:
    return (raw or "").strip() in UNFAVORABLE_SHAPES


def is_unfavorable_height(raw: str | None) -> bool:
    return (raw or "").strip() in UNFAVORABLE_HEIGHTS
