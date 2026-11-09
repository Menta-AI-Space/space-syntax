"""Phase 0 unification tests: profile schema validation, ProfileResolver id
resolution, and presence of the previously-missing beauty/convenience
competitor rules.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.industry_profile import IndustryAnalysisProfile
from app.services.industry_profiles import load_all_profiles
from app.services.profile_resolver import ProfileResolver
from app.services.site_score import ARCHETYPE_PROFILES

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

_MINIMAL_VALID_PROFILE = {
    "id": "test_vertical",
    "name": "Test",
    "parentCategory": "test",
    "version": "1.0.0",
    "effectiveFrom": "2026-01-01",
    "analysis": {"catchment": {"primary": "walk"}},
    "ui": {},
}


class TestProfileSchemaValidation:
    def test_minimal_valid_profile_passes(self):
        IndustryAnalysisProfile.model_validate(_MINIMAL_VALID_PROFILE)

    def test_missing_required_field_fails_fast(self):
        bad = {k: v for k, v in _MINIMAL_VALID_PROFILE.items() if k != "parentCategory"}
        with pytest.raises(ValidationError):
            IndustryAnalysisProfile.model_validate(bad)

    def test_unknown_field_is_rejected(self):
        bad = {**_MINIMAL_VALID_PROFILE, "unexpectedField": "nope"}
        with pytest.raises(ValidationError):
            IndustryAnalysisProfile.model_validate(bad)

    def test_all_profiles_validate(self):
        profiles = load_all_profiles()
        # 6 MVP verticals must always exist; expansion profiles (2026-07-15)
        # are additive — assert the full current set so accidental drops fail.
        assert {
            "restaurant", "cafe", "beauty", "clinic", "academy", "convenience",
        } <= set(profiles)
        assert {
            "gas_station", "ev_charging", "fitness", "leisure",
            "retail_specialty", "life_service", "pharmacy",
            "lodging", "professional_office",
        } <= set(profiles)
        for profile in profiles.values():
            IndustryAnalysisProfile.model_validate(profile)


class TestSiteFeatureMigrationConsistency:
    """Guards against transcription drift between the migrated JSON weights
    and the still-present hardcoded ARCHETYPE_PROFILES table they were
    copied from (Phase 0 requires byte-identical scoring output)."""

    @pytest.mark.parametrize(
        "profile_id,archetype",
        [
            ("restaurant", "food_drink"),
            ("cafe", "food_drink"),
            ("clinic", "medical"),
            ("academy", "education"),
            ("beauty", "beauty_service"),
            ("convenience", "daily_retail"),
        ],
    )
    def test_site_features_match_archetype_profiles(self, profile_id, archetype):
        profiles = load_all_profiles()
        site_features = profiles[profile_id]["analysis"]["siteFeatures"]
        assert site_features == ARCHETYPE_PROFILES[archetype]["weights"]


class TestProfileResolver:
    def test_resolve_by_scls_exact_match(self):
        resolver = ProfileResolver.load()
        assert resolver.resolve(inds_scls_cd="I21201") == "cafe"
        assert resolver.resolve(inds_scls_cd="P10501") == "academy"
        assert resolver.resolve(inds_scls_cd="G20405") == "convenience"

    def test_resolve_by_mcls_exact_match(self):
        resolver = ProfileResolver.load()
        assert resolver.resolve(inds_mcls_cd="I212") == "cafe"
        assert resolver.resolve(inds_mcls_cd="Q102") == "clinic"
        assert resolver.resolve(inds_mcls_cd="S207") == "beauty"
        assert resolver.resolve(inds_mcls_cd="I201") == "restaurant"

    def test_resolve_by_business_type_preset(self):
        resolver = ProfileResolver.load()
        assert resolver.resolve(business_type="beauty") == "beauty"
        assert resolver.resolve(business_type="convenience") == "convenience"

    def test_resolve_by_archetype_fallback_prefers_restaurant_over_cafe(self):
        resolver = ProfileResolver.load()
        assert resolver.resolve(archetype="food_drink") == "restaurant"
        assert resolver.resolve_by_archetype("food_drink") == "restaurant"

    def test_resolve_by_archetype_fallback_unambiguous_verticals(self):
        resolver = ProfileResolver.load()
        assert resolver.resolve(archetype="medical") == "clinic"
        assert resolver.resolve(archetype="education") == "academy"
        assert resolver.resolve(archetype="beauty_service") == "beauty"
        assert resolver.resolve(archetype="daily_retail") == "convenience"

    def test_resolve_general_archetype_has_no_canonical_profile(self):
        resolver = ProfileResolver.load()
        assert resolver.resolve(archetype="general") is None

    def test_resolve_returns_none_when_nothing_matches(self):
        resolver = ProfileResolver.load()
        assert resolver.resolve() is None
        assert resolver.resolve(inds_mcls_cd="ZZZ999") is None


class TestBeautyConvenienceCompetitorRules:
    """Phase 0 explicitly adds the previously-missing beauty and
    convenience competitor rules (backend/data/competitor_rules_*.json),
    matching the structure of the 5 pre-existing files."""

    @pytest.mark.parametrize("vertical", ["beauty", "convenience"])
    def test_competitor_rules_file_exists_and_has_expected_shape(self, vertical):
        path = DATA_DIR / f"competitor_rules_{vertical}.json"
        assert path.is_file(), f"missing {path}"
        rules = json.loads(path.read_text(encoding="utf-8"))
        assert rules["vertical"] == vertical
        assert rules["distance_bands_m"]
        assert len(rules["intensity_thresholds"]) == 3
        assert rules["buckets"]
        assert any(b["key"] == "direct" for b in rules["buckets"])

    @pytest.mark.parametrize("profile_id", ["beauty", "convenience"])
    def test_canonical_profile_embeds_competitor_rules(self, profile_id):
        profiles = load_all_profiles()
        competitor_rules = profiles[profile_id]["analysis"]["competitorRules"]
        assert competitor_rules["vertical"] == profile_id
        assert competitor_rules["buckets"]


class TestTaxonomyConsistency:
    """프로필 스토어 ↔ 엔진(가중치/경쟁규칙) 정합성 가드 — 드리프트 방지."""

    def test_every_archetype_reaches_a_profile(self):
        """'general' 외 모든 fallbackArchetype은 어떤 프로필로든 해석돼야 한다.

        해석 실패 = 그 업종군 전체가 조용히 generic 가중치로 채점된다
        (2026-07 신규 4개 아키타입이 실제로 이렇게 누락됐었다)."""
        resolver = ProfileResolver.load()
        archetypes = {
            (p.get("matchRules") or {}).get("fallbackArchetype")
            for p in resolver.profiles.values()
        }
        archetypes.discard(None)
        archetypes.discard("general")  # 의도적 미해석 (프로필 없는 일반 업종)
        unresolved = [a for a in archetypes if resolver.resolve_by_archetype(a) is None]
        assert unresolved == []

    def test_site_feature_weights_sum_to_one(self):
        resolver = ProfileResolver.load()
        for pid, profile in resolver.profiles.items():
            weights = (profile.get("analysis") or {}).get("siteFeatures")
            if not weights:
                continue
            assert abs(sum(weights.values()) - 1.0) < 0.011, pid

    def test_resolved_verticals_have_rules_files(self):
        """resolve_vertical이 반환할 수 있는 프로필 id는 규칙 파일이 있어야 한다."""
        from app.services.real_competition import _available_verticals

        resolver = ProfileResolver.load()
        for archetype in ("food_drink", "beauty_service", "daily_retail", "medical", "education"):
            pid = resolver.resolve_by_archetype(archetype)
            assert pid is not None
            assert pid in _available_verticals(), (archetype, pid)
