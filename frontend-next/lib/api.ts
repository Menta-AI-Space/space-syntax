// 판정 계약은 landAnalysis.ts 한 곳에만 둔다 — 이 파일에 다시 적으면 갈라진다.
import type { StructuredAnalysis } from "./landAnalysis";

export type RadiusM = 500 | 1000;
export type TravelMode = "walk" | "drive" | "bike" | "moto";

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL || "").replace(/\/$/, "");
// Must exceed the backend's worst-case /api/analyze budget: up to
// depthmapx_timeout_seconds (180s) before falling back to
// analyze_compute_timeout_seconds (120s) for the Python metrics path, plus
// geocode/db-fetch/graph-build overhead. See backend/app/config.py and the
// matching proxy timeout in frontend-next/app/api/[...path]/route.ts.
const DEFAULT_TIMEOUT_MS = 330_000;

export type JsonRecord = Record<string, unknown>;

export interface ApiErrorPayload { detail?: unknown; [key: string]: unknown }
export class ApiClientError extends Error {
  status: number;
  payload: ApiErrorPayload | null;
  constructor(message: string, status: number, payload: ApiErrorPayload | null) {
    super(message); this.name = "ApiClientError"; this.status = status; this.payload = payload;
  }
}

// 백엔드 내부 오류(DB/SQL/스택트레이스 등)가 사용자에게 그대로 노출되지 않도록
// 친절한 문구로 치환한다. 검증·입력 오류 같은 사용자가 이해할 수 있는 메시지는 유지.
function sanitizeErrorText(msg: string): string {
  const internal = /database query failed|\brelation\b|릴레이션|syntax error|psycopg|sqlalchemy|traceback|internal server error/i;
  if (internal.test(msg)) return "분석 데이터를 불러오는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.";
  return msg;
}

// 본문에 이유(detail)가 없는 실패 — 배포 중 Railway가 HTML 503을 돌려줄 때 등 — 엔
// 「요청을 처리하지 못했습니다 (503).」처럼 숫자만 떠 무엇을 하라는지 몰랐다. 서버 쪽 실패는
// 기다렸다 다시 하면 되는 일이라 그렇게 말하고, 숫자는 콘솔에만 남긴다 (2026-11-08 UX 자동 개선).
export function errorMessage(data: ApiErrorPayload | null, status: number): string {
  const detail = data?.detail;
  let raw: string;
  if (Array.isArray(detail)) raw = detail.map((item) => typeof item === "object" && item && "msg" in item ? String((item as { msg: unknown }).msg) : JSON.stringify(item)).join("; ");
  else if (typeof detail === "string") raw = detail;
  else if (detail != null) raw = JSON.stringify(detail);
  else if (status >= 500) raw = "분석 서버가 잠시 응답하지 않습니다. 잠시 후 다시 시도해 주세요.";
  else raw = "요청을 처리하지 못했습니다. 입력한 내용을 확인한 뒤 다시 시도해 주세요.";
  return sanitizeErrorText(raw);
}

/** 화면 언어 — LLM이 처음부터 그 말로 쓰게 요청에 싣는다(영어 모드에서 한국어가 잠깐 보였다 바뀌지 않게). */
function uiLang(): "en" | "ko" {
  return typeof document !== "undefined" && document.documentElement.lang === "ko" ? "ko" : "en";
}

