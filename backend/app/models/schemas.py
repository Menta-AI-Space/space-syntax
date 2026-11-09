from typing import Literal, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.isochrone import allowed_competition_minutes


class AnalyzeRequest(BaseModel):
    address: str | None = Field(None, min_length=1, description="Korean street address or place name")
    lat: float | None = None
    lng: float | None = None
    radius_m: int = Field(..., description="Analysis radius in meters (500 or 1000)")
    mode: str = Field("walk", description="Analysis mode: walk or drive")

    @field_validator("radius_m")
    @classmethod
    def validate_radius(cls, v: int) -> int:
        if v not in (500, 1000):
            raise ValueError("radius_m must be 500 or 1000")
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("walk", "drive"):
            raise ValueError("mode must be walk or drive")
        return v

    @model_validator(mode="after")
    def validate_location(self) -> "AnalyzeRequest":
        has_coords = self.lat is not None and self.lng is not None
        has_address = bool(self.address and self.address.strip())
        if has_coords or has_address:
            return self
        raise ValueError("address or lat/lng is required")


class CenterPoint(BaseModel):
    lat: float
    lng: float


class AnalyzeResponse(BaseModel):
    center: CenterPoint
    radius_m: int
    segment_count: int
    warnings: list[str] = Field(default_factory=list)
    segments: dict
    engines: dict[str, dict] = Field(default_factory=dict)


class SiteScoreRequest(BaseModel):
    lat: float | None = None
    lng: float | None = None
    address: str | None = Field(None, description="Optional address instead of lat/lng")
    radius_m: int = Field(500, description="Analysis radius in meters (500 or 1000)")
    inds_lcls_cd: str | None = None
    inds_mcls_cd: str | None = None
    inds_scls_cd: str | None = None
    business_type: str = Field(
        "general",
        description="Deprecated preset slug: cafe|convenience|restaurant|clinic|academy|general",
    )
    brand: str | None = Field(
        None,
        max_length=40,
        description="프랜차이즈 브랜드명 (프랜차이즈 KPI 계산용, 예: 메가커피)",
    )
    priorities: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="사용자가 고른 중요 조건 키 (최대 3개, personal_fit.PRIORITIES)",
    )
    # 우선순위별 강도 배율(1.0~3.0). 없으면 종전 기본 부스트(1.8)를 쓴다 —
    # GF식 "your weight" 슬라이더의 입력이다.
    priority_boosts: dict[str, float] = Field(default_factory=dict)
    include_ai_explanation: bool = Field(
        default=True,
        description="Generate the slower LLM explanation; false uses the immediate rule explanation",
    )

    @field_validator("radius_m")
    @classmethod
    def validate_radius(cls, v: int) -> int:
        if v not in (500, 1000):
            raise ValueError("radius_m must be 500 or 1000")
        return v

    @model_validator(mode="after")
    def validate_upjong(self) -> "SiteScoreRequest":
        has_code = bool(self.inds_mcls_cd or self.inds_scls_cd)
        has_preset = self.business_type not in ("general", "custom", "")
        if has_code or has_preset or self.business_type == "general":
            return self
        raise ValueError(
            "inds_mcls_cd or inds_scls_cd is required (or use a business_type preset)"
        )


class UpjongPresetOut(BaseModel):
    id: str
    label: str
    inds_lcls_cd: str
    inds_mcls_cd: str
    inds_scls_cd: str | None = None


class UpjongSearchResultOut(BaseModel):
    type: str
    inds_lcls_cd: str
    inds_mcls_cd: str
    inds_scls_cd: str | None = None
    label: str
    breadcrumb: str
    matched_alias: str | None = None


class UpjongTreeResponse(BaseModel):
    large: list[dict]
    presets: list[UpjongPresetOut]


class ClinicSubjectOut(BaseModel):
    subject_code: str
    subject_name: str
    institution_count: int = 0


class ClinicSubjectsResponse(BaseModel):
    subjects: list[ClinicSubjectOut] = []
    warnings: list[str] = []


class SubScoreOut(BaseModel):
    key: str
    label: str
    score: float | None = None
    detail: str
    tooltip: str = ""
    summary_label: str = ""
    display: str = ""
    rank_top_pct: float | None = None


class NearestSegmentOut(BaseModel):
    osm_id: int
    highway: str
    distance_m: float
    integration_top_pct: float
    choice_top_pct: float
    mean_depth_top_pct: float
    connectivity_top_pct: float
    connectivity_degree: int


class TransitOut(BaseModel):
    station_name: str
    distance_m: float


class SeoulRelativeOut(BaseModel):
    """값 하나와, 그 값이 서울 425개 행정동 중 어디쯤인지.

    절대수만 주면 좋은지 나쁜지 알 수 없다 — '유동인구 11만명'보다 '상위 8%'가
    판단에 쓰인다. top_pct는 낮을수록 상위다.
    """

    value: float
    top_pct: int | None = None
    seoul_median: float | None = None
    vs_median_pct: float | None = None


class DongMarketContextOut(BaseModel):
    """유동인구 구성·지역 소비를 서울 동 단위 분포에서의 위치로."""

    dong_name: str
    quarter: str = ""
    footfall_daily: SeoulRelativeOut | None = None
    female_pct: SeoulRelativeOut | None = None
    #: 연령대(10대·20대…) → 유동인구 중 비중. 성별×연령 교차는 공개 데이터에
    #: 없어 각각 따로 낸다(20대 여성 비율은 만들 수 없다).
    age_pcts: dict[str, SeoulRelativeOut] = Field(default_factory=dict)
    #: 그 지역에서 일어나는 카드 소비 규모(월). 주민 구매력과는 다르다.
    local_spend_monthly: SeoulRelativeOut | None = None


class DemandOut(BaseModel):
    adm_cd: str
    adm_name: str
    population: int | None = None
    households: int | None = None
    companies: int | None = None
    workers: int | None = None
    avg_age: float | None = None
    population_density: float | None = None
    age_bands: list["AgeBandOut"] = []
    gender: "GenderOut | None" = None
    housing: "HousingMixOut | None" = None
    industries: list["IndustryOut"] = []
    seoul_rank: "SeoulRankOut | None" = None
    apartment_market: "ApartmentMarketOut | None" = None
    market_context: DongMarketContextOut | None = None
    demographic_fit: float | None = None
    demographic_fit_label: str = ""
    data_scope: str = ""
    radius_m: int | None = None
    dong_population: int | None = None
    dong_workers: int | None = None
    dong_households: int | None = None


class AgeBandOut(BaseModel):
    band: str
    count: int
    pct: float


class GenderOut(BaseModel):
    male_pct: float
    female_pct: float
    male_count: int | None = None
    female_count: int | None = None


class HousingMixOut(BaseModel):
    apartment_pct: float
    officetel_pct: float
    row_house_pct: float
    detached_pct: float
    apartment_count: int | None = None
    officetel_count: int | None = None


class IndustryOut(BaseModel):
    code: str
    name: str
    companies: int
    workers: int


class SeoulRankOut(BaseModel):
    population_top_pct: int | None = None
    workers_top_pct: int | None = None
    youth_top_pct: int | None = None


class ApartmentMarketOut(BaseModel):
    median_price_per_sqm: float | None = None
    sample_count: int = 0
    scope: str = ""
    vs_seoul_pct: int | None = None
    seoul_median_per_sqm: float | None = None


class CompetitionOut(BaseModel):
    same_category_count: int
    total_store_count: int
    density_per_km2: float | None = None
    match_method: str = "mcls"
    intensity_label: str = ""


class ExplanationOut(BaseModel):
    text: str
    source: str = Field(..., description="llm or rule")


class IndustryKpiOut(BaseModel):
    key: str
    label: str
    score: float | None = None
    display: str = "—"
    detail: str = ""


