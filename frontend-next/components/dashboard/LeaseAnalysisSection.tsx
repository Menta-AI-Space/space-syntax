"use client";

import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type LeaseFeasibilityResponse, type RadiusM } from "@/lib/api";
import type { UpjongSelection } from "@/components/BusinessPicker";
import { explanationSourceLabel } from "@/lib/explanationSource";
import { siteInputsKey } from "@/lib/inputsKey";
import { buildLeaseSiteContextTiles, leaseBestModels } from "@/lib/leaseSiteContextCard";
import { defaultLeaseInputs, intensityClass, leaseFmtMan, saveLeaseInputs, verdictClass, type LeaseInputs } from "./leaseUtils";

interface LeaseAnalysisSectionProps {
  lat: number;
  lng: number;
  radiusM: RadiusM;
  upjong: UpjongSelection | null;
  subjectCodes?: string[] | null;
}

function renderBoldSegments(line: string): ReactNode[] {
  const parts = line.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    const m = part.match(/^\*\*([^*]+)\*\*$/);
    return m ? <strong key={i}>{m[1]}</strong> : <Fragment key={i}>{part}</Fragment>;
  });
}

// Ported from `renderLeaseResult`'s `explanation.text` markdown-lite
// formatting (`**bold**`, `\n\n` paragraphs, `\n` line breaks) — rendered as
// React nodes instead of `innerHTML` to avoid injecting raw HTML from an API
// response.
function renderExplanation(text: string): ReactNode {
  return text.split(/\n\n/).map((para, pi) => (
    <p key={pi}>
      {para.split(/\n/).map((line, li, arr) => (
        <Fragment key={li}>
          {renderBoldSegments(line)}
          {li < arr.length - 1 && <br />}
        </Fragment>
      ))}
    </p>
  ));
}

function LeaseResult({ data }: { data: LeaseFeasibilityResponse }) {
  const band = data.appropriate_rent_band || "벤치마크 없음";
  const bench =
    data.benchmark_monthly_revenue_man != null
      ? `상권 추정 월매출 약 ${leaseFmtMan(data.benchmark_monthly_revenue_man)}`
      : data.benchmark_scope || "서울 외/데이터 없음";
  const explanationSource = explanationSourceLabel(data.explanation?.source);
  const siteTiles = buildLeaseSiteContextTiles(data.site_context);
  const bestModels = leaseBestModels(data.site_context);

  return (
    <div className="lease-result">
      <p className={`lease-verdict ${verdictClass(data.verdict)}`}>판정: {data.verdict}</p>
      <ul className="lease-kpi-list">
        <li><span>월 고정비</span><strong>{leaseFmtMan(data.monthly_fixed_cost_man)}</strong></li>
        <li><span>손익분기 월매출</span><strong>{leaseFmtMan(data.break_even_monthly_man)}</strong></li>
        <li><span>필요 일매출</span><strong>{leaseFmtMan(data.break_even_daily_man)}</strong></li>
        <li><span>필요 주문/일</span><strong>{data.orders_per_day}건</strong></li>
        <li><span>임대료 부담률</span><strong>{data.rent_burden_pct != null ? `${data.rent_burden_pct}%` : "—"}</strong></li>
        <li><span>적정 임차비</span><strong>{band}</strong></li>
        <li><span>권리금 회수</span><strong>{data.key_money_payback_months ? `${data.key_money_payback_months}개월` : "—"}</strong></li>
        <li><span>권장 월 임차비 상한</span><strong>{leaseFmtMan(data.recommended_rent_max_man)}</strong></li>
      </ul>
      <p className="demand-meta">{bench} · {data.benchmark_data_source || ""}</p>
      {(data.warnings || []).map((w, i) => <p key={i} className="demand-meta dash-iso-warn">{w}</p>)}
      {siteTiles.length > 0 && (
        <ul className="lease-kpi-list">
          {siteTiles.map((t) => (
            <li key={t.label}>
              <span>{t.label}</span>
              <strong className={t.toneLabel ? intensityClass(t.toneLabel) : undefined}>{t.value}</strong>
            </li>
          ))}
        </ul>
      )}
      {bestModels.length > 0 && <p className="demand-meta">이 자리에 맞는 모델: {bestModels.join(" · ")}</p>}
      <div className="lease-explanation">{renderExplanation(data.explanation?.text || "")}</div>
      {explanationSource && <p className="demand-meta">{explanationSource}</p>}
    </div>
  );
}

