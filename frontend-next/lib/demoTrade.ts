/**
 * 데모용 상권분석 — **예시 데이터다.** 실제 모델·API 출력이 아니다.
 *
 * 지금 서비스의 상권분석은 결과를 네 섹션(입지 진단 / 업종 분석 / 시세·유동인구 /
 * 확장 분석)으로 내고, 도달권·잠식·시너지·추세·임대타당성이 그 뒤에 흩어져 있다.
 * 데모는 같은 재료를 **답하는 질문 여섯 개**로 다시 묶는다 — 토지가치 화면과
 * 같은 규칙이다.
 *
 * 숫자는 업종마다 다르게 나오되 **고정 표에서 나온다.** 난수를 쓰지 않는 이유는
 * 발표 중에 같은 업종을 두 번 눌렀을 때 숫자가 달라지면 안 되기 때문이고, 손으로
 * 적은 값이 난수보다 그럴듯하기 때문이다.
 *
 * ⚠️ 표기: 매출은 **추정**이고 손익분기는 **입력한 가정 위의 계산**이다. 어느
 * 쪽도 "이만큼 번다"가 아니다. 화면이 그렇게 읽히지 않도록 단위와 말꼬리를
 * 여기서 고정한다.
 */

import type { RankedFactor } from "./demoParcels";

/* ── 모양 ─────────────────────────────────────────────────────────────── */

/** 도보 도달권 한 겹. 서비스의 isochrone band에 대응한다. */
export interface ReachBand {
  minutes: number;
  /** 도달 면적 (ha) */
  areaHa: number;
  /** 배후 추정 인구 (명) */
  population: number;
  /** 도달권 안 전체 점포 수 */
  stores: number;
  /** 그중 동일 업종 */
  sameCategory: number;
}

export interface HourSlice {
  hour: number;
  /** 시간대 유동인구 (명) */
  weekday: number;
  weekend: number;
}

export interface Slice {
  label: string;
  pct: number;
}

/** 경쟁 점포. **실재하는 상호를 쓰지 않는다** — 지어낸 평가를 실제 가게에 붙이면
 *  데모가 아니라 명예훼손이다. 거리와 성격만 쓴다. */
export interface Competitor {
  label: string;
  distanceM: number;
  /** "동일 업종" | "유사 업종" */
  kind: string;
  note: string;
}

export type Relation = "보완" | "경쟁" | "중립";

export interface SynergyItem {
  label: string;
  count: number;
  relation: Relation;
}

export interface QuarterPoint {
  quarter: string;
  /** 동일 업종 점포 수 */
  sameCategory: number;
  /** 상권 전체 점포 수 */
  total: number;
}

export interface StreetSignal {
  name: string;
  label: string;
  detail: string;
}

export interface AnalogArea {
  name: string;
  /** 0~1 */
  similarity: number;
  perStoreMonthlyMan: number;
  storeCount: number;
}

export interface TradeAnalysis {
  regionLabel: string;
  spotLabel: string;
  industry: string;

  /** 상권 매출의 집계 범위. 숫자만 내고 범위를 안 밝히면 "38억이 어디 얘기냐"를 묻지 못한다. */
  trdarName: string;
  trdarAreaHa: number;
  trdarStores: number;
  /** 원자료 기준 — 우리마을가게 `당월_매출_금액`은 이름과 달리 분기 합계다. 월로 낼 때 ÷3. */
  salesBasis: string;

  /** 0~100 */
  totalScore: number;
  grade: string;
  /** 한 줄 판정. */
  verdict: string;

  /** 입지 진단 6항목. */
  subscores: RankedFactor[];
  /** 공간 구조 — Space Syntax를 전용 탭으로 만들지 않는다. */
  spatial: RankedFactor[];
  transitLabel: string;
  transitM: number;
  busStops: number;

  reach: ReachBand[];
  footfall: {
    hourly: HourSlice[];
    ages: Slice[];
    days: Slice[];
    /** 주중 최대 시간대 */
    peakHour: number;
    /** 유동인구의 주말 비중 % */
    weekendSharePct: number;
    /** 서울 대비 백분위 */
    percentile: number;
  };

