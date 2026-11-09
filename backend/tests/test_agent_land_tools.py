"""땅값 도구 셋 — **에이전트 레지스트리에서 테스트한다.**

이것이 이 기능에서 가장 높은 seam이다: 한국어 질문을 넣고 올바른 도구가 골라지는지
본다. 갭 점수 계산식이나 정규식 내부는 보지 않는다.

특히 지키는 것: **상가 입지 질문이 땅값 도구로 새지 않는다.** 새면 카페 자리를 물은
사람에게 공시지가 순위가 나간다.
"""

import asyncio

import pytest

from app.routers import agent as ag


def _tools(query: str, **kw) -> list[str]:
    return ag._rule_tools(query, kw.pop("params", {}), kw.pop("place_count", 1), **kw)


# ── 범위별로 도구가 갈린다 ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "query",
    [
        "여의도동 12-3 이 땅 어때?",
        "이 땅 공시지가 얼마야",
        "여기 토지 괜찮아?",
        "그 필지 어떤지 봐줘",
    ],
)
def test_parcel_scope_questions_pick_the_parcel_tool(query):
    assert _tools(query)[0] == "land_parcel"


@pytest.mark.parametrize(
    "query",
    [
        "여의도동에서 싼 땅 있어?",
        "역삼동 저평가된 필지 찾아줘",
        "이 동네 저렴한 토지 알려줘",
    ],
)
def test_dong_scope_questions_pick_the_dong_tool(query):
    assert _tools(query)[0] == "land_dong"


@pytest.mark.parametrize(
    "query",
    [
        "서울에서 제일 싼 땅 어디야",
        "서울에서 가장 저평가된 동네 순위 보여줘",
        "땅값 제일 싼 데는 어디",
    ],
)
def test_city_scope_questions_pick_the_ranking_tool(query):
    assert _tools(query)[0] == "land_ranking"


def test_only_one_land_tool_fires_per_question():
    """셋은 같은 대상을 크기만 바꿔 보는 것이라 둘 이상 돌리면 답이 중복된다."""
    for query in ("여의도동 싼 땅", "이 땅 어때", "서울에서 제일 싼 땅"):
        picked = [t for t in _tools(query) if t.startswith("land_")]
        assert len(picked) == 1, f"{query}: {picked}"


# ── 다른 질문으로 새지 않는다 ────────────────────────────────────────────

@pytest.mark.parametrize(
    "query",
    [
        "강남역 카페 어때?",
        "연남동에 미용실 낼 만할까",
        "이 자리 월세 300이면 계약해도 될까",
        "강남구 아파트 시세 알려줘",
        "도보 10분 안에 몇 명 살아",
    ],
)
def test_non_land_questions_never_reach_the_land_tools(query):
    picked = [t for t in _tools(query) if t.startswith("land_")]
    assert picked == [], f"{query}가 땅값 도구로 샜다: {picked}"


def test_a_land_question_does_not_swallow_the_rent_verdict():
    """땅 얘기와 값 판정이 함께 오면 둘 다 남는다 — 신호가 겹치지 않는다."""
    tools = _tools("이 땅 어때? 월세 300이면 계약해도 될까", params={"monthly_rent_man": 300.0})
    assert "land_parcel" in tools
    assert any(t in tools for t in ("lease_verdict", "lease_feasibility"))


# ── 슬롯과 예산 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", ["land_parcel", "land_dong", "land_ranking"])
def test_land_tools_are_registered_with_a_label_and_are_ready_with_a_place(tool):
    spec = ag._TOOLS[tool]
    assert spec.label
    # 필요한 값은 "어디"뿐이고 그건 라우터가 도구 선택 전에 해소한다.
    ok, hint = ag._tool_ready(tool, ag._Intent(address="여의도동"), [ag._Point(37.5, 127.0, "여의도동")])
    assert ok and hint == ""


# ── 실행 핸들러가 실제로 있다 (등록만 하면 unknown tool로 죽는다) ────────

def _run(tool, monkeypatch, **patches):
    for name, value in patches.items():
        monkeypatch.setattr(ag, name, value)
    return asyncio.run(
        ag._run_tool(tool, ag._Intent(address="여의도동"), [ag._Point(37.5202, 126.9317, "여의도동")], 500)
    )


