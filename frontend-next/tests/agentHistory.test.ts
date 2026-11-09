import { test } from "node:test";
import * as assert from "node:assert/strict";

import { AGENT_HISTORY_LIMIT, appendAgentHistory, type AgentHistoryTurn } from "../lib/api.ts";

function turn(query: string): AgentHistoryTurn {
  return { query, address: `${query} 주소`, business_type: "cafe", business_label: "카페" };
}

test("appendAgentHistory keeps turns in order, oldest first", () => {
  const h1 = appendAgentHistory([], turn("신촌역 카페 어때"));
  const h2 = appendAgentHistory(h1, turn("그럼 옆 골목은"));
  assert.deepEqual(
    h2.map((t) => t.query),
    ["신촌역 카페 어때", "그럼 옆 골목은"],
  );
});

test("appendAgentHistory drops the oldest turn once past the limit, not the newest", () => {
  let history: AgentHistoryTurn[] = [];
  for (let i = 0; i < AGENT_HISTORY_LIMIT + 3; i++) {
    history = appendAgentHistory(history, turn(`질문 ${i}`));
  }
  assert.equal(history.length, AGENT_HISTORY_LIMIT);
  // 가장 최근 턴은 항상 남아 있어야 한다 — 대화가 길어질수록 최신 문맥이 가장 중요하다.
  assert.equal(history[history.length - 1].query, `질문 ${AGENT_HISTORY_LIMIT + 2}`);
  // 가장 오래된 턴부터 잘려나간다.
  assert.equal(history[0].query, `질문 3`);
});

test("appendAgentHistory does not mutate the array passed in", () => {
  const original = [turn("첫 질문")];
  const copy = original.slice();
  appendAgentHistory(original, turn("두번째 질문"));
  assert.deepEqual(original, copy);
});