  sameCategoryCount: number;
  /** 잠식 위험 % */
  cannibalPct: number;
  cannibalLabel: string;
  competitors: Competitor[];
  synergy: SynergyItem[];

  /** 상권 월 추정매출 (억원) */
  trdarMonthlyEok: number;
  /** 점포당 월 추정매출 (만원) */
  perStoreMonthlyMan: number;
  /** 서울 점포당 중앙값 (만원) */
  seoulMedianPerStoreMan: number;
  /** 매출의 주말 비중 % */
  weekendSalesPct: number;

  /** 적정 월 임대료 밴드 (만원) */
  rentLowMan: number;
  rentHighMan: number;
  /** 손익분기 월매출 (만원) */
  breakEvenMonthlyMan: number;
  /** 손익분기 일매출 (만원) */
  breakEvenDailyMan: number;
  /** 임대료 부담률 % — 추정매출 대비 */
  rentBurdenPct: number;
  /** 손익분기에 필요한 하루 객수 */
  ordersPerDay: number;
  /** 가정 객단가 (원) */
  ticketWon: number;

  trend: QuarterPoint[];
  /** 동일 업종 점포 수 증감률 % (8분기) */
  categoryGrowthPct: number;
  /** 상권 전체 점포 수 증감률 % */
  totalGrowthPct: number;
  streetSignals: StreetSignal[];
  analogs: AnalogArea[];
}

/* ── 업종별 고정 표 ───────────────────────────────────────────────────── */

interface IndustryProfile {
  totalScore: number;
  grade: string;
  verdict: string;
  /** 도보 5분 도달권의 동일 업종 수 — 나머지 겹은 여기서 파생한다. */
  sameCategory5m: number;
  cannibalPct: number;
  trdarMonthlyEok: number;
  perStoreMonthlyMan: number;
  seoulMedianPerStoreMan: number;
  weekendSalesPct: number;
  rentLowMan: number;
  rentHighMan: number;
  breakEvenMonthlyMan: number;
  rentBurdenPct: number;
  ticketWon: number;
  categoryGrowthPct: number;
  peakHour: number;
  /** 유동인구 시간대 모양 — 24시간 가중치. */
  shape: "day" | "meal" | "night" | "allday" | "evening";
  ages: Slice[];
  synergy: SynergyItem[];
  signals: StreetSignal[];
  /** 입지 진단 6항목의 백분위. 순서는 SUBSCORE_LABELS와 같다. */
  subPct: [number, number, number, number, number, number];
}

const SUBSCORE_LABELS = [
  "유동인구",
  "배후 거주·직장",
  "접근성",
  "경쟁 강도",
  "임대료 수준",
  "공간 구조",
] as const;