class SalesBenchmarkOut(BaseModel):
    trdar_cd: str
    trdar_nm: str
    trdar_se_nm: str = ""
    inside_area: bool = True
    distance_m: float = 0.0
    quarter: str
    monthly_sales_won: int
    store_count: int | None = None
    franchise_count: int | None = None
    per_store_monthly_won: int | None = None
    weekend_share_pct: float | None = None
    citywide_median_per_store_won: int | None = None
    citywide_trdar_count: int = 0
    #: 어느 공간 단위로 계산했는지 — "trdar"(골목·발달상권 등 지정 상권 안) 또는
    #: "dong"(상권 밖이라 행정동 평균으로 메움). 정밀도가 다르므로 화면에서
    #: 구분해 보여 줘야 한다. trdar_se_nm에도 "행정동"이 들어가지만 그건 사람이
    #: 읽는 라벨이고, 이 필드는 분기용이다.
    scope: str = "trdar"


class PriorityNoteOut(BaseModel):
    key: str
    label: str
    applied: bool
    note: str = ""


class PersonalFitOut(BaseModel):
    """업종 적합도(기본)와 사용자 우선순위 반영 맞춤 적합도 이중 점수."""

    base_score: float
    personal_score: float
    priorities: list[PriorityNoteOut] = []
    strengths: list[str] = []
    cautions: list[str] = []
    weights_used: dict[str, float] = {}


class PersonalFitRequest(BaseModel):
    base_score: float = Field(ge=0, le=100)
    subscores: list[SubScoreOut]
    base_weights: dict[str, float]
    priorities: list[str] = Field(default_factory=list, max_length=3)
    priority_boosts: dict[str, float] = Field(default_factory=dict)
    extra_factor_scores: dict[str, float] = Field(default_factory=dict)


class SiteScoreResponse(BaseModel):
    lat: float
    lng: float
    radius_m: int
    business_type: str
    business_label: str
    inds_lcls_cd: str | None = None
    inds_mcls_cd: str | None = None
    inds_scls_cd: str | None = None
    upjong_breadcrumb: str = ""
    total_score: float
    grade: str
    verdict_one_liner: str = ""
    segment: NearestSegmentOut
    subscores: list[SubScoreOut]
    transit: TransitOut | None = None
    demand: DemandOut | None = None
    competition: CompetitionOut | None = None
    explanation: ExplanationOut
    industry_profile_id: str | None = None
    industry_kpis: list[IndustryKpiOut] = []
    sales_benchmark: SalesBenchmarkOut | None = None
    personal_fit: PersonalFitOut | None = None
    personalization_weights: dict[str, float] = {}
    personalization_factors: dict[str, float] = {}
    #: 반경 생활인구 중 단기체류 외국인 비중(%). 2026-11-06: 계산해서 맞춤 점수에만
    #: 쓰고 버리던 값 — AI 상담이 외국인 수요를 말할 수 있게 응답에 싣는다.
    foreign_share_pct: float | None = None
    warnings: list[str] = []


# --- Dashboard (isochrone, cannibalization, trends, chat) ---


class DashboardPointRequest(BaseModel):
    lat: float | None = None
    lng: float | None = None
    address: str | None = None
    radius_m: int = Field(default=500, ge=100, le=2000)
    inds_lcls_cd: str | None = None
    inds_mcls_cd: str | None = None
    inds_scls_cd: str | None = None
    business_type: str = Field(
        default="general",
        description="Deprecated preset slug for legacy clients",
    )
    brand: str | None = Field(
        None,
        max_length=40,
        description="프랜차이즈 브랜드명 — 잠식 분석에서 자기 브랜드 점포 분리용 (예: 메가커피)",
    )
    mode: str = Field(default="walk", description="walk | drive | bike | moto")
    minutes: list[int] = Field(default=[5, 10, 15])
    competitor_minutes: int = Field(
        default=5,
        ge=1,
        le=30,
        description="Isochrone band for same-category competitor list",
    )
    include_territory: bool = Field(
        default=True,
        description="Include the expensive delivery-territory analysis in the bundle",
    )

    @field_validator("minutes")
    @classmethod
    def validate_minutes(cls, v: list[int]) -> list[int]:
        cleaned = sorted({m for m in v if 1 <= m <= 30})
        return cleaned or [5, 10, 15]

    @model_validator(mode="after")
    def validate_competition_band(self) -> "DashboardPointRequest":
        allowed = set(allowed_competition_minutes(self.mode))
        if self.competitor_minutes not in allowed:
            opts = ", ".join(str(m) for m in sorted(allowed))
            mode_label = self.mode
            raise ValueError(
                f"{mode_label} 모드에서는 {opts}분만 경쟁 권역으로 선택할 수 있습니다."
            )
        return self


class IsochroneBandOut(BaseModel):
    minutes: int
    max_distance_m: float
    polygon: dict
    area_m2: float
    store_count: int
    same_category_count: int
    population_estimate: int | None = None


class IsochroneResponse(BaseModel):
    lat: float
    lng: float
    mode: str
    bands: list[IsochroneBandOut]
    start_connected: bool
    warnings: list[str] = []


class CompetitorOut(BaseModel):
    name: str
    category: str
    distance_m: float
    address: str
    lat: float | None = None
    lng: float | None = None
    kakao_place_id: str | None = None
    place_url: str | None = None
    photo_url: str | None = None
    rating: float | None = None
    review_count: int | None = None
    tagline: str | None = None
    phone: str | None = None
    kakao_category: str | None = None


class CannibalizationResponse(BaseModel):
    risk_score: float
    risk_label: str
    competitors: list[CompetitorOut]
    overlap_pct: float | None = None
    overlap_with: str | None = None
    overlap_recommended_max_pct: float = 25.0
    detail: str
    mode: str = "walk"
    minutes: int = 5
    total_count: int = 0
    reach_polygon: dict | None = None
    self_total_count: int = 0
    self_competitors: list[CompetitorOut] = Field(default_factory=list)
    self_overlap_pct: float | None = None
    demand_factor: float | None = None


class TerritoryPatchOut(BaseModel):
    brand: str
    store_name: str
    lat: float
    lng: float
    mode: str
    minutes: int
    polygon: dict
    area_m2: float
    is_new_entrant: bool = False


class TerritoryZoneOut(BaseModel):
    zone_type: str
    label: str
    polygon: dict
    area_m2: float
    brands: list[str] = []
    note: str = ""


class TerritoryDemandOut(BaseModel):
    population: int | None = None
    workers: int | None = None
    households: int | None = None
    youth_pct: float | None = None
    middle_age_pct: float | None = None
    senior_pct: float | None = None
    apartment_pct: float | None = None
    demand_notes: list[str] = []


class TerritoryAnalysisResponse(BaseModel):
    delivery_mode: str
    delivery_minutes: int
    model_fit_mode: str
    model_fit_minutes: int
    before_territories: list[TerritoryPatchOut]
    after_territories: list[TerritoryPatchOut]
    zones: list[TerritoryZoneOut]
    demand: TerritoryDemandOut
    brand_fit_headline: str
    brand_fit_bullets: list[str]
    narrative: list[str]
    warnings: list[str] = []


class TrendSeriesOut(BaseModel):
    category_label: str
    quarters: list[str]
    category_counts: list[int]
    total_counts: list[int]
    category_growth_pct: float | None = None
    total_growth_pct: float | None = None


class StreetSignalOut(BaseModel):
    name: str
    label: str
    category_growth_pct: float | None = None
    store_growth_pct: float | None = None
    detail: str
    data_scope: str


class TrendsResponse(BaseModel):
    category_label: str
    series: TrendSeriesOut | None = None
    street_signals: list[StreetSignalOut]
    data_scope: str
    warnings: list[str] = []


class ChatMessageIn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn]
    lat: float | None = None
    lng: float | None = None
    business_type: str = "general"


class ChatResponse(BaseModel):
    reply: str
    source: str


