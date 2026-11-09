/** 관심목록 딜 파이프라인 — 옛 저장분이 깨지지 않는 것이 제1 조건이다. */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  stageOf, setItemStage, setItemMemo, stageCounts, type WatchlistItem,
} from "../lib/watchlist.ts";

const legacy: WatchlistItem = { id: "w-1", address: "연남동", lat: 37.55, lng: 126.92, source: "site-analysis" };

test("옛 저장분(stage 없음)은 검토중으로 읽힌다 — 저장 포맷은 안 건드린다", () => {
  assert.equal(stageOf(legacy), "reviewing");
  // 알 수 없는 값이 들어 있어도 죽지 않고 검토중으로 본다.
  assert.equal(stageOf({ ...legacy, stage: "이상한값" as never }), "reviewing");
});

test("단계 변경은 해당 항목만 바꾸고 갱신 시각을 남긴다", () => {
  const list = [legacy, { ...legacy, id: "w-2" }];
  const next = setItemStage(list, "w-2", "negotiating");
  assert.equal(stageOf(next[0]), "reviewing");
  assert.equal(next[1].stage, "negotiating");
  assert.ok(next[1].updated_at);
});

test("메모는 500자에서 자른다 — localStorage를 한 항목이 다 먹으면 안 된다", () => {
  const next = setItemMemo([legacy], "w-1", "가".repeat(600));
  assert.equal(next[0].memo?.length, 500);
});

test("단계별 개수 집계", () => {
  const list = [
    legacy,
    { ...legacy, id: "w-2", stage: "negotiating" as const },
    { ...legacy, id: "w-3", stage: "contracted" as const },
    { ...legacy, id: "w-4" },
  ];
  assert.deepEqual(stageCounts(list), { reviewing: 2, negotiating: 1, contracted: 1, hold: 0 });
});
