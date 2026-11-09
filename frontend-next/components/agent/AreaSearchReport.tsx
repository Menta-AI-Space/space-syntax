"use client";

import { CandidateMiniMap } from "@/components/agent/CandidateMiniMap";
import { EvidenceDetails, PointsGrid, ReportError, ReportLoading, RestartRow, ScoreBar, VerdictHeadline, WarningsCard } from "@/components/agent/ReportParts";
import { fmtMan, hasNumber, toneFor, toneForScore } from "@/lib/agentFormat";
import { areaSearchRegionLabel, type AreaCandidate, type AreaSearchResponse } from "@/lib/api";

const STAGES = ["동네 안 자리들을 훑는 중…", "자리마다 매출과 경쟁 계산 중…", "순위를 정리 중…"];

/** 백엔드(decision.compute_rank_drivers)가 계산한 '순위를 가른 요인' 한 줄. */
type RankDriver = {
  key?: string;
  label?: string;
  text?: string;
  detail?: string | null;
  direction?: "up" | "down" | "flat";
  impact?: number | null;
};

function readDrivers(evidence: Record<string, unknown> | undefined): RankDriver[] {
  const raw = evidence?.["rank_drivers"];
  return Array.isArray(raw) ? (raw as RankDriver[]) : [];
}

/**
 * 같은 동네에서 뽑힌 후보들은 상권 벤치마크가 같아 **예상 월매출이 서로 똑같은
 * 값**으로 찍힌다. 그런데 점수는 다르다. 이 줄이 없으면 "매출이 같은데 왜 순위가
 * 다르지?"에 화면이 답을 못 한다 — 점수 차를 만든 항목을 그대로 보여 준다.
 */
function RankDrivers({ drivers }: { drivers: RankDriver[] }) {
  if (drivers.length === 0) return null;
  return (
    <div className="rank-drivers">
      <span className="rank-drivers-title">순위를 가른 이유</span>
      <ul className="rank-drivers-list">
        {drivers.map((d, i) => (
          <li key={`${d.key ?? "driver"}-${i}`} className={`rank-driver rank-driver--${d.direction ?? "flat"}`}>
            <span className="rank-driver-mark" aria-hidden="true">
              {d.direction === "up" ? "▲" : d.direction === "down" ? "▼" : "＝"}
            </span>
            <span className="rank-driver-text">{d.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// /api/decide의 랭킹 카드와 같은 말과 순서를 쓴다 — 같은 판단을 두 화면에서
// 다르게 읽지 않도록.
function CandidateCard({ item, rank }: { item: AreaCandidate; rank: number }) {
  const tone = item.verdict ? toneFor(item.verdict) : toneForScore(item.recommend_score);
  const label = item.verdict_label ?? "";

  return (
    <article className={`card ranked-card ranked-card--${tone}`}>
      <header className="ranked-card-head">
        <span className="ranked-card-rank">{rank}위</span>
        <h3 className="ranked-card-label">{item.label || `후보 ${rank}`}</h3>
        {label ? <span className={`verdict-chip verdict-chip--${tone}`}>{label}</span> : null}
      </header>

      <ScoreBar label="추천도" score={item.recommend_score} note={label || undefined} tone={tone} />

      <div className="ranked-card-sales">
        <span className="ranked-card-sales-label">예상 월매출</span>
        <strong className="ranked-card-sales-value">{fmtMan(item.expected_monthly_sales_man)}</strong>
      </div>

      {hasNumber(item.max_acceptable_rent_man) && (
        // 상한이 0이면 "0만원까지입니다"라고 적히는데, 그건 뜻이 전달되지 않는다.
        // 실제 의미는 '예상 매출로는 인건비·고정비를 빼고 나면 월세 낼 여력이
        // 남지 않는다'이므로 그렇게 쓴다.
        (item.max_acceptable_rent_man as number) > 0 ? (
          <p className="rent-verdict rent-verdict--neutral">
            이 자리에서 감당 가능한 월세는 <strong>{fmtMan(item.max_acceptable_rent_man)}</strong>까지입니다.
          </p>
        ) : (
          <p className="rent-verdict rent-verdict--over">
            예상 매출로는 인건비·고정비를 빼고 나면 <strong>월세를 낼 여력이 남지 않습니다</strong>.
          </p>
        )
      )}

      <RankDrivers drivers={readDrivers(item.evidence)} />

      <PointsGrid strengths={item.strengths} risks={item.risks} />

      <EvidenceDetails evidence={item.evidence} />
    </article>
  );
}

export function AreaSearchReport({
  loading, error, result, onRetry, onRestart,
}: {
  loading: boolean;
  error: string;
  result: AreaSearchResponse | null;
  onRetry: () => void;
  onRestart: () => void;
}) {
  if (loading) return <ReportLoading title="어디가 좋을지 찾고 있어요" subtitle="20초 정도 걸립니다." stages={STAGES} />;
  if (error) return <ReportError title="자리를 찾지 못했어요" error={error} onRetry={onRetry} onRestart={onRestart} />;
  if (!result) return null;

  const candidates = Array.isArray(result.candidates) ? result.candidates : [];
  const topTone = candidates[0]
    ? (candidates[0].verdict ? toneFor(candidates[0].verdict) : toneForScore(candidates[0].recommend_score))
    : "info";

  return (
    <div className="agent-stage agent-stage--wide">
      <div className="verdict-report">
        <VerdictHeadline
          tone={topTone}
          text={result.headline || "찾은 자리를 정리했습니다."}
          chip={areaSearchRegionLabel(result)}
        />

        {candidates.length === 0 ? (
          <div className="card">
            <p className="agent-step-hint">이 동네에서는 추천할 만한 자리를 찾지 못했습니다. 동네나 예산을 바꿔서 다시 찾아보세요.</p>
            <button type="button" className="sim-btn" onClick={onRestart}>조건 수정</button>
          </div>
        ) : (
          <>
            {/* 주소만 나열하면 어디인지 감이 안 온다 — 순위 마커로 먼저 보여 준다. */}
            <CandidateMiniMap candidates={candidates} center={result.center} />
            <div className="ranked-list">
              {candidates.map((item, index) => (
                <CandidateCard key={`${item.label ?? "cand"}-${index}`} item={item} rank={index + 1} />
              ))}
            </div>
          </>
        )}

        <WarningsCard warnings={result.warnings} />

        <RestartRow label="조건 바꿔서 다시 찾기" onRestart={onRestart} />
      </div>
    </div>
  );
}