class DashboardResponse(BaseModel):
    isochrone: IsochroneResponse
    cannibalization: CannibalizationResponse
    trends: TrendsResponse
    territory: TerritoryAnalysisResponse | None = None


# --- Lease feasibility ---


class LeaseFeasibilityRequest(DashboardPointRequest):
    monthly_rent_man: int = Field(..., ge=0, description="월세 (만원)")
    maintenance_man: int = Field(0, ge=0, description="관리비 (만원)")
    deposit_man: int | None = Field(None, ge=0)
    key_money_man: int | None = Field(None, ge=0)
    area_pyeong: float | None = Field(None, gt=0)
    staff_count: int = Field(1, ge=0, le=50)
    ticket_price: int = Field(4500, ge=100)
    cogs_ratio: float | None = Field(None, ge=0, le=0.95)
    delivery_share: float = Field(0.0, ge=0, le=1)
    interior_cost_man: int | None = Field(None, ge=0)
    hours_per_day: float = Field(8.0, ge=1, le=24)
    days_per_week: int = Field(5, ge=1, le=7)
    labor_cost_man: int | None = Field(None, ge=0)
    misc_fixed_man: int | None = Field(None, ge=0)
    subject_codes: list[str] | None = Field(
        None, description="Explicit HIRA clinic subject codes override (e.g. ['05'] for 정형외과)"
    )


class LeaseSiteContextOut(BaseModel):
    total_score: float | None = None
    grade: str = ""
    competition_label: str = ""
    same_category_count: int = 0
    cannibalization_risk: float | None = None
    cannibalization_label: str = ""
    business_label: str = ""
    best_restaurant_models: list[str] = []
    best_cafe_models: list[str] = []


class LeaseFeasibilityResponse(BaseModel):
    verdict: str
    monthly_fixed_cost_man: float
    labor_cost_man: float
    misc_fixed_man: float
    variable_cost_ratio: float
    break_even_monthly_man: float
    break_even_daily_man: float
    orders_per_day: float
    rent_burden_pct: float | None = None
    appropriate_rent_low_man: float | None = None
    appropriate_rent_high_man: float | None = None
    appropriate_rent_band: str = ""
    rent_excess_man: float | None = None
    key_money_payback_months: float | None = None
    recommended_rent_max_man: float | None = None
    rent_per_pyeong_man: float | None = None
    benchmark_monthly_revenue_man: float | None = None
    benchmark_per_store_monthly_man: float | None = None
    benchmark_scope: str = ""
    benchmark_data_source: str = ""
    site_context: LeaseSiteContextOut
    explanation: ExplanationOut
    warnings: list[str] = []


# --- Real competition + model fit ---


class CompetitorSummaryOut(BaseModel):
    name: str
    category: str
    distance_m: float
    weight: float = 1.0
    subjects: list[str] = []
    specialist_count: int | None = None


class CompetitionBucketOut(BaseModel):
    key: str
    label: str
    count: int
    weighted_score: float
    intensity: str
    nearest: list[CompetitorSummaryOut] = []


class RealCompetitionResponse(BaseModel):
    vertical: str
    total_weighted_score: float
    total_intensity: str
    buckets: list[CompetitionBucketOut]
    summary_one_liner: str = ""
    reach_mode: str | None = None
    reach_minutes: int | None = None
    reach_label: str | None = None
    warnings: list[str] = []


class ModelFitItemOut(BaseModel):
    model_id: str
    label: str
    score: float
    verdict: str
    detail: str


class ModelFitResponse(BaseModel):
    vertical: str
    headline: str
    models: list[ModelFitItemOut]
    reach_label: str | None = None
    warnings: list[str] = []


class AcademyClusterStoreOut(BaseModel):
    name: str
    category: str
    nearby_category: str
    relation: str
    distance_m: float
    same_building: bool
    weight: float
    address: str = ""


class AcademyClusterBucketOut(BaseModel):
    key: str
    label: str
    relation_group: str
    count: int
    weighted_score: float
    intensity: str
    stores: list[AcademyClusterStoreOut] = []


class AcademyScoresOut(BaseModel):
    direct_competition_pressure: float
    cluster_synergy: float
    parent_safety: float


class ParentSafetyDetailOut(BaseModel):
    level: str
    same_building_risks: list[str] = []
    nearby_risks: list[str] = []
    sensitivity_note: str = ""


class AcademyClusterResponse(BaseModel):
    target_model: str
    target_label: str
    clusters: list[AcademyClusterBucketOut]
    scores: AcademyScoresOut
    parent_safety_detail: ParentSafetyDetailOut
    headline: str
    interpretation: list[str] = []
    model_fit: ModelFitResponse | None = None
    warnings: list[str] = []


class SynergyStoreOut(BaseModel):
    name: str
    category: str
    nearby_category: str
    relation: str
    distance_m: float
    same_building: bool
    weight: float
    address: str = ""


class SynergyClusterBucketOut(BaseModel):
    key: str
    label: str
    relation_group: str
    count: int
    weighted_score: float
    intensity: str
    stores: list[SynergyStoreOut] = []


class SynergyScoresOut(BaseModel):
    complementary_synergy: float
    direct_competition_pressure: float


class SynergyResponse(BaseModel):
    target: str
    target_label: str
    clusters: list[SynergyClusterBucketOut]
    scores: SynergyScoresOut
    headline: str
    interpretation: list[str] = []
    warnings: list[str] = []


class RealCompetitionBundleResponse(BaseModel):
    competition: RealCompetitionResponse | None = None
    academy_cluster: AcademyClusterResponse | None = None
    model_fit: ModelFitResponse | None = None
    synergy: SynergyResponse | None = None
    warnings: list[str] = []


class RealCompetitionRequest(DashboardPointRequest):
    has_parking: bool | None = None
    has_exhaust: bool | None = None
    has_elevator: bool | None = None
    has_pharmacy_nearby: bool | None = None
    floor_level: int | None = Field(None, ge=1, le=50)
    area_pyeong: float | None = Field(None, gt=0)
    target_model: str | None = Field(
        None,
        description="Academy subject: math|english|korean|science|exam_cram|arts|coding|adult",
    )
    bld_mng_no: str | None = Field(None, description="Candidate building management number")
    subject_codes: list[str] | None = Field(
        None, description="Explicit HIRA clinic subject codes override (e.g. ['05'] for 정형외과)"
    )
    include_model_fit: bool = Field(
        True,
        description="False면 모델 적합도(느린 site_score+도보15분 그래프)를 건너뛰고 경쟁·시너지만 빠르게 반환",
    )
    model_fit_only: bool = Field(
        False,
        description="True면 모델 적합도만 계산(경쟁·시너지 생략). 모델 적합도는 경쟁 mode/minutes와 무관해 위치당 1회 별도 조회하면 됨",
    )


class AcademyClusterRequest(RealCompetitionRequest):
    target_model: str = Field(
        ...,
        description="Academy subject: math|english|korean|science|exam_cram|arts|coding|adult",
    )


class VirtualRoadIn(BaseModel):
    coordinates: list[list[float]] = Field(
        ..., min_length=2, description="[[lng, lat], ...] polyline vertices"
    )
    road_type: str = Field("footway", description="footway | residential")

    @field_validator("road_type")
    @classmethod
    def validate_road_type(cls, v: str) -> str:
        if v not in ("footway", "residential"):
            raise ValueError("road_type must be 'footway' or 'residential'")
        return v


class SimulationContextRequest(BaseModel):
    address: str | None = Field(None, min_length=1, description="Korean street address or place name")
    lat: float | None = None
    lng: float | None = None
    radius_m: int = Field(500, description="Analysis radius in meters (500 or 1000)")

    @field_validator("radius_m")
    @classmethod
    def validate_radius(cls, v: int) -> int:
        if v not in (500, 1000):
            raise ValueError("radius_m must be 500 or 1000")
        return v

    @model_validator(mode="after")
    def validate_location(self) -> "SimulationContextRequest":
        has_coords = self.lat is not None and self.lng is not None
        has_address = bool(self.address and self.address.strip())
        if has_coords or has_address:
            return self
        raise ValueError("address or lat/lng is required")


