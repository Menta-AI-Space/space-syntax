"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode, type SyntheticEvent } from "react";
import { ChatIcon } from "@/components/agent/ChatIcon";
import { ChatSidebar } from "@/components/agent/ChatSidebar";
import {
  loadChatSessions,
  newChatSessionId,
  removeChatSession,
  upsertChatSession,
  type ChatSession,
  type ChatSessionTurn,
} from "@/lib/chatSessions";
import {
  loadChatResultSnapshot,
  removeChatResultSnapshot,
  saveChatResultSnapshot,
  type ChatResultSnapshot,
} from "@/lib/chatResultSnapshots";
import { isInverseMetric } from "@/lib/metricDirection";
import { parcelTurnFor, type StructuredAnalysis } from "@/lib/landAnalysis";
import { useSpeechInput } from "@/lib/useSpeechInput";
import { AnalysisVisuals } from "@/components/agent/AnalysisVisuals";
import { FounderCard } from "@/components/agent/FounderCard";
import { VerdictReport } from "@/components/agent/VerdictReport";
import { LeaseVerdictReport } from "@/components/agent/LeaseVerdictReport";
import { PurchaseVerdictReport } from "@/components/agent/PurchaseVerdictReport";
import { DiagnoseReport } from "@/components/agent/DiagnoseReport";
import { AreaSearchReport } from "@/components/agent/AreaSearchReport";
import { AnalogsToolCard } from "@/components/agent/AnalogsToolCard";
import { LandAnalysisCard } from "@/components/parcels/LandAnalysisCard";
import { PointList, RangeBar, TileRow, VerdictHeadline, WarningsCard } from "@/components/agent/ReportParts";
import { fmtPricePerSqm, pricePercentileText } from "@/components/profitability/profitabilityUtils";
import { profitabilityWarnings } from "@/lib/profitabilityWarnings";
import { AreaInsightsSection } from "@/components/AreaInsightsSection";
import { TerritorySection } from "@/components/dashboard/TerritorySection";
import { ModelFitSection } from "@/components/dashboard/RealCompetitionSection";
import { SynergySection } from "@/components/dashboard/SynergySection";
import { formatDashNum, ISO_MODE_LABELS } from "@/components/dashboard/dashboardUtils";
import { buildCannibalizationView } from "@/lib/cannibalizationCard";
import { explanationSourceLabel } from "@/lib/explanationSource";
import { buildLeaseSiteContextTiles, leaseBestModels } from "@/lib/leaseSiteContextCard";
import { competitionReachLine } from "@/lib/realCompetitionCard";
import { marketTimingNote } from "@/lib/propertyMarketTiming";
import { fmtMan, fmtMonths, fmtPct, fmtRatio, fmtSignedPct, followUpExample, hasNumber, type VerdictTone } from "@/lib/agentFormat";
import {
  api,
  appendAgentHistory,
  ApiClientError,
  type AcademyClusterResponse,
  type AgentAnswerRequest,
  type AgentAskResponse,
  type AgentHistoryTurn,
  type AnalogsResponse,
  type AreaInsightsResponse,
  type AreaSearchResponse,
  type CannibalizationResponse,
  type CompetitionBucket,
  type CompetitorSummary,
  type DecideResponse,
  type DiagnoseResponse,
  type FranchiseBrandDetail,
  type IsochroneResponse,
  type LeaseFeasibilityResponse,
  type LeaseVerdictResponse,
  type ProfitabilityAnalyzeResponse,
  type PropertyMarketResult,
  type PurchaseVerdictResponse,
  type RealCompetitionBundleResponse,
  type RentReference,
  type TerritoryAnalysisResponse,
  type SiteScoreResponse,
  type TrendsResponse,
} from "@/lib/api";
import { streamAgentAsk } from "@/lib/agentStream";
import { SpeakButton } from "@/components/SpeakButton";
import { type AnswerBlock, type InlineRun, parseAnswerBlocks, rowTopPercent } from "@/lib/answerBlocks";
import { sectionVisual, type SectionVisual } from "@/lib/answerSectionVisuals";

/** market.excluded의 키(backend/app/services/property_market.py의 집계 사유)를
 * 한글 라벨로 바꾼다. 알려지지 않은 키가 와도(백엔드가 사유를 추가해도) 값을
 * 숨기지 않고 키 그대로 보여준다 — 조용히 버리지 않는다. */
const EXCLUDED_REASON_LABELS: Record<string, string> = {
  cancelled: "해제(취소) 신고",
  share_deal: "지분 거래",
  direct_deal: "직거래",
  approx_geo: "위치 근사치",
  other_building_type: "건물유형 상이",
};

/** market.excluded(같은 반경·유형·기간에서 집계 시 제외된 거래 건수, 밴드 필터와
 * 무관하게 집계됨)를 0건인 사유는 빼고 "사유 N건" 목록으로 바꾼다. */
function excludedReasonItems(excluded: Record<string, number> | undefined | null): string[] {
  if (!excluded) return [];
  return Object.entries(excluded)
    .filter(([, count]) => count > 0)
    .map(([reason, count]) => `${EXCLUDED_REASON_LABELS[reason] ?? reason} ${count}건`);
}

/** 주변 매매 실거래 밴드(P25~P75) — 표본이 있어도 backend가
 * insufficient_sample=true로 표시하면(8건 미만) 확정 시세로 보이지 않게
 * caution 톤과 문구를 붙인다. p50 자체가 없으면(표본 극소) 밴드를 그리지
 * 않고 건수만 말한다 — 없는 값을 지어내지 않는다. */
function ProfitabilityMarketSection({ market }: { market: PropertyMarketResult }) {
  const excludedItems = excludedReasonItems(market.excluded);
  if (!hasNumber(market.p50)) {
    return (
      <div className="report-block">
        <PointList
          title="주변 실거래"
          items={[`비교 가능한 실거래가 ${market.n}건뿐이라 시장가격 밴드를 만들 수 없습니다.`]}
          tone="caution"
        />
        <PointList title="같은 반경에서 집계 시 제외된 거래" items={excludedItems} tone="info" />
      </div>
    );
  }
  const thinSample = market.insufficient_sample;
  const noteParts = [
    thinSample ? `표본이 ${market.n}건뿐이라(최소 8건 기준) 참고용입니다` : `표본 ${market.n}건`,
    `반경 ${market.radius_m}m`,
    market.data_source,
  ];
  if (market.min_deal_date && market.max_deal_date) {
    noteParts.push(
      market.min_deal_date === market.max_deal_date
        ? `${market.min_deal_date} 거래`
        : `${market.min_deal_date}~${market.max_deal_date} 거래`,
    );
  }
  if (market.relaxations.length > 0) {
    noteParts.push(`완화: ${market.relaxations.join(", ")}`);
  }
  const timingNote = marketTimingNote(market);
  if (timingNote) noteParts.push(timingNote);
  if (hasNumber(market.subject_percentile)) {
    noteParts.push(pricePercentileText(market.subject_percentile as number));
  }
  return (
    <div className="report-block">
      <RangeBar
        label="주변 매매 실거래 (₩/㎡)"
        low={market.p25}
        high={market.p75}
        mid={market.p50}
        value={market.subject_price_per_sqm}
        format={fmtPricePerSqm}
        midLabel={`중위 ${fmtPricePerSqm(market.p50)}`}
        valueLabel="검토 단가"
        note={noteParts.join(" · ")}
        tone={thinSample ? "caution" : "info"}
      />
      <PointList title="같은 반경에서 집계 시 제외된 거래" items={excludedItems} tone="info" />
    </div>
  );
}

/** 주변 임대료 참고치(R-ONE 지역통계 또는 OffiRent 실거래) — matched=false면
 * 매칭된 상권이 없다는 뜻이라 아무것도 그리지 않는다. */
function ProfitabilityRentReferenceSection({ rentReference }: { rentReference: RentReference }) {
  if (!rentReference.matched) return null;
  const hasBand = hasNumber(rentReference.monthly_rent_low_manwon) && hasNumber(rentReference.monthly_rent_high_manwon);
  const bandText = hasBand
    ? rentReference.monthly_rent_low_manwon === rentReference.monthly_rent_high_manwon
      ? fmtMan(rentReference.monthly_rent_low_manwon)
      : `${fmtMan(rentReference.monthly_rent_low_manwon)}~${fmtMan(rentReference.monthly_rent_high_manwon)}`
    : null;
  const tiles = [
    bandText ? { label: "주변 임대료(월세) 참고", value: bandText } : null,
    hasNumber(rentReference.vacancy_default_pct) ? { label: "공실률(참고)", value: fmtPct(rentReference.vacancy_default_pct) } : null,
  ].filter((t): t is { label: string; value: string } => t !== null);
  const typeLines = (rentReference.types ?? []).map((t) => {
    const rent = hasNumber(t.rent_krw_per_sqm) ? `${Math.round(t.rent_krw_per_sqm as number).toLocaleString("ko-KR")}원/㎡` : "—";
    const vacancy = hasNumber(t.vacancy_pct) ? ` · 공실 ${(t.vacancy_pct as number).toFixed(1)}%` : "";
    return `${t.property_type} ${rent}${vacancy}`;
  });
  const footer = [
    rentReference.sanggwon_nm ? `${rentReference.sanggwon_nm} 상권` : "",
    rentReference.quarter,
    rentReference.data_source,
  ].filter(Boolean).join(" · ");
  if (tiles.length === 0 && typeLines.length === 0 && !footer) return null;
  return (
    <div className="report-block">
      <TileRow tiles={tiles} />
      <PointList title="유형별 임대료 참고" items={typeLines} tone="info" />
      {footer && <p className="agent-step-hint">{footer}</p>}
    </div>
  );
}

/** /api/profitability/analyze 결과 — decide·lease_verdict처럼 전용 Report
 * 컴포넌트가 없어 여기서 직접 조립한다. 값이 있는 타일만 세운다. */
function ProfitabilityToolCard({ data }: { data: ProfitabilityAnalyzeResponse }) {
  const calc = data.calculation;
  const tiles = [
    hasNumber(calc.cap_rate_noi_pct) ? { label: "순영업소득 수익률", value: fmtPct(calc.cap_rate_noi_pct) } : null,
    hasNumber(calc.cash_on_cash_pct) ? { label: "현금 대비 수익률", value: fmtPct(calc.cash_on_cash_pct) } : null,
    hasNumber(calc.dscr) ? { label: "대출 감당력", value: fmtRatio(calc.dscr) } : null,
    hasNumber(calc.irr_pct) ? { label: "IRR", value: fmtPct(calc.irr_pct) } : null,
  ].filter((t): t is { label: string; value: string } => t !== null);
  const risks = Array.isArray(data.risks) ? data.risks.map((r) => r.message) : [];
  const calculationWarnings = Array.isArray(calc.warnings) ? calc.warnings : [];
  const levers = Array.isArray(data.levers) ? data.levers : [];

  return (
    <div className="verdict-report">
      <VerdictHeadline tone="info" text={data.headline || "투자 수익성 계산이 준비됐습니다."} />
      <div className="card report-block">
        <TileRow tiles={tiles} />
        <PointList title="위험" items={risks} tone="caution" />
        <PointList title="계산 경고" items={calculationWarnings} tone="caution" />
        <PointList title="레버(조정 가능한 조건)" items={levers} tone="info" />
      </div>
      {data.rent_reference && <ProfitabilityRentReferenceSection rentReference={data.rent_reference} />}
      {data.market && <ProfitabilityMarketSection market={data.market} />}
      <WarningsCard warnings={profitabilityWarnings(data)} />
    </div>
  );
}

