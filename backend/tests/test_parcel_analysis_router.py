"""`/api/parcel-analysis` 계약 — 서비스는 monkeypatch하고 응답 모양만 본다.

여기서 지키는 것은 **없는 것을 지어내지 않는다**다. 판정 자료가 없으면 404여야
하고, fixture나 빈 껍데기를 200으로 주면 화면에서 진짜와 구분되지 않는다.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.models.land_analysis import PeerGroup, Subject
from app.routers import undervalued_parcels as router_mod
from app.services.land_verdict import VerdictInput, build_structured_analysis

client = TestClient(app)

PNU = "1156011000100120003"


def _analysis():
    return build_structured_analysis(
        subject=Subject(pnu=PNU, base_year=2026, address="여의도동 12-3"),
        peer_group=PeerGroup(description="같은 동 412필지", size=412, level="dong_zone"),
        inp=VerdictInput(
            peer_count=412,
            peer_median_price_per_sqm=12_000_000,
            actual_price_per_sqm=8_300_000,
            gap_score=62.8,
        ),
        model_version="no-estimator",
    )


def test_unknown_parcel_is_404_not_an_invented_verdict(monkeypatch):
    monkeypatch.setattr(router_mod, "analyze_parcel", lambda pnu, base_year=2026: None)
    res = client.get("/api/parcel-analysis", params={"pnu": PNU})
    assert res.status_code == 404


def test_known_parcel_returns_the_whole_contract(monkeypatch):
    monkeypatch.setattr(router_mod, "analyze_parcel", lambda pnu, base_year=2026: _analysis())
    res = client.get("/api/parcel-analysis", params={"pnu": PNU})
    assert res.status_code == 200
    body = res.json()
    # 화면이 이 응답만으로 그릴 수 있어야 한다.
    assert body["subject"]["address"] == "여의도동 12-3"
    assert body["verdict"]["headline"]
    assert body["peer_group"]["description"]
    assert body["official_price"]["per_sqm"] == 8_300_000
    assert body["peer_position"]["gap_score"] == 62.8
    assert body["caveats"]
    # 정적 서술이 기본값으로 함께 온다 — LLM 없이도 설명이 나온다.
    assert body["narrative"]["source"] == "static"
    assert body["narrative"]["paragraphs"]


def test_a_malformed_pnu_is_rejected_before_touching_the_db():
    res = client.get("/api/parcel-analysis", params={"pnu": "123"})
    assert res.status_code == 422


def test_llm_narrative_is_opt_in_and_falls_back(monkeypatch):
    monkeypatch.setattr(router_mod, "analyze_parcel", lambda pnu, base_year=2026: _analysis())

    async def _fail(analysis, **kwargs):
        # LLM이 없는 환경을 흉내낸다 — 정적 서술이 그대로 남는다.
        return analysis.narrative

    monkeypatch.setattr(router_mod, "build_llm_narrative", _fail)
    res = client.get("/api/parcel-analysis", params={"pnu": PNU, "narrative": "llm"})
    assert res.status_code == 200
    assert res.json()["narrative"]["source"] == "static"
