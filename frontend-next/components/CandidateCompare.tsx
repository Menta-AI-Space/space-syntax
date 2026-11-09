"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { api, type RadiusM, type SiteScoreResponse, type SubScore } from "@/lib/api";
import type { UpjongSelection } from "@/components/BusinessPicker";
import { compareInputsKey } from "@/lib/inputsKey";
import { leadIdsForMetric } from "@/lib/metricDirection";
import {
  clearSiteAnalysisWatchlist,
  getSiteAnalysisWatchlist,
  removeFromWatchlist,
  DEAL_STAGES,
  DEAL_STAGE_LABELS,
  setItemMemo,
  setItemStage,
  stageCounts,
  stageOf,
  type DealStage,
  type WatchlistItem,
} from "@/lib/watchlist";

const MIN_CANDIDATES = 2;
const MAX_CANDIDATES = 5;
export const CANDIDATE_LABELS = ["A", "B", "C", "D", "E"];

interface Candidate {
  id: string;
  label: string;
  address: string;
  lat: number;
  lng: number;
}

interface CandidateCompareProps {
  watchlist: WatchlistItem[];
  onWatchlistChange: (next: WatchlistItem[]) => void;
  radius: RadiusM;
  upjong: UpjongSelection | null;
  // Selection is lifted to AppShell so the shared KakaoMap instance (see
  // `syncCandidateMarkers` port in AppShell.tsx) can render A-E badges for
  // selected candidates. Ported from `selectedWatchlistIds` in
  // frontend/js/candidate_compare.js.
  selectedIds: string[];
  onSelectedIdsChange: (ids: string[]) => void;
  // Ported from `focusCandidateBounds`, called from `renderCompareResults`.
  onFocusCandidates: (targets: { lat: number; lng: number }[]) => void;
}

// A candidate whose analysis hit a data-source fallback/실패 (SiteScoreResponse.warnings)
// isn't reliable enough to compare against — see docs/agent/GOALS.md 품질 기준
// ("못 믿는 값으로 점수를 매기지 않는다").
function hasWarnings(data: SiteScoreResponse): boolean {
  return Array.isArray(data.warnings) && data.warnings.length > 0;
}

// Ported from `metricCell` in frontend/js/candidate_compare.js.
// subscore.display는 값이 없을 때 백엔드가 리터럴 "—"를 채워 넣는다(site_score.py:
// `comp_intensity or "—"`, `score_qualitative_label(t_score) if ... else "—"` 등).
// `if (ss.display)`만 보면 "—"가 truthy라서 rank_top_pct/score가 있어도 무시하고
// 항상 "—"를 반환했다.
function metricCell(data: SiteScoreResponse, key: string): string {
  const ss = data.subscores?.find((s) => s.key === key);
  if (!ss) return "—";
  if (ss.display && ss.display !== "—") return ss.display;
  if (ss.rank_top_pct != null) return `상위 ${ss.rank_top_pct}%`;
  if (ss.score != null) return String(ss.score);
  return "—";
}

// metricCell이 "—"로 떨어질 때, 왜 값이 없는지 설명하는 detail(진짜 이유)을
// 표 셀에는 공간이 없어 title 툴팁으로 붙인다. detail 자체를 감춰버리면
// SiteScorePanel.tsx와 같은 문제(진짜 이유가 어디에도 안 보임)가 재현된다.
function metricCellTitle(data: SiteScoreResponse, key: string): string | undefined {
  const ss = data.subscores?.find((s) => s.key === key);
  if (!ss) return undefined;
  const hasRealValue = (ss.display && ss.display !== "—") || ss.rank_top_pct != null || ss.score != null;
  return hasRealValue ? undefined : ss.detail || undefined;
}

