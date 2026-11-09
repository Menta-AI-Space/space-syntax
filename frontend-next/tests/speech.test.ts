import { test } from "node:test";
import * as assert from "node:assert/strict";

import { splitForSpeech, toSpeakable } from "../lib/speech.ts";

test("toSpeakable strips markdown and reads units", () => {
  const out = toSpeakable("**결론**\n\n- 반경 500m 안 카페 52곳\n- 면적 33㎡ → 월세 250만원");
  assert.equal(out, "결론\n반경 500미터 안 카페 52곳\n면적 33제곱미터, 월세 250만원");
});

test("toSpeakable reads a table row as one sentence and drops the divider", () => {
  const out = toSpeakable("| 순위 | 동네 |\n|---|---|\n| 1순위 | 공덕역 주변 |");
  assert.equal(out, "순위, 동네.\n1순위, 공덕역 주변.");
});

test("splitForSpeech keeps chunks short for Chrome's long-utterance cutoff", () => {
  const long = "가".repeat(100) + ". " + "나".repeat(100) + ". " + "다".repeat(30) + ".";
  const chunks = splitForSpeech(long, 160);
  assert.ok(chunks.every((c) => c.length <= 160), JSON.stringify(chunks.map((c) => c.length)));
  assert.equal(chunks.join(" ").replace(/\s+/g, ""), long.replace(/\s+/g, ""));
});
