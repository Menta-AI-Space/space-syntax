"use client";

import { AmountBar, EvidenceDetails, PointsGrid, RangeBar, ReportError, ReportLoading, RestartRow, ScoreBar, TileRow, VerdictHeadline, WarningsCard } from "@/components/agent/ReportParts";
import { bandPosition, barWidth, fmtMan, fmtMonths, fmtPct, hasNumber, toneFor } from "@/lib/agentFormat";
import type { LeaseVerdictRequest, LeaseVerdictResponse } from "@/lib/api";

const STAGES = ["매물 주변 상권을 읽는 중…", "예상 매출과 손익분기 계산 중…", "적정 임대료와 협상 조건 정리 중…"];

/** 손익분기와 예상 매출을 같은 자로 비교한다 — "벌 수 있는가"에 먼저 답한다. */
function BreakEvenBlock({ expected, expectedRange, breakEven }: { expected?: number | null; expectedRange?: number[] | null; breakEven?: number | null }) {
  if (!hasNumber(expected) && !hasNumber(breakEven)) return null;
  const max = Math.max(expected ?? 0, breakEven ?? 0);
  const covered = hasNumber(expected) && hasNumber(breakEven) ? (expected as number) - (breakEven as number) : null;
  // 점 하나는 확정치처럼 읽힌다 — 최근 분기 변동폭(백엔드가 같은 입지보정으로
  // 계산)을 병기해 정직한 불확실성을 보인다. 특히 본전선이 범위 안에 걸치면
  // "분기에 따라 적자가 될 수 있다"는 뜻이라 그 말도 해 준다.
  const range = Array.isArray(expectedRange) && expectedRange.length === 2 ? expectedRange : null;
  const rangeText = range ? `${fmtMan(range[0])}~${fmtMan(range[1])}` : null;
  const breakEvenInRange =
    range !== null && hasNumber(breakEven) &&
    (breakEven as number) >= range[0] && (breakEven as number) <= range[1];
  return (
    <div className="card report-block">
      <h3 className="report-block-title">벌어서 남는지</h3>
      <AmountBar
        label="예상 월매출"
        valueText={rangeText ? `${fmtMan(expected)} (${rangeText})` : fmtMan(expected)}
        ratio={barWidth(expected, max)}
        tone="info"
      />
      <AmountBar label="본전 되는 월매출" valueText={fmtMan(breakEven)} ratio={barWidth(breakEven, max)} tone="caution" />
      {breakEvenInRange && (
        <p className="rent-verdict rent-verdict--over">
          최근 분기 변동폭 안에 본전선이 걸칩니다 — 분기에 따라 적자가 날 수 있습니다.
        </p>
      )}
      {covered !== null && (
        <p className={`rent-verdict ${covered >= 0 ? "rent-verdict--within" : "rent-verdict--over"}`}>
          {covered >= 0
            ? <>예상 매출이 본전선보다 <strong>{fmtMan(covered)}</strong> 많습니다.</>
            : <>예상 매출이 본전선에 <strong>{fmtMan(Math.abs(covered))}</strong> 못 미칩니다.</>}
        </p>
      )}
    </div>
  );
}

