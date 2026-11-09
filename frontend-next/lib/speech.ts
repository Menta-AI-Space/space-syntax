// 답을 소리로 읽어 주기 (2026-11-07 소유자: 「물어보면 그걸 말로 설명해 줄 수 있는 게 필요하다 —
// 텍스트를 소리로 변환해서 읽어 주는 기능」).
//
// 브라우저에 든 음성 합성(Web Speech API, speechSynthesis)을 쓴다 — 서버·유료 API가 없고 아이폰
// 사파리·안드로이드 크롬·데스크톱 크롬/엣지 모두 한국어 목소리가 있다. 목소리 품질은 기기마다 다르다.
//
// 함정 두 가지:
// - 크롬은 한 번에 긴 글을 넘기면 15초쯤에서 말을 멈춘다 → 문장 단위로 잘라 차례로 넘긴다.
// - 아이폰은 사용자가 누른 순간에만 말을 시작한다 → 버튼 클릭 안에서 speak를 부른다.

export function isSpeechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

/** 화면 글(굵게·목록·표·단위 기호)을 귀로 듣기 좋은 문장으로. */
export function toSpeakable(text: string): string {
  return (
    text
      // 표 구분 줄(|---|---|) 지우고, 표 줄은 칸을 쉼표로 이어 한 문장으로
      .replace(/^[ \t]*\|?[ \t]*:?-{2,}.*$/gm, "")
      .replace(/^[ \t]*\|(.+)\|[ \t]*$/gm, (_m, row: string) => row.split("|").map((c) => c.trim()).filter(Boolean).join(", ") + ".")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/^#{1,6}\s*/gm, "")
      .replace(/^[ \t]*(?:[-*•]|\d+[.)])[ \t]+/gm, "")
      .replace(/^>\s*/gm, "")
      .replace(/https?:\/\/\S+/g, "")
      .replace(/(\d)\s*㎡/g, "$1제곱미터")
      .replace(/(\d)\s*km\b/g, "$1킬로미터")
      .replace(/(\d)\s*m\b/g, "$1미터")
      .replace(/\s*→\s*/g, ", ")
      .replace(/[「」『』《》〈〉]/g, "")
      .replace(/·/g, ", ")
      .replace(/[ \t]+/g, " ")
      .replace(/\n{2,}/g, "\n")
      .trim()
  );
}

/** 문장 단위로 자르되 너무 짧은 조각은 붙여 한 번에 약 160자 이하로. */
export function splitForSpeech(text: string, max = 160): string[] {
  const sentences = text
    .split(/(?<=[.!?。？！])\s+|\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const chunks: string[] = [];
  let cur = "";
  for (const s of sentences) {
    if (s.length > max) {
      if (cur) chunks.push(cur);
      cur = "";
      // 쉼표에서 한 번 더 자른다
      let part = "";
      for (const piece of s.split(/(?<=,)\s*/)) {
        if ((part + " " + piece).trim().length > max && part) {
          chunks.push(part.trim());
          part = piece;
        } else {
          part = (part + " " + piece).trim();
        }
      }
      if (part) chunks.push(part.trim());
      continue;
    }
    if ((cur + " " + s).trim().length > max && cur) {
      chunks.push(cur);
      cur = s;
    } else {
      cur = (cur + " " + s).trim();
    }
  }
  if (cur) chunks.push(cur);
  return chunks;
}

function koreanVoice(): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices().filter((v) => v.lang?.toLowerCase().startsWith("ko"));
  if (!voices.length) return null;
  // 자연스러운 목소리를 먼저 — 구글(크롬)·유나/시리(애플)·마이크로소프트 온라인(엣지).
  const prefer = [/google/i, /yuna|siri|유나/i, /online|natural/i];
  for (const re of prefer) {
    const v = voices.find((x) => re.test(x.name));
    if (v) return v;
  }
  return voices[0];
}

// 지금 읽고 있는 글의 주인(버튼마다 id). 한 번에 하나만 읽는다.
let currentOwner: string | null = null;
const listeners = new Set<() => void>();
function emit() {
  listeners.forEach((fn) => fn());
}

export function subscribeSpeech(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function speakingOwner(): string | null {
  return currentOwner;
}

export function stopSpeaking(): void {
  if (!isSpeechSupported()) return;
  currentOwner = null;
  window.speechSynthesis.cancel();
  emit();
}

/** text를 읽는다. 같은 owner가 이미 읽는 중이면 멈춘다(누르면 켜고 다시 누르면 끈다). */
export function toggleSpeak(owner: string, text: string, rate = 1.05): void {
  if (!isSpeechSupported()) return;
  const synth = window.speechSynthesis;
  if (currentOwner === owner) {
    stopSpeaking();
    return;
  }
  synth.cancel();
  const chunks = splitForSpeech(toSpeakable(text));
  if (!chunks.length) return;
  const voice = koreanVoice();
  currentOwner = owner;
  emit();
  chunks.forEach((chunk, i) => {
    const u = new SpeechSynthesisUtterance(chunk);
    u.lang = voice?.lang || "ko-KR";
    if (voice) u.voice = voice;
    u.rate = rate;
    if (i === chunks.length - 1) {
      u.onend = () => {
        if (currentOwner === owner) {
          currentOwner = null;
          emit();
        }
      };
    }
    u.onerror = () => {
      if (currentOwner === owner) {
        currentOwner = null;
        emit();
      }
    };
    synth.speak(u);
  });
}
