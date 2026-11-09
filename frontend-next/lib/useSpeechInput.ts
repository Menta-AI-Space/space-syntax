"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 브라우저 내장 음성 인식으로 한 문장을 받아온다.
 *
 * **서버를 거치지 않는다.** Whisper 같은 서버측 STT는 요청마다 왕복이 생겨 5초
 * 룰과 부딪치고 호출 비용이 붙는다. 브라우저 API는 공짜이고 즉시라, "말하면
 * 된다"를 보여주는 데는 이쪽이 맞다. 정확도가 부족해지면 그때 서버측으로 갈아탄다.
 *
 * 인식 결과를 **바로 제출하지 않고 입력창에 넣는다.** 음성 인식은 지명을 자주
 * 틀리는데("여의도동"→"여의도 동"), 곧바로 분석을 걸면 엉뚱한 곳을 분석하고
 * 사용자는 왜 그렇게 됐는지 모른다. 고칠 기회를 남긴다.
 *
 * 크롬·엣지 계열만 지원한다(`webkitSpeechRecognition`). 지원하지 않는 브라우저에서는
 * `supported`가 false이므로 호출자가 버튼을 숨긴다 — 눌러도 아무 일이 없는 버튼을
 * 두지 않는다.
 */

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: unknown) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onend: (() => void) | null;
};

type Ctor = new () => SpeechRecognitionLike;

function getConstructor(): Ctor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: Ctor;
    webkitSpeechRecognition?: Ctor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** 인식 결과 이벤트에서 문장만 꺼낸다. 브라우저마다 모양이 조금씩 다르다. */
function transcriptOf(event: unknown): string {
  const results = (event as { results?: ArrayLike<ArrayLike<{ transcript?: string }>> })
    .results;
  if (!results) return "";
  let text = "";
  for (let i = 0; i < results.length; i += 1) {
    text += results[i]?.[0]?.transcript ?? "";
  }
  return text.trim();
}

export type SpeechInput = {
  /** 이 브라우저에서 쓸 수 있는가. false면 버튼을 숨긴다. */
  supported: boolean;
  listening: boolean;
  /** 마이크 권한 거부 등 사용자에게 알려야 하는 상태. */
  error: string;
  start: () => void;
  stop: () => void;
};

export function useSpeechInput(onTranscript: (text: string) => void): SpeechInput {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  // 콜백이 매 렌더 새로 오더라도 인식 세션을 다시 만들지 않게 최신 것만 들고 있는다.
  const handlerRef = useRef(onTranscript);
  handlerRef.current = onTranscript;

  useEffect(() => {
    setSupported(getConstructor() !== null);
    return () => {
      // 컴포넌트가 사라질 때 마이크를 놓아준다 — 안 그러면 탭에 녹음 표시가 남는다.
      recognitionRef.current?.abort();
      recognitionRef.current = null;
    };
  }, []);

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
    setListening(false);
  }, []);

  const start = useCallback(() => {
    const Ctor = getConstructor();
    if (!Ctor) return;
    setError("");
    // 이미 듣고 있으면 새로 시작하지 않는다 — 브라우저가 예외를 던진다.
    if (recognitionRef.current) recognitionRef.current.abort();

    const recognition = new Ctor();
    recognition.lang = "ko-KR";
    // 한 문장만 받는다. continuous는 사용자가 말을 멈춰도 계속 듣고 있어서
    // 언제 끝났는지 알 수 없다.
    recognition.continuous = false;
    recognition.interimResults = false;

    recognition.onresult = (event) => {
      const text = transcriptOf(event);
      if (text) handlerRef.current(text);
    };
    recognition.onerror = (event) => {
      const code = (event as { error?: string }).error ?? "";
      // 말을 안 한 것은 오류로 취급하지 않는다 — 사용자가 버튼을 잘못 눌렀을 뿐이다.
      if (code === "no-speech" || code === "aborted") {
        setListening(false);
        return;
      }
      setError(
        code === "not-allowed" || code === "service-not-allowed"
          ? "마이크 사용을 허용해 주세요."
          : "음성을 인식하지 못했습니다. 다시 시도해 주세요.",
      );
      setListening(false);
    };
    recognition.onend = () => setListening(false);

    recognitionRef.current = recognition;
    try {
      recognition.start();
      setListening(true);
    } catch {
      // 브라우저가 시작을 거부하는 경우(이미 세션이 있거나 권한 문제).
      setError("음성 인식을 시작할 수 없습니다.");
      setListening(false);
    }
  }, []);

  return { supported, listening, error, start, stop };
}
