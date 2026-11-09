/**
 * Structured Analysis — 필지 판정 화면이 보는 **유일한** 형태.
 *
 * 백엔드 `app/models/land_analysis.py`의 미러다. 두 쪽이 갈라지면 화면이 조용히
 * 거짓말을 하므로, 필드를 늘릴 때는 반드시 양쪽을 같이 고친다.
 *
 * 여기에 **표시 문구를 만드는 로직을 두지 않는다.** 제목(`verdict.headline`)·
 * 요인 라벨(`factors[].label`)·주의(`caveats`)는 백엔드가 완성해 보낸다. 프런트가
 * 임계값과 비교해 문구를 고르기 시작하면 규칙이 두 곳에 생기고, 그때부터 G7의
 * 캘리브레이션이 화면에 반영되지 않는다.
 *
 * 화면이 반드시 지켜야 하는 것 셋:
 *  1. `official_price.disclaimer`를 금액과 함께 보여준다 — 공시지가는 시세가 아니다.
 *  2. `expected_official_price`는 **범위로** 보여준다. `point`를 단독으로 찍지 않는다.
 *  3. `verdict.strength === "weak"`이면 단정하는 말투를 쓰지 않는다.
 */

export type VerdictLabel =
  | "undervalued"
  | "cheap_with_reason"
  | "aligned"
  | "above_peers"
  | "undetermined";

export type Strength = "strong" | "moderate" | "weak";
/** 왜 판정하지 않았는가. "비교군이 얇다"와 "모형이 아직 없다"는 다른 뜻이다. */
export type UndeterminedReason = "few_peers" | "no_estimate" | "missing_price";
export type ConfidenceLevel = "high" | "medium" | "low";
export type FactorDirection = "positive" | "negative";

export interface Subject {
  pnu: string;
  base_year: number;
  address: string;
  ldong_name: string | null;
  jibun: string | null;
  area_sqm: number | null;
  lat: number | null;
  lng: number | null;
}

export interface Attribute {
  key: string;
  label: string;
  value: string;
  note: string | null;
}

export interface PriceRange {
  low: number;
  high: number;
  /** 정렬·순위용 대표값. 화면에 단독으로 찍지 않는다. */
  point: number | null;
  unit: string;
}

export interface OfficialPrice {
  per_sqm: number;
  base_year: number;
  label: string;
  disclaimer: string;
}

export interface PeerGroup {
  description: string;
  size: number;
  level: string;
  criteria: string[];
  median_official_price_per_sqm: number | null;
}

export interface Factor {
  key: string;
  label: string;
  direction: FactorDirection;
  magnitude_label: string;
  /** 이 요인이 '싼 이유'로 설명되는 단점인가. */
  explains_discount: boolean;
  detail: string | null;
}

/** `total = explained + unexplained`가 정확히 성립한다. */
export interface DiscountBreakdown {
  total_pct: number;
  explained_pct: number;
  /** 단점을 다 감안해도 남는 몫. **이것이 저평가의 정의다.** */
  unexplained_pct: number;
}

export interface Confidence {
  score: number;
  level: ConfidenceLevel;
  peer_count: number;
  peer_similarity: number | null;
  model_uncertainty_pct: number | null;
  in_training_distribution: boolean | null;
  reasons: string[];
}

/**
 * 비교군 안에서의 자리. **추정 모형이 없어도 온다** — `current_valuation`과
 * 일부러 나눠 둔 이유가 그것이다(한 덩어리로 묶었더니 모형이 없는 동안 갭 점수
 * 까지 화면에서 사라졌다).
 */
export interface PeerPosition {
  price_percentile: number | null;
  structure_percentile: number | null;
  gap_score: number | null;
}

/**
 * 방법론 C — 지금 이 필지가 같은 조건 대비 낮은가 높은가.
 * **추정 모형이 있어야만 온다.** 없으면 null이고, 그때도 `official_price`와
 * `peer_position`은 남는다.
 */
export interface CurrentValuation {
  expected_official_price: PriceRange;
  discount: DiscountBreakdown;
  /** 싸다 — 이유를 따지지 않은 사실. */
  is_cheap: boolean;
  /** 저평가 — 설명되지 않는 할인분이 크다. `is_cheap`과 별개다. */
  is_undervalued: boolean;
}

/** 방법론 A — h=1·3·5년. */
export interface PriceForecastHorizon {
  years: number;
  expected_official_price: PriceRange;
  annualized_change_pct: number | null;
}