def test_parcel_tool_returns_the_structured_analysis(monkeypatch):
    from app.models.land_analysis import PeerGroup, Subject
    from app.services.land_verdict import VerdictInput, build_structured_analysis

    analysis = build_structured_analysis(
        subject=Subject(pnu="1" * 19, base_year=2026, address="여의도동 12-3"),
        peer_group=PeerGroup(description="같은 동 250필지", size=250, level="dong_zone"),
        inp=VerdictInput(peer_count=250, actual_price_per_sqm=8_300_000, gap_score=12.0),
        model_version="no-estimator",
    )
    tool, result = _run("land_parcel", monkeypatch, analyze_parcel_at=lambda lat, lng: analysis)
    assert tool == "land_parcel"
    assert result.subject.address == "여의도동 12-3"


def test_parcel_tool_returns_none_when_no_parcel_is_near(monkeypatch):
    """지오코딩이 동 중심을 준 경우 등 — 지어내지 않고 '찾지 못했다'로 간다."""
    _, result = _run("land_parcel", monkeypatch, analyze_parcel_at=lambda lat, lng: None)
    assert result is None


def test_dong_tool_lists_parcels_with_their_comparison_size(monkeypatch):
    rows = [
        {
            "ldong_name": "여의도동",
            "jibun": "21-5",
            "gap_score": 91.6,
            "price_per_sqm": 7_000_000,
            "area_sqm": 300.0,
            "road_side_name": "중로한면",
            "stratum_size": 250,
        }
    ]
    _, result = _run(
        "land_dong",
        monkeypatch,
        dong_adm_cd_at=lambda lat, lng: ("11190540", "서울특별시 영등포구 여의동"),
        list_top_parcels_in_dong=lambda adm_cd, limit=10: rows,
    )
    assert result["adm_nm"].endswith("여의동")
    assert result["parcels"][0]["address"] == "여의도동 21-5"
    assert result["parcels"][0]["stratum_size"] == 250


def test_dong_tool_returns_none_when_the_batch_has_not_run(monkeypatch):
    _, result = _run(
        "land_dong",
        monkeypatch,
        dong_adm_cd_at=lambda lat, lng: ("11190540", "여의동"),
        list_top_parcels_in_dong=lambda adm_cd, limit=10: [],
    )
    assert result is None


def test_ranking_tool_returns_dong_rows(monkeypatch):
    _, result = _run(
        "land_ranking",
        monkeypatch,
        list_top_undervalued=lambda limit: [{"adm_nm": "여의동", "gap_score": 30.0}],
    )
    assert result["dongs"][0]["adm_nm"] == "여의동"


# ── 서술로 넘기는 표현 ───────────────────────────────────────────────────

def test_facts_never_call_the_official_price_a_market_price():
    """서술 단계에서 공시지가가 시세로 바뀌면 사용자는 거래가로 읽는다."""
    facts = ag._tool_facts_from_dict(
        "land_parcel",
        {
            "verdict": {"headline": "판정을 보류했습니다"},
            "official_price": {"per_sqm": 16_740_000, "base_year": 2026},
            "peer_group": {"description": "같은 동 250필지", "median_official_price_per_sqm": 22_150_000},
            "peer_position": {"gap_score": 5.4},
            "factors": [{"label": "넓은 길에 접함", "direction": "positive"}],
            "narrative": {"paragraphs": ["한 문장."]},
        },
    )
    assert "개별공시지가" in facts
    assert "시세가 아니다" in facts
    # 낙인 표현·확정 평가액 표현이 서술 재료에 없다.
    assert "고평가" not in facts
    assert "감정평가" not in facts


def test_dong_facts_explain_what_the_gap_is_not():
    facts = ag._tool_facts_from_dict(
        "land_dong",
        {
            "adm_nm": "여의동",
            "parcels": [
                {
                    "address": "여의도동 21-5",
                    "gap_score": 91.6,
                    "official_price_per_sqm": 7_000_000,
                    "stratum_size": 250,
                }
            ],
        },
    )
    assert "같은 조건 250필지 중" in facts
    assert "시세 대비 할인율이 아니다" in facts
