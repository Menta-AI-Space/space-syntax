"use client";

import type { SiteScoreResponse } from "@/lib/api";
import { isInverseMetric, topFactorChips } from "@/lib/metricDirection";

const PRIORITY_OPTIONS = [
  { key: "youth", label: "20대 인구", hint: "젊은 고객층" },
  { key: "teen", label: "10대 인구", hint: "학생·학원가 수요" },
  // 업종마다 노리는 손님이 다르다 — 연령·성별을 직접 고를 수 있어야 한다.
  { key: "age_30s", label: "30대 인구", hint: "직장인·신혼 수요" },
  { key: "age_40s", label: "40대 인구", hint: "가족·구매력" },
  { key: "age_50plus", label: "50대 이상", hint: "중장년·시니어 수요" },
  { key: "female", label: "여성 인구", hint: "여성 대상 업종" },
  { key: "male", label: "남성 인구", hint: "남성 대상 업종" },
  { key: "foreign", label: "외국인 유동", hint: "관광·외국인 수요" },
  { key: "strong_workers", label: "직장인", hint: "평일 업무 수요" },
  { key: "strong_residential", label: "거주인구", hint: "배후 주거 수요" },
  { key: "high_footfall", label: "유동인구", hint: "통행·접근성" },
  { key: "low_competition", label: "낮은 경쟁", hint: "동종업종 희소성" },
  { key: "high_sales", label: "매출 가능성", hint: "상권 매출 수준" },
  { key: "delivery", label: "배달 수요", hint: "배후 수요 중심" },
  { key: "stability", label: "운영 안정성", hint: "수요·경쟁 균형" },
] as const;

function fmt(value: unknown) {
  return typeof value === "number" ? value.toLocaleString() : value == null || value === "" ? "—" : String(value);
}

// subscore.display는 값이 없을 때 백엔드가 리터럴 "—"를 채워 넣는다(site_score.py:
// `comp_intensity or "—"`, `score_qualitative_label(t_score) if ... else "—"` 등).
// `display || detail`로 고르면 "—"가 truthy라서 항상 이겼고, 정작 왜 없는지 설명하는
// detail("지하철역 정보 없음" 등 진짜 이유)은 절대 화면에 나오지 않았다.
export function subscoreDisplayText(s: { display?: string; detail: string }): string {
  return s.display && s.display !== "—" ? s.display : s.detail;
}

// 백엔드 해석은 "**한 줄 결론**\n본문\n\n**왜 좋은지**\n본문 …" 형태의 4섹션 텍스트다.
// 통짜 <p>에 넣으면 \n\n이 공백으로 뭉치고 **볼드**가 별표 그대로 보여 한 덩어리로
// 읽힌다. 섹션별로 쪼개 헤더는 굵게, 본문은 문단으로 띄운다.
function explanationSections(text: string): { head: string; body: string }[] {
  // **헤더** 뒤 본문을 다음 **헤더**(또는 끝)까지 잡는다. blob·줄바꿈·"— " 구분자
  // 어느 형태든 동일하게 처리한다.
  const re = /\*\*([^*]+?)\*\*\s*[—:-]?\s*([\s\S]*?)(?=\*\*[^*]+?\*\*|$)/g;
  const out: { head: string; body: string }[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    out.push({ head: m[1].trim(), body: m[2].replace(/\*\*/g, "").trim() });
  }
  if (out.length) return out;
  return [{ head: "", body: text.replace(/\*\*/g, "").trim() }];
}

function ExplanationBody({ text }: { text: string }) {
  const sections = explanationSections(text);
  return (
    <div className="explanation-sections">
      {sections.map((s, i) => (
        <div key={i} style={{ marginTop: i ? "var(--space-4, 14px)" : 0 }}>
          {s.head && <strong style={{ display: "block", marginBottom: "var(--space-1, 4px)" }}>{s.head}</strong>}
          {s.body && <p style={{ margin: 0, lineHeight: 1.6 }}>{s.body}</p>}
        </div>
      ))}
    </div>
  );
}

interface SiteScorePanelProps {
  result: SiteScoreResponse | null;
  loading: boolean;
  error: string;
  priorities: string[];
  priorityBoosts?: Record<string, number>;
  personalizing: boolean;
  onPrioritiesChange: (next: string[]) => void;
  onBoostsChange?: (boosts: Record<string, number>) => void;
}

