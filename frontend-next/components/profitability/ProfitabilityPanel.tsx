"use client";

import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import {
  api,
  type ProfitabilityAnalyzeResponse,
  type ProfitabilityResult,
  type PropertyMarketResult,
  type RentReference as RentReferenceData,
} from "@/lib/api";
import { explanationSourceLabel } from "@/lib/explanationSource";
import { pointKey } from "@/lib/inputsKey";
import { marketTimingNote } from "@/lib/propertyMarketTiming";
import {
  defaultProfitabilityInputs,
  saveProfitabilityInputs,
  num,
  fmtManwon,
  fmtPct,
  fmtPricePerSqm,
  pricePercentileText,
  severityLabel,
  severityClass,
  dscrClass,
  type ProfitabilityInputs,
  type NumOrEmpty,
} from "./profitabilityUtils";

interface ProfitabilityPanelProps {
  /** 공유 지도/분석에서 선택된 위치 (시장 컨텍스트용). 없으면 순수 계산만. */
  location: { lat: number; lng: number; label?: string } | null;
}

function renderBold(line: string): ReactNode[] {
  return line.split(/(\*\*[^*]+\*\*)/g).map((part, i) => {
    const m = part.match(/^\*\*([^*]+)\*\*$/);
    return m ? <strong key={i}>{m[1]}</strong> : <Fragment key={i}>{part}</Fragment>;
  });
}

function renderExplanation(text: string): ReactNode {
  return text.split(/\n\n/).map((para, pi) => (
    <p key={pi}>
      {para.split(/\n/).map((line, li, arr) => (
        <Fragment key={li}>
          {renderBold(line)}
          {li < arr.length - 1 && <br />}
        </Fragment>
      ))}
    </p>
  ));
}

/** 음수면 위험(적자), 그 외 중립. IRR·Cash-on-Cash처럼 음수가 명백히 나쁜 지표에 사용. */
function negativeCls(v: number | null | undefined): string {
  if (v == null) return "";
  return v < 0 ? "prof-kpi--danger" : "";
}

/** 손익분기 점유율: 100% 이상이면 만실이어도 적자(심각), 90% 초과면 공실 여력이 거의 없음(주의). */
function breakEvenCls(v: number | null | undefined): string {
  if (v == null) return "";
  if (v >= 100) return "prof-kpi--danger";
  if (v > 90) return "prof-kpi--warn";
  return "";
}

function StepLegend({ step, title, hint, tag }: { step?: number; title: string; hint: string; tag?: ReactNode }) {
  return (
    <>
      <legend className="prof-legend">
        {step != null
          ? <span className="prof-step-num">{step}</span>
          : <span className="prof-step-num prof-step-num--base">기본</span>}
        <span className="prof-legend-title">{title}</span>
        {tag}
      </legend>
      <p className="prof-fieldset-hint">{hint}</p>
    </>
  );
}

function KpiGrid({ calc }: { calc: ProfitabilityResult }) {
  const cells: { label: string; value: string; cls?: string; hint?: string }[] = [
    { label: "NOI Cap Rate", value: fmtPct(calc.cap_rate_noi_pct), hint: "부동산 운영수익률" },
    { label: "총원가 Cap Rate", value: fmtPct(calc.cap_rate_total_cost_pct), hint: "부대비용 포함" },
    { label: "Cash-on-Cash", value: calc.cash_on_cash_pct != null ? fmtPct(calc.cash_on_cash_pct) : "—", cls: negativeCls(calc.cash_on_cash_pct), hint: "자기자본 대비 현금수익" },
    { label: "DSCR", value: calc.dscr != null ? calc.dscr.toFixed(2) : "—", cls: dscrClass(calc.dscr), hint: "임대수익÷원리금" },
    { label: "IRR", value: calc.irr_pct != null ? fmtPct(calc.irr_pct) : "—", cls: negativeCls(calc.irr_pct), hint: "보유·매각 전체 수익률" },
    { label: "손익분기 점유율", value: calc.break_even_occupancy_pct != null ? fmtPct(calc.break_even_occupancy_pct, 1) : "—", cls: breakEvenCls(calc.break_even_occupancy_pct), hint: "공실 여력" },
  ];
  return (
    <div className="prof-kpi-grid">
      {cells.map((c) => (
        <div key={c.label} className={`prof-kpi ${c.cls || ""}`}>
          <span className="prof-kpi-label">{c.label}</span>
          <strong className="prof-kpi-value">{c.value}</strong>
          {c.hint && <span className="prof-kpi-hint">{c.hint}</span>}
        </div>
      ))}
    </div>
  );
}

