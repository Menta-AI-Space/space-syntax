"use client";

import { useEffect, useRef, useState } from "react";
import { BusinessPicker, type UpjongSelection } from "@/components/BusinessPicker";
import { searchKoreanAddresses, type AddressSearchItem } from "@/lib/address";
import type { DecideRequest, DecisionPriority } from "@/lib/api";
import { useFocusOpenStep } from "@/components/agent/AgentForm";
import { parseNumber, withObjectParticle } from "@/lib/agentFormat";

interface CandidateDraft {
  key: string;
  query: string;
  address: string;
  lat: number | null;
  lng: number | null;
  rent: string;
  pyeong: string;
  floor: string;
}

const MIN_CANDIDATES = 2;
const MAX_CANDIDATES = 5;

const PRIORITY_OPTIONS: { id: DecisionPriority; label: string; hint: string }[] = [
  { id: "revenue", label: "매출이 높은 곳", hint: "다소 위험해도 크게 벌 수 있는 자리" },
  { id: "stability", label: "안정적인 곳", hint: "덜 벌더라도 흔들리지 않는 자리" },
  { id: "balanced", label: "균형", hint: "둘 사이에서 무난한 자리" },
];

let draftSeq = 0;
function emptyDraft(): CandidateDraft {
  draftSeq += 1;
  return { key: `cand-${draftSeq}`, query: "", address: "", lat: null, lng: null, rent: "", pyeong: "", floor: "" };
}

// 숫자 칸에서 Enter(휴대폰 「다음」)는 같은 후보의 다음 칸으로, 층 칸이면 다음 후보지의 빈 주소
// 칸으로, 더 없으면 자판을 내린다 — 예전엔 아무 반응이 없어 칸마다 손으로 옮겨야 했다
// (2026-11-08 UX 자동 개선, GoalIntake 61eb181과 같은 동작).
function enterNext(e: React.KeyboardEvent<HTMLInputElement>) {
  if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
  e.preventDefault();
  const input = e.currentTarget;
  const inputs = Array.from(input.closest(".candidate-row-fields")?.querySelectorAll<HTMLInputElement>("input") ?? []);
  const nextInput = inputs[inputs.indexOf(input) + 1];
  if (nextInput) { nextInput.focus(); return; }
  const nextAddress = input.closest(".candidate-row")?.nextElementSibling
    ?.querySelector<HTMLInputElement>(".candidate-row-search input");
  if (nextAddress && !nextAddress.value.trim()) { nextAddress.focus(); return; }
  input.blur();
}