/** 신촌권 연세로 일대에 실제로 있을 법한 업종별 성격을 손으로 적었다. */
const INDUSTRY: Record<string, IndustryProfile> = {
  카페: {
    totalScore: 82,
    grade: "A",
    verdict: "유동인구는 최상위, 다만 동일 업종 밀도가 높아 차별화가 전제다",
    sameCategory5m: 34,
    cannibalPct: 41,
    trdarMonthlyEok: 38.2,
    perStoreMonthlyMan: 2_180,
    seoulMedianPerStoreMan: 1_640,
    weekendSalesPct: 38,
    rentLowMan: 320,
    rentHighMan: 430,
    breakEvenMonthlyMan: 1_780,
    rentBurdenPct: 17.4,
    ticketWon: 6_800,
    categoryGrowthPct: 12.4,
    peakHour: 15,
    shape: "day",
    ages: [
      { label: "20대", pct: 44 },
      { label: "30대", pct: 24 },
      { label: "40대", pct: 15 },
      { label: "50대 이상", pct: 17 },
    ],
    synergy: [
      { label: "학원·독서실", count: 22, relation: "보완" },
      { label: "의류·잡화", count: 41, relation: "보완" },
      { label: "카페", count: 34, relation: "경쟁" },
      { label: "제과·베이커리", count: 9, relation: "경쟁" },
    ],
    signals: [
      { name: "연세로", label: "동일 업종 순증", detail: "8분기 +12.4% — 신규 진입이 계속된다" },
      { name: "명물길", label: "점포 회전 빠름", detail: "폐업·개업이 함께 늘어 순증은 완만하다" },
    ],
    subPct: [96, 71, 88, 22, 34, 94],
  },
  한식: {
    totalScore: 74,
    grade: "B+",
    verdict: "점심 수요가 두껍지만 저녁 체류가 짧아 객단가 설계가 관건이다",
    sameCategory5m: 26,
    cannibalPct: 33,
    trdarMonthlyEok: 44.6,
    perStoreMonthlyMan: 2_640,
    seoulMedianPerStoreMan: 2_310,
    weekendSalesPct: 31,
    rentLowMan: 280,
    rentHighMan: 380,
    breakEvenMonthlyMan: 2_240,
    rentBurdenPct: 14.1,
    ticketWon: 11_500,
    categoryGrowthPct: 3.1,
    peakHour: 12,
    shape: "meal",
    ages: [
      { label: "20대", pct: 33 },
      { label: "30대", pct: 27 },
      { label: "40대", pct: 21 },
      { label: "50대 이상", pct: 19 },
    ],
    synergy: [
      { label: "사무·업무시설", count: 18, relation: "보완" },
      { label: "카페", count: 34, relation: "보완" },
      { label: "한식", count: 26, relation: "경쟁" },
      { label: "분식", count: 14, relation: "경쟁" },
    ],
    signals: [
      { name: "연세로", label: "점심 집중", detail: "11~13시에 유동의 28%가 몰린다" },
      { name: "창천동 골목", label: "임대료 완만", detail: "이면도로 임대료가 대로변의 62% 수준이다" },
    ],
    subPct: [88, 79, 74, 41, 58, 86],
  },
  "치킨·주류": {
    totalScore: 79,
    grade: "A−",
    verdict: "야간 유동이 서울 상위 3%다 — 심야 운영을 못 하면 상권의 절반을 버린다",
    sameCategory5m: 31,
    cannibalPct: 46,
    trdarMonthlyEok: 51.3,
    perStoreMonthlyMan: 3_120,
    seoulMedianPerStoreMan: 2_480,
    weekendSalesPct: 47,
    rentLowMan: 300,
    rentHighMan: 410,
    breakEvenMonthlyMan: 2_580,
    rentBurdenPct: 12.8,
    ticketWon: 24_000,
    categoryGrowthPct: -2.6,
    peakHour: 21,
    shape: "night",
    ages: [
      { label: "20대", pct: 52 },
      { label: "30대", pct: 25 },
      { label: "40대", pct: 13 },
      { label: "50대 이상", pct: 10 },
    ],
    synergy: [
      { label: "노래연습장", count: 11, relation: "보완" },
      { label: "당구·오락", count: 7, relation: "보완" },
      { label: "치킨·주류", count: 31, relation: "경쟁" },
      { label: "편의점", count: 16, relation: "중립" },
    ],
    signals: [
      { name: "연세로", label: "심야 유동 상위", detail: "22~02시 유동인구가 서울 상위 3%다" },
      { name: "신촌역 2번 출구", label: "동일 업종 감소", detail: "8분기 −2.6% — 폐업이 개업을 앞선다" },
    ],
    subPct: [97, 62, 91, 14, 39, 92],
  },
  편의점: {
    totalScore: 71,
    grade: "B",
    verdict: "24시간 유동은 좋으나 반경 안 동일 브랜드 밀도가 임계에 가깝다",
    sameCategory5m: 16,
    cannibalPct: 58,
    trdarMonthlyEok: 22.8,
    perStoreMonthlyMan: 1_420,
    seoulMedianPerStoreMan: 1_380,
    weekendSalesPct: 34,
    rentLowMan: 260,
    rentHighMan: 340,
    breakEvenMonthlyMan: 1_310,
    rentBurdenPct: 19.6,
    ticketWon: 5_400,
    categoryGrowthPct: 6.8,
    peakHour: 18,
    shape: "allday",
    ages: [
      { label: "20대", pct: 47 },
      { label: "30대", pct: 23 },
      { label: "40대", pct: 16 },
      { label: "50대 이상", pct: 14 },
    ],
    synergy: [
      { label: "고시원·원룸", count: 29, relation: "보완" },
      { label: "치킨·주류", count: 31, relation: "보완" },
      { label: "편의점", count: 16, relation: "경쟁" },
      { label: "슈퍼마켓", count: 4, relation: "경쟁" },
    ],
    signals: [
      { name: "연세로", label: "동일 브랜드 근접", detail: "도보 5분 안에 같은 브랜드 3곳이 있다" },
      { name: "대현동", label: "1인 가구 비중", detail: "원룸·고시원 밀도가 서울 상위 9%다" },
    ],
    subPct: [94, 84, 86, 9, 46, 88],
  },
  학원: {
    totalScore: 68,
    grade: "B",
    verdict: "대학가라 성인 대상은 맞지만 초·중등 배후는 얇다 — 대상 연령이 판정을 가른다",
    sameCategory5m: 22,
    cannibalPct: 29,
    trdarMonthlyEok: 18.4,
    perStoreMonthlyMan: 1_960,
    seoulMedianPerStoreMan: 2_050,
    weekendSalesPct: 22,
    rentLowMan: 240,
    rentHighMan: 330,
    breakEvenMonthlyMan: 1_620,
    rentBurdenPct: 15.2,
    ticketWon: 280_000,
    categoryGrowthPct: 8.9,
    peakHour: 19,
    shape: "evening",
    ages: [
      { label: "20대", pct: 51 },
      { label: "30대", pct: 22 },
      { label: "40대", pct: 16 },
      { label: "50대 이상", pct: 11 },
    ],
    synergy: [
      { label: "카페", count: 34, relation: "보완" },
      { label: "서점·문구", count: 6, relation: "보완" },
      { label: "학원", count: 22, relation: "경쟁" },
      { label: "치킨·주류", count: 31, relation: "중립" },
    ],
    signals: [
      { name: "연세로", label: "성인 대상 우세", detail: "어학·자격 학원이 22곳 중 15곳이다" },
      { name: "창천동", label: "초·중등 배후 얇음", detail: "반경 500m 학령인구가 서울 하위 24%다" },
    ],
    subPct: [86, 58, 81, 52, 61, 84],
  },
  미용: {
    totalScore: 76,
    grade: "B+",
    verdict: "20대 여성 유동이 서울 상위 5%다 — 재방문 설계가 되면 강한 자리다",
    sameCategory5m: 19,
    cannibalPct: 35,
    trdarMonthlyEok: 16.9,
    perStoreMonthlyMan: 1_740,
    seoulMedianPerStoreMan: 1_520,
    weekendSalesPct: 41,
    rentLowMan: 250,
    rentHighMan: 340,
    breakEvenMonthlyMan: 1_480,
    rentBurdenPct: 16.9,
    ticketWon: 42_000,
    categoryGrowthPct: 14.2,
    peakHour: 16,
    shape: "day",
    ages: [
      { label: "20대", pct: 55 },
      { label: "30대", pct: 24 },
      { label: "40대", pct: 12 },
      { label: "50대 이상", pct: 9 },
    ],
    synergy: [
      { label: "의류·잡화", count: 41, relation: "보완" },
      { label: "화장품", count: 12, relation: "보완" },
      { label: "미용", count: 19, relation: "경쟁" },
      { label: "네일·에스테틱", count: 8, relation: "경쟁" },
    ],
    signals: [
      { name: "연세로", label: "동일 업종 순증", detail: "8분기 +14.2%로 여섯 업종 중 가장 빠르다" },
      { name: "명물길", label: "체류 시간 김", detail: "평균 체류가 인접 상권보다 18분 길다" },
    ],
    subPct: [93, 66, 85, 44, 51, 90],
  },
};