// Ported from `topMetricsLine`.
function topMetricsLine(data: SiteScoreResponse): string {
  const ranked = (data.subscores || [])
    .filter((s): s is SubScore & { rank_top_pct: number } => s.rank_top_pct != null)
    .sort((a, b) => a.rank_top_pct - b.rank_top_pct)
    .slice(0, 2);
  if (!ranked.length) return data.verdict_one_liner || "";
  return ranked.map((s) => `${s.summary_label || s.label} 상위 ${s.rank_top_pct}%`).join(" · ");
}

// Ported from `badgeForScore`.
function BadgeForScore({ score, isBest }: { score: number; isBest: boolean }) {
  if (isBest) return <span className="compare-badge compare-badge--best">추천</span>;
  if (score >= 65) return <span className="compare-badge compare-badge--good">양호</span>;
  if (score >= 50) return <span className="compare-badge compare-badge--ok">검토</span>;
  return <span className="compare-badge compare-badge--caution">주의</span>;
}

// Ported from `shortAddressName`.
function shortAddressName(address: string): string {
  const parts = address.split(/\s+/);
  if (parts.length >= 2) return parts.slice(-2).join(" ");
  return address.slice(0, 24);
}

function upjongLabel(upjong: UpjongSelection | null): string {
  return upjong?.breadcrumb || upjong?.label || "선택 업종";
}


