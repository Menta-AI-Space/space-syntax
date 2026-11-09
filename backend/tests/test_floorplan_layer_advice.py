"""벽 레이어 자동 추천 — 실제 도면에서 **틀렸던 방식**들을 고정해 둔다.

여기 있는 사례는 전부 2026-10-01에 실무 도면 3장으로 실패를 본 것들이다.
합성 도면으로 옮겨 적어 두지 않으면 다음에 또 같은 방식으로 되돌아간다.
"""

from __future__ import annotations

from app.services.floorplan_layer_advice import suggest_wall_layers


def _rect(x0, y0, x1, y1):
    return [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]


def test_외벽이_다른_레이어에_있으면_찾아_더한다():
    # 이름이 벽인 레이어엔 내부 칸막이만 있고 외벽은 `envelope`에 있다.
    layers = {
        "WALL": [(10.0, 0.0, 10.0, 20.0)],
        "envelope": _rect(0.0, 0.0, 20.0, 20.0),
    }
    advice = suggest_wall_layers(layers, ["WALL"], 20.0, 20.0)

    assert "envelope" in advice.wall_layers
    assert advice.indoor_after > advice.indoor_before
    # 20×20 안쪽이 두 칸으로 갈렸으니 가장 큰 덩어리는 대략 절반이다.
    assert 120.0 < advice.indoor_after < 220.0


def test_해치_뭉치를_벽으로_집지_않는다():
    # `1층평면도.dwg`의 `fin`이 이렇게 생겼다 — 포장·조경선이 빽빽해서 선 사이에
    # 갇힌 자잘한 칸이 수백 개 나온다. **면적 합계로 점수를 매기면** 이게 이긴다.
    hatch = []
    for i in range(1, 40):
        hatch.append((float(i), 0.0, float(i), 40.0))
        hatch.append((0.0, float(i), 40.0, float(i)))
    layers = {"WALL": _rect(5.0, 5.0, 15.0, 15.0), "fin": hatch}
    advice = suggest_wall_layers(layers, ["WALL"], 40.0, 40.0)

    assert "fin" not in advice.wall_layers
    assert advice.added == []


def test_대지경계선으로_땅을_감싸지_않는다():
    # 첫 방식은 「선이 감싼 상자 대비 실내」로 점수를 매겨 `A-SP-LOT`을 벽으로
    # 집었다 — 건물이 아니라 땅을 감싸 놓고 점수가 올랐다.
    layers = {
        "A-WL-FUL": _rect(40.0, 40.0, 60.0, 60.0),
        "A-SP-LOT": _rect(0.0, 0.0, 100.0, 100.0),
    }
    advice = suggest_wall_layers(layers, ["A-WL-FUL"], 100.0, 100.0)

    assert advice.wall_layers == ["A-WL-FUL"]
    # 분석 영역도 대지가 아니라 건물이어야 한다.
    assert advice.core_box is not None
    assert advice.core_box[2] - advice.core_box[0] < 40.0


def test_한_장에_도면이_여럿이면_벽이_든_쪽을_고른다():
    # `1층평면도(1).dwg`가 이랬다. 덩어리를 **크기**로 고르면 벽이 없는 옆 도면을
    # 집는다 — 고르는 기준은 이름으로 찾은 벽이 얼마나 들었나여야 한다.
    big_but_wall_less = []
    for i in range(0, 60, 3):
        big_but_wall_less.append((200.0 + i, 0.0, 200.0 + i, 60.0))
    layers = {
        "WALL": _rect(0.0, 0.0, 20.0, 20.0),
        "landscape": big_but_wall_less,
    }
    advice = suggest_wall_layers(layers, ["WALL"], 300.0, 60.0)

    assert advice.core_box is not None
    assert advice.core_box[0] < 20.0  # 왼쪽 건물 쪽을 골랐다
    assert advice.indoor_after > 250.0


def test_벽을_하나도_못_찾으면_그렇게_말한다():
    advice = suggest_wall_layers({"TEXT": [(0.0, 0.0, 1.0, 1.0)]}, [], 10.0, 10.0)

    assert advice.wall_layers == []
    assert "직접 골라" in advice.reason
