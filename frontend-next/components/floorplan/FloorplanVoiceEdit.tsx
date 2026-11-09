"use client";

import { useState } from "react";

import { SpeakButton } from "@/components/SpeakButton";
import { useLang } from "@/lib/i18n";

import type { FloorplanEditIntentResponse } from "@/lib/api";
import { pastTense, QUICK_EDITS } from "@/lib/floorplanEditOps";
import { useSpeechInput } from "@/lib/useSpeechInput";

/**
 * 말로 고치기 입력 칸 (2026-11-09, SPEC_FLOORPLAN_TABS_UI).
 *
 * 도면에서 자리를 고르는 일과 바뀐 자리 그리기는 FloorplanAnalyzer의 캔버스가 맡고, 여기는
 * 「무엇을 할지」 받는 칸과 방금 바꾼 것 안내·되돌리기만 맡는다.
 *
 * **LLM이 먼저 묻는다** (2026-11-09 소유자: 「적용할래?라고 물으면 적용을 해 주라 — LLM이 물으라고」).
 * 제안과 「적용할까요?」가 뜨면 같은 입력칸·마이크로 「응」이라고 답해 적용하고, 「아니」면 취소,
 * 「아니 2미터로」처럼 고쳐 말하면 다시 제안한다(답 가르기는 FloorplanAnalyzer + confirmIntent).
 * 적용 뒤엔 「되돌리기」를 바로 옆에 두고, 입력칸은 그대로 둬서 이어서 시킬 수 있다.
 *
 * 마이크는 1탭과 같은 브라우저 음성 입력이라 크롬 계열에서만 보인다(아이폰 사파리는 숨김).
 * 말이 끝나면 바로 보낸다(1탭은 지명 오인식 때문에 입력칸에 넣고 멈추는 것과 다르다).
 */
export function FloorplanVoiceEdit({
  selectionText,
  busy,
  error,
  reply,
  pending,
  done,
  onSubmit,
  onApply,
  onCancel,
  onUndo,
}: {
  /** 고른 자리를 한 줄로 — 없으면 아직 안 고른 것. */
  selectionText: string | null;
  busy: boolean;
  error: string;
  /** 되묻는 말이나 「들은 말」 같은 안내. */
  reply: string;
  /** LLM이 제안하고 「적용할까요?」라고 물은 것 — 사용자의 답을 기다린다. */
  pending: FloorplanEditIntentResponse | null;
  /** 방금 적용한 것 — 도면이 그 판일 때만 온다(되돌리면 null). */
  done: FloorplanEditIntentResponse | null;
  onSubmit: (text: string) => void;
  onApply: () => void;
  onCancel: () => void;
  onUndo: () => void;
}) {
  const [text, setText] = useState("");
  const speech = useSpeechInput((heard) => {
    if (selectionText) {
      setText("");
      onSubmit(heard);
    } else {
      setText(heard);
    }
  });
  const ready = !!selectionText && !busy;
  // 「고른 자리」가 자동 번역에서 「accessible spot」으로 옮겨졌다(「자리」·「목」 용어집에 끌려) — 고정한다.
  const { t } = useLang();

  function send(value: string) {
    const v = value.trim();
    if (!v || !ready) return;
    // 보낸 말은 칸에서 비운다 — LLM이 물은 뒤엔 「응」만 치면 되도록.
    setText("");
    onSubmit(v);
  }

  return (
    <div className="fp-voice" aria-live="polite">
      <p className="fp-voice__where">
        {selectionText ? (
          <>
            <strong>{t("고른 자리", "Selected spot")}</strong> {selectionText}
          </>
        ) : (
          "먼저 도면에서 고칠 자리를 누르거나, 끌어서 네모로 고르세요."
        )}
      </p>

      {pending && (
        <div className="fp-voice__plan fp-voice__plan--ask">
          <p className="fp-voice__plan-message">{pending.message}</p>
          {pending.steps.length > 1 && (
            <ul className="fp-voice__steps">
              {pending.steps.map((s, i) => (
                <li key={i}>{s.text}</li>
              ))}
            </ul>
          )}
          {!!pending.warnings?.length && (
            <ul className="fp-voice__warnings">
              {pending.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          <p className="fp-voice__legend">
            도면에서 <span className="fp-voice__swatch fp-voice__swatch--add" aria-hidden /> 초록은 새로 생길 것,{" "}
            <span className="fp-voice__swatch fp-voice__swatch--remove" aria-hidden /> 빨간 점선은 없어질 것입니다.
            「응」이라고 말하거나 쓰면 적용하고, 「아니」면 취소합니다. 다르게 말하면 다시 고쳐 드려요.
          </p>
          <div className="fp-voice__actions">
            <button type="button" className="primary-button" onClick={onApply}>
              적용
            </button>
            <button type="button" className="secondary-button" onClick={onCancel}>
              취소
            </button>
            <SpeakButton text={pending.message} />
          </div>
        </div>
      )}

      {done && !pending && (
        <div className="fp-voice__plan">
          <p className="fp-voice__plan-title">적용했습니다</p>
          {done.steps.length > 0 && (
            <ul className="fp-voice__steps">
              {done.steps.map((s, i) => (
                <li key={i}>{pastTense(s.text)}</li>
              ))}
            </ul>
          )}
          {!!done.warnings?.length && (
            <ul className="fp-voice__warnings">
              {done.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          <p className="fp-voice__legend">
            도면에서 <span className="fp-voice__swatch fp-voice__swatch--add" aria-hidden /> 초록은 새로 생긴 것,{" "}
            <span className="fp-voice__swatch fp-voice__swatch--remove" aria-hidden /> 빨간 점선은 없어진 것입니다.
          </p>
          <div className="fp-voice__actions">
            <button type="button" className="secondary-button" onClick={onUndo}>
              되돌리기
            </button>
          </div>
        </div>
      )}

      <form
        className="fp-voice__form"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <input
          className="text-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={pending ? "응 / 아니 / 다르게 말하기" : "예: 여기 문 내줘"}
          aria-label="도면을 어떻게 고칠지"
          maxLength={300}
          disabled={!selectionText}
        />
        {speech.supported && (
          <button
            type="button"
            className={`secondary-button fp-voice__mic${speech.listening ? " fp-voice__mic--on" : ""}`}
            onClick={speech.listening ? speech.stop : speech.start}
            disabled={!ready}
            aria-pressed={speech.listening}
            aria-label={speech.listening ? "음성 입력 멈추기" : "말로 시키기"}
          >
            {speech.listening ? "듣는 중" : "🎤"}
          </button>
        )}
        <button type="submit" className="primary-button" disabled={!ready || !text.trim()}>
          {busy ? "생각 중…" : pending ? "답하기" : "보내기"}
        </button>
      </form>
      <div className="fp-voice__quick" role="group" aria-label="자주 쓰는 지시">
        {QUICK_EDITS.map((q) => (
          <button key={q} type="button" className="fp-voice__chip" disabled={!ready} onClick={() => send(q)}>
            {q}
          </button>
        ))}
      </div>

      {reply && <p className="fp-voice__reply">{reply}</p>}
      {(error || speech.error) && (
        <p className="fp-voice__error" role="alert">
          {error || speech.error}
        </p>
      )}
    </div>
  );
}