class SimulationContextResponse(BaseModel):
    center: CenterPoint
    radius_m: int
    segment_count: int
    segments: dict


class SimulateRequest(BaseModel):
    lat: float
    lng: float
    radius_m: int = Field(500, description="Analysis radius in meters (500 or 1000)")
    roads: list[VirtualRoadIn] = Field(default_factory=list, max_length=5)
    closed_osm_ids: list[int] = Field(default_factory=list, max_length=20)

    @field_validator("radius_m")
    @classmethod
    def validate_radius(cls, v: int) -> int:
        if v not in (500, 1000):
            raise ValueError("radius_m must be 500 or 1000")
        return v

    @model_validator(mode="after")
    def validate_simulation_actions(self) -> "SimulateRequest":
        if self.roads or self.closed_osm_ids:
            return self
        raise ValueError("roads or closed_osm_ids is required")


class SimulationMetricOut(BaseModel):
    display_before: float
    display_after: float
    change_pct: float | None = None


class SimulationImpactRoadOut(BaseModel):
    label: str
    osm_id: int
    integration_delta_pct: float | None = None
    choice_delta_pct: float | None = None


class NewRoadMetricOut(BaseModel):
    """신설(가상) 도로 자체의 변경 후 space syntax 지표."""

    osm_id: int
    road_type: str
    length_m: float
    integration: float
    choice: float
    integration_top_pct: float | None = None


class SimulateResponse(BaseModel):
    lat: float
    lng: float
    radius_m: int
    integration: SimulationMetricOut
    choice: SimulationMetricOut
    local_integration: SimulationMetricOut | None = None
    local_choice: SimulationMetricOut | None = None
    local_radius_m: int = 150
    local_segment_count: int = 0
    new_roads: list[NewRoadMetricOut] = Field(default_factory=list)
    improved_count: int
    harmed_count: int
    segment_count: int
    top_improved: list[SimulationImpactRoadOut]
    top_worsened: list[SimulationImpactRoadOut] = Field(default_factory=list)
    segments: dict
    explanation: ExplanationOut
    warnings: list[str] = Field(default_factory=list)


