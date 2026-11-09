"use client";

import { useEffect, useMemo, useState } from "react";
import { HIGHER_IS_WORSE, rankTopPctText } from "@/lib/metricDirection";

import { api, salesBenchmarkScopeNote, type AdaptiveIndustryProfile, type AdaptiveUserType, type SiteScoreResponse } from "@/lib/api";

const LABELS: Record<string, string> = {
  demand: "수요", competition: "경쟁", delivery: "배달권역", sales: "상권 매출", compare: "후보지 비교",
  existing_store_impact: "기존점 영향", demand_type: "수요 유형", cafe_competition: "카페 유형별 경쟁",
  time_slots: "시간대 분석", walk_access: "도보 접근성", care_demand: "진료권 수요", same_specialty: "동일 진료과",
  medical_gap: "의료 공백", transport: "교통 접근성", parking: "주차", customer_area: "고객 생활권",
  competition_type: "경쟁 유형", price_band: "가격대", repeat_demand: "반복수요", booking: "예약",
  student_demand: "학생 수요", school_housing: "학교·주거", same_subject: "동일 과목 경쟁", commute_area: "통학권",
  academy_cluster: "학원가", local_demand: "생활권 수요", competitors: "경쟁점", road_access: "도로 접근성",
  mixed_use: "주거·업무 혼합", network: "점포망", cannibalization: "기존점 중첩", new_coverage: "신규 커버",
  candidates: "후보지 일괄 비교", care_overlap: "진료권 중첩", commute_overlap: "통학권 중첩",
  lunch_demand: "점심 수요", dinner_demand: "저녁 수요", worker_density: "직장인 밀도",
  residential_demand: "주거 수요", direct_competitors: "직접 경쟁점", delivery_coverage: "배달권역",
  takeout_demand: "테이크아웃 수요", stay_demand: "체류 수요", workers: "직장인", students: "학생",
  franchise_cafes: "프랜차이즈 카페", budget_cafes: "저가 카페", dessert_cafes: "디저트 카페",
  female_population: "핵심 여성 인구", apartments: "아파트·오피스텔", residential_area: "생활권",
  hair_salons: "미용실", barbershops: "바버숍", beauty_services: "뷰티업", age_population: "연령대별 인구",
  transit_catchment: "대중교통 진료권", drive_catchment: "차량 진료권", schools: "학교",
  school_age_population: "학령인구", same_subject_academies: "동일 과목 학원", walk_catchment: "도보 생활권",
  worker_demand: "직장인 수요", late_night_demand: "심야 수요", corner_nodes: "코너·교차로",
  convenience_competitors: "편의점 경쟁점", existing_stores: "기존점", existing_store_overlap: "기존점 중복",
  brand_whitespace: "브랜드 공백", cafe_types: "카페 유형", all_day: "전 시간대", late_night: "심야",
  weekday_lunch: "평일 점심", weekday_dinner: "평일 저녁", weekend: "주말", commute_morning: "출근 시간",
  post_lunch: "점심 이후", weekend_afternoon: "주말 오후", weekday_evening: "평일 저녁",
  weekday_day: "평일 주간", saturday_morning: "토요일 오전", after_school: "방과 후", evening: "저녁",
};

const MODE_LABELS: Record<string, string> = { walk: "도보", drive: "차량", transit: "대중교통", residential: "생활권", delivery: "배달" };

function labelFor(key: string) {
  return LABELS[key] || key.replaceAll("_", " ");
}

function catchmentText(profile: AdaptiveIndustryProfile) {
  const catchment = profile.analysis.catchment;
  const values = catchment.walkMinutes || catchment.transitMinutes || catchment.driveMinutes || catchment.deliveryMinutes || [];
  return `${MODE_LABELS[catchment.primary] || catchment.primary} ${values.join("·")}분`;
}

function findKpi(result: SiteScoreResponse | null, key: string) {
  const industryKpi = result?.industry_kpis?.find((item) => item.key === key);
  if (industryKpi && industryKpi.score != null) return industryKpi;
  const subscore = result?.subscores.find((item) => item.key === key);
  if (subscore && subscore.score != null) return subscore;
  return null;
}

function kpiDisplay(result: SiteScoreResponse | null, key: string) {
  if (!result) return "분석 후 계산";
  const matched = findKpi(result, key);
  if (!matched || matched.score == null) return "데이터 없음";
  return matched.display || `${matched.score.toFixed(1)}점`;
}

