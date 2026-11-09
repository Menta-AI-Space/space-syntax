"""Unit tests for the /api/agent/ask orchestrator's pure logic.

The site-score engine call needs PostGIS, so these tests cover only the parts
that don't: intent extraction (LLM + rule fallback), the honest step trace, and
the rule-based answer. site_score/complete_* are the seams we stub.
"""

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException

from app.routers import agent


def test_keyword_business_type_maps_common_words():
    assert agent._keyword_business_type("신촌 카페 자리 어때") == "cafe"
    assert agent._keyword_business_type("여기 편의점 내도 될까") == "convenience"
    assert agent._keyword_business_type("동네 밥집 열 자리") == "restaurant"
    # 세부 메뉴어는 프리셋(한식 고정)이 아니라 업종 표에서 정확한 소분류로 간다.
    assert agent._keyword_business_type("치킨집 열 자리") == "general"
    assert agent._keyword_business_type("치과 개원 자리") == "clinic"
    assert agent._keyword_business_type("영어학원 자리") == "academy"
    assert agent._keyword_business_type("그냥 여기 점수 매겨줘") == "general"
    # 회귀 방지: "샐러드"는 정밀 소분류 경로에도 안 걸려 general로 새면서
    # 답변이 통째로 비어버렸다(2026-07-28 사용자 리포트) — 최소한으로 구제.
    assert agent._keyword_business_type("홍대입구샐러드집 괜찮아?") == "restaurant"


def test_keyword_business_type_maps_expanded_industries():
    assert agent._keyword_business_type("서강대역 근처 미용실 자리 분석") == "beauty"
    assert agent._keyword_business_type("네일샵 열 자리") == "beauty"
    assert agent._keyword_business_type("노래방 자리 어때") == "leisure"
    assert agent._keyword_business_type("여기 헬스장 되나") == "fitness"
    assert agent._keyword_business_type("필라테스 스튜디오 자리") == "fitness"
    assert agent._keyword_business_type("모텔 자리 분석") == "lodging"
    assert agent._keyword_business_type("주유소 낼 자리") == "gas_station"
    assert agent._keyword_business_type("전기차충전소 자리") == "ev_charging"
    assert agent._keyword_business_type("공인중개사 사무소 자리") == "professional_office"
    # 미용학원은 미용(beauty)이 아니라 학원(academy)으로 — 순서 우선.
    assert agent._keyword_business_type("미용학원 자리") == "academy"


def test_expanded_business_types_resolve_to_a_profile():
    # 에이전트가 넘기는 business_type이 실제 업종 프로파일로 연결되어야
    # site_score가 general(→500)로 새지 않는다.
    from app.services.profile_resolver import ProfileResolver

    resolver = ProfileResolver.load()
    for btype in ("beauty", "fitness", "leisure", "lodging",
                  "gas_station", "ev_charging", "professional_office"):
        resolved = resolver.resolve(business_type=btype)
        assert resolved is not None, f"{btype} 이(가) 프로파일로 연결되지 않음"


def test_rule_extract_address_strips_filler_and_category():
    assert agent._rule_extract_address("신촌에 카페 자리 점수 매겨줘") == "신촌"
    assert agent._rule_extract_address("강남역 근처 편의점 자리 어때?") == "강남역"
    assert agent._rule_extract_address("홍대입구 음식점 자리 분석해줘") == "홍대입구"
    assert agent._rule_extract_address("신촌 카페") == "신촌"
    # 지명만 있으면 그대로
    assert agent._rule_extract_address("신촌역") == "신촌역"
    # 짧은 지명의 조사처럼 보이는 끝글자는 보존 (종로의 '로')
    assert agent._rule_extract_address("종로") == "종로"
    # 업종어에 상호 접미사가 붙어도 지명에 섞이면 안 된다 — '응암동 미용샵'을
    # 통째로 지오코딩에 넘겨 "위치를 찾지 못했어요"가 났다.
    assert agent._rule_extract_address("응암동 미용샵 어때") == "응암동"
    # 업종 표에서 찾은 낱말도 접미사째로 걷어낸다.
    assert agent._rule_extract_address("홍제동 치킨집 될까", drop={"치킨집"}) == "홍제동"


def test_rule_extract_address_drops_predicate_tails_not_in_the_word_list():
    """낱말 목록에 없는 서술어 꼬리도 지명에서 걷어낸다.

    실측 버그: 예시 칩 "강남역 편의점 낼 만할까?"에서 '만할까'가 목록에 없어
    지명이 '강남역 만할까'가 되고 "분석할 위치를 찾지 못했어요"가 났다. 한국어
    어미를 목록으로 다 적을 수는 없으므로 꼬리 패턴으로 잡는다.
    """
    assert agent._rule_extract_address("강남역 편의점 낼 만할까?") == "강남역"
    assert agent._rule_extract_address("홍대입구 음식점 여기 괜찮아?") == "홍대입구"
    assert agent._rule_extract_address("신촌 샐러디에 카페 차리면 어때?") == "신촌 샐러디"
    # 지명 접미사와 겹치는 꼬리는 지명 쪽을 살린다 — '양평면'을 '하면'처럼
    # 지우면 지명이 사라진다.
    assert agent._rule_extract_address("양평면 카페 어때") == "양평면"
    assert agent._rule_extract_address("종로") == "종로"
    assert agent._rule_extract_address("신도림3가 카페") == "신도림3가"


def test_resolve_point_narrows_from_the_right_when_the_whole_string_fails(monkeypatch):
    """추출기가 놓친 잔여물이 있어도 앞쪽 지명으로 좌표를 찾아낸다."""
    tried: list[str] = []

    def _no_dong(place):
        tried.append(place)
        return None

    async def _geocode(place):
        if place == "강남역":
            return (37.4979, 127.0276)
        raise agent.GeocodingError("not found")

    monkeypatch.setattr(agent, "resolve_unique_dong", _no_dong)
    monkeypatch.setattr(agent, "geocode_address", _geocode)

    point = asyncio.run(agent._resolve_point("강남역 만할까"))
    assert point is not None
    assert (round(point.lat, 4), round(point.lng, 4)) == (37.4979, 127.0276)
    assert tried[0] == "강남역 만할까", "먼저 문자열 전체로 시도한다"


def test_resolve_point_gives_up_instead_of_walking_to_a_bare_first_word(monkeypatch):
    """아무 후보도 못 찾으면 None이다 — 엉뚱한 곳을 찍지 않는다."""
    monkeypatch.setattr(agent, "resolve_unique_dong", lambda place: None)

    async def _never(place):
        raise agent.GeocodingError("not found")

    monkeypatch.setattr(agent, "geocode_address", _never)
    assert asyncio.run(agent._resolve_point("있지도 않은 지명 뭐라뭐라")) is None


def test_extract_intent_falls_back_to_rules_when_llm_off(monkeypatch):
    async def _no_llm(**_kwargs):
        return None

    monkeypatch.setattr(agent, "complete_json", _no_llm)
    intent = asyncio.run(agent._extract_intent("신촌역 카페 자리 점수 매겨줘"))
    assert intent.address == "신촌역"  # 규칙 추출기가 지명만 남김
    assert intent.business_type == "cafe"


def test_extract_intent_uses_llm_result(monkeypatch):
    async def _llm(**_kwargs):
        return {"address": "서울 서대문구 신촌로 90", "business_type": "cafe"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("신촌 어딘가 커피숍"))
    assert intent.address == "서울 서대문구 신촌로 90"
    assert intent.business_type == "cafe"


def test_extract_intent_rejects_unknown_business_type(monkeypatch):
    async def _llm(**_kwargs):
        return {"address": "서울 마포구", "business_type": "spaceship"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("마포 어딘가 카페"))
    assert intent.business_type == "cafe"  # 잘못된 값은 규칙 폴백으로 교정


def test_build_steps_only_lists_computed_sections():
    bare = SimpleNamespace(demand=None, transit=None, competition=None)
    keys = [s.key for s in agent._build_steps(bare)]
    assert keys == ["geocode", "roads", "score"]

    full = SimpleNamespace(demand=object(), transit=object(), competition=object())
    keys_full = [s.key for s in agent._build_steps(full)]
    assert keys_full == ["geocode", "roads", "demand", "transit", "competition", "score"]


def test_suggested_questions_returns_up_to_three_when_nothing_ran():
    out = agent._suggested_questions(set())
    assert 2 <= len(out) <= 3
    # 순서는 우선순위대로 — 2026-11-06부터 창업 준비자 기준(월세 질문이 먼저,
    # 2호점 잠식은 맨 뒤).
    assert out[0] == agent._SUGGESTION_TEMPLATES["lease_verdict"]
    assert agent._SUGGESTION_TEMPLATES["cannibalization"] not in out


def test_suggested_questions_skips_tools_already_executed():
    # 이번 응답이 이미 lease_verdict와 cannibalization을 돌렸으면, 그 질문은
    # 다시 권하지 않는다 — 물으나 마나 같은 값이 또 나오기 때문.
    out = agent._suggested_questions({"lease_verdict", "cannibalization"})
    assert agent._SUGGESTION_TEMPLATES["lease_verdict"] not in out
    assert agent._SUGGESTION_TEMPLATES["cannibalization"] not in out
    assert agent._SUGGESTION_TEMPLATES["trends"] in out


def test_suggested_questions_empty_when_every_candidate_already_ran():
    out = agent._suggested_questions(set(agent._SUGGESTION_PRIORITY))
    assert out == []


def test_rule_answer_prefers_verdict_one_liner():
    site = SimpleNamespace(
        verdict_one_liner="이 자리는 도보 접근성이 강점입니다.",
        total_score=82,
        grade="A",
        subscores=[SimpleNamespace(label="접근성", score=90)],
    )
    assert agent._rule_answer(site) == "이 자리는 도보 접근성이 강점입니다."


def test_facts_for_answer_uses_real_competition_fields():
    # 회귀 방지: CompetitionOut엔 .summary가 없다 — 있는 필드만 써야 500이 안 난다.
    site = SimpleNamespace(
        upjong_breadcrumb="카페",
        business_label="카페",
        total_score=80,
        grade="A",
        subscores=[SimpleNamespace(label="접근성", score=88)],
        demand=SimpleNamespace(population=12000),
        competition=SimpleNamespace(same_category_count=7, intensity_label="치열"),
    )
    facts = agent._facts_for_answer(site)
    assert "동일업종 7곳" in facts and "치열" in facts
    assert "12,000" in facts


def test_agent_ask_wraps_unexpected_crash_in_friendly_422(monkeypatch):
    # 분석 파이프라인이 아직 미완성인 업종은 500 대신 친절한 422로 안내해야 한다.
    async def _llm(**_kwargs):
        return {"address": "신촌", "business_type": "leisure"}

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _boom(*_a, **_k):
        raise KeyError("downstream profile crash")

    monkeypatch.setattr(agent, "complete_json", _llm)
    # 순수 입지 질문엔 trends가 곁들여져 좌표가 먼저 필요하다 — 지오코딩 실패로
    # 이 테스트가 노리는 site_score 크래시 경로 전에 다른 400이 먼저 나지 않도록
    # 동네 좌표를 고정해 둔다(다른 대부분의 agent_ask 테스트와 같은 패턴).
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _boom)

    try:
        asyncio.run(agent.agent_ask(agent.AgentAskRequest(query="신촌 노래방 자리 분석")))
        assert False, "예외가 발생했어야 함"
    except HTTPException as e:
        assert e.status_code == 422
        assert "지원하지 않" in e.detail  # 무서운 500이 아니라 친절한 안내


def test_agent_ask_passes_through_location_errors(monkeypatch):
    # 위치·도로 관련 4xx는 삼키지 말고 그대로 올려 정확히 알려야 한다.
    async def _llm(**_kwargs):
        return {"address": "없는곳", "business_type": "cafe"}

    async def _location_error(*_a, **_k):
        raise HTTPException(status_code=400, detail="No place found for: 없는곳")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "run_site_score", _location_error)

    try:
        asyncio.run(agent.agent_ask(agent.AgentAskRequest(query="없는곳 카페 자리")))
        assert False, "예외가 발생했어야 함"
    except HTTPException as e:
        assert e.status_code == 400  # 업종 미지원(422)으로 뭉개지 않는다


def test_rule_answer_falls_back_to_top_subscore():
    site = SimpleNamespace(
        verdict_one_liner="",
        total_score=71,
        grade="B",
        subscores=[SimpleNamespace(label="유동인구", score=88), SimpleNamespace(label="경쟁", score=40)],
    )
    out = agent._rule_answer(site)
    assert "71점" in out and "유동인구" in out


def test_facts_for_answer_skips_none_subscores():
    # 회귀 방지: 파이프라인 미완성 업종은 subscore.score가 None으로 온다.
    # None과 float은 정렬이 안 돼 500이 났었다 — 점수 있는 항목만 써야 한다.
    site = SimpleNamespace(
        upjong_breadcrumb="노래방",
        business_label="노래방",
        total_score=62,
        grade="C",
        subscores=[
            SimpleNamespace(label="접근성", score=70),
            SimpleNamespace(label="수요", score=None),
            SimpleNamespace(label="경쟁", score=None),
        ],
        demand=None,
        competition=None,
    )
    facts = agent._facts_for_answer(site)  # 크래시하지 않아야 함
    # 내부 원점수(70점)는 넘기지 않는다 — LLM이 '접근성 70점'을 그대로 답변에
    # 인용해서 사용자에겐 의미 없는 숫자가 나갔다. 판정어는 엔진이 붙인다.
    assert "접근성(좋음)" in facts
    assert "70점" not in facts
    assert "None" not in facts


def test_rule_answer_ignores_none_subscores():
    site = SimpleNamespace(
        verdict_one_liner="",
        total_score=62,
        grade="C",
        subscores=[
            SimpleNamespace(label="접근성", score=70),
            SimpleNamespace(label="수요", score=None),
        ],
    )
    out = agent._rule_answer(site)  # max()가 None으로 크래시하지 않아야 함
    assert "62점" in out and "접근성" in out


def test_confident_rule_intent_skips_llm_for_clear_queries():
    # 업종 키워드 + 지명이 분명하면 LLM 파서를 부르지 않는다 (응답 예산 절약).
    clear = agent._confident_rule_intent("신촌에 카페 자리 점수 매겨줘")
    assert (clear.address, clear.business_type) == ("신촌", "cafe")
    clear2 = agent._confident_rule_intent("강남역 근처 편의점 자리 어때?")
    assert (clear2.address, clear2.business_type) == ("강남역", "convenience")


def test_confident_rule_intent_defers_to_llm_when_unsure():
    # 업종을 모르면(general) 규칙만으로 확신할 수 없다.
    assert agent._confident_rule_intent("여기 어때?") is None
    # 걷어낼 요청어가 없어 원문 그대로면 문장을 이해했다고 볼 수 없다.
    assert agent._confident_rule_intent("카페") is None


def test_extract_intent_uses_rules_without_calling_llm(monkeypatch):
    async def _boom(**_kwargs):  # LLM을 부르면 실패해야 한다
        raise AssertionError("clear query should not call the LLM parser")

    monkeypatch.setattr(agent, "complete_json", _boom)
    intent = asyncio.run(agent._extract_intent("신촌에 카페 자리 점수 매겨줘"))
    assert (intent.address, intent.business_type) == ("신촌", "cafe")


def test_confident_rule_intent_defers_when_place_is_vague(monkeypatch):
    # 지명이 한 낱말로 안 떨어지면('신촌 어딘가') LLM이 정식 주소로 정규화해야 한다.
    assert agent._confident_rule_intent("신촌 어딘가 커피숍") is None


def _site(**over):
    """_facts_for_answer/_rule_answer에 넣을 최소 사이트 응답 스텁."""
    base = dict(
        upjong_breadcrumb="",
        business_label="",
        verdict_one_liner="",
        total_score=72,
        grade="좋음",
        subscores=[],
        demand=None,
        transit=None,
        competition=None,
        sales_benchmark=None,
        industry_kpis=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_facts_never_claim_zero_competitors_when_counting_is_disabled():
    # 가장 위험했던 결함: 노래방·주유소처럼 업종 세부분류가 연결 안 된 업종은
    # 엔진이 동일업종 집계를 아예 하지 않고 count=0을 준다. 이걸 '동일업종 0곳'
    # 으로 넘겼더니 "이태원에 노래방 0곳 → 경쟁 매우 낮아 진입 유리"라는 사실과
    # 정반대 답변이 나갔다.
    site = _site(
        competition=SimpleNamespace(
            same_category_count=0, total_store_count=955,
            density_per_km2=0.0, match_method="disabled", intensity_label="",
        )
    )
    facts = agent._facts_for_answer(site, place="용산구 이태원동", business_label="오락·여가")
    assert "동일업종 0곳" not in facts
    assert "판단 불가" in facts
    assert "0곳/km²" not in facts

    answer = agent._rule_answer(site)
    assert "0곳" not in answer
    assert "판단할 수 없" in answer


def test_facts_still_report_real_competition_counts():
    site = _site(
        competition=SimpleNamespace(
            same_category_count=78, total_store_count=2408,
            density_per_km2=239.9, match_method="scls", intensity_label="매우 높음",
        )
    )
    facts = agent._facts_for_answer(site)
    assert "동일업종 78곳" in facts and "매우 높음" in facts


def test_facts_include_place_and_business_the_user_asked_about():
    # 이전엔 첫 줄이 "장소: {upjong_breadcrumb or business_label} 기준"이라
    # 지명이 아예 없었고, 노래방·주유소는 그 둘이 빈 문자열이라 업종도 사라졌다.
    # 그래서 주유소 질의에 "메뉴 구성·테이크아웃" 같은 엉뚱한 조언이 나왔다.
    facts = agent._facts_for_answer(
        _site(), place="강남구 역삼1동", business_label="주유소", radius_m=500
    )
    assert "강남구 역삼1동" in facts
    assert "반경 500m" in facts
    assert "업종: 주유소" in facts


def test_facts_exclude_competition_from_strength_ranking():
    # 경쟁 밀도는 'higher = more competition'이라 점수 방향이 반대다. 점수
    # 내림차순으로 강한 항목을 뽑으면 최악(100점)이 '강점'으로 나간다.
    site = _site(
        subscores=[
            SimpleNamespace(key="competition", label="경쟁 밀도", score=100.0, rank_top_pct=None),
            SimpleNamespace(key="accessibility", label="접근성", score=62.5, rank_top_pct=37.5),
            SimpleNamespace(key="demand", label="배후 수요", score=100.0, rank_top_pct=None),
        ]
    )
    facts = agent._facts_for_answer(site)
    assert "경쟁 밀도" not in facts
    assert "배후 수요(매우 좋음)" in facts
    assert "접근성(상위 38%)" in facts


def test_rank_verdict_is_decided_by_the_engine_not_the_llm():
    assert agent._rank_verdict(1) == "매우 많은 편"
    assert agent._rank_verdict(21) == "많은 편"
    assert agent._rank_verdict(68) == "적은 편"
    site = _site(
        demand=SimpleNamespace(
            seoul_rank=SimpleNamespace(
                population_top_pct=71, workers_top_pct=3, youth_top_pct=1
            )
        )
    )
    facts = agent._facts_for_answer(site)
    assert "거주인구 서울 상위 71%(적은 편)" in facts
    assert "직장인 서울 상위 3%(매우 많은 편)" in facts


def test_won_amounts_are_folded_into_readable_korean():
    # '5,421,847,864원'은 자릿수를 세야 크기를 알 수 있어 비교 기준으로 쓸모없다.
    assert agent._won(5_421_847_864) == "54.2억원"
    assert agent._won(17_357_788) == "1,736만원"
    site = _site(
        sales_benchmark=SimpleNamespace(
            trdar_nm="신촌역", monthly_sales_won=5_421_847_864,
            per_store_monthly_won=45_561_746,
            citywide_median_per_store_won=17_357_788, weekend_share_pct=32.6,
        )
    )
    facts = agent._facts_for_answer(site)
    assert "54.2억원" in facts
    assert "5,421,847,864" not in facts


def _site_with_seoul_baselines():
    """거주인구·직장인·연령대·아파트 시세·매출까지 서울 비교값이 다 붙은 스텁."""
    return _site(
        demand=SimpleNamespace(
            population=28_503,
            workers=15_320,
            seoul_rank=SimpleNamespace(
                population_top_pct=71, workers_top_pct=3, youth_top_pct=12
            ),
            age_bands=[
                SimpleNamespace(band="20대", pct=18.4),
                SimpleNamespace(band="30대", pct=20.1),
                SimpleNamespace(band="10대", pct=5.9),
            ],
            apartment_market=SimpleNamespace(
                median_price_per_sqm=1850.0, seoul_median_per_sqm=1500.0, vs_seoul_pct=23,
            ),
        ),
        sales_benchmark=SimpleNamespace(
            per_store_monthly_won=21_370_000, citywide_median_per_store_won=6_230_000,
        ),
    )


def test_comparison_table_rows_pair_each_local_value_with_its_seoul_baseline():
    site = _site_with_seoul_baselines()
    table = agent._comparison_table_md(site)
    assert table.splitlines() == [
        "| 지표 | 이 동네 | 서울 비교 |",
        "|---|---|---|",
        "| 거주인구 | 28,503명 | 서울 상위 71% |",
        "| 직장인 | 15,320명 | 서울 상위 3% |",
        # 10~20대 비중은 age_bands에서 '10대'+'20대' pct를 합해 잰다
        # (demand_benchmark.youth_pct_from_age_bands와 같은 정의라야 옆의
        # youth_top_pct 순위와 짝이 맞는다) — 30대(20.1%)는 빠져야 한다.
        "| 10~20대 인구 비중 | 24.3% | 서울 상위 12% |",
        "| 아파트 시세(㎡당) | 1,850만원 | 서울 중앙값 1,500만원/㎡ (+23%) |",
        "| 점포당 월매출 | 2,137만원 | 서울 중앙값 623만원 (+243%) |",
    ]


def test_comparison_table_skips_rows_without_a_seoul_baseline():
    # 이 동네 값은 있어도 비교 기준(서울 상위%·중앙값)이 없으면 그 줄은 뺀다 —
    # 비교 없는 절대값은 표의 목적에 안 맞고, 없는 기준을 지어내면 안 된다.
    site = _site(
        demand=SimpleNamespace(
            population=28_503, workers=None, seoul_rank=None,
            age_bands=[], apartment_market=None,
        ),
        sales_benchmark=SimpleNamespace(
            per_store_monthly_won=1_000_000, citywide_median_per_store_won=None,
        ),
    )
    assert agent._comparison_table_md(site) == ""


def test_write_answer_appends_comparison_table_to_llm_narrative(monkeypatch):
    async def _llm(**_kwargs):
        return "이 자리는 좋습니다."

    monkeypatch.setattr(agent, "complete_text", _llm)
    text, source = asyncio.run(agent._write_answer(_site_with_seoul_baselines()))
    assert source == "llm"
    assert text.startswith("이 자리는 좋습니다.")
    assert "**서울 비교**" in text
    assert "| 거주인구 | 28,503명 | 서울 상위 71% |" in text
    assert "| 점포당 월매출 | 2,137만원 | 서울 중앙값 623만원 (+243%) |" in text


def test_write_answer_appends_comparison_table_to_rule_fallback(monkeypatch):
    async def _no_llm(**_kwargs):
        return None

    monkeypatch.setattr(agent, "complete_text", _no_llm)
    text, source = asyncio.run(agent._write_answer(_site_with_seoul_baselines()))
    assert source == "rule"
    assert "**서울 비교**" in text
    assert "| 직장인 | 15,320명 | 서울 상위 3% |" in text


def test_write_answer_omits_table_section_when_no_baseline_available(monkeypatch):
    async def _llm(**_kwargs):
        return "이 자리는 좋습니다."

    monkeypatch.setattr(agent, "complete_text", _llm)
    site = _site(
        demand=SimpleNamespace(
            population=28_503, workers=None, seoul_rank=None,
            age_bands=[], apartment_market=None,
        )
    )
    text, _source = asyncio.run(agent._write_answer(site))
    assert "**서울 비교**" not in text


def test_agent_ask_asks_for_a_place_when_only_a_business_type_is_given(monkeypatch):
    # '카페'만 치면 지오코딩이 아무 카페나 하나 집어와 엉뚱한 곳을 분석하거나
    # 영어 404가 그대로 노출됐다. 물어보고 끝내야 한다.
    async def _llm(**_kwargs):
        return {"address": "", "business_type": "cafe"}

    async def _boom(*_a, **_k):
        raise AssertionError("장소가 없으면 엔진을 돌리면 안 된다")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "run_site_score", _boom)

    try:
        asyncio.run(agent.agent_ask(agent.AgentAskRequest(query="카페")))
        assert False, "예외가 발생했어야 함"
    except HTTPException as e:
        assert e.status_code == 400
        assert "어느 지역인지" in e.detail


