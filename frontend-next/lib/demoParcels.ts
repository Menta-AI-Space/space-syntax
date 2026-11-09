/**
 * 데모용 필지 분석 — **예시 데이터다.** 실제 모델 출력이 아니다.
 *
 * 데모는 "서비스가 돌아가면 이렇게 보인다"를 보여주는 것이 목적이므로 백엔드나
 * DB에 붙지 않는다. 대신 **고정 시드**로 만들어 매번 같은 화면이 나오게 한다 —
 * 발표 중에 숫자가 바뀌면 설명한 것과 화면이 어긋난다.
 *
 * 지도(카카오맵 타일)와 좌표만 실제다. 필지 경계 폴리곤은 그리지 않는다 —
 * 합성한 경계는 실제 건물·도로와 어긋나서 지도 위에서 바로 티가 난다. 대신 실제
 * 좌표에 놓인 지점을 누르면 그 필지 분석이 열린다.
 *
 * 실제로 식별되는 건물 이름을 쓰지 않는다. 지어낸 저평가·고평가 판정을 실재하는
 * 건물에 붙이면 데모가 아니라 허위 평가가 된다 — 지번 표기만 쓴다.
 *
 * ⚠️ Gap의 부호가 이 파일의 핵심이다. PRD 정의는
 *
 *     Gap% = (모델 예측 공시지가 − 실제 개별공시지가) / 실제 × 100
 *
 * 이고 **양수가 상대적 저평가 후보**다. 뒤집으면 화면이 조용히 거짓말을 한다.
 */

/** 데모 무대 — PRD의 MVP 지역 중 신촌권. 연세로 일대. */
export const DEMO_CENTER = { lat: 37.5559, lng: 126.9372 } as const;

/** 분석 지역 후보 — 셋업 화면에서 고르는 것. PRD 10장의 MVP panel 범위다. */
export const DEMO_REGIONS = [
  { id: "sinchon", label: "신촌권", detail: "서대문구 창천동·대현동 · 2021~2026" },
  { id: "jongno", label: "종로·을지로권", detail: "종로구·중구 · 2021~2026" },
] as const;

/** 분석 종류 — 종류에 따라 그다음 받아야 하는 입력이 다르다. */
export const ANALYSIS_KINDS = [
  {
    id: "land",
    label: "토지가치분석",
    detail: "저평가·고평가 · 미래 공시지가 · 유망입지",
    /** 추가 입력이 없다 — 바로 실행으로 간다. */
    needsIndustry: false,
    needsPlan: false,
  },
  {
    id: "trade",
    label: "상권분석",
    detail: "입지 진단 · 경쟁·잠식 · 수익성",
    /** 업종을 받아야 한다. 업종이 없으면 무엇과 비교할지가 정해지지 않는다. */
    needsIndustry: true,
    needsPlan: false,
  },
  {
    id: "floorplan",
    label: "평면 공간구조 분석",
    detail: "얼마나 보이나 · 찾아오기 쉬운가 · 해석",
    needsIndustry: false,
    /**
     * 도면을 받아야 한다 — 이 분석의 입력은 지역이 아니라 **건물 한 층의 평면**이다.
     * 받는 형식은 DXF 벡터뿐이고 사진·스캔은 받지 않는다(`docs/ARCHITECTURE_RULES.md`).
     */
    needsPlan: true,
  },
] as const;

/** 셋업에서 고를 수 있는 예시 도면. 데모라 하나뿐이고, 업로드 자리를 대신한다. */
export const DEMO_PLANS = [
  { id: "office-1f", label: "오피스 1층", detail: "DXF · 벽 · 문/창 · 방 이름" },
] as const;

export type AnalysisKindId = (typeof ANALYSIS_KINDS)[number]["id"];

export const INDUSTRIES = ["카페", "한식", "치킨·주류", "편의점", "학원", "미용"] as const;

/**
 * Gap% → 색과 이름.
 *
 * 구간으로 끊는다. Gap%는 모델 잔차의 비율이라 ±2%p 차이에 뜻을 부여할 근거가
 * 없고, 매끄러운 그라데이션은 없는 정밀도를 있는 것처럼 보이게 한다.
 *
 * 고평가 쪽에 빨강을 쓰지 않는다. PRD가 말하는 고평가는 "시장가보다 비싸다"가
 * 아니라 **모델 예측과 실제 공시지가 사이의 anomaly**이고, 빨강은 그것을
 * "나쁜 땅"으로 읽히게 한다.
 */
