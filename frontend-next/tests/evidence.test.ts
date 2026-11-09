import { test } from "node:test";
import * as assert from "node:assert/strict";

import { buildEvidenceTree, formatEvidenceValue, humanizeEvidenceKey, type EvidenceNode } from "../lib/evidence.ts";

function leafValue(nodes: EvidenceNode[], label: string): string | null {
  for (const node of nodes) {
    if (node.kind === "leaf" && node.label === label) return node.value;
    if (node.kind === "branch") {
      const found = leafValue(node.children, label);
      if (found !== null) return found;
    }
  }
  return null;
}

test("formats numbers, booleans and known keys for humans", () => {
  assert.equal(formatEvidenceValue(1234567), "1,234,567");
  assert.equal(formatEvidenceValue(0.12345), "0.12");
  assert.equal(formatEvidenceValue(true), "예");
  assert.equal(formatEvidenceValue(Number.NaN), "—");
  assert.equal(humanizeEvidenceKey("total_score"), "종합 점수");
  assert.equal(humanizeEvidenceKey("some_unknown_key"), "Some unknown key");
});

test("walks nested objects and drops empty values", () => {
  const nodes = buildEvidenceTree({
    total_score: 82,
    empty_string: "",
    nothing: null,
    demand: { population: 24000, workers: null },
  });
  assert.equal(leafValue(nodes, "종합 점수"), "82");
  assert.equal(leafValue(nodes, "생활인구"), "24,000");
  assert.equal(leafValue(nodes, "Empty string"), null);
  assert.equal(leafValue(nodes, "Nothing"), null);
  assert.equal(leafValue(nodes, "Workers"), null);
});

test("joins primitive arrays and expands object arrays", () => {
  const nodes = buildEvidenceTree({
    tags: ["역세권", "대로변"],
    competitors: [{ name: "A카페", distance_m: 120 }, { name: "B카페", distance_m: 240 }],
  });
  assert.equal(leafValue(nodes, "Tags"), "역세권, 대로변");
  assert.equal(leafValue(nodes, "이름"), "A카페");
  const competitors = nodes.find((n) => n.label.startsWith("Competitors"));
  assert.ok(competitors && competitors.kind === "branch");
});

// 백엔드가 어떤 모양을 주더라도 판정문 화면이 죽으면 안 된다.
test("never throws on hostile or unexpected shapes", () => {
  const cyclic: Record<string, unknown> = { name: "loop" };
  cyclic.self = cyclic;
  const exploding = { get boom() { throw new Error("nope"); } };
  const deep = { a: { b: { c: { d: { e: { f: { g: 1 } } } } } } };

  for (const input of [null, undefined, 42, "text", [], {}, cyclic, exploding, deep, new Date(0)]) {
    assert.doesNotThrow(() => buildEvidenceTree(input));
    assert.ok(Array.isArray(buildEvidenceTree(input)));
  }
  assert.deepEqual(buildEvidenceTree(null), []);
  assert.deepEqual(buildEvidenceTree({}), []);
  assert.equal(leafValue(buildEvidenceTree(cyclic), "이름"), "loop");
});
