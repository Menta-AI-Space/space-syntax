"use client";

import { useState } from "react";
import { BusinessPicker, type UpjongSelection } from "@/components/BusinessPicker";
import { AddressAutocomplete, IntakeShell, LabeledNumberInput, type AgentStepDef } from "@/components/agent/AgentForm";
import { fmtManEok, parseNumber, withObjectParticle } from "@/lib/agentFormat";
import type { DecisionPriority } from "@/lib/api";

// 목적 4개(계약·매입·진단·장소 찾기)는 묻는 것이 조금씩 다르지만 모양은 같다:
// 한 번에 한 가지만 묻고, 끝난 단계는 한 줄 요약으로 접힌다. 그래서 화면을
// 네 벌 만들지 않고 "무엇을 어떤 순서로 물을지"만 설정으로 받는다.

export interface IntakeNumberField {
  key: string;
  label: string;
  unit?: string;
  placeholder?: string;
  required?: boolean;
}

export interface IntakeToggleField {
  key: string;
  label: string;
}

export type IntakeStep =
  | { kind: "upjong"; title: string; required?: boolean; hint?: string }
  | { kind: "location"; title: string; label: string; placeholder: string; hint?: string; /** 동네 이름처럼 넓은 범위도 허용 (목록에서 고르지 않아도 됨). */ allowFreeText?: boolean }
  | { kind: "numbers"; title: string; fields: IntakeNumberField[]; toggles?: IntakeToggleField[]; hint?: string }
  | { kind: "priority"; title: string; hint?: string };

export interface GoalIntakeConfig {
  title: string;
  subtitle: string;
  cta: string;
  steps: IntakeStep[];
}

export interface GoalIntakeValues {
  /** 목록에서 고른 위치의 이름. 좌표 없이 동네 이름만 받을 수도 있다. */
  label: string;
  lat: number | null;
  lng: number | null;
  query: string;
  upjong: UpjongSelection | null;
  numbers: Record<string, number | null>;
  flags: Record<string, boolean>;
  priority: DecisionPriority;
}

const PRIORITY_OPTIONS: { id: DecisionPriority; label: string; hint: string }[] = [
  { id: "revenue", label: "매출이 높은 곳", hint: "다소 위험해도 크게 벌 수 있는 자리" },
  { id: "stability", label: "매출이 낮아도 안정적인 곳", hint: "덜 벌더라도 흔들리지 않는 자리" },
  { id: "balanced", label: "매출과 경쟁이 중간인 곳", hint: "매출도 경쟁도 무난한 자리" },
];

function fieldSummary(field: IntakeNumberField, raw: string): string | null {
  const value = parseNumber(raw ?? "");
  if (value === null) return null;
  if (field.unit === "만원") return `${field.label} ${fmtManEok(value)}`;
  return `${field.label} ${value.toLocaleString()}${field.unit ?? ""}`;
}