/* ── 시간대 유동 모양 ─────────────────────────────────────────────────── */

/** 업종 성격별 24시간 가중치. 합이 아니라 모양만 정한다. */
const SHAPES: Record<IndustryProfile["shape"], number[]> = {
  // 낮에 완만하게 오르고 오후에 정점.
  day: [4, 3, 2, 2, 2, 3, 6, 12, 20, 28, 38, 52, 64, 70, 76, 82, 78, 71, 60, 48, 36, 26, 16, 8],
  // 점심·저녁 두 봉우리.
  meal: [3, 2, 2, 2, 2, 4, 9, 18, 26, 32, 44, 74, 82, 58, 42, 40, 46, 62, 74, 66, 48, 32, 18, 8],
  // 밤이 본체.
  night: [46, 34, 20, 10, 6, 4, 5, 9, 14, 18, 22, 30, 36, 34, 32, 36, 44, 56, 70, 84, 92, 96, 82, 64],
  // 하루 종일 평평하고 저녁에 살짝.
  allday: [22, 16, 12, 10, 10, 14, 24, 38, 46, 48, 50, 58, 62, 58, 56, 58, 64, 72, 76, 70, 62, 52, 40, 30],
  // 저녁에 몰린다.
  evening: [4, 3, 2, 2, 2, 3, 7, 14, 22, 26, 30, 36, 40, 38, 42, 52, 64, 78, 88, 82, 62, 40, 22, 10],
};