// 후보지 한 줄 — 주소 자동완성은 AppShell의 「분석 지역」 입력과 같은 방식
// (searchKoreanAddresses + 시퀀스 레이스 가드)을 쓴다.
function CandidateRow({
  draft, index, canRemove, onChange, onRemove,
}: {
  draft: CandidateDraft;
  index: number;
  canRemove: boolean;
  onChange: (patch: Partial<CandidateDraft>) => void;
  onRemove: () => void;
}) {
  const [results, setResults] = useState<AddressSearchItem[]>([]);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  // 검색이 끝났는데 후보가 0개였던 검색어 — 예전엔 목록도 안내도 없이 「목록에서 정확한 위치를
  // 골라 주세요」만 남았다 (2026-11-08 UX 자동 개선, 단계별 입력 주소 칸 d4b268a와 같은 안내).
  const [emptyFor, setEmptyFor] = useState("");
  const seqRef = useRef(0);

  useEffect(() => {
    const q = draft.query.trim();
    const seq = ++seqRef.current;
    if (!dirty || q.length < 2) { setResults([]); return; }
    const timer = window.setTimeout(() => {
      searchKoreanAddresses(q, 6)
        .then((items) => { if (seq === seqRef.current) { setResults(items); setError(""); setEmptyFor(items.length === 0 ? q : ""); } })
        .catch((err) => { if (seq === seqRef.current) { setResults([]); setError(err instanceof Error ? err.message : "주소 검색 실패"); } });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [draft.query, dirty]);

  const picked = draft.lat !== null && draft.lng !== null;
  const notFound = !picked && !error && emptyFor !== "" && emptyFor === draft.query.trim();

  function pick(item: AddressSearchItem) {
    onChange({ query: item.address, address: item.address, lat: item.lat, lng: item.lng });
    setDirty(false);
    setResults([]);
  }

  // Enter(휴대폰 「다음」): 후보가 하나뿐이면 그것을 고르고 같은 줄의 월세 칸으로 넘어간다.
  // 여럿이면 목록에서 고르게 둔다 — 예전엔 Enter가 아무 반응이 없었다 (2026-11-08 UX 자동 개선).
  function onAddressKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
    e.preventDefault();
    if (results.length !== 1) return;
    pick(results[0]);
    e.currentTarget.closest(".candidate-row")?.querySelector<HTMLInputElement>(".candidate-row-fields input")?.focus();
  }

  return (
    <li className="candidate-row">
      <div className="candidate-row-head">
        <span className="candidate-row-badge">{index + 1}</span>
        <div className="address-autocomplete-wrap candidate-row-search">
          <input
            className="text-input"
            value={draft.query}
            aria-label={`${index + 1}번째 후보지 주소`}
            placeholder="주소나 상호를 입력하세요"
            enterKeyHint="next"
            onKeyDown={onAddressKeyDown}
            onChange={(e) => { onChange({ query: e.target.value, address: "", lat: null, lng: null }); setDirty(true); setEmptyFor(""); }}
            onBlur={() => window.setTimeout(() => setDirty(false), 150)}
          />
          {dirty && results.length > 0 && (
            <ul className="address-autocomplete-results">
              {results.map((item) => (
                <li key={`${item.lat}-${item.lng}-${item.address}`}>
                  <button
                    type="button"
                    className="address-autocomplete-item"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => pick(item)}
                  >
                    {item.source === "place" ? "가게" : "주소"} · {item.address}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {canRemove && (
          <button type="button" className="candidate-row-remove" aria-label={`${index + 1}번째 후보지 삭제`} onClick={onRemove}>✕</button>
        )}
      </div>

      {error && <p className="status-banner status-banner--error">{error}</p>}
      {notFound ? (
        <p className="candidate-row-hint" role="status">
          {withObjectParticle(`「${emptyFor}」`)} 찾지 못했어요 — 역·동 이름이나 도로명 주소(예: 망원로 45)로 다시 적어 보세요.
        </p>
      ) : !picked && draft.query.trim().length > 0 && !error && (
        <p className="candidate-row-hint">목록에서 정확한 위치를 골라 주세요.</p>
      )}

      <div className="candidate-row-fields">
        <label className="candidate-field">
          <span>월세 (만원)</span>
          <input className="text-input" inputMode="numeric" value={draft.rent} placeholder="선택" enterKeyHint="next" onKeyDown={enterNext} onChange={(e) => onChange({ rent: e.target.value })} />
        </label>
        <label className="candidate-field">
          <span>평수</span>
          <input className="text-input" inputMode="numeric" value={draft.pyeong} placeholder="선택" enterKeyHint="next" onKeyDown={enterNext} onChange={(e) => onChange({ pyeong: e.target.value })} />
        </label>
        <label className="candidate-field">
          <span>층</span>
          <input className="text-input" inputMode="numeric" value={draft.floor} placeholder="선택" enterKeyHint="next" onKeyDown={enterNext} onChange={(e) => onChange({ floor: e.target.value })} />
        </label>
      </div>
    </li>
  );
}

export function CandidateIntake({ onBack, onSubmit }: { onBack: () => void; onSubmit: (body: DecideRequest) => void }) {
  // 한 번에 하나씩 물어본다 — 완료된 단계는 요약으로 접히고, 「수정」으로 다시 연다.
  const [step, setStepRaw] = useState(0);
  // 열어 본 적 있는 가장 뒤 단계. 고르지 않아도 되는 단계(월세 상한)는 한 번 열어 본 뒤에만
  // ✓를 붙인다 — 처음부터 ✓·「상한 없음」이면 이미 답한 것처럼 읽혔다(2026-11-07 UX 자동 개선).
  const [reached, setReached] = useState(0);
  function setStep(index: number) {
    setStepRaw(index);
    setReached((prev) => Math.max(prev, index));
  }
  const stepperRef = useFocusOpenStep(step);
  const [upjong, setUpjong] = useState<UpjongSelection | null>(null);
  const [drafts, setDrafts] = useState<CandidateDraft[]>(() => [emptyDraft(), emptyDraft()]);
  const [priority, setPriority] = useState<DecisionPriority | null>(null);
  const [maxRent, setMaxRent] = useState("");
  const [formError, setFormError] = useState("");

  const readyCandidates = drafts.filter((d) => d.lat !== null && d.lng !== null);
  const candidatesValid = readyCandidates.length >= MIN_CANDIDATES;

  function patchDraft(key: string, patch: Partial<CandidateDraft>) {
    setDrafts((prev) => prev.map((d) => (d.key === key ? { ...d, ...patch } : d)));
  }

  function submit() {
    if (!candidatesValid) {
      setFormError(`후보지를 ${MIN_CANDIDATES}곳 이상 선택해 주세요.`);
      setStep(1);
      return;
    }
    setFormError("");
    onSubmit({
      goal: "compare_candidates",
      inds_lcls_cd: upjong?.inds_lcls_cd ?? null,
      inds_mcls_cd: upjong?.inds_mcls_cd ?? null,
      inds_scls_cd: upjong?.inds_scls_cd ?? null,
      radius_m: 500,
      priority: priority ?? "balanced",
      brand: null,
      candidates: readyCandidates.slice(0, MAX_CANDIDATES).map((d, index) => ({
        id: d.key,
        label: d.address || d.query.trim() || `후보 ${index + 1}`,
        lat: d.lat as number,
        lng: d.lng as number,
        monthly_rent_man: parseNumber(d.rent),
        deposit_man: null,
        area_pyeong: parseNumber(d.pyeong),
        floor: parseNumber(d.floor),
      })),
      constraints: parseNumber(maxRent) !== null ? { max_rent_man: parseNumber(maxRent) } : null,
    });
  }

  const steps: { title: string; summary: string; done: boolean; body: React.ReactNode }[] = [
    {
      title: "어떤 업종인가요?",
      summary: upjong ? upjong.label : "아직 안 골랐어요",
      done: Boolean(upjong),
      body: (
        <>
          <BusinessPicker value={upjong} onChange={setUpjong} onConfirm={() => setStep(1)} />
          <div className="agent-step-actions">
            <button type="button" className="primary-button primary-button--compact" disabled={!upjong} onClick={() => setStep(1)}>다음</button>
          </div>
        </>
      ),
    },
    {
      title: "후보지를 알려주세요",
      summary: candidatesValid ? `${readyCandidates.length}곳` : "2~5곳이 필요해요",
      done: candidatesValid,
      body: (
        <>
          <ul className="candidate-list">
            {drafts.map((draft, index) => (
              <CandidateRow
                key={draft.key}
                draft={draft}
                index={index}
                canRemove={drafts.length > MIN_CANDIDATES}
                onChange={(patch) => patchDraft(draft.key, patch)}
                onRemove={() => setDrafts((prev) => prev.filter((d) => d.key !== draft.key))}
              />
            ))}
          </ul>
          {drafts.length < MAX_CANDIDATES && (
            <button type="button" className="sim-btn" onClick={() => setDrafts((prev) => [...prev, emptyDraft()])}>+ 후보지 추가</button>
          )}
          <div className="agent-step-actions">
            <button type="button" className="primary-button primary-button--compact" disabled={!candidatesValid} onClick={() => { setFormError(""); setStep(2); }}>다음</button>
          </div>
        </>
      ),
    },
    {
      title: "무엇이 더 중요한가요?",
      // 제출은 우선순위를 요구하지 않는다(아래 버튼은 candidatesValid만 본다).
      // 안 고르면 요청이 "balanced"로 나가므로, 화면도 무엇으로 비교할지 밝힌다 —
      // "아직 안 골랐어요"라고만 적어 두면 아무 기준 없이 비교한 것처럼 읽힌다.
      summary: priority
        ? (PRIORITY_OPTIONS.find((p) => p.id === priority)?.label ?? "")
        : "안 고르면 균형으로 비교합니다",
      done: Boolean(priority),
      body: (
        <div className="priority-options">
          {PRIORITY_OPTIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              className={`priority-option${priority === option.id ? " priority-option--active" : ""}`}
              aria-pressed={priority === option.id}
              onClick={() => { setPriority(option.id); setStep(3); }}
            >
              <strong>{option.label}</strong>
              <span>{option.hint}</span>
            </button>
          ))}
        </div>
      ),
    },
    {
      title: "월세 상한이 있나요?",
      summary: parseNumber(maxRent) !== null
        ? `${parseNumber(maxRent)!.toLocaleString()}만원까지`
        : reached >= 3 ? "상한 없음" : "안 적어도 됩니다",
      done: parseNumber(maxRent) !== null || reached >= 3,
      body: (
        <>
          <label className="candidate-field candidate-field--wide">
            <span>감당 가능한 월세 <span className="candidate-field__unit">(만원, 선택)</span></span>
            <input className="text-input" inputMode="numeric" value={maxRent} placeholder="예: 300" enterKeyHint="done" onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) e.currentTarget.blur(); }} onChange={(e) => setMaxRent(e.target.value)} />
          </label>
          <p className="agent-step-hint">비워 두면 상한 없이 비교합니다.</p>
        </>
      ),
    },
  ];

  return (
    <div className="agent-stage">
      <div className="card agent-card">
        <button type="button" className="agent-back" onClick={onBack}>← 목적 다시 고르기</button>
        <h2 className="agent-title">어디가 나은지 비교해 드릴게요</h2>
        <p className="agent-subtitle">네 가지만 알려주시면 됩니다.</p>

        <ol className="agent-stepper" ref={stepperRef}>
          {steps.map((item, index) => {
            const open = step === index;
            return (
              <li key={item.title} className={`agent-step${open ? " agent-step--open" : ""}${item.done ? " agent-step--done" : ""}`}>
                <div className="agent-step-head">
                  <span className="agent-step-index" aria-hidden="true">{item.done && !open ? "✓" : index + 1}</span>
                  <span className="agent-step-title">{item.title}</span>
                  {/* 안 채운 단계는 「입력」 — AgentStepper와 같은 규칙 (2026-11-08 UX 자동 개선) */}
                  {!open && (
                    <>
                      <span className="agent-step-summary">{item.summary}</span>
                      <button type="button" className="agent-step-edit" onClick={() => setStep(index)}>{item.done ? "수정" : "입력"}</button>
                    </>
                  )}
                </div>
                {open && <div className="agent-step-body">{item.body}</div>}
              </li>
            );
          })}
        </ol>

        {formError && <p className="status-banner status-banner--error">{formError}</p>}
        <button type="button" className="primary-button agent-submit" disabled={!candidatesValid} onClick={submit}>
          어디가 나은지 알려주세요
        </button>
        {!candidatesValid && <p className="agent-step-hint">후보지를 {MIN_CANDIDATES}곳 이상 고르면 분석할 수 있어요.</p>}
      </div>
    </div>
  );
}