def test_agent_ask_translates_internal_location_errors(monkeypatch):
    # 내부 영어 메시지가 그대로 사용자에게 나가면 안 된다.
    async def _llm(**_kwargs):
        return {"address": "안녕", "business_type": "general"}

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="안녕")

    async def _location_error(*_a, **_k):
        raise HTTPException(
            status_code=404,
            detail="No road segments found within 500m of the point",
        )

    monkeypatch.setattr(agent, "complete_json", _llm)
    # 순수 입지 질문엔 trends가 곁들여져 좌표가 먼저 필요하다 — 지오코딩 실패로
    # 이 테스트가 노리는 site_score 경로 전에 다른 400이 먼저 나지 않도록 동네
    # 좌표를 고정해 둔다(다른 대부분의 agent_ask 테스트와 같은 패턴).
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _location_error)

    try:
        asyncio.run(agent.agent_ask(agent.AgentAskRequest(query="안녕")))
        assert False, "예외가 발생했어야 함"
    except HTTPException as e:
        assert e.status_code == 404
        assert "No road segments" not in e.detail
        assert "찾지 못했어요" in e.detail


def test_agent_ask_answers_without_tools_when_no_place_can_be_found(monkeypatch):
    """위치를 못 잡은 질문에 되묻고 끝내지 않고, 도구 없이 답한다.

    종전엔 "'…'에서 분석할 위치를 찾지 못했어요" 한 문장이 전부였다. 그런데 이
    자리로 오는 말은 대개 위치를 빠뜨린 분석 요청이 아니라 개념·범위를 묻는
    질문이다("공시지가랑 실거래가 차이가 뭐야?").
    """
    async def _llm(**_kwargs):
        return None  # 파서 LLM은 꺼진 셈

    async def _talk(question, **_kwargs):
        assert "공시지가" in question
        return "공시지가는 정부가 매기는 가격이고, 실거래가는 실제로 거래된 값입니다."

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", lambda _q: None)
    monkeypatch.setattr(agent, "talk_answer", _talk)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="공시지가랑 실거래가 차이가 뭐야?", include_answer=False)
        )
    )

    assert resp.mode == "talk"
    assert resp.site is None, "도구를 돌리지 않았으므로 보고서가 없다"
    assert "공시지가" in resp.answer
    assert resp.answer_source == "llm"


def test_agent_ask_falls_back_to_the_old_message_when_the_llm_is_off(monkeypatch):
    """LLM이 없으면 종전 안내(400)로 돌아간다 — 조용히 빈 답을 주지 않는다."""
    async def _llm(**_kwargs):
        return None

    async def _no_talk(_question, **_kwargs):
        return None

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", lambda _q: None)
    monkeypatch.setattr(agent, "talk_answer", _no_talk)

    try:
        asyncio.run(
            agent.agent_ask(agent.AgentAskRequest(query="공시지가랑 실거래가 차이가 뭐야?"))
        )
        assert False, "예외가 발생했어야 함"
    except HTTPException as e:
        assert e.status_code == 400
        assert "찾지 못했어요" in e.detail or "어느 지역인지" in e.detail


def test_ask_request_defaults_to_including_answer():
    # 기존 호출자(프론트 외 다른 사용처)는 그대로 서술까지 받아야 한다.
    assert agent.AgentAskRequest(query="신촌 카페").include_answer is True
    assert agent.AgentAskRequest(query="신촌 카페", include_answer=False).include_answer is False


def test_agent_answer_endpoint_writes_narrative_without_rerunning_engine(monkeypatch):
    # 2단계 렌더: 이미 계산된 site를 받아 문장만 만든다.
    calls: list[str] = []

    seen_history: list = []

    async def _fake_write(site, *, place="", business_label="", radius_m=None,
                          question="", tool="site_score", tool_result=None, tool_results=None,
                          history=None, brief=False):
        calls.append(f"{place}|{business_label}|{radius_m}|{question}")
        seen_history.append(history)
        return "서술입니다.", "llm"

    monkeypatch.setattr(agent, "_write_answer", _fake_write)
    req = agent.AgentAnswerRequest.model_construct(
        site=SimpleNamespace(),
        place="서대문구 신촌동",
        business_label="카페",
        radius_m=500,
        query="신촌 카페 야간 장사 어때",
        tool="site_score",
        tool_result=None,
        tool_results={},
        history=[agent.AgentHistoryTurn(query="신촌 카페 어때", address="서대문구 신촌동")],
    )
    out = asyncio.run(agent.agent_answer(req))
    assert out.answer == "서술입니다." and out.answer_source == "llm"
    # 사용자가 물은 문장이 서술 단계까지 그대로 전달돼야 한다.
    assert calls == ["서대문구 신촌동|카페|500|신촌 카페 야간 장사 어때"]
    # 앞 대화도 전달돼야 한다 — 없으면 후속 질문마다 처음부터 다시 설명한다.
    assert seen_history and [t.query for t in seen_history[0]] == ["신촌 카페 어때"]


def test_agent_history_turn_accepts_business_label_the_frontend_sends():
    """프론트가 보내는 필드는 모델에 다 있어야 한다.

    2026-08-24 회귀: 모델에 business_label이 없는데 라우터 두 곳이 그 속성을
    읽어서, history가 **한 건이라도** 실리면 /ask가 AttributeError로 500이 났다.
    화면에서는 첫 답 뒤 두 번째 질문부터 "분석 서버에 연결하지 못했습니다"만 떠
    메시지가 아예 안 보내지는 것처럼 보였다.
    """
    turn = agent.AgentHistoryTurn(
        query="신촌 샐러디집에 카페를 차려볼까?",
        address="서대문구 신촌동",
        business_type="cafe",
        business_label="카페",
    )
    assert turn.business_label == "카페"
    # 프론트가 옛 버전이라 안 보내도 죽지 않아야 한다.
    assert agent.AgentHistoryTurn().business_label == ""


def test_talk_response_summarizes_history_without_crashing(monkeypatch):
    """되묻는 경로도 history를 읽는다 — 여기서도 같은 500이 났다."""
    seen: list[str] = []

    async def _fake_talk(query, *, history_summary=""):
        seen.append(history_summary)
        return "설명입니다."

    monkeypatch.setattr(agent, "talk_answer", _fake_talk)
    req = agent.AgentAskRequest(
        query="그럼 옆 골목은?",
        history=[
            agent.AgentHistoryTurn(
                query="신촌 샐러디집에 카페를 차려볼까?",
                address="서대문구 신촌동",
                business_type="cafe",
                business_label="카페",
            )
        ],
    )
    out = asyncio.run(agent._talk_response(req, fallback_detail="위치를 알려 주세요"))
    assert out.mode == "talk" and out.answer == "설명입니다."
    assert seen == ["서대문구 신촌동 · 카페"]


def test_agent_ask_passes_history_summary_into_the_tool_loop(monkeypatch):
    """루프 경로가 history를 요약해 넘긴다 — 500이 났던 자리."""
    from app.services import agent_loop as loop_mod

    seen: list[str] = []

    async def _fake_loop(question, **kwargs):
        seen.append(kwargs["history_summary"])
        # 관찰이 없으면 _loop_response가 mode="talk"로 끝낸다(엔진·DB 불필요).
        return loop_mod.LoopResult(answer="답입니다.", observations=[], rounds=1)

    monkeypatch.setattr(agent.settings, "agent_loop_enabled", True)
    monkeypatch.setattr(agent, "run_agent_loop", _fake_loop)
    req = agent.AgentAskRequest(
        query="그럼 옆 골목은?",
        history=[
            agent.AgentHistoryTurn(
                query="신촌 샐러디집에 카페를 차려볼까?",
                address="서대문구 신촌동",
                business_type="cafe",
                business_label="카페",
            )
        ],
    )
    out = asyncio.run(agent.agent_ask(req))
    assert out.answer == "답입니다." and out.mode == "talk"
    assert seen == ["서대문구 신촌동 · 카페"]


def test_agent_answer_endpoint_excludes_the_tool_it_just_ran_from_suggestions(monkeypatch):
    async def _fake_write(site, *, place="", business_label="", radius_m=None,
                          question="", tool="site_score", tool_result=None, tool_results=None,
                          history=None, brief=False):
        return "서술입니다.", "llm"

    monkeypatch.setattr(agent, "_write_answer", _fake_write)
    req = agent.AgentAnswerRequest.model_construct(
        site=SimpleNamespace(),
        place="서대문구 신촌동",
        business_label="카페",
        radius_m=500,
        query="신촌 카페 월세 300에 계약해도 될까",
        tool="lease_verdict",
        tool_results={"lease_verdict": {}},
    )
    out = asyncio.run(agent.agent_answer(req))
    assert agent._SUGGESTION_TEMPLATES["lease_verdict"] not in out.suggested_questions
    assert 1 <= len(out.suggested_questions) <= 3


def _tree():
    from app.services.sbiz_upjong import _load_fallback_tree

    return _load_fallback_tree()


def test_upjong_table_covers_industries_that_have_no_preset():
    # 프리셋은 13종뿐인데 서울 업종은 1,000여 종이다. 프리셋 밖 업종을 general로
    # 흘리면 엔진이 동일업종을 아예 세지 않아("판단 불가") 상담이 성립하지 않는다.
    tree = _tree()
    for word, expect in [
        ("세탁소", "S209"),   # 세탁업
        ("꽃집", "G219"),     # 화초 및 식물 소매업
        ("정육점", "G205"),   # 식료품 소매업
        ("약국", "G215"),     # 의약품 소매업
        ("독서실", "R102"),   # 도서관·유사 여가
        ("서점", "G213"),     # 문화·오락·여가 용품 소매업
        ("주점", "I211"),     # 주점업
    ]:
        hit = agent._match_upjong(tree, word)
        assert hit is not None, f"{word} 이(가) 업종 표에 안 붙음"
        assert hit["inds_mcls_cd"] == expect, (word, hit)


def test_upjong_matching_ignores_place_names_and_bare_suffixes():
    # 지명이 업종으로 오인되면 엉뚱한 경쟁 집계가 붙는다.
    tree = _tree()
    for place in ("신촌", "성수동", "가로수길", "여의도", "왕십리", "청량리", "목동"):
        assert agent._match_upjong(tree, place) is None, place
    # '무인 아이스크림 가게'의 '가게'가 '반찬가게' 별칭에 걸려 엉뚱한 업종이 잡혔다.
    for suffix in agent._SHOP_SUFFIXES:
        assert agent._match_upjong(tree, suffix) is None, suffix


def test_rule_upjong_strips_shop_suffix_and_prefers_the_longest_word():
    tree = _tree()
    hit, word = agent._rule_upjong_from_query(tree, "면목동 꽃집 어때")
    assert word == "꽃집" and hit["inds_scls_cd"] == "G21901"
    # 붙여 쓴 상호 접미사는 떼고 다시 찾는다 — '곱창집'은 표에 없고 '곱창'은 있다.
    hit2, _ = agent._rule_upjong_from_query(tree, "왕십리 곱창집 ㅇㅋ?")
    assert hit2["inds_scls_cd"] == "I20109"
    # '치킨집'은 한식(I201)이 아니라 기타 간이 음식점업(I210)의 치킨이다.
    hit3, word3 = agent._rule_upjong_from_query(tree, "홍제동 치킨집 될까")
    assert word3 == "치킨집" and hit3["inds_scls_cd"] == "I21006"
    # 업종어가 없는 문장에서 지명이 업종으로 잡히면 안 된다.
    assert agent._rule_upjong_from_query(tree, "여의도 자리 어때") is None
    # 조건을 가리키는 낱말도 업종어가 아니다 — '사도 되나'의 '사도'가 별칭에
    # 걸려 매입 질의가 '고용 알선업' 분석으로 나갔다(라이브 실측).
    assert agent._rule_upjong_from_query(tree, "사당동 상가 6억5천 25평 사도 되나") is None


def test_rule_upjong_finds_alias_when_place_and_business_are_run_together():
    """지명과 업종이 붙어 와도 별칭으로 정확한 소분류를 찾는다.

    "홍대입구샐러드집"처럼 띄어쓰기가 없으면 낱말 단위 매칭이 전부 실패해
    general로 샜고, 그러면 restaurant 프리셋(한식 고정)에 걸려 샐러드집이
    "한식 음식점업"으로 분석됐다(라이브 실측, 2026-07-29).
    """
    tree = _tree()
    hit, alias = agent._rule_upjong_from_query(tree, "홍대입구샐러드집 괜찮아?")
    # 한식(I201)이 아니라 기타 간이 음식점업의 토스트/샌드위치/샐러드여야 한다.
    assert hit["inds_scls_cd"] == "I21005"
    assert hit["inds_mcls_cd"] == "I210"
    assert alias == "샐러드"
    # 띄어 쓴 경우와 같은 결과여야 한다 — 띄어쓰기가 분류를 바꾸면 안 된다.
    spaced, _ = agent._rule_upjong_from_query(tree, "홍대입구 샐러드집 괜찮아?")
    assert spaced["inds_scls_cd"] == hit["inds_scls_cd"]
    # 폴백이 아무 데나 걸리면 안 된다 — 업종어 없는 문장은 여전히 None.
    assert agent._rule_upjong_from_query(tree, "연남동 자리 어때") is None


def test_confident_rule_intent_covers_industries_without_a_preset():
    # 프리셋에 없는 업종도 규칙만으로 확신이 서면 LLM 왕복을 건너뛴다.
    intent = agent._confident_rule_intent("면목동 꽃집 어때", _tree())
    assert intent is not None
    assert intent.address == "면목동"  # 업종어가 지명에 섞여 들어가지 않는다
    assert intent.business_type == "general"
    assert intent.upjong["inds_scls_cd"] == "G21901"


def test_llm_general_never_overrides_a_specific_rule_match(monkeypatch):
    # 실제 라이브 결함: '노원역 스터디카페', '미아사거리 키즈카페'에서 LLM이
    # business_type="general"을 주면 규칙이 잡은 cafe가 버려졌고, 그러면 경쟁
    # 집계가 통째로 꺼져 답변이 "일반 업종 … 판단 불가"만 남았다.
    async def _llm(**_kwargs):
        return {"address": "노원역", "business_type": "general", "business_word": ""}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("노원역 스터디카페 어때", _tree()))
    assert intent.business_type == "cafe"


def test_llm_business_word_resolves_to_a_real_upjong_code(monkeypatch):
    # 프리셋에 없는 업종은 LLM이 표준 업종명만 주면 코드로 붙는다(하드코딩 없음).
    async def _llm(**_kwargs):
        return {
            "address": "서울대입구",
            "business_type": "general",
            "business_word": "세탁소",
        }

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("서울대입구 자취생 상대로 세탁방", _tree()))
    assert intent.business_type == "general"
    assert intent.upjong is not None and intent.upjong["inds_mcls_cd"] == "S209"


def test_empty_llm_address_is_not_replaced_by_sentence_debris(monkeypatch):
    # 실제 라이브 결함: "'안녕하세요 좋은 추천해 주실 수 있나요'에서 분석할 위치를
    # 찾지 못했어요" — LLM이 '지명 없음'이라고 답했는데 문장 찌꺼기를 지명 자리에
    # 밀어 넣은 탓이다.
    async def _llm(**_kwargs):
        return {"address": "", "business_type": "general", "business_word": ""}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(
        agent._extract_intent("안녕하세요 좋은 자리 좀 추천해 주실 수 있나요", _tree())
    )
    assert intent.address == ""


def test_empty_llm_address_still_uses_a_plausible_place_leftover(monkeypatch):
    # 반대로 잔여물이 지명일 법하면(두 낱말 이하) 그대로 써서 지오코더에 맡긴다.
    async def _llm(**_kwargs):
        return {"address": "", "business_type": "cafe", "business_word": "카페"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("신사동 가로수길 카페 자리 어때", _tree()))
    assert intent.address == "신사동 가로수길"


def test_comparison_queries_are_flagged_instead_of_silently_narrowed():
    # 실제 라이브 결함: '홍대랑 연남동 중에 어디가 나아?'가 한 지점 분석으로
    # 뭉개졌고, 답변은 두 곳을 한 곳인 양 서술했다.
    for q in (
        "홍대랑 연남동 중에 어디가 나아?",
        "합정 브런치카페랑 이자카야 둘 중에",
        "성수 vs 연남 카페",
        "강남역이랑 삼성역 비교해줘",
    ):
        assert agent._comparison_notice(q, "마포구 연남동"), q
    # 평범한 질의에는 붙지 않는다 — '중'이 들어간 지명·업종에 오작동하면 안 된다.
    for q in (
        "종로3가 중국집 자리 어때",
        "중랑구 면목동 카페",
        "신촌에 카페 자리 점수 매겨줘",
    ):
        assert agent._comparison_notice(q, "중구") == "", q


def test_facts_carry_the_users_actual_question():
    # 서술이 질문과 무관해지지 않도록 원문을 사실 블록 맨 위에 둔다.
    facts = agent._facts_for_answer(
        _site(), place="광진구 화양동", business_label="주점", question="야간 장사할 건데 어때"
    )
    assert facts.startswith("사용자 질문: 야간 장사할 건데 어때")


def test_location_error_says_seoul_only():
    # '부산 서면 카페'에 "어느 지역인지 알려 주세요"라고만 되물으면 무엇이
    # 잘못됐는지 알 수 없다.
    assert "서울" in agent._SEOUL_ONLY


def test_money_expressions_become_manwon_numbers():
    # 표현이 다양하다 — 하나만 되면 처음 보는 문장에서 바로 무너진다.
    assert agent._money_to_man("10억") == 100_000
    assert agent._money_to_man("3억5천") == 35_000
    assert agent._money_to_man("300만원") == 300
    assert agent._money_to_man("3백만원") == 300
    assert agent._money_to_man("5천") == 5_000
    assert agent._money_to_man("1,200") == 1_200
    assert agent._money_to_man("") is None


def test_parameters_are_read_from_the_sentence_not_dropped():
    # 지금까지 주소·업종만 뽑고 수치를 통째로 버려서, "월세 300만원에 계약해도
    # 될까?"에도 그냥 입지 점수만 나왔다.
    p = agent._extract_parameters("신촌에 월세 300만원 카페 계약해도 될까?")
    assert p["monthly_rent_man"] == 300

    p = agent._extract_parameters("연희동 3층 40평 월세 500 관리비 30 권리금 2천 카페")
    assert p["monthly_rent_man"] == 500
    assert p["maintenance_man"] == 30  # 앞의 500을 끌어오면 안 된다
    assert p["key_money_man"] == 2_000
    assert p["area_pyeong"] == 40 and p["floor"] == 3

    p = agent._extract_parameters("보증금 5천에 월세 300인 성수동 카페")
    assert p["deposit_man"] == 5_000 and p["monthly_rent_man"] == 300

    p = agent._extract_parameters("강남 상가 8억에 사서 월세 350 받으면")
    assert p["price_manwon"] == 80_000 and p["monthly_rent_man"] == 350

    p = agent._extract_parameters("역삼동 상가 12억 30평 매입 어떄")
    assert p["price_manwon"] == 120_000 and p["area_pyeong"] == 30