class FloorplanLineIn(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class FloorplanLabelOut(BaseModel):
    text: str
    x: float
    y: float


class FloorplanSymbolOut(BaseModel):
    kind: Literal["stair", "elevator", "entrance", "exit", "other"]
    name: str
    x: float
    y: float


class FloorplanLayerOut(BaseModel):
    """도면의 레이어 하나 — 사람이 "이게 벽입니다"를 고를 수 있게 통계를 함께 준다."""

    name: str
    entity_count: int
    segment_count: int
    total_length: float
    median_length: float = Field(..., description="선분 길이 중앙값 — 벽은 길고 문·치수선은 짧다")
    text_count: int
    block_count: int
    guess: Literal["wall", "opening", "text", "symbol", "other"]
    guessed_by: Literal["name", "size", "none"] = Field(
        ..., description="name=레이어 이름으로 확정, size=크기로 짐작(확인 필요)"
    )


class FloorplanXrefOut(BaseModel):
    name: str
    path: str


class FloorplanExtractResponse(BaseModel):
    canvas_width: float
    canvas_height: float
    lines: list[FloorplanLineIn]
    source: str = Field(..., description="image or dxf")
    openings: list[FloorplanLineIn] = Field(
        default_factory=list,
        description="DXF 문·창 엔티티 — 벽에서 뚫어야 할 개구부 (이미지 업로드는 비어 있음)",
    )
    meters_per_unit: float | None = Field(
        None, description="캔버스 1단위가 몇 m인지 — DXF 헤더($INSUNITS)에 단위가 있을 때만"
    )
    dxf_units: str | None = Field(None, description="DXF가 선언한 길이 단위 (mm/cm/m/inch/ft)")
    layers: list[FloorplanLayerOut] = Field(default_factory=list, description="도면의 레이어 목록")
    xrefs: list[FloorplanXrefOut] = Field(
        default_factory=list, description="외부 참조 — 벽이 다른 파일에 있어 이 파일엔 없다"
    )
    layers_guessed: bool = Field(
        False, description="레이어 이름으로 벽을 못 찾아 크기로 짐작했는지 — 사람 확인이 필요하다"
    )
    suspect_wall_layers: list[str] = Field(
        default_factory=list, description="벽으로 안 썼지만 벽일 수 있는 레이어 (선 길이가 상당함)"
    )
    auto_notes: list[str] = Field(
        default_factory=list,
        description=(
            "사람이 고르지 않아도 프로그램이 알아서 한 일 — 한 장에 여러 도면이 있어 "
            "하나를 골랐다거나, 외곽이 닫히도록 레이어를 더했다거나. 경고가 아니라 "
            "「이렇게 읽었습니다」이므로 화면에서 경고와 구분해 보여 준다."
        ),
    )
    labels: list[FloorplanLabelOut] = Field(default_factory=list, description="실명(室名) — TEXT/MTEXT")
    symbols: list[FloorplanSymbolOut] = Field(
        default_factory=list, description="계단·엘리베이터·출입구 — INSERT 블록명 + 바깥벽 문 추정"
    )
    warnings: list[str] = Field(default_factory=list)


class FloorplanAnalyzeRequest(BaseModel):
    canvas_width: float = Field(..., gt=0)
    canvas_height: float = Field(..., gt=0)
    lines: list[FloorplanLineIn] = Field(..., min_length=1, max_length=5000)
    openings: list[FloorplanLineIn] = Field(default_factory=list, max_length=5000)
    meters_per_unit: float | None = Field(
        None, gt=0, description="캔버스 1단위 = 몇 m (없으면 m 단위 좌표·거리를 내지 않음)"
    )
    labels: list["FloorplanLabelOut"] = Field(
        default_factory=list, max_length=2000, description="추출 단계의 실명 — 문제 제목에 방 이름을 붙이는 데 쓴다"
    )


class FloorplanCellOut(BaseModel):
    row: int
    col: int
    x: float
    y: float
    integration: float
    connectivity: float
    integration_local: float = Field(0.0, description="국소 통합도 (3단계 반경)")
    mean_depth: float = Field(0.0, description="평균 깊이 — 낮을수록 중심")
    choice: float = Field(0.0, description="선택도(경유 빈도) — 높을수록 지나가는 자리")
    aisle_width_m: float | None = Field(None, description="이 칸을 지나는 통로 폭(m, 축척을 알 때) — 로봇 통로 판정용")


class FloorplanSpotOut(BaseModel):
    x: float
    y: float
    value: float
    percentile: float = Field(..., description="전체 칸 중 이 값 이하인 비율(%)")
    x_m: float | None = None
    y_m: float | None = None


class FloorplanExcludedCellOut(BaseModel):
    row: int
    col: int


class FloorplanSummaryOut(BaseModel):
    cols: int
    rows: int
    cell_size: float
    cell_count: int
    integration_mean: float
    integration_max: float
    connectivity_mean: float
    coverage_pct: float = Field(
        100.0, description="분석에 포함된 열린 공간 비율 — 낮으면 도면 일부만 본 것"
    )
    integration_local_mean: float = 0.0
    mean_depth_mean: float = 0.0
    choice_max: float = 0.0
    choice_sampled: bool = Field(False, description="칸이 많아 선택도를 표본 계산했는지")
    cell_size_m: float | None = Field(None, description="격자 한 칸의 실제 크기(m), 축척을 알 때만")
    best_spots: list[FloorplanSpotOut] = Field(default_factory=list, description="목 좋은 자리 TOP5")
    through_spots: list[FloorplanSpotOut] = Field(default_factory=list, description="지나가는 자리 TOP5")
    dead_spots: list[FloorplanSpotOut] = Field(default_factory=list, description="외진 자리 5곳")
    robot_passable_pct: float | None = Field(
        None, description="통로 폭이 로봇 기준(robot_aisle_min_m) 이상인 바닥 비율(%) — 축척을 알 때만"
    )
    robot_aisle_min_m: float = Field(1.6, description="로봇이 지나갈 수 있다고 보는 최소 통로 폭(m)")
    robot_dead_ends: list[FloorplanSpotOut] = Field(default_factory=list, description="로봇 통로의 막다른 끝 ≤5곳")


class FloorplanRoomOut(BaseModel):
    """벽으로 닫힌 공간 하나 — 매장별 표의 한 줄."""

    index: int
    name: str | None = None
    cell_count: int
    area_m2: float | None = None
    x: float
    y: float
    x_m: float | None = None
    y_m: float | None = None
    integration_mean: float
    choice_mean: float
    connectivity_mean: float
    integration_percentile: float = Field(..., description="방들끼리 비교한 목 백분위")
    analyzed_pct: float = Field(..., description="분석에 포함된 비율 — 낮으면 벽에 막힌 방")


class FloorplanIssueOut(BaseModel):
    """도면에서 찾은 문제 하나 — 화면의 기본 뷰는 지표가 아니라 이 목록이다."""

    severity: Literal["high", "medium", "low"] = Field(..., description="high=즉시 검토, medium=개선 권장, low=참고")
    title: str
    detail: str
    x: float
    y: float
    x_m: float | None = None
    y_m: float | None = None
    cell_count: int = 0
    area_m2: float | None = None
    evidence: str = ""
    cells: list[list[int]] = Field(
        default_factory=list, description="구역을 이루는 격자 칸 [row, col] — 화면이 그 부분을 잘라 보여 준다"
    )
    kind: str = Field(
        "",
        description='카드 종류 표지 — "best_zone"(가장 목이 좋은 구역, 문제가 아니라 안내)·"dead_zone"(외진 구역). '
        "화면이 「발견된 문제」 개수에서 좋은 구역을 빼는 데 쓴다.",
    )


class FloorplanCompareRequest(BaseModel):
    """같은 도면의 두 판(원본 / 고친 것)을 한 번에 돌려 비교한다. 두 판은 캔버스
    크기와 축척을 공유하고 벽·문만 다르다."""

    canvas_width: float = Field(..., gt=0)
    canvas_height: float = Field(..., gt=0)
    meters_per_unit: float | None = Field(None, gt=0)
    labels: list[FloorplanLabelOut] = Field(default_factory=list, max_length=2000)
    before_lines: list[FloorplanLineIn] = Field(..., min_length=1, max_length=5000)
    before_openings: list[FloorplanLineIn] = Field(default_factory=list, max_length=5000)
    after_lines: list[FloorplanLineIn] = Field(..., min_length=1, max_length=5000)
    after_openings: list[FloorplanLineIn] = Field(default_factory=list, max_length=5000)


class FloorplanEditPointIn(BaseModel):
    x: float
    y: float


class FloorplanEditIntentRequest(BaseModel):
    """말로 고치기 — 도면에서 고른 자리 + 한 문장 지시 (SPEC_FLOORPLAN_TABS_UI 2026-11-09).

    ``box``가 있으면 네모로 고른 것이고 ``point``는 그 가운데다."""

    instruction: str = Field(..., min_length=1, max_length=300)
    # LLM이 「적용할까요?」라고 물은 뒤 사용자가 「아니 2미터로」처럼 고쳐 말하면 직전 지시를 함께 보낸다.
    previous_instruction: str | None = Field(None, max_length=300)
    point: FloorplanEditPointIn
    box: FloorplanLineIn | None = None
    canvas_width: float = Field(..., gt=0)
    canvas_height: float = Field(..., gt=0)
    meters_per_unit: float | None = Field(None, gt=0)
    labels: list[FloorplanLabelOut] = Field(default_factory=list, max_length=2000)
    lines: list[FloorplanLineIn] = Field(..., min_length=1, max_length=5000)
    openings: list[FloorplanLineIn] = Field(default_factory=list, max_length=5000)


class FloorplanEditStepOut(BaseModel):
    op: Literal["add_door", "remove_wall", "close_door", "add_wall", "clear_walls_in_box"]
    text: str


class FloorplanEditIntentResponse(BaseModel):
    """더할 선과 지울 선 번호. 번호는 요청에 실려 온 ``lines``·``openings`` 배열의 순서다.
    ``question``이 있으면 아무것도 바꾸지 않고 되묻는 것이다."""

    steps: list[FloorplanEditStepOut] = Field(default_factory=list)
    add_lines: list[FloorplanLineIn] = Field(default_factory=list)
    remove_line_indices: list[int] = Field(default_factory=list)
    add_openings: list[FloorplanLineIn] = Field(default_factory=list)
    remove_opening_indices: list[int] = Field(default_factory=list)
    message: str = ""
    question: str | None = None
    source: Literal["llm", "rule"] = "rule"
    warnings: list[str] = Field(default_factory=list)


class FloorplanDeltaOut(BaseModel):
    key: str
    label: str
    before: float
    after: float
    change_pct: float
    direction: Literal["better", "worse", "same"]
    unit: str = ""
    note: str = ""


class FloorplanCompareResponse(BaseModel):
    deltas: list[FloorplanDeltaOut]
    appeared: list[FloorplanIssueOut] = Field(default_factory=list, description="바꾼 뒤 새로 생긴 문제")
    resolved: list[FloorplanIssueOut] = Field(default_factory=list, description="바꿔서 없어진 문제")
    summary: str
    before_summary: FloorplanSummaryOut
    after_summary: FloorplanSummaryOut
    warnings: list[str] = Field(default_factory=list)


class FloorplanExplainRequest(BaseModel):
    """분석 응답의 summary·issues·규칙 설명을 그대로 돌려보내 LLM 해석을 받는다.
    분석과 분리한 이유: LLM은 수 초가 걸리고 5초 룰은 분석에만 적용한다."""

    summary: "FloorplanSummaryOut"
    issues: list[FloorplanIssueOut] = Field(default_factory=list)
    rule_text: str = Field(..., min_length=1, max_length=4000)


class FloorplanChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=2000)


class FloorplanChatRequest(BaseModel):
    """분석 결과를 두고 이어 묻는 상담(2026-11-09). 라우터는 stateless라 화면이 분석
    응답(summary·issues·rooms·규칙 요약)과 지금까지의 대화를 매번 함께 보낸다."""

    summary: "FloorplanSummaryOut"
    issues: list[FloorplanIssueOut] = Field(default_factory=list, max_length=50)
    rooms: list[FloorplanRoomOut] = Field(default_factory=list, max_length=500)
    rule_text: str = Field(..., min_length=1, max_length=4000)
    narrative: str | None = Field(None, max_length=4000, description="화면에 보이는 AI 해석(있으면)")
    messages: list[FloorplanChatMessage] = Field(..., min_length=1, max_length=40)


class FloorplanChatResponse(BaseModel):
    text: str
    source: str = Field(..., description="llm or rule")
    card: int | None = Field(None, description="답이 가리키는 카드 번호(1부터) — 화면이 그 구역을 칠한다")


class FloorplanAnalyzeResponse(BaseModel):
    summary: FloorplanSummaryOut
    cells: list[FloorplanCellOut]
    issues: list[FloorplanIssueOut] = Field(default_factory=list, description="중요도순 문제 목록")
    rooms: list[FloorplanRoomOut] = Field(default_factory=list, description="목 좋은 순 방 목록")
    excluded_cells: list[FloorplanExcludedCellOut] = Field(
        default_factory=list,
        description="벽에 막혀 분석에서 빠진 열린 공간 — 어디가 막혔는지 화면에 표시용",
    )
    explanation: ExplanationOut
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list, description="경고가 아닌 계산 안내 — 엔진이 알아서 바로잡은 것"
    )


