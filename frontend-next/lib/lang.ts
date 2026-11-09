/** 화면 언어 (2026-11-09) — React 없이 쓰는 부분만. 화면 쪽은 lib/i18n.tsx. */
export type Lang = "en" | "ko";

/** 주소의 `lang`을 읽는다. 없거나 모르는 값이면 null(그때는 저장된 값 → 영어). */
export function langFromSearch(search: string): Lang | null {
  const v = new URLSearchParams(search).get("lang");
  return v === "ko" || v === "en" ? v : null;
}

const HANGUL = /[\uac00-\ud7a3\u3131-\u318e]/;

export function hasHangul(text: string | null | undefined): boolean {
  return !!text && HANGUL.test(text);
}

/** 앞뒤 공백은 그대로 두고 가운데만 옮긴다 — JSX 사이 띄어쓰기가 붙어 있다. */
export function splitPad(text: string): [string, string, string] {
  const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
  return m ? [m[1], m[2], m[3]] : ["", text, ""];
}