def test_non_money_numbers_are_not_read_as_amounts():
    # 문장에는 돈이 아닌 숫자가 섞여 있다. 라벨 없는 숫자는 쓰지 않는다.
    assert agent._extract_parameters("천호동 24시간 무인 헬스장 어때") == {}
    assert agent._extract_parameters("20대 여성 타겟 신사동 편집샵") == {}
    # '회사도'의 '사도'가 매입으로 잡히면 안 된다.
    assert "price_manwon" not in agent._extract_parameters("우리 회사도 강남인데 카페 어때")


def test_tool_is_chosen_by_the_values_found_in_the_sentence():
    # 라우팅은 특정 문구가 아니라 **뽑힌 파라미터**가 결정한다.
    def tool(q, places=1):
        return agent._rule_tool(q, agent._extract_parameters(q), places)

    assert tool("신촌에 월세 300만원 카페 계약해도 될까?") == "lease_verdict"
    assert tool("우리 신촌 카페 월매출 800인데 왜 낮아?") == "diagnose"
    assert tool("신촌 상가 10억에 사면 수익 나올까?") == "purchase_verdict"
    assert tool("강남 상가 8억에 사서 월세 350 받으면") == "profitability"
    assert tool("연남동에서 카페 자리 5곳 추천해줘") == "area_search"
    assert tool("연남동이랑 신촌 중에 카페 하기 어디가 나아?", places=2) == "decide"
    assert tool("망원동 카페 자리 어때") == "site_score"

    # 뜨는지 지는지는 값으로 갈리지 않는 의도라 표현으로 받는다.
    assert tool("연남동 요즘 뜨는 동네야?") == "trends"
    assert tool("성수동 카페 시장 추이 어때") == "trends"
    assert tool("이 동네 카페 늘고 있어?") == "trends"
    # 자리를 찾아 달라는 쪽이 섞이면 탐색이 맞다 — 추이를 보여 줘도 답이 아니다.
    assert tool("연남동에서 뜨는 자리 추천해줘") == "area_search"
    # 값이 있는 질문은 그 값이 이긴다. '추이'가 섞여도 계약 판단이 먼저다.
    assert tool("신촌 월세 300만원인데 요즘 추이 보고 계약해도 될까") == "lease_verdict"


def test_places_are_split_instead_of_geocoded_as_one_string():
    # '연남동, 신촌'을 한 주소로 지오코딩하면 둘 다 아닌 점이 잡힌다.
    assert agent._split_places("연남동, 신촌") == ["연남동", "신촌"]
    assert agent._split_places("연남동이랑 신촌") == ["연남동", "신촌"]
    assert agent._split_places("성수 vs 연남") == ["성수", "연남"]
    assert agent._split_places("서대문구 신촌동") == ["서대문구 신촌동"]
    assert agent._split_places("") == []


def test_tool_is_only_used_when_its_inputs_are_all_present():
    # 값이 모자란 채 도구를 부르면 0으로 계산돼 거짓 결론이 된다. 대신 무엇이
    # 더 필요한지 알려 주고 기본 분석으로 내려간다.
    point = agent._Point(37.5, 127.0, "테스트")
    only_price = agent._Intent(address="신촌", params={"price_manwon": 100_000})
    ready, hint = agent._tool_ready("purchase_verdict", only_price, [point])
    assert ready is False and "면적" in hint

    with_area = agent._Intent(
        address="신촌", params={"price_manwon": 100_000, "area_pyeong": 30}
    )
    assert agent._tool_ready("purchase_verdict", with_area, [point])[0] is True

    # 비교는 지점이 둘 이상일 때만.
    two = agent._Intent(address="연남동", places=["연남동", "신촌"])
    assert agent._tool_ready("decide", two, [point])[0] is False
    assert agent._tool_ready("decide", two, [point, point])[0] is True


def test_default_path_keeps_the_five_second_rule_and_tools_get_more():
    # 조건 없이 묻는 기본 경로는 도구를 부르지 않으므로 5초 하드룰 그대로다.
    assert agent._TOOLS["site_score"].budget_s <= 5.0
    # 조건이 붙은 질문은 도구를 못 돌리면 **다른 질문에 답하게 된다**
    # (두 곳 중 어디 → 한 곳 점수). 그래서 예산을 더 준다.
    for key in ("lease_verdict", "purchase_verdict", "profitability", "diagnose"):
        assert agent._TOOLS[key].budget_s > 5.0, key
    # 여러 지점·여러 후보를 실제로 계산하는 쪽은 더 오래 걸린다(라이브 실측).
    assert agent._TOOLS["decide"].budget_s >= 15.0
    assert agent._TOOLS["area_search"].budget_s >= 20.0


def test_tool_facts_are_folded_into_the_answer_material():
    facts = agent._facts_for_answer(
        _site(),
        place="서대문구 신촌동",
        business_label="카페",
        question="월세 300에 계약해도 될까",
        tool="lease_verdict",
        tool_result={
            "headline": "월세가 적정선보다 높습니다.",
            "verdict_label": "재검토",
            "expected_monthly_sales_man": 1800,
            "rent_burden_pct": 22.5,
            "risks": [{"label": "임차비 부담 과다"}],
        },
    )
    assert "실행한 도구: 임차 계약 판정" in facts
    assert "월세가 적정선보다 높습니다." in facts
    assert "22.5%" in facts
    assert "임차비 부담 과다" in facts
    # 만원 단위는 사람이 읽는 금액으로 접는다.
    assert "1,800만원" in facts


def test_error_message_does_not_echo_a_huge_input():
    # '카페'를 60번 붙인 입력에서 그 문자열이 통째로 에러 메시지에 실려
    # 화면과 로그를 덮었다.
    long_place = "카페" * 60
    out = agent._shorten(long_place)
    assert len(out) <= 31 and out.endswith("…")
    assert agent._shorten("신촌") == "신촌"


def test_preset_business_type_still_reaches_tools_that_have_no_preset_field():
    """decide·area_search는 business_type 필드가 없다 — 프리셋도 코드로 풀어야 한다.

    비우고 넘겼더니 "연남동이랑 신촌 중 카페" 비교에서 예상 월매출이 양쪽 다
    None이었다(매출 벤치마크가 업종 없이는 안 붙는다).
    """
    cafe = agent._codes(agent._Intent(address="신촌", business_type="cafe"))
    assert cafe["inds_scls_cd"] == "I21201" and cafe["inds_mcls_cd"] == "I212"

    # 표에서 직접 찾은 코드가 있으면 그쪽이 우선이다(프리셋으로 덮지 않는다).
    direct = agent._codes(
        agent._Intent(address="신촌", business_type="general", upjong={"inds_mcls_cd": "R104"})
    )
    assert direct["inds_mcls_cd"] == "R104"

    # 프리셋도 코드도 없으면 비운 채로 — 없는 업종을 지어내지 않는다.
    assert agent._codes(agent._Intent(address="신촌")) == {
        "inds_lcls_cd": None, "inds_mcls_cd": None, "inds_scls_cd": None,
    }


def test_dong_fallback_sales_is_not_called_a_trdar():
    """상권 밖이라 행정동 평균으로 메운 매출을 '상권 ○○동'이라 부르면 안 된다.

    골목상권 매출과 같은 정밀도로 읽히기 때문이다 — 단위를 그대로 밝힌다.
    """
    from types import SimpleNamespace

    site = SimpleNamespace(
        upjong_breadcrumb="", business_label="카페", total_score=60.0, grade="C",
        verdict_one_liner="", subscores=[], demand=None, competition=None,
        sales_benchmark=SimpleNamespace(
            trdar_nm="상계9동", monthly_sales_won=1_000_000_000,
            per_store_monthly_won=21_370_000, citywide_median_per_store_won=6_230_000,
            weekend_share_pct=None, scope="dong",
        ),
    )
    facts = agent._facts_for_answer(site)
    assert "행정동 상계9동" in facts and "행정동 평균" in facts
    assert "상권 상계9동" not in facts

    # 지정 상권 안이면 지금까지처럼 '상권'이라 부른다.
    site.sales_benchmark.trdar_nm = "신촌역"
    site.sales_benchmark.scope = "trdar"
    facts = agent._facts_for_answer(site)
    assert "상권 신촌역" in facts and "행정동 평균" not in facts


def test_demand_scope_note_flags_radius_estimates_but_not_grid_measurements():
    """반경 인구가 SGIS 격자 실측이 아니라 행정동 밀도로 넓힌 근사치면 밝힌다.

    demand_radius.scale_demand_profile_to_radius가 격자 데이터를 못 찾으면
    data_scope 문구에 '추정'이 들어간다(_radius_data_scope) — 그 값을 실측처럼
    "거주인구 28,503명"이라고만 답하면 반경 밖 사람까지 세었을 수 있다는 걸
    사용자가 알 길이 없다. 격자 실측일 때는 안내가 필요 없으니 안 붙어야 한다.
    """
    estimated = _site(
        demand=SimpleNamespace(
            population=28_503, workers=None,
            data_scope="반경 500m 원형 추정 (SGIS 2024 행정동 밀도×πr², 행정동 총량 상한)",
        )
    )
    facts = agent._facts_for_answer(estimated)
    assert "28,503명" in facts
    assert "추정치" in facts

    measured = _site(
        demand=SimpleNamespace(
            population=28_503, workers=None,
            data_scope="반경 500m SGIS 격자 합산 (인구 100m, 인구·가구 2024, 종사·사업체 2023, 4셀·...)",
        )
    )
    facts_measured = agent._facts_for_answer(measured)
    assert "28,503명" in facts_measured
    assert "추정치" not in facts_measured


def test_rule_answer_marks_demand_estimate_and_dong_fallback_sales():
    """LLM이 꺼져 규칙 폴백으로 떨어져도 추정치·행정동 폴백 안내가 사라지면 안 된다.

    _facts_for_answer(LLM에 넘기는 자료)에만 안내를 붙이고 _rule_answer(LLM
    없을 때 그대로 나가는 문장)에는 빠뜨리면, 구독 한도로 LLM이 죽는 순간
    사용자에게는 안내 없이 숫자만 나간다.
    """
    site = SimpleNamespace(
        verdict_one_liner="", total_score=70, grade="B",
        subscores=[SimpleNamespace(label="접근성", score=80)],
        demand=SimpleNamespace(
            population=28_503, workers=None, seoul_rank=None,
            data_scope="반경 500m 원형 추정 (SGIS 2024 행정동 밀도×πr², 행정동 총량 상한)",
        ),
        sales_benchmark=SimpleNamespace(
            per_store_monthly_won=21_370_000, citywide_median_per_store_won=6_230_000,
            scope="dong",
        ),
        competition=None, transit=None,
    )
    out = agent._rule_answer(site)
    assert "28,503명" in out and "추정치" in out
    assert "행정동 평균" in out

    site.demand.data_scope = "반경 500m SGIS 격자 합산 (인구 100m, ...)"
    site.sales_benchmark.scope = "trdar"
    out2 = agent._rule_answer(site)
    assert "추정치" not in out2
    assert "행정동 평균" not in out2


def test_apartment_market_discloses_gu_fallback_and_sample_count():
    """동 단위 아파트 실거래가 3건 미만이면 구 전체 중앙값으로 메운다.

    (housing_market.fetch_apartment_market) — sales_benchmark의 행정동 폴백과
    같은 이유로, 그 정밀도 차이를 밝히지 않으면 구 전체 평균이 '이 동네 시세'로
    오독된다. 표본 건수도 함께 밝혀 값의 신뢰도를 판단할 수 있게 한다.
    """
    dong_level = _site(
        demand=SimpleNamespace(
            population=None, workers=None,
            apartment_market=SimpleNamespace(
                median_price_per_sqm=1850.0, scope="신촌동 (최근 12개월 매매)",
                sample_count=6,
            ),
        )
    )
    facts = agent._facts_for_answer(dong_level)
    assert "1,850만원/㎡" in facts
    assert "거래 6건" in facts
    assert "구 전체 중앙값" not in facts

    gu_fallback = _site(
        demand=SimpleNamespace(
            population=None, workers=None,
            apartment_market=SimpleNamespace(
                median_price_per_sqm=1500.0, scope="구 단위 (11410, 최근 12개월 매매)",
                sample_count=42,
            ),
        )
    )
    facts_gu = agent._facts_for_answer(gu_fallback)
    assert "1,500만원/㎡" in facts_gu
    assert "구 전체 중앙값" in facts_gu
    assert "거래 42건" in facts_gu


def test_trend_facts_reach_the_narrative():
    """추이 지표는 series 안에, 성장 신호는 목록 안에 들어 있다.

    평면 필드만 훑던 기존 추출로는 하나도 안 잡혀, 도구를 돌리고도 서술이
    "성장 신호"를 한 줄도 못 쓰는 상태가 된다.
    """
    facts = agent._tool_facts_from_dict(
        "trends",
        {
            "category_label": "카페",
            "series": {
                "quarters": ["2024Q1", "2024Q2", "2024Q3", "2024Q4"],
                "category_growth_pct": 12.5,
                "total_growth_pct": -3.0,
            },
            "street_signals": [
                {"name": "연남동 골목", "label": "신호 강함", "category_growth_pct": 18.2},
                {"name": "합정 대로변", "label": "관찰", "store_growth_pct": -4.0},
            ],
            "data_scope": "서울 상권분석서비스",
        },
    )

    assert "업종 점포 증감: +12.5%" in facts
    assert "전체 점포 증감: -3.0%" in facts
    assert "2024Q1~2024Q4 (4분기)" in facts
    assert "연남동 골목 신호 강함(+18.2%)" in facts
    # 업종 증감이 없으면 전체 점포 증감으로 물러난다.
    assert "합정 대로변 관찰(-4.0%)" in facts


def test_trend_facts_survive_missing_pieces():
    """추이가 없는 동네도 있다 — 없는 값으로 문장을 만들지 않는다."""
    facts = agent._tool_facts_from_dict(
        "trends", {"category_label": "카페", "street_signals": [], "series": None}
    )

    assert "실행한 도구: 성장 신호·업종 추이" in facts
    assert "증감" not in facts and "성장 신호:" not in facts


def test_extract_brand_reads_the_word_right_before_a_store_number():
    assert agent._extract_brand("스타벅스 2호점 내면 1호점 매출 뺏겨?") == "스타벅스"
    # 호점 앞에 상호가 없고 요청어만 있으면 브랜드로 보지 않는다.
    assert agent._extract_brand("여기 2호점 내면 매출 뺏겨?") is None
    # 애초에 호점 언급이 없으면 당연히 없다.
    assert agent._extract_brand("이 자리 카페 잠식 위험 있어?") is None
    # 업종어가 호점 앞에 오면 그건 브랜드가 아니라 업종이다.
    assert agent._extract_brand("카페 2호점 내면 매출 뺏겨?") is None


def test_tool_is_chosen_for_cannibalization_questions():
    def tool(q, places=1):
        return agent._rule_tool(q, agent._extract_parameters(q), places)

    assert tool("스타벅스 2호점 내면 1호점 매출 뺏겨?") == "cannibalization"
    assert tool("이 자리 잠식 위험 있어?") == "cannibalization"
    assert tool("여기 카페 내면 옆 매장이랑 매출 나눠먹기 돼?") == "cannibalization"
    # 값이 있는 질문은 그 값이 이긴다 — 계약 판단이 먼저다.
    assert tool("신촌 월세 300만원인데 2호점 내면 잠식되나") == "lease_verdict"


def test_cannibalization_tool_needs_no_extra_values():
    # 잠식 분석은 지점(+업종)만 있으면 계산된다 — 브랜드는 있으면 더 쓰지만
    # 없어도 도구를 막지 않는다.
    point = agent._Point(37.5, 127.0, "테스트")
    intent = agent._Intent(address="신촌")
    assert agent._tool_ready("cannibalization", intent, [point]) == (True, "")


def test_run_tool_passes_brand_and_codes_to_cannibalization(monkeypatch):
    captured = {}

    async def _fake_cannibalization(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_cannibalization", _fake_cannibalization)
    intent = agent._Intent(address="신촌", business_type="cafe", brand="스타벅스")
    point = agent._Point(37.5, 127.0, "신촌")

    tool, _ = asyncio.run(agent._run_tool("cannibalization", intent, [point], 500))

    assert tool == "cannibalization"
    req = captured["request"]
    assert req.lat == 37.5 and req.lng == 127.0 and req.radius_m == 500
    assert req.brand == "스타벅스"
    # cafe 프리셋 코드가 decide·area_search와 같은 방식으로 그대로 실려야 한다.
    assert req.inds_scls_cd == "I21201" and req.inds_mcls_cd == "I212"


def test_cannibalization_facts_state_direction_and_skip_unsearched_self_brand():
    facts = agent._tool_facts_from_dict(
        "cannibalization",
        {
            "risk_score": 62.3,
            "risk_label": "중간",
            "total_count": 8,
            "overlap_pct": 41.0,
            "overlap_with": "이디야 신촌점",
            "self_total_count": 0,
            "self_overlap_pct": None,
        },
    )
    # risk_score는 높을수록 나쁜 지표라 방향을 사실 문장에 그대로 못 박는다.
    assert "잠식 위험도: 62점 (중간, 높을수록 위험)" in facts
    assert "도달권 내 동일업종 경쟁 8곳" in facts
    assert "이디야 신촌점" in facts and "41.0%" in facts
    # 브랜드를 못 찾아 자기잠식을 아예 안 본 경우, 없다고 단정하지 않는다
    # (도구 이름 자체엔 '자기잠식'이 들어가므로, 후보 집계 문구로만 확인한다).
    assert "자기잠식 후보" not in facts
    assert "같은 브랜드 매장" not in facts


def test_cannibalization_facts_report_self_brand_overlap_when_found():
    facts = agent._tool_facts_from_dict(
        "cannibalization",
        {
            "risk_score": 30.0,
            "risk_label": "낮음",
            "total_count": 2,
            "self_total_count": 1,
            "self_overlap_pct": 63.0,
        },
    )
    assert "같은 브랜드 매장 1곳(자기잠식 후보)" in facts
    assert "63.0%" in facts


def test_extract_intent_reads_brand_and_accepts_cannibalization_tool(monkeypatch):
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "cafe", "tool": "cannibalization"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("신촌 스타벅스 2호점 내면 매출 뺏겨?"))
    assert intent.llm_tool == "cannibalization"
    assert intent.brand == "스타벅스"


def test_extract_intent_falls_back_to_brand_extraction_without_llm(monkeypatch):
    async def _no_llm(**_kwargs):
        return None

    monkeypatch.setattr(agent, "complete_json", _no_llm)
    intent = asyncio.run(agent._extract_intent("신촌 스타벅스 2호점 내면 매출 뺏겨?"))
    assert intent.brand == "스타벅스"


def test_tool_is_chosen_for_area_insights_questions():
    def tool(q, places=1):
        return agent._rule_tool(q, agent._extract_parameters(q), places)

    assert tool("여기 아파트 시세랑 유동인구 어때?") == "area_insights"
    assert tool("이 동네 집값 어때?") == "area_insights"
    assert tool("이 근처 생활인구 알려줘") == "area_insights"
    # 값이 있는 질문은 그 값이 이긴다 — 계약 판단이 먼저다.
    assert tool("신촌 월세 300만원인데 시세도 알려줘") == "lease_verdict"
    # "아파트"만으론 시세 얘기가 아닐 수 있다 — 시세류 낱말이 붙어야 한다.
    assert tool("아파트 상가 1층 카페 자리 어때") == "site_score"


def test_area_insights_tool_needs_no_extra_values():
    # 시세·생활인구는 지점만 있으면 계산된다 — 업종·수치 입력이 필요 없다.
    point = agent._Point(37.5, 127.0, "테스트")
    intent = agent._Intent(address="신촌")
    assert agent._tool_ready("area_insights", intent, [point]) == (True, "")


