"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { startAutoTranslate } from "@/lib/autoTranslate";
import { langFromSearch, type Lang } from "@/lib/lang";

export type { Lang };

/**
 * 화면 언어 — 영어가 기본, 한국어는 고를 수 있다 (2026-11-09 소유자 결정).
 *
 * 링크로 정한다: `?lang=ko`면 한국어, `?lang=en`이거나 없으면 영어. 화면 위 단추로 바꾸면
 * 주소의 `lang`도 같이 바뀌어, 그 주소를 그대로 보내면 받은 사람도 같은 언어로 본다.
 * 링크에 `lang`이 없을 때만 이 브라우저에서 마지막으로 고른 언어를 쓴다.
 *
 * 번역은 문자열 사전이 아니라 **그 자리에 두 말을 같이 적는다** — `t("벽 그리기", "Draw wall")`.
 * 화면 코드가 이미 한국어 문장 위주라, 키를 따로 만들면 문장과 번역이 멀어져 빠뜨리기 쉽다.
 * 아직 영어를 못 붙인 곳은 한국어로 남는다(점진 번역).
 */


const STORAGE_KEY = "ui-lang";

function readInitialLang(): Lang {
  if (typeof window === "undefined") return "en";
  try {
    const fromUrl = langFromSearch(window.location.search);
    if (fromUrl) return fromUrl;
  } catch {
    // 주소를 못 읽으면 아래로
  }
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "ko" || stored === "en") return stored;
  } catch {
    // 사생활 보호 창 등 — 기본값으로
  }
  return "en";
}

type LangContextValue = {
  lang: Lang;
  setLang: (lang: Lang) => void;
  /** 지금 언어에 맞는 쪽을 고른다. */
  t: (ko: string, en: string) => string;
};

const LangContext = createContext<LangContextValue>({
  lang: "en",
  setLang: () => undefined,
  t: (_ko, en) => en,
});

export function LangProvider({ children }: { children: ReactNode }) {
  // 서버 렌더와 첫 화면이 어긋나지 않게 처음엔 기본값(영어)으로 그리고, 붙은 뒤 주소를 읽는다.
  const [lang, setLangState] = useState<Lang>("en");

  useEffect(() => {
    setLangState(readInitialLang());
  }, []);

  useEffect(() => {
    if (typeof document !== "undefined") document.documentElement.lang = lang;
    // 영어 모드: 화면에 뜬 한국어를 자동으로 옮긴다(lib/autoTranslate.ts). 손으로 t()를 붙인 곳은 이미 영어라 건드리지 않는다.
    if (lang === "en") startAutoTranslate();
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    // 영어 → 한국어는 페이지를 다시 연다 — 자동 번역이 바꿔 끼운 글을 하나하나 되돌리는 것보다 확실하다.
    const reload = lang === "en" && next === "ko";
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // 저장 못 해도 이번 화면은 바뀐다
    }
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("lang", next);
      window.history.replaceState(window.history.state, "", url.toString());
      if (reload) window.location.reload();
    } catch {
      // 주소를 못 바꿔도 화면은 바뀐다
    }
  }, [lang]);

  const value = useMemo<LangContextValue>(
    () => ({ lang, setLang, t: (ko, en) => (lang === "ko" ? ko : en) }),
    [lang, setLang],
  );
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): LangContextValue {
  return useContext(LangContext);
}

