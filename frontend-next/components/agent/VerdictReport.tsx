"use client";

import { EvidenceDetails, PointsGrid, ReportError, ReportLoading, RestartRow, ScoreBar, VerdictHeadline, WarningsCard } from "@/components/agent/ReportParts";
import { fmtMan, toneFor } from "@/lib/agentFormat";
import type { DecideRankedItem, DecideResponse } from "@/lib/api";

const STAGES = ["후보지 입지 계산 중…", "경쟁 분석 중…", "결론 정리 중…"];

function RentVerdictLine({ item }: { item: DecideRankedItem }) {
  const gap = item.rent_gap_man;
  if (gap === null || gap === undefined || !Number.isFinite(gap)) {
    if (item.max_acceptable_rent_man === null || item.max_acceptable_rent_man === undefined) return null;
    // 상한 0은 "0만원까지입니다"로 적히면 뜻이 전달되지 않는다 — 예상 매출로는
    // 인건비·고정비를 빼고 나면 월세 낼 여력이 없다는 뜻이다.
    if (item.max_acceptable_rent_man <= 0) {
      return (
        <p className="rent-verdict rent-verdict--over">
          예상 매출로는 인건비·고정비를 빼고 나면 <strong>월세를 낼 여력이 남지 않습니다</strong>.
        </p>
      );
    }
    return (
      <p className="rent-verdict rent-verdict--neutral">
        이 자리에서 감당 가능한 월세는 <strong>{fmtMan(item.max_acceptable_rent_man)}</strong>까지입니다.
      </p>
    );
  }
  if (gap > 0) {
    return (
      <p className="rent-verdict rent-verdict--over">
        현재 월세 <strong>{fmtMan(item.current_rent_man)}</strong> — 적정 상한 {fmtMan(item.max_acceptable_rent_man)}보다{" "}
        <strong>{fmtMan(gap)} 높음</strong>
      </p>
    );
  }
  return (
    <p className="rent-verdict rent-verdict--within">
      현재 월세 <strong>{fmtMan(item.current_rent_man)}</strong> — 적정 상한 {fmtMan(item.max_acceptable_rent_man)} 안쪽{" "}
      {gap < 0 ? <>(<strong>{fmtMan(Math.abs(gap))}</strong> 여유)</> : null}
    </p>
  );
}

function RankedCard({ item }: { item: DecideRankedItem }) {
  const tone = toneFor(item.verdict);

  return (
    <article className={`card ranked-card ranked-card--${tone}`}>
      <header className="ranked-card-head">
        <span className="ranked-card-rank">{item.rank}위</span>
        <h3 className="ranked-card-label">{item.label}</h3>
        <span className={`verdict-chip verdict-chip--${tone}`}>{item.verdict_label}</span>
      </header>

      <ScoreBar label="추천도" score={item.recommend_score} note={item.verdict_label} tone={tone} />

      <div className="ranked-card-sales">
        <span className="ranked-card-sales-label">예상 월매출</span>
        <strong className="ranked-card-sales-value">{fmtMan(item.expected_monthly_sales_man)}</strong>
        {/* 점 하나는 확정치처럼 읽힌다 — 최근 분기 변동폭을 정직하게 병기. */}
        {Array.isArray(item.expected_monthly_sales_range_man) && item.expected_monthly_sales_range_man.length === 2 && (
          <span className="ranked-card-sales-range">
            {" "}({fmtMan(item.expected_monthly_sales_range_man[0])}~{fmtMan(item.expected_monthly_sales_range_man[1])})
          </span>
        )}
        {item.expected_sales_basis && <p className="ranked-card-sales-basis">{item.expected_sales_basis}</p>}
      </div>

      <RentVerdictLine item={item} />

      <PointsGrid strengths={item.strengths} risks={item.risks} actions={item.actions} />

      <EvidenceDetails evidence={item.evidence} />
    </article>
  );
}

export function VerdictReport({
  loading, error, result, onRetry, onRestart,
}: {
  loading: boolean;
  error: string;
  result: DecideResponse | null;
  onRetry: () => void;
  onRestart: () => void;
}) {
  if (loading) return <ReportLoading title="후보지를 비교하고 있어요" subtitle="10초 정도 걸립니다." stages={STAGES} />;

  if (error) return <ReportError title="결론을 내지 못했어요" error={error} onRetry={onRetry} onRestart={onRestart} />;

  if (!result) return null;

  const ranked = Array.isArray(result.ranked) ? result.ranked : [];
  const topTone = toneFor(ranked[0]?.verdict);

  return (
    <div className="agent-stage agent-stage--wide">
      <div className="verdict-report">
        <VerdictHeadline tone={topTone} text={result.headline || "비교 결과가 준비됐습니다."} />

        {ranked.length === 0 ? (
          <div className="card">
            <p className="agent-step-hint">비교할 수 있는 후보지가 없습니다. 후보지를 다시 골라 주세요.</p>
            <button type="button" className="sim-btn" onClick={onRestart}>조건 수정</button>
          </div>
        ) : (
          <div className="ranked-list">
            {ranked.map((item) => <RankedCard key={item.id ?? `rank-${item.rank}`} item={item} />)}
          </div>
        )}

        {result.comparison_note && (
          <div className="card comparison-note">
            <h3>어떤 경우에 무엇을 고를까</h3>
            <p>{result.comparison_note}</p>
          </div>
        )}

        <WarningsCard warnings={result.warnings} />

        <RestartRow label="조건 바꿔서 다시 비교" onRestart={onRestart} />
      </div>
    </div>
  );
}