def test_run_tool_calls_area_insights_with_point_and_radius_only(monkeypatch):
    captured = {}

    async def _fake_area_insights(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_area_insights", _fake_area_insights)
    intent = agent._Intent(address="신촌", business_type="cafe")
    point = agent._Point(37.5, 127.0, "신촌")

    tool, _ = asyncio.run(agent._run_tool("area_insights", intent, [point], 500))

    assert tool == "area_insights"
    req = captured["request"]
    assert req.lat == 37.5 and req.lng == 127.0 and req.radius_m == 500
    # 시세·생활인구는 업종과 무관하다 — 요청 모델 자체에 업종 코드 필드가 없다.
    assert not hasattr(req, "inds_scls_cd")


def test_area_insights_facts_state_apartment_price_and_footfall():
    facts = agent._tool_facts_from_dict(
        "area_insights",
        {
            "apartment": {
                "trade_count": 5,
                "median_per_sqm_manwon": 1800.0,
                "median_per_pyeong_manwon": 5950.0,
                "scope": "반경 500m",
                "scope_kind": "radius",
            },
            "footfall": {
                "weekday_hourly": [0.0] * 24,
                "weekend_hourly": [0.0] * 24,
                "weekday_peak_hour": 18,
            },
            "footfall_breakdown": {
                "adm_nm": "신촌동",
                "quarter": "20251",
                "total": 1000,
                "male_pct": 48.0,
                "female_pct": 52.0,
                "ages": [
                    {"label": "20대", "value": 400, "pct": 40.0},
                    {"label": "30대", "value": 200, "pct": 20.0},
                ],
                "days": [],
            },
        },
    )
    assert "실행한 도구: 시세·생활인구 조회" in facts
    assert "아파트 시세(3.3㎡당): 5,950만원 — 반경 내 실거래(거래 5건)" in facts
    assert "평일 생활인구가 가장 많은 시간대: 18시" in facts
    assert "유동인구 성비: 남 48% · 여 52%" in facts
    assert "주 연령대: 20대(40%)" in facts


def test_area_insights_facts_mark_gu_fallback_scope():
    facts = agent._tool_facts_from_dict(
        "area_insights",
        {
            "apartment": {
                "trade_count": 30,
                "median_per_sqm_manwon": 1500.0,
                "median_per_pyeong_manwon": 4950.0,
                "scope": "강남구 전체",
                "scope_kind": "gu",
            },
        },
    )
    assert "인근 실거래 없어 자치구 전체 중앙값" in facts
    assert "반경 내 실거래" not in facts


def test_area_insights_facts_skip_missing_pieces_instead_of_fabricating():
    # 반경 안에 아파트 거래도, 생활인구 데이터도 없으면 각 필드는 None으로
    # 온다 — 이걸 0이나 평균으로 지어내면 안 되고, 그 줄 자체가 없어야 한다.
    facts = agent._tool_facts_from_dict(
        "area_insights",
        {"apartment": None, "footfall": None, "footfall_breakdown": None},
    )
    assert facts == "실행한 도구: 시세·생활인구 조회"


def test_area_insights_facts_skip_breakdown_when_total_is_zero():
    # total=0은 "조회는 됐지만 표본이 없다"는 뜻이라, 성비·연령대를 지어내면
    # 안 된다.
    facts = agent._tool_facts_from_dict(
        "area_insights",
        {
            "footfall_breakdown": {
                "adm_nm": "신촌동", "quarter": "20251", "total": 0,
                "male_pct": None, "female_pct": None, "ages": [], "days": [],
            }
        },
    )
    assert "유동인구 성비" not in facts
    assert "주 연령대" not in facts


def test_extract_intent_accepts_area_insights_tool(monkeypatch):
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "general", "tool": "area_insights"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("신촌 아파트 시세랑 유동인구 어때?"))
    assert intent.llm_tool == "area_insights"


def test_tool_is_chosen_for_territory_questions():
    def tool(q, places=1):
        return agent._rule_tool(q, agent._extract_parameters(q), places)

    assert tool("여기 들어가면 누구 상권을 뺏어?") == "territory"
    assert tool("이 자리 배달권역 분석해줘") == "territory"
    assert tool("배달 반경 겹치는지 봐줘") == "territory"
    # 값이 있는 질문은 그 값이 이긴다 — 계약 판단이 먼저다.
    assert tool("신촌 월세 300만원인데 권역도 봐줘") == "lease_verdict"
    # "잠식"류 문구는 여전히 cannibalization으로 간다 — 권역과 구분된다.
    assert tool("2호점 내면 매출 뺏겨?") == "cannibalization"


def test_territory_tool_needs_no_extra_values():
    # 권역 Before/After는 지점(+업종)만 있으면 계산된다.
    point = agent._Point(37.5, 127.0, "테스트")
    intent = agent._Intent(address="신촌")
    assert agent._tool_ready("territory", intent, [point]) == (True, "")


def test_run_tool_calls_territory_with_point_radius_and_codes(monkeypatch):
    captured = {}

    async def _fake_territory(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_territory", _fake_territory)
    intent = agent._Intent(address="신촌", business_type="cafe")
    point = agent._Point(37.5, 127.0, "신촌")

    tool, _ = asyncio.run(agent._run_tool("territory", intent, [point], 500))

    assert tool == "territory"
    req = captured["request"]
    assert req.lat == 37.5 and req.lng == 127.0 and req.radius_m == 500
    # cafe 프리셋 코드가 decide·cannibalization과 같은 방식으로 그대로 실려야 한다.
    assert req.inds_scls_cd == "I21201" and req.inds_mcls_cd == "I212"


def test_territory_facts_state_headline_zones_and_demand_without_fabricating():
    facts = agent._tool_facts_from_dict(
        "territory",
        {
            "brand_fit_headline": "이 업종은 이 권역에 적합도가 높습니다",
            "brand_fit_bullets": ["배후 인구가 풍부합니다", "경쟁 브랜드 밀도가 낮습니다"],
            "narrative": ["아직 비어 있는 권역이 있어 선점 전략이 유효할 수 있습니다."],
            "zones": [
                {"zone_type": "gap", "area_m2": 30_000.0},
                {"zone_type": "overlap", "area_m2": 12_000.0},
                {"zone_type": "gap", "area_m2": 5_000.0},
            ],
            "demand": {"population": 12345},
        },
    )
    assert "실행한 도구: 권역 Before/After 분석" in facts
    assert "권역 적합도 판정: 이 업종은 이 권역에 적합도가 높습니다" in facts
    assert "배후 인구가 풍부합니다" in facts and "경쟁 브랜드 밀도가 낮습니다" in facts
    assert "선점 전략이 유효할 수 있습니다" in facts
    # 같은 zone_type은 합산되어야 한다(공백 3.5만㎡ = 3.0만 + 0.5만).
    assert "공백 권역 3.5만㎡" in facts
    assert "겹침 권역 1.2만㎡" in facts
    assert "권역 내 배후 인구: 12,345명" in facts


def test_territory_facts_skip_missing_pieces_instead_of_fabricating():
    # 도로망 연결 실패 등으로 headline만 있고 나머지가 비어 있을 수 있다 —
    # 없는 항목을 지어내면 안 되고 그 줄 자체가 없어야 한다.
    facts = agent._tool_facts_from_dict(
        "territory",
        {
            "brand_fit_headline": "",
            "brand_fit_bullets": [],
            "narrative": [],
            "zones": [],
            "demand": {"population": None},
        },
    )
    assert facts == "실행한 도구: 권역 Before/After 분석"


def test_extract_intent_accepts_territory_tool(monkeypatch):
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "cafe", "tool": "territory"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("신촌 카페 배달권역 겹치는지 봐줘"))
    assert intent.llm_tool == "territory"


def _full_site(**over):
    """AgentAskResponse.site를 채우는 최소 유효 SiteScoreResponse.

    territory가 None을 줄 때(학원 등) agent_ask가 실제로 끝까지 도는지
    확인하려면 site_score 응답 스키마를 그대로 만족해야 한다.
    """
    from app.models.schemas import ExplanationOut, NearestSegmentOut, SiteScoreResponse

    base = dict(
        lat=37.5, lng=127.0, radius_m=500,
        business_type="academy", business_label="학원",
        total_score=70.0, grade="B", verdict_one_liner="",
        segment=NearestSegmentOut(
            osm_id=1, highway="residential", distance_m=10.0,
            integration_top_pct=50.0, choice_top_pct=50.0,
            mean_depth_top_pct=50.0, connectivity_top_pct=50.0,
            connectivity_degree=3,
        ),
        subscores=[],
        explanation=ExplanationOut(text="", source="rule"),
    )
    base.update(over)
    return SiteScoreResponse(**base)


def test_agent_ask_explains_territory_inapplicable_instead_of_blaming_timeout(monkeypatch):
    # 학원처럼 배달권역이 없는 업종은 /territory가 그냥 None을 준다 — 이건
    # 시간초과가 아니므로 "계산이 오래 걸려 담지 못했다"는 거짓 안내를 하면
    # 안 되고, 왜 안 나왔는지 정확히 말해야 한다.
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "academy", "tool": "territory"}

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _territory(_request):
        return None

    async def _site_score(_request):
        return _full_site()

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_territory", _territory)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="신촌 학원 배달권역 겹치는지 봐줘", include_answer=False)
        )
    )

    assert resp.tool == "site_score"
    assert resp.tool_result is None
    assert "배달권역이 없는 업종" in resp.notice
    assert "오래 걸려" not in resp.notice


def test_tool_is_chosen_for_real_competition_questions():
    def tool(q, places=1):
        return agent._rule_tool(q, agent._extract_parameters(q), places)

    assert tool("이 자리 카페로 어떤 형태로 하면 맞아?") == "real_competition"
    assert tool("여기 경쟁 구조 어때?") == "real_competition"
    assert tool("이 카페 모델 적합도 봐줘") == "real_competition"
    assert tool("여기 사업 모델 뭐가 맞을까") == "real_competition"
    # 값이 있는 질문은 그 값이 이긴다 — 계약 판단이 먼저다.
    assert tool("신촌 월세 300만원인데 경쟁 구조도 봐줘") == "lease_verdict"
    # "잠식"·"호점"류 문구는 여전히 cannibalization으로 간다 — 경쟁구조와 구분된다.
    assert tool("2호점 내면 매출 뺏겨?") == "cannibalization"
    # "권역"류 문구는 여전히 territory로 간다 — 경쟁구조와 구분된다.
    assert tool("여기 들어가면 누구 상권을 뺏어?") == "territory"


def test_real_competition_tool_needs_no_extra_values():
    # 경쟁 구조·모델 적합도는 지점(+업종)만 있으면 계산된다.
    point = agent._Point(37.5, 127.0, "테스트")
    intent = agent._Intent(address="신촌")
    assert agent._tool_ready("real_competition", intent, [point]) == (True, "")


def test_run_tool_calls_real_competition_with_point_radius_and_codes(monkeypatch):
    captured = {}

    async def _fake_real_competition(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_real_competition", _fake_real_competition)
    intent = agent._Intent(address="신촌", business_type="cafe")
    point = agent._Point(37.5, 127.0, "신촌")

    tool, _ = asyncio.run(agent._run_tool("real_competition", intent, [point], 500))

    assert tool == "real_competition"
    req = captured["request"]
    assert req.lat == 37.5 and req.lng == 127.0 and req.radius_m == 500
    # cafe 프리셋 코드가 decide·cannibalization과 같은 방식으로 그대로 실려야 한다.
    assert req.inds_scls_cd == "I21201" and req.inds_mcls_cd == "I212"


def test_run_tool_forwards_target_model_to_real_competition(monkeypatch):
    # 학원 업종은 real_competition 내부에서 academy_cluster로 위임되는데, 거기서
    # target_model이 없으면 400을 낸다. 문장에서 과목을 뽑아 놓고도 여기서 안
    # 실어 보내면 매번 그 400을 맞는다(그리고 위에서 "시간초과"로 오표시된다).
    captured = {}

    async def _fake_real_competition(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_real_competition", _fake_real_competition)
    intent = agent._Intent(address="신촌", business_type="academy", target_model="math")
    point = agent._Point(37.5, 127.0, "신촌")

    asyncio.run(agent._run_tool("real_competition", intent, [point], 500))

    assert captured["request"].target_model == "math"


def test_real_competition_facts_state_competition_and_model_fit_without_fabricating():
    facts = agent._tool_facts_from_dict(
        "real_competition",
        {
            "competition": {
                "total_weighted_score": 62.5,
                "total_intensity": "높음",
                "summary_one_liner": "직접 경쟁이 밀집한 상권입니다.",
                "buckets": [
                    {"key": "direct", "count": 5},
                    {"key": "substitute", "count": 3},
                ],
            },
            "model_fit": {
                "headline": "저가형보다 스페셜티 모델이 유리합니다",
                "models": [
                    {"model_id": "low_price", "label": "저가형", "score": 40.0, "verdict": "부적합"},
                    {"model_id": "specialty", "label": "스페셜티", "score": 78.0, "verdict": "적합"},
                ],
            },
            "synergy": {"headline": "베이커리·디저트류와 시너지가 있습니다"},
        },
    )
    assert "실행한 도구: 경쟁 구조·모델 적합도 분석" in facts
    assert "경쟁 강도: 62.5점 (높음, 높을수록 경쟁 치열)" in facts
    assert "경쟁 요약: 직접 경쟁이 밀집한 상권입니다." in facts
    assert "경쟁 유형 2개 구간 · 경쟁점 총 8곳" in facts
    assert "모델 적합도 판정: 저가형보다 스페셜티 모델이 유리합니다" in facts
    # 점수가 가장 높은 모델(스페셜티, 78점)이 뽑혀야 한다 — 목록 순서가 아니라 점수 기준.
    assert "가장 적합한 모델: 스페셜티 (적합, 78점)" in facts
    assert "주변 업종 시너지: 베이커리·디저트류와 시너지가 있습니다" in facts


def test_real_competition_facts_skip_missing_pieces_instead_of_fabricating():
    # 업종이 모델 적합도를 지원하지 않으면(미용실 등) model_fit·synergy가
    # 통째로 None이다 — 없는 항목을 지어내면 안 되고 그 줄 자체가 없어야 한다.
    facts = agent._tool_facts_from_dict(
        "real_competition",
        {
            "competition": {
                "total_weighted_score": 10.0,
                "total_intensity": "낮음",
                "summary_one_liner": "",
                "buckets": [],
            },
            "model_fit": None,
            "synergy": None,
            "academy_cluster": None,
        },
    )
    assert "경쟁 강도: 10.0점 (낮음, 높을수록 경쟁 치열)" in facts
    assert "모델 적합도 판정" not in facts
    assert "가장 적합한 모델" not in facts
    assert "주변 업종 시너지" not in facts
    assert "학원 클러스터 판정" not in facts


def test_extract_intent_accepts_real_competition_tool(monkeypatch):
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "cafe", "tool": "real_competition"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("신촌 카페 경쟁 구조랑 모델 적합도 봐줘"))
    assert intent.llm_tool == "real_competition"


def test_agent_ask_falls_back_to_site_score_when_real_competition_rejects_business_type(monkeypatch):
    # real_competition은 경쟁규칙 프로필이 없는 업종(운동시설·숙박 등)에 400을
    # 준다 — cannibalization·territory·area_insights와 달리 진짜로 거부할 수
    # 있는 도구다. 이때도 500이 아니라 site_score로 안전하게 내려가야 한다.
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "fitness", "tool": "real_competition"}

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _real_competition(_request):
        raise HTTPException(status_code=400, detail="이 업종은 경쟁 분석을 지원하지 않습니다.")

    async def _site_score(_request):
        return _full_site(business_type="fitness", business_label="헬스장")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_real_competition", _real_competition)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="신촌 헬스장 경쟁 구조 어때?", include_answer=False)
        )
    )

    assert resp.tool == "site_score"
    assert resp.tool_result is None
    assert resp.notice != ""


def test_tool_is_chosen_for_property_market_questions():
    def tool(q, places=1):
        return agent._rule_tool(q, agent._extract_parameters(q), places)

    assert tool("이 가격 비싼 거야?") == "property_market"
    assert tool("이 근처 상가 실거래가 알려줘") == "property_market"
    assert tool("오피스텔 시세 비교해줘") == "property_market"
    assert tool("적정가가 얼마인지 봐줘") == "property_market"
    # 값이 있는 질문은 그 값이 이긴다 — 매입 판단이 먼저다("매입" 키워드로 매입가가 잡힘).
    assert tool("신촌에 8억에 매입하려는데 비싼가요?") == "purchase_verdict"
    # "아파트" 시세는 여전히 area_insights로 간다 — 이 물건 자체의 값이 아니다.
    assert tool("여기 아파트 시세랑 유동인구 어때?") == "area_insights"


def test_property_market_tool_needs_no_extra_values():
    # 실거래 비교는 지점만 있으면 계산된다 — 업종·수치 입력이 필요 없다.
    point = agent._Point(37.5, 127.0, "테스트")
    intent = agent._Intent(address="신촌")
    assert agent._tool_ready("property_market", intent, [point]) == (True, "")


def test_extract_property_type_defaults_to_commercial():
    assert agent._extract_property_type("신촌 상가 실거래 비교해줘") == "commercial"
    assert agent._extract_property_type("신촌 오피스텔 실거래 비교해줘") == "officetel"


