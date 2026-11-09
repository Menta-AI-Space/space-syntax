import { test } from "node:test";
import * as assert from "node:assert/strict";

import { marketTimingNote } from "../lib/propertyMarketTiming.ts";

test("as_of가 없으면 기준일을 지어내지 않는다", () => {
  assert.equal(marketTimingNote({ time_adjusted: false, as_of: "" }), null);
});

test("time_adjusted=false면 원자료(시점보정 없음)임을 명시한다", () => {
  const note = marketTimingNote({ time_adjusted: false, as_of: "2026-07-29" });
  assert.match(note!, /시점보정 없음/);
  assert.match(note!, /2026-07-29/);
  assert.doesNotMatch(note!, /시점보정 적용/);
});

test("time_adjusted=true면 보정이 적용됐다고 말한다 — 없는 보정을 지어내지 않는다", () => {
  const note = marketTimingNote({ time_adjusted: true, as_of: "2026-07-29" });
  assert.match(note!, /시점보정 적용/);
  assert.doesNotMatch(note!, /시점보정 없음/);
});

test("as_of 값이 바뀌면 문구도 그 값을 그대로 따라간다", () => {
  const note = marketTimingNote({ time_adjusted: false, as_of: "2025-01-15" });
  assert.match(note!, /2025-01-15/);
});
