"""Adaptive industry analysis/UI profiles."""

from fastapi.testclient import TestClient

from app.main import app
from app.services.industry_profiles import get_adaptive_profile, list_profile_summaries
from app.services.sbiz_upjong import QUICK_PRESETS


def test_cafe_entrepreneur_profile_has_cafe_specific_ui():
    profile = get_adaptive_profile("cafe", "entrepreneur")

    assert profile["industryId"] == "cafe"
    assert profile["userType"] == "entrepreneur"
    assert profile["analysis"]["catchment"]["primary"] == "walk"
    assert "takeout_demand" in [item["key"] for item in profile["ui"]["summaryKpis"]]
    assert "medical_gap" not in [item["key"] for item in profile["ui"]["summaryKpis"]]
    assert "takeout_demand" in profile["ui"]["mapLayers"]


def test_clinic_profile_removes_food_metrics_and_uses_transit_catchment():
    profile = get_adaptive_profile("clinic", "entrepreneur")
    keys = [item["key"] for item in profile["ui"]["summaryKpis"]]

    assert profile["analysis"]["catchment"]["primary"] == "transit"
    assert "medical_gap" in keys
    assert "lunch_demand" not in keys
    assert "delivery_coverage" not in profile["ui"]["mapLayers"]


def test_franchise_profile_prioritizes_network_impact():
    profile = get_adaptive_profile("restaurant", "franchise")

    assert profile["ui"]["primaryTabs"][0] == "network"
    assert profile["ui"]["summaryKpis"][0]["key"] == "cannibalization_risk"
    assert "existing_store_overlap" in profile["ui"]["mapLayers"]


def test_profile_catalog_contains_all_mvp_industries_and_user_types():
    summaries = list_profile_summaries()

    # MVP 6종은 항상 존재해야 하고, 확장 프로파일(2026-07-15)은 추가만 허용
    assert {item["id"] for item in summaries} >= {
        "restaurant", "cafe", "beauty", "clinic", "academy", "convenience"
    }
    assert all(item["supportedUserTypes"] == ["entrepreneur", "franchise"] for item in summaries)


def test_quick_picker_exposes_every_mvp_industry():
    assert {item["id"] for item in QUICK_PRESETS} >= {
        "restaurant", "cafe", "beauty", "clinic", "academy", "convenience"
    }


def test_profile_api_returns_configuration_without_frontend_hardcoding():
    client = TestClient(app)
    response = client.get("/api/industry-profiles/cafe", params={"user_type": "entrepreneur"})

    assert response.status_code == 200
    body = response.json()
    assert body["industryId"] == "cafe"
    assert body["ui"]["reportSections"]
    assert body["version"]


def test_profile_api_rejects_unknown_industry():
    client = TestClient(app)
    response = client.get("/api/industry-profiles/unknown", params={"user_type": "entrepreneur"})

    assert response.status_code == 404
