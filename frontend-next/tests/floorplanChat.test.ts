import assert from "node:assert/strict";
import { test } from "node:test";

import type { FloorplanIssue } from "../lib/api.ts";
import { buildChatMessages, cardToIssueIndex, MAX_SENT_TURNS, suggestedQuestions } from "../lib/floorplanChat.ts";

function issue(kind?: string): FloorplanIssue {
  return { severity: "medium", title: "t", detail: "d", x: 0, y: 0, cell_count: 1, evidence: "e", kind };
}

test("buildChatMessages drops screen-only fields and ends with the new question", () => {
  const msgs = buildChatMessages(
    [{ role: "user", content: "a" }, { role: "assistant", content: "b", card: 2, source: "llm" }],
    "  c  ",
  );
  assert.deepEqual(msgs, [
    { role: "user", content: "a" },
    { role: "assistant", content: "b" },
    { role: "user", content: "c" },
  ]);
});

test("buildChatMessages keeps only the recent turns", () => {
  const lines = Array.from({ length: 30 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: String(i) }) as const);
  const msgs = buildChatMessages([...lines], "q");
  assert.equal(msgs.length, MAX_SENT_TURNS);
  assert.equal(msgs[msgs.length - 1].content, "q");
  assert.equal(msgs[msgs.length - 2].content, "29");
});

test("suggestedQuestions only offers what the plan has", () => {
  assert.deepEqual(suggestedQuestions([]), ["이 결과를 얼마나 믿어도 돼요?"]);
  const qs = suggestedQuestions([issue("dead_zone"), issue("best_zone")]);
  assert.equal(qs.length, 4);
  assert.ok(qs.some((q) => q.includes("창고")));
  assert.ok(qs.some((q) => q.includes("잘 모이는")));
  // 좋은 구역 카드만 있으면 「문제」 질문은 내지 않는다.
  assert.ok(!suggestedQuestions([issue("best_zone")]).some((q) => q.includes("문제")));
});

test("cardToIssueIndex guards the range", () => {
  assert.equal(cardToIssueIndex(1, 3), 0);
  assert.equal(cardToIssueIndex(3, 3), 2);
  assert.equal(cardToIssueIndex(4, 3), null);
  assert.equal(cardToIssueIndex(0, 3), null);
  assert.equal(cardToIssueIndex(null, 3), null);
  assert.equal(cardToIssueIndex(1.5, 3), null);
});
