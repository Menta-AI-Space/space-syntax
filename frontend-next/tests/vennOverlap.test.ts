import { test } from "node:test";
import * as assert from "node:assert/strict";

import { vennCenterDistance } from "../components/dashboard/dashboardUtils.ts";

// 겹침 벤 다이어그램은 원 두 개를 고정 좌표로 그려서, 겹침이 5%든 80%든
// 그림이 똑같았다 — 옆의 숫자와 그림이 다른 말을 하고, 눈은 그림을 먼저 읽는다.

const R = 26;

/** 반지름이 같은 두 원이 거리 d일 때 겹치는 넓이가 한 원 넓이에서 차지하는 비율. */
function areaFraction(d: number, r: number): number {
  if (d >= 2 * r) return 0;
  if (d <= 0) return 1;
  const lens = 2 * r * r * Math.acos(d / (2 * r)) - (d / 2) * Math.sqrt(4 * r * r - d * d);
  return lens / (Math.PI * r * r);
}

test("그려진 겹침이 실제로 그 비율이다", () => {
  for (const pct of [5, 15, 25, 40, 60, 80, 95]) {
    const d = vennCenterDistance(pct / 100, R);
    const drawn = areaFraction(d, R) * 100;
    assert.ok(
      Math.abs(drawn - pct) < 0.5,
      `${pct}%로 그리려 했는데 실제 겹침은 ${drawn.toFixed(2)}%`,
    );
  }
});

test("겹침이 커질수록 원이 가까워진다", () => {
  const distances = [10, 30, 50, 70, 90].map((p) => vennCenterDistance(p / 100, R));
  for (let i = 1; i < distances.length; i++) {
    assert.ok(distances[i] < distances[i - 1], "겹침이 늘었는데 거리가 안 줄었다");
  }
});

test("양 끝값은 맞닿음과 완전 포개짐", () => {
  assert.equal(vennCenterDistance(0, R), 2 * R);
  assert.equal(vennCenterDistance(1, R), 0);
});

test("범위 밖 값도 그림이 깨지지 않는다", () => {
  assert.equal(vennCenterDistance(-0.2, R), 2 * R);
  assert.equal(vennCenterDistance(1.5, R), 0);
});
