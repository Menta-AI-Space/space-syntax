"""Load versioned industry analysis and UI profiles from JSON configuration."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models.industry_profile import IndustryAnalysisProfile

PROFILE_ROOT = Path(__file__).resolve().parents[2] / "industry-profiles"
_SUPPORTED_USER_TYPES = ("entrepreneur", "franchise")


def _load_profiles() -> dict[str, dict[str, Any]]:
    """Parse + validate every profile JSON against the canonical superset schema.

    Validation is fail-fast: a malformed profile raises immediately on load.
    The *raw* (already-valid) dict is what's cached/returned — not the
    validated model's dump — so existing consumers (get_adaptive_profile,
    list_profile_summaries, the API router, tests) see byte-for-byte the
    same shape they always have.
    """
    profiles: dict[str, dict[str, Any]] = {}
    for path in sorted(PROFILE_ROOT.rglob("*.json")):
        with path.open(encoding="utf-8") as handle:
            profile = json.load(handle)
        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not profile_id:
            raise ValueError(f"Industry profile without id: {path}")
        if profile_id in profiles:
            raise ValueError(f"Duplicate industry profile id: {profile_id}")
        try:
            IndustryAnalysisProfile.model_validate(profile)
        except ValidationError as exc:
            raise ValueError(f"Invalid industry profile {path}: {exc}") from exc
        profiles[profile_id] = profile
    return profiles


def load_all_profiles() -> dict[str, dict[str, Any]]:
    """Public accessor for the raw, validated profile dicts (e.g. for ProfileResolver)."""
    return _load_profiles()


def list_profile_summaries() -> list[dict[str, Any]]:
    profiles = _load_profiles()
    return [
        {
            "id": profile["id"],
            "name": profile["name"],
            "parentCategory": profile["parentCategory"],
            "supportedUserTypes": profile.get("supportedUserTypes", list(_SUPPORTED_USER_TYPES)),
            "version": profile["version"],
        }
        for profile in profiles.values()
    ]


def get_adaptive_profile(industry_id: str, user_type: str = "entrepreneur") -> dict[str, Any]:
    profiles = _load_profiles()
    if industry_id not in profiles:
        raise KeyError(industry_id)
    if user_type not in _SUPPORTED_USER_TYPES:
        raise ValueError(f"Unsupported user type: {user_type}")

    raw = deepcopy(profiles[industry_id])
    ui = raw.pop("ui")
    overrides = raw.pop("userOverrides", {})
    ui.update(deepcopy(overrides.get(user_type, {})))
    return {
        "industryId": raw.pop("id"),
        "userType": user_type,
        **raw,
        "ui": ui,
    }
