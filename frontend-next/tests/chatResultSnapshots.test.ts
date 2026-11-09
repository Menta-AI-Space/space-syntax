import { test } from "node:test";
import * as assert from "node:assert/strict";

import {
  loadChatResultSnapshot,
  removeChatResultSnapshot,
  saveChatResultSnapshot,
} from "../lib/chatResultSnapshots.ts";
import type { AgentAskResponse } from "../lib/api.ts";

function result(query: string): AgentAskResponse {
  return {
    query,
    address: "강남역",
    business_type: "convenience",
    business_label: "편의점",
    steps: [],
    answer: "직장인 수요가 강합니다.",
    answer_source: "test",
    site: null,
    tool: "site_score",
    tool_result: { dashboard: { worker_rank_top_pct: 1 } },
  };
}

test("대화별 최신 분석 결과를 저장하고 다시 불러온다", async () => {
  await saveChatResultSnapshot("session-a", result("첫 분석"), 1234);
  const loaded = await loadChatResultSnapshot("session-a");
  assert.equal(loaded?.sessionId, "session-a");
  assert.equal(loaded?.updatedAt, 1234);
  assert.equal(loaded?.result.query, "첫 분석");
  assert.deepEqual(loaded?.result.tool_result, { dashboard: { worker_rank_top_pct: 1 } });
});

test("대화를 삭제하면 저장된 최신 분석도 지운다", async () => {
  await saveChatResultSnapshot("session-delete", result("삭제할 분석"));
  await removeChatResultSnapshot("session-delete");
  assert.equal(await loadChatResultSnapshot("session-delete"), null);
});