export function GoalIntake({
  config, onBack, onSubmit,
}: {
  config: GoalIntakeConfig;
  onBack: () => void;
  onSubmit: (values: GoalIntakeValues) => void;
}) {
  const [step, setStepRaw] = useState(0);
  // 열어 본 적 있는 가장 뒤 단계 — 고르지 않아도 되는 단계는 열어 본 뒤에만 ✓를 붙인다
  // (처음부터 ✓면 이미 답한 것처럼 읽힌다, 2026-11-07 UX 자동 개선).
  const [reached, setReached] = useState(0);
  function setStep(index: number) {
    setStepRaw(index);
    setReached((prev) => Math.max(prev, index));
  }
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<{ label: string; lat: number; lng: number } | null>(null);
  const [upjong, setUpjong] = useState<UpjongSelection | null>(null);
  const [raw, setRaw] = useState<Record<string, string>>({});
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const [priority, setPriority] = useState<DecisionPriority>("balanced");
  const [formError, setFormError] = useState("");

  const locationStep = config.steps.find((s) => s.kind === "location");
  const allowFreeText = locationStep?.kind === "location" ? Boolean(locationStep.allowFreeText) : false;
  const locationDone = allowFreeText ? query.trim().length > 0 : picked !== null;
  const upjongStep = config.steps.find((s) => s.kind === "upjong");
  const upjongRequired = upjongStep?.kind === "upjong" ? upjongStep.required !== false : false;

  function numberValue(key: string): number | null {
    return parseNumber(raw[key] ?? "");
  }

  // 못 채운 필수 항목 하나 — CTA 비활성화와 "어느 단계로 되돌릴지"에 함께 쓴다.
  function findMissing(): { message: string; stepIndex: number } | null {
    for (let index = 0; index < config.steps.length; index += 1) {
      const s = config.steps[index];
      if (s.kind === "location" && !locationDone) {
        return { message: allowFreeText ? "어느 동네인지 먼저 알려주세요." : "목록에서 위치를 골라 주세요.", stepIndex: index };
      }
      if (s.kind === "upjong" && upjongRequired && !upjong) {
        return { message: "업종을 골라 주세요.", stepIndex: index };
      }
      if (s.kind === "numbers") {
        const bad = s.fields.find((f) => f.required && !((numberValue(f.key) ?? 0) > 0));
        if (bad) return { message: `${withObjectParticle(bad.label)} 0보다 큰 숫자로 입력해 주세요.`, stepIndex: index };
      }
    }
    return null;
  }
  const missingRequired = findMissing();

  function submit() {
    if (missingRequired) {
      setFormError(missingRequired.message);
      setStep(missingRequired.stepIndex);
      return;
    }
    setFormError("");
    const numbers: Record<string, number | null> = {};
    for (const s of config.steps) {
      if (s.kind !== "numbers") continue;
      for (const field of s.fields) numbers[field.key] = numberValue(field.key);
    }
    onSubmit({
      label: picked?.label || query.trim(),
      lat: picked?.lat ?? null,
      lng: picked?.lng ?? null,
      query: query.trim(),
      upjong,
      numbers,
      flags,
      priority,
    });
  }

  const lastIndex = config.steps.length - 1;

  // 필수 단계는 채우기 전까지 다음으로 넘어갈 수 없다 (선택 단계는 그냥 넘긴다).
  const nextButton = (index: number, disabled = false) => index < lastIndex
    ? (
      <div className="agent-step-actions">
        <button type="button" className="primary-button primary-button--compact" disabled={disabled} onClick={() => setStep(index + 1)}>다음</button>
      </div>
    )
    : null;

  const steps: AgentStepDef[] = config.steps.map((s, index): AgentStepDef => {
    const next = nextButton(index);

    if (s.kind === "location") {
      return {
        title: s.title,
        summary: picked?.label || query.trim() || "아직 안 골랐어요",
        done: locationDone,
        body: (
          <>
            <label className="candidate-field candidate-field--wide">
              <span>{s.label}</span>
              <AddressAutocomplete
                query={query}
                ariaLabel={s.label}
                placeholder={s.placeholder}
                includeDongs={Boolean(s.allowFreeText)}
                onQueryChange={(text) => { setQuery(text); setPicked(null); }}
                onPick={(item) => { setQuery(item.address); setPicked({ label: item.address, lat: item.lat, lng: item.lng }); }}
                // 동네 이름만 적어도 되는 단계에서 Enter(휴대폰 「다음」)는 다음 단계로 —
                // 예전엔 아무 반응이 없어 「다음」 단추를 따로 찾아 눌러야 했다.
                // 정확한 위치가 필요한 단계(매물·가게 위치)는 후보가 하나뿐이면 Enter로 그것을 골라 다음으로 —
                // 예전엔 Enter가 무반응이라 「목록에서 정확한 위치를 골라 주세요」만 보였다(2026-11-09 UX 자동 개선).
                requirePick={!s.allowFreeText}
                onEnter={(only) => {
                  if (s.allowFreeText ? !query.trim() : !(only || picked)) return;
                  if (index < lastIndex) setStep(index + 1);
                }}
              />
            </label>
            {s.hint && <p className="agent-step-hint">{s.hint}</p>}
            {!picked && query.trim().length > 0 && !s.allowFreeText && (
              <p className="agent-step-hint">목록에서 정확한 위치를 골라 주세요.</p>
            )}
            {nextButton(index, !locationDone)}
          </>
        ),
      };
    }

    if (s.kind === "upjong") {
      return {
        title: s.title,
        summary: upjong ? upjong.label : s.required === false ? "안 골라도 됩니다" : "아직 안 골랐어요",
        done: Boolean(upjong) || (s.required === false && reached >= index),
        body: (
          <>
            <BusinessPicker value={upjong} onChange={setUpjong} onConfirm={index < lastIndex ? () => setStep(index + 1) : undefined} />
            {s.hint && <p className="agent-step-hint">{s.hint}</p>}
            {nextButton(index, s.required !== false && !upjong)}
          </>
        ),
      };
    }

    if (s.kind === "numbers") {
      const filled = s.fields.map((f) => fieldSummary(f, raw[f.key] ?? "")).filter((text): text is string => text !== null);
      const toggled = (s.toggles ?? []).filter((t) => flags[t.key]).map((t) => t.label);
      const required = s.fields.filter((f) => f.required);
      const requiredOk = required.every((f) => (numberValue(f.key) ?? 0) > 0);
      const done = required.length === 0 ? filled.length > 0 : requiredOk;
      // 숫자 칸에서 Enter(휴대폰 「다음」)는 같은 단계의 다음 칸으로, 마지막 칸이면 다음 단계로 —
      // 예전엔 아무 반응이 없어 칸마다 손으로 눌러 옮기고 자판에 가린 「다음」을 찾아야 했다
      // (2026-11-08 UX 자동 개선). 필수 칸이 비었으면 넘어가지 않고 자판만 내린다.
      const enterFrom = (input: HTMLInputElement) => {
        const inputs = Array.from(input.closest(".candidate-row-fields")?.querySelectorAll<HTMLInputElement>("input") ?? []);
        const nextInput = inputs[inputs.indexOf(input) + 1];
        if (nextInput) { nextInput.focus(); return; }
        input.blur();
        if (index < lastIndex && requiredOk) setStep(index + 1);
      };
      return {
        title: s.title,
        summary: [...filled, ...toggled].join(" · ") || "아직 안 적었어요",
        done,
        body: (
          <>
            <div className="candidate-row-fields">
              {s.fields.map((field) => (
                <LabeledNumberInput
                  key={field.key}
                  label={field.label}
                  unit={field.unit}
                  placeholder={field.required ? "필수" : (field.placeholder ?? "선택")}
                  value={raw[field.key] ?? ""}
                  onChange={(value) => setRaw((prev) => ({ ...prev, [field.key]: value }))}
                  onEnter={enterFrom}
                />
              ))}
            </div>
            {(s.toggles ?? []).map((toggle) => (
              <label key={toggle.key} className="radio-label">
                <input
                  type="checkbox"
                  checked={Boolean(flags[toggle.key])}
                  onChange={(e) => setFlags((prev) => ({ ...prev, [toggle.key]: e.target.checked }))}
                /> {toggle.label}
              </label>
            ))}
            {s.hint && <p className="agent-step-hint">{s.hint}</p>}
            {next}
          </>
        ),
      };
    }

    return {
      title: s.title,
      summary: PRIORITY_OPTIONS.find((p) => p.id === priority)?.label ?? "균형",
      done: reached >= index,
      body: (
        <>
          <div className="priority-options">
            {PRIORITY_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                className={`priority-option${priority === option.id ? " priority-option--active" : ""}`}
                aria-pressed={priority === option.id}
                onClick={() => { setPriority(option.id); if (index < lastIndex) setStep(index + 1); }}
              >
                <strong>{option.label}</strong>
                <span>{option.hint}</span>
              </button>
            ))}
          </div>
          {s.hint && <p className="agent-step-hint">{s.hint}</p>}
        </>
      ),
    };
  });

  return (
    <IntakeShell
      onBack={onBack}
      title={config.title}
      subtitle={config.subtitle}
      steps={steps}
      step={step}
      onEdit={setStep}
      error={formError}
      cta={config.cta}
      ctaDisabled={missingRequired !== null}
      hint={missingRequired?.message}
      onSubmit={submit}
    />
  );
}