/* ── 지역 ─────────────────────────────────────────────────────────────── */

interface RegionProfile {
  label: string;
  spotLabel: string;
  /** 매출 집계의 범위 — 서울시 우리마을가게 상권 경계 이름. "38억"이 어디 얘기인지 밝힌다. */
  trdarName: string;
  trdarAreaHa: number;
  /** 그 경계 안 전체 업종 점포 수. 상권 매출 ÷ 이 수 = 점포당 매출. */
  trdarStores: number;
  /** 유동인구 규모 배율 — 상권 크기 차이다. */
  scale: number;
  percentile: number;
  transitLabel: string;
  transitM: number;
  busStops: number;
  spatial: RankedFactor[];
  analogs: AnalogArea[];
}

const REGION: Record<string, RegionProfile> = {
  sinchon: {
    label: "신촌권",
    spotLabel: "서대문구 창천동 연세로 일대",
    trdarName: "신촌역 발달상권",
    trdarAreaHa: 12.4,
    trdarStores: 175,
    scale: 1,
    percentile: 96,
    transitLabel: "신촌역 (2호선)",
    transitM: 180,
    busStops: 8,
    spatial: [
      { label: "Integration", value: "상위 6%", percentile: 94 },
      { label: "Choice", value: "상위 12%", percentile: 88 },
      { label: "최근접 간선도로", value: "연세로 · 42m" },
      { label: "가로 연결도", value: "상위 9%", percentile: 91 },
    ],
    analogs: [
      { name: "홍대입구역", similarity: 0.91, perStoreMonthlyMan: 2_640, storeCount: 1_842 },
      { name: "건대입구역", similarity: 0.86, perStoreMonthlyMan: 2_310, storeCount: 1_206 },
      { name: "성신여대입구", similarity: 0.79, perStoreMonthlyMan: 1_880, storeCount: 742 },
    ],
  },
  jongno: {
    label: "종로·을지로권",
    spotLabel: "종로구 관철동 젊음의거리 일대",
    trdarName: "종각역 발달상권",
    trdarAreaHa: 15.8,
    trdarStores: 217,
    scale: 1.24,
    percentile: 98,
    transitLabel: "종각역 (1호선)",
    transitM: 140,
    busStops: 12,
    spatial: [
      { label: "Integration", value: "상위 2%", percentile: 98 },
      { label: "Choice", value: "상위 4%", percentile: 96 },
      { label: "최근접 간선도로", value: "종로 · 28m" },
      { label: "가로 연결도", value: "상위 3%", percentile: 97 },
    ],
    analogs: [
      { name: "을지로3가", similarity: 0.88, perStoreMonthlyMan: 2_960, storeCount: 1_508 },
      { name: "명동", similarity: 0.83, perStoreMonthlyMan: 3_420, storeCount: 1_961 },
      { name: "광화문", similarity: 0.77, perStoreMonthlyMan: 3_140, storeCount: 1_122 },
    ],
  },
};

