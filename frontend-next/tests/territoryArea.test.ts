import { test } from "node:test";
import * as assert from "node:assert/strict";

import { fmtTerritoryArea } from "../components/dashboard/dashboardUtils.ts";

// 권역 면적은 늘 `Math.round(m2 / 10000)만㎡`로 적혔다. 1만㎡보다 작은 권역은
// 전부 "0만㎡"가 돼, 실제로 존재하는 권역이 화면에서는 없는 것처럼 보였다.

test("1만㎡ 미만도 0으로 뭉개지 않는다", () => {
  assert.equal(fmtTerritoryArea(4200), "4,200㎡");
  assert.equal(fmtTerritoryArea(9999), "9,999㎡");
});

test("1만㎡ 이상은 만㎡로 줄여 쓴다", () => {
  assert.equal(fmtTerritoryArea(10_000), "1.0만㎡");
  assert.equal(fmtTerritoryArea(125_000), "12.5만㎡");
});

test("면적이 없으면 0이 아니라 값 없음으로 적는다", () => {
  assert.equal(fmtTerritoryArea(0), "—");
  assert.equal(fmtTerritoryArea(Number.NaN), "—");
});