export interface PriceForecast {
  horizons: PriceForecastHorizon[];
}

/** 방법론 B — peer 대비 초과성장. */
export interface GrowthOutlook {
  horizon_years: number;
  own_growth_pct: number | null;
  peer_growth_pct: number | null;
  excess_growth_pct: number | null;
  hot_probability: number | null;
}

export interface RankCheck {
  unexplained_discount_rank_pct: number | null;
  gap_score_rank_pct: number | null;
  agreement: "agree" | "disagree" | "unknown";
}

export interface AuditTrail {
  model_version: string;
  rules_version: string;
  thresholds: Record<string, number>;
  rank_check: RankCheck;
  notes: string[];
}

export interface Verdict {
  label: VerdictLabel;
  headline: string;
  strength: Strength;
  confidence: Confidence;
  undetermined_reason: UndeterminedReason | null;
}

/**
 * 사람이 읽는 설명. **판정 값과 분리돼 있다** — `source`가 `llm`이든 `static`이든
 * 위의 판정 숫자는 같다.
 */
export interface Narrative {
  source: "static" | "llm";
  headline: string;
  paragraphs: string[];
}

export interface StructuredAnalysis {
  schema_version: string;
  subject: Subject;
  attributes: Attribute[];
  peer_group: PeerGroup;
  factors: Factor[];
  verdict: Verdict;
  /** 사실 — 추정 모형과 무관하게 DB에 있는 값이다. 판정을 보류해도 남는다. */
  official_price: OfficialPrice | null;
  peer_position: PeerPosition | null;
  current_valuation: CurrentValuation | null;
  growth_outlook: GrowthOutlook | null;
  price_forecast: PriceForecast | null;
  narrative: Narrative | null;
  caveats: string[];
  audit: AuditTrail;
}

/** 개발용 fixture. 백엔드 없이 화면을 만들 수 있게 public에 둔다. */
export const FIXTURE_URL = "/fixtures/structured-analysis.json";

export interface FixturePayload {
  generated_by: string;
  note: string;
  cases: Record<string, StructuredAnalysis>;
}

/**
 * 화면이 "확정 감정평가액"으로 읽히지 않게 하는 한 줄.
 *
 * 범위를 그리는 곳이 여러 군데라 문구가 갈라지기 쉬워서 여기 한 번만 둔다.
 */
export function priceRangeText(range: PriceRange): string {
  const fmt = (v: number) => Math.round(v).toLocaleString("ko-KR");
  return `${fmt(range.low)} ~ ${fmt(range.high)}${range.unit}`;
}

/**
 * 신뢰도가 낮으면 단정하지 않는다. 서술·화면이 어조를 고르는 단 하나의 기준.
 */
export function allowsAssertiveWording(verdict: Verdict): boolean {
  return verdict.strength !== "weak" && verdict.label !== "undetermined";
}

/**
 * 필지 클릭을 대화 스레드의 한 턴으로 바꾼다.
 *
 * 컴포넌트 안에 두면 테스트할 수 없어서 여기로 뺐다(브라우저에서 Kakao 폴리곤
 * 클릭을 합성 이벤트로 흉내낼 수 없다 — SDK가 자기 객체에 리스너를 단다).
 *
 * `lastPnu`는 **직전에 붙인 필지**다. 지도는 같은 폴리곤을 다시 눌러도 콜백을
 * 부르므로, 그대로 두면 같은 카드가 계속 쌓인다. 다른 필지면 새 턴이고 같은
 * 필지면 아무 일도 없다 — 어느 경우에도 **스레드를 지우지 않는다**(진행 중인
 * 대화가 사라지면 안 된다).
 */
export interface ParcelTurn {
  query: string;
  verdict: string;
  landAnalysis: StructuredAnalysis;
}

export function parcelTurnFor(
  analysis: StructuredAnalysis | null | undefined,
  lastPnu: string | null,
): ParcelTurn | null {
  if (!analysis) return null;
  if (lastPnu === analysis.subject.pnu) return null;
  return {
    // 클릭도 결국 "이 땅 뭐야?"라는 질문이라 질문 자리에 그렇게 적는다.
    query: `${analysis.subject.address} — 이 땅 뭐야?`,
    verdict: analysis.verdict.headline,
    landAnalysis: analysis,
  };
}