// Ported from `candidate_compare.js`'s watchlist selection + compare flow
// (`toggleWatchlistSelection`, `buildComparePayload`, `runCompare`,
// `renderCompareResults`). Candidate map markers (`syncCandidateMarkers`) and
// bounds-focusing (`focusCandidateBounds`) are implemented via the
// `selectedIds`/`onFocusCandidates` props — the actual marker rendering
// happens in AppShell.tsx's shared KakaoMap instance (see the marker
// `useMemo`s there), since this component doesn't own a map.
export function CandidateCompare({
  watchlist,
  onWatchlistChange,
  radius,
  upjong,
  selectedIds,
  onSelectedIdsChange,
  onFocusCandidates,
}: CandidateCompareProps) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [compareResults, setCompareResults] = useState<Map<string, SiteScoreResponse>>(new Map());
  const [status, setStatus] = useState<{ message: string; tone: "loading" | "success" | "error" } | null>(null);
  const [running, setRunning] = useState(false);
  const runVersionRef = useRef(0);
  const hasResultsRef = useRef(false);
  const inputsKey = compareInputsKey(upjong, radius);
  const inputsKeyRef = useRef(inputsKey);

  const items = getSiteAnalysisWatchlist(watchlist);
  const selCount = selectedIds.length;

  function toggleSelection(id: string, checked: boolean) {
    if (checked) {
      if (selectedIds.length >= MAX_CANDIDATES) {
        setStatus({ message: `비교는 최대 ${MAX_CANDIDATES}곳까지 선택할 수 있습니다.`, tone: "error" });
        return;
      }
      onSelectedIdsChange([...selectedIds, id]);
    } else {
      onSelectedIdsChange(selectedIds.filter((x) => x !== id));
    }
  }

  async function runCompare() {
    if (selectedIds.length < MIN_CANDIDATES) {
      setStatus({ message: `비교하려면 ${MIN_CANDIDATES}곳 이상 선택해 주세요.`, tone: "error" });
      return;
    }
    if (selectedIds.length > MAX_CANDIDATES) {
      setStatus({ message: `최대 ${MAX_CANDIDATES}곳까지 비교할 수 있습니다.`, tone: "error" });
      return;
    }

    const nextCandidates: Candidate[] = selectedIds
      .map((id) => items.find((w) => w.id === id))
      .filter((w): w is WatchlistItem => Boolean(w))
      .map((w, i) => ({ id: w.id, label: CANDIDATE_LABELS[i], address: w.address, lat: w.lat, lng: w.lng }));
    setCandidates(nextCandidates);

    const version = ++runVersionRef.current;
    setRunning(true);
    const results = new Map<string, SiteScoreResponse>();
    setCompareResults(new Map());
    setStatus({ message: "후보지 분석 준비 중…", tone: "loading" });

    try {
      for (let i = 0; i < nextCandidates.length; i++) {
        const c = nextCandidates[i];
        setStatus({ message: `후보 ${c.label} 분석 중… (${i + 1}/${nextCandidates.length})`, tone: "loading" });
        try {
          const data = await api.siteScore({
            lat: c.lat,
            lng: c.lng,
            radius_m: radius,
            inds_lcls_cd: upjong?.inds_lcls_cd || null,
            inds_mcls_cd: upjong?.inds_mcls_cd || null,
            inds_scls_cd: upjong?.inds_scls_cd || null,
            business_type: upjong?.business_type || "general",
          });
          if (version !== runVersionRef.current) return;
          results.set(c.id, data);
        } catch (err) {
          const detail = err instanceof Error ? err.message : String(err);
          throw new Error(`후보 ${c.label}: ${detail}`);
        }
      }
      if (version !== runVersionRef.current) return;
      setCompareResults(new Map(results));
      hasResultsRef.current = results.size >= MIN_CANDIDATES;
      setStatus({ message: `${nextCandidates.length}곳 비교 완료`, tone: "success" });
    } catch (err) {
      if (version !== runVersionRef.current) return;
      setCompareResults(new Map(results));
      hasResultsRef.current = results.size >= MIN_CANDIDATES;
      setStatus({ message: err instanceof Error ? err.message : String(err), tone: "error" });
    } finally {
      if (version === runVersionRef.current) setRunning(false);
    }
  }

  function handleClear() {
    runVersionRef.current += 1;
    setCandidates([]);
    setCompareResults(new Map());
    hasResultsRef.current = false;
    onSelectedIdsChange([]);
    onWatchlistChange(clearSiteAnalysisWatchlist(watchlist));
    setStatus(null);
  }

  function handleRemove(id: string) {
    onWatchlistChange(removeFromWatchlist(watchlist, id));
    onSelectedIdsChange(selectedIds.filter((x) => x !== id));
    setCompareResults((cur) => {
      if (!cur.has(id)) return cur;
      const next = new Map(cur);
      next.delete(id);
      hasResultsRef.current = next.size >= MIN_CANDIDATES;
      return next;
    });
    setCandidates((cur) => cur.filter((c) => c.id !== id));
  }

  // Ported from `document.addEventListener("DOMContentLoaded", ... onUpjongChange(...))`,
  // 다만 업종만이 아니라 비교 조건 전체(업종·반경)를 감시한다 —
  // compareInputsKey 주석 참고.
  useEffect(() => {
    if (inputsKeyRef.current !== inputsKey) {
      inputsKeyRef.current = inputsKey;
      if (hasResultsRef.current) void runCompare();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputsKey]);

  // Prune selection/results when watchlist items are removed elsewhere.
  useEffect(() => {
    const ids = new Set(items.map((w) => w.id));
    const pruned = selectedIds.filter((id) => ids.has(id));
    if (pruned.length !== selectedIds.length) onSelectedIdsChange(pruned);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchlist]);

  const canCompare = selCount >= MIN_CANDIDATES && selCount <= MAX_CANDIDATES;
  const rows = candidates
    .map((c) => {
      const data = compareResults.get(c.id);
      return data ? { candidate: c, data } : null;
    })
    .filter((r): r is { candidate: Candidate; data: SiteScoreResponse } => Boolean(r));
  const showResults = rows.length >= MIN_CANDIDATES;
  // Candidates with a data-source warning are excluded from the "best/추천"
  // comparison — their total_score may rest on fallback/누락 data, so ranking
  // them against clean candidates would overstate confidence.
  const reliableRows = rows.filter((r) => !hasWarnings(r.data));
  const bestScore = reliableRows.length ? Math.max(...reliableRows.map((r) => r.data.total_score)) : 0;
  const bestIds: Set<string> =
    reliableRows.length > 0
      ? new Set(reliableRows.filter((r) => r.data.total_score === bestScore).map((r) => r.candidate.id))
      : new Set();
  // Per-metric winners, so each column can flag the strongest candidate. Only
  // meaningful when candidates actually differ (see leadIdsForMetric).
  const scoreLeads: Set<string> =
    reliableRows.length > 0 && new Set(reliableRows.map((r) => r.data.total_score)).size > 1 ? bestIds : new Set();
  const metricLeads = {
    integration: leadIdsForMetric(rows, "integration"),
    choice: leadIdsForMetric(rows, "choice"),
    demand: leadIdsForMetric(rows, "demand"),
    competition: leadIdsForMetric(rows, "competition"),
  } as const;

  // Ported from `focusCandidateBounds(candidates)`, called at the end of
  // `renderCompareResults` in frontend/js/candidate_compare.js whenever the
  // results table renders (a fresh run, or a recompute after removing a
  // candidate that still leaves >= MIN_CANDIDATES results).
  const rowsKey = rows.map((r) => r.candidate.id).join(",");
  useEffect(() => {
    if (showResults) onFocusCandidates(rows.map((r) => ({ lat: r.candidate.lat, lng: r.candidate.lng })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowsKey, showResults]);

  return (
    <section className="card candidate-compare-section" id="candidate-compare-root">
      <div className="candidate-compare-panel">
        <div className="candidate-compare-header">
          <div>
            <h2>관심 목록 · 후보지 비교</h2>
            <p className="candidate-compare-intro">
              입지 분석에서 저장한 위치 중 {MIN_CANDIDATES}~{MAX_CANDIDATES}곳을 선택해 선택한 업종·반경 기준으로
              입지를 나란히 비교합니다.
            </p>
          </div>
          <span className="candidate-count">
            {selCount}/{MAX_CANDIDATES} 선택 · {items.length}곳 저장
          </span>
        </div>

        {items.length === 0 ? (
          <div className="candidate-empty">
            <p className="candidate-empty-title">아직 저장한 후보지가 없습니다.</p>
            <p className="candidate-empty-hint">
              입지 분석에서 마음에 드는 위치를 저장하면 여기에서 {MIN_CANDIDATES}~{MAX_CANDIDATES}곳을 골라 나란히
              비교할 수 있습니다.
            </p>
          </div>
        ) : (
          <>
          {/* 단계 요약 — 딜이 어디까지 왔는지 한 줄로. 0건 단계는 숨긴다. */}
          <p className="watchlist-stage-summary">
            {DEAL_STAGES
              .map((st) => ({ st, n: stageCounts(items)[st] }))
              .filter(({ n }) => n > 0)
              .map(({ st, n }) => `${DEAL_STAGE_LABELS[st]} ${n}`)
              .join(" · ")}
          </p>
          <ul className="watchlist-list">
            {items.map((w) => {
              const checked = selectedIds.includes(w.id);
              const selIdx = checked ? selectedIds.indexOf(w.id) : -1;
              const atMax = selCount >= MAX_CANDIDATES && !checked;
              return (
                <Fragment key={w.id}>
                <li className="watchlist-item">
                  <label className="watchlist-item-select">
                    <input
                      type="checkbox"
                      className="watchlist-checkbox"
                      checked={checked}
                      disabled={atMax}
                      onChange={(e) => toggleSelection(w.id, e.target.checked)}
                    />
                    {checked && selIdx >= 0 && (
                      <span className={`candidate-badge candidate-badge--${CANDIDATE_LABELS[selIdx].toLowerCase()}`}>
                        {CANDIDATE_LABELS[selIdx]}
                      </span>
                    )}
                    <span className="watchlist-item-address">{w.address}</span>
                  </label>
                  {/* 딜 단계 — 관심목록이 "주소 저장함"이 아니라 "검토 중인 딜
                      목록"이 되게 한다(GROWTHFACTOR_GAP P2). 옛 저장분은 stage가
                      없으므로 stageOf가 검토중으로 읽는다. */}
                  <select
                    className="watchlist-stage-select"
                    value={stageOf(w)}
                    aria-label={`${w.address} 딜 단계`}
                    onChange={(e) => onWatchlistChange(setItemStage(watchlist, w.id, e.target.value as DealStage))}
                  >
                    {DEAL_STAGES.map((st) => (
                      <option key={st} value={st}>{DEAL_STAGE_LABELS[st]}</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="candidate-text-btn candidate-text-btn--remove"
                    onClick={() => handleRemove(w.id)}
                  >
                    삭제
                  </button>
                </li>
                {stageOf(w) !== "reviewing" || w.memo ? (
                  <li className="watchlist-item-memo-row">
                    <input
                      className="watchlist-memo-input"
                      type="text"
                      defaultValue={w.memo ?? ""}
                      placeholder="메모 — 예: 보증금 5천 조정 가능, 5월 중 답 주기로"
                      aria-label={`${w.address} 메모`}
                      onBlur={(e) => {
                        if ((w.memo ?? "") !== e.target.value) {
                          onWatchlistChange(setItemMemo(watchlist, w.id, e.target.value));
                        }
                      }}
                    />
                  </li>
                ) : null}
                </Fragment>
              );
            })}
          </ul>
          </>
        )}

        <div className="candidate-actions">
          <button
            type="button"
            className="candidate-primary-btn"
            disabled={!canCompare || running}
            onClick={() => void runCompare()}
          >
            {MIN_CANDIDATES}~{MAX_CANDIDATES}곳 비교하기
          </button>
          <button type="button" className="candidate-secondary-btn" onClick={handleClear}>
            목록 비우기
          </button>
        </div>

        {items.length > 0 && !canCompare && !status && (
          <p className="candidate-hint">
            비교하려면 후보를 {MIN_CANDIDATES}곳 이상 선택해 주세요. (현재 {selCount}곳)
          </p>
        )}

        {status && (
          <div className={`status status-banner status-banner--${status.tone === "loading" ? "loading" : status.tone}`}>
            {status.message}
          </div>
        )}

        {showResults && (
          <div className="candidate-compare-results">
            <div className="compare-results-header">
              <h3>비교 결과</h3>
              <p className="compare-results-meta">
                {upjongLabel(upjong)} · 반경 {radius}m · {rows.length}곳
              </p>
              <p className="compare-legend">
                <span className="compare-lead-mark" aria-hidden="true">
                  ▲
                </span>
                항목별로 가장 앞서는 후보를 초록으로 표시합니다.
              </p>
              {rows.some(({ data }) => hasWarnings(data)) && (
                <p className="compare-legend warnings-banner">
                  일부 데이터를 확인하지 못한 후보는 점수가 정확하지 않을 수 있어 &quot;추천&quot; 비교에서
                  제외했습니다. 아래 표에서 확인하세요.
                </p>
              )}
            </div>
            <div className="compare-table-wrap">
              <table className="compare-table">
                <thead>
                  <tr>
                    <th>후보</th>
                    <th>주소</th>
                    <th>점수</th>
                    <th>등급</th>
                    <th>접근성</th>
                    <th>통과교통</th>
                    <th>수요</th>
                    <th>교통</th>
                    <th>경쟁</th>
                    <th>한줄 평</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(({ candidate, data }) => {
                    const cid = candidate.id;
                    const isBest = bestIds.has(cid);
                    const transitData = data.transit;
                    const transit =
                      transitData?.station_name && transitData?.distance_m != null
                        ? `${transitData.station_name} ${transitData.distance_m}m`
                        : metricCell(data, "transit");
                    const transitTitle =
                      transitData?.station_name && transitData?.distance_m != null
                        ? undefined
                        : metricCellTitle(data, "transit");
                    const leadCell = (value: string, lead: boolean, title?: string) => (
                      <td className={lead ? "compare-metric compare-metric--lead" : "compare-metric"} title={title}>
                        {value}
                        {lead && (
                          <span className="compare-lead-mark" title="항목 최고" aria-label="항목 최고">
                            ▲
                          </span>
                        )}
                      </td>
                    );
                    return (
                      <tr key={cid} className={isBest ? "compare-row compare-row--best" : "compare-row"}>
                        <td>
                          <span className={`candidate-badge candidate-badge--${candidate.label.toLowerCase()}`}>
                            {candidate.label}
                          </span>
                          <BadgeForScore score={data.total_score} isBest={isBest} />
                        </td>
                        <td className="compare-address">{shortAddressName(candidate.address)}</td>
                        <td className={scoreLeads.has(cid) ? "compare-score compare-score--lead" : "compare-score"}>
                          {Math.round(data.total_score)}
                          {scoreLeads.has(cid) && (
                            <span className="compare-lead-mark" title="최고 점수" aria-label="최고 점수">
                              ▲
                            </span>
                          )}
                        </td>
                        <td>{data.grade}</td>
                        {leadCell(
                          metricCell(data, "integration"),
                          metricLeads.integration.has(cid),
                          metricCellTitle(data, "integration"),
                        )}
                        {leadCell(metricCell(data, "choice"), metricLeads.choice.has(cid), metricCellTitle(data, "choice"))}
                        {leadCell(metricCell(data, "demand"), metricLeads.demand.has(cid), metricCellTitle(data, "demand"))}
                        <td title={transitTitle}>{transit}</td>
                        {leadCell(
                          metricCell(data, "competition"),
                          metricLeads.competition.has(cid),
                          metricCellTitle(data, "competition"),
                        )}
                        <td className="compare-verdict">{data.verdict_one_liner || topMetricsLine(data)}</td>
                      </tr>
                    );
                  })}
                  {rows
                    .filter(({ data }) => hasWarnings(data))
                    .map(({ candidate, data }) => (
                      <tr key={`${candidate.id}-warnings`} className="compare-row compare-row--warnings">
                        <td colSpan={10} className="warnings-banner">
                          {candidate.label}: {(data.warnings || []).join(" · ")}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            <div className="compare-cards">
              {rows.map(({ candidate, data }) => {
                const cid = candidate.id;
                const isBest = bestIds.has(cid);
                const metricItem = (label: string, key: keyof typeof metricLeads) => {
                  const lead = metricLeads[key].has(cid);
                  return (
                    <li
                      className={lead ? "compare-card-metric compare-card-metric--lead" : "compare-card-metric"}
                      title={metricCellTitle(data, key)}
                    >
                      <span className="compare-card-metric-label">{label}</span>
                      <span className="compare-card-metric-value">
                        {metricCell(data, key)}
                        {lead && (
                          <span className="compare-lead-mark" title="항목 최고" aria-label="항목 최고">
                            ▲
                          </span>
                        )}
                      </span>
                    </li>
                  );
                };
                return (
                  <article key={cid} className={`compare-card${isBest ? " compare-card--best" : ""}`}>
                    <header className="compare-card-header">
                      <span className={`candidate-badge candidate-badge--${candidate.label.toLowerCase()}`}>
                        {candidate.label}
                      </span>
                      <span className="compare-card-score">
                        {Math.round(data.total_score)}
                        <small>/100</small>
                      </span>
                      <BadgeForScore score={data.total_score} isBest={isBest} />
                    </header>
                    <p className="compare-card-address">{candidate.address}</p>
                    <p className="compare-card-grade">{data.grade}</p>
                    <ul className="compare-card-metrics">
                      {metricItem("접근성", "integration")}
                      {metricItem("통과교통", "choice")}
                      {metricItem("수요", "demand")}
                      {metricItem("경쟁", "competition")}
                    </ul>
                    <p className="compare-card-verdict">{data.verdict_one_liner || topMetricsLine(data)}</p>
                    {hasWarnings(data) && (
                      <div className="warnings-banner">
                        {(data.warnings || []).map((w, i) => (
                          <p key={i}>{w}</p>
                        ))}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
