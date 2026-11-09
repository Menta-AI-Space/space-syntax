import { test } from "node:test";
import * as assert from "node:assert/strict";

import { parseSseBuffer } from "../lib/agentStream.ts";

test("parseSseBuffer reads complete events and keeps the cut-off tail", () => {
  const buffer =
    'data: {"type":"status","text":"신촌역 · 카페 분석 중"}\n\n' +
    'data: {"type":"delta","text":"괜찮은 "}\n\n' +
    'data: {"type":"delta","te';
  const { events, rest } = parseSseBuffer(buffer);
  assert.deepEqual(events, [
    { type: "status", text: "신촌역 · 카페 분석 중" },
    { type: "delta", text: "괜찮은 " },
  ]);
  assert.equal(rest, 'data: {"type":"delta","te');
});

test("parseSseBuffer joins a split event across chunks", () => {
  const first = parseSseBuffer('data: {"type":"delta","te');
  const second = parseSseBuffer(first.rest + 'xt":"자리입니다."}\n\n');
  assert.deepEqual(second.events, [{ type: "delta", text: "자리입니다." }]);
  assert.equal(second.rest, "");
});

test("parseSseBuffer handles CRLF and skips a broken event without dropping the rest", () => {
  const { events } = parseSseBuffer(
    'data: {broken\r\n\r\ndata: {"type":"done","response":null,"fallback":true}\r\n\r\n',
  );
  assert.deepEqual(events, [{ type: "done", response: null, fallback: true }]);
});
