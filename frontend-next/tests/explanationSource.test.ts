import { test } from "node:test";
import * as assert from "node:assert/strict";

import { explanationSourceLabel } from "../lib/explanationSource.ts";

test("source가 llm이면 AI 라벨, rule이면 규칙 라벨 — 같은 값을 두 번 반환하지 않는다", () => {
  const llmLabel = explanationSourceLabel("llm");
  const ruleLabel = explanationSourceLabel("rule");
  assert.ok(llmLabel, "llm 라벨이 비어 있다");
  assert.ok(ruleLabel, "rule 라벨이 비어 있다");
  assert.notEqual(llmLabel, ruleLabel, "llm과 rule이 같은 문구로 나오면 사용자가 출처를 구분할 수 없다");
  assert.match(llmLabel!, /AI/);
  assert.match(ruleLabel!, /규칙/);
});

test("source가 없거나 모르는 값이면 지어내지 않고 아무것도 반환하지 않는다", () => {
  assert.equal(explanationSourceLabel(undefined), null);
  assert.equal(explanationSourceLabel(null), null);
  assert.equal(explanationSourceLabel(""), null);
  assert.equal(explanationSourceLabel("unknown-source"), null);
});