function IrrDecomposition({ calc }: { calc: ProfitabilityResult }) {
  if (calc.irr_pct == null || calc.capital_gain_contribution_pct == null) return null;
  const op = calc.irr_no_appreciation_pct ?? 0;
  const cap = calc.capital_gain_contribution_pct;
  const share = calc.irr_pct > 0 ? Math.round((cap / calc.irr_pct) * 100) : 0;
  return (
    <p className="prof-irr-note">
      IRR <strong>{fmtPct(calc.irr_pct)}</strong> = 운영수익 {fmtPct(op)} + 매각차익 가정 {fmtPct(cap)}
      {share >= 50 && <span className="prof-irr-warn"> — 기대수익의 약 {share}%가 매각가 상승 가정에서 나옵니다.</span>}
    </p>
  );
}

function ScenarioTable({ calc }: { calc: ProfitabilityResult }) {
  return (
    <table className="prof-table">
      <thead>
        <tr><th>시나리오</th><th>공실률</th><th>금리</th><th>NOI</th><th>세전현금흐름</th><th>CoC</th><th>DSCR</th></tr>
      </thead>
      <tbody>
        {calc.scenarios.map((s) => (
          <tr key={s.label}>
            <td>{s.label}</td>
            <td>{s.vacancy_rate_pct}%</td>
            <td>{s.loan_rate_pct}%</td>
            <td>{fmtManwon(s.noi_manwon)}</td>
            <td>{fmtManwon(s.btcf_manwon)}</td>
            <td>{s.cash_on_cash_pct != null ? fmtPct(s.cash_on_cash_pct) : "—"}</td>
            <td>{s.dscr != null ? s.dscr.toFixed(2) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SensitivityMatrix({ calc }: { calc: ProfitabilityResult }) {
  if (!calc.sensitivity_coc_pct.length) return null;
  return (
    <div className="prof-sensitivity">
      <p className="prof-section-title">공실률 × 금리 → Cash-on-Cash</p>
      <table className="prof-table prof-table--matrix">
        <thead>
          <tr>
            <th>공실 ＼ 금리</th>
            {calc.sensitivity_rate_pct.map((r) => <th key={r}>{r}%</th>)}
          </tr>
        </thead>
        <tbody>
          {calc.sensitivity_coc_pct.map((row, i) => (
            <tr key={i}>
              <th>{calc.sensitivity_vacancy_pct[i]}%</th>
              {row.map((v, j) => (
                <td key={j} className={v != null && v < 0 ? "prof-cell--neg" : ""}>
                  {v != null ? fmtPct(v) : "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 제외 사유 코드 → 화면 표시 라벨. 값은 backend property_market.py의 excluded 집계 키와 1:1로 대응한다. */
const EXCLUDED_REASON_LABELS: Record<string, string> = {
  cancelled: "계약 해제",
  share_deal: "지분 거래",
  direct_deal: "직거래(중개 없음)",
  approx_geo: "위치 근사치",
  other_building_type: "건물 구분 다름",
};

/** 같은 반경·유형·기간 내에서 밴드 계산에 넣지 않은 거래의 사유별 건수.
    사유가 겹칠 수 있어 합산해도 전체 표본 수와 맞지 않는다 — 사유별 참고용으로만 보여준다. */
function ExcludedTrades({ excluded }: { excluded: Record<string, number> }) {
  const entries = Object.entries(excluded).filter(([, v]) => v > 0);
  if (!entries.length) return null;
  return (
    <p className="prof-meta">
      비교에서 제외된 거래: {entries.map(([key, count]) => `${EXCLUDED_REASON_LABELS[key] ?? key} ${count}건`).join(" · ")}
    </p>
  );
}

/** 표본이 있어도(p50 계산됨) backend가 insufficient_sample=true로 표시한 경우가 있다
    (property_market.py MIN_SAMPLE=8 미만) — 1~7건짜리 밴드를 확정 시세처럼 보여주면 안 된다. */
function MarketBand({ market }: { market: PropertyMarketResult }) {
  if (!market.p50) {
    return (
      <>
        <p className="prof-meta">
          주변 비교 실거래가 {market.n}건뿐이라 시장가격 밴드를 만들 수 없습니다 (통건물은 지번 마스킹으로 표본이 적습니다).
        </p>
        <ExcludedTrades excluded={market.excluded} />
      </>
    );
  }
  const thinSample = market.insufficient_sample;
  const timingNote = marketTimingNote(market);
  return (
    <div className="prof-market">
      <p className="prof-section-title">
        주변 동종 매매 실거래 (₩/㎡){" "}
        {thinSample && <span className="prof-source-tag">표본 부족 · 참고용</span>}
      </p>
      <div className="prof-band">
        <span>P25 {fmtPricePerSqm(market.p25)}</span>
        <span className="prof-band-mid">중위 {fmtPricePerSqm(market.p50)}</span>
        <span>P75 {fmtPricePerSqm(market.p75)}</span>
      </div>
      {thinSample && (
        <p className="prof-meta prof-meta--warn">
          표본이 {market.n}건뿐이라(최소 8건 기준) 확정된 시세밴드로 보기 어렵습니다 — 참고용으로만 사용하세요.
        </p>
      )}
      {market.subject_price_per_sqm != null && (
        <p className="prof-subject">
          검토 매입 단가 <strong>{fmtPricePerSqm(market.subject_price_per_sqm)}</strong>
        </p>
      )}
      {market.subject_price_per_sqm != null && market.subject_percentile != null && (
        <p className="prof-subject-percentile">{pricePercentileText(market.subject_percentile)}</p>
      )}
      <p className="prof-meta">
        표본 {market.n}건 · 반경 {market.radius_m}m · {market.min_deal_date}~{market.max_deal_date} · {market.data_source}
      </p>
      {timingNote && <p className="prof-meta">{timingNote}</p>}
      {market.relaxations.length > 0 && (
        <p className="prof-meta">표본 확보 완화: {market.relaxations.join(", ")}</p>
      )}
      <ExcludedTrades excluded={market.excluded} />
    </div>
  );
}

function RentReferenceBlock({
  ref,
  onApply,
}: {
  ref: RentReferenceData;
  onApply: (rentManwon: number, vacancyPct: number) => void;
}) {
  if (!ref.matched) return null;
  const hasBand = ref.monthly_rent_low_manwon != null && ref.monthly_rent_high_manwon != null;
  const mid = hasBand
    ? Math.round((ref.monthly_rent_low_manwon! + ref.monthly_rent_high_manwon!) / 2)
    : null;
  const bandText = hasBand
    ? ref.monthly_rent_low_manwon === ref.monthly_rent_high_manwon
      ? `${ref.monthly_rent_low_manwon!.toLocaleString("ko-KR")}만원/월`
      : `${ref.monthly_rent_low_manwon!.toLocaleString("ko-KR")}~${ref.monthly_rent_high_manwon!.toLocaleString("ko-KR")}만원/월`
    : null;
  // 상가는 R-ONE 지역통계(실거래 아님), 오피스텔은 OffiRent 실거래. data_source가 구분한다.
  const isEstimate = ref.data_source.includes("실거래 아님");
  const footerParts = [
    ref.sanggwon_nm ? `${ref.sanggwon_nm} 상권` : "",
    ref.quarter,
    ref.data_source,
  ].filter(Boolean);
  return (
    <div className="prof-rent-ref">
      <p className="prof-section-title">
        주변 임대료 참고치{" "}
        {isEstimate && <span className="prof-source-tag">실거래 아님</span>}
      </p>
      <div className="prof-rent-ref-body">
        {bandText && <span className="prof-rent-ref-band">월세 {bandText}</span>}
        {ref.vacancy_default_pct != null && <span>공실률 {ref.vacancy_default_pct}%</span>}
        {mid != null && ref.vacancy_default_pct != null && (
          <button type="button" className="prof-rent-apply" onClick={() => onApply(mid, ref.vacancy_default_pct!)}>
            이 값으로 입력 채우기
          </button>
        )}
      </div>
      {ref.types.length > 0 && (
        <div className="prof-rent-ref-types">
          {ref.types.map((t) => (
            <span key={t.property_type}>
              {t.property_type} {t.rent_krw_per_sqm != null ? `${Math.round(t.rent_krw_per_sqm).toLocaleString("ko-KR")}원/㎡` : "—"}
              {t.vacancy_pct != null ? ` (공실 ${t.vacancy_pct.toFixed(1)}%)` : ""}
            </span>
          ))}
        </div>
      )}
      <p className="prof-meta">{footerParts.join(" · ")}</p>
    </div>
  );
}

function Results({
  data,
  onApplyRent,
}: {
  data: ProfitabilityAnalyzeResponse;
  onApplyRent: (rentManwon: number, vacancyPct: number) => void;
}) {
  const { calculation: calc } = data;
  const explanationSource = explanationSourceLabel(data.explanation_source);
  return (
    <div className="prof-result">
      <p className="prof-headline">{data.headline}</p>
      <KpiGrid calc={calc} />
      <IrrDecomposition calc={calc} />

      {/* 실투입 자기자본이 어떻게 나오는지 계산 관계식으로 보여준다
          (총투자원가 − 대출 = 자기자본). 대출은 총투자원가 − 자기자본으로 도출. */}
      <div className="prof-invest-flow">
        <div className="prof-flow-row">
          <span className="prof-flow-label">총투자원가</span>
          <strong className="prof-flow-val">{fmtManwon(calc.total_cost_manwon)}</strong>
        </div>
        <div className="prof-flow-row prof-flow-row--op">
          <span className="prof-flow-label">− 대출</span>
          <strong className="prof-flow-val">{fmtManwon(calc.total_cost_manwon - calc.equity_manwon)}</strong>
        </div>
        <div className="prof-flow-row prof-flow-row--result">
          <span className="prof-flow-label">= 실투입 자기자본</span>
          <strong className="prof-flow-val">{fmtManwon(calc.equity_manwon)}</strong>
        </div>
      </div>
      <div className="prof-invest-support">
        <span>연 NOI <strong>{fmtManwon(calc.noi_manwon)}</strong></span>
        <span>월 상환액 <strong>{fmtManwon(calc.monthly_payment_manwon)}</strong></span>
      </div>

      {data.rent_reference && <RentReferenceBlock ref={data.rent_reference} onApply={onApplyRent} />}
      {data.market && <MarketBand market={data.market} />}

      <ScenarioTable calc={calc} />
      <SensitivityMatrix calc={calc} />

      {data.risks.length > 0 && (
        <div className="prof-risks">
          <p className="prof-section-title">핵심 위험</p>
          <ul>
            {data.risks.map((r, i) => (
              <li key={i} className={severityClass(r.severity)}>
                <span className="prof-risk-badge">{severityLabel(r.severity)}</span>
                {r.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.levers.length > 0 && (
        <div className="prof-levers">
          <p className="prof-section-title">개선·협상 포인트</p>
          <ul>{data.levers.map((lv, i) => <li key={i}>{lv}</li>)}</ul>
        </div>
      )}

      <div className="prof-explanation">{renderExplanation(data.explanation)}</div>
      {explanationSource && <p className="prof-meta">{explanationSource}</p>}

      {calc.assumptions.length > 0 && (
        <p className="prof-meta">가정: {calc.assumptions.join(" · ")}</p>
      )}
      {(calc.warnings.length > 0 || data.warnings.length > 0) && (
        <p className="prof-meta prof-meta--warn">
          {[...calc.warnings, ...data.warnings].join(" · ")}
        </p>
      )}
      <p className="prof-meta">
        임대 조건(월세·공실률 등)은 사용자 입력 기준이며, 실제 계약·세금·권리금 등 개별 조건에 따라 달라질 수 있습니다.
      </p>
    </div>
  );
}

export function ProfitabilityPanel({ location }: ProfitabilityPanelProps) {
  const [inputs, setInputs] = useState<ProfitabilityInputs>(() => defaultProfitabilityInputs());
  const [result, setResult] = useState<ProfitabilityAnalyzeResponse | null>(null);
  // 결과 전체가 이 위치에 묶여 있다: 주변 실거래 밴드는 물론, 한 줄 결론과
  // 위험 항목도 그 밴드를 보고 쓰인다(backend explain_profitability(result, band)).
  // 위치가 바뀌면 머리글의 "물건 위치"만 새 주소로 바뀌고 판정은 옛 동네
  // 비교 그대로 남았다 — 입력값은 그대로 두고 결과만 비운다.
  const locKey = pointKey(location);
  const locKeyRef = useRef(locKey);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (locKeyRef.current === locKey) return;
    locKeyRef.current = locKey;
    setResult(null);
    setError("");
  }, [locKey]);

  function set<K extends keyof ProfitabilityInputs>(key: K, value: ProfitabilityInputs[K]) {
    setInputs((cur) => ({ ...cur, [key]: value }));
  }
  function fieldNum(key: keyof ProfitabilityInputs, value: string) {
    set(key, (value === "" ? "" : Number(value)) as ProfitabilityInputs[keyof ProfitabilityInputs]);
  }

  function handleApplyRent(rentManwon: number, vacancyPct: number) {
    setInputs((cur) => ({ ...cur, monthly_rent_manwon: rentManwon, vacancy_rate_pct: vacancyPct }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    saveProfitabilityInputs(inputs);
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await api.profitabilityAnalyze({
        purchase: {
          price_manwon: num(inputs.price_manwon),
          acquisition_tax_manwon: inputs.acquisition_tax_manwon === "" ? null : num(inputs.acquisition_tax_manwon),
          broker_fee_manwon: num(inputs.broker_fee_manwon),
          legal_fee_manwon: num(inputs.legal_fee_manwon),
          renovation_manwon: num(inputs.renovation_manwon),
          other_initial_manwon: num(inputs.other_initial_manwon),
        },
        rental: {
          deposit_manwon: num(inputs.deposit_manwon),
          monthly_rent_manwon: num(inputs.monthly_rent_manwon),
          other_monthly_income_manwon: num(inputs.other_monthly_income_manwon),
          vacancy_rate: num(inputs.vacancy_rate_pct) / 100,
          credit_loss_rate: num(inputs.credit_loss_rate_pct) / 100,
          owner_opex_monthly_manwon: num(inputs.owner_opex_monthly_manwon),
          annual_repair_reserve_manwon: num(inputs.annual_repair_reserve_manwon),
        },
        loan: num(inputs.loan_principal_manwon) > 0 ? {
          principal_manwon: num(inputs.loan_principal_manwon),
          annual_rate: num(inputs.loan_annual_rate_pct) / 100,
          term_years: num(inputs.loan_term_years),
          interest_only: inputs.loan_interest_only,
        } : null,
        exit_plan: {
          hold_years: num(inputs.hold_years, 5),
          rent_growth_rate: num(inputs.rent_growth_rate_pct) / 100,
          expense_growth_rate: num(inputs.expense_growth_rate_pct) / 100,
          sale_price_growth_rate: num(inputs.sale_price_growth_rate_pct) / 100,
          sale_cost_rate: num(inputs.sale_cost_rate_pct) / 100,
          discount_rate: inputs.discount_rate_pct === "" ? null : num(inputs.discount_rate_pct) / 100,
        },
        market_context: location ? {
          lat: location.lat,
          lng: location.lng,
          property_type: inputs.property_type,
          building_type: inputs.building_type,
          floor: inputs.property_type === "commercial" ? inputs.floor : null,
          area_sqm: inputs.area_sqm === "" ? null : num(inputs.area_sqm),
        } : null,
      });
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "분석 실패");
    } finally {
      setLoading(false);
    }
  }

  const n = (key: keyof ProfitabilityInputs) => inputs[key] as NumOrEmpty;

  return (
    <div className="prof-panel">
      <form className="prof-form" onSubmit={(e) => { void handleSubmit(e); }}>
        <div className="prof-location">
          {location
            ? <>물건 위치: <strong>{location.label || `${location.lat.toFixed(5)}, ${location.lng.toFixed(5)}`}</strong> — 주변 실거래로 매입가를 검증합니다.</>
            : <>입지 분석 탭에서 위치를 먼저 선택하면 주변 실거래 비교가 함께 계산됩니다. (지금은 계산만 수행)</>}
        </div>

        <fieldset className="prof-fieldset">
          <StepLegend title="물건 정보" hint="분석할 부동산의 유형과 규모입니다." />
          <div className="prof-grid">
            <label>유형
              <select value={inputs.property_type} onChange={(e) => set("property_type", e.target.value as ProfitabilityInputs["property_type"])}>
                <option value="commercial">상가 (상업업무용)</option>
                <option value="officetel">오피스텔</option>
              </select>
            </label>
            <label>구분
              <select value={inputs.building_type} onChange={(e) => set("building_type", e.target.value as ProfitabilityInputs["building_type"])}>
                <option value="집합">집합 (구분호실)</option>
                <option value="일반">일반 (통건물)</option>
              </select>
            </label>
            {inputs.property_type === "commercial" && (
              <label>층<input value={inputs.floor} onChange={(e) => set("floor", e.target.value)} placeholder="예: 1, -1, 3" /></label>
            )}
            <label>전용면적 (㎡)<input type="number" min={1} step={0.1} value={inputs.area_sqm} onChange={(e) => fieldNum("area_sqm", e.target.value)} /></label>
          </div>
        </fieldset>

        <fieldset className="prof-fieldset">
          <StepLegend step={1} title="매입 조건" hint="매입가와 부대비용 — 총투자원가의 바탕이 됩니다. 취득세를 비우면 4.6%로 자동 계산합니다." />
          <div className="prof-grid">
            <label>매입가 (만원)<input type="number" min={0} value={n("price_manwon")} onChange={(e) => fieldNum("price_manwon", e.target.value)} required /></label>
            <label>취득세 (만원)<input type="number" min={0} value={n("acquisition_tax_manwon")} onChange={(e) => fieldNum("acquisition_tax_manwon", e.target.value)} placeholder="비우면 4.6% 자동" /></label>
            <label>중개보수 (만원)<input type="number" min={0} value={n("broker_fee_manwon")} onChange={(e) => fieldNum("broker_fee_manwon", e.target.value)} /></label>
            <label>법무비 (만원)<input type="number" min={0} value={n("legal_fee_manwon")} onChange={(e) => fieldNum("legal_fee_manwon", e.target.value)} /></label>
            <label>인테리어·수선 (만원)<input type="number" min={0} value={n("renovation_manwon")} onChange={(e) => fieldNum("renovation_manwon", e.target.value)} /></label>
            <label>기타 초기비용 (만원)<input type="number" min={0} value={n("other_initial_manwon")} onChange={(e) => fieldNum("other_initial_manwon", e.target.value)} /></label>
          </div>
        </fieldset>

        <fieldset className="prof-fieldset">
          <StepLegend step={2} title="임대 조건" hint="월세·공실 등 운영 수입과 비용입니다. 아래 결과의 주변 임대료 참고치로 채울 수 있습니다." tag={<span className="prof-source-tag">사용자 입력</span>} />
          <div className="prof-grid">
            <label>임차보증금 (만원)<input type="number" min={0} value={n("deposit_manwon")} onChange={(e) => fieldNum("deposit_manwon", e.target.value)} /></label>
            <label>월세 (만원)<input type="number" min={0} value={n("monthly_rent_manwon")} onChange={(e) => fieldNum("monthly_rent_manwon", e.target.value)} /></label>
            <label>기타 월수입 (만원)<input type="number" min={0} value={n("other_monthly_income_manwon")} onChange={(e) => fieldNum("other_monthly_income_manwon", e.target.value)} /></label>
            <label>예상 공실률 (%)<input type="number" min={0} max={100} value={n("vacancy_rate_pct")} onChange={(e) => fieldNum("vacancy_rate_pct", e.target.value)} /></label>
            <label>미수·연체율 (%)<input type="number" min={0} max={100} value={n("credit_loss_rate_pct")} onChange={(e) => fieldNum("credit_loss_rate_pct", e.target.value)} /></label>
            <label>소유자 부담 관리비 (월/만원)<input type="number" min={0} value={n("owner_opex_monthly_manwon")} onChange={(e) => fieldNum("owner_opex_monthly_manwon", e.target.value)} /></label>
            <label>연 수선충당금 (만원)<input type="number" min={0} value={n("annual_repair_reserve_manwon")} onChange={(e) => fieldNum("annual_repair_reserve_manwon", e.target.value)} /></label>
          </div>
        </fieldset>

        <fieldset className="prof-fieldset">
          <StepLegend step={3} title="대출" hint="대출을 활용할 때만 입력하세요. 대출원금을 0으로 두면 전액 자기자본으로 계산합니다." tag={<span className="prof-source-tag">선택</span>} />
          <div className="prof-grid">
            <label>대출원금 (만원)<input type="number" min={0} value={n("loan_principal_manwon")} onChange={(e) => fieldNum("loan_principal_manwon", e.target.value)} /></label>
            <label>금리 (%)<input type="number" min={0} step={0.1} value={n("loan_annual_rate_pct")} onChange={(e) => fieldNum("loan_annual_rate_pct", e.target.value)} /></label>
            <label>기간 (년)<input type="number" min={0} max={50} value={n("loan_term_years")} onChange={(e) => fieldNum("loan_term_years", e.target.value)} /></label>
            <label className="prof-checkbox"><input type="checkbox" checked={inputs.loan_interest_only} onChange={(e) => set("loan_interest_only", e.target.checked)} /> 이자만 상환(거치)</label>
          </div>
        </fieldset>

        <fieldset className="prof-fieldset">
          <StepLegend step={4} title="보유·매각" hint="보유기간과 연 상승률 가정입니다. IRR·NPV 계산에 쓰이며, NPV 할인율을 비우면 NPV는 생략됩니다." />
          <div className="prof-grid">
            <label>보유기간 (년)<input type="number" min={1} max={30} value={n("hold_years")} onChange={(e) => fieldNum("hold_years", e.target.value)} /></label>
            <label>연 임대료 상승률 (%)<input type="number" step={0.1} value={n("rent_growth_rate_pct")} onChange={(e) => fieldNum("rent_growth_rate_pct", e.target.value)} /></label>
            <label>연 비용 상승률 (%)<input type="number" step={0.1} value={n("expense_growth_rate_pct")} onChange={(e) => fieldNum("expense_growth_rate_pct", e.target.value)} /></label>
            <label>연 매각가 상승률 (%)<input type="number" step={0.1} value={n("sale_price_growth_rate_pct")} onChange={(e) => fieldNum("sale_price_growth_rate_pct", e.target.value)} /></label>
            <label>매각비용률 (%)<input type="number" min={0} step={0.1} value={n("sale_cost_rate_pct")} onChange={(e) => fieldNum("sale_cost_rate_pct", e.target.value)} /></label>
            <label>NPV 할인율 (%)<input type="number" min={0} step={0.1} value={n("discount_rate_pct")} onChange={(e) => fieldNum("discount_rate_pct", e.target.value)} placeholder="비우면 NPV 생략" /></label>
          </div>
        </fieldset>

        <div className="prof-actions">
          <button type="submit" className="prof-submit-btn" disabled={loading}>{loading ? "분석 중…" : "수익성 분석"}</button>
          <span className="prof-actions-hint">NOI·Cap Rate·DSCR·IRR과 시나리오·민감도, 주변 실거래 비교를 계산합니다.</span>
        </div>
      </form>

      <div className="prof-result-root">
        {error && (
          <div className="prof-error" role="alert">
            <strong>분석을 완료하지 못했습니다</strong>
            <span>{error}</span>
          </div>
        )}
        {!error && loading && (
          <div className="prof-loading" aria-live="polite">
            <span className="prof-spinner" aria-hidden="true" />
            주변 실거래를 조회하고 재무지표를 계산하는 중…
          </div>
        )}
        {!error && !loading && !result && (
          <div className="prof-empty">
            <p className="prof-empty-title">아직 분석 결과가 없습니다</p>
            <p className="prof-empty-desc">매입·임대·대출 조건을 입력하고 “수익성 분석”을 누르면 결과가 여기에 표시됩니다.</p>
          </div>
        )}
        {result && <Results data={result} onApplyRent={handleApplyRent} />}
      </div>
    </div>
  );
}
