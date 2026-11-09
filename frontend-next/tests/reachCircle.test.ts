import { test } from "node:test";
import * as assert from "node:assert/strict";

import {
  REACH_MODE_ORDER,
  REACH_SPEED_M_PER_MIN,
  formatDistance,
  interpolatedReachRadiusM,
  reachByMode,
  type ReachState,
} from "../lib/reachCircle.ts";

function state(over: Partial<ReachState> = {}): ReachState {
  return {
    lat: 37.5559,
    lng: 126.9368,
    mode: "walk",
    // 도보 5분 350m / 10분 700m / 15분 1000m — 직선(75m/분)보다 짧다(길은 돌아간다).
    distancesM: { 5: 350, 10: 700, 15: 1000 },
    bandMinutes: [5, 10, 15],
    minMinute: 1,
    maxMinute: 15,
    minute: 10,
    ...over,
  };
}

test("formatDistance: 단위를 붙이고 없는 값은 숫자를 지어내지 않는다", () => {
  assert.equal(formatDistance(340), "340m");
  assert.equal(formatDistance(1_900), "1.9km");
  assert.equal(formatDistance(12_000), "12km");
  assert.equal(formatDistance(0), "—");
  assert.equal(formatDistance(Number.NaN), "—");
});

test("reachByMode: 이동수단 넷을 모두, 반경 오름차순으로 낸다", () => {
  const rings = reachByMode(state());
  assert.equal(rings.length, REACH_MODE_ORDER.length);
  const radii = rings.map((r) => r.radiusM);
  assert.deepEqual([...radii].sort((a, b) => a - b), radii);
  // 도보가 가장 안쪽, 차량이 가장 바깥.
  assert.equal(rings[0].mode, "walk");
  assert.equal(rings[rings.length - 1].mode, "drive");
});

test("reachByMode: 실측 모드만 measured=true", () => {
  const rings = reachByMode(state({ mode: "walk" }));
  const measured = rings.filter((r) => r.measured);
  assert.equal(measured.length, 1);
  assert.equal(measured[0].mode, "walk");
  // 실측값을 그대로 쓴다 (10분 밴드 = 700m).
  assert.equal(Math.round(measured[0].radiusM), 700);
});

test("reachByMode: 다른 이동수단에도 실측에서 관측한 우회 정도를 물려준다", () => {
  // 도보 10분 실측 700m ÷ 직선 750m = 0.9333 우회계수.
  const rings = reachByMode(state());
  const bike = rings.find((r) => r.mode === "bike");
  assert.ok(bike);
  const straightBike = REACH_SPEED_M_PER_MIN.bike * 10;
  assert.ok(bike!.radiusM < straightBike, "직선 근사보다 작아야 한다");
  assert.equal(Math.round(bike!.radiusM), Math.round(straightBike * (700 / 750)));
  assert.equal(bike!.measured, false);
});

test("reachByMode: 실측이 하나도 없으면 속도×시간으로 물러서되 실측이라 하지 않는다", () => {
  const rings = reachByMode(state({ distancesM: {}, bandMinutes: [] }));
  assert.equal(rings.length, REACH_MODE_ORDER.length);
  assert.ok(rings.every((r) => !r.measured));
  const walk = rings.find((r) => r.mode === "walk");
  assert.equal(Math.round(walk!.radiusM), REACH_SPEED_M_PER_MIN.walk * 10);
});

test("reachByMode: 반경이 0 이하인 이동수단은 그리지 않는다", () => {
  const rings = reachByMode(state({ minute: 0 }));
  assert.equal(rings.length, 0);
});

test("interpolatedReachRadiusM: 밴드 사이는 실측을 선형보간한다", () => {
  const s = state();
  // 5분 350m ↔ 10분 700m 사이의 7분.
  assert.equal(
    Math.round(interpolatedReachRadiusM("walk", 7, s.distancesM, s.bandMinutes)),
    490,
  );
});
