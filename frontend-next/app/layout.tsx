import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "목좋은곳 — 목 좋은 곳을 찾아 드립니다",
  description: "길과 건물의 구조로 사람 발길이 모이는 자리를 찾습니다 — 지도 분석 · 건축 평면도 분석 · 신도시 계획도면 분석",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <head>
        {/* Pretendard Variable — tokens.css의 --font-primary가 참조하는 실제
            폰트. dynamic-subset이라 필요한 글리프만 로드된다. */}
        <link
          rel="stylesheet"
          crossOrigin="anonymous"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css"
        />
        {/* 질의 화면(Prism AI Chat 킷)이 쓰는 두 폰트 — Instrument Sans는 UI,
            Merriweather는 첫 화면 큰 문장용이다. 둘 다 라틴 전용이라 한글은
            뒤의 Pretendard가 받는다(킷의 폰트 스택 순서 그대로). */}
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        {/* App Router에는 pages/_document.js가 없다 — 이 규칙은 여기 해당하지 않는다. */}
        {/* eslint-disable-next-line @next/next/no-page-custom-font */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=Merriweather:wght@300;400&display=swap"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
