"use client";

import { AmountBar, EvidenceDetails, PointsGrid, RangeBar, ReportError, ReportLoading, RestartRow, TileRow, VerdictHeadline, WarningsCard } from "@/components/agent/ReportParts";
import { bandPosition, barWidth, fmtMan, fmtPct, fmtRatio, fmtSignedPct, fmtYears, hasNumber, toneFor, type VerdictTone } from "@/lib/agentFormat";
import type { PurchaseMarketBand, PurchaseVerdictRequest, PurchaseVerdictResponse } from "@/lib/api";

const STAGES = ["주변 실거래를 모으는 중…", "임대수익과 대출 상환 계산 중…", "적정 매입가 정리 중…"];

// lib/api.ts의 PurchaseMarketBand는 아직 이 필드들을 선언하지 않았지만
// 백엔드(app/routers/goals.py MarketBandOut)는 이미 내려준다 — 표본이 적을 때
// 캡션 없이 색만 확신 있게 그려지는 걸 막으려면 이 값들이 꼭 필요하다.
type MarketBandWithSample = PurchaseMarketBand & {
  insufficient_sample?: boolean;
  relaxations?: string[];
  data_source?: string;
};

/** 대출을 감당하는지(DSCR)는 1.0이 기준선이라 색을 따로 준다. */
function dscrTone(dscr: number | null | undefined): VerdictTone {
  if (!hasNumber(dscr)) return "info";
  if ((dscr as number) >= 1.3) return "positive";
  if ((dscr as number) >= 1.0) return "caution";
  return "negative";
}

export function PurchaseVerdictReport({
  loading, error, result, request, onRetry, onRestart,
}: {
  loading: boolean;
  error: string;
  result: PurchaseVerdictResponse | null;
  request: PurchaseVerdictRequest | null;
  onRetry: () => void;
  onRestart: () => void;
}) {
  if (loading) return <ReportLoading title="매입해도 될지 보고 있어요" subtitle="15초 정도 걸립니다." stages={STAGES} />;
  if (error) return <ReportError title="판정하지 못했어요" error={error} onRetry={onRetry} onRestart={onRestart} />;
  if (!result) return null;

  const tone = toneFor(result.verdict);
  const askingPrice = request?.price_manwon ?? null;
  const areaSqm = request?.area_sqm ?? null;
  const subjectPerSqm = hasNumber(askingPrice) && hasNumber(areaSqm) && (areaSqm as number) > 0
    ? (askingPrice as number) / (areaSqm as number)
    : null;
  const band = (result.market_band as MarketBandWithSample | null | undefined) ?? null;
  // 표본이 없으면 market_band 자체가 없거나 한쪽 끝만 온다 — 그리지 않는다.
  const bandDrawable = bandPosition(subjectPerSqm, band?.p25, band?.p75) !== null;
  // 표본이 너무 적으면(백엔드 기준 미달) 판정 색(초록/빨강)을 쓰지 않고 중립색으로
  // 그리고, 몇 건짜리인지·조건을 완화했는지를 캡션에 그대로 적는다.
  const bandInsufficientSample = band?.insufficient_sample ?? true;
  const bandTone: VerdictTone = bandInsufficientSample ? "info" : tone;
  const maxPrice = result.max_acceptable_price_manwon;
  const priceScaleMax = Math.max(askingPrice ?? 0, maxPrice ?? 0);
  const overPay = hasNumber(askingPrice) && hasNumber(maxPrice) ? (askingPrice as number) - (maxPrice as number) : null;

  const tiles = [
    { label: "연 수익률", value: fmtPct(result.cap_rate_pct), hint: hasNumber(result.monthly_noi_man) ? `월 순수입 ${fmtMan(result.monthly_noi_man)}` : "임대수입 기준", tone },
    { label: "대출 감당력", value: fmtRatio(result.dscr), hint: "1.0배 미만이면 월세로 이자를 못 냅니다", tone: dscrTone(result.dscr) },
    { label: "원금 회수", value: fmtYears(result.payback_years), hint: "지금 수입이 이어질 때", tone: "info" as VerdictTone },
  ];

  return (
    <div className="agent-stage agent-stage--wide">
      <div className="verdict-report">
        <VerdictHeadline tone={tone} text={result.headline || "매입 판정이 준비됐습니다."} chip={result.verdict_label} />

        <div className="card report-block">
          <h3 className="report-block-title">이 매물의 수익성</h3>
          <TileRow tiles={tiles} />
        </div>

        {(hasNumber(askingPrice) || hasNumber(maxPrice)) && (
          <div className={`card deal-card deal-card--${tone}`}>
            <h3 className="report-block-title">얼마까지 내도 되는가</h3>
            <AmountBar label="제시가" valueText={fmtMan(askingPrice)} ratio={barWidth(askingPrice, priceScaleMax)} tone="info" />
            <AmountBar label="적정 매입가 상한" valueText={fmtMan(maxPrice)} ratio={barWidth(maxPrice, priceScaleMax)} tone={tone} />
            {overPay !== null && (
              <p className={`rent-verdict ${overPay > 0 ? "rent-verdict--over" : "rent-verdict--within"}`}>
                {overPay > 0
                  ? <>제시가가 적정 상한보다 <strong>{fmtMan(overPay)}</strong> 높습니다.</>
                  : <>제시가가 적정 상한보다 <strong>{fmtMan(Math.abs(overPay))}</strong> 낮습니다.</>}
                {hasNumber(result.price_premium_pct) ? ` (${fmtSignedPct(result.price_premium_pct)})` : ""}
              </p>
            )}
          </div>
        )}

        {bandDrawable && band && (
          <div className="card report-block">
            <h3 className="report-block-title">주변 거래가와 비교하면</h3>
            <RangeBar
              label="주변 거래가 구간에서 이 매물 (면적 1㎡당)"
              low={band.p25}
              high={band.p75}
              mid={band.p50}
              value={subjectPerSqm}
              format={fmtMan}
              lowLabel={`싼 편 ${fmtMan(band.p25)}`}
              midLabel={hasNumber(band.p50) ? `가운데 ${fmtMan(band.p50)}` : undefined}
              highLabel={`비싼 편 ${fmtMan(band.p75)}`}
              valueLabel="이 매물"
              tone={bandTone}
              note={[
                hasNumber(band.n)
                  ? `최근 거래 ${band.n}건${hasNumber(band.radius_m) ? ` · 반경 ${band.radius_m}m` : ""} 기준입니다.`
                  : null,
                bandInsufficientSample ? "표본이 적어 참고용으로만 보세요." : null,
                band.relaxations && band.relaxations.length > 0
                  ? `조건을 완화해 모은 표본입니다 (${band.relaxations.join(", ")}).`
                  : null,
              ].filter(Boolean).join(" ") || undefined}
            />
            {subjectPerSqm === null && <p className="agent-step-hint">전용면적을 입력하면 이 매물이 구간 어디인지 표시됩니다.</p>}
          </div>
        )}

        <div className="card report-block">
          <PointsGrid strengths={result.strengths} risks={result.risks} actions={result.actions} />
          <EvidenceDetails evidence={result.evidence} />
        </div>

        <WarningsCard warnings={result.warnings} />

        <RestartRow label="조건 바꿔서 다시 보기" onRestart={onRestart} />
      </div>
    </div>
  );
}