function kpiDetail(result: SiteScoreResponse | null, key: string) {
  const matched = findKpi(result, key);
  return matched && "detail" in matched ? matched.detail || undefined : undefined;
}

// 점수가 높을수록 "나쁜" 지표 — 정의는 lib/metricDirection.ts 한 곳.
// (포화도 100점을 초록으로 칠하면 좋은 입지로 오독된다.)
const INVERSE_KEYS = HIGHER_IS_WORSE;

// 점수 구간별 색 — tokens.css의 상태 색을 그대로 쓴다(하드코딩 없음).
function scoreColor(score: number, inverse = false): string {
  const s = inverse ? 100 - score : score;
  if (s >= 80) return "var(--status-positive)";
  if (s >= 60) return "var(--blue-50)";
  if (s >= 40) return "var(--status-cautionary)";
  return "var(--status-negative)";
}

// KPI/지표 한 줄 = 라벨 + 등급 + 막대(점수) + 상위% + 근거 문구.
// 막대 하나로 "얼마나 좋은지"가 한눈에 보이게 하는 것이 목적.
function MetricBar({ label, score, display, detail, rankTopPct, inverse = false }: {
  label: string;
  score: number | null;
  display: string;
  detail?: string;
  rankTopPct?: number | null;
  inverse?: boolean;
}) {
  const hasScore = score != null;
  // display가 이미 "상위 N%" 형태면 백분위를 두 번 적지 않는다.
  const pctText = hasScore ? rankTopPctText(score, rankTopPct, inverse) : "";
  const showPct = hasScore && !display.startsWith("상위");
  return (
    <div className="metric-bar-row">
      <div className="metric-bar-head">
        <span className="metric-bar-label">{label}</span>
        <span className="metric-bar-value">
          <strong>{display}</strong>
          {showPct && <em>{pctText}</em>}
        </span>
      </div>
      <div className="metric-bar-track" role="img" aria-label={`${label} ${display}${showPct ? ` (${pctText})` : ""}`}>
        {hasScore && <i style={{ width: `${Math.max(2, Math.min(100, score))}%`, background: scoreColor(score, inverse) }} />}
      </div>
      {detail && <p className="metric-bar-detail">{detail}</p>}
    </div>
  );
}