export const GAP_BANDS = [
  { min: 15, label: "저평가 후보", color: "#1d4ed8" },
  { min: 5, label: "약한 저평가", color: "#60a5fa" },
  { min: -5, label: "모델과 비슷", color: "#94a3b8" },
  { min: -15, label: "약한 고평가", color: "#fbbf24" },
  { min: -Infinity, label: "고평가 후보", color: "#d97706" },
] as const;

export type GapBand = { label: string; color: string };

export function gapBand(gapPct: number): GapBand {
  return GAP_BANDS.find((b) => gapPct >= b.min) ?? GAP_BANDS[GAP_BANDS.length - 1];
}

/**
 * PRD 5장의 판정. **"적정 공시지가"라고 쓰지 않는다** — 우리는 정부 공시지가가
 * 틀렸다고 판정하는 시스템이 아니라 모델과 실제 사이의 anomaly를 찾는다.
 */
export type Verdict = "저평가 후보" | "고평가 후보" | "모델과 비슷";

export function verdictOf(gapPct: number): Verdict {
  if (gapPct >= 5) return "저평가 후보";
  if (gapPct <= -5) return "고평가 후보";
  return "모델과 비슷";
}

/* ── 탭이 쓰는 모양 ───────────────────────────────────────────────────── */

export interface FuturePrice {
  /** 예측 지평 — PRD Model A의 h. 0은 현재다. */
  horizon: 0 | 1 | 3 | 5;
  year: number;
  /** 원/㎡ */
  price: number;
  /** 현재 대비 누적 상승률 % */
  pct: number;
}

/** 값을 밀거나 당기는 요인. SHAP을 사람이 읽는 말로 바꾼 것. */
export interface ValueDriver {
  label: string;
  direction: "up" | "down";
  /** 화살표 개수 — 기여의 크기. 숫자를 그대로 내보내지 않는다. */
  strength: 1 | 2;
}

/** "교통 접근성 — 상위 7%"처럼 이름과 자리를 한 쌍으로 보여주는 항목. */
export interface RankedFactor {
  label: string;
  value: string;
  /** 막대로 그릴 백분위. 없으면 막대를 그리지 않는다. */
  percentile?: number;
}

export type PlanImpact = "매우 긍정적" | "긍정적" | "중립" | "부정적";

export interface PlanItem {
  label: string;
  value: string;
  impact: PlanImpact;
}

export interface Comparable {
  name: string;
  distanceM: number;
  zoning: string;
  areaM2: number;
  road: string;
  /** 원/㎡ */
  price: number;
  /** 모델 유사도 0~1 */
  similarity: number;
  lat: number;
  lng: number;
}

export interface DemoSite {
  id: string;
  /** 지번 표기. 실제 건물 이름을 쓰지 않는다. */
  jibun: string;
  dong: string;
  lat: number;
  lng: number;

  areaM2: number;
  zoning: string;
  jimok: string;
  roadFrontage: string;
  landShape: string;

  /** 실제 개별공시지가 (원/㎡) */
  actual: number;
  /** 모델 예측 공시지가 (원/㎡) */
  predicted: number;
  /** 모델 예측 − 실제 (원/㎡) */
  residual: number;
  /** (모델 예측 − 실제) / 실제 × 100 */
  gapPct: number;
  peerCount: number;

  /** 현재 + 1·3·5년 */
  future: FuturePrice[];
  futureDrivers: RankedFactor[];
  futureNarrative: string;

  valueUp: ValueDriver[];
  valueDown: ValueDriver[];

  /** 유망입지 Score 0~100 */
  hotspotScore: number;
  /** 권역 내 백분위 — 100이 가장 높다 */
  percentile: number;
  /** 이 필지의 3년 예상 상승률 % */
  growth3y: number;
  /** 유사 필지의 3년 예상 상승률 % */
  peerGrowth3y: number;
  /** 초과성장 %p */
  excessGrowthPp: number;
  hotspotFactors: RankedFactor[];

  spatial: RankedFactor[];
  plans: PlanItem[];
  comparables: Comparable[];

  /** 종합 판정 한 줄. */
  overall: string;
}

/* ── 시드 난수 ────────────────────────────────────────────────────────── */