// Ported from `renderLeaseAnalysisCard`/`runLeaseAnalysis`/`renderLeaseResult`
// in frontend/js/lease_analysis.js. Input persistence uses the exact same
// localStorage key/shape (`space_syntax_lease_inputs`) so previously saved
// inputs keep loading.
export function LeaseAnalysisSection({ lat, lng, radiusM, upjong, subjectCodes }: LeaseAnalysisSectionProps) {
  const [inputs, setInputs] = useState<LeaseInputs>(() => defaultLeaseInputs());
  const [result, setResult] = useState<LeaseFeasibilityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // 결과는 이 지점·업종으로 계산된 값이다. 상권 추정 월매출·적정 임차비·권장
  // 상한이 전부 위치에서 나오므로, 다른 자리를 분석하고 돌아왔는데 판정이 그대로
  // 남아 있으면 신촌 벤치마크를 강남 자리의 판정으로 읽게 된다.
  const inputsKey = siteInputsKey({ lat, lng, radiusM, upjong, extra: subjectCodes || [] });
  const inputsKeyRef = useRef(inputsKey);
  useEffect(() => {
    if (inputsKeyRef.current === inputsKey) return;
    inputsKeyRef.current = inputsKey;
    setResult(null);
    setError("");
  }, [inputsKey]);

  function field(key: keyof LeaseInputs, value: string) {
    setInputs((cur) => ({ ...cur, [key]: value === "" ? "" : Number(value) }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    saveLeaseInputs(inputs);
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await api.leaseFeasibility({
        lat,
        lng,
        radius_m: radiusM,
        inds_lcls_cd: upjong?.inds_lcls_cd || null,
        inds_mcls_cd: upjong?.inds_mcls_cd || null,
        inds_scls_cd: upjong?.inds_scls_cd || null,
        business_type: upjong?.business_type || "general",
        monthly_rent_man: Number(inputs.monthly_rent_man) || 0,
        maintenance_man: Number(inputs.maintenance_man) || 0,
        key_money_man: inputs.key_money_man === "" ? null : Number(inputs.key_money_man),
        area_pyeong: inputs.area_pyeong === "" ? null : Number(inputs.area_pyeong) || null,
        staff_count: Number(inputs.staff_count) || 0,
        ticket_price: Number(inputs.ticket_price) || 4500,
        cogs_ratio: inputs.cogs_ratio_pct === "" ? null : Number(inputs.cogs_ratio_pct) / 100,
        delivery_share: inputs.delivery_share_pct === "" ? 0 : Number(inputs.delivery_share_pct) / 100,
        subject_codes: subjectCodes || null,
      });
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "분석 실패");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="dash-card dash-card--lease" data-dash="lease">
      <header className="dash-card-header">
        <span className="dash-card-icon" aria-hidden="true">💴</span>
        <h3>임대조건 적정성 분석</h3>
      </header>
      <form className="lease-form" onSubmit={(e) => { void handleSubmit(e); }}>
        <div className="lease-form-grid">
          <label>월세 (만원)<input type="number" min={0} value={inputs.monthly_rent_man} onChange={(e) => field("monthly_rent_man", e.target.value)} required /></label>
          <label>관리비 (만원)<input type="number" min={0} value={inputs.maintenance_man} onChange={(e) => field("maintenance_man", e.target.value)} /></label>
          <label>권리금 (만원)<input type="number" min={0} value={inputs.key_money_man} onChange={(e) => field("key_money_man", e.target.value)} placeholder="선택" /></label>
          <label>면적 (평)<input type="number" min={1} step={0.1} value={inputs.area_pyeong} onChange={(e) => field("area_pyeong", e.target.value)} /></label>
          <label>직원 수<input type="number" min={0} max={50} value={inputs.staff_count} onChange={(e) => field("staff_count", e.target.value)} /></label>
          <label>객단가 (원)<input type="number" min={100} value={inputs.ticket_price} onChange={(e) => field("ticket_price", e.target.value)} /></label>
          <label>원가율 (%)<input type="number" min={0} max={95} value={inputs.cogs_ratio_pct} onChange={(e) => field("cogs_ratio_pct", e.target.value)} /></label>
          <label>배달 비중 (%)<input type="number" min={0} max={100} value={inputs.delivery_share_pct} onChange={(e) => field("delivery_share_pct", e.target.value)} /></label>
        </div>
        <button type="submit" className="lease-submit-btn" disabled={loading}>{loading ? "분석 중…" : "적정성 분석"}</button>
      </form>
      <div className="lease-result-root">
        {error && <p className="demand-meta">분석 실패: {error}</p>}
        {!error && !result && <p className="demand-meta">월세·관리비·인건비를 입력하고 분석하면 손익분기와 적정 임차비를 계산합니다.</p>}
        {result && <LeaseResult data={result} />}
      </div>
    </section>
  );
}
