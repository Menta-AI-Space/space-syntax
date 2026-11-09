import { test } from "node:test";
import * as assert from "node:assert/strict";

import { hasHangul, splitPad } from "../lib/lang.ts";

test("hasHangul / splitPad", () => {
  assert.equal(hasHangul("Floor plan"), false);
  assert.equal(hasHangul("벽 72 · 문 8"), true);
  assert.deepEqual(splitPad("  되돌리기 "), ["  ", "되돌리기", " "]);
});
