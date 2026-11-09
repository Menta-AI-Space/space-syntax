"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { searchKoreanAddresses, type AddressSearchItem } from "@/lib/address";
import { withObjectParticle } from "@/lib/agentFormat";

// 결정 에이전트의 모든 목적(비교·계약·매입·진단·장소 찾기)이 함께 쓰는
// 입력 조각들. 목적별 화면은 "무엇을 물을지"만 정하고 스텝퍼·주소 자동완성·
// 숫자 입력 같은 공통 동작은 전부 여기서 한 벌만 유지한다.

/** 주소·상호 자동완성 한 칸. 250ms debounce + 시퀀스 레이스 가드. */
export function AddressAutocomplete({
  query,
  ariaLabel,
  placeholder = "주소나 상호를 입력하세요",
  limit = 6,
  className = "",
  includeDongs = false,
  onQueryChange,
  onPick,
  onEnter,
  requirePick = false,
}: {
  query: string;
  ariaLabel: string;
  placeholder?: string;
  limit?: number;
  className?: string;
  /** 동네를 물을 때 — 행정동(예: 연남동)을 목록 맨 위에 올린다. */
  includeDongs?: boolean;
  /** 사용자가 직접 타이핑할 때 — 호출한 쪽에서 이전에 고른 좌표를 지운다. */
  onQueryChange: (text: string) => void;
  onPick: (item: AddressSearchItem) => void;
  /** Enter(휴대폰 자판 「이동」)를 눌렀을 때. 후보가 하나뿐이면 그것을 먼저 고른다.
   * 넘기지 않으면 Enter는 아무 일도 하지 않는다(예전 동작). */
  onEnter?: (picked?: AddressSearchItem) => void;
  /** 정확한 위치가 꼭 있어야 하는 칸(매물·가게 위치) — 후보가 여럿일 때 Enter는 목록을 그대로 둔다
   *  (닫아 버리면 다시 쳐야 목록이 뜬다). 후보가 하나면 고르고 onEnter(그 후보). */
  requirePick?: boolean;
}) {
  const [results, setResults] = useState<AddressSearchItem[]>([]);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  // 검색이 끝났는데 후보가 0개였던 검색어 — 목록도 안내도 없이 「목록에서 골라 주세요」만
  // 남던 자리 (2026-11-08 UX 자동 개선, 첫 화면 주소 칸 f475d61과 같은 안내).
  const [emptyFor, setEmptyFor] = useState("");
  const seqRef = useRef(0);

  useEffect(() => {
    const q = query.trim();
    const seq = ++seqRef.current;
    if (!dirty || q.length < 2) { setResults([]); return; }
    const timer = window.setTimeout(() => {
      searchKoreanAddresses(q, limit, { includeDongs })
        .then((items) => { if (seq === seqRef.current) { setResults(items); setError(""); setEmptyFor(items.length === 0 ? q : ""); } })
        .catch((err) => { if (seq === seqRef.current) { setResults([]); setError(err instanceof Error ? err.message : "주소 검색 실패"); } });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query, dirty, limit, includeDongs]);

  return (
    <div className={`address-autocomplete-wrap${className ? ` ${className}` : ""}`}>
      <input
        className="text-input"
        value={query}
        aria-label={ariaLabel}
        placeholder={placeholder}
        enterKeyHint={onEnter ? "next" : undefined}
        onChange={(e) => { onQueryChange(e.target.value); setDirty(true); setEmptyFor(""); }}
        onKeyDown={(e) => {
          if (!onEnter || e.key !== "Enter" || e.nativeEvent.isComposing) return;
          e.preventDefault();
          if (requirePick && results.length > 1) return;
          const only = results.length === 1 ? results[0] : undefined;
          if (only) onPick(only);
          setDirty(false);
          setResults([]);
          e.currentTarget.blur();
          onEnter(only);
        }}
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
                onClick={() => { onPick(item); setDirty(false); setResults([]); }}
              >
                {item.source === "dong" ? "동네" : item.source === "place" ? "가게" : "주소"} · {item.address}
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="status-banner status-banner--error">{error}</p>}
      {!error && emptyFor && emptyFor === query.trim() && (
        <p className="candidate-hint" role="status">
          {withObjectParticle(`「${emptyFor}」`)} 찾지 못했어요 — 역·동 이름이나 도로명 주소(예: 망원로 45)로 다시 적어 보세요.
        </p>
      )}
    </div>
  );
}

/** 숫자 한 칸 (만원·평·층 등). 단위는 라벨에만 붙이고 값은 숫자만 받는다. */
export function LabeledNumberInput({
  label, unit, value, placeholder = "선택", onChange, wide = false, onEnter,
}: {
  label: string;
  unit?: string;
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
  wide?: boolean;
  /** Enter(휴대폰 자판 「다음」)를 눌렀을 때. 주면 자판에 「다음」이 뜬다. */
  onEnter?: (input: HTMLInputElement) => void;
}) {
  return (
    <label className={`candidate-field${wide ? " candidate-field--wide" : ""}`}>
      {/* 단위 괄호는 한 덩어리로 — 좁은 칸에서 「(만 / 원)」처럼 괄호 안에서 꺾였다 (2026-11-08 UX 자동 개선). */}
      <span>
        {label}
        {unit && " "}
        {unit && <span className="candidate-field__unit">({unit})</span>}
      </span>
      <input
        className="text-input"
        inputMode="numeric"
        value={value}
        placeholder={placeholder}
        enterKeyHint={onEnter ? "next" : undefined}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (!onEnter || e.key !== "Enter" || e.nativeEvent.isComposing) return;
          e.preventDefault();
          onEnter(e.currentTarget);
        }}
      />
    </label>
  );
}