async function requestJson<T>(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, headers, signal: providedSignal, ...rest } = init;
  // 호출한 쪽이 signal을 넘겨도 시간 제한은 지킨다. 전엔 signal이 있으면 타이머를 아예 안
  // 걸어, 지도 분석 탭(streetGrades, 20초)은 서버가 답을 안 주면 「길을 불러오는 중…」에서
  // 영영 멈추고 「다시 불러오기」도 안 떴다 (2026-11-07 UX 자동 개선).
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const forwardAbort = () => controller.abort(providedSignal?.reason);
  if (providedSignal?.aborted) forwardAbort();
  else providedSignal?.addEventListener("abort", forwardAbort, { once: true });
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...rest,
      headers: { "Content-Type": "application/json", ...(headers || {}) },
      signal: controller.signal,
    });
    const data = await response.json().catch(() => null) as ApiErrorPayload | null;
    if (!response.ok) throw new ApiClientError(errorMessage(data, response.status), response.status, data);
    return data as T;
  } catch (error) {
    // 호출한 쪽이 끊은 것은 시간 초과가 아니다 — 원래 오류 그대로 돌려 호출한 쪽이 무시하게 둔다.
    if (providedSignal?.aborted) throw error;
    if (controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
      throw new Error("요청 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.");
    }
    // fetch 자체 실패(분석 서버 미연결·오프라인 등)는 TypeError로 온다.
    if (error instanceof TypeError) {
      throw new Error("분석 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
    providedSignal?.removeEventListener("abort", forwardAbort);
  }
}

export interface DongHit { name: string; lat: number; lng: number }

/** 행정동 이름 검색 (자동완성 상단 '동네'). 실패·짧은 질의는 빈 목록. */
export function searchDongs(q: string, limit = 5): Promise<DongHit[]> {
  const query = q.trim();
  if (query.length < 2) return Promise.resolve([]);
  return requestJson<DongHit[]>(
    `/api/geo/dong-search?q=${encodeURIComponent(query)}&limit=${limit}`,
    { method: "GET", timeoutMs: 4000 },
  ).catch(() => []);
}

export interface GeoJsonFeature { type: string; geometry?: { type: string; coordinates?: [number, number][] | unknown }; properties?: JsonRecord }
export interface AnalyzeRequest { address?: string | null; lat?: number | null; lng?: number | null; radius_m: RadiusM; mode?: "walk" | "drive" }
export interface AnalyzeResponse { center: { lat: number; lng: number }; radius_m: number; segment_count: number; warnings?: string[]; segments: { type: string; features: GeoJsonFeature[] }; engines?: Record<string, unknown> }
export interface SiteScoreRequest { lat?: number | null; lng?: number | null; address?: string | null; radius_m: RadiusM; inds_lcls_cd?: string | null; inds_mcls_cd?: string | null; inds_scls_cd?: string | null; business_type?: string; brand?: string | null; priorities?: string[]; include_ai_explanation?: boolean }
export interface SubScore { key: string; label: string; score?: number | null; detail: string; tooltip?: string; summary_label?: string; display?: string; rank_top_pct?: number | null }
export interface NearestSegment { osm_id: number; highway: string; distance_m: number; integration_top_pct: number; choice_top_pct: number; mean_depth_top_pct: number; connectivity_top_pct: number; connectivity_degree: number }
export interface TransitInfo { station_name: string; distance_m: number }
export interface PriorityNote { key: string; label: string; applied: boolean; note?: string }
export interface PersonalFit { base_score: number; personal_score: number; priorities: PriorityNote[]; strengths: string[]; cautions: string[]; weights_used: Record<string, number> }
export interface PersonalFitRequest { base_score: number; subscores: SubScore[]; base_weights: Record<string, number>; priorities: string[]; /** 우선순위별 강도 배율 1.0~3.0 — GF식 'your weight' 슬라이더. */ priority_boosts?: Record<string, number>; extra_factor_scores?: Record<string, number> }
export interface SiteScoreResponse { lat: number; lng: number; radius_m: number; business_type: string; business_label: string; inds_lcls_cd?: string | null; inds_mcls_cd?: string | null; inds_scls_cd?: string | null; upjong_breadcrumb?: string; total_score: number; grade: string; verdict_one_liner?: string; segment: NearestSegment; subscores: SubScore[]; transit?: TransitInfo | null; demand?: JsonRecord | null; competition?: JsonRecord | null; explanation?: { text: string; source: string }; industry_profile_id?: string | null; industry_kpis?: IndustryKpi[]; sales_benchmark?: SalesBenchmark | null; personal_fit?: PersonalFit | null; personalization_weights?: Record<string, number>; personalization_factors?: Record<string, number>; warnings?: string[] }
export interface IndustryKpi { key: string; label: string; score?: number | null; display?: string; detail?: string }
export interface SalesBenchmark { trdar_cd: string; trdar_nm: string; trdar_se_nm?: string; inside_area?: boolean; distance_m?: number; quarter: string; monthly_sales_won: number; store_count?: number | null; franchise_count?: number | null; per_store_monthly_won?: number | null; weekend_share_pct?: number | null; citywide_median_per_store_won?: number | null; citywide_trdar_count?: number; /** "trdar" = 상권 실측, "dong" = 상권 밖이라 행정동 평균으로 대체. */ scope?: string }

/** scope가 "dong"이면 상권 실측이 아니라 행정동 평균으로 메운 값이라는 표시를 반환한다. */
export function salesBenchmarkScopeNote(benchmark: Pick<SalesBenchmark, "scope"> | null | undefined): string {
  return benchmark?.scope === "dong" ? " (행정동 평균 대체)" : "";
}
export interface DashboardPointRequest extends SiteScoreRequest { mode?: TravelMode; minutes?: number[]; competitor_minutes?: number; include_territory?: boolean }
export interface ChatRequest { messages: Array<{ role: string; content: string }>; lat?: number | null; lng?: number | null; business_type?: string }

export interface GeoPolygon { type: string; coordinates: [number, number][][] }
export interface IsochroneBand { minutes: number; max_distance_m: number; polygon: GeoPolygon; area_m2: number; store_count: number; same_category_count: number; population_estimate?: number | null }
export interface IsochroneResponse { lat: number; lng: number; mode: string; bands: IsochroneBand[]; start_connected: boolean; warnings?: string[] }
export interface CompetitorOut { name: string; category: string; distance_m: number; address: string; lat?: number | null; lng?: number | null; kakao_place_id?: string | null; place_url?: string | null; photo_url?: string | null; rating?: number | null; review_count?: number | null; tagline?: string | null; phone?: string | null; kakao_category?: string | null }
export interface CannibalizationResponse { risk_score: number; risk_label: string; competitors: CompetitorOut[]; overlap_pct?: number | null; overlap_with?: string | null; overlap_recommended_max_pct: number; detail: string; mode: string; minutes: number; total_count: number; reach_polygon?: GeoPolygon | null; self_total_count?: number; self_competitors?: CompetitorOut[]; self_overlap_pct?: number | null; demand_factor?: number | null }
export interface TerritoryPatch { brand: string; store_name: string; lat: number; lng: number; mode: string; minutes: number; polygon: GeoPolygon; area_m2: number; is_new_entrant?: boolean }
export interface TerritoryZone { zone_type: string; label: string; polygon: GeoPolygon; area_m2: number; brands: string[]; note: string }
export interface TerritoryDemand { population?: number | null; workers?: number | null; households?: number | null; youth_pct?: number | null; middle_age_pct?: number | null; senior_pct?: number | null; apartment_pct?: number | null; demand_notes: string[] }
export interface TerritoryAnalysisResponse { delivery_mode: string; delivery_minutes: number; model_fit_mode: string; model_fit_minutes: number; before_territories: TerritoryPatch[]; after_territories: TerritoryPatch[]; zones: TerritoryZone[]; demand: TerritoryDemand; brand_fit_headline: string; brand_fit_bullets: string[]; narrative: string[]; warnings?: string[] }
export interface TrendSeries { category_label: string; quarters: string[]; category_counts: number[]; total_counts: number[]; category_growth_pct?: number | null; total_growth_pct?: number | null }
export interface StreetSignal { name: string; label: string; category_growth_pct?: number | null; store_growth_pct?: number | null; detail: string; data_scope: string }
export interface TrendsResponse { category_label: string; series?: TrendSeries | null; street_signals: StreetSignal[]; data_scope: string; warnings?: string[] }
export interface DashboardResponse { isochrone: IsochroneResponse; cannibalization: CannibalizationResponse; trends: TrendsResponse; territory?: TerritoryAnalysisResponse | null }
export interface ChatResponse { reply: string; source: string }
export interface SynergyStore { name: string; category: string; nearby_category: string; relation: string; distance_m: number; same_building: boolean; weight: number; address?: string }
export interface SynergyClusterBucket { key: string; label: string; relation_group: string; count: number; weighted_score: number; intensity: string; stores: SynergyStore[] }
export interface SynergyScores { complementary_synergy: number; direct_competition_pressure: number }
export interface SynergyResponse { target: string; target_label: string; clusters: SynergyClusterBucket[]; scores: SynergyScores; headline: string; interpretation: string[]; warnings?: string[] }
export interface CompetitorSummary { name: string; category: string; distance_m: number; weight?: number; subjects?: string[]; specialist_count?: number | null }
export interface CompetitionBucket { key: string; label: string; count: number; weighted_score: number; intensity: string; nearest?: CompetitorSummary[] }
export interface RealCompetitionResponse { vertical: string; total_weighted_score: number; total_intensity: string; buckets: CompetitionBucket[]; summary_one_liner?: string; reach_mode?: string | null; reach_minutes?: number | null; reach_label?: string | null; warnings?: string[] }
export interface ModelFitItem { model_id: string; label: string; score: number; verdict: string; detail: string }
export interface ModelFitResponse { vertical: string; headline: string; models: ModelFitItem[]; reach_label?: string | null; warnings?: string[] }
export interface AcademyClusterStore { name: string; category: string; nearby_category: string; relation: string; distance_m: number; same_building: boolean; weight: number; address?: string }
export interface AcademyClusterBucket { key: string; label: string; relation_group: string; count: number; weighted_score: number; intensity: string; stores?: AcademyClusterStore[] }
export interface AcademyScores { direct_competition_pressure: number; cluster_synergy: number; parent_safety: number }
export interface ParentSafetyDetail { level: string; same_building_risks?: string[]; nearby_risks?: string[]; sensitivity_note?: string }
export interface AcademyClusterResponse { target_model: string; target_label: string; clusters: AcademyClusterBucket[]; scores: AcademyScores; parent_safety_detail: ParentSafetyDetail; headline: string; interpretation?: string[]; model_fit?: ModelFitResponse | null; warnings?: string[] }
export interface RealCompetitionBundleResponse { competition?: RealCompetitionResponse | null; academy_cluster?: AcademyClusterResponse | null; model_fit?: ModelFitResponse | null; synergy?: SynergyResponse | null; warnings?: string[] }
export interface RealCompetitionRequest extends DashboardPointRequest { has_parking?: boolean | null; has_exhaust?: boolean | null; has_elevator?: boolean | null; has_pharmacy_nearby?: boolean | null; floor_level?: number | null; area_pyeong?: number | null; target_model?: string | null; bld_mng_no?: string | null; subject_codes?: string[] | null }
export interface LeaseSiteContext { total_score?: number | null; grade?: string; competition_label?: string; same_category_count?: number; cannibalization_risk?: number | null; cannibalization_label?: string; business_label?: string; best_restaurant_models?: string[]; best_cafe_models?: string[] }
export interface LeaseFeasibilityResponse { verdict: string; monthly_fixed_cost_man: number; labor_cost_man: number; misc_fixed_man: number; variable_cost_ratio: number; break_even_monthly_man: number; break_even_daily_man: number; orders_per_day: number; rent_burden_pct?: number | null; appropriate_rent_low_man?: number | null; appropriate_rent_high_man?: number | null; appropriate_rent_band?: string; rent_excess_man?: number | null; key_money_payback_months?: number | null; recommended_rent_max_man?: number | null; rent_per_pyeong_man?: number | null; benchmark_monthly_revenue_man?: number | null; benchmark_per_store_monthly_man?: number | null; benchmark_scope?: string; benchmark_data_source?: string; site_context: LeaseSiteContext; explanation: { text: string; source: string }; warnings?: string[]; notes?: string[] }
export interface LeaseFeasibilityRequest extends DashboardPointRequest { monthly_rent_man: number; maintenance_man?: number; deposit_man?: number | null; key_money_man?: number | null; area_pyeong?: number | null; staff_count?: number; ticket_price?: number; cogs_ratio?: number | null; delivery_share?: number; interior_cost_man?: number | null; hours_per_day?: number; days_per_week?: number; labor_cost_man?: number | null; misc_fixed_man?: number | null; subject_codes?: string[] | null }
export interface SimulationContextResponse { center: { lat: number; lng: number }; radius_m: number; segment_count: number; segments: { type: string; features: GeoJsonFeature[] } }
export interface SimulationContextRequest { address?: string | null; lat?: number | null; lng?: number | null; radius_m: RadiusM }
export interface VirtualRoadRequest { coordinates: Array<[number, number]>; road_type?: "footway" | "residential" }
export interface SimulationMetric { display_before: number; display_after: number; change_pct?: number | null }
export interface SimulationImpactRoad { label: string; osm_id: number; integration_delta_pct?: number | null; choice_delta_pct?: number | null }
export interface SimulateRequest { lat: number; lng: number; radius_m: RadiusM; roads?: VirtualRoadRequest[]; closed_osm_ids?: number[] }
export interface NewRoadMetric { osm_id: number; road_type: string; length_m: number; integration: number; choice: number; integration_top_pct?: number | null }
export interface SimulateResponse { lat: number; lng: number; radius_m: RadiusM; integration: SimulationMetric; choice: SimulationMetric; local_integration?: SimulationMetric | null; local_choice?: SimulationMetric | null; local_radius_m?: number; local_segment_count?: number; new_roads?: NewRoadMetric[]; improved_count: number; harmed_count: number; segment_count: number; top_improved: SimulationImpactRoad[]; top_worsened: SimulationImpactRoad[]; segments: { type: string; features: GeoJsonFeature[] }; explanation: { text: string; source: string }; warnings?: string[] }
export interface UpjongSearchResult { type: string; inds_lcls_cd: string; inds_mcls_cd: string; inds_scls_cd?: string | null; label: string; breadcrumb: string; matched_alias?: string | null }
export interface AptRecentTrade { bldg_nm: string; umd_nm: string; area_sqm: number; floor?: number | null; deal_amount_manwon: number; deal_date: string }
export interface ApartmentPrices { trade_count: number; median_per_sqm_manwon?: number | null; median_per_pyeong_manwon?: number | null; scope: string; /** "radius" = 반경 안 실거래, "gu" = 반경에 없어 구 전체 중앙값으로 폴백. */ scope_kind?: string; recent: AptRecentTrade[] }
export interface FootfallProfile { weekday_hourly: number[]; weekend_hourly: number[]; weekday_peak_hour?: number | null; source: string }
export interface FootfallSlice { label: string; value: number; pct?: number | null }
export interface FootfallBreakdown { adm_nm: string; quarter: string; total: number; male_pct?: number | null; female_pct?: number | null; ages: FootfallSlice[]; days: FootfallSlice[]; source: string }
export interface AreaInsightsResponse { apartment?: ApartmentPrices | null; footfall?: FootfallProfile | null; footfall_breakdown?: FootfallBreakdown | null }
export interface UpjongTreeResponse { large: Array<JsonRecord>; presets: Array<JsonRecord> }
export interface FranchiseBrand { brand_nm: string; industry_mid?: string | null; hq_conm?: string | null; frcs_cnt?: number | null; yr?: number | null }
export interface FranchiseBrandsResponse { brands: FranchiseBrand[] }
export interface FranchiseBrandDetail { brand_nm: string; industry_mid?: string | null; hq_conm?: string | null; yr?: number | null; frcs_cnt?: number | null; new_open_cnt?: number | null; contract_end_cnt?: number | null; contract_cancel_cnt?: number | null; churn_pct?: number | null; startup_cost_total_manwon?: number | null }
export interface ClinicSubject { subject_code: string; subject_name: string; institution_count: number }
export interface ClinicSubjectsResponse { subjects: ClinicSubject[]; warnings?: string[] }
export type AdaptiveUserType = "entrepreneur" | "franchise";
export interface AdaptiveKpiConfig { key: string; label: string }
export interface AdaptiveIndustryProfile {
  industryId: string;
  name: string;
  parentCategory: string;
  userType: AdaptiveUserType;
  version: string;
  effectiveFrom: string;
  analysis: {
    catchment: { primary: string; walkMinutes?: number[]; driveMinutes?: number[]; transitMinutes?: number[]; deliveryMinutes?: number[] };
    timeSlots: string[];
    competitorGroups: Record<string, string[]>;
  };
  ui: {
    summaryKpis: AdaptiveKpiConfig[];
    primaryTabs: string[];
    secondaryTabs: string[];
    mapLayers: string[];
    alerts: string[];
    recommendations: string[];
    reportSections: string[];
  };
}
export interface AdaptiveProfileSummary { id: string; name: string; parentCategory: string; supportedUserTypes: AdaptiveUserType[]; version: string }

// --- Investment profitability (POST /api/profitability/analyze) ---
export interface ProfitabilityPurchaseInput { price_manwon: number; acquisition_tax_manwon?: number | null; broker_fee_manwon?: number; legal_fee_manwon?: number; renovation_manwon?: number; other_initial_manwon?: number }
export interface ProfitabilityRentalInput { deposit_manwon?: number; monthly_rent_manwon?: number; other_monthly_income_manwon?: number; vacancy_rate?: number; credit_loss_rate?: number; owner_opex_monthly_manwon?: number; annual_repair_reserve_manwon?: number }
export interface ProfitabilityLoanInput { principal_manwon?: number; annual_rate?: number; term_years?: number; interest_only?: boolean }
export interface ProfitabilityExitInput { hold_years?: number; rent_growth_rate?: number; expense_growth_rate?: number; sale_price_growth_rate?: number; sale_cost_rate?: number; discount_rate?: number | null }
export interface ProfitabilityMarketContextInput { lat: number; lng: number; property_type: "commercial" | "officetel"; building_type: "집합" | "일반"; floor?: string | null; area_sqm?: number | null }
export interface ProfitabilityAnalyzeRequest { purchase: ProfitabilityPurchaseInput; rental: ProfitabilityRentalInput; loan?: ProfitabilityLoanInput | null; exit_plan?: ProfitabilityExitInput | null; market_context?: ProfitabilityMarketContextInput | null }
export interface ProfitabilityScenario { label: string; vacancy_rate_pct: number; loan_rate_pct: number; noi_manwon: number; btcf_manwon: number; cash_on_cash_pct?: number | null; dscr?: number | null }
export interface ProfitabilityYearRow { year: number; effective_gross_income: number; operating_expenses: number; noi: number; debt_service: number; btcf: number; loan_balance_end: number }
export interface ProfitabilityResult { total_cost_manwon: number; equity_manwon: number; gross_potential_income_manwon: number; effective_gross_income_manwon: number; operating_expenses_manwon: number; noi_manwon: number; annual_debt_service_manwon: number; btcf_manwon: number; monthly_payment_manwon: number; gross_yield_pct: number; cap_rate_noi_pct: number; cap_rate_total_cost_pct: number; cash_on_cash_pct?: number | null; dscr?: number | null; break_even_occupancy_pct?: number | null; scenarios: ProfitabilityScenario[]; sensitivity_vacancy_pct: number[]; sensitivity_rate_pct: number[]; sensitivity_coc_pct: (number | null)[][]; yearly: ProfitabilityYearRow[]; sale_price_manwon?: number | null; net_sale_proceeds_manwon?: number | null; total_operating_cashflow_manwon?: number | null; irr_pct?: number | null; irr_no_appreciation_pct?: number | null; capital_gain_contribution_pct?: number | null; npv_manwon?: number | null; assumptions: string[]; warnings: string[] }
export interface ComparableTrade { deal_date: string; price_per_sqm: number; deal_amount_manwon: number; area_sqm: number; floor: string; umd_nm: string; bldg_nm: string; building_type: string; lat: number; lng: number; distance_m: number }
export interface PropertyMarketResult { lat: number; lng: number; property_type: string; building_type: string; n: number; radius_m: number; months_back: number; p25?: number | null; p50?: number | null; p75?: number | null; min_deal_date?: string | null; max_deal_date?: string | null; relaxations: string[]; insufficient_sample: boolean; excluded: Record<string, number>; comparables: ComparableTrade[]; subject_price_per_sqm?: number | null; subject_percentile?: number | null; time_adjusted: boolean; as_of: string; data_source: string; warnings: string[] }
export interface ProfitabilityRiskItem { category: string; severity: "critical" | "high" | "medium"; message: string }
export interface RebRentType { property_type: string; rent_krw_per_sqm?: number | null; vacancy_pct?: number | null }
export interface RentReference { matched: boolean; sanggwon_nm: string; quarter: string; types: RebRentType[]; rent_low_krw_per_sqm?: number | null; rent_high_krw_per_sqm?: number | null; monthly_rent_low_manwon?: number | null; monthly_rent_high_manwon?: number | null; vacancy_default_pct?: number | null; data_source: string }
export interface ProfitabilityAnalyzeResponse { calculation: ProfitabilityResult; market?: PropertyMarketResult | null; rent_reference?: RentReference | null; headline: string; risks: ProfitabilityRiskItem[]; levers: string[]; explanation: string; explanation_source: string; warnings: string[] }

// --- 결정 에이전트 (POST /api/decide) ---
// 원자료 대시보드 대신 "그래서 어디를 골라야 하는가"에 답하는 판정 API.
// MVP는 goal="compare_candidates"(후보지 2~5곳 비교)만 구현되어 있다.
export type DecisionGoal = "find_site" | "compare_candidates" | "evaluate_listing" | "evaluate_purchase" | "diagnose_store";
export type DecisionPriority = "revenue" | "stability" | "balanced";
export type DecisionVerdict = "recommend" | "conditional" | "hold" | "avoid";
export interface DecideCandidateInput { id: string; label: string; lat: number; lng: number; monthly_rent_man?: number | null; deposit_man?: number | null; area_pyeong?: number | null; floor?: number | null }
export interface DecideConstraints { max_rent_man?: number | null }
export interface DecideRequest { goal: "compare_candidates"; inds_lcls_cd?: string | null; inds_mcls_cd?: string | null; inds_scls_cd?: string | null; radius_m: RadiusM; priority: DecisionPriority; brand?: string | null; candidates: DecideCandidateInput[]; constraints?: DecideConstraints | null }
export interface DecideRankedItem { id: string; label: string; rank: number; recommend_score: number; verdict: DecisionVerdict; verdict_label: string; expected_monthly_sales_man: number | null; expected_monthly_sales_range_man?: number[] | null; expected_sales_basis: string; max_acceptable_rent_man: number | null; current_rent_man: number | null; rent_gap_man: number | null; strengths: string[]; risks: string[]; actions: string[]; evidence: Record<string, unknown> }
export interface DecideResponse { ranked: DecideRankedItem[]; headline: string; comparison_note: string; narrative_source: "llm" | "rule"; warnings: string[] }

// 목적별 판정 API. /api/decide(후보지 비교)와 같은 어투("결론 → 숫자 → 근거")로
// 답하는 형제 엔드포인트들이며, 화면에서 고르는 목적 하나에 하나씩 대응한다.
export type NarrativeSource = "llm" | "rule";

// 네 엔드포인트가 공통으로 돌려주는 서사 부분. 숫자 필드는 계산이 안 되면
// 백엔드가 null을 줄 수 있으므로 화면은 항상 "없을 수도 있다"고 가정한다.
export interface AgentNarrative { headline: string; narrative_source: NarrativeSource; warnings: string[] }

/** 판정 요청이 공통으로 갖는 위치 + 업종 + 반경. */
export interface AgentPointRequest {
  lat: number;
  lng: number;
  inds_lcls_cd?: string | null;
  inds_mcls_cd?: string | null;
  inds_scls_cd?: string | null;
  radius_m: RadiusM;
}

// --- 1. 계약해도 되는지 (POST /api/lease-verdict) ---
export interface LeaseVerdictRequest extends AgentPointRequest { monthly_rent_man: number; maintenance_man?: number | null; deposit_man?: number | null; key_money_man?: number | null; area_pyeong?: number | null; floor?: number | null; brand?: string | null }
// negotiation은 계약상 항상 오지만, 한 필드가 비었다고 판정문 전체가 깨지지
// 않도록 여기서도 null을 허용해 두고 화면에서 있는 항목만 렌더한다.
export interface LeaseNegotiation { target_rent_man?: number | null; rent_free_months?: number | null; max_key_money_man?: number | null }
export interface LeaseVerdictResponse extends AgentNarrative { verdict: DecisionVerdict; verdict_label: string; recommend_score?: number | null; expected_monthly_sales_man?: number | null; expected_monthly_sales_range_man?: number[] | null; expected_sales_basis?: string | null; break_even_monthly_man?: number | null; rent_burden_pct?: number | null; appropriate_rent_low_man?: number | null; appropriate_rent_high_man?: number | null; rent_gap_man?: number | null; negotiation?: LeaseNegotiation | null; key_money_payback_months?: number | null; strengths?: string[]; risks?: string[]; actions?: string[]; evidence?: Record<string, unknown> }

// --- 2. 매입해도 되는지 (POST /api/purchase-verdict) ---
export interface PurchaseLoanInput { principal_manwon?: number | null; annual_rate?: number | null; term_years?: number | null; interest_only?: boolean }
export interface PurchaseVerdictRequest extends AgentPointRequest { price_manwon: number; area_sqm: number; property_type?: string | null; building_type?: string | null; floor?: number | null; loan?: PurchaseLoanInput | null }
/** 주변 실거래 밴드 (㎡당 만원). 표본이 없으면 통째로 없을 수 있다. */
export interface PurchaseMarketBand { p25?: number | null; p50?: number | null; p75?: number | null; n?: number | null; radius_m?: number | null }
export interface PurchaseVerdictResponse extends AgentNarrative { verdict: DecisionVerdict; verdict_label: string; cap_rate_pct?: number | null; dscr?: number | null; payback_years?: number | null; max_acceptable_price_manwon?: number | null; price_premium_pct?: number | null; market_band?: PurchaseMarketBand | null; monthly_noi_man?: number | null; strengths?: string[]; risks?: string[]; actions?: string[]; evidence?: Record<string, unknown> }

// --- 3. 매출이 낮은 이유 (POST /api/diagnose) ---
export type DiagnoseSeverity = "high" | "medium" | "low";
export interface DiagnoseRequest extends AgentPointRequest { current_monthly_sales_man: number; monthly_rent_man?: number | null; brand?: string | null }
export interface DiagnoseCause { key: string; label: string; severity: DiagnoseSeverity; finding?: string | null; action?: string | null }
export interface DiagnoseResponse extends AgentNarrative { expected_monthly_sales_man?: number | null; gap_man?: number | null; gap_pct?: number | null; verdict_label?: string | null; causes?: DiagnoseCause[] | null; strengths?: string[]; evidence?: Record<string, unknown> }

// --- 4. 어디에 열지 (POST /api/area-search) ---
export interface AreaSearchRequest { region_query: string; lat?: number | null; lng?: number | null; inds_lcls_cd?: string | null; inds_mcls_cd?: string | null; inds_scls_cd?: string | null; budget_max_rent_man?: number | null; priority?: DecisionPriority }
/** "이 자리랑 비슷한 동네" — 유사도는 구조(직장인·점포밀도·주말비중·프랜차이즈
 * 비율)로 재고, 성과(점포당 매출)는 비교만 한다(backend/app/services/analogs.py). */
export interface AnalogRow { trdar_cd: string; trdar_nm: string; similarity?: number | null; per_store_monthly_won?: number | null; store_count?: number; weekend_share_pct?: number | null; franchise_ratio_pct?: number | null; workers?: number | null }
export interface AnalogsResponse { subject: AnalogRow; analogs: AnalogRow[]; quarter: string; basis: string; data_source: string }

export interface AreaCandidate { label: string; lat?: number | null; lng?: number | null; recommend_score?: number | null; verdict?: DecisionVerdict | null; verdict_label?: string | null; expected_monthly_sales_man?: number | null; max_acceptable_rent_man?: number | null; strengths?: string[]; risks?: string[]; evidence?: Record<string, unknown> }
// center.label이 백엔드가 실제로 채우는 "어디를 찾았는지" 값이다(app/routers/goals.py
// CenterOut). 최상위 region_label 필드는 백엔드 응답에 존재한 적이 없다.
// region_label은 백엔드가 주는 값이 아니라 클라이언트가 요청에 실어 보낸
// region_query를 되비춰 채우는 값이다(withAreaSearchRegionLabel) — 그래서 optional.
export interface AreaSearchResponse extends AgentNarrative { center?: { lat: number; lng: number; label?: string | null; search_radius_m?: number } | null; candidates?: AreaCandidate[] | null; region_label?: string | null }

/** "어디를 찾았는지" 헤드라인 칩에 쓸 라벨. 없으면 칩을 아예 숨기도록 undefined를 준다. */
export function areaSearchRegionLabel(result: Pick<AreaSearchResponse, "center"> | null | undefined): string | undefined {
  return result?.center?.label || undefined;
}

// 백엔드 /api/area-search 응답에는 region_label 필드가 없다 — 내부적으로만 쓰고
// 안 돌려준다. 화면의 지역 칩은 실제로 이 검색에 쓰인 값(요청에 실어 보낸
// region_query, 즉 사용자가 고른 동네 이름)을 그대로 보여준다 — 새로 지어내는
// 값이 아니라 이미 우리가 보낸 요청을 그대로 되비추는 것이다.
export function withAreaSearchRegionLabel(result: AreaSearchResponse, requestedRegion: string | null): AreaSearchResponse {
  if (result.region_label) return result;
  return { ...result, region_label: requestedRegion || null };
}

// /api/agent/ask의 tool_result(area_search)도 같은 이유로 region_label이 없다.
// 이 경로에서는 응답 최상위 address가 에이전트가 실제로 해석한 지역 텍스트라
// 그것을 그대로 옮긴다.
export function withAgentAskAreaSearchRegionLabel(result: AgentAskResponse): AgentAskResponse {
  if (result.tool !== "area_search" || !result.tool_result) return result;
  if (result.tool_result.region_label) return result;
  return { ...result, tool_result: { ...result.tool_result, region_label: result.address || null } };
}

/** 필지 하나의 길 구조 등급. 금액은 사용자가 총액을 넣었을 때만 채워진다. */
export interface ParcelStructure {
  pnu: string; area_sqm: number; lat?: number | null; lng?: number | null;
  geometry?: { type: string; coordinates: unknown } | null;
  structure?: number | null; integration?: number | null; choice?: number | null;
  road_distance_m?: number | null; frontage_count: number;
  percentile?: number | null; grade?: number | null;
  allocation_share?: number | null; allocation_manwon?: number | null;
}
export interface ParcelStructureSummary { parcel_count: number; graded_count: number; landlocked_count: number; structure_spread: number; corner_count: number }
// 지도 분석 — 화면 범위의 길을 「목 좋은 길」 3단계로 (backend/app/services/street_grade.py)
export interface StreetSegment { grade: 1 | 2 | 3; top_pct: number; highway: string | null; path: [number, number][] }
export interface StreetGradeResponse { overview: boolean; segments: StreetSegment[]; caveats: string[] }
export interface ParcelStructureResponse { summary: ParcelStructureSummary; parcels: ParcelStructure[]; caveats: string[] }
/** 계획 단계 택지 — 아직 없는 길 위에서 낸 획지 등급. */
export interface PlannedSiteRequest {
  roads: { coordinates: number[][]; road_type: string; name?: string; width_m?: number | null }[];
  lots: { lot_id: string; geometry: { type: string; coordinates: unknown } }[];
  total_amount_manwon?: number | null;
  include_existing_roads?: boolean;
  centre_lat?: number | null;
  centre_lng?: number | null;
}
/** 획지의 한 변(동·서·남·북)이 닿은 길. 값은 이 도면 안 도로 구간끼리의 백분위. */
export interface PlannedLotSide {
  direction: string; length_m: number; integration_pct: number; choice_pct: number; score: number;
  footway: boolean; reasons: string[]; edge?: { type: string; coordinates: unknown } | null;
}
export interface PlannedLotNeighbor { lot_id: string; distance_m: number; relation: string }
/** 상가를 낼 수 없는 땅(학교·아파트)은 순위에서 빠지고 옆 상가 땅의 「근처」로만 나온다. */
export interface PlannedLotNearby { lot_id: string; kind: "school" | "housing" | "public"; distance_m: number }
/** 지어진 신도시 10곳에서 배운 「가게가 들어설 가능성」 — 그 획지가 든 블록 단위 (backend plan_model.py). */
export interface PlanScore {
  block_area_m2: number; score: number; expected_stores: number; commercial_share: number; top_pct: number;
  features: Record<string, number>;
}
export interface PlannedLot extends ParcelStructure {
  sides: PlannedLotSide[]; neighbors: PlannedLotNeighbor[]; best_side_score?: number | null;
  kind: "candidate" | "school" | "housing" | "public"; nearby: PlannedLotNearby[]; plan?: PlanScore | null;
}
/** 길 구간 하나의 값(0~100, 이 도면 안 길끼리 견줌). 2026-11-06 — 블록 안 길마다 색을 칠한다. */
export interface PlannedRoadScore { coordinates: number[][]; score: number; planned: boolean }
export interface PlannedSiteResponse {
  summary: ParcelStructureSummary; lots: PlannedLot[]; warnings: string[]; caveats: string[];
  roads?: PlannedRoadScore[];
}
export interface PlanLayer { name: string; entity_count: number; total_length: number; closed_count: number; guess: "road" | "lot" | "other" }
export interface PlanCrsCandidate { epsg: string; lat: number; lng: number }
/** 계획 도면 DXF에서 꺼낸 길·획지. roads·lots는 그대로 plannedSite에 넣는다. */
export interface PlannedSiteExtractResponse {
  roads: PlannedSiteRequest["roads"];
  lots: PlannedSiteRequest["lots"];
  layers: PlanLayer[];
  crs: string;
  crs_candidates: PlanCrsCandidate[];
  centre_lat: number | null;
  centre_lng: number | null;
  notes: string[];
}

export interface FloorplanLine { x1: number; y1: number; x2: number; y2: number }
export interface FloorplanLabel { text: string; x: number; y: number }
export type FloorplanSymbolKind = "stair" | "elevator" | "entrance" | "exit" | "other";
export interface FloorplanSymbol { kind: FloorplanSymbolKind; name: string; x: number; y: number }
export type FloorplanLayerGuess = "wall" | "opening" | "text" | "symbol" | "other";
/** 도면의 레이어 하나. 자동 추측이 틀릴 수 있어 사람이 고를 수 있게 통계를 함께 준다. */
export interface FloorplanLayer { name: string; entity_count: number; segment_count: number; total_length: number; median_length: number; text_count: number; block_count: number; guess: FloorplanLayerGuess; guessed_by: "name" | "size" | "none" }
export interface FloorplanXref { name: string; path: string }
export interface FloorplanExtractResponse { canvas_width: number; canvas_height: number; lines: FloorplanLine[]; source: "dxf"; openings?: FloorplanLine[]; meters_per_unit?: number | null; dxf_units?: string | null; layers?: FloorplanLayer[]; xrefs?: FloorplanXref[]; layers_guessed?: boolean; suspect_wall_layers?: string[]; auto_notes?: string[]; labels?: FloorplanLabel[]; symbols?: FloorplanSymbol[]; warnings?: string[] }
export interface FloorplanAnalyzeRequest { canvas_width: number; canvas_height: number; lines: FloorplanLine[]; openings?: FloorplanLine[]; meters_per_unit?: number | null; labels?: FloorplanLabel[] }
export interface FloorplanCell { row: number; col: number; x: number; y: number; integration: number; connectivity: number; integration_local: number; mean_depth: number; choice: number; /** 이 칸이 속한 통로 폭(m, 축척을 알 때) — 로봇 통로 판정용 */ aisle_width_m?: number | null }
/** 설명이 가리킬 수 있는 자리 하나 — 캔버스 좌표와, 축척을 알 때는 m 좌표. */
export interface FloorplanSpot { x: number; y: number; value: number; percentile: number; x_m?: number | null; y_m?: number | null }
export interface FloorplanSummary {
  cols: number; rows: number; cell_size: number; cell_count: number;
  integration_mean: number; integration_max: number; connectivity_mean: number; coverage_pct: number;
  integration_local_mean: number; mean_depth_mean: number; choice_max: number; choice_sampled: boolean;
  cell_size_m?: number | null;
  best_spots: FloorplanSpot[]; through_spots: FloorplanSpot[]; dead_spots: FloorplanSpot[];
  /** 로봇 통로(2026-11-09): 통로 폭이 robot_aisle_min_m 이상인 바닥 비율(%) — 축척을 알 때만 */
  robot_passable_pct?: number | null; robot_aisle_min_m?: number; robot_dead_ends?: FloorplanSpot[];
}
export interface FloorplanExcludedCell { row: number; col: number }
export type FloorplanIssueSeverity = "high" | "medium" | "low";
/** 도면에서 찾은 문제 하나. 기본 뷰는 지표가 아니라 이 목록이다. */
export interface FloorplanIssue { severity: FloorplanIssueSeverity; title: string; detail: string; x: number; y: number; x_m?: number | null; y_m?: number | null; cell_count: number; area_m2?: number | null; evidence: string; cells?: number[][]; /** "best_zone"=가장 목이 좋은 구역(문제 아님), "dead_zone"=외진 구역 */ kind?: string }
/** 벽으로 닫힌 공간 하나 — 매장별 표의 한 줄. */
export interface FloorplanRoom { index: number; name?: string | null; cell_count: number; area_m2?: number | null; x: number; y: number; x_m?: number | null; y_m?: number | null; integration_mean: number; choice_mean: number; connectivity_mean: number; integration_percentile: number; analyzed_pct: number }
export type FloorplanDeltaDirection = "better" | "worse" | "same";
export interface FloorplanDelta { key: string; label: string; before: number; after: number; change_pct: number; direction: FloorplanDeltaDirection; unit: string; note: string }
export interface FloorplanCompareRequest { canvas_width: number; canvas_height: number; meters_per_unit?: number | null; labels?: FloorplanLabel[]; before_lines: FloorplanLine[]; before_openings?: FloorplanLine[]; after_lines: FloorplanLine[]; after_openings?: FloorplanLine[] }
export interface FloorplanCompareResponse { deltas: FloorplanDelta[]; appeared: FloorplanIssue[]; resolved: FloorplanIssue[]; summary: string; before_summary: FloorplanSummary; after_summary: FloorplanSummary; warnings?: string[] }
/** 말로 고치기 (2026-11-09) — 고른 자리 + 한 문장 지시. box가 있으면 네모로 고른 것. */
export interface FloorplanEditIntentRequest { instruction: string; previous_instruction?: string | null; point: { x: number; y: number }; box?: FloorplanLine | null; canvas_width: number; canvas_height: number; meters_per_unit?: number | null; labels?: FloorplanLabel[]; lines: FloorplanLine[]; openings?: FloorplanLine[] }
export type FloorplanEditOp = "add_door" | "remove_wall" | "close_door" | "add_wall" | "clear_walls_in_box";
/** 더할 선과 지울 선 번호(요청에 보낸 배열 순서). question이 있으면 아무것도 안 바꾸고 되묻는 것. */
export interface FloorplanEditIntentResponse { steps: Array<{ op: FloorplanEditOp; text: string }>; add_lines: FloorplanLine[]; remove_line_indices: number[]; add_openings: FloorplanLine[]; remove_opening_indices: number[]; message: string; question?: string | null; source: "llm" | "rule"; warnings?: string[] }
export interface FloorplanChatMessage { role: "user" | "assistant"; content: string }
export interface FloorplanChatRequest { summary: FloorplanSummary; issues: FloorplanIssue[]; rooms?: FloorplanRoom[]; rule_text: string; narrative?: string | null; messages: FloorplanChatMessage[] }
/** card: 답이 가리키는 카드 번호(1부터) — 화면이 그 구역을 도면에 칠한다 */
export interface FloorplanChatResponse { text: string; source: string; card?: number | null }
export interface FloorplanAnalyzeResponse { summary: FloorplanSummary; cells: FloorplanCell[]; issues?: FloorplanIssue[]; rooms?: FloorplanRoom[]; excluded_cells?: FloorplanExcludedCell[]; explanation: { text: string; source: string }; warnings?: string[]; notes?: string[] }

/** multipart 업로드라 requestJson(항상 JSON Content-Type)을 쓸 수 없다 — FormData는
 * 브라우저가 boundary를 붙인 Content-Type을 직접 세팅해야 한다. */
async function requestUpload<T>(path: string, form: FormData, timeoutMs = 60_000): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, { method: "POST", body: form, signal: controller.signal });
    const data = await response.json().catch(() => null) as ApiErrorPayload | null;
    if (!response.ok) throw new ApiClientError(errorMessage(data, response.status), response.status, data);
    return data as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("요청 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.");
    }
    if (error instanceof TypeError) {
      throw new Error("분석 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export const api = {
  analyze: (body: AnalyzeRequest) => requestJson<AnalyzeResponse>("/api/analyze", { method: "POST", body: JSON.stringify(body) }),
  decide: (body: DecideRequest) => requestJson<DecideResponse>("/api/decide", { method: "POST", body: JSON.stringify(body) }),
  leaseVerdict: (body: LeaseVerdictRequest) => requestJson<LeaseVerdictResponse>("/api/lease-verdict", { method: "POST", body: JSON.stringify(body) }),
  purchaseVerdict: (body: PurchaseVerdictRequest) => requestJson<PurchaseVerdictResponse>("/api/purchase-verdict", { method: "POST", body: JSON.stringify(body) }),
  diagnose: (body: DiagnoseRequest) => requestJson<DiagnoseResponse>("/api/diagnose", { method: "POST", body: JSON.stringify(body) }),
  areaSearch: (body: AreaSearchRequest) =>
    requestJson<AreaSearchResponse>("/api/area-search", { method: "POST", body: JSON.stringify(body) }).then(
      (result) => withAreaSearchRegionLabel(result, body.region_query),
    ),
  areaInsights: (body: { lat: number; lng: number; radius_m: number }) => requestJson<AreaInsightsResponse>("/api/area-insights", { method: "POST", body: JSON.stringify(body), timeoutMs: 20_000 }),
  profitabilityAnalyze: (body: ProfitabilityAnalyzeRequest) => requestJson<ProfitabilityAnalyzeResponse>("/api/profitability/analyze", { method: "POST", body: JSON.stringify(body) }),
  siteScore: (body: SiteScoreRequest, init?: RequestInit) => requestJson<SiteScoreResponse>("/api/site-score", { method: "POST", body: JSON.stringify(body), ...init }),
  personalFit: (body: PersonalFitRequest) => requestJson<PersonalFit>("/api/personal-fit", { method: "POST", body: JSON.stringify(body), timeoutMs: 10_000 }),
  dashboard: (body: DashboardPointRequest) => requestJson<DashboardResponse>("/api/dashboard", { method: "POST", body: JSON.stringify(body) }),
  territory: (body: DashboardPointRequest) => requestJson<TerritoryAnalysisResponse | null>("/api/territory", { method: "POST", body: JSON.stringify(body) }),
  cannibalization: (body: DashboardPointRequest) => requestJson<CannibalizationResponse>("/api/cannibalization", { method: "POST", body: JSON.stringify(body) }),
  isochrone: (body: DashboardPointRequest) => requestJson<IsochroneResponse>("/api/isochrone", { method: "POST", body: JSON.stringify(body) }),
  chat: (body: ChatRequest) => requestJson<ChatResponse>("/api/chat", { method: "POST", body: JSON.stringify(body) }),
  realCompetition: (body: RealCompetitionRequest) => requestJson<RealCompetitionBundleResponse>("/api/real-competition", { method: "POST", body: JSON.stringify(body) }),
  leaseFeasibility: (body: LeaseFeasibilityRequest) => requestJson<LeaseFeasibilityResponse>("/api/lease-feasibility", { method: "POST", body: JSON.stringify(body) }),
  simulationContext: (body: SimulationContextRequest) => requestJson<SimulationContextResponse>("/api/simulation/context", { method: "POST", body: JSON.stringify(body) }),
  simulate: (body: SimulateRequest) => requestJson<SimulateResponse>("/api/simulate", { method: "POST", body: JSON.stringify(body) }),
  undervaluedAreas: (limit = 20) =>
    requestJson<UndervaluedAreasResponse>(`/api/undervalued-areas?limit=${limit}`),
  undervaluedParcels: (admCd: string, limit = 20) =>
    requestJson<UndervaluedParcelsResponse>(
      `/api/undervalued-parcels?adm_cd=${encodeURIComponent(admCd)}&limit=${limit}`,
    ),
  // 반경 상한(600m)은 서버가 강제한다 — 전 서울 89만 폴리곤을 GeoJSON으로
  // 내리면 5초 룰을 못 지키기 때문이다. 넓게 보려면 지도를 움직여 다시 부른다.
  mapParcels: (lng: number, lat: number, radiusM = 400) =>
    requestJson<MapParcelsResponse>(
      `/api/map-parcels?lng=${lng}&lat=${lat}&radius_m=${radiusM}`,
    ),
  /** 필지 하나의 판정 전체. 화면·서술·도구가 이 응답 하나만 본다.
   *  없는 필지는 404다 — 지어낸 값을 주지 않는다. */
  parcelAnalysis: (pnu: string, opts?: { baseYear?: number; narrative?: "static" | "llm" }) =>
    requestJson<StructuredAnalysis>(
      `/api/parcel-analysis?pnu=${encodeURIComponent(pnu)}` +
        `&base_year=${opts?.baseYear ?? 2026}` +
        `&narrative=${opts?.narrative ?? "static"}`,
    ),
  upjongTree: () => requestJson<UpjongTreeResponse>("/api/upjong/tree"),
  upjongSearch: (q: string, limit = 30) => requestJson<UpjongSearchResult[]>(`/api/upjong/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  clinicSubjects: () => requestJson<ClinicSubjectsResponse>("/api/clinic-subjects"),
  franchiseBrands: (q: string, limit = 10) => requestJson<FranchiseBrandsResponse>(`/api/franchise/brands?q=${encodeURIComponent(q)}&limit=${limit}`),
  franchiseBrandDetail: (name: string) => requestJson<FranchiseBrandDetail>(`/api/franchise/brand-detail?name=${encodeURIComponent(name)}`),
  industryProfiles: () => requestJson<AdaptiveProfileSummary[]>("/api/industry-profiles"),
  industryProfile: (industryId: string, userType: AdaptiveUserType) => requestJson<AdaptiveIndustryProfile>(`/api/industry-profiles/${encodeURIComponent(industryId)}?user_type=${encodeURIComponent(userType)}`),
  agentAsk: (body: AgentAskRequest) =>
    requestJson<AgentAskResponse>("/api/agent/ask", { method: "POST", body: JSON.stringify(body) }).then(
      withAgentAskAreaSearchRegionLabel,
    ),
  // 2단계 렌더의 두 번째 단계 — 이미 받은 분석으로 서술만 채운다(엔진 재실행 없음).
  agentAnswer: (body: AgentAnswerRequest) => requestJson<AgentAnswerResponse>("/api/agent/answer", { method: "POST", body: JSON.stringify(body), timeoutMs: 35_000 }),
  /** 레이어를 고쳐 다시 읽을 때 roles를 함께 보낸다 — 파일은 서버에 두지 않아 매번 올린다. */
  /** 블록 하나의 필지를 길 구조로 줄 세운다. 총액을 넣으면 배분 금액도 온다. */
  /** 계획 도로망 + 획지를 보내 그 자리에서 등급을 받는다. 기존 도로를 함께 보면 느리다. */
  plannedSite: (body: PlannedSiteRequest) =>
    requestJson<PlannedSiteResponse>("/api/planned-site", { method: "POST", body: JSON.stringify(body), timeoutMs: 30_000 }),
  plannedSiteExtract: (file: File, opts?: { crs?: string; roadLayers?: string[]; lotLayers?: string[] }) => {
    const form = new FormData();
    form.append("file", file);
    if (opts?.crs) form.append("crs", opts.crs);
    if (opts?.roadLayers) form.append("road_layers", opts.roadLayers.join(","));
    if (opts?.lotLayers) form.append("lot_layers", opts.lotLayers.join(","));
    return requestUpload<PlannedSiteExtractResponse>("/api/planned-site/extract", form);
  },
  streetGrades: (bbox: { west: number; south: number; east: number; north: number }, signal?: AbortSignal) =>
    requestJson<StreetGradeResponse>("/api/street-grades", { method: "POST", body: JSON.stringify(bbox), timeoutMs: 20_000, signal }),
  parcelStructure: (body: { lat: number; lng: number; radius_m?: number; pnus?: string[]; total_amount_manwon?: number | null }) =>
    requestJson<ParcelStructureResponse>("/api/parcel-structure", { method: "POST", body: JSON.stringify(body), timeoutMs: 20_000 }),
  floorplanExtract: (file: File, roles?: { wall: string[]; opening: string[] }) => {
    const form = new FormData();
    form.append("file", file);
    if (roles) {
      form.append("wall_layers", roles.wall.join(","));
      form.append("opening_layers", roles.opening.join(","));
    }
    return requestUpload<FloorplanExtractResponse>("/api/floorplan/extract", form);
  },
  floorplanAnalyze: (body: FloorplanAnalyzeRequest) => requestJson<FloorplanAnalyzeResponse>("/api/floorplan/analyze", { method: "POST", body: JSON.stringify(body) }),
  /** 분석 뒤에 따로 부르는 LLM 해석. 실패·꺼짐이면 source:"rule"로 규칙 문장이 돌아온다. */
  /** A/B — 원본과 고친 판을 한 번에 돌린다. 두 번 계산하므로 분석보다 느리다. */
  floorplanCompare: (body: FloorplanCompareRequest) =>
    requestJson<FloorplanCompareResponse>("/api/floorplan/compare", { method: "POST", body: JSON.stringify(body), timeoutMs: 30_000 }),
  /** 말로 고치기 — GPT가 명령을 고르고 서버가 선을 계산한다. 분석은 돌리지 않는다. */
  floorplanEditIntent: (body: FloorplanEditIntentRequest) =>
    requestJson<FloorplanEditIntentResponse>("/api/floorplan/edit-intent", { method: "POST", body: JSON.stringify({ ...body, lang: uiLang() }), timeoutMs: 25_000 }),
  floorplanChat: (body: FloorplanChatRequest) =>
    requestJson<FloorplanChatResponse>("/api/floorplan/chat", { method: "POST", body: JSON.stringify(body), timeoutMs: 40_000 }),
  /** 영어 모드 화면 번역(lib/autoTranslate.ts). 옮기지 못한 자리는 null. */
  translate: (texts: string[]) =>
    requestJson<{ translations: Array<string | null> }>("/api/translate", { method: "POST", body: JSON.stringify({ texts }), timeoutMs: 45_000 }),
  floorplanExplain: (body: { summary: FloorplanSummary; issues: FloorplanIssue[]; rule_text: string }) =>
    requestJson<{ text: string; source: string }>("/api/floorplan/explain", { method: "POST", body: JSON.stringify({ ...body, lang: uiLang() }), timeoutMs: 30_000 }),
};

export interface AgentStep { key: string; label: string; status: string }
/** 앞 턴 요약 — 이번 문장에 주소·업종이 빠져 있을 때 백엔드가 이어받을 최소 정보.
 *  전체 서술(answer)은 안 싣는다 — 턴이 쌓일수록 토큰이 커져 5초 룰을 위협한다. */
export interface AgentHistoryTurn {
  query: string;
  address: string;
  business_type: string;
  business_label: string;
  /** 프리셋 밖 업종(꽃집·세탁소 등)의 SBIZ 코드. business_type만으로는 "일반"인지
   *  "꽃집"인지 구분이 안 되므로, 후속 질문이 같은 업종으로 이어지려면 이 값이
   *  그대로 돌아가야 한다(백엔드 AgentHistoryTurn.upjong). */
  upjong?: Record<string, unknown> | null;
  /** 그 턴에서 뽑힌 수치(월세·매입가 등). 이걸 안 보내면 "월세 300 신촌 카페"
   *  다음 "15평이야"에서 월세 300이 사라진다. */
  params?: Record<string, number>;
  /** 그 턴에 화면이 보여 준 답 본문(2026-11-06). 후속 질문에 모델이 앞에서 무슨 말을
   *  했는지 알게 한다. 길면 서버가 자른다. */
  answer?: string;
}
export interface AgentAskRequest {
  query: string;
  radius_m?: number;
  include_answer?: boolean;
  /** 최근 턴만 보낸다(AGENT_HISTORY_LIMIT) — 전체를 보내면 대화가 길어질수록
   *  느려지고 토큰이 터진다. */
  history?: AgentHistoryTurn[];
  /** true면 site_score만 돌려 결정 카드를 2초 안팎에 받는다(도구 루프·곁들이 도구 생략). */
  fast?: boolean;
  /** false면 도구 루프(LLM이 도구를 고르는 18초)를 건너뛰고 규칙이 고른 도구만 돌린다. */
  loop?: boolean;
  /** false면 위치가 없는 질문에 글 답을 쓰지 않고 빈 talk 응답을 바로 준다 — 글은
   *  /ask/stream이 쓴다(같은 답을 두 번 쓰지 않게). */
  talk?: boolean;
}

/** AgentChat이 대화 상태로 들고 있는 history 배열에 이번 턴을 추가하고 최근
 *  limit개만 남긴다. 앞부분(오래된 턴)을 자른다 — 최근 대화가 다음 질문
 *  해석에 더 중요하기 때문. */
export const AGENT_HISTORY_LIMIT = 5;
export function appendAgentHistory(
  history: AgentHistoryTurn[],
  turn: AgentHistoryTurn,
  limit: number = AGENT_HISTORY_LIMIT,
): AgentHistoryTurn[] {
  return [...history, turn].slice(-limit);
}
export interface AgentAnswerRequest {
  /** true면 350자 안팎을 작은 모델로 빨리 쓴다(「자세히 보기」용). */
  brief?: boolean;
  site: SiteScoreResponse;
  place?: string;
  business_label?: string;
  radius_m?: number;
  /** 사용자가 실제로 물은 문장. 없으면 서술이 질문과 무관해진다 — '야간 장사',
   *  '20대 여성 타깃' 같은 조건이 통째로 사라진다. */
  query?: string;
  /** 질문에 맞춰 실제로 실행한 도구와 그 결과. 계약·매입·비교·진단을 돌렸으면
   *  그 판정이 답의 중심이 된다. */
  tool?: string;
  tool_result?: Record<string, unknown> | null;
  /** 앞 대화 턴들. 이게 없으면 좌표는 이어지는데 말은 안 이어진다 — 후속
   *  질문마다 같은 장소를 처음부터 다시 소개하는 답이 나온다. */
  history?: AgentHistoryTurn[];
}
export interface AgentAnswerResponse { answer: string; answer_source: string }
export interface AgentAskResponse {
  query: string;
  address: string;
  business_type: string;
  business_label: string;
  steps: AgentStep[];
  /** 위치를 특정하지 못해 도구를 돌리지 않은 답(mode="talk")에는 없다. */
  site?: SiteScoreResponse | null;
  answer: string;
  answer_source: string;
  /** "analyze" = 엔진을 돌려 보고서까지 낸 답. "talk" = 도구 없이 글로만 답한 것. */
  mode?: string;
  /** 질문을 좁혀서 분석했거나, 도구에 필요한 값이 모자랄 때의 한 줄 고지. */
  notice?: string;
  /** 실제로 실행한 도구("site_score" | "decide" | "lease_verdict" | …)와 그 결과.
   *  질문마다 도는 도구가 달라 결과 모양도 달라지므로 느슨한 형태로 둔다. */
  tool?: string;
  tool_result?: Record<string, unknown> | null;
  /** 문장에서 뽑힌 수치. 다음 턴의 history에 params로 실어 보내야 이어받는다. */
  parameters?: Record<string, number>;
  /** 프리셋 밖 업종의 SBIZ 코드. 다음 턴의 history에 그대로 실어 보낸다. */
  upjong?: Record<string, unknown> | null;
  /** 창업자 결정 카드(신호등 + 월세 상한). 규칙은 백엔드 services/founder_verdict.py. */
  founder?: FounderVerdict | null;
}

export interface FounderVerdict {
  signal: "go" | "conditional" | "no";
  headline: string;
  reason: string;
  rent_cap_man: number | null;
  rent_line: string;
  flow: "good" | "ok" | "weak";
  flow_label: string;
  competition: "low" | "high" | "very_high";
  competition_label: string;
  integration_top_pct: number;
  choice_top_pct: number;
  same_category_count: number | null;
  cautions: string[];
  disclaimer: string;
}

/** 저평가 판정 1단계 — 행정동 단위 탐색. 실제 판정 단위(필지)는 그 안에서 좁힌다. */
export interface UndervaluedArea {
  adm_cd: string;
  adm_nm: string;
  structure_score: number;
  structure_percentile: number;
  price_per_sqm_manwon: number | null;
  price_percentile: number | null;
  trade_sample_count: number;
  gap_score: number;
  computed_at: string;
}
export interface UndervaluedAreasResponse {
  areas: UndervaluedArea[];
  note: string;
}

/**
 * 저평가 판정 2단계 — 필지(PNU) 단위. 여기가 실제 판정 단위다.
 *
 * `official_price_per_sqm`은 **개별공시지가**(원/㎡)이지 시세가 아니다. 나지상정
 * 평가액이라 화면에서 "이 땅의 가격"으로 읽히게 두면 안 된다 — 판정의 본문은
 * 금액이 아니라 백분위다.
 */
export interface UndervaluedParcel {
  pnu: string;
  address: string;
  adm_cd: string;
  adm_nm: string | null;
  zone_group: string;
  land_category: string | null;
  area_sqm: number | null;
  road_side_name: string | null;
  structure_percentile: number;
  price_percentile: number;
  gap_score: number;
  official_price_per_sqm: number;
  stratum_level: string;
  stratum_size: number;
  base_year: number;
  lat: number | null;
  lng: number | null;
}
export interface UndervaluedParcelsResponse {
  parcels: UndervaluedParcel[];
  adm_cd: string;
  adm_nm: string | null;
  note: string;
}

/**
 * 지도에 그릴 필지 — 경계와 판정.
 *
 * `UndervaluedParcel`과 다르게 **판정 필드가 전부 nullable**이다. 지도는 땅
 * 전체를 그려야 하는데 판정된 필지만 주면 지도에 구멍이 뚫리고, 사용자는 그게
 * "판정 못 함"인지 "땅이 아님"인지 구분할 수 없다. 서버가 판정을 LEFT JOIN으로
 * 붙이는 이유이고, 그 계약을 백엔드 테스트가 지키고 있다.
 */
export interface MapParcel {
  pnu: string;
  address: string;
  geometry: { type: string; coordinates: unknown };
  land_category: string | null;
  zone_name: string | null;
  road_side_name: string | null;
  area_sqm: number | null;
  official_price_per_sqm: number | null;
  gap_score: number | null;
  structure_percentile: number | null;
  price_percentile: number | null;
  stratum_level: string | null;
  stratum_size: number | null;
  zone_group: string | null;
}
export interface MapParcelsResponse {
  parcels: MapParcel[];
  /** 판정이 있는 필지 수. 화면이 "회색은 판정 없음"을 설명할 근거다. */
  judged_count: number;
  /** 상한에 걸려 바깥 테두리가 잘렸는가. 조용히 자르면 구멍을 "땅이 아님"으로 읽는다. */
  truncated: boolean;
  note: string;
}

export { API_BASE_URL };
