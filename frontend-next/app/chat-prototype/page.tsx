"use client";

// PROTOTYPE — throwaway route. AI 챗 화면 디자인 시스템 3안 비교용.
// 프로덕션 `/`는 건드리지 않는다. 채택 후 이 디렉터리는 삭제한다.
//
// http://localhost:3000/chat-prototype            → 현재 대시보드 (기준선)
// http://localhost:3000/chat-prototype?variant=A  → ChatGPT식 사이드바
// http://localhost:3000/chat-prototype?variant=B  → Claude식 분할 캔버스
// http://localhost:3000/chat-prototype?variant=C  → 플로팅 챗 오버레이

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ChatPrototypeSwitcher } from "@/components/chat-prototype/ChatPrototypeSwitcher";
import { VariantA } from "@/components/chat-prototype/VariantA";
import { VariantB } from "@/components/chat-prototype/VariantB";
import { VariantC } from "@/components/chat-prototype/VariantC";

function readVariant(): string {
  if (typeof window === "undefined") return "current";
  return new URLSearchParams(window.location.search).get("variant") ?? "current";
}

export default function ChatPrototypePage() {
  const [variant, setVariant] = useState("current");

  useEffect(() => {
    setVariant(readVariant());
    const sync = () => setVariant(readVariant());
    window.addEventListener("popstate", sync);
    window.addEventListener("cp-variant-change", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("cp-variant-change", sync);
    };
  }, []);

  return (
    <>
      {variant === "A" ? <VariantA /> : variant === "B" ? <VariantB /> : variant === "C" ? <VariantC /> : <AppShell />}
      <ChatPrototypeSwitcher current={variant} />
    </>
  );
}