export function AdaptiveDashboard({ industryId, siteScore, brand, onBrandChange }: { industryId: string; siteScore: SiteScoreResponse | null; brand?: string; onBrandChange?: (value: string) => void }) {
  const [userType, setUserType] = useState<AdaptiveUserType>("entrepreneur");
  const [profile, setProfile] = useState<AdaptiveIndustryProfile | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setError("");
    api.industryProfile(industryId, userType).then((next) => {
      if (cancelled) return;
      setProfile(next);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof Error ? err.message : "업종 화면 프로파일을 불러오지 못했습니다.");
    });
    return () => { cancelled = true; };
  }, [industryId, userType]);

  const oneLine = useMemo(() => {
    if (siteScore?.verdict_one_liner) return siteScore.verdict_one_liner;
    if (!profile) return "업종 프로파일을 불러오는 중입니다.";
    return `${profile.name}의 ${catchmentText(profile)} 권역과 ${profile.analysis.timeSlots.map(labelFor).join("·")} 수요를 기준으로 분석합니다.`;
  }, [profile, siteScore]);

  if (error) return <section className="adaptive-dashboard"><div className="status-banner status-banner--error">{error}</div></section>;
  if (!profile) return <section className="adaptive-dashboard adaptive-dashboard--loading">적응형 분석 화면 구성 중...</section>;

  return (
    <section className="adaptive-dashboard" aria-label="업종별 분석">
      {/* 실데이터 우선: 한줄 진단 → KPI(상세 문구 포함) → 상권 매출 벤치마크.
          이전의 내부 탭 줄·지도 레이어 토글·시스템 설명 카드 3종은 실제
          데이터와 연결되지 않은 장식이라 제거했다. */}
      <div className="adaptive-toolbar">
        <div>
          <h2>{profile.name} 분석</h2>
          <p>{oneLine}</p>
        </div>
      </div>

      {/* 업종 KPI — 막대 + 상위% 로 "얼마나 좋은지"를 시각화. */}
      <div className="metric-bars">
        {profile.ui.summaryKpis.map((kpi) => {
          const matched = findKpi(siteScore, kpi.key);
          return (
            <MetricBar
              key={kpi.key}
              label={kpi.label}
              score={matched?.score ?? null}
              display={kpiDisplay(siteScore, kpi.key)}
              detail={kpiDetail(siteScore, kpi.key)}
              rankTopPct={matched && "rank_top_pct" in matched ? (matched.rank_top_pct as number | null) : null}
              inverse={INVERSE_KEYS.has(kpi.key)}
            />
          );
        })}
      </div>

      {/* 입지 기본 지표 — 서버가 실제 서울 백분위(rank_top_pct)를 주는 항목. */}
      {siteScore && siteScore.subscores.length > 0 && (
        <div className="metric-bars metric-bars--secondary">
          <p className="adaptive-group-title">입지 지표 (서울 전체 대비)</p>
          {siteScore.subscores.map((sub) => (
            <MetricBar
              key={sub.key}
              label={sub.summary_label || sub.label}
              score={sub.score ?? null}
              display={sub.display || (sub.score != null ? `${sub.score.toFixed(0)}점` : "데이터 없음")}
              detail={sub.detail}
              rankTopPct={sub.rank_top_pct}
              inverse={INVERSE_KEYS.has(sub.key)}
            />
          ))}
        </div>
      )}

      {siteScore?.sales_benchmark && (
        <div className="adaptive-summary-strip" title={`${siteScore.sales_benchmark.trdar_se_nm || "상권"} · ${siteScore.sales_benchmark.quarter.slice(0, 4)}년 ${siteScore.sales_benchmark.quarter.slice(4)}분기 · 서울 ${siteScore.sales_benchmark.citywide_trdar_count || 0}개 상권 비교`}>
          <span>
            <strong>상권 매출</strong>{siteScore.sales_benchmark.trdar_nm}{siteScore.sales_benchmark.inside_area === false ? ` (최근접 ${Math.round(siteScore.sales_benchmark.distance_m || 0)}m)` : ""}
            {salesBenchmarkScopeNote(siteScore.sales_benchmark)}
          </span>
          {siteScore.sales_benchmark.per_store_monthly_won != null && (
            <span><strong>점포당 월매출</strong>{(siteScore.sales_benchmark.per_store_monthly_won / 10_000).toLocaleString(undefined, { maximumFractionDigits: 0 })}만원
              {siteScore.sales_benchmark.citywide_median_per_store_won ? ` (서울 중앙값의 ${(siteScore.sales_benchmark.per_store_monthly_won / siteScore.sales_benchmark.citywide_median_per_store_won).toFixed(1)}배)` : ""}</span>
          )}
          {siteScore.sales_benchmark.store_count != null && (
            <span><strong>동종 점포</strong>{siteScore.sales_benchmark.store_count}곳{siteScore.sales_benchmark.franchise_count != null ? ` · 프랜차이즈 ${siteScore.sales_benchmark.franchise_count}` : ""}</span>
          )}
          {siteScore.sales_benchmark.weekend_share_pct != null && (
            <span><strong>주말 매출 비중</strong>{siteScore.sales_benchmark.weekend_share_pct}%</span>
          )}
        </div>
      )}

      {/* 분석 조건 요약 + 사용자 유형 전환 (유형에 따라 KPI 구성이 바뀐다). */}
      <div className="adaptive-summary-strip">
        <span><strong>분석권역</strong>{catchmentText(profile)}</span>
        <span><strong>핵심 시간대</strong>{profile.analysis.timeSlots.map(labelFor).join(" · ")}</span>
      </div>
      <div className="adaptive-controls">
        <label>사용자 유형<select value={userType} onChange={(event) => setUserType(event.target.value as AdaptiveUserType)}><option value="entrepreneur">예비 창업자</option><option value="franchise">프랜차이즈 본사</option></select></label>
        {userType === "franchise" && onBrandChange && (
          <label>브랜드명<input type="text" value={brand || ""} maxLength={40} placeholder="예: 메가커피 (입력 후 재분석)" onChange={(event) => onBrandChange(event.target.value)} /></label>
        )}
      </div>
    </section>
  );
}