export interface AgentStepDef {
  title: string;
  summary: string;
  done: boolean;
  body: ReactNode;
}

/** 다음 단계로 넘어가면 그 단계의 첫 입력칸에 초점을 둔다 — 예전엔 업종을 고른 뒤 초점이 사라져
 *  휴대폰 자판이 내려가고, 「어느 동네」 칸을 다시 눌러야 했다(2026-11-08 UX 자동 개선).
 *  처음 열 때는 두지 않는다(화면을 열자마자 자판이 덮지 않게). 입력칸 없는 단계(우선순위)는 그대로.
 *  스텝퍼 <ol>에 돌려준 ref를 단다 — AgentStepper·CandidateIntake 공통. */
export function useFocusOpenStep(step: number) {
  const listRef = useRef<HTMLOListElement>(null);
  const firstStep = useRef(true);
  useEffect(() => {
    if (firstStep.current) { firstStep.current = false; return; }
    const input = listRef.current?.querySelector<HTMLInputElement>(".agent-step--open .agent-step-body input:not([type=hidden]):not([disabled])");
    input?.focus({ preventScroll: true });
  }, [step]);
  return listRef;
}

/** 한 번에 한 단계만 열리는 스텝퍼. 끝난 단계는 요약으로 접히고 「수정」으로 다시 연다.
 *  아직 안 채운 단계(「아직 안 골랐어요」)에 「수정」은 고칠 게 없어 어색했다 — 「입력」으로 (2026-11-08 UX 자동 개선). */
export function AgentStepper({ steps, step, onEdit }: { steps: AgentStepDef[]; step: number; onEdit: (index: number) => void }) {
  const listRef = useFocusOpenStep(step);
  return (
    <ol className="agent-stepper" ref={listRef}>
      {steps.map((item, index) => {
        const open = step === index;
        return (
          <li key={item.title} className={`agent-step${open ? " agent-step--open" : ""}${item.done ? " agent-step--done" : ""}`}>
            <div className="agent-step-head">
              <span className="agent-step-index" aria-hidden="true">{item.done && !open ? "✓" : index + 1}</span>
              <span className="agent-step-title">{item.title}</span>
              {!open && (
                <>
                  <span className="agent-step-summary">{item.summary}</span>
                  <button type="button" className="agent-step-edit" onClick={() => onEdit(index)}>{item.done ? "수정" : "입력"}</button>
                </>
              )}
            </div>
            {open && <div className="agent-step-body">{item.body}</div>}
          </li>
        );
      })}
    </ol>
  );
}

/** 입력 화면의 겉껍데기 — 뒤로가기 · 제목 · 스텝퍼 · 오류 · 실행 버튼. */
export function IntakeShell({
  onBack, title, subtitle, steps, step, onEdit, error, cta, ctaDisabled, hint, onSubmit,
}: {
  onBack: () => void;
  title: string;
  subtitle: string;
  steps: AgentStepDef[];
  step: number;
  onEdit: (index: number) => void;
  error: string;
  cta: string;
  ctaDisabled: boolean;
  hint?: string;
  onSubmit: () => void;
}) {
  return (
    <div className="agent-stage">
      <div className="card agent-card">
        <button type="button" className="agent-back" onClick={onBack}>← 목적 다시 고르기</button>
        <h2 className="agent-title">{title}</h2>
        <p className="agent-subtitle">{subtitle}</p>

        <AgentStepper steps={steps} step={step} onEdit={onEdit} />

        {error && <p className="status-banner status-banner--error">{error}</p>}
        <button type="button" className="primary-button agent-submit" disabled={ctaDisabled} onClick={onSubmit}>{cta}</button>
        {hint && <p className="agent-step-hint">{hint}</p>}
      </div>
    </div>
  );
}