def test_run_tool_calls_property_market_with_point_and_property_type(monkeypatch):
    captured = {}

    async def _fake_property_market(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_property_market", _fake_property_market)
    intent = agent._Intent(address="신촌", business_type="cafe", property_type="officetel")
    point = agent._Point(37.5, 127.0, "신촌")

    tool, _ = asyncio.run(agent._run_tool("property_market", intent, [point], 500))

    assert tool == "property_market"
    req = captured["request"]
    assert req.lat == 37.5 and req.lng == 127.0
    assert req.property_type == "officetel"
    # 업종·반경은 이 요청 모델에 아예 필드가 없다 — 매매 실거래는 업종과 무관하다.
    assert not hasattr(req, "inds_scls_cd")
    assert not hasattr(req, "radius_m")
    assert req.purchase_price_manwon is None
    assert req.area_sqm is None
    assert req.floor is None


def test_run_tool_calls_property_market_with_price_area_and_floor_when_extracted(monkeypatch):
    captured = {}

    async def _fake_property_market(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_property_market", _fake_property_market)
    intent = agent._Intent(
        address="신촌",
        params={"price_manwon": 80000.0, "area_pyeong": 20.0, "floor": 2.0},
    )
    point = agent._Point(37.5, 127.0, "신촌")

    asyncio.run(agent._run_tool("property_market", intent, [point], 500))

    req = captured["request"]
    assert req.purchase_price_manwon == 80000
    assert req.area_sqm is not None and round(req.area_sqm, 1) == round(20.0 * agent._PYEONG_TO_SQM, 1)
    assert req.floor == "2"


def test_property_market_facts_state_band_and_subject_percentile_without_fabricating():
    facts = agent._tool_facts_from_dict(
        "property_market",
        {
            "property_type": "commercial",
            "n": 12,
            "months_back": 12,
            "p25": 12_000_000.0,
            "p50": 15_000_000.0,
            "p75": 18_000_000.0,
            "insufficient_sample": False,
            "subject_price_per_sqm": 13_500_000.0,
            "subject_percentile": 30.0,
        },
    )
    assert "실행한 도구: 실거래 비교" in facts
    assert "주변 상가 매매 실거래 12건(최근 12개월) — ㎡당 중앙값 1,500만원" in facts
    assert "실거래 가격대(㎡당): 하위 25% 1,200만원 ~ 상위 25% 1,800만원" in facts
    assert "제시 가격 ㎡당 1,350만원 — 주변 실거래 중 하위 30%(낮을수록 저렴한 축)" in facts


def test_property_market_facts_mark_insufficient_sample():
    facts = agent._tool_facts_from_dict(
        "property_market",
        {
            "property_type": "officetel",
            "n": 3,
            "months_back": 12,
            "p25": 10_000_000.0,
            "p50": 11_000_000.0,
            "p75": 12_000_000.0,
            "insufficient_sample": True,
        },
    )
    assert "표본이 적어 참고용" in facts


def test_property_market_facts_skip_missing_pieces_instead_of_fabricating():
    # 반경 안에 비교할 실거래가 아예 없으면 n=0이고 p50 등은 None으로 온다 —
    # 없는 값을 지어내면 안 되고, 표본이 없다는 사실만 밝힌다.
    facts = agent._tool_facts_from_dict(
        "property_market",
        {"property_type": "commercial", "n": 0, "p25": None, "p50": None, "p75": None},
    )
    assert facts == "실행한 도구: 실거래 비교\n주변 실거래 비교 표본 없음 — 이 정보로는 가격 수준을 판단할 수 없음"


def test_extract_intent_accepts_property_market_tool(monkeypatch):
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "general", "tool": "property_market"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("신촌 상가 실거래 비교해줘"))
    assert intent.llm_tool == "property_market"


# ─────────────────────────── academy_cluster ───────────────────────────


def test_extract_target_model_reads_subject_keywords():
    assert agent._extract_target_model("여기 수학학원 자리 경쟁 어때?") == "math"
    assert agent._extract_target_model("영어학원 좋을까?") == "english"
    assert agent._extract_target_model("국어 논술 학원 자리") == "korean"
    assert agent._extract_target_model("과학학원 클러스터 봐줘") == "science"
    assert agent._extract_target_model("입시 보습 학원 자리") == "exam_cram"
    assert agent._extract_target_model("예체능 학원 자리 어때") == "arts"
    assert agent._extract_target_model("코딩학원 어때") == "coding"
    assert agent._extract_target_model("성인 자격증 학원") == "adult"
    # 과목어가 없으면 지어내지 않는다.
    assert agent._extract_target_model("학원 자리 어때?") is None


def test_tool_is_chosen_for_academy_cluster_questions():
    def tool(q, business_type="academy"):
        target_model = agent._extract_target_model(q)
        return agent._rule_tool(
            q, agent._extract_parameters(q), 1,
            business_type=business_type, target_model=target_model,
        )

    assert tool("여기 수학학원 자리 경쟁 어때?") == "academy_cluster"
    assert tool("영어학원인데 주변 학원들이랑 시너지 어때?") == "academy_cluster"
    # 과목이 안 잡히면 못 간다 — 어떤 과목 기준인지 알 수 없어 서비스가 거부한다.
    assert tool("학원 자리 어때?") == "site_score"
    # 학원 업종이 아니면 과목어가 있어도 academy_cluster로 가지 않는다.
    assert tool("수학 과외 자리 어때?", business_type="general") == "site_score"
    # 값이 있는 질문은 그 값이 이긴다 — 계약 판단이 먼저다.
    assert tool("수학학원인데 월세 300만원에 계약해도 될까") == "lease_verdict"


def test_academy_cluster_tool_ready_only_with_target_model():
    point = agent._Point(37.5, 127.0, "테스트")
    not_ready = agent._Intent(address="신촌", business_type="academy")
    ready, hint = agent._tool_ready("academy_cluster", not_ready, [point])
    assert ready is False
    assert "과목" in hint

    ready_intent = agent._Intent(
        address="신촌", business_type="academy", target_model="math"
    )
    assert agent._tool_ready("academy_cluster", ready_intent, [point]) == (True, "")


def test_run_tool_calls_academy_cluster_with_point_radius_codes_and_target_model(monkeypatch):
    captured = {}

    async def _fake_academy_cluster(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_academy_cluster_analysis", _fake_academy_cluster)
    intent = agent._Intent(address="신촌", business_type="academy", target_model="math")
    point = agent._Point(37.5, 127.0, "신촌")

    tool, _ = asyncio.run(agent._run_tool("academy_cluster", intent, [point], 500))

    assert tool == "academy_cluster"
    req = captured["request"]
    assert req.lat == 37.5 and req.lng == 127.0 and req.radius_m == 500
    assert req.target_model == "math"
    # academy 프리셋 코드가 real_competition과 같은 방식으로 그대로 실려야 한다.
    assert req.inds_scls_cd == "P10501" and req.inds_mcls_cd == "P105"


def test_academy_cluster_facts_state_scores_and_safety_without_fabricating():
    facts = agent._tool_facts_from_dict(
        "academy_cluster",
        {
            "headline": "수학학원 기준 클러스터 분석",
            "scores": {
                "direct_competition_pressure": 42.0,
                "cluster_synergy": 15.5,
                "parent_safety": 80.0,
            },
            "parent_safety_detail": {
                "level": "양호", "same_building_risks": [], "nearby_risks": [],
            },
            "model_fit": {"headline": "그룹형보다 개인지도형이 유리합니다"},
        },
    )
    assert "실행한 도구: 학원 과목 클러스터 분석" in facts
    assert "도구 판정: 수학학원 기준 클러스터 분석" in facts
    assert "직접 경쟁 압력: 42.0점(높을수록 경쟁 치열)" in facts
    assert "클러스터 시너지: 15.5점" in facts
    assert "학부모 안전 점수: 80.0점" in facts
    assert "학부모 안전 등급: 양호" in facts
    assert "모델 적합도 판정: 그룹형보다 개인지도형이 유리합니다" in facts


def test_academy_cluster_facts_skip_missing_pieces_instead_of_fabricating():
    # 모델 적합도는 site_score가 실패하면 None으로 온다 — 안전 등급도 빈 문자열일
    # 수 있다. 둘 다 없는 값을 지어내지 않고 그 줄 자체가 없어야 한다.
    facts = agent._tool_facts_from_dict(
        "academy_cluster",
        {
            "headline": "영어학원 기준 클러스터 분석",
            "scores": {
                "direct_competition_pressure": 10.0,
                "cluster_synergy": 5.0,
                "parent_safety": 90.0,
            },
            "parent_safety_detail": {
                "level": "", "same_building_risks": [], "nearby_risks": [],
            },
            "model_fit": None,
        },
    )
    assert "학부모 안전 등급" not in facts
    assert "모델 적합도 판정" not in facts


def test_extract_intent_accepts_academy_cluster_tool(monkeypatch):
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "academy", "tool": "academy_cluster"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("신촌 수학학원 경쟁 어때?"))
    assert intent.llm_tool == "academy_cluster"
    assert intent.target_model == "math"


# ─────────────────────────── lease_feasibility ───────────────────────────


def test_tool_is_chosen_for_lease_feasibility_questions():
    def tool(q):
        return agent._rule_tool(q, agent._extract_parameters(q), 1)

    assert tool("월세 300만원인데 임차 타당성 좀 자세히 봐줘") == "lease_feasibility"
    assert tool("월세 350에 손익분기까지 따져줘") == "lease_feasibility"
    # "타당성"·"손익분기" 언급이 없으면 여전히 간단 판정(lease_verdict)으로 간다.
    assert tool("월세 300만원에 계약해도 될까") == "lease_verdict"


def test_lease_feasibility_tool_needs_monthly_rent():
    point = agent._Point(37.5, 127.0, "테스트")
    not_ready = agent._Intent(address="신촌")
    ready, hint = agent._tool_ready("lease_feasibility", not_ready, [point])
    assert ready is False
    assert "월세" in hint

    ready_intent = agent._Intent(address="신촌", params={"monthly_rent_man": 300.0})
    assert agent._tool_ready("lease_feasibility", ready_intent, [point]) == (True, "")


def test_run_tool_calls_lease_feasibility_with_point_radius_codes_and_rent(monkeypatch):
    captured = {}

    async def _fake_lease_feasibility(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_lease_feasibility", _fake_lease_feasibility)
    intent = agent._Intent(
        address="신촌",
        business_type="cafe",
        params={
            "monthly_rent_man": 300.0,
            "deposit_man": 3000.0,
            "area_pyeong": 15.0,
        },
    )
    point = agent._Point(37.5, 127.0, "신촌")

    tool, _ = asyncio.run(agent._run_tool("lease_feasibility", intent, [point], 500))

    assert tool == "lease_feasibility"
    req = captured["request"]
    assert req.lat == 37.5 and req.lng == 127.0 and req.radius_m == 500
    assert req.monthly_rent_man == 300
    assert req.deposit_man == 3000
    assert req.area_pyeong == 15.0
    # cafe 프리셋 코드가 real_competition·decide와 같은 방식으로 그대로 실려야 한다.
    assert req.inds_scls_cd == "I21201" and req.inds_mcls_cd == "I212"


def test_lease_feasibility_facts_state_verdict_and_benchmark_without_fabricating():
    facts = agent._tool_facts_from_dict(
        "lease_feasibility",
        {
            "verdict": "적정",
            "break_even_monthly_man": 850.0,
            "rent_burden_pct": 12.5,
            "appropriate_rent_low_man": 200.0,
            "appropriate_rent_high_man": 320.0,
            "appropriate_rent_band": "200~320만 원",
            "benchmark_monthly_revenue_man": 4200.0,
            "benchmark_scope": "동일 상권·업종",
        },
    )
    assert "실행한 도구: 임차 타당성 상세 분석" in facts
    assert "임차 타당성 판정: 적정" in facts
    assert "손익분기 월매출: 850만원" in facts
    assert "매출 대비 임차비 비중: 12.5%" in facts
    assert "적정 월세 하한: 200만원" in facts
    assert "적정 월세 상한: 320만원" in facts
    assert "업종 평균 월매출 벤치마크: 4,200만원(동일 상권·업종 기준)" in facts
    assert "적정 임차비 밴드: 200~320만 원" in facts


def test_lease_feasibility_facts_skip_missing_benchmark_instead_of_fabricating():
    # 벤치마크 표본이 부족하면 benchmark_monthly_revenue_man이 None으로 온다 —
    # 값을 지어내지 않고 그 줄 자체가 없어야 한다.
    facts = agent._tool_facts_from_dict(
        "lease_feasibility",
        {
            "verdict": "부적정",
            "break_even_monthly_man": 900.0,
            "benchmark_monthly_revenue_man": None,
            "appropriate_rent_band": "",
        },
    )
    assert "임차 타당성 판정: 부적정" in facts
    assert "업종 평균 월매출 벤치마크" not in facts
    assert "적정 임차비 밴드" not in facts


def test_extract_intent_accepts_lease_feasibility_tool(monkeypatch):
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "cafe", "tool": "lease_feasibility"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(
        agent._extract_intent("신촌 카페 월세 300만원인데 타당성 자세히 봐줘")
    )
    assert intent.llm_tool == "lease_feasibility"


# ─────────────────────────── industry_profile ───────────────────────────


def test_tool_is_chosen_for_industry_profile_questions():
    def tool(q, business_type="cafe"):
        return agent._rule_tool(
            q, agent._extract_parameters(q), 1, business_type=business_type,
        )

    assert tool("카페 창업할 때 뭘 챙겨야 해") == "industry_profile"
    assert tool("여기 미용실 체크리스트 보여줘", business_type="beauty") == "industry_profile"
    assert tool("병원 개원 전에 뭘 준비해야 해", business_type="clinic") == "industry_profile"
    # 값이 뽑히는 질문은 그 값이 이긴다 — 계약 판단이 체크리스트보다 먼저다.
    assert tool("신촌 카페인데 월세 300만원에 계약해도 될까") == "lease_verdict"
    # 체크리스트 표현이 없으면 여전히 기본 분석(site_score)이다.
    assert tool("신촌 카페 자리 어때") == "site_score"


def test_resolve_industry_profile_id_covers_every_business_type_preset():
    # _BUSINESS_TYPES의 프리셋 12종은 모두 같은 이름의 프로필로 바로 해석돼야
    # 한다 — site_score의 ProfileResolver와 같은 경로를 쓰기 때문이다.
    for business_type in (
        "cafe", "convenience", "restaurant", "clinic", "academy", "beauty",
        "fitness", "leisure", "lodging", "gas_station", "ev_charging",
        "professional_office",
    ):
        intent = agent._Intent(address="신촌", business_type=business_type)
        assert agent._resolve_industry_profile_id(intent) == business_type


def test_resolve_industry_profile_id_covers_presetless_profiles_via_upjong_codes():
    # pharmacy·retail_specialty·life_service는 agent의 business_type 프리셋
    # 표에 없다 — "약국"처럼 업종 표(SBIZ)에서 직접 찾은 코드(upjong)로만
    # 프로필에 닿는다. 이 경로가 막혀 있으면 15종 중 3종은 챗봇으로 영원히
    # 체크리스트를 못 받는다.
    pharmacy = agent._Intent(
        address="신촌", business_type="general",
        upjong={"inds_lcls_cd": "G2", "inds_mcls_cd": "G215", "inds_scls_cd": "G21501"},
    )
    assert agent._resolve_industry_profile_id(pharmacy) == "pharmacy"

    retail = agent._Intent(
        address="신촌", business_type="general",
        upjong={"inds_lcls_cd": "G2", "inds_mcls_cd": "G209", "inds_scls_cd": None},
    )
    assert agent._resolve_industry_profile_id(retail) == "retail_specialty"

    life = agent._Intent(
        address="신촌", business_type="general",
        upjong={"inds_lcls_cd": "S2", "inds_mcls_cd": "S206", "inds_scls_cd": None},
    )
    assert agent._resolve_industry_profile_id(life) == "life_service"


def test_resolve_industry_profile_id_is_none_for_unresolvable_general_business():
    # 업종을 특정할 수 없으면 체크리스트를 지어낼 수 없다 — None이어야 도구가
    # 안전하게 site_score로 폴백한다.
    intent = agent._Intent(address="신촌", business_type="general")
    assert agent._resolve_industry_profile_id(intent) is None


def test_industry_profile_tool_ready_only_when_profile_resolves():
    point = agent._Point(37.5, 127.0, "테스트")
    not_ready = agent._Intent(address="신촌", business_type="general")
    ready, hint = agent._tool_ready("industry_profile", not_ready, [point])
    assert ready is False
    assert "업종" in hint

    ready_intent = agent._Intent(address="신촌", business_type="cafe")
    assert agent._tool_ready("industry_profile", ready_intent, [point]) == (True, "")


def test_run_tool_returns_real_cafe_checklist_from_the_committed_profile_json():
    # get_adaptive_profile은 DB 없이 backend/industry-profiles/food/cafe.json을
    # 그대로 읽는다 — 실제 파일 내용과 어긋나면 이 테스트가 잡아낸다(문구가 아니라
    # 실제로 반환된 key·label 값을 검사한다).
    intent = agent._Intent(address="신촌", business_type="cafe")
    point = agent._Point(37.5, 127.0, "신촌")

    tool, result = asyncio.run(
        agent._run_tool("industry_profile", intent, [point], 500)
    )

    assert tool == "industry_profile"
    assert result.industry_id == "cafe"
    assert result.user_type == "entrepreneur"
    kpi_keys = {k.key for k in result.summary_kpis}
    assert kpi_keys == {
        "takeout_demand", "stay_demand", "cafe_saturation", "visibility",
    }
    labels = {k.key: k.label for k in result.summary_kpis}
    assert labels["takeout_demand"] == "테이크아웃 수요"


def test_run_tool_uses_franchise_user_type_when_franchise_brand_is_set():
    # 프랜차이즈 브랜드가 뚜렷이 잡혔으면 franchise용 체크리스트(잠식·신규
    # 커버리지 등)를 준다 — 개인 창업자용과 KPI 목록이 다르다.
    intent = agent._Intent(
        address="신촌", business_type="cafe", franchise_brand="이디야",
    )
    point = agent._Point(37.5, 127.0, "신촌")

    tool, result = asyncio.run(
        agent._run_tool("industry_profile", intent, [point], 500)
    )

    assert tool == "industry_profile"
    assert result.user_type == "franchise"
    kpi_keys = {k.key for k in result.summary_kpis}
    assert "cannibalization_risk" in kpi_keys


def test_run_tool_returns_none_for_industry_profile_when_business_is_general():
    # 값을 지어내지 않는다 — 업종을 못 정했으면 체크리스트도 없다.
    intent = agent._Intent(address="신촌", business_type="general")
    point = agent._Point(37.5, 127.0, "신촌")

    tool, result = asyncio.run(
        agent._run_tool("industry_profile", intent, [point], 500)
    )

    assert tool == "industry_profile"
    assert result is None


def test_industry_profile_facts_state_kpi_labels_without_fabricating():
    facts = agent._tool_facts_from_dict(
        "industry_profile",
        {
            "industry_id": "cafe",
            "industry_label": "카페·제과",
            "user_type": "entrepreneur",
            "summary_kpis": [
                {"key": "takeout_demand", "label": "테이크아웃 수요"},
                {"key": "cafe_saturation", "label": "카페 공급 포화도"},
            ],
        },
    )
    assert "실행한 도구: 업종별 체크리스트" in facts
    assert "'카페·제과' 업종 체크리스트 지표: 테이크아웃 수요, 카페 공급 포화도" in facts


def test_extract_intent_accepts_industry_profile_tool(monkeypatch):
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "cafe", "tool": "industry_profile"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("신촌 카페 창업할 때 뭘 챙겨야 해"))
    assert intent.llm_tool == "industry_profile"


# ─────────────────────── personal_fit(우선순위 재가중) ───────────────────────


def test_extract_priorities_reads_the_emphasized_factor():
    assert agent._extract_priorities("신촌 카페인데 유동인구를 더 중요하게 봐줘") == [
        "high_footfall"
    ]
    assert agent._extract_priorities("경쟁이 적은 곳을 가장 중요하게 봐줘") == [
        "low_competition"
    ]
    assert agent._extract_priorities("여기 20대 인구 위주로 봐줘") == ["youth"]


def test_extract_priorities_ignores_plain_mentions_without_emphasis():
    # 그냥 사실을 물었을 뿐이면 재가중으로 읽지 않는다 — 매번 personal_fit이
    # 켜지면 요청하지 않은 재가중이 화면에 나타난다.
    assert agent._extract_priorities("신촌 유동인구 얼마나 돼?") == []
    assert agent._extract_priorities("신촌 카페 자리 점수 매겨줘") == []
    assert agent._extract_priorities("여기 경쟁 심한가요") == []


def test_extract_priorities_keeps_sentence_order_and_caps_at_max():
    # "20대"가 "유동인구"보다 먼저 나오면 그 순서를 따른다.
    assert agent._extract_priorities("20대랑 유동인구 위주로 봐줘") == [
        "youth",
        "high_footfall",
    ]
    result = agent._extract_priorities(
        "유동인구, 20대, 여성, 경쟁 적은 곳 전부 중요하게 봐줘"
    )
    assert len(result) == agent.MAX_PRIORITIES


def test_extract_priorities_maps_only_known_priority_keys():
    from app.services.personal_fit import PRIORITIES

    for word, key in agent._PRIORITY_KEYWORDS:
        assert key in PRIORITIES, f"{word} → {key} 는 personal_fit.PRIORITIES에 없음"


def test_extract_intent_reads_priorities_without_llm(monkeypatch):
    async def _no_llm(**_kwargs):
        return None

    monkeypatch.setattr(agent, "complete_json", _no_llm)
    intent = asyncio.run(
        agent._extract_intent("신촌 카페인데 유동인구를 더 중요하게 봐줘")
    )
    assert intent.priorities == ["high_footfall"]


def test_agent_ask_forwards_priorities_to_site_score(monkeypatch):
    # 사용자가 "유동인구를 더 중요하게 봐줘"라고 하면 그 조건이 실제로
    # site_score의 personal_fit 재가중에 실려야 한다(무시되면 안 된다).
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "cafe"}

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    captured = {}

    async def _site_score(request):
        captured["request"] = request
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="신촌 카페인데 유동인구를 더 중요하게 봐줘", include_answer=False
            )
        )
    )

    assert captured["request"].priorities == ["high_footfall"]
    assert resp.priorities == ["high_footfall"]


def test_agent_ask_leaves_priorities_empty_without_emphasis(monkeypatch):
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "cafe"}

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    captured = {}

    async def _site_score(request):
        captured["request"] = request
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="신촌 카페 자리 점수 매겨줘", include_answer=False)
        )
    )

    assert captured["request"].priorities == []


def test_tool_is_chosen_for_isochrone_questions():
    def tool(q, places=1):
        return agent._rule_tool(q, agent._extract_parameters(q), places)

    assert tool("여기 도보 10분 안에 몇 명 살아?") == "isochrone"
    assert tool("걸어서 5분 이내에 거주 인구가 얼마나 돼") == "isochrone"
    assert tool("차로 15분 안에 사는 사람이 몇 명이야") == "isochrone"
    # 값이 있는 질문은 그 값이 이긴다 — 계약 판단이 먼저다.
    assert tool("신촌 월세 300만원인데 도보 10분 안에 몇 명 살아?") == "lease_verdict"
    # 시간만 있고 인구·거주 얘기가 없으면 위치 설명일 뿐, 도달권역 계산을
    # 요구한 게 아니다.
    assert tool("도보 10분 거리에 있는 카페 자리 어때") == "site_score"
    # "생활인구"만으론 시간 기준이 없어 area_insights로 간다(구분되어야 한다).
    assert tool("이 근처 생활인구 알려줘") == "area_insights"


def test_isochrone_tool_needs_no_extra_values():
    # 도달권역은 지점만 있으면 계산된다 — 시간·이동수단은 문장에 없으면 기본값을 쓴다.
    point = agent._Point(37.5, 127.0, "테스트")
    intent = agent._Intent(address="신촌")
    assert agent._tool_ready("isochrone", intent, [point]) == (True, "")


def test_extract_reach_minutes_and_mode_from_the_sentence():
    assert agent._extract_reach_minutes("도보 10분 안에 몇 명 살아?") == 10
    assert agent._extract_reach_mode("도보 10분 안에 몇 명 살아?") == "walk"
    assert agent._extract_reach_minutes("차로 15분 이내 인구") == 15
    assert agent._extract_reach_mode("차로 15분 이내 인구") == "drive"
    assert agent._extract_reach_mode("자전거로 10분 거리 인구") == "bike"
    assert agent._extract_reach_mode("오토바이로 배달 거리 안 인구") == "moto"
    # 시간이 문장에 없으면 지어내지 않는다 — 도구 쪽 기본 밴드에 맡긴다.
    assert agent._extract_reach_minutes("여기 카페 어때") is None
    # 31분처럼 스키마가 안 받는 값은 걸러진다(스키마 상한은 30분).
    assert agent._extract_reach_minutes("도보 45분 안에 몇 명 살아") is None


def test_run_tool_calls_isochrone_with_point_radius_mode_and_minutes(monkeypatch):
    captured = {}

    async def _fake_isochrone(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_isochrone", _fake_isochrone)
    intent = agent._Intent(
        address="신촌", business_type="cafe", reach_minutes=10, reach_mode="walk"
    )
    point = agent._Point(37.5, 127.0, "신촌")

    tool, _ = asyncio.run(agent._run_tool("isochrone", intent, [point], 500))

    assert tool == "isochrone"
    req = captured["request"]
    assert req.lat == 37.5 and req.lng == 127.0 and req.radius_m == 500
    assert req.mode == "walk"
    assert req.minutes == [10]
    # cafe 프리셋 코드가 다른 도구들과 같은 방식으로 그대로 실려야 한다.
    assert req.inds_scls_cd == "I21201" and req.inds_mcls_cd == "I212"


def test_run_tool_uses_default_minute_bands_when_sentence_has_no_time(monkeypatch):
    captured = {}

    async def _fake_isochrone(request):
        captured["request"] = request
        return SimpleNamespace()

    monkeypatch.setattr(agent, "run_isochrone", _fake_isochrone)
    intent = agent._Intent(address="신촌")
    point = agent._Point(37.5, 127.0, "신촌")

    asyncio.run(agent._run_tool("isochrone", intent, [point], 500))

    req = captured["request"]
    assert req.mode == "walk"
    assert req.minutes == [5, 10, 15]


def test_isochrone_facts_report_population_and_skip_missing_pieces():
    facts = agent._tool_facts_from_dict(
        "isochrone",
        {
            "mode": "walk",
            "start_connected": True,
            "bands": [
                {
                    "minutes": 5,
                    "population_estimate": 3200,
                    "store_count": 40,
                    "same_category_count": 3,
                },
                # 수요 데이터가 없어 population_estimate가 None인 밴드는 그
                # 값을 지어내지 않고 있는 값(업소 수)만 보고한다.
                {
                    "minutes": 10,
                    "population_estimate": None,
                    "store_count": 90,
                    "same_category_count": 0,
                },
            ],
            "warnings": [],
        },
    )
    assert "실행한 도구: 도달권역(등시선) 분석" in facts
    assert "도보 5분 도달권: 거주인구 약 3,200명, 업소 40곳, 동일업종 3곳" in facts
    assert "도보 10분 도달권: 업소 90곳" in facts
    # 동일업종 0곳은 "집계했더니 0"인지 "안 찾았는지" 구분이 안 되므로 줄 자체를 만들지 않는다.
    assert "동일업종 0곳" not in facts


def test_isochrone_facts_state_disconnected_network_instead_of_fabricating_zero():
    facts = agent._tool_facts_from_dict(
        "isochrone",
        {
            "mode": "walk",
            "start_connected": False,
            "bands": [],
            "warnings": ["No start node"],
        },
    )
    assert "도보 도달권역: 분석 위치 주변 도로망을 찾지 못해 계산할 수 없음" in facts
    # 밴드가 없으니 몇 명이 사는지에 대한 값을 지어내지 않는다.
    assert "거주인구" not in facts


def test_isochrone_facts_surface_warnings_such_as_vehicle_approximation():
    facts = agent._tool_facts_from_dict(
        "isochrone",
        {
            "mode": "drive",
            "start_connected": True,
            "bands": [
                {"minutes": 10, "population_estimate": 5000, "store_count": 12, "same_category_count": 0}
            ],
            "warnings": ["차량 도달권은 직선거리 기반 근사입니다."],
        },
    )
    assert "차량 도달권은 직선거리 기반 근사입니다." in facts


def test_extract_intent_accepts_isochrone_tool(monkeypatch):
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "cafe", "tool": "isochrone"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    intent = asyncio.run(agent._extract_intent("신촌 카페 도보 10분 안에 몇 명 살아?"))
    assert intent.llm_tool == "isochrone"
    assert intent.reach_minutes == 10
    assert intent.reach_mode == "walk"


def test_agent_ask_runs_isochrone_tool_end_to_end(monkeypatch):
    async def _llm(**_kwargs):
        return None  # 규칙 폴백으로도 라우팅이 되는지 함께 검증한다.

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    captured = {}

    from app.models.schemas import IsochroneBandOut, IsochroneResponse

    async def _isochrone(request):
        captured["request"] = request
        return IsochroneResponse(
            lat=37.5, lng=127.0, mode="walk", start_connected=True,
            bands=[
                IsochroneBandOut(
                    minutes=10, max_distance_m=750.0, polygon={"type": "Polygon", "coordinates": []},
                    area_m2=100_000.0, store_count=55, same_category_count=4,
                    population_estimate=8400,
                )
            ],
        )

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_isochrone", _isochrone)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="신촌 카페 도보 10분 안에 몇 명 살아?", include_answer=False
            )
        )
    )

    assert resp.tool == "isochrone"
    assert captured["request"].mode == "walk"
    assert captured["request"].minutes == [10]
    assert resp.tool_result["bands"][0]["population_estimate"] == 8400
    assert resp.priorities == []