/** property_market 도구 응답의 property_type("commercial"/"officetel")을
 * 화면에 쓰는 한글로 바꾼다 — 백엔드가 영문 그대로 내려주기 때문. */
const PROPERTY_TYPE_LABEL: Record<string, string> = {
  commercial: "상가",
  officetel: "오피스텔",
};

/** /api/property-market/comparables 결과 — "이 가격 비싼 거야?" 질문에 답하는
 * 실거래 비교 단독 도구. 시장 밴드는 profitability와 같은 섹션(ProfitabilityMarketSection)을
 * 재사용하고, 비교에 쓰인 실거래를 근거로 덧붙인다. 백엔드는 이 목록을
 * deal_date DESC로 정렬해 내려주므로(거리순이 아니다) "최근"이라고만 말한다. */
function PropertyMarketToolCard({ data }: { data: PropertyMarketResult }) {
  const typeLabel = PROPERTY_TYPE_LABEL[data.property_type] ?? data.property_type;
  const comparables = (data.comparables ?? []).slice(0, 5).map((c) => {
    const place = [c.umd_nm, c.bldg_nm].filter(Boolean).join(" ") || "위치 미상";
    return `${place} · ${fmtPricePerSqm(c.price_per_sqm)} · ${c.deal_date} · ${Math.round(c.distance_m)}m`;
  });

  return (
    <div className="verdict-report">
      <VerdictHeadline
        tone="info"
        eyebrow="실거래 비교"
        text={`주변 ${typeLabel} 매매 실거래 ${data.n}건과 비교했습니다.`}
      />
      <div className="card report-block">
        <ProfitabilityMarketSection market={data} />
        <PointList title="최근 비교 거래" items={comparables} tone="info" />
      </div>
      <WarningsCard warnings={data.warnings} />
    </div>
  );
}

/** /api/trends 결과 — 성장 신호 요약. 전용 Report 컴포넌트가 없어 여기서 조립한다.
 * data_scope는 street_signals의 출처일 뿐 series(카테고리·전체 성장률 타일)의
 * 출처가 아니다(둘은 backend/app/services/store_trends.py에서 서로 다른 API를
 * 탄다) — 그래서 상단 타일이 아니라 "거리별 신호" 옆에만 붙인다. */
function TrendsToolCard({ data }: { data: TrendsResponse }) {
  const series = data.series ?? null;
  const tiles = [
    hasNumber(series?.category_growth_pct)
      ? { label: `${data.category_label} 성장률`, value: fmtSignedPct(series?.category_growth_pct) }
      : null,
    hasNumber(series?.total_growth_pct) ? { label: "전체 업종 성장률", value: fmtSignedPct(series?.total_growth_pct) } : null,
  ].filter((t): t is { label: string; value: string } => t !== null);
  const signals = Array.isArray(data.street_signals)
    ? data.street_signals.map((s) => `${s.label}: ${s.detail}`)
    : [];

  return (
    <div className="verdict-report">
      <VerdictHeadline tone="info" text={`${data.category_label} 성장 신호`} />
      <div className="card report-block">
        <TileRow tiles={tiles} />
        <PointList title="거리별 신호" items={signals} tone="info" />
        {signals.length > 0 && data.data_scope && (
          <p className="agent-step-hint">신호 출처: {data.data_scope}</p>
        )}
      </div>
      <WarningsCard warnings={data.warnings} />
    </div>
  );
}

/** 한글 판정 라벨(백엔드가 그대로 내려주는 문자열) → 색 톤.
 * "높음"·"위험" 계열은 잠식·경쟁·포화처럼 높을수록 나쁜 지표에서만 쓰인다
 * (frontend-next/lib/metricDirection.ts의 HIGHER_IS_WORSE와 같은 방향). */
const KOREAN_LABEL_TONE: Record<string, VerdictTone> = {
  위험: "negative",
  "매우 높음": "negative",
  높음: "negative",
  주의: "negative",
  중간: "caution",
  보통: "caution",
  낮음: "positive",
  양호: "positive",
};
function toneForKoreanLabel(label: string | null | undefined): VerdictTone {
  return KOREAN_LABEL_TONE[label ?? ""] ?? "info";
}

/** /api/cannibalization 결과 — 잠식·자기잠식 위험. 전용 Report 컴포넌트가
 * 없어 여기서 조립한다. 겹침 벤 다이어그램(대시보드용)은 들고 오지 않고
 * 숫자와 근접 경쟁점만 보여준다 — 지도 없이도 읽히는 요약이 목적이라서다.
 * self_total_count는 브랜드가 주어져 자기 매장을 실제로 찾았을 때만 0보다
 * 크다(backend/app/routers/agent.py의 같은 주석 참고) — 0/undefined일 땐
 * "자기잠식 없음"이 아니라 "브랜드를 몰라 안 찾음"일 수 있으므로 그 경우
 * 자기잠식 블록 자체를 그리지 않는다. */
function CannibalizationToolCard({ data }: { data: CannibalizationResponse }) {
  const modeLabel = ISO_MODE_LABELS[data.mode] || data.mode;
  const view = buildCannibalizationView(data, modeLabel);

  return (
    <div className="verdict-report">
      <VerdictHeadline
        tone={toneForKoreanLabel(data.risk_label)}
        eyebrow="잠식 분석"
        text={data.detail || "동일업종 잠식 분석 결과입니다."}
        chip={data.risk_label}
      />
      <div className="card report-block">
        <TileRow tiles={view.tiles} />
        <PointList title="가까운 동일업종" items={view.competitors} tone="caution" />
        {view.hasSelfData && (
          <PointList
            title={`같은 브랜드 매장 · 자기잠식 후보 (${view.selfTotal}곳)`}
            items={view.selfCompetitors}
            tone={view.selfOverRecommended ? "negative" : "caution"}
          />
        )}
        {view.demandFactor != null && (
          <p className="agent-step-hint">유동인구 보정 ×{view.demandFactor.toFixed(2)}</p>
        )}
      </div>
    </div>
  );
}

/** /api/isochrone 결과 — 도달권(등시선) 밴드별 인구·동일업종. 지도 원은
 * AnalysisVisuals가 이미 그려주므로 여기선 밴드 표만 요약한다. */
function IsochroneToolCard({ data }: { data: IsochroneResponse }) {
  const modeLabel = ISO_MODE_LABELS[data.mode] || data.mode;
  if (!data.bands?.length) {
    return (
      <div className="verdict-report">
        <VerdictHeadline tone="caution" eyebrow="도달권 분석" text="도로망에 연결되지 않아 도달권을 계산할 수 없습니다." />
      </div>
    );
  }
  const lines = data.bands.map(
    (b) =>
      `${b.minutes}분 · 반경 ~${Math.round(b.max_distance_m)}m · 동일업종 ${b.same_category_count}곳` +
      (hasNumber(b.population_estimate) ? ` · 인구 ${formatDashNum(b.population_estimate)}명` : ""),
  );
  return (
    <div className="verdict-report">
      <VerdictHeadline tone="info" eyebrow="도달권 분석" text={`${modeLabel} 기준 도달권 분석 결과입니다.`} />
      <div className="card report-block">
        <PointList title={`${modeLabel} 도달권`} items={lines} tone="info" />
      </div>
      <WarningsCard warnings={data.warnings} />
    </div>
  );
}

/** 학원 클러스터 분석(academy_cluster 도구 단독 호출·real_competition 번들
 * 안쪽 양쪽에서 재사용) — 직접 경쟁·클러스터 시너지·학부모 안심도 세 점수. */
function AcademyClusterBlock({ data }: { data: AcademyClusterResponse }) {
  const tiles = [
    { label: "직접 경쟁 압박", value: `${Math.round(data.scores.direct_competition_pressure)}점` },
    { label: "클러스터 시너지", value: `${Math.round(data.scores.cluster_synergy)}점` },
    { label: "학부모 안심도", value: `${Math.round(data.scores.parent_safety)}점 · ${data.parent_safety_detail.level}` },
  ];
  const clusters = (data.clusters ?? [])
    .filter((c) => c.count > 0)
    .map((c) => `${c.label}: ${c.count}곳 · ${c.intensity}`);
  const safetyRisks = [
    ...(data.parent_safety_detail.same_building_risks ?? []),
    ...(data.parent_safety_detail.nearby_risks ?? []),
  ];

  return (
    <>
      <VerdictHeadline
        tone={toneForKoreanLabel(data.parent_safety_detail.level)}
        eyebrow="학원 클러스터"
        text={data.headline || `${data.target_label} 학원 클러스터 분석 결과입니다.`}
      />
      <div className="card report-block">
        <TileRow tiles={tiles} />
        <PointList title="클러스터" items={clusters} tone="info" />
        <PointList title="해설" items={data.interpretation} tone="info" />
        <PointList title="안전 관련" items={safetyRisks} tone="caution" />
        {data.parent_safety_detail.sensitivity_note && (
          <p className="agent-step-hint">{data.parent_safety_detail.sensitivity_note}</p>
        )}
      </div>
      <WarningsCard warnings={data.warnings} />
    </>
  );
}

/** 근접 경쟁점 한 곳을 한 줄로 요약한다 — 이름 · 거리(있으면) · 진료과목(있으면)
 * · 전문의 수(있으면). backend가 안 보낸 값은 빈 문자열로 채우지 않고 그 자리를
 * 통째로 뺀다. */
function formatNearestCompetitor(n: CompetitorSummary): string {
  const parts = [n.name];
  if (hasNumber(n.distance_m)) parts.push(`${Math.round(n.distance_m as number)}m`);
  if (n.subjects && n.subjects.length > 0) parts.push(n.subjects.join("·"));
  if (hasNumber(n.specialist_count) && (n.specialist_count as number) > 0) parts.push(`전문의 ${n.specialist_count}명`);
  return parts.join(" · ");
}

const NEAREST_COMPETITOR_DISPLAY_LIMIT = 5;

/** 경쟁 구간(bucket) 하나의 근접 경쟁점 목록 — backend가 이미 거리순으로 정렬해
 * 보낸 bucket.nearest 중 가까운 순으로 몇 곳만 보여주고, bucket.count가 보여준
 * 곳보다 많으면(backend가 nearest를 최대 25곳까지만 채워 보내므로 실제로도
 * 종종 그렇다) 남은 개수만 밝힌다 — 안 보여준 곳의 이름을 지어내지 않는다. */
function competitionBucketNearestLines(bucket: CompetitionBucket): string[] {
  const nearest = bucket.nearest ?? [];
  if (nearest.length === 0) return [];
  const shown = nearest.slice(0, NEAREST_COMPETITOR_DISPLAY_LIMIT);
  const lines = shown.map(formatNearestCompetitor);
  const remainder = bucket.count - shown.length;
  if (remainder > 0) lines.push(`외 ${remainder}곳 더`);
  return lines;
}

