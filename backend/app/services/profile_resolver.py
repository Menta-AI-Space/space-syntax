"""Consolidates industry-profile resolution.

Phase 0 unifies three previously-independent lookup paths — the SBIZ
mcls/scls code -> archetype table (sbiz_upjong.MCLS_ARCHETYPE), the quick
business-type presets (sbiz_upjong.BUSINESS_TYPE_PRESETS), and the vertical
resolver used for competitor rules (real_competition.resolve_vertical) —
into a single ``ProfileResolver`` that returns one canonical
industry-profile id (matching backend/industry-profiles/**/*.json ``id``
fields) for a given upjong selection.

Resolution order (most to least specific):
  1. Exact SBIZ small-category (scls) code match against a profile's
     matchRules.sbizCodes.scls.
  2. Exact SBIZ middle-category (mcls) code match against
     matchRules.sbizCodes.mcls.
  3. Quick business-type preset id (e.g. "cafe", "beauty") that appears in
     a profile's matchRules.businessTypePresets.
  4. Archetype fallback: the coarse archetype (food_drink / daily_retail /
     medical / education / beauty_service / general) resolves to whichever
     profile declares it as matchRules.fallbackArchetype, using a fixed
     priority order so that ambiguous archetypes (food_drink is shared by
     both "restaurant" and "cafe") resolve the same way
     real_competition.resolve_vertical does today: to the broader vertical
     ("restaurant") unless a more specific code/preset already matched in
     an earlier step.

Returns ``None`` when nothing matches — e.g. archetype "general" has no
canonical MVP profile, matching the current system's behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.industry_profiles import load_all_profiles

# Deterministic fallback order — first match wins. This mirrors
# real_competition.resolve_vertical's implicit preference for the broader
# "restaurant" vertical over "cafe" when only the coarse food_drink
# archetype is known (exact I212/I21201 codes are already caught in the
# scls/mcls match steps before fallback is ever consulted).
_ARCHETYPE_FALLBACK_PRIORITY = (
    "restaurant",
    "cafe",
    "beauty",
    "clinic",
    "academy",
    "convenience",
    # 공유 아키타입의 대표 프로필: auto_service→gas_station, fitness_leisure→fitness.
    # life_service는 의도적으로 제외 — fallbackArchetype이 "general"이라 목록에
    # 넣으면 프로필 없는 일반 업종 전체의 가중치가 바뀐다.
    "gas_station",
    "fitness",
    "lodging",
    "professional_office",
)


@dataclass
class ProfileResolver:
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "ProfileResolver":
        return cls(profiles=load_all_profiles())

    def _match_rules(self, profile_id: str) -> dict[str, Any]:
        return self.profiles.get(profile_id, {}).get("matchRules") or {}

    def _profiles_in_match_order(self) -> list[str]:
        """우선순위 목록 먼저, 나머지는 id 정렬 — 결정적 mcls 매칭 순서."""
        ordered = [p for p in _ARCHETYPE_FALLBACK_PRIORITY if p in self.profiles]
        rest = sorted(p for p in self.profiles if p not in _ARCHETYPE_FALLBACK_PRIORITY)
        return ordered + rest

    def resolve_by_archetype(self, archetype: str | None) -> str | None:
        """Resolve using only the coarse archetype fallback (step 4)."""
        if not archetype:
            return None
        for profile_id in _ARCHETYPE_FALLBACK_PRIORITY:
            if profile_id not in self.profiles:
                continue
            if self._match_rules(profile_id).get("fallbackArchetype") == archetype:
                return profile_id
        return None

    def resolve(
        self,
        *,
        inds_lcls_cd: str | None = None,
        inds_mcls_cd: str | None = None,
        inds_scls_cd: str | None = None,
        business_type: str | None = None,
        archetype: str | None = None,
    ) -> str | None:
        """Resolve request fields to a single canonical industry-profile id."""
        if inds_scls_cd:
            for profile_id, profile in self.profiles.items():
                codes = (profile.get("matchRules") or {}).get("sbizCodes") or {}
                if inds_scls_cd in (codes.get("scls") or []):
                    return profile_id

        if inds_mcls_cd:
            for profile_id in self._profiles_in_match_order():
                codes = self._match_rules(profile_id).get("sbizCodes") or {}
                if inds_mcls_cd in (codes.get("mcls") or []):
                    return profile_id

        if business_type and business_type in self.profiles:
            presets = self._match_rules(business_type).get("businessTypePresets") or []
            if business_type in presets:
                return business_type

        return self.resolve_by_archetype(archetype)
