import { test } from "node:test";
import * as assert from "node:assert/strict";

import {
  groupChatSessions,
  loadChatSessions,
  removeChatSession,
  upsertChatSession,
  type ChatSession,
} from "../lib/chatSessions.ts";

const DAY = 86_400_000;

function session(id: string, title: string, updatedAt: number): ChatSession {
  return { id, title, updatedAt, turns: [{ query: title, verdict: "78점 · B" }], history: [] };
}

// 2026-08-23 12:00 (로컬) — 묶음 경계를 시각에 상관없이 재게 하려고 고정값을 쓴다.
const NOW = new Date(2026, 7, 23, 12, 0, 0).getTime();
const TODAY = new Date(2026, 7, 23, 0, 0, 0).getTime();

test("groupChatSessions는 오늘·어제·지난 7일·이전으로 나눈다", () => {
  const groups = groupChatSessions(
    [
      session("a", "오늘 것", TODAY + 3600_000),
      session("b", "어제 것", TODAY - 2 * 3600_000),
      session("c", "닷새 전 것", TODAY - 5 * DAY),
      session("d", "한 달 전 것", TODAY - 30 * DAY),
    ],
    NOW,
  );
  assert.deepEqual(
    groups.map((g) => [g.label, g.items.map((s) => s.id)]),
    [
      ["오늘", ["a"]],
      ["어제", ["b"]],
      ["지난 7일", ["c"]],
      ["이전", ["d"]],
    ],
  );
});

test("groupChatSessions는 빈 묶음을 내보내지 않고, 각 묶음을 최신순으로 준다", () => {
  const groups = groupChatSessions(
    [session("old", "오래된 오늘 것", TODAY + 60_000), session("new", "방금 것", TODAY + 7200_000)],
    NOW,
  );
  assert.deepEqual(groups.length, 1);
  assert.deepEqual(groups[0].label, "오늘");
  assert.deepEqual(
    groups[0].items.map((s) => s.id),
    ["new", "old"],
  );
});

test("upsertChatSession은 같은 id를 갈아끼우고 최신순 맨 앞에 둔다", () => {
  const first = upsertChatSession([], session("a", "첫 질문", NOW - 1000));
  const second = upsertChatSession(first, session("b", "다른 대화", NOW - 500));
  const updated = upsertChatSession(second, {
    ...session("a", "첫 질문", NOW),
    turns: [
      { query: "첫 질문", verdict: "78점 · B" },
      { query: "이어 물은 것", verdict: "계약 신중" },
    ],
  });

  assert.deepEqual(
    updated.map((s) => s.id),
    ["a", "b"],
    "갱신된 대화가 목록 맨 앞으로 온다",
  );
  assert.deepEqual(updated.filter((s) => s.id === "a").length, 1, "같은 id가 둘로 늘지 않는다");
  assert.deepEqual(updated[0].turns.length, 2);
});

test("removeChatSession은 그 대화만 지운다", () => {
  const list = upsertChatSession(upsertChatSession([], session("a", "A", NOW - 10)), session("b", "B", NOW));
  assert.deepEqual(
    removeChatSession(list, "a").map((s) => s.id),
    ["b"],
  );
});

test("loadChatSessions는 localStorage가 없는 환경(SSR)에서 빈 목록을 준다", () => {
  // 서버 렌더 중에는 window가 없다 — 여기서 던지면 화면 전체가 죽는다.
  assert.deepEqual(loadChatSessions(), []);
});