class PropertyMarketRequest(BaseModel):
    """주변 매매 실거래 comparable 조회 (투자 수익성 분석용)."""

    address: str | None = Field(None, min_length=1, description="Korean street address or place name")
    lat: float | None = None
    lng: float | None = None
    property_type: str = Field(..., description="commercial | officetel")
    building_type: str = Field("집합", description="집합(구분호실) | 일반(통건물)")
    floor: str | None = Field(None, description="대상 층 (상가 집합만 버킷 필터 적용)")
    area_sqm: float | None = Field(None, gt=0, description="전용/건물면적 m² (±20% 필터)")
    purchase_price_manwon: int | None = Field(None, gt=0, description="검토 매입가 (만원)")
    months_back: int = Field(12, ge=1, le=24)

    @field_validator("property_type")
    @classmethod
    def validate_property_type(cls, v: str) -> str:
        if v not in ("commercial", "officetel"):
            raise ValueError("property_type must be commercial or officetel")
        return v

    @field_validator("building_type")
    @classmethod
    def validate_building_type(cls, v: str) -> str:
        if v not in ("집합", "일반"):
            raise ValueError("building_type must be 집합 or 일반")
        return v

    @model_validator(mode="after")
    def validate_location(self) -> "PropertyMarketRequest":
        has_coords = self.lat is not None and self.lng is not None
        has_address = bool(self.address and self.address.strip())
        if has_coords or has_address:
            return self
        raise ValueError("address or lat/lng is required")


class ComparableTradeOut(BaseModel):
    deal_date: str
    price_per_sqm: float
    deal_amount_manwon: int
    area_sqm: float
    floor: str
    umd_nm: str
    bldg_nm: str
    building_type: str
    lat: float
    lng: float
    distance_m: float


class PropertyMarketResponse(BaseModel):
    lat: float
    lng: float
    property_type: str
    building_type: str
    n: int
    radius_m: int
    months_back: int
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    min_deal_date: str | None = None
    max_deal_date: str | None = None
    relaxations: list[str] = Field(default_factory=list)
    insufficient_sample: bool = False
    excluded: dict[str, int] = Field(default_factory=dict)
    comparables: list[ComparableTradeOut] = Field(default_factory=list)
    subject_price_per_sqm: float | None = None
    subject_percentile: float | None = None
    time_adjusted: bool = False
    as_of: str = ""
    data_source: str = ""
    warnings: list[str] = Field(default_factory=list)


class ProfitabilityPurchaseIn(BaseModel):
    price_manwon: float = Field(..., gt=0, description="매입가 (만원)")
    acquisition_tax_manwon: float | None = Field(None, ge=0, description="취득세 (미입력 시 4.6% 자동)")
    broker_fee_manwon: float = Field(0, ge=0)
    legal_fee_manwon: float = Field(0, ge=0)
    renovation_manwon: float = Field(0, ge=0)
    other_initial_manwon: float = Field(0, ge=0)


class ProfitabilityRentalIn(BaseModel):
    deposit_manwon: float = Field(0, ge=0, description="임차보증금 (수익 아님·자기자본 차감)")
    monthly_rent_manwon: float = Field(0, ge=0)
    other_monthly_income_manwon: float = Field(0, ge=0)
    vacancy_rate: float = Field(0.05, ge=0, le=1)
    credit_loss_rate: float = Field(0, ge=0, le=1)
    owner_opex_monthly_manwon: float = Field(0, ge=0)
    annual_repair_reserve_manwon: float = Field(0, ge=0)


class ProfitabilityLoanIn(BaseModel):
    principal_manwon: float = Field(0, ge=0)
    annual_rate: float = Field(0, ge=0, le=0.3)
    term_years: int = Field(0, ge=0, le=50)
    interest_only: bool = False


class ProfitabilityExitIn(BaseModel):
    hold_years: int = Field(5, ge=1, le=30)
    rent_growth_rate: float = Field(0, ge=-0.2, le=0.2)
    expense_growth_rate: float = Field(0, ge=-0.2, le=0.2)
    sale_price_growth_rate: float = Field(0, ge=-0.2, le=0.2)
    sale_cost_rate: float = Field(0.01, ge=0, le=0.2)
    discount_rate: float | None = Field(None, ge=0, le=0.3)


class ProfitabilityRequest(BaseModel):
    purchase: ProfitabilityPurchaseIn
    rental: ProfitabilityRentalIn
    loan: ProfitabilityLoanIn | None = None
    exit_plan: ProfitabilityExitIn | None = None


class ScenarioOut(BaseModel):
    label: str
    vacancy_rate_pct: float
    loan_rate_pct: float
    noi_manwon: float
    btcf_manwon: float
    cash_on_cash_pct: float | None = None
    dscr: float | None = None


class YearRowOut(BaseModel):
    year: int
    effective_gross_income: float
    operating_expenses: float
    noi: float
    debt_service: float
    btcf: float
    loan_balance_end: float


class ProfitabilityResponse(BaseModel):
    total_cost_manwon: float
    equity_manwon: float
    gross_potential_income_manwon: float
    effective_gross_income_manwon: float
    operating_expenses_manwon: float
    noi_manwon: float
    annual_debt_service_manwon: float
    btcf_manwon: float
    monthly_payment_manwon: float
    gross_yield_pct: float
    cap_rate_noi_pct: float
    cap_rate_total_cost_pct: float
    cash_on_cash_pct: float | None = None
    dscr: float | None = None
    break_even_occupancy_pct: float | None = None
    scenarios: list[ScenarioOut] = Field(default_factory=list)
    sensitivity_vacancy_pct: list[float] = Field(default_factory=list)
    sensitivity_rate_pct: list[float] = Field(default_factory=list)
    sensitivity_coc_pct: list[list[float | None]] = Field(default_factory=list)
    yearly: list[YearRowOut] = Field(default_factory=list)
    sale_price_manwon: float | None = None
    net_sale_proceeds_manwon: float | None = None
    total_operating_cashflow_manwon: float | None = None
    irr_pct: float | None = None
    irr_no_appreciation_pct: float | None = None
    capital_gain_contribution_pct: float | None = None
    npv_manwon: float | None = None
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProfitabilityMarketContextIn(BaseModel):
    """analyze 시 주변 실거래 밴드·입지 위험을 함께 계산하기 위한 위치 정보 (선택)."""

    lat: float
    lng: float
    property_type: str = Field("commercial", description="commercial | officetel")
    building_type: str = Field("집합", description="집합 | 일반")
    floor: str | None = None
    area_sqm: float | None = Field(None, gt=0)

    @field_validator("property_type")
    @classmethod
    def validate_property_type(cls, v: str) -> str:
        if v not in ("commercial", "officetel"):
            raise ValueError("property_type must be commercial or officetel")
        return v


class ProfitabilityAnalyzeRequest(BaseModel):
    purchase: ProfitabilityPurchaseIn
    rental: ProfitabilityRentalIn
    loan: ProfitabilityLoanIn | None = None
    exit_plan: ProfitabilityExitIn | None = None
    market_context: ProfitabilityMarketContextIn | None = None


class RiskItemOut(BaseModel):
    category: str
    severity: str
    message: str


class RebRentTypeOut(BaseModel):
    property_type: str
    rent_krw_per_sqm: float | None = None
    vacancy_pct: float | None = None


class RentReferenceOut(BaseModel):
    matched: bool
    sanggwon_nm: str = ""
    quarter: str = ""
    types: list[RebRentTypeOut] = Field(default_factory=list)
    rent_low_krw_per_sqm: float | None = None
    rent_high_krw_per_sqm: float | None = None
    monthly_rent_low_manwon: float | None = None
    monthly_rent_high_manwon: float | None = None
    vacancy_default_pct: float | None = None
    data_source: str = ""


