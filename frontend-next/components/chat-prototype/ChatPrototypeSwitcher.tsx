"use client";

// PROTOTYPE — throwaway. 프로덕션 빌드에는 표시하지 않는다.
// next/navigation의 useSearchParams를 쓰지 않는다 — 그걸 쓰면 페이지 전체가
// Suspense 경계로 묶여 프로덕션 셸의 hydration이 멈추는 문제가 있었다.

import { useEffect } from "react";

const VARIANT_LABELS: Record<string, string> = {
  current: "현재 대시보드 (기준선)",
  A: "A — ChatGPT식 사이드바",
  B: "B — Claude식 분할 캔버스",
  C: "C — 플로팅 챗 오버레이",
};

const ORDER = ["current", "A", "B", "C"];

export function ChatPrototypeSwitcher({ current }: { current: string }) {
  const goTo = (variant: string) => {
    const params = new URLSearchParams(window.location.search);
    if (variant === "current") params.delete("variant");
    else params.set("variant", variant);
    const qs = params.toString();
    window.history.replaceState(null, "", qs ? `${window.location.pathname}?${qs}` : window.location.pathname);
    window.dispatchEvent(new Event("cp-variant-change"));
  };

  const cycle = (dir: 1 | -1) => {
    const idx = ORDER.indexOf(current);
    goTo(ORDER[(idx + dir + ORDER.length) % ORDER.length]);
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      if (e.key === "ArrowLeft") cycle(-1);
      if (e.key === "ArrowRight") cycle(1);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  if (process.env.NODE_ENV === "production") return null;

  return (
    <div className="cp-switcher">
      <button type="button" onClick={() => cycle(-1)} aria-label="이전 변형">
        ←
      </button>
      <span className="cp-switcher__label">{VARIANT_LABELS[current] ?? current}</span>
      <button type="button" onClick={() => cycle(1)} aria-label="다음 변형">
        →
      </button>
    </div>
  );
}
