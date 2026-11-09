import { test } from "node:test";
import * as assert from "node:assert/strict";

import { rankLabel, tierOf } from "../lib/streetTier.ts";

test("단계 경계는 상위 2·10·25·50%에서 끊긴다 (경계값은 위 단계)", () => {
  assert.deepEqual(
    [1, 2, 3, 10, 11, 25, 26, 50, 51, 100].map(tierOf),
    [1, 1, 2, 2, 3, 3, 4, 4, 5, 5],
  );
});

test("빨강·주황(1·2단계)은 백엔드 1등급(상위 10%)과 정확히 같다", () => {
  for (let p = 1; p <= 100; p++) assert.equal(tierOf(p) <= 2, p <= 10);
});

test("누른 길 순위 문구 — 아래 절반은 범례처럼 「하위」로 말한다", () => {
  assert.equal(rankLabel(5), "상위 5%");
  assert.equal(rankLabel(50), "상위 50%");
  assert.equal(rankLabel(80), "하위 20%");
  assert.equal(rankLabel(100), "하위 1%");
});
