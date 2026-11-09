"use client";

// 「소리로 듣기」 버튼 — 답·해석 글을 브라우저 음성으로 읽는다(lib/speech.ts). 2026-11-07 소유자 요청.
// 음성 합성이 없는 브라우저에서는 그리지 않는다. 한 번에 한 글만 읽고, 다시 누르면 멈춘다.

import { useEffect, useId, useState, useSyncExternalStore } from "react";

import { isSpeechSupported, speakingOwner, stopSpeaking, subscribeSpeech, toggleSpeak } from "@/lib/speech";

export function SpeakButton({ text, className, label = "소리로 듣기" }: { text: string; className?: string; label?: string }) {
  const id = useId();
  // 서버 렌더와 첫 그림을 같게 — 지원 여부는 붙은 뒤에 본다.
  const [supported, setSupported] = useState(false);
  useEffect(() => {
    setSupported(isSpeechSupported());
    // 목소리 목록은 늦게 온다(크롬) — 미리 한 번 불러 둔다.
    if (isSpeechSupported()) window.speechSynthesis.getVoices();
  }, []);
  const owner = useSyncExternalStore(subscribeSpeech, speakingOwner, () => null);
  const speaking = owner === id;

  // 이 버튼이 사라지면(답이 바뀜·탭 이동) 읽던 것도 멈춘다.
  useEffect(() => {
    return () => {
      if (speakingOwner() === id) stopSpeaking();
    };
  }, [id]);
  // 글이 바뀌면(스트리밍 중 다른 답) 멈춘다.
  useEffect(() => {
    if (speakingOwner() === id) stopSpeaking();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  if (!supported || !text.trim()) return null;
  return (
    <button
      type="button"
      className={`speak-button${speaking ? " speak-button--on" : ""}${className ? ` ${className}` : ""}`}
      aria-pressed={speaking}
      onClick={() => toggleSpeak(id, text)}
    >
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M11 5 6 9H2v6h4l5 4V5z" />
        {speaking ? <path d="M16 9v6M20 9v6" /> : <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />}
      </svg>
      {speaking ? "멈추기" : label}
    </button>
  );
}