class ProfitabilityAnalyzeResponse(BaseModel):
    calculation: ProfitabilityResponse
    market: PropertyMarketResponse | None = None
    rent_reference: RentReferenceOut | None = None
    headline: str
    risks: list[RiskItemOut] = Field(default_factory=list)
    levers: list[str] = Field(default_factory=list)
    explanation: str
    explanation_source: str
    warnings: list[str] = Field(default_factory=list)


# --- 동네 인사이트 (주변 아파트 시세 + 생활인구) ---


class AptRecentTradeOut(BaseModel):
    bldg_nm: str
    umd_nm: str = ""
    area_sqm: float
    floor: int | None = None
    deal_amount_manwon: int
    deal_date: str = ""


class ApartmentPricesOut(BaseModel):
    trade_count: int
    median_per_sqm_manwon: float | None = None
    median_per_pyeong_manwon: float | None = None
    scope: str = ""
    #: "radius" = 반경 안 실거래, "gu" = 반경에 없어 구 전체 중앙값으로 폴백.
    scope_kind: str = ""
    recent: list[AptRecentTradeOut] = Field(default_factory=list)


class FootfallProfileOut(BaseModel):
    weekday_hourly: list[float] = Field(default_factory=list)
    weekend_hourly: list[float] = Field(default_factory=list)
    weekday_peak_hour: int | None = None
    source: str = "서울 생활인구 (서울열린데이터광장)"


class FootfallSliceOut(BaseModel):
    label: str
    value: int
    pct: float | None = None


class FootfallBreakdownOut(BaseModel):
    adm_nm: str
    quarter: str
    total: int
    male_pct: float | None = None
    female_pct: float | None = None
    ages: list[FootfallSliceOut] = Field(default_factory=list)
    days: list[FootfallSliceOut] = Field(default_factory=list)
    source: str = "서울 상권분석서비스 길단위 유동인구 (행정동)"


class AreaInsightsRequest(BaseModel):
    lat: float
    lng: float
    radius_m: int = 500


class AreaInsightsResponse(BaseModel):
    apartment: ApartmentPricesOut | None = None
    footfall: FootfallProfileOut | None = None
    footfall_breakdown: FootfallBreakdownOut | None = None


class UndervaluedAreaOut(BaseModel):
    adm_cd: str
    adm_nm: str
    structure_score: float
    structure_percentile: float
    price_per_sqm_manwon: float | None = None
    price_percentile: float | None = None
    trade_sample_count: int
    gap_score: float
    computed_at: str


class UndervaluedAreasResponse(BaseModel):
    areas: list[UndervaluedAreaOut] = Field(default_factory=list)
    # ⚠️ "매수 추천이 아니다"만으로는 약하다. 이 화면이 실제로 읽히는 방식은
    # "여기가 오른다"이고, 그건 우리 검증이 부정한 것이다(§16 기저효과,
    # §24 상관 −0.007). 무엇을 보여주는 값인지까지 여기서 말한다.
    note: str = (
        "구조 점수는 각 동 중심점 500m 도보 반경의 상대값이며, 가격은 최근 24개월 "
        "실거래 중앙값입니다. 이 갭은 **지금 두 순위가 벌어진 정도**일 뿐이며, "
        "미래 상승을 뜻하지 않습니다 — 저희 검증에서 이 값으로 상승을 맞히려 해 봤지만 "
        "무작위보다 낫지 않았습니다. 매수 추천도, 감정평가도 아닙니다."
    )


class UndervaluedParcelOut(BaseModel):
    """필지 하나. 금액이 아니라 **백분위**가 판정의 본문이다.

    ``official_price_per_sqm``은 개별공시지가(원/㎡)이지 시세가 아니다. 나지상정
    평가액이고 시세반영률이 100%가 아니라서, 화면에서 이 값을 "이 땅의 가격"으로
    읽히게 두면 안 된다 — 표기 규칙은 응답 ``note``에 함께 나간다.
    """

    pnu: str
    address: str
    adm_cd: str
    adm_nm: str | None = None
    zone_group: str
    land_category: str | None = None
    area_sqm: float | None = None
    road_side_name: str | None = None
    structure_percentile: float
    price_percentile: float
    gap_score: float
    official_price_per_sqm: float
    stratum_level: str
    stratum_size: int
    base_year: int
    lat: float | None = None
    lng: float | None = None


class MapParcelOut(BaseModel):
    """지도에 그릴 필지 하나 — 경계 + (있으면) 판정.

    판정 필드가 전부 None일 수 있다. 지도는 **땅 전체**를 그려야 하므로 판정이
    없는 필지도 함께 오고, 화면은 그걸 회색으로 그려 "여기는 아직 판정 못 함"을
    보여준다. 판정된 것만 주면 지도에 구멍이 뚫려 데이터 없음과 땅 아님을
    구분할 수 없다.
    """

    pnu: str
    address: str
    geometry: dict[str, Any]          # GeoJSON MultiPolygon (WGS84)
    land_category: str | None = None
    zone_name: str | None = None
    road_side_name: str | None = None
    area_sqm: float | None = None
    official_price_per_sqm: float | None = None
    # 아래는 판정이 있을 때만 채워진다.
    gap_score: float | None = None
    structure_percentile: float | None = None
    price_percentile: float | None = None
    stratum_level: str | None = None
    stratum_size: int | None = None
    zone_group: str | None = None


class MapParcelsResponse(BaseModel):
    parcels: list[MapParcelOut] = Field(default_factory=list)
    judged_count: int = 0
    #: 상한에 걸려 바깥 테두리가 잘렸는가. 조용히 자르면 화면은 "이 반경에 필지가
    #: 이만큼뿐"으로 보이고 사용자는 구멍을 데이터 없음으로 읽는다.
    truncated: bool = False
    note: str = (
        "회색 필지는 판정하지 않은 땅입니다 — 접면 도로를 찾지 못했거나 개발이 "
        "제한된 용도지역입니다. 금액은 개별공시지가이며 시세가 아닙니다."
    )


class UndervaluedParcelsResponse(BaseModel):
    parcels: list[UndervaluedParcelOut] = Field(default_factory=list)
    adm_cd: str = ""
    adm_nm: str | None = None
    note: str = (
        "같은 동·같은 용도지역 필지들과 비교한 상대 순위입니다. "
        "'시세보다 싸다'가 아니라 '같은 조건 필지들의 개별공시지가 수준 대비 낮다'는 "
        "뜻이며, 인과 추정치가 아닙니다. 참고용 신호이지 매수 추천이 아닙니다."
    )


class ParcelStructureRequest(BaseModel):
    """한 블록의 필지들을 길 구조로 줄 세운다 (분양·매입 검토용).

    ``pnus``를 주면 **그 필지들만** 대상이 된다 — 화면에서 "이게 내 사업지"라고
    고른 경우다. 그때는 지도를 옮겨도 대상이 흔들리지 않아 등급을 보고서에 쓸 수
    있다. 비워 두면 좌표 주변을 훑는다.
    """

    lat: float
    lng: float
    pnus: list[str] = Field(
        default_factory=list, max_length=2000, description="고른 필지의 지번코드 — 주면 이것만 본다"
    )
    radius_m: int = Field(400, ge=100, le=800, description="이 반경 안의 필지를 한 묶음으로 본다")
    total_amount_manwon: float | None = Field(
        None, gt=0, description="총액(만원). 넣으면 필지별 배분 금액을 함께 낸다"
    )