const DEFAULT_BOOST = 1.8; // personal_fit.BOOST와 같은 값 — 슬라이더 초기값

export function SiteScorePanel({ result, loading, error, priorities, priorityBoosts, personalizing, onPrioritiesChange, onBoostsChange }: SiteScorePanelProps) {
  function togglePriority(key: string) {
    if (priorities.includes(key)) {
      onPrioritiesChange(priorities.filter((item) => item !== key));
      return;
    }
    if (priorities.length >= 3) return;
    onPrioritiesChange([...priorities, key]);
  }

  const fit = result?.personal_fit;
  const delta = fit ? Math.round((fit.personal_score - fit.base_score) * 10) / 10 : 0;

  // 종합점수의 "왜 이 점수인가"를 헤드라인에 바로 붙이는 칩. 방향을 맞춰
  // 고르는 일은 lib/metricDirection에 있다 — 점수 크기순으로 자르면 경쟁 밀도가
  // 늘 1등으로 올라와 "경쟁이 극심한 것"이 좋은 점수의 근거처럼 보였다.
  const topFactors = result ? topFactorChips(result.subscores) : [];

  // 깊이(mean_depth)는 접근성(integration)을 뒤집어 정규화하기 전의 원값이라
  // 둘이 거의 같이 움직인다(코드상 integration = node_count^0.2 / mean_depth).
  // 따로 한 줄을 더 쓰면 비슷한 숫자 둘이 나란히 떠서 "뭐가 다르지"가 된다 —
  // 접근성 아래 근거로 붙여 한 줄로 읽히게 한다.
  const depth = result?.subscores.find((s) => s.key === "mean_depth") ?? null;
  const visibleSubscores = (result?.subscores ?? []).filter((s) => s.key !== "mean_depth");

  return (
    <div className="site-panel-scroll">
      <div className="card priority-card">
        <div className="priority-card-heading">
          <div><h3>나에게 중요한 조건</h3><p className="site-panel-meta">최대 3개를 고르면 최종 맞춤 점수가 즉시 조정됩니다.</p></div>
          <span>{priorities.length}/3</span>
        </div>
        <div className="priority-chip-grid">
          {PRIORITY_OPTIONS.map((option) => {
            const selected = priorities.includes(option.key);
            // 우선순위 키와 factor 키가 다른 경우가 있다: "high_sales" 칩의 데이터는
            // personalization_factors["sales"]로 온다 (personal_fit.py의 매핑 참고).
            // 칩 키로 그대로 조회하면 항상 undefined → 데이터가 있어도 "데이터 없음".
            const factorKey = option.key === "high_sales" ? "sales" : option.key;
            const unavailable = result && ["youth", "teen", "foreign", "high_sales"].includes(option.key) && result.personalization_factors?.[factorKey] == null;
            return (
              <button
                key={option.key}
                type="button"
                className={`priority-chip${selected ? " priority-chip--selected" : ""}`}
                aria-pressed={selected}
                disabled={Boolean(unavailable) || (!selected && priorities.length >= 3)}
                title={unavailable ? "현재 위치의 해당 데이터가 없습니다." : option.hint}
                onClick={() => togglePriority(option.key)}
              >
                <strong>{option.label}</strong><small>{unavailable ? "데이터 없음" : option.hint}</small>
              </button>
            );
          })}
        </div>
        {/* 선택한 조건마다 강도 슬라이더 — GF의 "your weight". 1.0=반영 안 함에
            가깝게, 3.0=강하게. 기본 1.8은 종전 부스트와 같아 슬라이더를 안
            만지면 결과가 어제와 같다. */}
        {priorities.length > 0 && onBoostsChange && (
          <div className="priority-boosts">
            {priorities.map((key) => {
              const option = PRIORITY_OPTIONS.find((o) => o.key === key);
              const value = priorityBoosts?.[key] ?? DEFAULT_BOOST;
              return (
                <label key={key} className="priority-boost-row">
                  <span className="priority-boost-label">{option?.label ?? key}</span>
                  <input
                    type="range"
                    min={1}
                    max={3}
                    step={0.1}
                    value={value}
                    aria-label={`${option?.label ?? key} 반영 강도`}
                    onChange={(e) => onBoostsChange({ ...(priorityBoosts ?? {}), [key]: Number(e.target.value) })}
                  />
                  <span className="priority-boost-value">×{value.toFixed(1)}</span>
                </label>
              );
            })}
          </div>
        )}
        {personalizing && <p className="site-panel-meta">맞춤 점수 조정 중…</p>}
      </div>

      {loading && <div className="card"><h2>입지 진단</h2><p>입지 점수 계산 중...</p></div>}
      {!loading && error && <div className="card"><h2>입지 진단</h2><p className="status-banner status-banner--error">{error}</p></div>}
      {!loading && !error && !result && <div className="card"><h2>입지 진단</h2><p className="site-panel-placeholder">분석 실행 또는 지도 클릭 후 입지 점수와 맞춤 점수가 표시됩니다.</p></div>}

      {!loading && !error && result && (
        <>
          <div className="card site-score-hero">
            <div className="site-score-hero-top">
              <div className="site-score-hero-figure">
                <span className="site-score-hero-value">{result.total_score.toFixed(1)}</span>
                <span className="site-score-hero-unit">점</span>
              </div>
              <div className="site-score-hero-meta">
                <span className="status-pill site-score-hero-grade">{result.grade}</span>
                <span className="site-score-hero-business">{result.business_label}</span>
              </div>
            </div>
            {result.verdict_one_liner && <p className="site-score-hero-verdict">{result.verdict_one_liner}</p>}
            {topFactors.length > 0 && (
              <div className="site-score-hero-factors">
                <span className="site-score-hero-factors-label">핵심 근거</span>
                <div className="site-score-hero-factor-list">
                  {topFactors.map((f) => (
                    <span className="site-score-factor-chip" key={f.key}>
                      <strong>{f.label}</strong>
                      <span>{f.value}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
            {result.upjong_breadcrumb && <p className="site-panel-meta site-score-hero-breadcrumb">{result.upjong_breadcrumb}</p>}
          </div>

          {fit && priorities.length > 0 && (
            <div className="card personal-fit-card">
              <span className="personal-fit-label">내 조건 반영</span>
              <div className="personal-fit-score-row">
                <strong>{fit.personal_score.toFixed(1)}점</strong>
                <span className={delta >= 0 ? "personal-fit-delta personal-fit-delta--up" : "personal-fit-delta personal-fit-delta--down"}>
                  기본 대비 {delta >= 0 ? "+" : ""}{delta.toFixed(1)}
                </span>
              </div>
              {fit.strengths.length > 0 && <p className="personal-fit-strength">강점 · {fit.strengths.join(" · ")}</p>}
              {fit.cautions.length > 0 && <p className="personal-fit-caution">주의 · {fit.cautions.join(" · ")}</p>}
              {fit.priorities.filter((item) => !item.applied).map((item) => <p key={item.key} className="site-panel-meta">{item.label}: {item.note}</p>)}
            </div>
          )}

          <div className="card">
            <h3>세부 지표</h3>
            {visibleSubscores.map((s) => {
              // 경쟁 밀도처럼 높을수록 나쁜 지표는 막대가 길수록 나쁘다. 다른
              // 행과 같은 초록 막대로 칠하면 꽉 찬 막대가 "아주 좋음"으로 읽힌다.
              const inverse = isInverseMetric(s.key);
              return (
                <div key={s.key} className="subscore-row">
                  <div className="subscore-row-head">
                    <strong>{s.label}</strong>
                    <span>{fmt(s.score)}점{inverse && s.score != null ? " · 높을수록 불리" : ""}</span>
                  </div>
                  <div className={`score-bar${inverse ? " score-bar--inverse" : ""}`}>
                    <span style={{ width: `${Math.max(0, Math.min(100, s.score || 0))}%` }} />
                  </div>
                  <p className="site-panel-meta">{subscoreDisplayText(s)}</p>
                  {s.key === "integration" && depth && (
                    <p className="site-panel-meta subscore-support">
                      └ {depth.label}: {subscoreDisplayText(depth)}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
          {result.explanation?.text && <div className="card"><h3>분석 해석</h3><ExplanationBody text={result.explanation.text} /></div>}
          {result.warnings?.length ? <div className="warnings-banner">{result.warnings.map((w, i) => <div key={i}>{w}</div>)}</div> : null}
        </>
      )}
    </div>
  );
}