/** /api/real-competition 결과 — 경쟁 구조(RealCompetitionResponse) +
 * 학원 클러스터 + 모델 적합도 + 보완업체 시너지를 한 응답에 담아 온다
 * (RealCompetitionBundleResponse). 모델 적합·시너지는 이미 지도·조작이
 * 필요 없는 전용 컴포넌트(ModelFitSection·SynergySection)가 있어 그대로
 * 재사용한다. */
function RealCompetitionToolCard({ data }: { data: RealCompetitionBundleResponse }) {
  const comp = data.competition;
  const compTiles = comp ? [{ label: "경쟁 강도", value: `${Math.round(comp.total_weighted_score)}점 · ${comp.total_intensity}` }] : [];
  const activeBuckets = (comp?.buckets ?? []).filter((b) => b.count > 0);
  const buckets = activeBuckets.map((b) => `${b.label}: ${b.count}곳 · ${b.intensity}`);
  const reachLine = competitionReachLine(comp);

  return (
    <div className="verdict-report">
      {comp && (
        <>
          <VerdictHeadline
            tone={toneForKoreanLabel(comp.total_intensity)}
            eyebrow="경쟁 구조"
            text={comp.summary_one_liner || "경쟁 구조 분석 결과입니다."}
            chip={comp.total_intensity}
          />
          <div className="card report-block">
            <TileRow tiles={compTiles} />
            {reachLine && <p className="agent-step-hint">{reachLine}</p>}
            <PointList title="경쟁 구간" items={buckets} tone="info" />
            {activeBuckets.map((b) => (
              <PointList key={b.key} title={`${b.label} 근접 경쟁점`} items={competitionBucketNearestLines(b)} tone="info" />
            ))}
          </div>
        </>
      )}
      {data.academy_cluster && <AcademyClusterBlock data={data.academy_cluster} />}
      {data.model_fit && <ModelFitSection modelFit={data.model_fit} />}
      <SynergySection synergy={data.synergy ?? null} loading={false} error="" />
      <WarningsCard warnings={data.warnings} />
    </div>
  );
}

/** /api/lease-feasibility 결과 — 손익분기·적정 월세·권리금 회수. */
function LeaseFeasibilityToolCard({ data }: { data: LeaseFeasibilityResponse }) {
  const tiles = [
    { label: "손익분기 월매출", value: fmtMan(data.break_even_monthly_man) },
    hasNumber(data.rent_burden_pct) ? { label: "임차비 부담률", value: fmtPct(data.rent_burden_pct) } : null,
    hasNumber(data.appropriate_rent_low_man) && hasNumber(data.appropriate_rent_high_man)
      ? { label: "적정 월세", value: `${fmtMan(data.appropriate_rent_low_man)} ~ ${fmtMan(data.appropriate_rent_high_man)}` }
      : null,
    hasNumber(data.key_money_payback_months) ? { label: "권리금 회수", value: fmtMonths(data.key_money_payback_months) } : null,
    hasNumber(data.benchmark_monthly_revenue_man)
      ? { label: "상권 벤치마크 월매출", value: fmtMan(data.benchmark_monthly_revenue_man) }
      : null,
    hasNumber(data.recommended_rent_max_man)
      ? { label: "권장 임차비 상한", value: fmtMan(data.recommended_rent_max_man) }
      : null,
    ...buildLeaseSiteContextTiles(data.site_context).map((t) => ({
      label: t.label,
      value: t.value,
      tone: t.toneLabel ? toneForKoreanLabel(t.toneLabel) : undefined,
    })),
  ].filter((t): t is { label: string; value: string; tone?: VerdictTone } => t !== null);
  // benchmark_scope는 상권 벤치마크 월매출이 없을 때만(서울 외 지역 등) 왜
  // 없는지 알려주는 대체 문구다 — 값이 있으면 data_source만으로 충분하다.
  const benchmarkFooter = [
    !hasNumber(data.benchmark_monthly_revenue_man) && data.benchmark_scope ? data.benchmark_scope : null,
    data.benchmark_data_source,
  ].filter(Boolean).join(" · ");
  const explanationSource = explanationSourceLabel(data.explanation?.source);
  const bestModels = leaseBestModels(data.site_context);

  return (
    <div className="verdict-report">
      <VerdictHeadline
        tone={toneForKoreanLabel(data.verdict)}
        eyebrow="임대 타당성"
        text={data.explanation?.text || "임대 타당성 분석 결과입니다."}
        chip={data.verdict}
      />
      <div className="card report-block">
        <TileRow tiles={tiles} />
        <PointList title="이 자리에 맞는 모델" items={bestModels} tone="info" />
        {benchmarkFooter && <p className="agent-step-hint">{benchmarkFooter}</p>}
        {explanationSource && <p className="agent-step-hint">{explanationSource}</p>}
      </div>
      <WarningsCard warnings={data.warnings} />
    </div>
  );
}

/** GET /api/franchise-brands/detail 결과 — 가맹 현황(개점·해지·폐업률·창업비). */
function FranchiseBrandDetailToolCard({ data }: { data: FranchiseBrandDetail }) {
  const tiles = [
    hasNumber(data.frcs_cnt) ? { label: "가맹점 수", value: `${data.frcs_cnt}개` } : null,
    hasNumber(data.new_open_cnt) ? { label: "신규 개점", value: `${data.new_open_cnt}개` } : null,
    hasNumber(data.contract_end_cnt) ? { label: "계약 종료", value: `${data.contract_end_cnt}건` } : null,
    hasNumber(data.contract_cancel_cnt) ? { label: "계약 해지", value: `${data.contract_cancel_cnt}건` } : null,
    hasNumber(data.churn_pct) ? { label: "폐업률", value: fmtPct(data.churn_pct) } : null,
    hasNumber(data.startup_cost_total_manwon) ? { label: "총 창업비용", value: fmtMan(data.startup_cost_total_manwon) } : null,
  ].filter((t): t is { label: string; value: string } => t !== null);

  return (
    <div className="verdict-report">
      <VerdictHeadline
        tone="info"
        eyebrow="가맹 정보"
        text={`${data.brand_nm}${data.hq_conm ? ` (${data.hq_conm})` : ""} 가맹 정보입니다.${data.yr ? ` (${data.yr}년 기준)` : ""}`}
      />
      <div className="card report-block">
        <TileRow tiles={tiles} />
      </div>
    </div>
  );
}

/** 실제로 실행한 도구(decide·lease_verdict 등)의 판정을 구조화해 보여준다.
 * site_score(기본 경로, 도구를 부르지 않음)나 결과가 없으면 아무것도 그리지
 * 않는다 — 값을 지어내지 않고, 실행되지 않은 도구를 실행된 것처럼 보이면 안 된다. */
function ToolResultCard({
  tool,
  toolResult,
  site,
  onRetry,
  onRestart,
}: {
  tool?: string;
  toolResult?: Record<string, unknown> | null;
  site?: { lat: number; lng: number } | null;
  onRetry: () => void;
  onRestart: () => void;
}) {
  if (!tool || tool === "site_score" || !toolResult) return null;

  switch (tool) {
    case "decide":
      return (
        <VerdictReport
          loading={false}
          error=""
          result={toolResult as unknown as DecideResponse}
          onRetry={onRetry}
          onRestart={onRestart}
        />
      );
    case "lease_verdict":
      return (
        <LeaseVerdictReport
          loading={false}
          error=""
          result={toolResult as unknown as LeaseVerdictResponse}
          request={null}
          onRetry={onRetry}
          onRestart={onRestart}
        />
      );
    case "purchase_verdict":
      return (
        <PurchaseVerdictReport
          loading={false}
          error=""
          result={toolResult as unknown as PurchaseVerdictResponse}
          request={null}
          onRetry={onRetry}
          onRestart={onRestart}
        />
      );
    case "diagnose":
      return (
        <DiagnoseReport
          loading={false}
          error=""
          result={toolResult as unknown as DiagnoseResponse}
          request={null}
          onRetry={onRetry}
          onRestart={onRestart}
        />
      );
    case "area_search":
      return (
        <AreaSearchReport
          loading={false}
          error=""
          result={toolResult as unknown as AreaSearchResponse}
          onRetry={onRetry}
          onRestart={onRestart}
        />
      );
    case "profitability":
      return <ProfitabilityToolCard data={toolResult as unknown as ProfitabilityAnalyzeResponse} />;
    case "property_market":
      return <PropertyMarketToolCard data={toolResult as unknown as PropertyMarketResult} />;
    case "trends":
      return <TrendsToolCard data={toolResult as unknown as TrendsResponse} />;
    case "cannibalization":
      return <CannibalizationToolCard data={toolResult as unknown as CannibalizationResponse} />;
    case "isochrone":
      return <IsochroneToolCard data={toolResult as unknown as IsochroneResponse} />;
    case "territory":
      return (
        <TerritorySection
          territory={toolResult as unknown as TerritoryAnalysisResponse}
          lat={site?.lat ?? null}
          lng={site?.lng ?? null}
        />
      );
    case "real_competition":
      return <RealCompetitionToolCard data={toolResult as unknown as RealCompetitionBundleResponse} />;
    case "academy_cluster":
      return (
        <div className="verdict-report">
          <AcademyClusterBlock data={toolResult as unknown as AcademyClusterResponse} />
        </div>
      );
    case "lease_feasibility":
      return <LeaseFeasibilityToolCard data={toolResult as unknown as LeaseFeasibilityResponse} />;
    case "franchise_brand_detail":
      return <FranchiseBrandDetailToolCard data={toolResult as unknown as FranchiseBrandDetail} />;
    case "area_insights":
      return <AreaInsightsSection data={toolResult as unknown as AreaInsightsResponse} loading={false} />;
    case "analogs":
      return <AnalogsToolCard data={toolResult as unknown as AnalogsResponse} />;
    default:
      // 아직 이 화면이 모르는 도구(추후 GOALS.md에 늘어날 도구들) — 지어내지
      // 않고 조용히 건너뛴다. site_score 근거는 아래에서 그대로 보인다.
      return null;
  }
}

/** "...**굵게**..." 안의 별표 쌍을 굵은 조각/일반 조각으로 나눈다. split의
 * 캡처 그룹 규칙상 홀수 인덱스가 항상 **로 감싸였던 부분이다. */
function renderInlineRuns(runs: InlineRun[]): ReactNode[] {
  return runs.map((run, i) => (run.bold ? <strong key={i}>{run.text}</strong> : <span key={i}>{run.text}</span>));
}

function SourceLine({ source }: { source?: string }) {
  // 숫자 옆에 출처를 항상 병기한다 — "점수만 주고 계산을 숨기는 도구"가 되지
  // 않기 위한 최소 장치(GrowthFactor의 traced-to-source).
  if (!source) return null;
  return <p className="answer-viz__source">출처 · {source}</p>;
}

