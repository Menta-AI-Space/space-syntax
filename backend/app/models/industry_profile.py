"""Superset schema for canonical industry analysis profiles (Phase 0 unification).

Validates `backend/industry-profiles/**/*.json` against a single contract that
covers every field currently spread across ``site_score.ARCHETYPE_PROFILES``,
``real_competition.resolve_vertical`` / ``competitor_rules_*.json``, and
``sbiz_upjong.MCLS_ARCHETYPE``. Field names intentionally mirror the JSON
config keys (camelCase) rather than following Python snake_case convention —
this module validates external config, it is not a FastAPI response schema.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_SUPPORTED_USER_TYPES = ("entrepreneur", "franchise")


class SbizCodeMatch(BaseModel):
    """SBIZ official upjong codes that resolve to this profile."""

    model_config = ConfigDict(extra="forbid")

    lcls: list[str] = Field(default_factory=list)
    mcls: list[str] = Field(default_factory=list)
    scls: list[str] = Field(default_factory=list)


class MatchRules(BaseModel):
    """Consolidates BUSINESS_TYPE_PRESETS / QUICK_PRESETS / MCLS_ARCHETYPE into one place."""

    model_config = ConfigDict(extra="forbid")

    sbizCodes: SbizCodeMatch = Field(default_factory=SbizCodeMatch)
    businessTypePresets: list[str] = Field(default_factory=list)
    fallbackArchetype: str


class CatchmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str
    walkMinutes: list[int] | None = None
    driveMinutes: list[int] | None = None
    transitMinutes: list[int] | None = None
    deliveryMinutes: list[int] | None = None


class CompetitorGroups(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direct: list[str] = Field(default_factory=list)
    secondary: list[str] = Field(default_factory=list)
    indirect: list[str] = Field(default_factory=list)
    channel: list[str] = Field(default_factory=list)


class SiteFeatureWeights(BaseModel):
    """Mirrors site_score.ARCHETYPE_PROFILES[archetype]["weights"]."""

    model_config = ConfigDict(extra="forbid")

    integration: float = 0.0
    choice: float = 0.0
    mean_depth: float = 0.0
    connectivity: float = 0.0
    transit: float = 0.0
    demand: float = 0.0
    competition: float = 0.0


class DistanceBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max: float
    weight: float
    same_building: bool | None = None


class CompetitorBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    weight: float = 1.0
    mcls_codes: list[str] = Field(default_factory=list)
    scls_codes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class CompetitorRules(BaseModel):
    """Mirrors backend/data/competitor_rules_<vertical>.json bodies verbatim."""

    model_config = ConfigDict(extra="forbid")

    vertical: str
    distance_bands_m: list[DistanceBand]
    intensity_thresholds: list[float]
    buckets: list[CompetitorBucket]
    intent_map: dict[str, str] | None = None


class PublicSalesMapping(BaseModel):
    """Mirrors an entry of data/seoul_service_upjong_map.json for this profile."""

    model_config = ConfigDict(extra="forbid")

    code: str
    service_code: str
    service_name: str
    keywords: list[str] = Field(default_factory=list)


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catchment: CatchmentConfig
    timeSlots: list[str] = Field(default_factory=list)
    competitorGroups: CompetitorGroups | None = None
    siteFeatures: SiteFeatureWeights | None = None
    demandFeatures: dict[str, Any] | None = None
    competitionFeatures: dict[str, Any] | None = None
    cannibalizationFeatures: dict[str, Any] | None = None
    competitorRules: CompetitorRules | None = None
    publicSalesMappings: list[PublicSalesMapping] = Field(default_factory=list)


class UIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summaryKpis: list[dict[str, str]] = Field(default_factory=list)
    primaryTabs: list[str] = Field(default_factory=list)
    secondaryTabs: list[str] = Field(default_factory=list)
    mapLayers: list[str] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    reportSections: list[str] = Field(default_factory=list)


class IndustryAnalysisProfile(BaseModel):
    """Superset schema for a canonical industry analysis/UI profile."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    parentCategory: str
    version: str
    effectiveFrom: str
    supportedUserTypes: list[str] = Field(
        default_factory=lambda: list(_SUPPORTED_USER_TYPES)
    )
    matchRules: MatchRules | None = None
    analysis: AnalysisConfig
    ui: UIConfig
    userOverrides: dict[str, UIConfig] = Field(default_factory=dict)
