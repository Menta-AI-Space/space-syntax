import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
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