class ParcelStructureOut(BaseModel):
    pnu: str
    area_sqm: float
    lat: float | None = None
    lng: float | None = None
    geometry: dict[str, Any] | None = Field(None, description="GeoJSON 경계(WGS84) — 지도에 칠하는 용")
    structure: float | None = Field(None, description="접한 도로의 구조 백분위 0~100")
    integration: float | None = None
    choice: float | None = None
    road_distance_m: float | None = None
    frontage_count: int = Field(0, description="접한 도로 구간 수 — 많으면 코너")
    percentile: float | None = Field(None, description="요청 범위 안에서의 백분위")
    grade: int | None = Field(None, description="1(가장 좋음) ~ 5")
    allocation_share: float | None = Field(None, description="총액 배분 비율(합 1)")
    allocation_manwon: float | None = Field(None, description="총액을 넣었을 때의 배분 금액")


class ParcelStructureSummaryOut(BaseModel):
    parcel_count: int
    graded_count: int
    landlocked_count: int = Field(0, description="접한 도로를 못 찾은 필지 — 맹지이거나 데이터 밖")
    structure_spread: float = Field(0.0, description="상위 10% / 하위 10% 배수")
    corner_count: int


class ParcelStructureResponse(BaseModel):
    summary: ParcelStructureSummaryOut
    parcels: list[ParcelStructureOut]
    caveats: list[str] = Field(default_factory=list, description="이 숫자를 어디까지 믿을지")


class PlannedRoadIn(BaseModel):
    """계획 도면의 길 하나. 아직 지어지지 않은 길이다."""

    coordinates: list[list[float]] = Field(..., min_length=2, description="[[lng, lat], ...]")
    road_type: str = Field("residential", description="footway | residential")
    name: str = ""
    width_m: float | None = Field(None, ge=0, description="도면에서 잰 도로 폭 — 계획도면 모델의 도로 등급")

    @field_validator("road_type")
    @classmethod
    def validate_road_type(cls, v: str) -> str:
        if v not in ("footway", "residential"):
            raise ValueError("road_type must be 'footway' or 'residential'")
        return v


class PlannedLotIn(BaseModel):
    lot_id: str = Field(..., min_length=1, max_length=64)
    geometry: dict[str, Any] = Field(..., description="GeoJSON Polygon/MultiPolygon (WGS84)")


class PlannedSiteRequest(BaseModel):
    """계획 도로망 + 획지를 받아 그 자리에서 구조 등급을 낸다 (택지 분양가용)."""

    roads: list[PlannedRoadIn] = Field(..., min_length=1, max_length=400)
    lots: list[PlannedLotIn] = Field(..., min_length=1, max_length=2000)
    total_amount_manwon: float | None = Field(None, gt=0)
    include_existing_roads: bool = Field(
        False, description="주변 기존 도로를 함께 넣어 도시 안에서의 위치까지 볼지"
    )
    centre_lat: float | None = Field(None, description="기존 도로를 넣을 때의 중심")
    centre_lng: float | None = None


class LotSideOut(BaseModel):
    """획지의 한 변이 닿은 길 — 값은 이 도면 안 도로 구간끼리의 백분위."""

    direction: str = Field(..., description="동 | 서 | 남 | 북")
    length_m: float
    integration_pct: float = Field(..., description="찾아오기 쉬운 길인가 (0~100)")
    choice_pct: float = Field(..., description="지나가는 길목인가 (0~100)")
    score: float
    footway: bool = False
    reasons: list[str] = Field(default_factory=list)
    edge: dict[str, Any] | None = Field(None, description="그 변의 경계선 GeoJSON(WGS84)")


class LotNeighborOut(BaseModel):
    lot_id: str
    distance_m: float
    relation: str = Field(..., description="맞닿음 | 길 건너")


class LotNearbyOut(BaseModel):
    lot_id: str
    kind: str = Field(..., description="school | housing")
    distance_m: float


class PlanScoreOut(BaseModel):
    """지어진 신도시 10곳에서 배운 「가게가 들어설 가능성」 — 그 획지가 든 블록 단위."""

    block_area_m2: float
    score: float = Field(..., description="모델 출력 log1p(가게 수)")
    expected_stores: float = Field(..., description="비슷한 블록에 들어선 가게 수(예상)")
    commercial_share: float = Field(..., description="지어진 신도시에서 이 점수대 블록 중 가게 10곳 이상이었던 비율")
    top_pct: int = Field(..., description="지어진 신도시 블록 가운데 상위 몇 %인가(10% 단위)")
    features: dict[str, float] = Field(default_factory=dict)


class PlannedLotOut(ParcelStructureOut):
    sides: list[LotSideOut] = Field(default_factory=list, description="좋은 변부터")
    neighbors: list[LotNeighborOut] = Field(default_factory=list, description="견줄 상가 후보 땅, 가까운 순")
    kind: str = Field("candidate", description="candidate(상가를 낼 수 있는 땅) | school | housing | public")
    nearby: list[LotNearbyOut] = Field(default_factory=list, description="근처 학교·아파트, 가까운 순")
    best_side_score: float | None = Field(None, description="가장 좋은 변의 값 — 이웃끼리 견준다")
    plan: PlanScoreOut | None = Field(None, description="가게가 들어설 가능성(신도시 10곳 학습 모델)")


class PlannedRoadScoreOut(BaseModel):
    coordinates: list[list[float]] = Field(..., description="[[lng, lat], ...] WGS84")
    score: float = Field(..., description="0~100 — 이 도면 안 길끼리 견준 (찾아오기 쉬움 + 지나가는 길목) 백분위 평균")
    planned: bool = Field(True, description="도면의 계획 길이면 true, 주변 기존 도로면 false")


class PlannedSiteResponse(BaseModel):
    summary: ParcelStructureSummaryOut
    lots: list[PlannedLotOut]
    # 2026-11-06: 블록 색만으로는 블록 안 어느 길이 좋은지 안 보였다(소유자) — 길마다 값을 낸다.
    roads: list[PlannedRoadScoreOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class PlanLayerOut(BaseModel):
    name: str
    entity_count: int
    total_length: float
    closed_count: int
    guess: str = Field(..., description="road | lot | other")


class PlanCrsCandidateOut(BaseModel):
    epsg: str
    lat: float
    lng: float


class PlannedSiteExtractResponse(BaseModel):
    """계획 도면(DXF)에서 꺼낸 길·획지. 그대로 `/api/planned-site`에 넣으면 된다."""

    roads: list[PlannedRoadIn] = Field(default_factory=list)
    lots: list[PlannedLotIn] = Field(default_factory=list)
    layers: list[PlanLayerOut] = Field(default_factory=list)
    crs: str = Field("", description="좌표 변환에 쓴 좌표계. 후보가 없으면 빈 문자열")
    crs_candidates: list[PlanCrsCandidateOut] = Field(
        default_factory=list, description="도면을 한국 땅에 놓는 좌표계 후보 — 사람이 확인한다"
    )
    centre_lat: float | None = None
    centre_lng: float | None = None
    notes: list[str] = Field(default_factory=list, description="「이렇게 읽었습니다」")


class StreetGradeRequest(BaseModel):
    """지도 분석 — 화면 범위(WGS84)의 길을 「목 좋은 길」 3단계로 받는다."""

    west: float = Field(..., ge=124.0, le=132.0)
    south: float = Field(..., ge=33.0, le=39.0)
    east: float = Field(..., ge=124.0, le=132.0)
    north: float = Field(..., ge=33.0, le=39.0)


class StreetSegmentOut(BaseModel):
    grade: int = Field(..., description="1 = 서울 상위 10%, 2 = 10~50%, 3 = 하위 50%")
    top_pct: int = Field(..., description="서울 길 구간 중 상위 몇 %")
    highway: str | None = None
    path: list[list[float]] = Field(..., description="[[lat, lng], ...]")


class StreetGradeResponse(BaseModel):
    overview: bool = Field(..., description="멀리서 본 화면이라 상위 10%만 보냈는가")
    segments: list[StreetSegmentOut]
    caveats: list[str]