export function LeaseVerdictReport({
  loading, error, result, request, onRetry, onRestart,
}: {
  loading: boolean;
  error: string;
  result: LeaseVerdictResponse | null;
  request: LeaseVerdictRequest | null;
  onRetry: () => void;
  onRestart: () => void;
}) {
  if (loading) return <ReportLoading title="계약해도 될지 보고 있어요" subtitle="15초 정도 걸립니다." stages={STAGES} />;
  if (error) return <ReportError title="판정하지 못했어요" error={error} onRetry={onRetry} onRestart={onRestart} />;
  if (!result) return null;

  const tone = toneFor(result.verdict);
  const negotiation = result.negotiation ?? null;
  const currentRent = request?.monthly_rent_man ?? null;
  // 값이 온 항목만 타일로 세운다 — 못 채운 칸을 "—"로 남기지 않는다.
  // 월세를 실제로 깎아야 하는 자리인지 — 제목과 조언의 어조를 여기서 가른다.
  const needsRentCut =
    hasNumber(negotiation?.target_rent_man) &&
    hasNumber(currentRent) &&
    (negotiation!.target_rent_man as number) < (currentRent as number);
  const negotiationTiles: { label: string; value: string; hint?: string }[] = [];
  if (negotiation) {
    // 목표가 현재 월세와 같으면 협상할 게 없다는 뜻이다. 그대로 "목표 월세
    // 520만원 / 지금 520만원"을 띄우면 조언처럼 보이지만 아무 내용이 없다.
    const target = negotiation.target_rent_man;
    if (hasNumber(target) && (!hasNumber(currentRent) || (target as number) < (currentRent as number))) {
      negotiationTiles.push({ label: "목표 월세", value: fmtMan(target), hint: hasNumber(currentRent) ? `지금 ${fmtMan(currentRent)}` : undefined });
    }
    // 0개월은 "받을 필요 없음"이지 협상 조건이 아니다.
    if (hasNumber(negotiation.rent_free_months) && (negotiation.rent_free_months as number) > 0) {
      negotiationTiles.push({ label: "렌트프리", value: `${negotiation.rent_free_months}개월`, hint: "초기 월세를 면제받으세요" });
    }
    if (hasNumber(negotiation.max_key_money_man)) {
      negotiationTiles.push({ label: "권리금 상한", value: fmtMan(negotiation.max_key_money_man), hint: "이보다 높으면 다시 생각해 보세요" });
    }
  }

  // 밴드(적정 구간)와 차액 문장은 각각 없을 수 있다 — 둘 다 없으면 블록 자체를 뺀다.
  const rentGap = result.rent_gap_man;
  const bandDrawable = bandPosition(currentRent, result.appropriate_rent_low_man, result.appropriate_rent_high_man) !== null;
  const rentGapLine = hasNumber(rentGap) ? (
    <p className={`rent-verdict ${(rentGap as number) > 0 ? "rent-verdict--over" : "rent-verdict--within"}`}>
      {(rentGap as number) > 0
        ? <>적정선보다 <strong>{fmtMan(rentGap)}</strong> 비쌉니다.</>
        : <>적정선보다 <strong>{fmtMan(Math.abs(rentGap as number))}</strong> 쌉니다.</>}
    </p>
  ) : null;

  return (
    <div className="agent-stage agent-stage--wide">
      <div className="verdict-report">
        <VerdictHeadline tone={tone} text={result.headline || "계약 판정이 준비됐습니다."} chip={result.verdict_label} />

        {hasNumber(result.recommend_score) && (
          <div className="card report-block">
            <ScoreBar label="추천도" score={result.recommend_score} note={result.verdict_label} tone={tone} />
          </div>
        )}

        {/* 이 화면의 핵심 — 무엇을 요구하고 들어갈 것인가. */}
        <div className={`card deal-card deal-card--${tone}`}>
          {/* 월세를 깎아야 하는 자리인지, 지금 조건이 이미 괜찮은지에 따라
              제목이 달라야 한다. 깎을 게 없는데 "협상하세요"라고 하면 안 된다. */}
          <h3 className="report-block-title">
            {needsRentCut ? "이 조건으로 협상하세요" : "계약 전 확인할 조건"}
          </h3>
          {!needsRentCut && hasNumber(currentRent) && (
            <p className="agent-step-hint">지금 월세는 감당 범위 안이라 인하 협상은 필요하지 않습니다.</p>
          )}
          {negotiationTiles.length > 0 ? (
            <TileRow tiles={negotiationTiles} />
          ) : (
            <p className="agent-step-hint">협상 조건을 계산할 만한 자료가 부족합니다. 아래 숫자와 위험을 보고 판단해 주세요.</p>
          )}
          {hasNumber(result.key_money_payback_months) && (
            <p className="deal-card-note">권리금은 {fmtMonths(result.key_money_payback_months)}이면 회수됩니다.</p>
          )}
        </div>

        <BreakEvenBlock expected={result.expected_monthly_sales_man} expectedRange={result.expected_monthly_sales_range_man} breakEven={result.break_even_monthly_man} />

        {result.expected_sales_basis && <p className="report-note">{result.expected_sales_basis}</p>}

        {/* 적정 임대료 밴드 안에서 지금 월세가 어디인지. */}
        {(bandDrawable || rentGapLine) && (
          <div className="card report-block">
            <h3 className="report-block-title">월세가 적정한지</h3>
            <RangeBar
              label="적정 월세 구간에서 지금 월세"
              low={result.appropriate_rent_low_man}
              high={result.appropriate_rent_high_man}
              value={currentRent}
              format={fmtMan}
              lowLabel={`낮음 ${fmtMan(result.appropriate_rent_low_man)}`}
              highLabel={`높음 ${fmtMan(result.appropriate_rent_high_man)}`}
              valueLabel="지금 월세"
              tone={tone}
              note={hasNumber(result.rent_burden_pct) ? `예상 매출의 ${fmtPct(result.rent_burden_pct)}가 월세로 나갑니다.` : undefined}
            />
            {rentGapLine}
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