/* ── 조립 ─────────────────────────────────────────────────────────────── */

const GRADE_NOTE: Record<string, string> = {
  A: "여섯 업종 중 가장 높다",
  "A−": "상위권이다",
  "B+": "평균 위다",
  B: "평균 언저리다",
};

function round(n: number, unit = 1): number {
  return Math.round(n / unit) * unit;
}

/**
 * 지역 × 업종 → 상권분석 결과. 같은 입력이면 항상 같은 결과다(난수 없음).
 *
 * 모르는 업종이 들어오면 카페 프로필로 떨어뜨린다 — 데모가 빈 화면을 내는 것보다
 * 낫고, 어차피 셋업이 여섯 개만 고르게 한다.
 */
export function buildTradeAnalysis(regionId: string, industry: string): TradeAnalysis {
  const region = REGION[regionId] ?? REGION.sinchon;
  const p = INDUSTRY[industry] ?? INDUSTRY["카페"];
  const s = region.scale;

  const shape = SHAPES[p.shape];
  const hourly: HourSlice[] = shape.map((w, hour) => ({
    hour,
    weekday: round(w * 62 * s, 10),
    // 주말은 낮이 얇고 밤이 두껍다 — 대학가의 성질이다.
    weekend: round(w * 62 * s * (0.72 + (hour >= 17 ? 0.46 : 0) + (hour <= 8 ? -0.18 : 0)), 10),
  }));

  const reach: ReachBand[] = [5, 10, 15].map((minutes, i) => {
    const grow = [1, 3.1, 6.4][i];
    return {
      minutes,
      areaHa: round(12.4 * grow * s, 0.1),
      population: round(4_820 * grow * s, 10),
      stores: round(286 * grow * s, 1),
      sameCategory: round(p.sameCategory5m * grow, 1),
    };
  });

  const subscores: RankedFactor[] = SUBSCORE_LABELS.map((label, i) => ({
    label,
    value: percentileLabel(p.subPct[i]),
    percentile: p.subPct[i],
  }));

  // 8분기 추이 — 시작점에서 증감률만큼 선형으로 움직인다. 꺾은선의 뜻은
  // "몇 개였다"가 아니라 "늘고 있나 줄고 있나"이므로 모양만 정직하면 된다.
  const q0Same = round(p.sameCategory5m / (1 + p.categoryGrowthPct / 100), 1);
  const q0Total = round(286 * s * 0.94, 1);
  const trend: QuarterPoint[] = Array.from({ length: 8 }, (_, i) => {
    const t = i / 7;
    return {
      quarter: `${2024 + Math.floor(i / 4)}Q${(i % 4) + 1}`,
      sameCategory: round(q0Same + (p.sameCategory5m - q0Same) * t, 1),
      total: round(q0Total + (286 * s - q0Total) * t, 1),
    };
  });

  const perStore = round(p.perStoreMonthlyMan * s, 10);
  const breakEven = round(p.breakEvenMonthlyMan * s, 10);

  return {
    regionLabel: region.label,
    spotLabel: region.spotLabel,
    industry,

    trdarName: region.trdarName,
    trdarAreaHa: region.trdarAreaHa,
    trdarStores: round(region.trdarStores * s, 1),
    salesBasis: "2025년 4개 분기 카드 추정매출 평균 · 분기 합계 ÷ 3",

    totalScore: p.totalScore,
    grade: p.grade,
    verdict: p.verdict,

    subscores,
    spatial: region.spatial,
    transitLabel: region.transitLabel,
    transitM: region.transitM,
    busStops: region.busStops,

    reach,
    footfall: {
      hourly,
      ages: p.ages,
      days: [
        { label: "월~목", pct: 44 },
        { label: "금", pct: 19 },
        { label: "토", pct: 22 },
        { label: "일", pct: 15 },
      ],
      peakHour: p.peakHour,
      weekendSharePct: 37,
      percentile: region.percentile,
    },

    sameCategoryCount: p.sameCategory5m,
    cannibalPct: p.cannibalPct,
    cannibalLabel: cannibalLabel(p.cannibalPct),
    competitors: buildCompetitors(p, industry),
    synergy: p.synergy,

    trdarMonthlyEok: round(p.trdarMonthlyEok * s, 0.1),
    perStoreMonthlyMan: perStore,
    seoulMedianPerStoreMan: p.seoulMedianPerStoreMan,
    weekendSalesPct: p.weekendSalesPct,

    rentLowMan: round(p.rentLowMan * s, 10),
    rentHighMan: round(p.rentHighMan * s, 10),
    breakEvenMonthlyMan: breakEven,
    breakEvenDailyMan: round(breakEven / 26, 1),
    rentBurdenPct: p.rentBurdenPct,
    ordersPerDay: Math.round((breakEven * 10_000) / 26 / p.ticketWon),
    ticketWon: p.ticketWon,

    trend,
    categoryGrowthPct: p.categoryGrowthPct,
    totalGrowthPct: 6.4,
    streetSignals: p.signals,
    analogs: region.analogs,
  };
}