function SectionVisualView({ visual }: { visual: SectionVisual }) {
  if (visual.kind === "gauges") {
    // 서울 상위 %는 낮을수록 좋다 — 채움 폭 100-N (SeoulRankGauges와 같은 규칙).
    return (
      <div className="answer-viz" aria-hidden>
        {visual.rows.map((r) => (
          <div key={r.label} className="answer-viz__row">
            <span className="answer-viz__label">{r.label}</span>
            <span className="answer-viz__track"><span style={{ width: `${Math.max(2, 100 - r.pct)}%` }} /></span>
            <span className="answer-viz__val">상위 {Math.round(r.pct)}%</span>
          </div>
        ))}
        <p className="answer-viz__hint">{visual.hint}</p>
        <SourceLine source={visual.source} />
      </div>
    );
  }
  if (visual.kind === "compare") {
    const max = Math.max(...visual.rows.map((r) => r.value), 1);
    return (
      <div className="answer-viz" aria-hidden>
        {visual.rows.map((r) => (
          <div key={r.label} className="answer-viz__row">
            <span className="answer-viz__label">{r.label}</span>
            <span className="answer-viz__track"><span style={{ width: `${Math.max(2, (r.value / max) * 100)}%` }} /></span>
            <span className="answer-viz__val">{r.display}</span>
          </div>
        ))}
        <p className="answer-viz__hint">{visual.unit} 비교</p>
        <SourceLine source={visual.source} />
      </div>
    );
  }
  return (
    <div className="answer-viz" aria-hidden>
      <div className="answer-viz__row">
        <span className="answer-viz__label">{visual.label}</span>
        <span className={`answer-viz__track${visual.inverse ? " answer-viz__track--inverse" : ""}`}>
          <span style={{ width: `${Math.max(2, Math.min(100, visual.score))}%` }} />
        </span>
        <span className="answer-viz__val">{Math.round(visual.score)}점{visual.inverse ? " · 높을수록 불리" : ""}</span>
      </div>
    </div>
  );
}