function rng(seed: number): () => number {
  let s = seed | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick<T>(pool: readonly T[], next: () => number): T {
  return pool[Math.min(pool.length - 1, Math.floor(next() * pool.length))];
}

/** 실제 신촌 좌표. 지번은 표기용이고 판정 수치는 예시다. */
const SEEDS: { jibun: string; lat: number; lng: number; gapPct?: number }[] = [
  { jibun: "창천동 52-3", lat: 37.5559, lng: 126.9366, gapPct: 15.7 },
  { jibun: "창천동 30-11", lat: 37.5571, lng: 126.9387 },
  { jibun: "창천동 20-5", lat: 37.5545, lng: 126.9351 },
  { jibun: "창천동 72-8", lat: 37.5584, lng: 126.9359 },
  { jibun: "창천동 12-2", lat: 37.5536, lng: 126.938 },
  { jibun: "창천동 62-1", lat: 37.5568, lng: 126.9339 },
  { jibun: "대현동 104-3", lat: 37.5553, lng: 126.9404 },
  { jibun: "창천동 41-7", lat: 37.5596, lng: 126.9376 },
  { jibun: "신촌동 8-14", lat: 37.5527, lng: 126.9357 },
];

const ZONINGS = ["제2종일반주거지역", "제3종일반주거지역", "준주거지역", "일반상업지역"];
const FAR_CAP: Record<string, string> = {
  제2종일반주거지역: "200%",
  제3종일반주거지역: "250%",
  준주거지역: "400%",
  일반상업지역: "800%",
};
const JIMOKS = ["대", "대", "대", "잡종지"];
const ROAD_FRONTAGES = ["광대한면", "중로한면", "소로한면", "세로한면(가)"];
const LAND_SHAPES = ["정형", "정형", "가장형", "부정형"];

const UP_POOL: string[] = [
  "지하철 접근성",
  "상업시설 밀도",
  "도로 네트워크 접근성",
  "Integration",
  "생활인구 밀도",
  "주변 토지가격 수준",
];

const DOWN_POOL: string[] = [
  "부정형 필지",
  "도로접면 조건",
  "필지 면적",
  "용적률 여력",
  "지형 경사",
];

/** 미래 상승 근거 — PRD 6장 feature 그룹 이름을 그대로 쓴다. */
const FUTURE_DRIVER_LABELS = [
  "교통 접근성",
  "주변 상권 성장",
  "도시계획",
  "인구 변화",
  "가격 추세",
  "공간 네트워크 구조",
];

const HOTSPOT_FACTOR_LABELS = ["교통 접근성", "상권 성장", "POI 증가", "Space Syntax"];

function pctText(p: number): string {
  return `상위 ${Math.max(1, Math.round(100 - p))}%`;
}

/** 한 지점의 분석 전체. 시드가 같으면 결과가 같다. */
function buildSite(seed: (typeof SEEDS)[number], i: number): DemoSite {
  const next = rng(4_100 + i * 6_367);

  // 첫 지점은 PRD 문서의 예시 숫자에 맞춰 고정한다 — 설명과 화면이 어긋나지 않게.
  const isHero = i === 0;
  const gapPct = seed.gapPct ?? Math.round((next() * 34 - 17) * 10) / 10;
  const actual = isHero ? 10_200_000 : Math.round((7_800_000 + next() * 7_600_000) / 10_000) * 10_000;
  const predicted = Math.round((actual * (1 + gapPct / 100)) / 10_000) * 10_000;

  const zoning = isHero ? "제2종일반주거지역" : pick(ZONINGS, next);
  const landShape = isHero ? "가장형" : pick(LAND_SHAPES, next);

  // 미래 상승률 — 저평가 쪽이 여력이 크다고 보되 Gap에만 매달지 않는다.
  const g1 = isHero ? 5.9 : Math.round(Math.max(0.5, 2.6 + gapPct * 0.09 + (next() - 0.5) * 1.8) * 10) / 10;
  const g3 = isHero ? 18.1 : Math.round(g1 * (2.9 + next() * 0.5) * 10) / 10;
  const g5 = isHero ? 31.4 : Math.round(g1 * (4.6 + next() * 0.9) * 10) / 10;

  const YEAR0 = 2026;
  const future: FuturePrice[] = [
    { horizon: 0, year: YEAR0, price: actual, pct: 0 },
    { horizon: 1, year: YEAR0 + 1, price: Math.round((actual * (1 + g1 / 100)) / 10_000) * 10_000, pct: g1 },
    { horizon: 3, year: YEAR0 + 3, price: Math.round((actual * (1 + g3 / 100)) / 10_000) * 10_000, pct: g3 },
    { horizon: 5, year: YEAR0 + 5, price: Math.round((actual * (1 + g5 / 100)) / 10_000) * 10_000, pct: g5 },
  ];

  const percentile = isHero ? 92 : Math.round(Math.min(98, Math.max(4, 50 + gapPct * 1.7 + (next() - 0.5) * 22)));
  const peerGrowth3y = isHero ? 8 : Math.round(Math.max(0.5, g3 - 3 - next() * 9) * 10) / 10;
  const excessGrowthPp = Math.round((g3 - peerGrowth3y) * 10) / 10;

  const upCount = gapPct >= 5 ? 4 : gapPct <= -5 ? 2 : 3;
  const downCount = gapPct <= -5 ? 3 : 2;
  const valueUp: ValueDriver[] = UP_POOL.slice(0, upCount).map((label, k) => ({
    label,
    direction: "up" as const,
    strength: k === 0 ? (2 as const) : (1 as const),
  }));
  const valueDown: ValueDriver[] = DOWN_POOL.slice(0, downCount).map((label, k) => ({
    label,
    direction: "down" as const,
    strength: k === 0 && gapPct <= -5 ? (2 as const) : (1 as const),
  }));

  const integrationPct = isHero ? 94 : Math.round(Math.min(98, Math.max(5, percentile + (next() - 0.5) * 24)));
  const choicePct = isHero ? 88 : Math.round(Math.min(98, Math.max(5, integrationPct - next() * 20)));
  const poiPct = isHero ? 85 : Math.round(Math.min(98, Math.max(5, percentile + (next() - 0.5) * 30)));
  const popPct = isHero ? 79 : Math.round(Math.min(98, Math.max(5, poiPct - next() * 18)));
  const stationM = isHero ? 180 : 120 + Math.round(next() * 700);
  const busStops = isHero ? 8 : 2 + Math.round(next() * 8);

  const planImpact: PlanImpact = gapPct >= 5 ? "긍정적" : gapPct <= -5 ? "중립" : "긍정적";

  const comparables: Comparable[] = ["A", "B", "C"].map((tag, k) => {
    const d = 120 + Math.round(next() * 380);
    const bearing = next() * Math.PI * 2;
    return {
      name: `유사필지 ${tag}`,
      distanceM: d,
      zoning: k === 0 ? zoning : pick(ZONINGS, next),
      areaM2: Math.round((160 + next() * 420) * 10) / 10,
      road: pick(ROAD_FRONTAGES, next),
      // 비교군은 대상보다 위에 있는 쪽으로 둔다 — 저평가 판정의 근거가 그것이다.
      price: Math.round((predicted * (0.93 + next() * 0.12)) / 10_000) * 10_000,
      similarity: Math.round((0.82 + next() * 0.15) * 100) / 100,
      lat: seed.lat + (Math.cos(bearing) * d) / 111_320,
      lng: seed.lng + (Math.sin(bearing) * d) / 88_270,
    };
  });

  const verdict = verdictOf(gapPct);
  const overall =
    verdict === "저평가 후보"
      ? percentile >= 70
        ? "저평가 · 미래 성장성 높음"
        : "저평가 · 성장성 보통"
      : verdict === "고평가 후보"
        ? "고평가 · 모델이 보지 못하는 요인 가능"
        : "모델 예측과 비슷 · 특이 신호 없음";

  return {
    id: `site-${i}`,
    jibun: seed.jibun,
    dong: seed.jibun.startsWith("대현") ? "서대문구" : "서대문구",
    lat: seed.lat,
    lng: seed.lng,
    areaM2: isHero ? 331.4 : Math.round((150 + next() * 460) * 10) / 10,
    zoning,
    jimok: pick(JIMOKS, next),
    roadFrontage: isHero ? "중로한면" : pick(ROAD_FRONTAGES, next),
    landShape,
    actual,
    predicted,
    residual: predicted - actual,
    gapPct,
    peerCount: isHero ? 412 : 240 + Math.floor(next() * 380),

    future,
    futureDrivers: FUTURE_DRIVER_LABELS.map((label, k) => {
      const p = Math.round(Math.min(98, Math.max(6, percentile - k * 4 + (next() - 0.5) * 18)));
      return { label, value: pctText(p), percentile: p };
    }),
    futureNarrative:
      verdict === "저평가 후보"
        ? "역까지 걸어서 닿는 거리이고 주변 상권의 추정매출이 3년 연속 늘었습니다. 도시계획 쪽에도 값을 끌어올릴 항목이 걸려 있어, 지금 조건이 이어진다면 공시지가가 권역 평균보다 빠르게 오를 것으로 봅니다."
        : "접근성은 나쁘지 않지만 주변 상권 지표가 꺾이고 있고 개발 계획도 값을 밀어 올릴 만한 것이 없습니다. 지금 조건이 이어진다면 공시지가는 권역 평균 정도로 오를 것으로 봅니다.",

    valueUp,
    valueDown,

    hotspotScore: isHero ? 87 : Math.round(Math.min(98, Math.max(6, percentile - 4 + (next() - 0.5) * 12))),
    percentile,
    growth3y: g3,
    peerGrowth3y,
    excessGrowthPp,
    hotspotFactors: [
      ...HOTSPOT_FACTOR_LABELS.map((label, k) => {
        const p = Math.round(Math.min(98, Math.max(6, percentile - k * 3 + (next() - 0.5) * 16)));
        return { label, value: pctText(p), percentile: p };
      }),
      { label: "개발계획 영향", value: planImpact },
    ],

    spatial: [
      { label: "Integration", value: pctText(integrationPct), percentile: integrationPct },
      { label: "Choice", value: pctText(choicePct), percentile: choicePct },
      { label: "지하철 거리", value: `${stationM}m` },
      { label: "버스정류장", value: `300m 내 ${busStops}개` },
      { label: "간선도로 접근성", value: integrationPct >= 70 ? "우수" : "보통" },
      { label: "POI 밀도", value: pctText(poiPct), percentile: poiPct },
      { label: "생활인구", value: pctText(popPct), percentile: popPct },
    ],

    plans: [
      { label: "용도지역", value: zoning, impact: "중립" },
      { label: "용적률 법정 상한", value: FAR_CAP[zoning] ?? "—", impact: "중립" },
      {
        label: "지구단위계획",
        value: gapPct >= 5 ? "신촌 지구단위계획 포함" : "해당 없음",
        impact: gapPct >= 5 ? "긍정적" : "중립",
      },
      {
        label: "정비사업",
        value: gapPct >= 5 ? "500m 이내 가로주택정비 1건" : "해당 없음",
        impact: gapPct >= 5 ? "긍정적" : "중립",
      },
      {
        label: "교통계획",
        value: stationM < 400 ? "경전철 신설역 예정 · 740m" : "해당 없음",
        impact: stationM < 400 ? "매우 긍정적" : "중립",
      },
      {
        label: "높이제한",
        value: landShape === "부정형" ? "최고고도지구 일부" : "해당 없음",
        impact: landShape === "부정형" ? "부정적" : "중립",
      },
    ],

    comparables,
    overall,
  };
}

/** 지도에 놓을 지점들. 고정 시드이므로 호출마다 같은 결과다. */
export function buildDemoSites(): DemoSite[] {
  return SEEDS.map(buildSite);
}

/** 시나리오가 자동으로 누르는 지점 — 문서 예시와 같은 저평가 필지다. */
export function heroSite(sites: DemoSite[]): DemoSite {
  return sites[0];
}

/** 비교군 중앙값 — 저평가 판정의 근거로 같이 보여준다. */
export function comparableMedian(site: DemoSite): number {
  const prices = site.comparables.map((c) => c.price).sort((a, b) => a - b);
  const mid = Math.floor(prices.length / 2);
  return prices.length % 2 ? prices[mid] : Math.round((prices[mid - 1] + prices[mid]) / 2);
}

/* ── 표기 ─────────────────────────────────────────────────────────────── */

/** 원/㎡ → "1,020만원/㎡". 금액을 만원 단위로 읽는 쪽이 자연스럽다. */
export function wonPerM2(won: number): string {
  return `${Math.round(won / 10_000).toLocaleString("ko-KR")}만원/㎡`;
}

/** 부호를 항상 붙인다 — Gap은 부호가 뜻의 전부다. */
export function signedPct(pct: number, digits = 1): string {
  return `${pct >= 0 ? "+" : "−"}${Math.abs(pct).toFixed(digits)}%`;
}

export function signedPp(pp: number, digits = 1): string {
  return `${pp >= 0 ? "+" : "−"}${Math.abs(pp).toFixed(digits)}%p`;
}

/**
 * 백분위를 사람이 읽는 말로. `상위 ${100 - p}%`만 쓰면 백분위 0이 "상위 100%"로
 * 나와 문장으로 뜻이 없다 — 아래쪽 절반은 "하위"로 뒤집어 말한다.
 */
export function percentileText(percentile: number): string {
  const p = Math.round(percentile);
  return p >= 50 ? `상위 ${Math.max(1, 100 - p)}%` : `하위 ${Math.max(1, p)}%`;
}
