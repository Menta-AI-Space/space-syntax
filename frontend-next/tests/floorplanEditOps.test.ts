import { test } from "node:test";
import * as assert from "node:assert/strict";

import { applyEditPlan, hasChanges, previewOf, selectionFrom } from "../lib/floorplanEditOps.ts";

const L = (x1: number, y1: number, x2: number, y2: number) => ({ x1, y1, x2, y2 });

test("applyEditPlan drops indices then appends new lines and doors", () => {
  const lines = [L(0, 0, 10, 0), L(10, 0, 10, 10), L(0, 5, 10, 5)];
  const openings = [L(2, 0, 4, 0)];
  const out = applyEditPlan(lines, openings, {
    remove_line_indices: [2],
    add_lines: [L(0, 5, 3, 5), L(7, 5, 10, 5)],
    remove_opening_indices: [0],
    add_openings: [L(10, 4, 10, 6)],
  });
  assert.deepEqual(out.lines, [L(0, 0, 10, 0), L(10, 0, 10, 10), L(0, 5, 3, 5), L(7, 5, 10, 5)]);
  assert.deepEqual(out.openings, [L(10, 4, 10, 6)]);
  // 원본 배열은 건드리지 않는다 — 되돌리기 목록이 원본을 들고 있다.
  assert.equal(lines.length, 3);
  assert.equal(openings.length, 1);
});

test("previewOf lists what disappears and ignores stale indices", () => {
  const lines = [L(0, 0, 1, 0)];
  const prev = previewOf(lines, [], { remove_line_indices: [0, 5], add_lines: [], remove_opening_indices: [], add_openings: [] });
  assert.deepEqual(prev.removed, [L(0, 0, 1, 0)]);
});

test("hasChanges is false for a pure question", () => {
  assert.equal(hasChanges({ remove_line_indices: [], add_lines: [], remove_opening_indices: [], add_openings: [] }), false);
});

test("selectionFrom: small move is a point, a drag is a normalised box", () => {
  assert.deepEqual(selectionFrom({ x: 5, y: 5 }, { x: 6, y: 5 }, 3), { point: { x: 5, y: 5 }, box: null });
  const sel = selectionFrom({ x: 10, y: 20 }, { x: 4, y: 2 }, 3);
  assert.deepEqual(sel.box, { x1: 4, y1: 2, x2: 10, y2: 20 });
  assert.deepEqual(sel.point, { x: 7, y: 11 });
});

import { confirmIntent } from "../lib/floorplanEditOps.ts";

test("confirmIntent: short yes / no, anything with content is a correction", () => {
  for (const t of ["응", "네!", "적용해 줘", "그래", "좋아요", "ㅇㅇ", "그렇게 해 주세요", "응 적용해"]) {
    assert.equal(confirmIntent(t), "yes", t);
  }
  for (const t of ["아니", "아니요.", "취소", "됐어", "그대로 둬"]) {
    assert.equal(confirmIntent(t), "no", t);
  }
  for (const t of ["아니 2미터로", "옆 벽으로 해 줘", "문 하나 더", ""]) {
    assert.equal(confirmIntent(t), null, t);
  }
});

import { pastTense } from "../lib/floorplanEditOps.ts";

test("pastTense turns proposals into done sentences", () => {
  assert.equal(pastTense("7번 매장 근처 가로 벽에 폭 3.0m 문을 냅니다"), "7번 매장 근처 가로 벽에 폭 3.0m 문을 냈습니다");
  assert.equal(pastTense("고른 네모 안의 벽 7개를 지웁니다"), "고른 네모 안의 벽 7개를 지웠습니다");
});