def test_extract_franchise_brand_reads_the_word_right_before_a_trigger_phrase():
    assert agent._extract_franchise_brand("이디야 창업비용 얼마야") == "이디야"
    assert agent._extract_franchise_brand("메가커피 폐업률 어때") == "메가커피"
    assert agent._extract_franchise_brand("BBQ 가맹점 수 얼마나 돼") == "BBQ"
    # 트리거 낱말 앞에 상호가 없고 요청어만 있으면 브랜드로 보지 않는다.
    assert agent._extract_franchise_brand("여기 창업비용 얼마야") is None
    # 애초에 트리거 낱말이 없으면 당연히 없다.
    assert agent._extract_franchise_brand("이디야 근처에 카페 낼까") is None
    # 업종어가 트리거 앞에 오면 그건 브랜드가 아니라 업종이다.
    assert agent._extract_franchise_brand("카페 창업비용 얼마야") is None


def test_fallback_franchise_brand_does_not_search_without_trigger_phrase(monkeypatch):
    async def _boom(q, limit=10):
        raise AssertionError("트리거 낱말이 없는 문장에서는 검색을 하면 안 된다")

    monkeypatch.setattr(agent, "run_franchise_brands", _boom)
    assert asyncio.run(agent._fallback_franchise_brand("이디야 커피 근처에 카페 낼까")) is None


def test_fallback_franchise_brand_searches_the_real_table_when_regex_misses(monkeypatch):
    from app.routers.franchise import FranchiseBrandItem, FranchiseBrandListOut

    seen = []

    async def _brands(q, limit=10):
        seen.append(q)
        if q == "이디야":
            return FranchiseBrandListOut(
                brands=[
                    FranchiseBrandItem(
                        brand_nm="이디야커피", industry_mid="커피전문점",
                        hq_conm="㈜이디야", frcs_cnt=3000, yr="2024",
                    )
                ]
            )
        return FranchiseBrandListOut(brands=[])

    monkeypatch.setattr(agent, "run_franchise_brands", _brands)
    # 트리거("창업비용") 바로 앞 낱말이 업종어 "커피"라 정규식은 못 잡는다.
    assert agent._extract_franchise_brand("이디야 커피 창업비용 얼마야") is None
    # 폴백은 문장에 나온 순서대로 낱말을 검색해 실제 테이블에 있는 브랜드를 찾는다.
    brand = asyncio.run(agent._fallback_franchise_brand("이디야 커피 창업비용 얼마야"))
    assert brand == "이디야커피"
    assert seen[0] == "이디야"


def test_fallback_franchise_brand_returns_none_when_search_finds_nothing(monkeypatch):
    from app.routers.franchise import FranchiseBrandListOut

    async def _empty(q, limit=10):
        return FranchiseBrandListOut(brands=[])

    monkeypatch.setattr(agent, "run_franchise_brands", _empty)
    # 실재하지 않는 브랜드는 검색해도 안 나오므로 지어내지 않고 None을 지킨다.
    assert asyncio.run(agent._fallback_franchise_brand("없는브랜드 커피 창업비용 얼마야")) is None


def test_extract_intent_uses_brand_search_fallback_when_regex_misses(monkeypatch):
    from app.routers.franchise import FranchiseBrandItem, FranchiseBrandListOut

    async def _llm_fails(**_kwargs):
        return None

    async def _brands(q, limit=10):
        if q == "이디야":
            return FranchiseBrandListOut(
                brands=[
                    FranchiseBrandItem(
                        brand_nm="이디야커피", industry_mid="커피전문점",
                        hq_conm="㈜이디야", frcs_cnt=3000, yr="2024",
                    )
                ]
            )
        return FranchiseBrandListOut(brands=[])

    monkeypatch.setattr(agent, "complete_json", _llm_fails)
    monkeypatch.setattr(agent, "run_franchise_brands", _brands)

    intent = asyncio.run(agent._extract_intent("이디야 커피 창업비용 얼마야", []))
    assert intent.franchise_brand == "이디야커피"
    # 도구 선택도 이 값을 그대로 타고 franchise_brand_detail로 간다.
    tool = agent._rule_tool(
        "이디야 커피 창업비용 얼마야", intent.params, 1,
        franchise_brand=intent.franchise_brand,
    )
    assert tool == "franchise_brand_detail"


def test_tool_is_chosen_for_franchise_brand_detail_questions():
    def tool(q, places=1):
        return agent._rule_tool(
            q, agent._extract_parameters(q), places,
            franchise_brand=agent._extract_franchise_brand(q),
        )

    assert tool("이디야 창업비용 얼마야") == "franchise_brand_detail"
    assert tool("신촌에서 메가커피 폐업률 어때") == "franchise_brand_detail"
    # 브랜드가 안 잡히면 다른 신호로 라우팅된다(값이 없으니 기본 경로).
    assert tool("여기 창업비용 얼마야") == "site_score"
    # 값이 있는 질문이어도 브랜드+트리거가 뚜렷하면 가맹정보 조회가 우선이다.
    assert tool("신촌 월세 300만원인데 이디야 창업비용도 알려줘") == "franchise_brand_detail"


def test_franchise_brand_detail_tool_needs_a_brand():
    point = agent._Point(37.5, 127.0, "테스트")
    without_brand = agent._Intent(address="신촌")
    ready, hint = agent._tool_ready("franchise_brand_detail", without_brand, [point])
    assert ready is False and "브랜드" in hint

    with_brand = agent._Intent(address="신촌", franchise_brand="이디야")
    assert agent._tool_ready("franchise_brand_detail", with_brand, [point])[0] is True


def test_run_tool_calls_franchise_brand_detail_with_brand_name(monkeypatch):
    from app.routers.franchise import FranchiseBrandDetailOut

    captured = {}

    async def _fake_detail(name):
        captured["name"] = name
        return FranchiseBrandDetailOut(
            brand_nm="이디야커피", industry_mid="커피전문점", hq_conm="㈜이디야",
            yr="2024", frcs_cnt=3000, new_open_cnt=120, contract_end_cnt=80,
            contract_cancel_cnt=20, churn_pct=3.3, startup_cost_total_manwon=9500.0,
        )

    monkeypatch.setattr(agent, "run_franchise_brand_detail", _fake_detail)
    intent = agent._Intent(address="신촌", franchise_brand="이디야")
    point = agent._Point(37.5, 127.0, "신촌")

    tool, result = asyncio.run(
        agent._run_tool("franchise_brand_detail", intent, [point], 500)
    )

    assert tool == "franchise_brand_detail"
    assert captured["name"] == "이디야"
    assert result.brand_nm == "이디야커피"
    assert result.startup_cost_total_manwon == 9500.0


def test_run_tool_franchise_brand_detail_returns_none_when_brand_not_found(monkeypatch):
    async def _not_found(name):
        raise HTTPException(status_code=404, detail=f"brand not found: {name}")

    monkeypatch.setattr(agent, "run_franchise_brand_detail", _not_found)
    intent = agent._Intent(address="신촌", franchise_brand="없는브랜드")
    point = agent._Point(37.5, 127.0, "신촌")

    tool, result = asyncio.run(
        agent._run_tool("franchise_brand_detail", intent, [point], 500)
    )

    assert tool == "franchise_brand_detail"
    assert result is None


def test_franchise_brand_detail_facts_report_cost_and_churn():
    facts = agent._tool_facts_from_dict(
        "franchise_brand_detail",
        {
            "brand_nm": "이디야커피", "industry_mid": "커피전문점", "hq_conm": "㈜이디야",
            "yr": "2024", "frcs_cnt": 3000, "new_open_cnt": 120, "contract_end_cnt": 80,
            "contract_cancel_cnt": 20, "churn_pct": 3.3, "startup_cost_total_manwon": 9500.0,
        },
    )
    assert "이디야커피" in facts and "커피전문점" in facts and "㈜이디야" in facts
    assert "2024년 가맹정보공개서" in facts
    assert "가맹점수: 3000곳" in facts
    assert "폐업률: 3.3%" in facts
    assert "계약종료 80곳" in facts and "계약해지 20곳" in facts
    assert "신규 개점: 120곳" in facts
    # startup_cost_total_manwon(만원)이 그대로가 아니라 원 단위로 풀려야 한다.
    assert "9,500" in facts


def test_franchise_brand_detail_facts_do_not_fabricate_missing_cost():
    # startup_cost_total_manwon이 None이면 그 줄 자체를 만들지 않는다 — 0이나
    # 평균으로 지어내지 않는다.
    facts = agent._tool_facts_from_dict(
        "franchise_brand_detail",
        {"brand_nm": "테스트브랜드", "frcs_cnt": 10, "churn_pct": 0.0},
    )
    assert "총 창업비용" not in facts
    assert "폐업률: 0.0%" in facts


def test_agent_ask_returns_franchise_brand_detail_tool_result(monkeypatch):
    from app.routers.franchise import FranchiseBrandDetailOut

    async def _llm(**_kwargs):
        return {
            "places": ["신촌"], "business_type": "cafe",
            "tool": "franchise_brand_detail",
        }

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    captured = {}

    async def _detail(name):
        captured["name"] = name
        return FranchiseBrandDetailOut(
            brand_nm="이디야커피", industry_mid="커피전문점", hq_conm="㈜이디야",
            yr="2024", frcs_cnt=3000, new_open_cnt=120, contract_end_cnt=80,
            contract_cancel_cnt=20, churn_pct=3.3, startup_cost_total_manwon=9500.0,
        )

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_franchise_brand_detail", _detail)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="신촌에서 이디야 창업비용 얼마야", include_answer=False
            )
        )
    )

    assert resp.tool == "franchise_brand_detail"
    assert captured["name"] == "이디야"
    assert resp.tool_result["brand_nm"] == "이디야커피"
    assert resp.tool_result["startup_cost_total_manwon"] == 9500.0
    assert resp.tool_result["churn_pct"] == 3.3


def test_agent_ask_explains_franchise_brand_not_found_instead_of_blaming_timeout(monkeypatch):
    async def _llm(**_kwargs):
        return {
            "places": ["신촌"], "business_type": "cafe",
            "tool": "franchise_brand_detail",
        }

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _not_found(name):
        raise HTTPException(status_code=404, detail=f"brand not found: {name}")

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_franchise_brand_detail", _not_found)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="신촌에서 없는브랜드 창업비용 얼마야", include_answer=False
            )
        )
    )

    assert resp.tool == "site_score"
    assert resp.tool_result is None
    assert "가맹정보를 찾지 못했" in resp.notice
    assert "오래 걸려" not in resp.notice


def test_tool_is_chosen_for_simulate_questions():
    def tool(q, places=1):
        return agent._rule_tool(q, agent._extract_parameters(q), places)

    assert tool("여기 길 뚫리면 어떻게 돼?") == "simulate"
    assert tool("이 근처에 새로 도로 하나 뚫으면 통합도가 좋아질까") == "simulate"
    assert tool("이 도로 막으면 어떻게 될까") == "simulate"
    assert tool("신설도로 시뮬레이션 해줘") == "simulate"
    # 값이 있는 질문은 그 값이 이긴다 — 계약 판단이 먼저다.
    assert tool("신촌 월세 300만원인데 길 뚫리면 어때") == "lease_verdict"
    # 값 없는 평범한 질문은 여전히 site_score다.
    assert tool("망원동 카페 자리 어때") == "site_score"


def test_simulate_tool_is_never_ready_from_chat_text():
    # 새 도로의 좌표(폴리라인)나 막을 도로의 OSM ID는 지도에서 그려야만 나오는
    # 값이라 문장만으로는 절대 뽑을 수 없다 — 지어내면 그리지 않은 도로를 분석한
    # 척하게 된다. 그래서 이 도구는 항상 "준비되지 않음"으로 보고 지도 화면을
    # 안내해야 한다.
    point = agent._Point(37.5, 127.0, "테스트")
    intent = agent._Intent(address="신촌")
    ready, hint = agent._tool_ready("simulate", intent, [point])
    assert ready is False
    assert "지도" in hint


def test_agent_ask_explains_simulate_needs_the_map_instead_of_running_site_score_silently(
    monkeypatch,
):
    # "여기 길 뚫리면 어떻게 돼?"는 이 도구로 인식은 되지만, 새 도로 좌표가
    # 채팅에는 없으니 site_score로 조용히 폴백하는 대신 지도 화면을 쓰라고
    # 정확히 알려야 한다(territory·franchise_brand_detail과 같은 패턴).
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "cafe", "tool": "simulate"}

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="신촌에 길 뚫리면 어떻게 돼?", include_answer=False)
        )
    )

    assert resp.tool == "site_score"
    assert resp.tool_result is None
    assert "지도" in resp.notice
    assert "오래 걸려" not in resp.notice


def test_salad_shop_is_not_analyzed_as_korean_food():
    """'샐러드집'이 "한식 음식점업"으로 분석되지 않는다.

    '샐러드'는 restaurant 프리셋으로 구제돼 있는데, 그 프리셋은 소분류를
    I201(한식)로 고정으로 박는다. 그래서 업종 라벨과 동일업종 경쟁 집계가
    통째로 한식 기준이 됐다 — 사실이 아니다. 업종 표에는 I21005(토스트/
    샌드위치/샐러드)가 정확히 들어 있으므로 그쪽이 이겨야 한다.

    붙여 쓴 것도 같이 본다 — 라이브에서 실제로 들어온 형태가 그것이다.
    """
    tree = asyncio.run(agent.get_upjong_tree())
    for query in ("홍대입구샐러드집 괜찮아?", "홍대입구 샐러드 집 괜찮아?"):
        btype = agent._keyword_business_type(query)
        found = agent._specific_upjong_beats_preset(btype, tree, query)
        assert found is not None, f"{query}: 소분류를 못 찾았다"
        assert found[0]["inds_scls_cd"] == "I21005", f"{query}: {found[0]}"


def test_generic_restaurant_words_keep_the_preset():
    """통칭은 프리셋 그대로 둔다 — 양보를 넓히면 더 틀린다.

    '강남 식당'까지 업종 표로 보내면 별칭 '구내식당'에 걸려 "구내식당 및 뷔페"가
    된다. 뭉개진 한식보다 더 틀린 답이다. 카페·편의점도 프리셋 자체가 제 업종이라
    (카페 KPI 파일럿이 이 경로에 붙어 있다) 건드리지 않는다.
    """
    tree = asyncio.run(agent.get_upjong_tree())
    for query in ("강남 식당 어때?", "연남동 카페", "신촌 편의점 어때?"):
        btype = agent._keyword_business_type(query)
        assert agent._specific_upjong_beats_preset(btype, tree, query) is None, query


def test_llm_path_also_uses_the_specific_upjong_not_the_preset(monkeypatch):
    """LLM이 붙은 요청도 소분류를 쓴다 — 규칙 경로만 고치면 새어 나간다.

    실제 사용자 요청은 대부분 LLM 파서를 탄다(answer_source=llm). 그 분기는
    `btype == "general"`일 때만 소분류를 채우고 있었고, '샐러드'는 restaurant로
    잡히니 소분류가 None으로 남아 프리셋의 한식(I201)이 그대로 이겼다.
    규칙 경로만 고치고 여기를 빼먹어 라이브에서 계속 한식으로 나왔다.
    """
    async def _llm(**_kwargs):
        return {"address": "서울 마포구 홍대입구", "business_type": "restaurant"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    tree = asyncio.run(agent.get_upjong_tree())
    intent = asyncio.run(agent._extract_intent("홍대입구샐러드집 괜찮아?", tree))
    assert intent.business_type == "restaurant"
    assert (intent.upjong or {}).get("inds_scls_cd") == "I21005", intent.upjong


def test_llm_path_keeps_the_preset_when_no_specific_upjong_applies(monkeypatch):
    """통칭 요청은 소분류를 억지로 붙이지 않는다."""
    async def _llm(**_kwargs):
        return {"address": "서울 강남구", "business_type": "restaurant"}

    monkeypatch.setattr(agent, "complete_json", _llm)
    tree = asyncio.run(agent.get_upjong_tree())
    intent = asyncio.run(agent._extract_intent("강남 식당 어때?", tree))
    assert intent.business_type == "restaurant"
    assert intent.upjong is None


def test_extract_intent_falls_back_to_franchise_search_when_regex_misses_brand(monkeypatch):
    from app.routers.franchise import FranchiseBrandItem, FranchiseBrandListOut

    async def _no_llm(**_kwargs):
        return None

    async def _fake_search(q, limit=10):
        if q == "이디야":
            return FranchiseBrandListOut(
                brands=[FranchiseBrandItem(brand_nm="이디야커피", yr="2024", frcs_cnt=3000)]
            )
        return FranchiseBrandListOut(brands=[])

    monkeypatch.setattr(agent, "complete_json", _no_llm)
    monkeypatch.setattr(agent, "run_franchise_brands", _fake_search)

    intent = asyncio.run(agent._extract_intent("창업비용 궁금한데 신촌에서 이디야 어때"))
    assert intent.franchise_brand == "이디야커피"



def test_agent_ask_uses_franchise_search_fallback_end_to_end(monkeypatch):
    from app.routers.franchise import (
        FranchiseBrandDetailOut,
        FranchiseBrandItem,
        FranchiseBrandListOut,
    )

    async def _llm(**_kwargs):
        return {
            "places": ["신촌"], "business_type": "cafe",
            "tool": "franchise_brand_detail",
        }

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _fake_search(q, limit=10):
        if q == "이디야":
            return FranchiseBrandListOut(
                brands=[FranchiseBrandItem(brand_nm="이디야커피", yr="2024", frcs_cnt=3000)]
            )
        return FranchiseBrandListOut(brands=[])

    captured = {}

    async def _detail(name):
        captured["name"] = name
        return FranchiseBrandDetailOut(
            brand_nm="이디야커피", industry_mid="커피전문점", hq_conm="㈜이디야",
            yr="2024", frcs_cnt=3000, new_open_cnt=120, contract_end_cnt=80,
            contract_cancel_cnt=20, churn_pct=3.3, startup_cost_total_manwon=9500.0,
        )

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_franchise_brands", _fake_search)
    monkeypatch.setattr(agent, "run_franchise_brand_detail", _detail)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="창업비용 궁금한데 신촌에서 이디야 어때", include_answer=False
            )
        )
    )

    assert resp.tool == "franchise_brand_detail"
    # 규칙 정규식은 이 문장에서 브랜드를 못 잡는다 — 검색 폴백이 찾아낸 실제
    # 브랜드명("이디야커피")이 그대로 franchise_brand_detail 호출에 실렸는지 본다.
    assert captured["name"] == "이디야커피"
    assert resp.tool_result["brand_nm"] == "이디야커피"
    assert resp.tool_result["startup_cost_total_manwon"] == 9500.0



def test_continue_from_history_fills_missing_address_and_business_type():
    intent = agent._Intent(
        address="", business_type="general", params={"monthly_rent_man": 300.0}
    )
    history = [
        agent.AgentHistoryTurn(
            address="신촌", business_type="cafe", params={"area_pyeong": 15.0}
        )
    ]
    merged = agent._continue_from_history(intent, history)
    assert merged.address == "신촌"
    assert merged.places == ["신촌"]
    assert merged.business_type == "cafe"
    # 이번 문장 값(월세)과 직전 값(평수)이 둘 다 남아야 한다.
    assert merged.params == {"area_pyeong": 15.0, "monthly_rent_man": 300.0}



def test_continue_from_history_ignores_history_when_query_has_its_own_place():
    # 이번 문장이 새 장소를 댔으면(비교 질의 등) 그쪽이 우선이다.
    intent = agent._Intent(address="연남동", business_type="general", params={})
    history = [agent.AgentHistoryTurn(address="신촌", business_type="cafe")]
    merged = agent._continue_from_history(intent, history)
    assert merged.address == "연남동"
    assert merged.business_type == "general"



