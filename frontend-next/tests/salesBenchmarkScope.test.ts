import { test } from "node:test";
import * as assert from "node:assert/strict";

import { salesBenchmarkScopeNote } from "../lib/api.ts";

test("scope가 dong이면 행정동 평균 대체 표시를 반환한다", () => {
  assert.equal(salesBenchmarkScopeNote({ scope: "dong" }), " (행정동 평균 대체)");
});

test("scope가 trdar(상권 실측)이면 대체 표시를 붙이지 않는다", () => {
  assert.equal(salesBenchmarkScopeNote({ scope: "trdar" }), "");
});

test("scope가 없거나 데이터가 없으면 대체 표시를 붙이지 않는다", () => {
  assert.equal(salesBenchmarkScopeNote({ scope: undefined }), "");
  assert.equal(salesBenchmarkScopeNote(null), "");
  assert.equal(salesBenchmarkScopeNote(undefined), "");
});