/** 거리만 다른 익명 경쟁 점포. 상호를 지어내지 않는다. */
function buildCompetitors(p: IndustryProfile, industry: string): Competitor[] {
  const near = p.synergy.filter((x) => x.relation === "경쟁");
  const dists = [40, 95, 160, 240];
  const out: Competitor[] = [];
  for (let i = 0; i < 4; i += 1) {
    const isSame = i < 2;
    out.push({
      label: `${isSame ? industry : (near[1]?.label ?? industry)} ${String.fromCharCode(65 + i)}`,
      distanceM: dists[i],
      kind: isSame ? "동일 업종" : "유사 업종",
      note:
        i === 0
          ? "같은 블록 · 도달권이 거의 겹친다"
          : i === 1
            ? "같은 가로 · 도달권 절반이 겹친다"
            : i === 2
              ? "이면도로 · 겹침이 얕다"
              : "도달권 가장자리",
    });
  }
  return out;
}

/* ── 표기 ─────────────────────────────────────────────────────────────── */

export function percentileLabel(percentile: number): string {
  const v = Math.round(percentile);
  return v >= 50 ? `상위 ${Math.max(1, 100 - v)}%` : `하위 ${Math.max(1, v)}%`;
}

/**
 * 잠식 위험 구간. 숫자 하나만 두면 41%와 46%가 다른 판정처럼 읽힌다 —
 * 구간으로 끊어 "그 차이에 뜻이 없다"를 화면이 말하게 한다.
 */
export function cannibalLabel(pct: number): string {
  if (pct >= 55) return "높음";
  if (pct >= 40) return "주의";
  if (pct >= 25) return "보통";
  return "낮음";
}

/** 만원 → "2,180만원". 상권 매출은 만원 단위로 읽는 쪽이 자연스럽다. */
export function manwon(man: number): string {
  return `${Math.round(man).toLocaleString("ko-KR")}만원`;
}

/** 억원 표기. 상권 전체 매출은 만원으로 쓰면 자릿수를 못 읽는다. */
export function eokwon(eok: number): string {
  return `${eok.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원`;
}

export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}시`;
}

export function gradeNote(grade: string): string {
  return GRADE_NOTE[grade] ?? "";
}

/** 점포당 매출이 서울 중앙값 대비 몇 %인지. 100이 중앙값이다. */
export function vsSeoulPct(perStore: number, median: number): number {
  return Math.round((perStore / median) * 100);
}