def test_continue_from_history_keeps_new_business_type_but_still_borrows_place():
    # 장소는 없지만 업종은 새로 말했으면 그 업종이 이긴다.
    intent = agent._Intent(address="", business_type="convenience", params={})
    history = [agent.AgentHistoryTurn(address="신촌", business_type="cafe")]
    merged = agent._continue_from_history(intent, history)
    assert merged.address == "신촌"
    assert merged.business_type == "convenience"



def test_continue_from_history_noop_without_history():
    intent = agent._Intent(address="", business_type="general")
    merged = agent._continue_from_history(intent, [])
    assert merged.address == ""
    assert merged.business_type == "general"



def test_continue_from_history_treats_bare_category_word_as_no_place():
    # "카페"만 남은 건 추출기가 걷어낼 게 없어 업종어를 그대로 돌려준 것뿐이다
    # — 실제 장소가 아니므로 직전 턴에서 이어받아야 한다.
    intent = agent._Intent(address="카페", business_type="cafe", params={})
    history = [agent.AgentHistoryTurn(address="신촌", business_type="cafe")]
    merged = agent._continue_from_history(intent, history)
    assert merged.address == "신촌"



def test_continue_from_history_borrows_place_when_extractor_echoes_the_sentence():
    """지명이 없는 후속 질문은 추출기가 **문장을 통째로** 주소로 돌려준다.

    이걸 장소로 믿으면 직전 턴을 못 이어받고 지오코딩까지 가서 400으로 죽는다
    — 실제로 그렇게 죽고 있었다. 후속 질문은 대화에서 가장 흔한 형태라, 이게
    곧 "대화가 이어지지 않는다"의 정체다.
    """
    for echoed in (
        "월세 300이면 계약해도 될까?",
        "여기 15평이야",
        "그럼 얼마면 괜찮을까",
        "저평가된 땅 있어?",   # 어미('?')가 이미 걷힌 뒤에도 잡혀야 한다
        "그거 왜 그런데",
    ):
        intent = agent._Intent(address=echoed, business_type="general", params={})
        history = [agent.AgentHistoryTurn(address="신촌", business_type="cafe")]
        merged = agent._continue_from_history(intent, history)
        assert merged.address == "신촌", f"{echoed!r}를 장소로 오인했다"


def test_has_place_accepts_real_place_names():
    """문장을 걸러내려다 진짜 지명까지 버리면 안 된다."""
    for place in (
        "여의도동",
        "영등포구 여의동",
        "강남구 역삼동",
        "서울역",
        "홍대입구",
        "신촌",
        "영등포동5가",      # 숫자+가
        "여의도 한강공원",
    ):
        assert agent._has_place(place), f"{place!r}를 장소가 아니라고 판단했다"


def test_has_place_rejects_sentences_that_end_like_places():
    """단음절 꼬리가 우연히 맞는 경우 — 꼬리 검사만으로는 못 거른다.

    '300이면'·'얼마면'의 '면'을 읍/면/리의 '면'으로 볼 뻔했다. 서울에는 읍·면·리가
    없어서 꼬리 목록에서 뺐고, '가'는 숫자 뒤에서만 인정한다(안 그러면 '저평가'가
    걸린다).
    """
    for sentence in ("월세 300이면", "그럼 얼마면", "저평가", "이 지역"):
        assert not agent._has_place(sentence), f"{sentence!r}를 장소로 오인했다"


def test_continue_from_history_does_nothing_when_last_turn_has_no_place():
    intent = agent._Intent(address="", business_type="general")
    history = [agent.AgentHistoryTurn(address="", business_type="cafe")]
    merged = agent._continue_from_history(intent, history)
    assert merged.address == ""



def test_continue_from_history_carries_sbiz_upjong_for_non_preset_business():
    # 프리셋 밖 업종(꽃집 등)은 business_type만으론 이어받을 수 없다 — SBIZ
    # 코드까지 그대로 넘어가야 같은 업종으로 계속 분석한다.
    intent = agent._Intent(address="", business_type="general", upjong=None)
    upjong = {
        "inds_lcls_cd": "G2", "inds_mcls_cd": "G219",
        "inds_scls_cd": "G21901", "label": "꽃집",
    }
    history = [
        agent.AgentHistoryTurn(address="망원동", business_type="general", upjong=upjong)
    ]
    merged = agent._continue_from_history(intent, history)
    assert merged.address == "망원동"
    assert merged.business_type == "general"
    assert merged.upjong == upjong



def test_agent_ask_runs_followup_lease_verdict_with_place_and_business_from_history(
    monkeypatch,
):
    # "신촌 카페 어때" 다음에 장소·업종을 다시 대지 않은 "월세 300이면 계약해도
    # 될까?"가 실제로 신촌·카페 기준 lease_verdict를 그 좌표로 불러야 한다.
    from app.routers.goals import LeaseVerdictResponse, NegotiationOut

    async def _llm(**_kwargs):
        return {"business_type": "general"}  # 이번 문장엔 장소·업종 신호가 없다

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    captured = {}

    async def _lease_verdict(request):
        captured["request"] = request
        return LeaseVerdictResponse(
            verdict="ok", verdict_label="적정", recommend_score=80.0,
            negotiation=NegotiationOut(),
        )

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_lease_verdict", _lease_verdict)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    history = [
        agent.AgentHistoryTurn(
            query="신촌 카페 자리 어때", address="신촌", business_type="cafe",
        )
    ]
    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="월세 300이면 계약해도 될까?",
                include_answer=False,
                history=history,
            )
        )
    )

    assert resp.tool == "lease_verdict"
    assert resp.address == "신촌"
    assert resp.business_type == "cafe"
    assert captured["request"].lat == 37.5
    assert captured["request"].lng == 127.0
    assert captured["request"].monthly_rent_man == 300.0



def test_agent_ask_roundtrips_upjong_through_history_for_non_preset_business(
    monkeypatch,
):
    # 프리셋 밖 업종(꽃집)도 대화가 이어져야 한다: 1턴에서 받은 upjong을 그대로
    # history에 담아 2턴을 물으면, 2턴이 같은 SBIZ 코드로 도구를 부른다.
    from app.routers.goals import LeaseVerdictResponse, NegotiationOut

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=126.9, name="망원동")

    async def _site_score(_request):
        return _full_site(business_type="general", business_label="꽃집")

    async def _llm_turn1(**_kwargs):
        return {"places": ["망원동"], "business_word": "꽃집"}

    monkeypatch.setattr(agent, "complete_json", _llm_turn1)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    turn1 = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="망원동 꽃집 자리 어때", include_answer=False)
        )
    )
    assert turn1.upjong is not None
    assert turn1.upjong.get("label") == "꽃집"

    history = [
        agent.AgentHistoryTurn(
            query="망원동 꽃집 자리 어때",
            address=turn1.address,
            business_type=turn1.business_type,
            upjong=turn1.upjong,
            params=turn1.parameters,
        )
    ]

    async def _llm_turn2(**_kwargs):
        return {"business_type": "general"}

    captured = {}

    async def _lease_verdict(request):
        captured["request"] = request
        return LeaseVerdictResponse(
            verdict="ok", verdict_label="적정", recommend_score=80.0,
            negotiation=NegotiationOut(),
        )

    monkeypatch.setattr(agent, "complete_json", _llm_turn2)
    monkeypatch.setattr(agent, "run_lease_verdict", _lease_verdict)

    turn2 = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="월세 250이면 계약해도 될까?",
                include_answer=False,
                history=history,
            )
        )
    )

    assert turn2.tool == "lease_verdict"
    assert turn2.address == "망원동"
    assert captured["request"].inds_scls_cd == "G21901"
    assert captured["request"].monthly_rent_man == 250.0



def test_merge_pending_context_fills_missing_address_and_business_type():
    context = agent.AgentPendingContext(
        tool="lease_verdict", address="신촌", business_type="cafe",
        params={"deposit_man": 5000.0},
    )
    intent = agent._Intent(address="", business_type="general", params={"monthly_rent_man": 300.0})
    merged = agent._merge_pending_context(intent, context)
    assert merged.address == "신촌"
    assert merged.business_type == "cafe"
    # pending의 값과 이번 문장의 값이 합쳐져야 한다 — 하나가 다른 하나를 지우면 안 된다.
    assert merged.params == {"deposit_man": 5000.0, "monthly_rent_man": 300.0}
    assert merged.llm_tool == "lease_verdict"



def test_merge_pending_context_prefers_the_new_messages_own_values():
    # 사용자가 지점·월세를 다시 말했으면(정정) 그쪽이 이겨야 한다.
    context = agent.AgentPendingContext(
        tool="lease_verdict", address="신촌", business_type="cafe",
        params={"monthly_rent_man": 200.0},
    )
    intent = agent._Intent(address="연남동", business_type="restaurant", params={"monthly_rent_man": 350.0})
    merged = agent._merge_pending_context(intent, context)
    assert merged.address == "연남동"
    assert merged.business_type == "restaurant"
    assert merged.params["monthly_rent_man"] == 350.0



def test_merge_pending_context_treats_a_bare_number_as_not_a_place():
    # "300이요"처럼 숫자만 오면 규칙 추출기가 지명 자리에 숫자를 그대로 남긴다
    # (_rule_extract_address의 "아무것도 안 남으면 원문 그대로" 폴백) — 그
    # 잔여물을 지명으로 오인해 pending의 지점을 덮으면 안 된다.
    context = agent.AgentPendingContext(tool="lease_verdict", address="신촌", business_type="cafe")
    intent = agent._Intent(address="300이요", business_type="general")
    merged = agent._merge_pending_context(intent, context)
    assert merged.address == "신촌"



def test_apply_bare_fallback_reads_labelless_number_for_the_one_missing_value():
    context = agent.AgentPendingContext(tool="lease_verdict", address="신촌", business_type="cafe")
    intent = agent._Intent(address="신촌", business_type="cafe", params={})
    filled = agent._apply_bare_fallback(intent, context, "300이요")
    assert filled.params.get("monthly_rent_man") == 300.0



def test_apply_bare_fallback_skips_when_more_than_one_value_still_missing():
    # purchase_verdict는 매입가·면적 둘 다 필요하다 — 숫자 하나로는 어느 것에
    # 답한 말인지 알 수 없으므로 손대지 않는다.
    context = agent.AgentPendingContext(tool="purchase_verdict", address="신촌")
    intent = agent._Intent(address="신촌", params={})
    filled = agent._apply_bare_fallback(intent, context, "80000")
    assert filled.params == {}



def test_apply_bare_fallback_reads_target_model_keyword():
    context = agent.AgentPendingContext(tool="academy_cluster", address="신촌", business_type="academy")
    intent = agent._Intent(address="신촌", business_type="academy")
    filled = agent._apply_bare_fallback(intent, context, "수학이요")
    assert filled.target_model == "math"



def test_apply_bare_fallback_reads_short_brand_reply():
    context = agent.AgentPendingContext(
        tool="franchise_brand_detail", address="신촌", business_type="cafe"
    )
    intent = agent._Intent(address="신촌", business_type="cafe")
    filled = agent._apply_bare_fallback(intent, context, "이디야")
    assert filled.franchise_brand == "이디야"



def test_apply_bare_fallback_leaves_intent_untouched_when_nothing_matches():
    context = agent.AgentPendingContext(tool="lease_verdict", address="신촌", business_type="cafe")
    intent = agent._Intent(address="신촌", business_type="cafe", params={})
    filled = agent._apply_bare_fallback(intent, context, "잘 모르겠어요")
    assert filled.params == {}



def test_agent_ask_asks_back_with_pending_when_llm_tool_is_missing_its_value(monkeypatch):
    # "계약해도 될까"는 LLM이 lease_verdict로 읽지만 월세가 문장에 없다. 예전
    # 코드는 규칙이 site_score를 고른 뒤로는 그 사실을 다시 확인하지 않아
    # 아무 알림도 없이 조용히 site_score로 넘어갔다 — 사용자는 자기 질문이
    # 무시됐는지조차 알 수 없었다. 이제는 되묻고, 다음 턴에 이어 갈 수 있도록
    # pending에 지금까지 뽑은 것을 남겨야 한다.
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "cafe", "tool": "lease_verdict"}

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    async def _boom(*_a, **_k):
        raise AssertionError("월세 없이 lease_verdict를 부르면 안 된다")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)
    monkeypatch.setattr(agent, "run_lease_verdict", _boom)

    # "신촌 어딘가"처럼 지명 자리에 두 낱말이 남아야 규칙 폴백의 확신 경로를
    # 건너뛰고 LLM 파서(위 _llm)를 실제로 거친다 — "신촌에"처럼 한 낱말로 깔끔히
    # 남으면 왕복을 아끼려고 LLM을 아예 안 부르고 llm_tool도 비어 있게 된다.
    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="신촌 어딘가 카페 계약해도 될까?", include_answer=False
            )
        )
    )

    assert resp.tool == "site_score"
    assert resp.tool_result is None
    assert "월세" in resp.notice
    assert resp.pending is not None
    assert resp.pending.tool == "lease_verdict"
    assert resp.pending.address == "신촌"
    assert resp.pending.business_type == "cafe"



def test_agent_ask_completes_the_pending_tool_from_a_bare_followup_reply(monkeypatch):
    # 직전 턴의 pending을 다음 요청의 context로 그대로 돌려보내면, "300이요"만
    # 담은 대답도 지점·업종을 다시 말하지 않고 원래 하려던 계약 판정으로 이어져야
    # 한다 — "한 번 알리고 끝"이 아니라 되물어서 값을 끌어낸 뒤 실제로 완성한다.
    from app.routers.goals import LeaseVerdictResponse, NegotiationOut

    pending = agent.AgentPendingContext(
        tool="lease_verdict", address="신촌", business_type="cafe",
    )

    async def _llm(**_kwargs):
        return None  # LLM 없이도(규칙 폴백) 이어져야 한다.

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    captured = {}

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)

    async def _fake_lease_verdict(request):
        captured["request"] = request
        return LeaseVerdictResponse(
            verdict="ok", verdict_label="계약해도 좋습니다", recommend_score=80.0,
            negotiation=NegotiationOut(),
        )

    monkeypatch.setattr(agent, "run_lease_verdict", _fake_lease_verdict)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="300이요", include_answer=False, context=pending)
        )
    )

    assert resp.tool == "lease_verdict"
    assert resp.notice == ""
    assert resp.pending is None
    assert captured["request"].monthly_rent_man == 300
    assert captured["request"].lat == 37.5 and captured["request"].lng == 127.0
    assert resp.tool_result["verdict_label"] == "계약해도 좋습니다"



def test_agent_ask_reasks_when_the_followup_still_has_no_value(monkeypatch):
    # 다음 턴 대답도 값을 못 채우면, 그냥 site_score로 조용히 넘어가지 않고
    # 같은 질문을 다시 겨냥해야 한다(대화가 끊기지 않는다).
    pending = agent.AgentPendingContext(
        tool="lease_verdict", address="신촌", business_type="cafe",
    )

    async def _llm(**_kwargs):
        return None

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    async def _boom(*_a, **_k):
        raise AssertionError("값이 여전히 없는데 lease_verdict를 부르면 안 된다")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)
    monkeypatch.setattr(agent, "run_lease_verdict", _boom)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="잘 모르겠어요", include_answer=False, context=pending
            )
        )
    )

    assert resp.tool == "site_score"
    assert "월세" in resp.notice
    assert resp.pending is not None
    assert resp.pending.tool == "lease_verdict"



def test_rule_tools_chains_independent_signals_found_in_one_sentence():
    # "월세 300만원"은 lease_verdict를, "2호점 내면 잠식"은 cannibalization을
    # 각각 가리킨다 — 문장 하나가 값 판정과 구조 분석을 동시에 요구한 것이므로
    # 더 이상 하나만 고르고 나머지를 버리면 안 된다.
    tools = agent._rule_tools(
        "신촌 월세 300만원인데 2호점 내면 잠식되나", {"monthly_rent_man": 300.0}, 1,
    )
    assert tools == ["lease_verdict", "cannibalization"]
    # 하나만 필요한 기존 호출부(_rule_tool)는 여전히 가장 확실한 것 하나만 받는다.
    assert agent._rule_tool(
        "신촌 월세 300만원인데 2호점 내면 잠식되나", {"monthly_rent_man": 300.0}, 1,
    ) == "lease_verdict"



def test_rule_tools_chains_up_to_five_independent_domains():
    # 월세(lease_verdict) + 호점(cannibalization) + 권역(territory) + 경쟁구조
    # (real_competition) + 실거래(property_market) — 다섯 신호가 겹치지 않고
    # 각자 다른 물음이므로 전부 후보로 잡혀야 한다(예산 상한은 agent_ask가 건다).
    query = (
        "신촌 카페 월세 300만원인데 2호점 내면 잠식되는지, 배달권역도 겹치는지, "
        "경쟁구조랑 실거래가도 같이 봐줘"
    )
    params = agent._extract_parameters(query)
    assert params["monthly_rent_man"] == 300.0
    tools = agent._rule_tools(query, params, 1)
    assert tools == [
        "lease_verdict", "real_competition", "cannibalization",
        "territory", "property_market",
    ]



def test_rule_tools_keeps_documented_keyword_overlaps_exclusive():
    # 이 세 쌍은 신호가 진짜 두 가지 다른 질문이 아니라 같은 낱말이 두 트리거를
    # 동시에 건드리는 경우다 — 같은 도구를 이름만 바꿔 두 번 돌리는 꼴이 되므로
    # 여전히 하나만 골라야 한다(agent.py의 _rule_tools 주석 참고).
    params = agent._extract_parameters("오피스텔 시세 비교해줘")
    assert agent._rule_tools("오피스텔 시세 비교해줘", params, 1) == ["property_market"]

    params = agent._extract_parameters("연남동에서 뜨는 자리 추천해줘")
    assert agent._rule_tools("연남동에서 뜨는 자리 추천해줘", params, 1) == ["area_search"]

    params = agent._extract_parameters("수학학원인데 경쟁구조 어때?")
    tools = agent._rule_tools(
        "수학학원인데 경쟁구조 어때?", params, 1,
        business_type="academy", target_model="math",
    )
    assert tools == ["academy_cluster"]



def test_agent_ask_runs_chained_tools_and_keeps_primary_backward_compatible(monkeypatch):
    # "월세 300에 계약해도 되고 2호점 잠식도 걱정"류 질문은 이제 lease_verdict와
    # cannibalization을 둘 다 실제로 돌려야 한다. tool/tool_result(단수)는 여전히
    # 첫 번째 도구를 가리켜 기존 호출자가 그대로 동작해야 한다.
    from app.models.schemas import CannibalizationResponse
    from app.routers.goals import LeaseVerdictResponse, NegotiationOut

    async def _llm(**_kwargs):
        return None  # 규칙 폴백 경로로 검증한다.

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    lease_captured = {}
    cannib_captured = {}

    async def _fake_lease_verdict(request):
        lease_captured["request"] = request
        return LeaseVerdictResponse(
            verdict="reconsider", verdict_label="재검토", recommend_score=45.0,
            negotiation=NegotiationOut(), headline="월세가 적정선보다 높습니다.",
        )

    async def _fake_cannibalization(request):
        cannib_captured["request"] = request
        return CannibalizationResponse(
            risk_score=62.3, risk_label="중간", competitors=[],
            overlap_pct=41.0, overlap_with="이디야 신촌점", detail="",
            total_count=8,
        )

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)
    monkeypatch.setattr(agent, "run_lease_verdict", _fake_lease_verdict)
    monkeypatch.setattr(agent, "run_cannibalization", _fake_cannibalization)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="신촌 카페 월세 300만원인데 2호점 내면 잠식되나",
                include_answer=False,
            )
        )
    )

    # 주 도구(단수 필드)는 여전히 첫 번째 것 — 기존 호출자와 하위호환.
    assert resp.tool == "lease_verdict"
    assert resp.tool_result is not None
    assert resp.tool_result["verdict_label"] == "재검토"
    # 새 복수 필드에 실제로 계산된 두 도구의 값이 그대로 실려야 한다.
    assert resp.tools == ["lease_verdict", "cannibalization"]
    assert set(resp.tool_results.keys()) == {"lease_verdict", "cannibalization"}
    assert resp.tool_results["cannibalization"]["risk_score"] == 62.3
    assert resp.tool_results["cannibalization"]["risk_label"] == "중간"
    # 두 도구가 각각 자신에게 필요한 요청(월세·좌표)을 정확히 받았는지도 본다.
    assert lease_captured["request"].monthly_rent_man == 300.0
    assert cannib_captured["request"].lat == 37.5 and cannib_captured["request"].lng == 127.0
    # 실행 트레이스에도 두 단계가 모두 남는다.
    step_keys = {s.key for s in resp.steps}
    assert {"lease_verdict", "cannibalization"} <= step_keys
    # 서술 재료에도 두 도구의 판정이 모두 실제 값 그대로 접혀 들어가야 한다 —
    # 하나만 넘기면 나머지는 계산해 놓고도 답변에서 사라진다.
    facts = agent._facts_for_answer(
        resp.site, place="신촌", business_label="카페", question=resp.query,
        tool=resp.tool, tool_result=resp.tool_result, tool_results=resp.tool_results,
    )
    assert "월세가 적정선보다 높습니다." in facts
    assert "잠식 위험도: 62점 (중간, 높을수록 위험)" in facts
    # 이번 응답이 이미 lease_verdict·cannibalization을 돌렸으니 이어 물을 질문
    # 후보에서 그 둘은 빠지고, 아직 안 돌린 도구(trends 등)가 대신 나온다.
    assert agent._SUGGESTION_TEMPLATES["lease_verdict"] not in resp.suggested_questions
    assert agent._SUGGESTION_TEMPLATES["cannibalization"] not in resp.suggested_questions
    assert resp.suggested_questions
    assert len(resp.suggested_questions) <= 3



