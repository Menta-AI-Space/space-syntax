"""말로 고치기 (2026-11-09) — 명령 → 선 계산과 낱말 규칙. GPT는 부르지 않는다(use_llm=False)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import floorplan_edit_intent as fe

# 단위 = m(meters_per_unit=1). 20×20 방 하나 + 가운데 가로 통로 벽 둘.
ROOM = [
    (0.0, 0.0, 40.0, 0.0),
    (40.0, 0.0, 40.0, 20.0),
    (40.0, 20.0, 0.0, 20.0),
    (0.0, 20.0, 0.0, 0.0),
    (0.0, 8.0, 40.0, 8.0),   # 4: 통로 위쪽 벽
    (0.0, 12.0, 40.0, 12.0),  # 5: 통로 아래쪽 벽 (통로 폭 4m, 가로로 길다)
]


def run(instruction, point, *, box=None, lines=ROOM, openings=(), labels=(), llm=False):
    return asyncio.run(
        fe.plan_edit(
            instruction=instruction,
            point=point,
            box=box,
            lines=list(lines),
            openings=list(openings),
            labels=list(labels),
            meters_per_unit=1.0,
            canvas_width=40,
            canvas_height=20,
            use_llm=llm,
        )
    )


def test_door_on_nearest_wall_at_selection():
    plan = run("여기 문 내줘", (39.5, 15.0))
    assert plan.question is None and plan.source == "rule"
    assert len(plan.add_openings) == 1 and not plan.remove_lines
    x1, y1, x2, y2 = plan.add_openings[0]
    assert x1 == pytest.approx(40) and x2 == pytest.approx(40)
    assert abs(y2 - y1) == pytest.approx(3.0)
    assert (y1 + y2) / 2 == pytest.approx(15.0)


def test_door_width_from_words_and_clamped_inside_wall():
    plan = run("문 2미터로 뚫어줘", (39.5, 19.5))
    x1, y1, x2, y2 = plan.add_openings[0]
    assert abs(y2 - y1) == pytest.approx(2.0)
    assert max(y1, y2) <= 20.0 + 1e-9  # 벽 끝을 넘지 않는다


def test_remove_wall_takes_overlapping_duplicate():
    lines = ROOM + [(40.0, 20.0, 40.0, 0.0)]  # 오른쪽 벽을 겹쳐 그림
    plan = run("이 벽 없애줘", (39.0, 10.0), lines=lines)
    assert plan.remove_lines == [1, 6]
    assert "겹쳐 그린 선 1개" in plan.message


def test_clear_box_keeps_parts_outside():
    plan = run("여기 다 터줘", (20, 10), box=(15.0, 6.0, 25.0, 14.0))
    assert plan.remove_lines == [4, 5]
    pieces = sorted(plan.add_lines)
    assert pieces == [
        pytest.approx((0.0, 8.0, 15.0, 8.0)),
        pytest.approx((0.0, 12.0, 15.0, 12.0)),
        pytest.approx((25.0, 8.0, 40.0, 8.0)),
        pytest.approx((25.0, 12.0, 40.0, 12.0)),
    ]


def test_close_door():
    plan = run("이 문 막아줘", (20.5, 8.2), openings=[(19.0, 8.0, 22.0, 8.0)])
    assert plan.remove_openings == [0] and not plan.add_lines


def test_block_corridor_crosses_the_narrow_way_whatever_the_box_shape():
    # 통로를 길게 고르든(가로 네모) 가로질러 끌든(세로 네모) 세로 벽이 선다 — 통로 폭이 더 좁으므로.
    for box in ((10.0, 9.0, 30.0, 11.0), (19.0, 7.0, 21.0, 13.0)):
        plan = run("여기 벽 세워서 막아줘", (20, 10), box=box)
        assert len(plan.add_lines) == 1, plan.warnings
        x1, y1, x2, y2 = plan.add_lines[0]
        assert x1 == pytest.approx(20) and x2 == pytest.approx(20)
        assert sorted((y1, y2)) == pytest.approx([8.0, 12.0])


def test_unknown_instruction_asks_back():
    plan = run("예쁘게 해줘", (20, 10))
    assert plan.question and not plan.add_lines and not plan.remove_lines


def test_llm_ops_validated_and_bad_ids_fall_back_to_rules(monkeypatch):
    async def fake(**kwargs):
        assert "w1" in kwargs["user"]
        return {"ops": [{"op": "remove_wall", "wall": "w99"}], "message": "엉뚱"}

    monkeypatch.setattr(fe, "complete_json", fake)
    plan = run("이 벽 없애줘", (39.0, 10.0), llm=True)
    assert plan.source == "rule" and plan.remove_lines == [1]


def test_llm_far_coordinates_rejected(monkeypatch):
    async def fake(**kwargs):
        return {"ops": [{"op": "add_wall", "from": [500, 500], "to": [600, 500]}], "message": "벽"}

    monkeypatch.setattr(fe, "complete_json", fake)
    plan = run("벽 세워줘", (20, 10), llm=True)
    # GPT 명령은 버려지고 규칙(가로지르는 벽)으로 넘어간다.
    assert plan.source == "rule" and len(plan.add_lines) == 1


def test_llm_question_passes_through(monkeypatch):
    async def fake(**kwargs):
        return {"ops": [], "message": "", "question": "어느 벽 말씀이세요?"}

    monkeypatch.setattr(fe, "complete_json", fake)
    plan = run("저거 바꿔", (20, 10), llm=True)
    assert plan.question == "어느 벽 말씀이세요?" and plan.source == "llm"


def test_router_returns_indices(monkeypatch):
    async def off(**kwargs):
        return None

    monkeypatch.setattr(fe, "complete_json", off)
    client = TestClient(app)
    res = client.post(
        "/api/floorplan/edit-intent",
        json={
            "instruction": "여기 문 내줘",
            "point": {"x": 39.5, "y": 15},
            "canvas_width": 40,
            "canvas_height": 20,
            "meters_per_unit": 1,
            "labels": [{"text": "매장 07", "x": 35, "y": 15}],
            "lines": [dict(zip(("x1", "y1", "x2", "y2"), s)) for s in ROOM],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["add_openings"]) == 1 and body["question"] is None
    assert body["steps"][0]["op"] == "add_door"
    assert "7번 매장" in body["message"]


def test_empty_box_says_why():
    plan = run("여기 벽 다 터줘", (20, 4), box=(15.0, 2.0, 25.0, 6.0))
    assert plan.question == "고른 네모 안에 지울 벽이 없습니다." and not plan.add_lines


def test_proposal_asks_before_applying():
    plan = run("여기 문 내줘", (39.5, 15.0))
    assert plan.message.endswith("적용할까요?")
    assert "문을 냅니다" in plan.message


def test_correction_uses_previous_instruction():
    plan = asyncio.run(
        fe.plan_edit(
            instruction="아니 2미터로",
            previous_instruction="여기 문 내줘",
            point=(39.5, 15.0), box=None, lines=list(ROOM), openings=[], labels=[],
            meters_per_unit=1.0, canvas_width=40, canvas_height=20, use_llm=False,
        )
    )
    assert plan.question is None and len(plan.add_openings) == 1
    x1, y1, x2, y2 = plan.add_openings[0]
    assert abs(y2 - y1) == pytest.approx(2.0)


# 가운데 세로벽(20,12~20)을 사이에 두고 왼쪽 「매장 17」, 오른쪽 「매장 18」. 앞벽은 y=12 가로벽.
SHOPS = ROOM + [(20.0, 12.0, 20.0, 20.0)]
SHOP_LABELS = [("매장 17", 15.0, 16.0), ("매장 18", 25.0, 16.0)]


def test_prompt_names_rooms_on_both_sides_of_a_wall(monkeypatch):
    seen = {}

    async def fake(**kwargs):
        seen["user"] = kwargs["user"]
        return None

    monkeypatch.setattr(fe, "complete_json", fake)
    run("17번이랑 18번 사이 벽 뚫어줘", (20.5, 13.0), lines=SHOPS, labels=SHOP_LABELS, llm=True)
    assert "양쪽: 18번 매장 | 17번 매장" in seen["user"] or "양쪽: 17번 매장 | 18번 매장" in seen["user"]


def test_open_instruction_rejects_close_door_answer(monkeypatch):
    async def fake(**kwargs):
        return {"ops": [{"op": "close_door", "door": "d1"}], "message": "기존 문 d1을 메울게요. 적용할까요?"}

    monkeypatch.setattr(fe, "complete_json", fake)
    plan = run("벽 뚫어서 통로 내줘", (39.5, 15.0), openings=[(39.0, 2.0, 39.0, 4.0)], llm=True)
    assert plan.source == "rule" and plan.add_openings and not plan.remove_openings


def test_internal_ids_never_reach_the_user(monkeypatch):
    async def fake(**kwargs):
        return {"ops": [{"op": "add_door", "wall": "w1"}], "message": "w1 벽에 문을 낼게요. 적용할까요?"}

    monkeypatch.setattr(fe, "complete_json", fake)
    plan = run("여기 문 내줘", (39.5, 15.0), llm=True)
    assert plan.source == "llm" and "w1" not in plan.message and plan.message.endswith("적용할까요?")


def test_where_names_the_wall_between_two_rooms():
    plan = run("이 벽 없애줘", (20.2, 16.0), lines=SHOPS, labels=SHOP_LABELS)
    assert "17번 매장과 18번 매장 사이 세로 벽" in plan.message or "18번 매장과 17번 매장 사이 세로 벽" in plan.message
