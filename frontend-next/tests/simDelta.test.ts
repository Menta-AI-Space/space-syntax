import { test } from "node:test";
import * as assert from "node:assert/strict";

import { SIM_COLORS, deltaTone, simDeltaColor } from "../lib/simDelta.ts";

// 지도 색과 왼쪽 패널의 증감 숫자가 같은 기준을 써야 한다. 예전에는 숫자 쪽이
// "0 이상이면 초록"이라, 지도가 회색으로 변화 없다고 칠한 도로의 수치가 옆에서는
// 좋아졌다고 초록으로 떴다.

test("작은 변화는 좋다고도 나쁘다고도 하지 않는다", () => {
  assert.equal(deltaTone(0), "flat");
  assert.equal(deltaTone(1.9), "flat");
  assert.equal(deltaTone(-1.9), "flat");
  assert.equal(simDeltaColor(1.9), SIM_COLORS.unchanged);
});

test("값이 없으면 판단하지 않는다", () => {
  assert.equal(deltaTone(null), "flat");
  assert.equal(deltaTone(undefined), "flat");
  assert.equal(simDeltaColor(null), SIM_COLORS.unchanged);
});

test("기준을 넘으면 개선·악화로 나뉜다", () => {
  assert.equal(deltaTone(2), "up");
  assert.equal(deltaTone(-2), "down");
  assert.equal(simDeltaColor(2), SIM_COLORS.improved);
  assert.equal(simDeltaColor(-2), SIM_COLORS.worsened);
});

test("크게 개선은 따로 구분한다", () => {
  assert.equal(simDeltaColor(20), SIM_COLORS.improvedStrong);
  assert.equal(simDeltaColor(19.9), SIM_COLORS.improved);
  // 크게 개선도 방향은 개선이다.
  assert.equal(deltaTone(20), "up");
});