def test_agent_ask_caps_chained_tools_and_reports_what_was_dropped(monkeypatch):
    # 다섯 신호가 한 문장에 다 섞여도 무한정 돌리지 않는다(_MAX_CHAINED_TOOLS=3).
    # 상한을 넘긴 나머지는 조용히 버리지 않고 무엇을 못 돌렸는지 notice로 밝힌다.
    from app.models.schemas import CannibalizationResponse, RealCompetitionBundleResponse
    from app.routers.goals import LeaseVerdictResponse, NegotiationOut

    async def _llm(**_kwargs):
        return None

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _fake_lease_verdict(_request):
        return LeaseVerdictResponse(
            verdict="ok", verdict_label="적정", recommend_score=80.0,
            negotiation=NegotiationOut(),
        )

    async def _fake_real_competition(_request):
        return RealCompetitionBundleResponse()

    async def _fake_cannibalization(_request):
        return CannibalizationResponse(
            risk_score=30.0, risk_label="낮음", competitors=[], detail="", total_count=2,
        )

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)
    monkeypatch.setattr(agent, "run_lease_verdict", _fake_lease_verdict)
    monkeypatch.setattr(agent, "run_real_competition", _fake_real_competition)
    monkeypatch.setattr(agent, "run_cannibalization", _fake_cannibalization)
    # territory·property_market은 상한에 걸려 아예 호출되지 말아야 한다 —
    # 실제 라우터를 몽키패치하지 않고 두어, 호출되면(DB 없이) 예외로 드러나게 한다.

    query = (
        "신촌 카페 월세 300만원인데 2호점 내면 잠식되는지 배달권역도 겹치는지 "
        "경쟁구조도 실거래가도 같이 봐줘"
    )
    # 문장에 쉼표·'랑' 같은 비교 접속어가 없어 지점 하나로만 읽혀야 한다(decide로
    # 새지 않는지는 이 테스트의 전제조건이라 먼저 확인해 둔다).
    assert agent._rule_places(query) == agent._split_places(agent._rule_extract_address(query))

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query=query, include_answer=False)
        )
    )

    assert resp.tools == ["lease_verdict", "real_competition", "cannibalization"]
    assert len(resp.tools) == agent._MAX_CHAINED_TOOLS
    assert "territory" not in resp.tool_results
    assert "property_market" not in resp.tool_results
    # 못 돌린 두 도구의 이름이 notice에 그대로 남아야 한다 — 조용히 버리지 않는다.
    assert "권역 Before/After 분석" in resp.notice
    assert "실거래 비교" in resp.notice



def test_agent_ask_keeps_decide_exclusive_even_when_llm_prefers_another_tool(monkeypatch):
    # decide(비교)는 후보 지점을 여럿 받아야 뜻이 서는 도구다. 다른 도구는
    # 전부 지점 하나(points[0])만 본다 — LLM이 "월세도 있으니 lease_verdict가
    # 낫다"고 판단해도 두 지점 비교 자체를 대체할 수는 없으므로, 여러 도구를
    # 엮는 새 로직이라 해도 decide는 여전히 단독으로 돈다.
    from app.routers.decide import DecideResponse

    async def _llm(**_kwargs):
        return {
            "places": ["연남동", "신촌"], "business_type": "cafe",
            "tool": "lease_verdict",
        }

    def _dong(query):
        return SimpleNamespace(lat=37.5, lng=127.0, name=query)

    decide_captured = {}

    async def _fake_decide(request):
        decide_captured["request"] = request
        return DecideResponse(ranked=[], headline="", comparison_note="")

    async def _lease_verdict_should_not_run(_request):
        raise AssertionError("decide 비교 질의에서 lease_verdict가 함께 돌면 안 된다")

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)
    monkeypatch.setattr(agent, "run_decide", _fake_decide)
    monkeypatch.setattr(agent, "run_lease_verdict", _lease_verdict_should_not_run)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(
                query="연남동이랑 신촌 중에 카페 월세 300만원이면 어디가 나아?",
                include_answer=False,
            )
        )
    )

    assert resp.tool == "decide"
    assert resp.tools == ["decide"]
    assert "request" in decide_captured
    assert len(decide_captured["request"].candidates) == 2



def test_rule_tools_plain_site_question_falls_back_to_site_score_plus_trends():
    # 다른 신호가 하나도 없는 순수 입지 질문("이 자리 어때?")도 성장 신호를
    # 곁들여야 한다 — real_competition은 업종 코드가 없으면(_rule_tools는
    # 문장만 봐서 코드 체계를 모른다) 여기서는 못 판단하므로 agent_ask가
    # 따로 덧붙인다(_real_competition_supported).
    assert agent._rule_tools("망원동 카페 자리 어때", {}, 1) == ["site_score", "trends"]
    # 값 신호가 있으면 이 fallback을 타지 않는다 — 기존 라우팅이 그대로 이긴다.
    assert agent._rule_tools(
        "신촌 월세 300만원", {"monthly_rent_man": 300.0}, 1
    ) == ["lease_verdict"]


def test_real_competition_supported_gates_on_vertical_and_academy_subject():
    # 카페는 competitor_rules_cafe.json이 있어 지원된다.
    cafe = agent._Intent(address="신촌", business_type="cafe")
    assert agent._real_competition_supported(cafe) is True
    # 헬스장은 경쟁규칙 프로필이 없어 real_competition이 400을 준다 — 순수 입지
    # 질문마다 자동으로 붙이면 매번 "지원 안 함" 알림만 뜨므로 미리 걸러야 한다.
    fitness = agent._Intent(address="신촌", business_type="fitness")
    assert agent._real_competition_supported(fitness) is False
    # 학원은 vertical 자체는 지원되지만 과목(target_model) 없이 부르면
    # academy_cluster가 400을 준다 — 과목이 있을 때만 붙인다.
    academy_no_subject = agent._Intent(address="신촌", business_type="academy")
    assert agent._real_competition_supported(academy_no_subject) is False
    academy_with_subject = agent._Intent(
        address="신촌", business_type="academy", target_model="math"
    )
    assert agent._real_competition_supported(academy_with_subject) is True


def test_agent_ask_adds_trends_and_real_competition_depth_to_a_plain_site_question(
    monkeypatch,
):
    # 핵심 회귀 대상: "망원동 카페 자리 어때?"처럼 다른 신호가 전혀 없는 순수
    # 입지 질문도 성장 추이·경쟁 구조를 실제로 계산해 곁들여야 한다("분석 깊이
    # 확보"). 값이 화면에 실리려면 실제로 그 도구가 돌아 나온 값이어야 하므로
    # (GOALS.md 품질 기준), 각 도구가 진짜 계산한 값이 tool_results·서술 재료에
    # 그대로 실리는지까지 검증한다 — 문구가 맞는지가 아니라 값이 맞는지를 본다.
    from app.models.schemas import (
        CompetitionBucketOut,
        RealCompetitionBundleResponse,
        RealCompetitionResponse,
        StreetSignalOut,
        TrendSeriesOut,
        TrendsResponse,
    )

    async def _llm(**_kwargs):
        return None  # 규칙 폴백 경로로 검증한다.

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="망원동")

    trends_captured = {}
    competition_captured = {}

    async def _fake_trends(request):
        trends_captured["request"] = request
        return TrendsResponse(
            category_label="카페",
            series=TrendSeriesOut(
                category_label="카페", quarters=["2025Q3", "2025Q4"],
                category_counts=[40, 47], total_counts=[900, 910],
                category_growth_pct=17.5, total_growth_pct=1.1,
            ),
            street_signals=[
                StreetSignalOut(
                    name="망원동 큰길", label="성장", category_growth_pct=22.0,
                    detail="", data_scope="dong",
                ),
            ],
            data_scope="dong",
        )

    async def _fake_real_competition(request):
        competition_captured["request"] = request
        return RealCompetitionBundleResponse(
            competition=RealCompetitionResponse(
                vertical="cafe", total_weighted_score=71.4, total_intensity="높음",
                buckets=[
                    CompetitionBucketOut(
                        key="direct", label="직접 경쟁", count=12,
                        weighted_score=71.4, intensity="높음",
                    )
                ],
                summary_one_liner="직접 경쟁이 몰려 있는 자리입니다.",
            ),
        )

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)
    monkeypatch.setattr(agent, "run_trends", _fake_trends)
    monkeypatch.setattr(agent, "run_real_competition", _fake_real_competition)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="망원동 카페 자리 어때", include_answer=False)
        )
    )

    # 사용자는 트렌드·경쟁구조를 직접 물은 적이 없다 — 주 도구는 여전히
    # site_score이고, 곁들인 두 도구는 복수 필드(tools/tool_results)로 실린다.
    assert resp.tool == "site_score"
    assert resp.tool_result is None
    assert resp.tools == ["trends", "real_competition"]
    assert set(resp.tool_results.keys()) == {"trends", "real_competition"}
    # 좌표가 진짜로 실렸는지(지어낸 값이 아니라 이 지점 기준으로 계산됐는지).
    assert trends_captured["request"].lat == 37.5 and trends_captured["request"].lng == 127.0
    assert competition_captured["request"].lat == 37.5
    # 실제로 계산된 값 그대로 tool_results에 실려야 한다.
    assert resp.tool_results["trends"]["series"]["category_growth_pct"] == 17.5
    assert resp.tool_results["real_competition"]["competition"]["total_weighted_score"] == 71.4
    # 서술 재료(LLM/규칙 답변이 참조하는 사실 목록)에도 두 도구의 실제 값이
    # 그대로 접혀 들어가야 한다 — 계산해 놓고 답변에서 사라지면 "깊이 확보"가
    # 아니다.
    facts = agent._facts_for_answer(
        resp.site, place="망원동", business_label="카페", question=resp.query,
        tool=resp.tool, tool_result=resp.tool_result, tool_results=resp.tool_results,
    )
    assert "업종 점포 증감: +17.5%" in facts
    assert "경쟁 강도: 71.4점 (높음, 높을수록 경쟁 치열)" in facts


def test_agent_ask_fast_runs_site_score_only_and_returns_founder_card(monkeypatch):
    # 결정 카드를 바로 그리기 위한 빠른 요청 — 도구 루프와 곁들이 도구를 건너뛴다.
    # 도구를 하나라도 부르면 실패하도록 막아 두고, 카드가 실리는지까지 본다.
    async def _llm(**_kwargs):
        return None

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="망원동")

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    async def _no_tool(*_args, **_kwargs):
        raise AssertionError("fast 요청에서 도구가 돌았다")

    async def _no_loop(*_args, **_kwargs):
        raise AssertionError("fast 요청에서 도구 루프를 탔다")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)
    monkeypatch.setattr(agent, "run_trends", _no_tool)
    monkeypatch.setattr(agent, "run_real_competition", _no_tool)
    monkeypatch.setattr(agent, "run_agent_loop", _no_loop)
    monkeypatch.setattr(agent.settings, "agent_loop_enabled", True, raising=False)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="망원동 카페 자리 어때", include_answer=False, fast=True)
        )
    )

    assert resp.site is not None
    assert resp.tools == []
    assert resp.founder is not None
    assert resp.founder["signal"] in ("go", "conditional", "no")


def test_agent_ask_skips_real_competition_depth_for_unsupported_business_type(
    monkeypatch,
):
    # 헬스장처럼 경쟁규칙 프로필이 없는 업종은 순수 입지 질문에서 real_competition을
    # 아예 시도하지 않아야 한다 — 시도하면 매번 400을 맞고 "지원 안 함" 알림만
    # 뜬다(노이즈). trends는 업종과 무관하게 항상 계산할 수 있으므로 그대로 곁들인다.
    async def _llm(**_kwargs):
        return {"places": ["신촌"], "business_type": "fitness"}

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _fake_trends(_request):
        from app.models.schemas import TrendsResponse

        return TrendsResponse(category_label="헬스장", street_signals=[], data_scope="dong")

    async def _real_competition_should_not_run(_request):
        raise AssertionError("경쟁규칙이 없는 업종에서 real_competition을 부르면 안 된다")

    async def _site_score(_request):
        return _full_site(business_type="fitness", business_label="헬스장")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)
    monkeypatch.setattr(agent, "run_trends", _fake_trends)
    monkeypatch.setattr(agent, "run_real_competition", _real_competition_should_not_run)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="신촌 헬스장 자리 어때", include_answer=False)
        )
    )

    assert resp.tool == "site_score"
    assert resp.tools == ["trends"]
    assert "real_competition" not in resp.tool_results


def test_agent_ask_reports_explicit_trends_request_as_the_primary_tool(monkeypatch):
    # depth-only 취급은 "다른 신호가 전혀 없을 때"만 적용된다 — 사용자가 직접
    # "요즘 뜨는 동네야?"라고 물으면 trends가 곁들이가 아니라 진짜 주 도구다.
    from app.models.schemas import TrendsResponse

    async def _llm(**_kwargs):
        return None

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="신촌")

    async def _fake_trends(_request):
        return TrendsResponse(category_label="카페", street_signals=[], data_scope="dong")

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)
    monkeypatch.setattr(agent, "run_trends", _fake_trends)

    resp = asyncio.run(
        agent.agent_ask(
            agent.AgentAskRequest(query="신촌 카페 요즘 뜨는 동네야?", include_answer=False)
        )
    )

    assert resp.tool == "trends"
    assert resp.tool_result is not None



def test_analogs_tool_is_routed_for_similar_area_questions():
    """"비슷한 동네 어디야?"가 analogs 도구로 간다."""
    for q in ("연남동이랑 비슷한 동네 어디야?", "여기랑 유사 상권 알려줘", "이 자리랑 닮은 상권은?"):
        assert "analogs" in agent._rule_tools(q, {}, 1), q
    # 추이·시세 질문을 침범하지 않는다.
    assert "analogs" not in agent._rule_tools("연남동 요즘 뜨는 동네야?", {}, 1)


def test_analogs_facts_expose_structure_vs_outcome():
    """서술 재료에 '구조는 같은데 성과가 다르다'가 드러나야 한다."""
    facts = agent._tool_facts_from_dict("analogs", {
        "subject": {"trdar_nm": "홍대입구역 3번", "per_store_monthly_won": 49_000_000, "store_count": 30},
        "analogs": [
            {"trdar_nm": "공릉역 2번", "similarity": 93.5, "per_store_monthly_won": 24_500_000},
            {"trdar_nm": "성산중학교", "similarity": 92.6, "per_store_monthly_won": 88_700_000},
        ],
        "basis": "직장인 규모 · 점포 밀도 · 주말 비중 · 프랜차이즈 비율",
        "quarter": "20261",
    })
    assert "홍대입구역 3번" in facts and "4,900만원" in facts
    assert "공릉역 2번" in facts and "93.5" in facts
    assert "직장인 규모" in facts  # 무엇으로 쟀는지가 서술에 들어간다


def test_facts_for_answer_includes_previous_turns():
    """앞 대화가 사실 블록 맨 위에 들어가야 후속 질문을 후속으로 답할 수 있다.

    /ask는 history로 장소·업종을 이어받았지만 서술 단계에는 history가 아예
    없어서, 좌표는 이어지고 말은 안 이어졌다 — 후속 질문마다 같은 장소를
    처음부터 다시 소개했다.
    """
    site = _site()
    facts = agent._facts_for_answer(
        site,
        place="서대문구 신촌동",
        question="월세 300이면 계약해도 될까",
        history=[
            agent.AgentHistoryTurn(query="신촌 카페 어때", address="서대문구 신촌동"),
        ],
    )
    assert "앞 대화" in facts
    assert "신촌 카페 어때" in facts
    # 앞 대화 블록이 이번 질문보다 위에 와야 한다(모델이 후속임을 먼저 안다).
    assert facts.index("앞 대화") < facts.index("사용자 질문")


def test_facts_for_answer_without_history_is_unchanged():
    """history가 없으면 종전과 똑같아야 한다 — 기존 호출자를 깨지 않는다."""
    site = _site()
    assert "앞 대화" not in agent._facts_for_answer(site, place="서대문구 신촌동", question="어때")


def test_agent_answer_brief_asks_short_text_on_the_fast_model(monkeypatch):
    # 「자세히 보기」를 빨리 — brief면 짧은 구성과 작은 모델(fast)로 쓴다.
    captured = {}

    async def _fake_text(*, system, user, max_tokens, timeout_s, fast=False):
        captured.update(system=system, max_tokens=max_tokens, fast=fast)
        return "**한 줄 결론**\n짧은 답."

    monkeypatch.setattr(agent, "complete_text", _fake_text)
    resp = asyncio.run(
        agent.agent_answer(
            agent.AgentAnswerRequest(site=_full_site(business_type="cafe", business_label="카페"), brief=True)
        )
    )
    assert resp.answer_source == "llm"
    assert captured["fast"] is True
    # 2026-11-06: 고정 3칸(결론·왜·할 일)을 풀었다 — 질문이 묻는 것에 맞춰 짧게.
    assert "600자" in captured["system"]
    assert "**왜**" not in captured["system"]
    assert captured["max_tokens"] < 1800


def test_agent_ask_loop_false_skips_the_tool_loop(monkeypatch):
    async def _llm(**_kwargs):
        return None

    def _dong(_query):
        return SimpleNamespace(lat=37.5, lng=127.0, name="망원동")

    async def _site_score(_request):
        return _full_site(business_type="cafe", business_label="카페")

    async def _no_loop(*_args, **_kwargs):
        raise AssertionError("loop=False인데 도구 루프를 탔다")

    async def _ok_tool(_request):
        raise RuntimeError("도구는 돌아도 된다 — 실패해도 답은 나가야 한다")

    monkeypatch.setattr(agent, "complete_json", _llm)
    monkeypatch.setattr(agent, "resolve_unique_dong", _dong)
    monkeypatch.setattr(agent, "run_site_score", _site_score)
    monkeypatch.setattr(agent, "run_trends", _ok_tool)
    monkeypatch.setattr(agent, "run_real_competition", _ok_tool)
    monkeypatch.setattr(agent, "run_agent_loop", _no_loop)
    monkeypatch.setattr(agent.settings, "agent_loop_enabled", True, raising=False)

    resp = asyncio.run(
        agent.agent_ask(agent.AgentAskRequest(query="망원동 카페 자리 어때", include_answer=False, loop=False))
    )
    assert resp.site is not None
    assert resp.founder is not None


# ───── 2026-11-06: 계산해 놓고 상담 모델에 안 넘기던 데이터 ─────


def test_facts_carry_footfall_spend_and_foreign_share():
    """유동인구·지역 소비(market_context)와 외국인 비중이 상담 모델에 간다.

    소유자 지적: 유동인구·외국인 데이터를 넣어 뒀는데 답은 길 구조 얘기뿐이다.
    """
    from app.models.schemas import DemandOut, DongMarketContextOut, SeoulRelativeOut

    demand = DemandOut(
        adm_cd="1141058500",
        adm_name="신촌동",
        market_context=DongMarketContextOut(
            dong_name="신촌동",
            quarter="2026Q2",
            footfall_daily=SeoulRelativeOut(value=112000, top_pct=4),
            female_pct=SeoulRelativeOut(value=56, top_pct=20),
            age_pcts={"20대": SeoulRelativeOut(value=41), "30대": SeoulRelativeOut(value=18)},
            local_spend_monthly=SeoulRelativeOut(value=54_000_000_000, top_pct=3),
        ),
    )
    site = _full_site(business_type="cafe", business_label="카페").model_copy(
        update={"demand": demand, "foreign_share_pct": 6.2}
    )
    facts = agent._facts_for_answer(site, place="신촌역")

    assert "하루 유동인구 112,000명(서울 행정동 상위 4%)" in facts
    assert "주 연령대 20대 41%, 30대 18%" in facts
    assert "지역 카드 소비(월): 540.0억원(서울 행정동 상위 3%)" in facts
    assert "단기체류 외국인 비중: 6.2%" in facts


def test_area_insights_facts_summarise_time_slots():
    weekday = [10.0] * 24
    for h in (11, 12, 13):
        weekday[h] = 30.0
    facts = agent._tool_facts_from_dict(
        "area_insights",
        {"footfall": {"weekday_peak_hour": 12, "weekday_hourly": weekday, "weekend_hourly": [x / 2 for x in weekday]}},
    )
    assert "평일 생활인구가 가장 많은 시간대: 12시" in facts
    assert "점심(11~13시) 2.4배" in facts
    assert "주말 총량은 평일의 0.50배" in facts


def test_broad_region_words_are_not_a_place():
    """「서울에서 어느 동네가 좋아?」의 '서울'을 장소로 잡아 시청을 분석하던 것(§9-2)."""
    assert agent._is_broad_region("서울")
    assert agent._is_broad_region("서울에서")
    assert agent._is_broad_region("서울 전체")
    assert not agent._is_broad_region("서울 망원동")
    assert not agent._is_broad_region("강남역")
    assert not agent._has_place("서울")
    assert agent._has_place("망원동")


def test_continue_from_history_carries_business_when_place_answers_a_place_question():
    # "카페" → (어느 지역인지 되물음) → "신촌": 위치만 답해도 직전 업종을 잇는다.
    intent = agent._Intent(address="신촌", business_type="general", params={})
    history = [agent.AgentHistoryTurn(query="카페", address="", business_type="cafe")]
    merged = agent._continue_from_history(intent, history)
    assert merged.address == "신촌"
    assert merged.business_type == "cafe"


def test_continue_from_history_does_not_carry_business_after_an_analysed_place():
    # 직전 턴에 장소가 있었고 이번에 새 장소를 댔으면 종전대로 손대지 않는다.
    intent = agent._Intent(address="연남동", business_type="general", params={})
    history = [agent.AgentHistoryTurn(address="신촌", business_type="cafe")]
    assert agent._continue_from_history(intent, history).business_type == "general"


def test_business_only_query_returns_its_business_for_the_next_turn():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(agent.router)
    client = TestClient(app)
    res = client.post(
        "/api/agent/ask", json={"query": "카페", "include_answer": False, "fast": True, "talk": False}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["site"] is None and body["business_type"] == "cafe"