function AnswerBlockView({ block, site }: { block: AnswerBlock; site?: SiteScoreResponse | null }) {
  if (block.kind === "table") {
    return (
      <div className="agent-answer__block agent-answer__table-wrap">
        {block.title && <h4 className="agent-answer__section-title">{block.title}</h4>}
        <table className="agent-answer__table">
          <thead>
            <tr>
              {block.header.map((cell, i) => (
                <th key={i}>{renderInlineRuns(cell)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, ri) => {
              // "서울 상위 N%"가 있는 행은 막대를 같이 그린다. 상위 1%와 99%가
              // 같은 크기 텍스트로만 나오면 훑어서는 차이가 안 잡힌다 — 채움
              // 폭(100-N)으로 즉시 잡히게 한다.
              const topPct = rowTopPercent(row);
              return (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci}>
                      {renderInlineRuns(cell)}
                      {topPct !== null && ci === row.length - 1 && (
                        <span className="agent-answer__rankbar" aria-hidden>
                          <span style={{ width: `${Math.max(2, Math.min(100, 100 - topPct))}%` }} />
                        </span>
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }
  if (block.kind === "list") {
    const items = block.items.map((item, i) => <li key={i}>{renderInlineRuns(item)}</li>);
    const className = "agent-answer__block agent-answer__list";
    const list = block.ordered ? <ol className={className}>{items}</ol> : <ul className={className}>{items}</ul>;
    if (!block.title) return list;
    return (
      <div className="agent-answer__block">
        <h4 className="agent-answer__section-title">{block.title}</h4>
        {list}
      </div>
    );
  }
  // 산문 섹션 밑에 그 섹션의 엔진 값으로 미니 차트를 붙인다 — 숫자가 문장에
  // 묻히면 훑어서는 크기가 안 잡힌다(사용자 피드백). LLM 문장을 파싱하지 않고
  // 응답의 구조화 값만 쓴다(lib/answerSectionVisuals 참고).
  const visual = block.title && site ? sectionVisual(block.title, site) : null;
  return (
    <div className="agent-answer__block agent-answer__section">
      {block.title && <h4 className="agent-answer__section-title">{block.title}</h4>}
      <p className="agent-answer__section-body">{renderInlineRuns(block.body)}</p>
      {visual && <SectionVisualView visual={visual} />}
    </div>
  );
}

/** 질문 틀. 누르면 **입력창에 틀만 채우고 보내지 않는다** — 첫 「○○」가 선택돼
 *  있어 바로 자기 장소·업종을 치면 된다.
 *
 *  2026-11-06: 종전엔 "신촌역 카페 자리 어때?"를 누르면 그 질문이 바로 분석됐다.
 *  소유자 지적 — 사용자는 자기 자리를 알고 싶은데 남의 예시(신촌역)에 대한 답이
 *  나오는 건 아무 의미가 없다. 예시는 묻는 법을 보여 주는 데서 멈춘다. */
const EXAMPLES = [
  { icon: "Search", text: "○○역 근처에 ○○ 열어도 괜찮을까?" },
  { icon: "Analytics", text: "○○동 30평 월세 300이면 계약해도 될까?" },
  { icon: "Rocket", text: "○○동이랑 ○○동 중에 ○○ 차리기 어디가 나아?" },
];
const TEMPLATE_SLOT = "○○";

/** backend/app/routers/agent.py의 AgentAskResponse·AgentAnswerResponse가 내려주는
 * suggested_questions는 frontend-next/lib/api.ts의 타입에는 아직 없다(이 작업은
 * lib/api.ts를 건드릴 수 없는 범위라 타입을 못 고친다) — 런타임 응답에는 실려
 * 오므로 여기서만 느슨한 타입으로 읽는다. */
type WithSuggestedQuestions = { suggested_questions?: unknown };

/** 이미 계산한 도구는 backend가 다시 권하지 않으므로(_suggested_questions) 여기서
 * 값을 만들어내지 않고, 문자열이 아닌 항목만 걸러낸다. */
function suggestedQuestionsOf(result: AgentAskResponse | null): string[] {
  const list = (result as (AgentAskResponse & WithSuggestedQuestions) | null)?.suggested_questions;
  if (!Array.isArray(list)) return [];
  return list.filter((q): q is string => typeof q === "string" && q.trim() !== "");
}

/** 응답을 기다리는 동안 보여줄 예상 단계(실제 트레이스는 응답에 담겨 온다). */
// "주소 좌표 변환"은 뺐다 — 내부 전처리라 사용자가 이해할 수 없는 말이고,
// 이해 못 할 단계를 보여 주는 건 진행감이 아니라 소음이다(사용자 피드백).
const LOADING_STEPS = ["도로망 구조 분석", "배후 인구·수요 조회", "경쟁 업종 탐색", "종합 점수 산출"];

//: 다음 단계로 넘어가는 간격(ms). 전형적인 분석이 4~7초라 4단계 × 1.3초면
//: 마지막 단계가 응답 도착까지 자연스럽게 돌고 있게 된다.
const LOADING_STEP_MS = 1300;

export interface AgentResultLocation {
  lat: number;
  lng: number;
  address: string;
}

/** 지난 턴을 한 줄로 접을 때 쓰는 결론 문장. 스레드와 세션 목록이 같은 규칙을
 *  쓰도록 한 곳에 둔다. */
/** fold면 「자세히 보기」로 접고, 아니면 그대로 편다. */
function MaybeFold({ fold, summary, children }: { fold: boolean; summary: string; children: ReactNode }) {
  if (!fold) return <>{children}</>;
  // 휴대폰에선 이 접힘 줄이 입력창 바로 위(화면 맨 아래)에 있어, 펴도 펼친 근거가
  // 화면 밖 아래로만 생겨 아무 일도 안 일어난 듯 보였다. 펼 때 접힘 줄이 화면
  // 아래쪽 절반에 있으면 줄을 화면 위쪽으로 올려 근거 첫머리가 보이게 한다.
  const onToggle = (e: SyntheticEvent<HTMLDetailsElement>) => {
    const el = e.currentTarget;
    if (!el.open) return;
    const summaryEl = el.querySelector("summary");
    if (!summaryEl) return;
    const rect = summaryEl.getBoundingClientRect();
    if (rect.top > window.innerHeight * 0.5) {
      summaryEl.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  };
  return (
    <details className="agent-more" onToggle={onToggle}>
      <summary>{summary}</summary>
      {children}
    </details>
  );
}

const FOUNDER_ICON = { go: "🟢", conditional: "🟡", no: "🔴" } as const;

/** 판정할 조건(월세·매입가·면적 등)을 사용자가 준 질문인가. 이때만 신호등 카드를 편다. */
function hasConditions(result: AgentAskResponse): boolean {
  return Object.keys(result.parameters ?? {}).length > 0;
}

function foldVerdict(result: AgentAskResponse, failReason = ""): string {
  // 결정 카드가 있으면 접힌 한 줄도 결정이다 — 종전 묘사("유입 조건은 좋지만…")가
  // 아니라 "🟡 조건이 맞으면 해볼 만합니다 · 월세 234만원 이하".
  if (result.founder) {
    const cap = result.founder.rent_cap_man;
    return `${FOUNDER_ICON[result.founder.signal]} ${result.founder.headline}${cap && cap > 0 ? ` · 월세 ${cap.toLocaleString()}만원 이하` : ""}`;
  }
  return (
    result.site?.verdict_one_liner ||
    (result.answer
      ? result.answer.replace(/\*\*/g, "").split(/\r?\n/).map((l) => l.trim()).filter(Boolean).slice(0, 2).pop() || ""
      : "") ||
    (result.site ? `${Math.round(result.site.total_score)}점 · ${result.site.grade}` : "") ||
    // 2026-11-07 UX 루프: 답을 못 쓴 질문도 다음 질문을 하면 「분석 완료」로 접혀, 실패가
    // 성공처럼 남았다(이어 물을 때 앞 질문을 다시 물어야 하는지 모름).
    // 2026-11-08 UX 루프: 이유가 있는 실패(400 「'○○'에서 분석할 위치를 찾지 못했어요 — 서울
    // 안만…」)도 다음 질문 뒤 「답변을 쓰지 못했습니다」로 접혀, 안내가 고장처럼 남았다.
    // 이유가 있으면 그 안내를 그대로 남긴다.
    (result.answer_source === "failed" ? failReason || "답변을 쓰지 못했습니다" : "분석 완료")
  );
}

function AgentSiteScoreCard({ result }: { result: AgentAskResponse }) {
  if (!result.site) return null;
  return (
    <div className="agent-score card">
      <div className="agent-score__head">
        <div>
          <span className="agent-score__title">종합 점수</span>
          <div className="agent-score__num">
            {Math.round(result.site.total_score)}
            <span className="agent-score__den">/100</span>
          </div>
        </div>
        <span className="agent-score__grade">{result.site.grade}</span>
      </div>
      <ul className="agent-score__bars">
        {result.site.subscores.map((score) => {
          const raw = score.score ?? 0;
          const value = Math.max(0, Math.min(100, raw));
          const inverse = isInverseMetric(score.key);
          return (
            <li key={score.key} className="agent-subscore">
              <span className="agent-subscore__label">
                {score.label}
                {inverse && score.score != null && <em className="agent-subscore__direction">높을수록 불리</em>}
              </span>
              <span className="agent-subscore__track">
                <span
                  className={`agent-subscore__fill${inverse ? " agent-subscore__fill--inverse" : ""}`}
                  style={{ width: `${value}%` }}
                />
              </span>
              <span className="agent-subscore__val">
                {Math.round(raw)}
                {typeof score.rank_top_pct === "number" && (
                  <em className="agent-subscore__rank">상위 {Math.round(score.rank_top_pct)}%</em>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function SavedAnalysisPanel({
  result,
  onClose,
  onRetry,
  onRestart,
}: {
  result: AgentAskResponse;
  onClose: () => void;
  onRetry: () => void;
  onRestart: () => void;
}) {
  if (!result.site) return null;
  return (
    <div className="agent-saved-analysis card" aria-label="저장된 분석 결과">
      <div className="agent-saved-analysis__head">
        <div>
          <span>저장된 분석 결과</span>
          <strong>{result.address} · {result.business_label}</strong>
        </div>
        <button type="button" onClick={onClose} aria-label="저장된 분석 결과 닫기">닫기</button>
      </div>
      {result.answer && (
        <div className="agent-answer agent-answer--saved">
          {parseAnswerBlocks(result.answer).map((block, index) => (
            <AnswerBlockView key={index} block={block} site={result.site} />
          ))}
        </div>
      )}
      <ToolResultCard
        tool={result.tool}
        toolResult={result.tool_result}
        site={{ lat: result.site.lat, lng: result.site.lng }}
        onRetry={onRetry}
        onRestart={onRestart}
      />
      <WarningsCard warnings={result.site.warnings} />
      <AnalysisVisuals site={result.site} />
      <AgentSiteScoreCard result={result} />
    </div>
  );
}

export function AgentChat({ onResult, onAsk, emptyExtra, parcelAnalysis, seed }: {
  onResult?: (loc: AgentResultLocation | null) => void;
  /** 첫 화면에서 고른 장소로 상담을 열 때 — 입력창에 「○○역 근처에 ○○ 열어도
   *  괜찮을까?」 틀을 그 장소로 채운다. nonce가 바뀔 때마다 한 번. (2026-11-06,
   *  사용자 테스트 §9-2: 망원역을 고르고 「AI에게 물어보기」를 눌렀는데 빈 입력창이 떴다.) */
  seed?: { place: string; nonce: number; midChat?: boolean } | null;
  /** 질문을 보낸 순간 — 지도 위 창이 접혀 있으면 진행 단계가 안 보이므로 올린다. */
  onAsk?: () => void;
  /** 빈 첫 화면에만 붙는 보조 입구(단계별 목적 선택 등). 대화가 시작되면
      사라진다 — 입력창 아래에 남으면 답변이 채팅창 밑으로 내려간 것처럼 보인다. */
  emptyExtra?: ReactNode;
  /** 지도에서 누른 필지의 판정. **스레드에 카드로 들어간다.**

      클릭도 결국 "이 땅 뭐야?"라는 질문이므로 답이 나오는 곳을 하나로 둔다 —
      별도 패널을 세 번째 칸으로 두면 같은 정보가 두 곳에 나오고 사용자가 어디를
      봐야 할지 헷갈린다. 지난 턴으로 쌓이므로 **필지를 눌러도 대화가 초기화되지
      않는다**. */
  parcelAnalysis?: StructuredAnalysis | null;
}) {
  const [query, setQuery] = useState("");
  // 인식된 문장을 바로 제출하지 않고 입력창에 넣는다 — 음성은 지명을 자주
  // 틀리는데("여의도동"→"여의도 동") 곧바로 분석을 걸면 엉뚱한 곳을 분석하고
  // 사용자는 이유를 모른다. 고칠 기회를 남긴다.
  // 인식한 말을 **덮어쓰지 않고 이어 붙인다.** 한 번 누르면 한 문장만 받으므로
  // (useSpeechInput의 continuous=false 주석 참조), 길게 말하려면 여러 번 눌러야
  // 하는데 덮어쓰면 마지막 문장만 남는다 — 실제로 소유자가 그렇게 막혔다.
  const appendTranscript = useCallback((text: string) => {
    setQuery((prev) => {
      const before = prev.trimEnd();
      if (!before) return text;
      // 앞말이 조사·어미로 끝나면 붙여 읽히도록 공백 하나만 넣는다.
      return `${before} ${text}`;
    });
  }, []);
  const speech = useSpeechInput(appendTranscript);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // 서버 쪽 실패(5xx·연결 끊김·시간 초과)면 같은 질문을 다시 보낼 단추를 단다 (2026-11-08 UX 자동 개선).
  const [errorRetryable, setErrorRetryable] = useState(false);
  const [result, setResult] = useState<AgentAskResponse | null>(null);
  const [latestAnalysis, setLatestAnalysis] = useState<ChatResultSnapshot | null>(null);
  const [savedAnalysisOpen, setSavedAnalysisOpen] = useState(false);
  const [answerLoading, setAnswerLoading] = useState(false);
  // 상담 스트림이 지금 하는 일("신촌역 · 카페 분석 중"). 글이 나오기 전까지 보여 준다.
  const [streamStatus, setStreamStatus] = useState("");
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  // 질문 틀의 「○○」를 다 안 채우고 보냈을 때 한 줄 안내(보내지 않는다).
  const [slotHint, setSlotHint] = useState("");
  /** 질문 틀을 입력창에 채우고 첫 「○○」를 선택해 둔다(보내지 않는다). */
  // 2026-11-07 UX 자동 개선: 첫 화면에서 장소를 고르고 「AI에게 물어보기」로 열면 틀은 채워지는데
  // 커서가 글 끝(「괜찮을까?」 뒤)에 있어, 업종을 치면 문장 뒤에 붙었다. 칩을 누를 때는 「○○」가
  // 선택됐다. 효과(seed)에서 채울 땐 다음 프레임에 아직 입력창 값이 옛 글이라 선택이 빗나간 것 —
  // 값이 틀과 같아질 때까지 몇 프레임 기다렸다 고른다.
  const fillTemplate = useCallback((text: string) => {
    setQuery(text);
    let tries = 0;
    const select = () => {
      const el = composerRef.current;
      if (!el) return;
      if (el.value !== text && tries++ < 10) {
        window.requestAnimationFrame(select);
        return;
      }
      el.focus();
      const at = text.indexOf(TEMPLATE_SLOT);
      if (at >= 0) el.setSelectionRange(at, at + TEMPLATE_SLOT.length);
      else el.setSelectionRange(text.length, text.length);
    };
    window.requestAnimationFrame(select);
  }, []);
  // 결정 카드는 빠른 요청으로 먼저 그리고, 경쟁·추세 같은 깊은 분석은 같은 질문을
  // 한 번 더 보내 뒤에서 채운다. 그동안 「자세히 보기」에 진행 중이라고 적는다.
  const [detailLoading, setDetailLoading] = useState(false);
  const [failReason, setFailReason] = useState("");
  // 다른 대화를 열거나 새 질문으로 바뀌면 지난 실패 이유는 지운다.
  const resultQuery = result?.query;
  useEffect(() => setFailReason(""), [resultQuery]);
  // 뒤따르는 요청이 오는 사이 사용자가 새로 물으면 늦게 온 답이 새 답을 덮으면 안 된다.
  const runSeqRef = useRef(0);
  // 조건이 붙은 질문은 도구를 더 돌려 10초를 넘긴다. 기다리는 동안 아무 말이
  // 없으면 멈춘 것처럼 보이므로, 6초가 지나면 오래 걸리는 게 정상이라고 알린다.
  const [slowHint, setSlowHint] = useState("");
  // 지금 돌고 있는 단계. 실제 진행 신호는 없지만(백엔드 호출은 한 번이다)
  // 다섯 개가 동시에 돌면 진행감이 없다(사용자 피드백) — 하나씩 끝내며
  // 내려가고, 마지막 단계는 응답이 올 때까지 돈다.
  const [loadingStep, setLoadingStep] = useState(0);

  useEffect(() => {
    if (!loading) return;
    setLoadingStep(0);
    const timer = setInterval(
      () => setLoadingStep((i) => Math.min(i + 1, LOADING_STEPS.length - 1)),
      LOADING_STEP_MS,
    );
    return () => clearInterval(timer);
  }, [loading]);
  const [copied, setCopied] = useState(false);
  // 앞 대화 요약(최근 몇 턴) — 백엔드가 '그럼 옆 골목은?' 같은 후속 질문에서
  // 주소·업종을 이어받을 수 있게 매 요청에 실어 보낸다.
  const [history, setHistory] = useState<AgentHistoryTurn[]>([]);
  // 지나간 턴들 — GPT처럼 대화가 쌓여야 한다(사용자 피드백: "하나 물어보면
  // 끝"). 지난 결과 전체를 다 그리면 보고서 여러 장이 쌓여 못 읽으므로,
  // 지난 턴은 질문 + 한 줄 결론으로 접고 최신 결과만 전체로 그린다.
  // 지난 턴 — 질문·한 줄 결론과 **그때 받은 답변 본문**까지 함께 남긴다.
  // 2026-08-23: 종전엔 한 줄 결론만 남기고 답변을 버렸다("보고서 더미를 막는다").
  // 그런데 이어 물으면 앞 답변이 통째로 사라져, 소유자에게는 "답변이 사라지고
  // 맨 아래로 간다"로 보였다. 서술은 남기고, 무거운 것(지도·차트·점수 막대·도구
  // 카드)만 최신 턴에서 그린다.
  const [turns, setTurns] = useState<ChatSessionTurn[]>([]);
  const [lastQuery, setLastQuery] = useState("");
  // 왼쪽 패널이 보여 주는 대화 목록. 저장은 브라우저 localStorage뿐이다
  // (계정·서버 저장이 없다 — lib/chatSessions.ts 주석 참고).
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  // 뜬 질의창은 대화 목록을 접어 둔다(styles/map-chat.css). 「지난 대화」로 그 위에 펼친다.
  const [listOpen, setListOpen] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const snapshotLoadRef = useRef(0);
  const currentResultRef = useRef<HTMLDivElement | null>(null);
  const savedAnalysisRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setSessions(loadChatSessions());
  }, []);

  // 지도에서 누른 필지를 스레드 맨 아래에 카드로 붙인다.
  //
  // **같은 필지를 두 번 눌러도 두 번 붙지 않는다** — 지도는 같은 폴리곤을 다시
  // 눌렀을 때도 콜백을 부르므로, PNU가 바뀐 경우만 새 턴으로 본다. 스레드는
  // 초기화하지 않는다(진행 중인 대화가 지워지면 안 된다).
  const lastCardPnuRef = useRef<string | null>(null);
  useEffect(() => {
    const turn = parcelTurnFor(parcelAnalysis, lastCardPnuRef.current);
    if (!turn) return;
    lastCardPnuRef.current = turn.landAnalysis.subject.pnu;
    setTurns((prev) => [...prev, turn]);
  }, [parcelAnalysis]);
  // 이어 물은 질문의 답이 어디에 그려지는지 — 입력창을 아래에 고정한 뒤로는
  // 새 답이 화면 위쪽에 렌더돼 아무 일도 안 일어난 것처럼 보인다. 방금 물은
  // 질문을 보이는 자리로 끌어온다.
  const currentTurnRef = useRef<HTMLParagraphElement | null>(null);

  async function run(raw: string) {
    const text = raw.trim();
    if (!text || loading) return;
    // 2026-11-06 UX 루프: 질문 틀의 「○○」를 하나만 바꾸고 Enter를 누르면 「망원역 근처에
    // ○○ 열어도 괜찮을까?」가 그대로 나갔다 — 업종 없이 분석돼 엉뚱한 답이 온다. 보내지
    // 않고 남은 「○○」를 골라 두고, 무엇을 채울지 한 줄로 알린다.
    const slotAt = text.indexOf(TEMPLATE_SLOT);
    if (slotAt >= 0) {
      setSlotHint("「○○」 자리를 채워 주세요 — 동네·역 이름이나 업종(예: 카페)을 넣으면 됩니다.");
      const el = composerRef.current;
      if (el) {
        const at = el.value.indexOf(TEMPLATE_SLOT);
        el.focus();
        if (at >= 0) el.setSelectionRange(at, at + TEMPLATE_SLOT.length);
      }
      return;
    }
    setSlotHint("");
    setLoading(true);
    onAsk?.();
    setSavedAnalysisOpen(false);
    setSlowHint("");
    setError("");
    setErrorRetryable(false);
    // 이전 결과는 지우되 버리지 않는다 — 질문+한 줄 결론으로 접어 스레드에 남긴다.
    if (result) {
      setTurns((prev) => [
        ...prev,
        { query: lastQuery, verdict: foldVerdict(result, failReason), answer: result.answer ?? "" },
      ]);
    } else if (error && lastQuery && lastQuery !== text) {
      // 통째로 실패한 질문도 접어 남긴다 — 안 그러면 다음 질문을 보내는 순간 흔적 없이 사라진다.
      setTurns((prev) => [...prev, { query: lastQuery, verdict: "답변을 받지 못했습니다", answer: "" }]);
    }
    setLastQuery(text);
    setResult(null);
    setCopied(false);
    onResult?.(null);
    try {
      const seq = ++runSeqRef.current;
      // 앞 대화를 서술 단계에도 넘겨야 하므로, 이번 턴을 붙이기 전 값을 잡아 둔다.
      const prevTurns = history;

      // 2026-11-06: 글 답은 상담 스트림(/ask/stream)이 쓴다. 상담 모델이 질문을 읽고
      // 필요하면 엔진을 도구로 부르며 답을 글자 단위로 흘린다. 종전엔 엔진 결과를 「결론·
      // 왜·할 일」 세 칸, 350자에 채웠다 — 소유자 지적: "그냥 GPT보다 못하고 틀에 박힌
      // 답만 한다". 결정 카드(fast)와 깊은 분석 카드(loop:false)는 종전대로 나란히 돈다.
      // 스트림을 못 쓰면(모델 없음·실패) 종전 경로(/answer brief, talk)로 쓴다.
      let streamText = "";
      let fastArrived = false;
      setStreamStatus("");
      setFailReason("");
      setAnswerLoading(true);
      const showStreamText = () => {
        if (seq !== runSeqRef.current || !fastArrived) return;
        setResult((prev) => (prev ? { ...prev, answer: streamText, answer_source: "llm" } : prev));
      };
      const streamRequest = streamAgentAsk({ query: text, history }, (event) => {
        if (seq !== runSeqRef.current) return;
        if (event.type === "status") {
          setStreamStatus(event.text);
        } else if (event.type === "reset") {
          streamText = "";
          showStreamText();
        } else if (event.type === "delta") {
          streamText += event.text;
          showStreamText();
        }
      });

      // 1단계: 결정 카드만 먼저(fast) — site_score 하나라 2초 안팎이다(소유자 지적 10/24).
      // 깊은 분석(경쟁 구조·성장 추이)은 같은 순간 출발시킨다. 결과는 아래 deepen()이 받는다.
      // talk:false — 위치가 없는 질문의 글 답은 스트림이 쓰므로 여기서 또 쓰지 않는다.
      const deepRequest = api.agentAsk({ query: text, include_answer: false, history, loop: false, talk: false });
      deepRequest.catch(() => undefined);
      const fastRes = await api.agentAsk({ query: text, include_answer: false, history, fast: true, talk: false });
      if (seq !== runSeqRef.current) return;
      fastArrived = true;
      setQuery("");
      setResult({
        ...fastRes,
        answer: streamText || fastRes.answer,
        answer_source: streamText ? "llm" : fastRes.answer_source,
      });
      if (fastRes.site) onResult?.({ lat: fastRes.site.lat, lng: fastRes.site.lng, address: fastRes.address });
      setLoading(false);
      // 이번 턴은 빠른 답이 온 즉시 붙인다 — 글을 기다리는 사이 이어 물어도
      // 장소·업종·월세가 이어져야 한다. 답 본문은 다 쓰인 뒤 rememberAnswer로 채운다.
      setHistory((prev) =>
        appendAgentHistory(prev, {
          query: text,
          address: fastRes.address,
          business_type: fastRes.business_type,
          business_label: fastRes.business_label,
          // 업종 코드와 수치도 실어야 후속 질문이 같은 업종·같은 월세로 이어진다.
          upjong: fastRes.upjong ?? null,
          params: fastRes.parameters ?? {},
        }),
      );
      const rememberAnswer = (answer: string) => {
        if (!answer) return;
        setHistory((prev) =>
          prev.map((t, i) => (i === prev.length - 1 && t.query === text ? { ...t, answer } : t)),
        );
      };

      async function writeAnswer(base: AgentAskResponse) {
        try {
          // 사용자가 실제로 물은 문장을 같이 보낸다 — 이게 없으면 '야간 장사',
          // '20대 여성 타깃' 같은 조건이 서술에서 통째로 사라진다.
          const answerBody: AgentAnswerRequest = {
            site: base.site ?? fastRes.site!,
            place: base.address,
            business_label: base.business_label,
            radius_m: (base.site ?? fastRes.site!).radius_m,
            query: text,
            // 계약·매입·비교·진단 도구를 돌렸으면 그 판정이 답의 중심이 된다.
            tool: base.tool ?? "site_score",
            tool_result: base.tool_result ?? null,
            // 이게 빠져 있어서 후속 질문의 답이 매번 첫 질문처럼 써졌다.
            history: prevTurns,
            brief: true,
          };
          const written = await api.agentAnswer(answerBody);
          if (seq !== runSeqRef.current) return;
          setResult((prev) => (prev ? { ...prev, ...written } : prev));
          rememberAnswer(written.answer);
        } catch {
          if (seq === runSeqRef.current) setResult((prev) => (prev ? { ...prev, answer_source: "failed" } : prev));
        }
      }

      // 위치가 없는 질문인데 스트림을 못 썼다 — 종전 talk 답으로 쓴다.
      async function writeTalk() {
        try {
          const res = await api.agentAsk({ query: text, include_answer: false, history: prevTurns });
          if (seq !== runSeqRef.current) return;
          setResult((prev) => (prev ? { ...prev, answer: res.answer, answer_source: res.answer_source } : res));
          rememberAnswer(res.answer);
        } catch (err) {
          // 400은 서버가 사람 말로 이유를 준다(「'부산'에서 분석할 위치를 찾지 못했어요 — 서울 안만…」).
          // 전엔 그걸 버리고 「잠시 후 다시」+「같은 질문 다시 묻기」만 보여 줘, 다시 눌러도 똑같이
          // 실패하는 길로 보냈다 (2026-11-07 UX 자동 개선).
          const reason = err instanceof ApiClientError && err.status === 400 ? err.message : "";
          if (seq === runSeqRef.current) {
            setFailReason(reason);
            setResult((prev) => (prev ? { ...prev, answer_source: "failed" } : prev));
          }
        }
      }

      async function deepen(): Promise<AgentAskResponse> {
        setDetailLoading(true);
        try {
          const full = await deepRequest;
          if (seq !== runSeqRef.current || !full.site) return fastRes;
          // 글이 먼저 와 있을 수 있다 — 도구 결과만 얹고 글은 지킨다.
          setResult((prev) =>
            prev
              ? { ...full, answer: prev.answer || full.answer, answer_source: prev.answer ? prev.answer_source : full.answer_source }
              : full,
          );
          return full;
        } catch {
          /* 깊은 분석이 실패해도 결정 카드와 기본 분석은 남는다. */
          return fastRes;
        } finally {
          if (seq === runSeqRef.current) setDetailLoading(false);
        }
      }

      const deepened = fastRes.site ? deepen() : Promise.resolve(fastRes);
      const done = await streamRequest;
      if (seq !== runSeqRef.current) return;
      setStreamStatus("");
      const streamed = streamText.trim() || done.response?.answer?.trim() || "";
      if (!done.fallback && streamed) {
        setResult((prev) => (prev ? { ...prev, answer: streamed, answer_source: "llm" } : prev));
        rememberAnswer(streamed);
      } else if (fastRes.site) {
        // 조건이 붙은 질문(월세·매입가)은 그 판정이 답의 중심이라 깊은 분석을 기다린다.
        const conditional = Object.keys(fastRes.parameters ?? {}).length > 0;
        await writeAnswer(conditional ? await deepened : fastRes);
      } else {
        await writeTalk();
      }
      if (seq === runSeqRef.current) setAnswerLoading(false);
      await deepened;
    } catch (e) {
      setError(e instanceof Error ? e.message : "분석에 실패했습니다.");
      setErrorRetryable(!(e instanceof ApiClientError) || e.status >= 500);
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!loading || !currentTurnRef.current) return;
    currentTurnRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [loading, lastQuery]);

  useEffect(() => {
    if (!loading) {
      setSlowHint("");
      return;
    }
    const timer = window.setTimeout(
      () => setSlowHint("조건이 붙은 질문이라 관련 도구까지 돌리는 중이에요 — 조금만 기다려 주세요."),
      6000,
    );
    return () => window.clearTimeout(timer);
  }, [loading]);

  // 답이 올 때마다 목록을 갱신한다. 제목은 그 대화의 첫 질문이다.
  useEffect(() => {
    if (!result || !lastQuery) return;
    const turnList: ChatSessionTurn[] = [
      ...turns,
      { query: lastQuery, verdict: foldVerdict(result, failReason), answer: result.answer ?? "" },
    ];
    const id = sessionId ?? newChatSessionId();
    if (!sessionId) setSessionId(id);
    setSessions((prev) =>
      upsertChatSession(prev, {
        id,
        title: turnList[0]?.query || lastQuery,
        updatedAt: Date.now(),
        turns: turnList,
        history,
      }),
    );
    // history·turns는 이 턴이 끝난 뒤 값이 필요해서 의존성에 둔다.
  }, [result, lastQuery, turns, history, sessionId, failReason]);

  // 대화 본문은 가볍게 localStorage에 두되, 다시 펼쳐야 하는 최신 구조화 결과 1건은
  // IndexedDB에 별도로 보관한다. 답변 서술이 늦게 도착하면 같은 스냅샷을 갱신한다.
  useEffect(() => {
    if (!sessionId || !result?.site) return;
    const updatedAt = Date.now();
    setLatestAnalysis({ sessionId, result, updatedAt });
    void saveChatResultSnapshot(sessionId, result, updatedAt);
  }, [result, sessionId]);

  /** 목록에서 지난 대화를 다시 연다. 글 대화는 즉시 복원하고, 대화별 최신 분석 1건은
   *  IndexedDB에서 따로 불러와 입력창 위의 고정 카드로 되살린다. */
  function openSession(id: string) {
    const target = sessions.find((s) => s.id === id);
    if (!target) return;
    const loadSequence = ++snapshotLoadRef.current;
    setSessionId(id);
    setTurns(target.turns);
    setHistory(target.history);
    setLastQuery("");
    setResult(null);
    setError("");
    setQuery("");
    setLatestAnalysis(null);
    setSavedAnalysisOpen(false);
    onResult?.(null);
    void loadChatResultSnapshot(id).then((snapshot) => {
      if (snapshotLoadRef.current !== loadSequence) return;
      setLatestAnalysis(snapshot);
    });
  }

  function startNewChat() {
    snapshotLoadRef.current += 1;
    setSessionId(null);
    resetConversation();
  }

  function deleteSession(id: string) {
    setSessions((prev) => removeChatSession(prev, id));
    void removeChatResultSnapshot(id);
    if (id === sessionId) startNewChat();
  }

  // 완전히 새 대화 시작 — 앞 턴 요약도 같이 비운다. 그대로 두면 다음 질문이
  // 이전 위치·업종을 이어받은 것처럼 처리돼 엉뚱한 곳을 분석하게 된다.
  function resetConversation() {
    setResult(null);
    setLatestAnalysis(null);
    setSavedAnalysisOpen(false);
    setQuery("");
    setHistory([]);
    setTurns([]);
    setLastQuery("");
    onResult?.(null);
  }

  function openLatestAnalysis() {
    if (!latestAnalysis) return;
    const isCurrentResult = result?.site && result.query === latestAnalysis.result.query;
    if (isCurrentResult) {
      currentResultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    setSavedAnalysisOpen(true);
    window.requestAnimationFrame(() => {
      savedAnalysisRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  // 빈 첫 화면 — 히어로 한 줄과 추천 질문만 둔다. 무언가 시작되면 스레드로 바뀐다.
  const isEmpty = !result && !loading && !error && turns.length === 0;

  const seedNonce = seed?.nonce;
  // 마지막으로 우리가 채운 틀. 입력창 글이 이것 그대로면 사용자가 손대지 않은 것이라
  // 다른 자리를 누르면 바꿔 채운다(라이브 확인 2026-11-06: 새 자리를 눌렀는데 옛 주소가 남았다).
  const seededTextRef = useRef("");
  useEffect(() => {
    if (!seed?.place) return;
    // 대화 중 지도를 눌렀으면(midChat) 사용자가 치던 문장은 덮지 않는다 — 비었거나 우리가
    // 채운 틀 그대로일 때만 바꾼다.
    const untouched = query.trim() === "" || query === seededTextRef.current;
    if (seed.midChat ? !untouched || loading : !isEmpty) return;
    const text = `${seed.place}에서 ${TEMPLATE_SLOT} 열어도 괜찮을까?`;
    seededTextRef.current = text;
    fillTemplate(text);
    // seed가 바뀐 순간에만 — 대화 중 다시 채우지 않는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedNonce]);

  // 이어 물을 말 — 결정 카드가 있으면 카드 바로 아래, 없으면 설명 카드 안에 둔다.
  function suggestionButtons(r: AgentAskResponse) {
    const qs = suggestedQuestionsOf(r);
    if (!qs.length) return null;
    return (
      <div className="agent-chat__examples" role="group" aria-label="이어서 물어보기">
        {qs.map((q) => (
          <button
            key={q}
            type="button"
            className="agent-chat__example"
            disabled={loading}
            onClick={() => {
              setQuery(q);
              void run(q);
            }}
          >
            {q}
          </button>
        ))}
      </div>
    );
  }

  async function copyAnswer() {
    if (!result?.answer) return;
    try {
      await navigator.clipboard.writeText(result.answer);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 클립보드 접근 실패는 조용히 무시 — 사용자가 직접 선택해 복사 가능 */
    }
  }

  const pastCount = sessions.filter((s) => s.id !== sessionId).length;

  return (
    <div className="prism-app">
      {/* 2026-11-07 UX 자동 개선: 뜬 질의창에선 목록이 접혀 저장된 대화를 다시 열 길이
          없었다(SPEC_ANSWER_DECISION §8.5). 목록이 있을 때만 단추를 보인다.
          지금 대화는 세지 않는다 — 첫 대화 중에 「지난 대화 1」이 떠서 눌러 보면 지금
          대화뿐이었다. 지난 대화가 없으면 같은 자리에서 바로 새 대화를 연다(목록이 이
          창의 유일한 「새 대화」 입구였다). */}
      {sessions.length > 0 && !listOpen && (
        <button
          type="button"
          className="prism-history-open"
          onClick={() => (pastCount > 0 ? setListOpen(true) : startNewChat())}
        >
          {pastCount > 0 ? `지난 대화 ${pastCount}` : "새 대화"}
        </button>
      )}
      <ChatSidebar
        sessions={sessions}
        activeId={sessionId}
        onSelect={(id) => {
          openSession(id);
          setListOpen(false);
        }}
        onNew={() => {
          startNewChat();
          setListOpen(false);
        }}
        onDelete={deleteSession}
        open={listOpen}
        onClose={() => setListOpen(false)}
      />

      <section className="prism" aria-label="입지 분석 에이전트">
      {/* 스크롤은 이 영역만 — 입력창은 아래 고정이라 답변 길이에 밀리지 않는다. */}
      <div className="prism__scroll">
        {isEmpty ? (
          <div className="prism__hero">
            <span className="prism__hero-title">어떤 자리를 알아볼까요?</span>
          </div>
        ) : (
          <div className="prism__thread agent-chat">
      {/* 지나간 턴 — 질문과 한 줄 결론만 남긴다. 보고서 전체를 쌓으면 대화가
          아니라 서류 더미가 된다. */}
      {turns.map((t, i) => (
        <div key={i} className="agent-thread__turn">
          <p className="agent-thread__q">{t.query}</p>
          {t.answer ? (
            // 그때 받은 서술을 그대로 — 지도·차트·점수 막대는 최신 턴에만 붙는다.
            // 지난 턴까지 다 그리면 한 화면에 같은 그림이 여러 장 쌓인다.
            <div className="agent-answer card agent-answer--past">
              {parseAnswerBlocks(t.answer).map((b, idx) => (
                <AnswerBlockView key={idx} block={b} />
              ))}
            </div>
          ) : (
            // 목록에서 되살린 옛 대화(답변 본문을 저장하기 전에 만든 것)와,
            // 서술이 실패한 턴은 한 줄 결론만 있다.
            <p className="agent-thread__a">{t.verdict}</p>
          )}
          {/* 필지 클릭으로 들어온 판정 카드. 지도·차트와 달리 지난 턴에도 남긴다 —
              두 필지를 나란히 견주는 것이 이 화면의 주 사용법이다. */}
          {t.landAnalysis && <LandAnalysisCard analysis={t.landAnalysis} />}
        </div>
      ))}
      {/* 지금 묻고 있는 질문도 말풍선으로 — 어떤 질문의 답이 오는 중인지 보인다. */}
      {/* 질문이 통째로 실패해도 말풍선은 남긴다 — 빨간 문장만 남으면 무엇이 실패했는지 모른다
          (2026-11-08 UX 자동 개선, 390px). */}
      {(loading || result || error) && lastQuery && (
        <p className="agent-thread__q" ref={currentTurnRef}>
          {lastQuery}
        </p>
      )}

      {loading && (
        <>
          <ul className="agent-chat__steps" aria-live="polite">
            {LOADING_STEPS.map((label, i) => (
              <li
                key={label}
                className={`agent-chat__step ${
                  i < loadingStep
                    ? "agent-chat__step--done"
                    : i === loadingStep
                      ? "agent-chat__step--running"
                      : "agent-chat__step--pending"
                }`}
              >
                {i < loadingStep ? (
                  <span className="agent-chat__check" aria-hidden="true">✓</span>
                ) : i === loadingStep ? (
                  <span className="agent-chat__spinner" aria-hidden="true" />
                ) : (
                  <span className="agent-chat__dot" aria-hidden="true" />
                )}
                {label}
              </li>
            ))}
          </ul>
          {/* 조건이 붙은 질문(비교·계약·자리찾기)은 도구를 더 돌려 10초를 넘긴다.
              아무 말 없이 기다리게 하면 멈춘 것처럼 보이므로, 오래 걸리는 게
              정상이라는 걸 알려 준다. 예상 시간을 약속하지는 않는다. */}
          {slowHint && <p className="agent-step-hint" aria-live="polite">{slowHint}</p>}
        </>
      )}

      {error && (
        <div role="alert">
          <p className="agent-chat__error">{error}</p>
          {/* 배포 중 503처럼 서버가 잠깐 못 받은 실패는 같은 질문을 다시 치지 않고 보낸다 —
              휴대폰에선 질문을 처음부터 다시 입력해야 했다 (2026-11-08 UX 자동 개선, 390px).
              입력 탓(4xx)은 다시 보내도 같아 달지 않는다. */}
          {errorRetryable && lastQuery && !loading && (
            <div className="agent-step-actions">
              <button
                type="button"
                className="primary-button primary-button--compact"
                onClick={() => void run(lastQuery)}
              >
                같은 질문 다시 묻기
              </button>
            </div>
          )}
        </div>
      )}

      {/* 도구를 돌리지 않은 답 — 글만 보여 준다. 점수·지도·표는 계산하지
          않았으므로 자리를 비워 두는 게 아니라 아예 그리지 않는다. */}
      {result && !result.site && (
        <div className="agent-chat__result">
          <div className="agent-answer card">
            {/* 위치가 없는 질문의 글은 상담 스트림이 쓴다 — 오기 전까지 진행 상태를 보여 준다. */}
            {!result.answer && answerLoading && (
              <p className="agent-step-hint" aria-live="polite">
                {streamStatus ? `${streamStatus}…` : "답변을 쓰는 중이에요…"}
              </p>
            )}
            {!result.answer && !answerLoading && result.answer_source === "failed" && (
              <>
                <p className="agent-step-hint">
                  {failReason || "답변을 쓰지 못했습니다. 잠시 후 다시 물어봐 주세요."}
                </p>
                {/* 같은 질문을 다시 치게 하지 않는다 — 휴대폰에선 질문을 처음부터 다시
                    입력해야 했다 (2026-11-06 UX 자동 개선, 390px). 이유가 있는 실패(failReason)는
                    다시 물어도 같으니 단추를 내지 않는다. */}
                {!failReason && (
                <div className="agent-step-actions">
                  <button
                    type="button"
                    className="primary-button primary-button--compact"
                    onClick={() => void run(result.query)}
                  >
                    같은 질문 다시 묻기
                  </button>
                </div>
                )}
              </>
            )}
            {parseAnswerBlocks(result.answer).map((b, idx) => (
              <AnswerBlockView key={idx} block={b} />
            ))}
            {suggestedQuestionsOf(result).length > 0 && (
              <div className="agent-chat__examples" role="group" aria-label="이렇게 물어보세요">
                {/* 위치 없는 답에 붙는 건 묻는 법의 예시다 — 누르면 틀만 채운다. */}
                {suggestedQuestionsOf(result).map((q) => (
                  <button
                    key={q}
                    type="button"
                    className="agent-chat__example"
                    disabled={loading}
                    onClick={() => fillTemplate(q)}
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {result && result.site && (
        <div className="agent-chat__result" ref={currentResultRef}>
          {/* 결정 카드가 있으면 끝난 단계 목록은 뺀다 — 휴대폰 반 창에서 결정
              카드를 밀어낸다. */}
          {!result.founder && (
            <ul className="agent-chat__steps">
              {/* "주소 좌표 변환"은 결과 기록에서도 숨긴다 — 내부 전처리라 사용자가
                  이해할 수 없는 말이다(사용자 피드백). 백엔드 기록 자체는 그대로
                  둔다: 디버깅에는 필요하고, 숨기는 건 화면의 일이다. */}
              {result.steps.filter((s) => !s.label.includes("좌표 변환")).map((s) => (
                <li key={s.key} className="agent-chat__step agent-chat__step--done">
                  <span className="agent-chat__check" aria-hidden="true">✓</span>
                  {s.label}
                </li>
              ))}
            </ul>
          )}

          <p className="agent-chat__analysis-for">
            <strong>{result.address}</strong> · {result.business_label} 기준
          </p>

          {/* 질문을 좁혀서 분석했으면 숨기지 않고 알린다 — 두 곳 비교를 한 곳
              분석으로 뭉갠 채 답하면 사용자는 비교 결과로 읽는다. */}
          {result.notice && (
            <p className="agent-chat__error" role="status">
              {result.notice}
            </p>
          )}

          {/* 2026-11-06: 맨 위는 **상담 글**이다(docs/SPEC_ANSWER_DECISION.md §9-2).
              종전엔 결정 카드가 맨 위에 서고 글은 「자세히 보기」 안에 접혀 있어서,
              사용자는 질문(평일 낮 vs 주말)에 답하지 않는 🔴 카드만 봤다 — 글과 반대말이기도
              했다. 신호등 카드는 월세·매입가처럼 판정할 조건을 준 질문에서만 글 아래에
              편다. 조건이 없으면 업종 평균 비용으로 낸 판정이라 「참고」로 접는다. */}
          {result.answer ? (
            <div className="agent-answer card">
              {parseAnswerBlocks(result.answer).map((b, idx) => (
                <AnswerBlockView key={idx} block={b} site={result.site} />
              ))}
              {!answerLoading && (
                <div>
                  <SpeakButton text={result.answer} />
                  <button type="button" className="agent-answer__copy" onClick={() => void copyAnswer()}>
                    {copied ? "복사됨 ✓" : "복사"}
                  </button>
                </div>
              )}
            </div>
          ) : answerLoading ? (
            <div className="agent-answer card">
              <p className="agent-step-hint" aria-live="polite">
                {streamStatus ? `${streamStatus}…` : "질문을 읽고 데이터를 확인하는 중이에요…"}
              </p>
            </div>
          ) : result.answer_source === "failed" ? (
            <div className="agent-answer card">
              <p className="agent-step-hint">설명을 쓰지 못했습니다. 아래 분석 결과는 그대로 보실 수 있어요.</p>
            </div>
          ) : null}

          {result.founder && hasConditions(result) && <FounderCard verdict={result.founder} site={result.site} />}
          {!answerLoading && suggestionButtons(result)}

          <MaybeFold
            fold={true}
            summary={
              detailLoading
                ? "근거 자세히 보기 — 경쟁·추세 분석을 마저 계산하는 중…"
                : "근거 자세히 보기 — 판정·점수·지도·그래프"
            }
          >
          {result.founder && !hasConditions(result) && (
            <>
              <p className="agent-step-hint">
                월세·매입가를 넣지 않은 질문이라, 아래 판정은 업종 평균 비용으로 낸 참고치예요.
              </p>
              <FounderCard verdict={result.founder} site={result.site} />
            </>
          )}
          {/* 조건이 붙은 질문(계약·매입·비교·투자·진단·성장세)의 도구 카드 — 근거다. */}
          <ToolResultCard
            tool={result.tool}
            toolResult={result.tool_result}
            site={{ lat: result.site.lat, lng: result.site.lng }}
            onRetry={() => void run(result.query)}
            onRestart={resetConversation}
          />

          {/* site_score 자체의 경고(폴백·표본 부족·도로망 결측 등). 종전엔 같은
              카드를 두 번(설명 아래, 점수 아래) 그렸다 — 한 번만. */}
          <WarningsCard warnings={result.site.warnings} />

          {/* 말로만 하면 어디인지·누가 있는지가 안 잡힌다 — 지도와 그래프로 먼저 보여 준다. */}
          <AnalysisVisuals site={result.site} />

          {/* 비용(아웃플로우) — 보증금·권리금·실거래가는 데이터가 없어, 그 동네
              매물로 바로 연결해 사용자가 직접 확인하게 한다. */}
          {/* 네이버 지도 검색은 중개업소 마커만 떠서 가격이 안 보였다(사용자 실확인).
              네이버 부동산 상가 탭은 매물별 보증금·월세·권리금이 나오고 좌표로
              딥링크가 된다 — 분석 좌표를 그대로 넘긴다. */}
          <a
            className="agent-listings"
            href={`https://new.land.naver.com/offices?ms=${result.site.lat},${result.site.lng},16&a=SG&e=RETAIL`}
            target="_blank"
            rel="noopener noreferrer"
          >
            <span className="agent-listings__title">🏢 이 동네 상가 매물 보기</span>
            {/* 「시세」는 화면에 쓰지 않는다(CONTEXT.md 「가격」 절) — 우리는 그 값을 모른다. */}
            <span className="agent-listings__sub">네이버 부동산에서 매물별 보증금·월세·권리금 확인 →</span>
          </a>

          {/* 점수·세부 항목은 근거로 뒤에 — 결론을 뒷받침하는 자료. */}
          <AgentSiteScoreCard result={result} />
          </MaybeFold>

          <button type="button" className="agent-chat__reset" onClick={resetConversation}>
            새 대화 시작 (위치·업종 초기화)
          </button>
        </div>
      )}

      {savedAnalysisOpen && latestAnalysis?.result.site && result?.query !== latestAnalysis.result.query && (
        <div ref={savedAnalysisRef}>
          <SavedAnalysisPanel
            result={latestAnalysis.result}
            onClose={() => setSavedAnalysisOpen(false)}
            onRetry={() => void run(latestAnalysis.result.query)}
            onRestart={resetConversation}
          />
        </div>
      )}
          </div>
        )}
      </div>

      {/* 입력창은 스크롤 영역 밖 — 대화가 길어져도 늘 같은 자리에 있다.
          Prism 킷의 composer(흰 카드, 모서리 16px, 안쪽 여백 16px)를 그대로 옮겼다.
          위치·업종은 history로 이어받으므로 "월세 300이면?"만 쳐도 된다. */}
      <div className="prism__footer">
        <div className="prism__footer-inner">
          {isEmpty && (
            <div className="prism__pills" role="group" aria-label="예시 질문">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex.text}
                  type="button"
                  className="prism-pill"
                  onClick={() => fillTemplate(ex.text)}
                >
                  <ChatIcon name={ex.icon} size={16} />
                  <span>{ex.text}</span>
                </button>
              ))}
            </div>
          )}

          {isEmpty && emptyExtra && <div className="prism__extra">{emptyExtra}</div>}

          {/* 지금 화면의 답이 바로 그 분석이면 막대를 내리지 않는다 — 같은 내용이
              휴대폰 반 창에서 결정 카드를 가린다. 이어 물어 다른 답으로 넘어갔을
              때만 「분석 결과 보기」로 되돌아갈 길이 된다. */}
          {latestAnalysis?.result.site && result?.query !== latestAnalysis.result.query && (
            <button
              type="button"
              className="agent-result-dock"
              onClick={openLatestAnalysis}
              aria-label={`${latestAnalysis.result.address} ${latestAnalysis.result.business_label} 분석 결과 보기`}
            >
              <span className="agent-result-dock__icon" aria-hidden="true">
                <ChatIcon name="Analytics" size={18} />
              </span>
              <span className="agent-result-dock__body">
                <strong>{latestAnalysis.result.address} · {latestAnalysis.result.business_label}</strong>
                <span>{foldVerdict(latestAnalysis.result)}</span>
              </span>
              <span className="agent-result-dock__action">분석 결과 보기</span>
            </button>
          )}

          {slotHint && (
            <p className="agent-chat__slot-hint" role="status">
              {slotHint}
            </p>
          )}
          <form
            className="prism-composer"
            onSubmit={(e) => {
              e.preventDefault();
              void run(query);
            }}
          >
            <textarea
              ref={composerRef}
              className="prism-composer__input"
              rows={2}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                if (slotHint && !e.target.value.includes(TEMPLATE_SLOT)) setSlotHint("");
              }}
              // Enter로 보낸다. Shift+Enter는 줄바꿈이고, 한글 입력기(IME)
              // 조합 중 Enter는 글자 확정이므로 보내지 않는다.
              onKeyDown={(e) => {
                if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
                e.preventDefault();
                void run(query);
              }}
              placeholder={
                speech.listening
                  ? "듣고 있습니다…"
                  : isEmpty
                    ? "어디에 무엇을 낼지 물어보세요 — 예: 망원동 디저트 카페 자리 어때?"
                    : // 장소 없이 글로만 답한 뒤(talk)엔 「월세 300이면 계약?」 예시가 맞지 않는다 —
                      // 이어받을 자리가 없으니 자리를 대 보라고 권한다 (2026-11-06 UX 자동 개선).
                      // 휴대폰 입력칸은 266px(15px 글씨 약 17자)이라 「이어서 물어보세요 — 」를 붙이면
                      // 두 줄로 꺾여 끝이 잘렸다 — 예시만 남긴다. 대화가 이미 보여 「이어서」는 말 안 해도 읽힌다.
                      result && !result.site
                      ? "예: 망원동 카페 자리 어때?"
                      : followUpExample(result?.parameters)
              }
              aria-label="분석할 위치와 업종"
              disabled={loading}
            />

            <div className="prism-composer__row">
              <span className="prism-composer__hint">
                {loading ? "분석 중…" : speech.listening ? "듣는 중 — 다시 누르면 멈춥니다" : "Enter로 전송 · Shift+Enter 줄바꿈"}
              </span>
              <div className="prism-composer__actions">
                {/* 지원하지 않는 브라우저에서는 버튼을 아예 두지 않는다 — 눌러도
                    아무 일이 없는 버튼은 고장으로 읽힌다. */}
                {speech.supported && (
                  <button
                    type="button"
                    className={`prism-composer__btn${speech.listening ? " prism-composer__btn--listening" : ""}`}
                    onClick={speech.listening ? speech.stop : speech.start}
                    disabled={loading}
                    aria-label={speech.listening ? "음성 입력 멈추기" : "음성으로 질문하기"}
                    aria-pressed={speech.listening}
                  >
                    <ChatIcon name={speech.listening ? "Stop" : "Mic"} size={20} />
                  </button>
                )}
                <button
                  type="submit"
                  className="prism-composer__btn prism-composer__send"
                  disabled={loading || !query.trim()}
                  aria-label="전송"
                >
                  <ChatIcon name="ArrowUp" size={20} />
                </button>
              </div>
            </div>
          </form>

          <span className={`prism__foot${isEmpty ? "" : " prism__foot--chat"}`}>
            공시지가·생활인구 같은 공공데이터로 계산한 추정입니다 — 계약 전에 현장과 등기를 확인해 주세요.
          </span>

          {speech.error && (
            <p className="agent-chat__mic-error" role="status">
              {speech.error}
            </p>
          )}
        </div>
      </div>
      </section>
    </div>
  );
}
