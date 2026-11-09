/**
 * Firebase Hosting 정적 빌드.
 *
 * 1. 정적 배포에서 제외할 라우트를 잠시 app 밖으로 옮긴다. 빌드가 끝나면
 *    성공·실패와 관계없이 반드시 되돌린다.
 *    - app/api: output:"export"와 호환되지 않는 서버 프록시
 * 2. STATIC_EXPORT=1 next build → out/
 * 3. out/ 을 firebase.json 의 public 폴더(.next/firebase-static)로 복사한다.
 *
 * 사용: node scripts/build-firebase-static.mjs
 * 배포: npx firebase-tools deploy --only hosting
 */

import { cpSync, existsSync, renameSync, rmSync } from "node:fs";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = path.join(root, "out");
const publicDir = path.join(root, ".next", "firebase-static");

const excludedPaths = [
  [path.join(root, "app", "api", "[...path]", "route.ts"), path.join(root, "app_api_route_excluded_during_static_export.ts")],
];
const moved = excludedPaths.filter(([source]) => existsSync(source));
for (const [source, aside] of moved) renameSync(source, aside);

try {
  execSync("npx next build", {
    cwd: root,
    stdio: "inherit",
    env: { ...process.env, STATIC_EXPORT: "1" },
  });
  rmSync(publicDir, { recursive: true, force: true });
  cpSync(outDir, publicDir, { recursive: true });
  console.log(`\nOK: static site copied to ${publicDir}`);
} finally {
  for (const [source, aside] of moved.reverse()) {
    if (existsSync(aside)) renameSync(aside, source);
  }
}
