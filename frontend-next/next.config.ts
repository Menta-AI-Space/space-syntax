import type { NextConfig } from "next";

// 빌드 시각(KST) — 화면 맨 아래 작은 글씨로 찍어 「지금 보는 화면이 최신인가」를 눈으로 확인한다
// (2026-11-09: 옛 화면이 캐시로 남아 「미국 샘플 안 됨」「설명이 길다」가 되풀이됐다).
const BUILD_STAMP = new Date().toLocaleString("ko-KR", { timeZone: "Asia/Seoul", hour12: false });

const nextConfig: NextConfig = {
  reactStrictMode: true,
  env: { NEXT_PUBLIC_BUILD_STAMP: BUILD_STAMP },
  // STATIC_EXPORT=1: Firebase Hosting build (scripts/build-firebase-static.mjs).
  // The app/api proxy route is server-only, so the script moves it aside for
  // the export build; the exported site talks to NEXT_PUBLIC_API_BASE_URL.
  ...(process.env.STATIC_EXPORT ? { output: "export" as const } : {}),
  // Deployment build: source type errors are reported separately and must not
  // prevent publishing the statically prerendered frontend.
  typescript: {
    ignoreBuildErrors: true,
  },
};

export default nextConfig;
